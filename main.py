# -*- coding: utf-8 -*-
# bot3_ai.py — Бот 3 с AI-прогнозами, дополнительным прогнозом и пропуском аномалий #R

import logging
import re
import asyncio
import os
import sys
import fcntl
import sqlite3
import urllib.request
import urllib.error
import json
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import io
from datetime import datetime, time, timedelta
from collections import defaultdict
from telegram import Update
from telegram.ext import (
    Application,
    MessageHandler,
    filters,
    ContextTypes
)
from telegram.error import Conflict

# Импортируем AI-модуль
try:
    from ai_predict import get_ai_prediction
except ImportError:
    # Заглушка если модуля нет
    def get_ai_prediction(game_num, classic_suit):
        return None, 0.0, None

# ======== НАСТРОЙКИ ========
TOKEN = "1163348874:AAFgZEXveILvD4MbhQ8jiLTwIxs4puYhmq0"
INPUT_CHANNEL_ID = -1003469691743
OUTPUT_CHANNEL_ID = -1003842401391

LOCK_FILE = f'/tmp/bot3_ai_{TOKEN[-10:]}.lock'
DB_FILE = 'bot3_stats.db'

# ======== ПРАВИЛА СМЕНЫ МАСТЕЙ ========
SUIT_CHANGE_RULES = {
    '♦️': '♣️',
    '♣️': '♦️',
    '♥️': '♠️',
    '♠️': '♥️'
}

# ======== ПРАВИЛА ДЛЯ ДОПОЛНИТЕЛЬНОЙ МАСТИ (крест-накрест) ========
EXTRA_SUIT_RULES = {
    '♠️': '♦️',
    '♣️': '♥️',
    '♦️': '♠️',
    '♥️': '♣️'
}

