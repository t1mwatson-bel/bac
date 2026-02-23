# -*- coding: utf-8 -*-
# bot3_ai.py — Бот 3 с AI-прогнозами и процентом уверенности

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
from ai_predict import get_ai_prediction

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
                  result TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS predictions
                 (pred_id INTEGER PRIMARY KEY,
                  source_game INTEGER,
                  target_game INTEGER,
                  suit TEXT,
                  quality TEXT,
                  ai_confidence REAL,
                  result TEXT,
                  attempt INTEGER)''')
    conn.commit()
    conn.close()

def save_game(game_data):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''INSERT OR REPLACE INTO games 
                 (game_num, left_suits, right_suits, has_r, has_x, is_tie)
                 VALUES (?, ?, ?, ?, ?, ?)''',
              (game_data['num'],
               ','.join(game_data['left']),
               ','.join(game_data['right']),
               1 if game_data['has_r'] else 0,
               1 if game_data['has_x'] else 0,
               1 if game_data['is_tie'] else 0))
    conn.commit()
    conn.close()

def save_prediction(pred, result=None):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''INSERT OR REPLACE INTO predictions
                 (pred_id, source_game, target_game, suit, quality, ai_confidence, result, attempt)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
              (pred['id'], pred['source'], pred['target'], pred['suit'],
               pred.get('quality', 'unknown'), pred.get('ai_confidence', 0.0),
               result, pred['attempt']))
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
    has_draw_arrow = '👉' in text or '👈' in text
    is_tie = '🔰' in text
    
    # 👉 Определяем, добирает ли игрок (стрелочка влево)
    player_draws = '👈' in text
    
    left_part = None
    right_part = None
    
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
        'has_draw_arrow': has_draw_arrow,
        'player_draws': player_draws,  # ✅ Новое поле
        'is_tie': is_tie,
        'has_digit_figure': has_digit_figure,
        'start_suit': start_suit,
        'quality': quality,
        'raw': text
    }

def compare_suits(s1, s2):
    if not s1 or not s2:
        return False
    return normalize_suit(s1) == normalize_suit(s2)

# ======== РАСЧЁТ УВЕРЕННОСТИ (НОВЫЙ) ========
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

# ======== ПРОВЕРКА ПРОГНОЗОВ (ИСПРАВЛЕННАЯ) ========
async def check_predictions(current_game, context):
    logger.info(f"\n🔍 ПРОВЕРКА ПРОГНОЗОВ (текущая игра #{current_game['num']})")
    
    for pred_id, pred in list(storage.predictions.items()):
        if pred['status'] != 'pending':
            continue
        
        target = pred['target']
        
        if current_game['num'] == target:
            logger.info(f"✅ Игра #{target} — проверяем")
            
            # ===== ВАЖНО: СНАЧАЛА ПРОВЕРЯЕМ МАСТЬ =====
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
            elif has_r and not pred.get('was_shifted', False):
                # #R без масти - переносим
                new_target = target + 2
                logger.info(f"⏭️ #R без масти → перенос на #{new_target}")
                pred['target'] = new_target
                pred['was_shifted'] = True
                await send_shift_notice(pred, target, new_target, context)
                
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

async def send_shift_notice(pred, old_target, new_target, context):
    if not pred.get('msg_id'):
        return
    try:
        time_str = msk_now().strftime('%H:%M:%S')
        
        text = (
            f"⏭️ *БОТ 3 — ПЕРЕНОС ПРОГНОЗА*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📊 *ИСТОЧНИК:* #{pred['source']}\n"
            f"🎯 *БЫЛО:* #{old_target} — масть {pred['suit']}\n"
            f"⚠️ *В ИГРЕ #R — ПЕРЕНОС НА +2*\n"
            f"🎯 *СТАЛО:* #{new_target}\n"
            f"🔄 *ДОГОН 1:* #{new_target + 1}\n"
            f"🔄 *ДОГОН 2:* #{new_target + 2}\n"
            f"⏱ {time_str} МСК"
        )
        
        await context.bot.edit_message_text(
            chat_id=OUTPUT_CHANNEL_ID,
            message_id=pred['msg_id'],
            text=text,
            parse_mode='Markdown'
        )
    except:
        pass

# ======== СОЗДАНИЕ ПРОГНОЗА ========
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
        'confidence': None  # Заполнится позже
    }
    
    # Сразу рассчитываем уверенность
    pred['confidence'] = calculate_confidence(pred)
    
    storage.predictions[pred_id] = pred
    save_prediction(pred)
    logger.info(f"🤖 НОВЫЙ ПРОГНОЗ #{pred_id}: классика={classic_suit}, AI={ai_suit} ({ai_confidence:.1%}), уверенность={pred['confidence']}%")
    
    await send_prediction(pred, context)