# ======== ЛОГГЕР ========
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ======== БАЗА ДАННЫХ ========
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS games
                 (game_num INTEGER PRIMARY KEY,
                  left_suits TEXT,
                  right_suits TEXT,
                  has_r INTEGER,
                  has_x INTEGER,
                  is_tie INTEGER,
                  has_check INTEGER,
                  has_green INTEGER,
                  result TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS predictions
                 (pred_id INTEGER PRIMARY KEY,
                  source_game INTEGER,
                  target_game INTEGER,
                  suit TEXT,
                  quality TEXT,
                  ai_confidence REAL,
                  result TEXT,
                  attempt INTEGER,
                  is_extra INTEGER DEFAULT 0,
                  on_hold INTEGER DEFAULT 0,
                  hold_until INTEGER DEFAULT 0)''')
    conn.commit()
    conn.close()

def save_game(game_data):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''INSERT OR REPLACE INTO games 
                 (game_num, left_suits, right_suits, has_r, has_x, is_tie, has_check, has_green)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
              (game_data['num'],
               ','.join(game_data['left']),
               ','.join(game_data['right']),
               1 if game_data.get('has_r') else 0,
               1 if game_data.get('has_x') else 0,
               1 if game_data.get('is_tie') else 0,
               1 if game_data.get('has_check') else 0,
               1 if game_data.get('has_green') else 0))
    conn.commit()
    conn.close()

def save_prediction(pred, result=None):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''INSERT OR REPLACE INTO predictions
                 (pred_id, source_game, target_game, suit, quality, ai_confidence, result, attempt, is_extra, on_hold, hold_until)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
              (pred['id'], pred['source'], pred['target'], pred['suit'],
               pred.get('quality', 'unknown'), pred.get('ai_confidence', 0.0),
               result, pred['attempt'], 1 if pred.get('is_extra') else 0,
               1 if pred.get('on_hold') else 0, pred.get('hold_until', 0)))
    conn.commit()
    conn.close()

# ======== ВРЕМЯ МСК ========
def msk_now():
    return datetime.utcnow() + timedelta(hours=3)

# ======== ХРАНИЛИЩЕ ========
class GameStorage:
    def __init__(self):
        self.games = {}
        self.pending_starts = {}
        self.predictions = {}
        self.stats = {'wins': 0, 'losses': 0}
        self.prediction_counter = 0

storage = GameStorage()
lock_fd = None

def acquire_lock():
    global lock_fd
    try:
        lock_fd = open(LOCK_FILE, 'w')
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        logger.info(f"🔒 Блокировка: {LOCK_FILE}")
        return True
    except:
        logger.error("❌ Бот уже запущен")
        return False

def release_lock():
    global lock_fd
    if lock_fd:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()
            if os.path.exists(LOCK_FILE):
                os.unlink(LOCK_FILE)
        except:
            pass

def check_bot_token():
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/getMe"
        req = urllib.request.Request(url, method='GET')
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            if data.get('ok'):
                logger.info(f"✅ Бот @{data['result']['username']} авторизован")
                return True
    except Exception as e:
        logger.error(f"Ошибка авторизации: {e}")
    return False

def normalize_suit(s):
    if not s:
        return None
    s = str(s).strip()
    if s in ('♥', '❤', '♡', '♥️'):
        return '♥️'
    if s in ('♠', '♤', '♠️'):
        return '♠️'
    if s in ('♣', '♧', '♣️'):
        return '♣️'
    if s in ('♦', '♢', '♦️'):
        return '♦️'
    return None

def extract_suits(text):
    suits = []
    for ch in text:
        norm = normalize_suit(ch)
        if norm:
            suits.append(norm)
    return suits

def get_quality(figures):
    """Определяет качество сигнала по фигурам"""
    if not figures:
        return '⚠️ СЛАБЫЙ'
    fig = figures[0][0]
    if fig in ('A', 'K'):
        return '🔥 СУПЕР'
    elif fig in ('Q', 'J'):
        return '📊 СРЕДНИЙ'
    return '⚠️ СЛАБЫЙ'

def parse_game(text):
    match = re.search(r'#N(\d+)', text)
    if not match:
        return None
    game_num = int(match.group(1))
    
    has_r = '#R' in text
    has_x = '#X' in text or '#X🟡' in text
    has_check = '✅' in text
    has_green = '🟩' in text
    has_draw_arrow = '👉' in text or '👈' in text
    is_tie = '🔰' in text
    
    # 👉👈 Определяем, кто добирает
    player_draws = '👈' in text
    banker_draws = '👉' in text
    
    left_part = None
    right_part = None
    
    # Ищем разделители
    if '🔰' in text:
        parts = text.split('🔰', 1)
        left_part = parts[0]
        right_part = parts[1] if len(parts) > 1 else ''
    elif '-' in text:
        parts = text.split('-', 1)
        left_part = parts[0]
        right_part = parts[1] if len(parts) > 1 else ''
    else:
        return None
    
    left_part = re.sub(r'#N\d+\.?\s*', '', left_part)
    left_suits = extract_suits(left_part)
    right_suits = extract_suits(right_part)
    
    digits = re.findall(r'\d+[♠♣♥♦]', right_part)
    figures = re.findall(r'[JQKA][♠♣♥♦]', right_part)
    has_digit_figure = len(digits) >= 1 and len(figures) >= 1
    
    start_suit = None
    if digits:
        suit_char = digits[0][-1]
        start_suit = normalize_suit(suit_char)
    
    quality = get_quality(figures) if has_digit_figure else None
    
    return {
        'num': game_num,
        'left': left_suits,
        'right': right_suits,
        'has_r': has_r,
        'has_x': has_x,
        'has_check': has_check,
        'has_green': has_green,
        'has_draw_arrow': has_draw_arrow,
        'player_draws': player_draws,
        'banker_draws': banker_draws,
        'is_tie': is_tie,
        'is_finished': has_check or is_tie or has_green,  # ✅ ИСПРАВЛЕНО: добавлен 🟩
        'has_digit_figure': has_digit_figure,
        'start_suit': start_suit,
        'quality': quality,
        'raw': text
    }

def compare_suits(s1, s2):
    if not s1 or not s2:
        return False
    return normalize_suit(s1) == normalize_suit(s2)

# ======== РАСЧЁТ УВЕРЕННОСТИ ========
def calculate_confidence(pred, current_game=None):
    """
    Рассчитывает процент уверенности прогноза
    """
    confidence = 50  # Базовый процент
    
    # 1. Качество сигнала
    if pred.get('quality') == '🔥 СУПЕР':
        confidence += 20
    elif pred.get('quality') == '📊 СРЕДНИЙ':
        confidence += 10
    else:
        confidence -= 5
    
    # 2. AI подтверждает классику
    if pred.get('ai_suit') and pred.get('suit'):
        if pred['ai_suit'] == pred['suit']:
            ai_bonus = int(pred.get('ai_confidence', 0) * 30)
            confidence += ai_bonus
        else:
            confidence -= 10
    
    # 3. Статистика мастей (если есть БД)
    try:
        hot_suits = get_hot_suits(pred['target'] - 1)
        if pred['suit'] in hot_suits:
            confidence += 15
        elif pred['suit'] in get_cold_suits(pred['target'] - 1):
            confidence -= 10
    except:
        pass
    
    # 4. Если знаем целевую игру - проверяем теги
    if current_game:
        if current_game.get('has_r'):
            confidence -= 5
        if current_game.get('has_x'):
            confidence -= 3
    
    # Ограничиваем от 0 до 100
    confidence = max(0, min(100, confidence))
    
    return confidence

def get_hot_suits(before_game, limit=20):
    """Возвращает список 'горячих' мастей за последние N игр"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    c.execute('''SELECT left_suits FROM games 
                 WHERE game_num <= ? AND game_num > ? - ?
                 ORDER BY game_num DESC''', 
              (before_game, before_game, limit))
    
    suits_count = {'♥️': 0, '♠️': 0, '♣️': 0, '♦️': 0}
    total = 0
    
    for (suits_str,) in c.fetchall():
        if not suits_str:
            continue
        suits = suits_str.split(',')
        for suit in suits:
            if suit in suits_count:
                suits_count[suit] += 1
                total += 1
    
    conn.close()
    
    if total == 0:
        return []
    
    # Горячие = выше среднего на 20%
    avg = total / 4
    threshold = avg * 1.2
    hot = [suit for suit, count in suits_count.items() if count > threshold]
    
    return hot

def get_cold_suits(before_game, limit=20):
    """Возвращает список 'холодных' мастей за последние N игр"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    c.execute('''SELECT left_suits FROM games 
                 WHERE game_num <= ? AND game_num > ? - ?
                 ORDER BY game_num DESC''', 
              (before_game, before_game, limit))
    
    suits_count = {'♥️': 0, '♠️': 0, '♣️': 0, '♦️': 0}
    total = 0
    
    for (suits_str,) in c.fetchall():
        if not suits_str:
            continue
        suits = suits_str.split(',')
        for suit in suits:
            if suit in suits_count:
                suits_count[suit] += 1
                total += 1
    
    conn.close()
    
    if total == 0:
        return []
    
    # Холодные = ниже среднего на 20%
    avg = total / 4
    threshold = avg * 0.8
    cold = [suit for suit, count in suits_count.items() if count < threshold]
    
    return cold

def get_confidence_emoji(confidence):
    """Возвращает эмодзи для процента уверенности"""
    if confidence >= 90:
        return "🚀"
    elif confidence >= 80:
        return "🔥"
    elif confidence >= 70:
        return "💪"
    elif confidence >= 60:
        return "📊"
    elif confidence >= 50:
        return "🤔"
    else:
        return "⚠️"

# ======== ПРОВЕРКА ПРОГНОЗОВ (С АНОМАЛИЯМИ #R) ========
async def check_predictions(current_game, context):
    logger.info(f"\n🔍 ПРОВЕРКА ПРОГНОЗОВ (текущая игра #{current_game['num']})")
    
    # Проверяем, завершена ли игра (есть ✅, 🔰 или 🟩)
    if not current_game.get('is_finished', False):
        logger.info(f"⏳ Игра #{current_game['num']} не завершена (нет ✅/🔰/🟩), пропускаем проверку")
        return
    
    logger.info(f"✅ Игра #{current_game['num']} завершена, проверяем прогнозы")
    
    # ===== ОТСЛЕЖИВАЕМ СЕРИИ #R =====
    if 'r_streak' not in context.bot_data:
        context.bot_data['r_streak'] = 0
    
    if current_game.get('has_r'):
        context.bot_data['r_streak'] += 1
        logger.info(f"📊 Серия #R: {context.bot_data['r_streak']} подряд")
    else:
        if context.bot_data['r_streak'] >= 3:
            logger.info(f"✅ Аномалия #R завершилась после {context.bot_data['r_streak']} игр")
            # Аномалия кончилась - размораживаем прогнозы со следующей игры
            for pred_id, pred in storage.predictions.items():
                if pred.get('on_hold') and pred['status'] == 'pending':
                    pred['hold_until'] = current_game['num'] + 2  # Проверим через игру
                    logger.info(f"📌 Прогноз #{pred_id} разморозится в #{pred['hold_until']}")
        context.bot_data['r_streak'] = 0
    
    # Определяем, находимся ли мы в аномалии
    in_anomaly = context.bot_data['r_streak'] >= 3
    
    # Список прогнозов для проверки
    for pred_id, pred in list(storage.predictions.items()):
        if pred['status'] != 'pending':
            continue
        
        target = pred['target']
        
        # Проверяем, не заморожен ли прогноз из-за аномалии
        if pred.get('on_hold'):
            if current_game['num'] == pred.get('hold_until', 0):
                # Пришло время проверить замороженный прогноз
                logger.info(f"📌 Проверяем замороженный прогноз #{pred_id} в игре #{current_game['num']}")
                pred['on_hold'] = False
                pred['hold_until'] = 0
            else:
                continue  # Еще не время проверять
        
        # Если мы в аномалии и прогноз должен проверяться сейчас - замораживаем
        if in_anomaly and current_game['num'] >= target and not pred.get('on_hold'):
            logger.info(f"⏸️ Прогноз #{pred_id} заморожен из-за аномалии #R (цель #{target})")
            pred['on_hold'] = True
            pred['hold_until'] = 0  # Будет установлено после окончания аномалии
            continue
        
        if current_game['num'] == target:
            logger.info(f"📊 Проверяем прогноз #{pred_id} на игру #{target} (масть {pred['suit']})")
            
            # Проверяем масть
            suit_found = any(compare_suits(pred['suit'], s) for s in current_game['left'])
            has_r = current_game['has_r']
            
            # ЕСЛИ МАСТЬ ЕСТЬ - ВЫИГРЫШ В ЛЮБОМ СЛУЧАЕ
            if suit_found:
                logger.info(f"✅ ПРОГНОЗ #{pred_id} ВЫИГРАЛ (масть {pred['suit']} найдена)")
                pred['status'] = 'win'
                storage.stats['wins'] += 1
                save_prediction(pred, 'win')
                
                note = "несмотря на #R" if has_r else ""
                await send_result(pred, target, 'win', context, note=note)
                
            # ЕСЛИ МАСТИ НЕТ - ТОЛЬКО ТОГДА СМОТРИМ НА #R
            elif has_r and not pred.get('was_shifted', False) and not pred.get('is_extra', False):
                # #R без масти для основного прогноза - переносим и создаем дополнительный
                new_target = target + 2
                logger.info(f"⏭️ #R без масти → перенос основного на #{new_target}")
                
                # Запоминаем старый target для логов
                old_target = pred['target']
                
                # Переносим основной прогноз
                pred['target'] = new_target
                pred['was_shifted'] = True
                
                # СОЗДАЕМ ДОПОЛНИТЕЛЬНЫЙ ПРОГНОЗ (в том же сообщении)
                extra_suit = EXTRA_SUIT_RULES.get(pred['suit'])
                if extra_suit:
                    await add_extra_to_prediction(pred, extra_suit, new_target, current_game, context)
                
                # Обновляем сообщение основного прогноза
                await send_shift_notice(pred, old_target, new_target, context, has_extra=True)
                
            # ЕСЛИ МАСТИ НЕТ И НЕТ #R (ИЛИ #R УЖЕ БЫЛ)
            else:
                logger.info(f"❌ Прогноз #{pred_id} не зашёл (масти нет)")
                if pred['attempt'] >= 2:
                    pred['status'] = 'loss'
                    storage.stats['losses'] += 1
                    save_prediction(pred, 'loss')
                    await send_result(pred, target, 'loss', context)
                else:
                    pred['attempt'] += 1
                    pred['target'] = pred['doggens'][pred['attempt']]
                    logger.info(f"🔄 Догон {pred['attempt']}, новая цель #{pred['target']}")
                    await update_prediction_message(pred, context)

async def add_extra_to_prediction(main_pred, extra_suit, target_game, current_game, context):
    """Добавляет дополнительный прогноз в существующее сообщение"""
    
    logger.info(f"➕ ДОБАВЛЯЕМ ДОПОЛНИТЕЛЬНЫЙ ПРОГНОЗ: {extra_suit} на игру #{target_game}")
    
    storage.prediction_counter += 1
    pred_id = storage.prediction_counter
    
    # Догоны как у основного
    doggens = [target_game, target_game + 1, target_game + 2]
    
    extra_pred = {
        'id': pred_id,
        'suit': extra_suit,
        'ai_suit': None,
        'ai_confidence': 0.0,
        'target': target_game,
        'doggens': doggens,
        'attempt': 0,
        'status': 'pending',
        'source': main_pred['source'],
        'quality': '',  # Без качества
        'created': datetime.now(),
        'msg_id': main_pred['msg_id'],  # Используем то же сообщение
        'confidence': 50,  # Базовый процент
        'is_extra': True,
        'was_shifted': False,
        'main_pred_id': main_pred['id'],
        'on_hold': False,
        'hold_until': 0
    }
    
    # Рассчитываем уверенность
    extra_pred['confidence'] = calculate_confidence(extra_pred)
    
    storage.predictions[pred_id] = extra_pred
    save_prediction(extra_pred)
    
    # Сохраняем ID дополнительного прогноза в основном
    if 'extra_ids' not in main_pred:
        main_pred['extra_ids'] = []
    main_pred['extra_ids'].append(pred_id)

async def send_shift_notice(pred, old_target, new_target, context, has_extra=False):
    """Обновляет сообщение при переносе из-за #R"""
    if not pred.get('msg_id'):
        return
    try:
        time_str = msk_now().strftime('%H:%M:%S')
        
        attempt_names = ["ОСНОВНАЯ", "ДОГОН 1", "ДОГОН 2"]
        
        # Собираем все дополнительные прогнозы
        extra_text = ""
        if has_extra and 'extra_ids' in pred:
            for extra_id in pred['extra_ids']:
                extra = storage.predictions.get(extra_id)
                if extra:
                    extra_text += f"\n➕ ДОПОЛНИТЕЛЬНЫЙ #{extra_id}: #{new_target} — {extra['suit']}"
        
        text = (
            f"🔄 *БОТ 3 — {attempt_names[pred['attempt']]} (ПЕРЕНОС #R)*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📊 *ИСТОЧНИК:* #{pred['source']}\n"
            f"🎯 *ОСНОВНОЙ:* #{new_target} — {pred['suit']}{extra_text}\n"
            f"📈 *КАЧЕСТВО:* {pred.get('quality', '⚠️ СЛАБЫЙ')}\n"
            f"⚡️ *УВЕРЕННОСТЬ:* {pred.get('confidence', 50)}% {get_confidence_emoji(pred.get('confidence', 50))}\n"
            f"🔄 *ДАЛЬШЕ:* #{new_target + 1}, #{new_target + 2}\n"
            f"⏱ {time_str} МСК"
        )
        
        await context.bot.edit_message_text(
            chat_id=OUTPUT_CHANNEL_ID,
            message_id=pred['msg_id'],
            text=text,
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Ошибка при обновлении сообщения о переносе: {e}")

# ======== СОЗДАНИЕ ОСНОВНОГО ПРОГНОЗА ========
async def create_prediction(start_game, repeat_game, player_game, context):
    start_suit = start_game['start_suit']
    classic_suit = SUIT_CHANGE_RULES.get(start_suit)
    
    if not classic_suit:
        return
    
    offset = player_game['num'] - repeat_game['num']
    target_game = player_game['num'] + offset
    
    storage.prediction_counter += 1
    pred_id = storage.prediction_counter
    
    doggens = [target_game, target_game + 1, target_game + 2]
    quality = start_game.get('quality', '⚠️ СЛАБЫЙ')
    
    # Получаем AI-прогноз
    ai_suit, ai_confidence, _ = get_ai_prediction(start_game['num'], classic_suit)
    
    pred = {
        'id': pred_id,
        'suit': classic_suit,
        'ai_suit': ai_suit,
        'ai_confidence': ai_confidence,
        'target': target_game,
        'doggens': doggens,
        'attempt': 0,
        'status': 'pending',
        'source': start_game['num'],
        'repeat': repeat_game['num'],
        'player_appearance': player_game['num'],
        'offset': offset,
        'quality': quality,
        'created': datetime.now(),
        'msg_id': None,
        'confidence': None,
        'is_extra': False,
        'was_shifted': False,
        'on_hold': False,
        'hold_until': 0,
        'extra_ids': []
    }
    
    # Сразу рассчитываем уверенность
    pred['confidence'] = calculate_confidence(pred)
    
    storage.predictions[pred_id] = pred
    save_prediction(pred)
    logger.info(f"🤖 НОВЫЙ ПРОГНОЗ #{pred_id}: классика={classic_suit}, AI={ai_suit} ({ai_confidence:.1%}), уверенность={pred['confidence']}%")
    
    await send_prediction(pred, context)

# ======== ОТПРАВКА ОСНОВНОГО ПРОГНОЗА ========
async def send_prediction(pred, context):
    try:
        time_str = msk_now().strftime('%H:%M:%S')
        
        # Берём уверенность
        confidence = pred.get('confidence', 50)
        confidence_emoji = get_confidence_emoji(confidence)
        
        # Формируем текст с ЦЕЛЬЮ и МАСТЬЮ
        if pred['ai_suit'] and pred['ai_confidence'] > 0:
            ai_pct = int(pred['ai_confidence'] * 100)
            
            text = (
                f"🎯 *БОТ 3 — ПРОГНОЗ #{pred['id']}*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📊 *ИСТОЧНИК:* #{pred['source']}\n"
                f"🎯 *ЦЕЛЬ:* #{pred['target']} — {pred['suit']}\n"
                f"🤖 *AI:* {pred['ai_suit']} ({ai_pct}%)\n"
                f"📈 *КАЧЕСТВО:* {pred['quality']}\n"
                f"⚡️ *УВЕРЕННОСТЬ:* {confidence}% {confidence_emoji}\n\n"
                f"🔄 *ДОГОН 1:* #{pred['doggens'][1]}\n"
                f"🔄 *ДОГОН 2:* #{pred['doggens'][2]}\n"
                f"⏱ {time_str} МСК"
            )
        else:
            text = (
                f"🎯 *БОТ 3 — ПРОГНОЗ #{pred['id']}*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📊 *ИСТОЧНИК:* #{pred['source']}\n"
                f"🎯 *ЦЕЛЬ:* #{pred['target']} — {pred['suit']}\n"
                f"📈 *КАЧЕСТВО:* {pred['quality']}\n"
                f"⚡️ *УВЕРЕННОСТЬ:* {confidence}% {confidence_emoji}\n\n"
                f"🔄 *ДОГОН 1:* #{pred['doggens'][1]}\n"
                f"🔄 *ДОГОН 2:* #{pred['doggens'][2]}\n"
                f"⏱ {time_str} МСК"
            )
        
        msg = await context.bot.send_message(
            chat_id=OUTPUT_CHANNEL_ID,
            text=text,
            parse_mode='Markdown'
        )
        pred['msg_id'] = msg.message_id
        logger.info(f"✅ Прогноз #{pred['id']} отправлен (цель #{pred['target']}, масть {pred['suit']})")
        
    except Exception as e:
        logger.error(f"❌ Ошибка отправки прогноза #{pred['id']}: {e}")

# ======== ОБНОВЛЕНИЕ СООБЩЕНИЯ О ДОГОНЕ ========
async def update_prediction_message(pred, context):
    if not pred.get('msg_id'):
        return
    try:
        time_str = msk_now().strftime('%H:%M:%S')
        
        # Пересчитываем уверенность
        confidence = calculate_confidence(pred)
        pred['confidence'] = confidence
        confidence_emoji = get_confidence_emoji(confidence)
        
        attempt_names = ["ОСНОВНАЯ", "ДОГОН 1", "ДОГОН 2"]
        
        # Собираем дополнительные прогнозы
        extra_text = ""
        if 'extra_ids' in pred:
            for extra_id in pred['extra_ids']:
                extra = storage.predictions.get(extra_id)
                if extra and extra['status'] == 'pending':
                    extra_text += f"\n➕ ДОП. #{extra_id}: #{extra['target']} — {extra['suit']}"
        
        text = (
            f"🔄 *БОТ 3 — {attempt_names[pred['attempt']]}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📊 *ИСТОЧНИК:* #{pred['source']}\n"
            f"🎯 *ОСНОВНОЙ:* #{pred['target']} — {pred['suit']}{extra_text}\n"
            f"📈 *КАЧЕСТВО:* {pred['quality']}\n"
            f"⚡️ *УВЕРЕННОСТЬ:* {confidence}% {confidence_emoji}\n"
            f"🔄 *СЛЕДУЮЩАЯ:* #{pred['target'] + 1}\n"
            f"⏱ {time_str} МСК"
        )
        
        await context.bot.edit_message_text(
            chat_id=OUTPUT_CHANNEL_ID,
            message_id=pred['msg_id'],
            text=text,
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Ошибка обновления сообщения: {e}")

# ======== ОТПРАВКА РЕЗУЛЬТАТА ========
async def send_result(pred, game_num, result, context, note=""):
    if not pred.get('msg_id'):
        return
    try:
        time_str = msk_now().strftime('%H:%M:%S')
        
        if result == 'win':
            emoji = "✅"
            status = "ЗАШЁЛ"
        else:
            emoji = "❌"
            status = "НЕ ЗАШЁЛ"
        
        attempt_names = ["основная", "догон 1", "догон 2"]
        note_text = f"\n{note}" if note else ""
        
        # Берём уверенность
        confidence = pred.get('confidence', 50)
        confidence_emoji = get_confidence_emoji(confidence)
        
        extra_tag = "➕ ДОП." if pred.get('is_extra') else ""
        
        # Собираем результаты дополнительных прогнозов
        extra_results = ""
        if 'extra_ids' in pred:
            for extra_id in pred['extra_ids']:
                extra = storage.predictions.get(extra_id)
                if extra:
                    extra_status = "✅" if extra.get('status') == 'win' else "❌" if extra.get('status') == 'loss' else "⏳"
                    extra_results += f"\n   {extra_status} ДОП.#{extra_id}: {extra['suit']}"
        
        text = (
            f"{emoji} *БОТ 3 — ПРОГНОЗ #{pred['id']} {extra_tag} {status}!*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📊 *ИСТОЧНИК:* #{pred['source']}\n"
            f"🎯 *ЦЕЛЬ:* #{pred['target']} — {pred['suit']}{extra_results}\n"
            f"📈 *КАЧЕСТВО:* {pred.get('quality', '—')}\n"
            f"⚡️ *УВЕРЕННОСТЬ:* {confidence}% {confidence_emoji}\n"
            f"🔄 *ПОПЫТКА:* {attempt_names[pred['attempt']]}\n"
            f"🎮 *ПРОВЕРЕНО В ИГРЕ:* #{game_num}\n"
            f"{note_text}\n"
            f"📊 *СТАТИСТИКА:* {storage.stats['wins']}✅ / {storage.stats['losses']}❌\n"
            f"⏱ {time_str} МСК"
        )
        
        await context.bot.edit_message_text(
            chat_id=OUTPUT_CHANNEL_ID,
            message_id=pred['msg_id'],
            text=text,
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Ошибка отправки результата: {e}")

# ======== ГЕНЕРАЦИЯ ГРАФИКА ========
async def generate_stats_chart():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    c.execute('''SELECT result FROM predictions ORDER BY pred_id DESC LIMIT 30''')
    results = [row[0] for row in c.fetchall()]
    results.reverse()
    
    conn.close()
    
    if not results:
        return None
    
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 8))
    
    wins = [1 if r == 'win' else -1 for r in results]
    cumulative = [sum(wins[:i+1]) for i in range(len(wins))]
    
    ax1.plot(range(1, len(cumulative)+1), cumulative, 'b-', linewidth=2)
    ax1.axhline(y=0, color='gray', linestyle='--', alpha=0.5)
    ax1.set_xlabel('Номер прогноза')
    ax1.set_ylabel('Результат')
    ax1.set_title('Динамика за последние 30 прогнозов')
    ax1.grid(True, alpha=0.3)
    
    win_count = results.count('win')
    loss_count = results.count('loss')
    
    ax2.bar(['Выигрыши', 'Поражения'], [win_count, loss_count], 
            color=['green', 'red'], alpha=0.7)
    ax2.set_ylabel('Количество')
    ax2.set_title('Статистика за последние 30 прогнозов')
    
    for i, (count, label) in enumerate([(win_count, win_count), (loss_count, loss_count)]):
        ax2.text(i, count + 0.1, str(label), ha='center', fontsize=12)
    
    plt.tight_layout()
    
    buf = io.BytesIO()
    plt.savefig(buf, format='png')
    buf.seek(0)
    plt.close()
    
    return buf

# ======== ЕЖЕДНЕВНАЯ СТАТИСТИКА ========
async def daily_stats(context: ContextTypes.DEFAULT_TYPE):
    total = storage.stats['wins'] + storage.stats['losses']
    percent = (storage.stats['wins'] / total * 100) if total > 0 else 0
    
    time_str = msk_now().strftime('%H:%M:%S')
    date_str = msk_now().strftime('%d.%m.%Y')
    
    text = (
        f"📊 *БОТ 3 — СТАТИСТИКА ЗА {date_str}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"✅ ВЫИГРЫШИ: {storage.stats['wins']}\n"
        f"❌ ПРОИГРЫШИ: {storage.stats['losses']}\n"
        f"📈 ПРОЦЕНТ: {percent:.1f}%\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⏱ {time_str} МСК"
    )
    
    await context.bot.send_message(
        chat_id=OUTPUT_CHANNEL_ID,
        text=text,
        parse_mode='Markdown'
    )
    
    chart = await generate_stats_chart()
    if chart:
        await context.bot.send_photo(
            chat_id=OUTPUT_CHANNEL_ID,
            photo=chart,
            caption=f"📈 График за последние 30 прогнозов"
        )

# ======== НАПОМИНАНИЕ ========
async def remind_r_rule(context: ContextTypes.DEFAULT_TYPE):
    text = (
        "⚠️ *НАПОМИНАНИЕ:* если в игре есть #R — первый раз перенос на +2, "
        "второй раз подряд — обычная проверка. #X — обычная проверка."
    )
    await context.bot.send_message(
        chat_id=OUTPUT_CHANNEL_ID,
        text=text,
        parse_mode='Markdown'
    )

# ======== ОБРАБОТЧИК СООБЩЕНИЙ ========
async def handle_new_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        message = None
        is_edit = False
        
        if update.channel_post:
            message = update.channel_post
            is_edit = False
        elif update.edited_channel_post:
            message = update.edited_channel_post
            is_edit = True
        else:
            return
        
        text = message.text
        if not text:
            return
        
        logger.info(f"\n{'='*60}")
        logger.info(f"📥 {'РЕДАКТИРОВАНИЕ' if is_edit else 'НОВОЕ'}: {text[:150]}...")
        
        game = parse_game(text)
        if not game:
            logger.warning("⚠️ Не удалось распарсить игру")
            return
        
        # Добавляем признак редактирования
        game['is_edit'] = is_edit
        
        logger.info(f"📊 Игра #{game['num']}")
        logger.info(f"   Игрок: {game['left']} ({len(game['left'])} карт)")
        logger.info(f"   Банкир: {game['right']} ({len(game['right'])} карт)")
        logger.info(f"   Теги: R={game['has_r']}, X={game['has_x']}, ✅={game['has_check']}, 🔰={game['is_tie']}, 🟩={game['has_green']}")
        logger.info(f"   Стрелки: 👈={game.get('player_draws', False)}, 👉={game.get('banker_draws', False)}")
        logger.info(f"   Завершена: {game.get('is_finished', False)}")
        logger.info(f"   Это редактирование: {is_edit}")
        
        # Сохраняем игру в хранилище
        storage.games[game['num']] = game
        save_game(game)
        
        # ========== ЕСЛИ ИГРА ЗАВЕРШЕНА - ПРОВЕРЯЕМ ПРОГНОЗЫ ==========
        if game.get('is_finished', False):
            logger.info(f"🔍 Игра #{game['num']} ЗАВЕРШЕНА — проверяем прогнозы")
            await check_predictions(game, context)
        else:
            logger.info(f"⏳ Игра #{game['num']} НЕ ЗАВЕРШЕНА — ждём финальную версию")
            
            # Сохраняем в очередь ожидания
            context.bot_data.setdefault('pending_games', {})
            context.bot_data['pending_games'][game['num']] = {
                'time': msk_now(),
                'game_num': game['num'],
                'first_seen': text[:100]
            }
            
            # НЕ проверяем прогнозы на незавершенной игре!
            return
        
        # ========== ЛОГИКА СТАРТОВ И ПОВТОРОВ ==========
        if game['is_tie']:
            logger.info("⏭️ Ничья — не стартуем")
            return
        
        if game['has_digit_figure'] and game['start_suit'] and not game['has_draw_arrow']:
            logger.info(f"✅ Подходит для старта: масть {game['start_suit']} [{game['quality']}]")
            storage.pending_starts[game['num'] + 1] = {
                'start_num': game['num'],
                'start_suit': game['start_suit'],
                'quality': game['quality'],
                'waiting_for': 'repeat'
            }
        
        if game['num'] in storage.pending_starts:
            pending = storage.pending_starts.pop(game['num'])
            
            if pending['waiting_for'] == 'repeat':
                if pending['start_suit'] in game['right']:
                    logger.info(f"✅ Повтор масти {pending['start_suit']} в игре #{game['num']}")
                    storage.pending_starts[game['num'] + 1] = {
                        'start_num': pending['start_num'],
                        'start_suit': pending['start_suit'],
                        'quality': pending['quality'],
                        'repeat_num': game['num'],
                        'waiting_for': 'player'
                    }
                else:
                    logger.info(f"❌ Повтора нет")
            
            elif pending['waiting_for'] == 'player':
                new_suit = SUIT_CHANGE_RULES.get(pending['start_suit'])
                if new_suit and new_suit in game['left']:
                    logger.info(f"✅ Новая масть {new_suit} у игрока в игре #{game['num']}")
                    start_game = storage.games.get(pending['start_num'])
                    repeat_game = storage.games.get(pending['repeat_num'])
                    if start_game and repeat_game:
                        await create_prediction(start_game, repeat_game, game, context)
                else:
                    logger.info(f"❌ Новой масти нет")
        
        # Очистка старых ожидающих стартов
        for n in list(storage.pending_starts.keys()):
            if n < game['num'] - 50:
                del storage.pending_starts[n]
        
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")

async def error_handler(update, context):
    try:
        if isinstance(context.error, Conflict):
            logger.warning("⚠️ Конфликт, выходим")
            release_lock()
            sys.exit(1)
    except:
        pass

def main():
    print("\n" + "="*60)
    print("🤖 БОТ 3 — AI EDITION (С ПРОПУСКОМ АНОМАЛИЙ #R И 🟩)")
    print("="*60)
    print("✅ AI-прогнозы с уверенностью")
    print("✅ Классика + AI в одном сообщении")
    print("✅ Фильтр качества (🔥 СУПЕР / 📊 СРЕДНИЙ)")
    print("✅ Умный перенос (только один #R)")
    print("✅ ДОПОЛНИТЕЛЬНЫЙ ПРОГНОЗ при #R (в том же сообщении)")
    print("✅ ПРОПУСК АНОМАЛИЙ: 3+ #R подряд")
    print("✅ Проверка через 1 игру после аномалии")
    print("✅ Правило смены: ♠️→♦️, ♣️→♥️, ♦️→♠️, ♥️→♣️")
    print("✅ Проверка по ✅, 🔰 или 🟩")
    print("✅ Процент уверенности с эмодзи 🚀🔥💪📊🤔⚠️")
    print("✅ Статистика горячих/холодных мастей")
    print("✅ Графики статистики")
    print("✅ База данных SQLite")
    print("="*60)
    
    init_db()
    
    if not acquire_lock():
        sys.exit(1)
    
    if not check_bot_token():
        release_lock()
        sys.exit(1)
    
    app = Application.builder().token(TOKEN).build()
    app.add_error_handler(error_handler)
    app.add_handler(MessageHandler(
        filters.Chat(INPUT_CHANNEL_ID) & filters.TEXT,
        handle_new_game
    ))
    
    job_queue = app.job_queue
    if job_queue:
        job_queue.run_daily(daily_stats, time=time(23, 59, 0))
        job_queue.run_repeating(remind_r_rule, interval=3600, first=10)
    
    try:
        app.run_polling(
            allowed_updates=['channel_post', 'edited_channel_post'],
            drop_pending_updates=True
        )
    finally:
        release_lock()

if __name__ == "__main__":
    # Добавляем обработчик сигналов
    import signal
    def signal_handler(sig, frame):
        logger.info("👋 Бот останавливается...")
        release_lock()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    main()