# ======== ОТПРАВКА ПРОГНОЗА (ОБНОВЛЕННАЯ) ========
async def send_prediction(pred, context):
    try:
        time_str = msk_now().strftime('%H:%M:%S')
        
        # Берём уверенность
        confidence = pred.get('confidence', 50)
        confidence_emoji = get_confidence_emoji(confidence)
        
        # Формируем текст в зависимости от наличия AI
        if pred['ai_suit'] and pred['ai_confidence'] > 0:
            ai_pct = int(pred['ai_confidence'] * 100)
            
            text = (
                f"🎯 *БОТ 3 — ПРОГНОЗ #{pred['id']}*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📊 *ИСТОЧНИК:* #{pred['source']}\n\n"
                f"🤖 *AI-ПРОГНОЗ:* {pred['ai_suit']} ({ai_pct}%)\n"
                f"🎯 *КЛАССИКА:* {pred['suit']}\n"
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
                f"🎯 *ПРОГНОЗ:* {pred['suit']}\n"
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
        logger.info(f"✅ Прогноз #{pred['id']} отправлен (уверенность {confidence}%)")
        
    except Exception as e:
        logger.error(f"❌ Ошибка отправки прогноза #{pred['id']}: {e}")

# ======== ОБНОВЛЕНИЕ СООБЩЕНИЯ О ДОГОНЕ (ОБНОВЛЕННАЯ) ========
async def update_prediction_message(pred, context):
    if not pred.get('msg_id'):
        return
    try:
        time_str = msk_now().strftime('%H:%M:%S')
        
        # Пересчитываем уверенность (может измениться со временем)
        confidence = calculate_confidence(pred)
        pred['confidence'] = confidence
        confidence_emoji = get_confidence_emoji(confidence)
        
        attempt_names = ["ОСНОВНАЯ", "ДОГОН 1", "ДОГОН 2"]
        
        text = (
            f"🔄 *БОТ 3 — {attempt_names[pred['attempt']]}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📊 *ИСТОЧНИК:* #{pred['source']}\n"
            f"🎯 *ЦЕЛЬ:* #{pred['target']} — {pred['suit']}\n"
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
    except:
        pass

# ======== ОТПРАВКА РЕЗУЛЬТАТА (ОБНОВЛЕННАЯ) ========
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
        note_text = f"\n✅ {note}" if note else ""
        
        # Берём уверенность из прогноза
        confidence = pred.get('confidence', 50)
        confidence_emoji = get_confidence_emoji(confidence)
        
        text = (
            f"{emoji} *БОТ 3 — ПРОГНОЗ #{pred['id']} {status}!*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📊 *ИСТОЧНИК:* #{pred['source']}\n"
            f"🎯 *ЦЕЛЬ:* #{pred['target']}\n"
            f"🃏 *МАСТЬ:* {pred['suit']}\n"
            f"📈 *КАЧЕСТВО:* {pred['quality']}\n"
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
    except:
        pass

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

# ======== ОБРАБОТЧИК СООБЩЕНИЙ (ПОЛНОСТЬЮ ИСПРАВЛЕННЫЙ) ========
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
        logger.info(f"   Теги: R={game['has_r']}, X={game['has_x']}, 🔰={game['is_tie']}")
        logger.info(f"   Стрелочки: 👈={game.get('player_draws', False)}")
        logger.info(f"   Это редактирование: {is_edit}")
        
        # Сохраняем игру в хранилище
        storage.games[game['num']] = game
        save_game(game)
        
        # ========== ЛОГИКА ПРОВЕРКИ ПОЛНОТЫ ИГРЫ ==========
        
        # Игра считается ПОЛНОЙ, если:
        # 1. Нет стрелочки 👈 (игрок не добирает) И
        # 2. Либо у игрока 3 карты, либо 2 карты (натурал или игрок не брал)
        
        is_complete = False
        
        if not game.get('player_draws', False):  # Нет стрелочки 👈
            if len(game['left']) >= 3:  # 3 карты - точно добрал
                is_complete = True
                logger.info(f"✅ Игра #{game['num']} полная: 3 карты у игрока")
            elif len(game['left']) == 2:  # 2 карты и не добирал
                is_complete = True
                logger.info(f"✅ Игра #{game['num']} полная: 2 карты, игрок не добирал")
        
        # Отдельно обрабатываем редактирования - они всегда полные
        if is_edit and not is_complete:
            # Если это редактирование, но мы почему-то решили что игра неполная -
            # всё равно проверяем, потому что редактирование приходит только когда всё готово
            logger.info(f"✏️ Редактирование игры #{game['num']} - проверяем в любом случае")
            is_complete = True
        
        # Если игра НЕ ПОЛНАЯ - ждём
        if not is_complete:
            logger.info(f"⏳ Игра #{game['num']} НЕ ПОЛНАЯ (👈 или мало карт) - ждём финальную версию")
            
            # Сохраняем в очередь ожидания
            context.bot_data.setdefault('pending_games', {})
            context.bot_data['pending_games'][game['num']] = {
                'time': msk_now(),
                'game_num': game['num'],
                'first_seen': text[:100]
            }
            
            # НЕ проверяем прогнозы на неполной игре!
            return
        
        # ========== ЕСЛИ ИГРА ПОЛНАЯ - ПРОВЕРЯЕМ ПРОГНОЗЫ ==========
        logger.info(f"🔍 Игра #{game['num']} ПОЛНАЯ — проверяем прогнозы")
        await check_predictions(game, context)
        
        # ========== ЛОГИКА СТАРТОВ И ПОВТОРОВ (ОСТАЁТСЯ БЕЗ ИЗМЕНЕНИЙ) ==========
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
    print("🤖 БОТ 3 — AI EDITION (ФИНАЛЬНАЯ ВЕРСИЯ)")
    print("="*60)
    print("✅ AI-прогнозы с уверенностью")
    print("✅ Классика + AI в одном сообщении")
    print("✅ Фильтр качества (🔥 СУПЕР / 📊 СРЕДНИЙ)")
    print("✅ Умный перенос (только один #R)")
    print("✅ Ожидание третьей карты (👈)")
    print("✅ Приоритет выигрыша над #R")
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
    main()