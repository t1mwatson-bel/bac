# -*- coding: utf-8 -*-
import logging
import re
import asyncio
import os
import sys
import fcntl
import urllib.request
import urllib.error
import json
from datetime import datetime, time
from collections import defaultdict
from telegram import Update
from telegram.ext import (
    Application,
    MessageHandler,
    filters,
    ContextTypes
)
from telegram.error import Conflict

# ======== НАСТРОЙКИ ========
TOKEN = "1163348874:AAFgZEXveILvD4MbhQ8jiLTwIxs4puYhmq0"
INPUT_CHANNEL_ID = -1003469691743
OUTPUT_CHANNEL_ID = -1003842401391

LOCK_FILE = f'/tmp/bot3_{TOKEN[-10:]}.lock'

# ======== ПРАВИЛА СМЕНЫ МАСТЕЙ ========
SUIT_CHANGE_RULES = {
    # Красные преимущества (не используются в этой версии, но оставим)
    ('♠️', 'red'): '♦️',
    ('♣️', 'red'): '♥️',
    ('♥️', 'red'): '♦️',
    ('♦️', 'red'): '♥️',
    # Чёрные преимущества
    ('♥️', 'black'): '♣️',
    ('♦️', 'black'): '♠️',
    ('♠️', 'black'): '♣️',
    ('♣️', 'black'): '♠️',
    # Прямая смена (без преимущества)
    '♦️': '♣️',
    '♣️': '♦️',
    '♥️': '♠️',
    '♠️': '♥️',
}

# ======== ЛОГГЕР ========
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ======== ХРАНИЛИЩЕ ========
class GameStorage:
    def __init__(self):
        self.games = {}           # все игры {номер: данные}
        self.pending_starts = {}   # игры, ожидающие подтверждения повтора
        self.predictions = {}       # активные прогнозы
        self.stats = {'wins': 0, 'losses': 0}
        self.prediction_counter = 0

storage = GameStorage()
lock_fd = None

# ======== БЛОКИРОВКА ========
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

# ======== ПРОВЕРКА ТОКЕНА ========
def check_bot_token():
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/getMe"
        req = urllib.request.Request(url, method='GET')
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            if data.get('ok'):
                logger.info(f"✅ Бот @{data['result']['username']} авторизован")
                return True
    except:
        pass
    logger.error("❌ Ошибка авторизации")
    return False

# ======== НОРМАЛИЗАЦИЯ МАСТИ ========
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

# ======== ИЗВЛЕЧЕНИЕ МАСТЕЙ ИЗ ТЕКСТА ========
def extract_suits(text):
    suits = []
    # ♥️♠️♣️♦️
    for ch in text:
        norm = normalize_suit(ch)
        if norm:
            suits.append(norm)
    return suits

# ======== ПАРСИНГ ИГРЫ ========
def parse_game(text):
    # Номер игры
    match = re.search(r'#N(\d+)', text)
    if not match:
        return None
    game_num = int(match.group(1))
    
    # Теги
    has_r = '#R' in text
    has_x = '#X' in text
    has_draw_arrow = '👉' in text or '👈' in text
    is_tie = '🔰' in text
    
    # Разделяем левую и правую часть
    # Ищем разделитель: - или 🔰
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
        # Если нет разделителя — возможно, неполное сообщение
        return None
    
    # Очищаем от номера игры
    left_part = re.sub(r'#N\d+\.?\s*', '', left_part)
    
    # Извлекаем масти из левой части (игрок)
    left_suits = extract_suits(left_part)
    
    # Извлекаем масти из правой части (банкир)
    right_suits = extract_suits(right_part)
    
    # Ищем цифры и фигуры в правой части (для старта)
    digits = re.findall(r'\d+[♠♣♥♦]', right_part)
    figures = re.findall(r'[JQKA][♠♣♥♦]', right_part)
    has_digit_figure = len(digits) >= 1 and len(figures) >= 1
    
    # Начальная масть от цифры (если есть)
    start_suit = None
    if digits:
        suit_char = digits[0][-1]
        start_suit = normalize_suit(suit_char)
    
    return {
        'num': game_num,
        'left': left_suits,
        'right': right_suits,
        'has_r': has_r,
        'has_x': has_x,
        'has_draw_arrow': has_draw_arrow,
        'is_tie': is_tie,
        'has_digit_figure': has_digit_figure,
        'start_suit': start_suit,
        'raw': text
    }

# ======== СРАВНЕНИЕ МАСТЕЙ ========
def compare_suits(s1, s2):
    if not s1 or not s2:
        return False
    return normalize_suit(s1) == normalize_suit(s2)

# ======== ЦВЕТ КАРТЫ ========
def suit_color(suit):
    if suit in ('♥️', '♦️'):
        return 'red'
    return 'black'

# ======== ПРОВЕРКА АКТИВНЫХ ПРОГНОЗОВ ========
async def check_predictions(current_game, context):
    logger.info(f"\n🔍 ПРОВЕРКА ПРОГНОЗОВ (текущая игра #{current_game['num']})")
    
    for pred_id, pred in list(storage.predictions.items()):
        if pred['status'] != 'pending':
            continue
        
        target = pred['target']
        logger.info(f"🎯 Прогноз #{pred_id}: цель #{target}, масть {pred['suit']}")
        
        # Если текущая игра — целевая (или перенесённая)
        if current_game['num'] == target:
            logger.info(f"✅ Игра #{target} — проверяем")
            
            # Проверяем, есть ли нужная масть у игрока
            suit_found = any(compare_suits(pred['suit'], s) for s in current_game['left'])
            
            # Если есть #R или #X в этой игре
            if current_game['has_r'] or current_game['has_x']:
                if suit_found:
                    logger.info(f"✅ ПРОГНОЗ #{pred_id} ЗАШЁЛ (несмотря на #R/#X)")
                    pred['status'] = 'win'
                    storage.stats['wins'] += 1
                    await send_result(pred, target, 'win', context, note="несмотря на #R")
                else:
                    # Перенос на +2, попытка не тратится
                    new_target = target + 2
                    logger.info(f"⏭️ #R/#X без масти → перенос на #{new_target}")
                    pred['target'] = new_target
                    # Не увеличиваем attempt
                    await send_shift_notice(pred, target, new_target, context)
            else:
                # Обычная игра
                if suit_found:
                    logger.info(f"✅ ПРОГНОЗ #{pred_id} ЗАШЁЛ")
                    pred['status'] = 'win'
                    storage.stats['wins'] += 1
                    await send_result(pred, target, 'win', context)
                else:
                    logger.info(f"❌ Прогноз #{pred_id} не зашёл")
                    if pred['attempt'] >= 2:
                        pred['status'] = 'loss'
                        storage.stats['losses'] += 1
                        await send_result(pred, target, 'loss', context)
                    else:
                        pred['attempt'] += 1
                        pred['target'] = pred['doggens'][pred['attempt']]
                        logger.info(f"🔄 Догон {pred['attempt']}, новая цель #{pred['target']}")
                        await update_prediction_message(pred, context)

# ======== СОЗДАНИЕ ПРОГНОЗА ========
async def create_prediction(start_game, repeat_game, player_game, context):
    """
    start_game — игра с начальной мастью (банкир, цифра+фигура)
    repeat_game — игра, где масть повторилась у банкира (X)
    player_game — игра, где новая масть появилась у игрока (Y)
    """
    start_num = start_game['num']
    repeat_num = repeat_game['num']
    player_num = player_game['num']
    
    start_suit = start_game['start_suit']
    
    # Смена масти
    new_suit = SUIT_CHANGE_RULES.get(start_suit)
    if not new_suit:
        logger.error(f"❌ Нет правила смены для {start_suit}")
        return
    
    # Отступ
    offset = player_num - repeat_num
    target_game = player_num + offset
    
    storage.prediction_counter += 1
    pred_id = storage.prediction_counter
    
    doggens = [target_game, target_game + 1, target_game + 2]
    
    pred = {
        'id': pred_id,
        'suit': new_suit,
        'target': target_game,
        'doggens': doggens,
        'attempt': 0,
        'status': 'pending',
        'source': start_num,
        'repeat': repeat_num,
        'player_appearance': player_num,
        'offset': offset,
        'created': datetime.now(),
        'msg_id': None
    }
    
    storage.predictions[pred_id] = pred
    logger.info(f"🤖 НОВЫЙ ПРОГНОЗ #{pred_id}: {new_suit} в игре #{target_game} (offset={offset})")
    
    await send_prediction(pred, context)

# ======== ОТПРАВКА ПРОГНОЗА ========
async def send_prediction(pred, context):
    try:
        moscow_tz = datetime.now()
        time_str = moscow_tz.strftime('%H:%M:%S')
        
        text = (
            f"🎯 *БОТ 3 — НОВЫЙ ПРОГНОЗ*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📊 *ИСТОЧНИК:* #{pred['source']}\n"
            f"🎯 *ПРОГНОЗ:* игра #{pred['target']} — масть {pred['suit']}\n"
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
    except Exception as e:
        logger.error(f"Ошибка отправки: {e}")

# ======== ОТПРАВКА УВЕДОМЛЕНИЯ О ПЕРЕНОСЕ ========
async def send_shift_notice(pred, old_target, new_target, context):
    if not pred.get('msg_id'):
        return
    try:
        moscow_tz = datetime.now()
        time_str = moscow_tz.strftime('%H:%M:%S')
        
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

# ======== ОБНОВЛЕНИЕ СООБЩЕНИЯ О ДОГОНЕ ========
async def update_prediction_message(pred, context):
    if not pred.get('msg_id'):
        return
    try:
        moscow_tz = datetime.now()
        time_str = moscow_tz.strftime('%H:%M:%S')
        
        attempt_names = ["ОСНОВНАЯ", "ДОГОН 1", "ДОГОН 2"]
        
        text = (
            f"🔄 *БОТ 3 — {attempt_names[pred['attempt']]}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📊 *ИСТОЧНИК:* #{pred['source']}\n"
            f"🎯 *ЦЕЛЬ:* #{pred['target']} — масть {pred['suit']}\n"
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

# ======== ОТПРАВКА РЕЗУЛЬТАТА ========
async def send_result(pred, game_num, result, context, note=""):
    if not pred.get('msg_id'):
        return
    try:
        moscow_tz = datetime.now()
        time_str = moscow_tz.strftime('%H:%M:%S')
        
        if result == 'win':
            emoji = "✅"
            status = "ЗАШЁЛ"
            result_text = f"✅ {note}".strip()
        else:
            emoji = "❌"
            status = "НЕ ЗАШЁЛ"
            result_text = ""
        
        attempt_names = ["основная", "догон 1", "догон 2"]
        
        text = (
            f"{emoji} *БОТ 3 — ПРОГНОЗ #{pred['id']} {status}!*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📊 *ИСТОЧНИК:* #{pred['source']}\n"
            f"🎯 *ЦЕЛЬ:* #{pred['target']}\n"
            f"🃏 *МАСТЬ:* {pred['suit']}\n"
            f"🔄 *ПОПЫТКА:* {attempt_names[pred['attempt']]}\n"
            f"🎮 *ПРОВЕРЕНО В ИГРЕ:* #{game_num}\n"
            f"{result_text}\n"
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

# ======== ЕЖЕДНЕВНАЯ СТАТИСТИКА ========
async def daily_stats(context: ContextTypes.DEFAULT_TYPE):
    total = storage.stats['wins'] + storage.stats['losses']
    percent = (storage.stats['wins'] / total * 100) if total > 0 else 0
    
    moscow_tz = datetime.now()
    date_str = moscow_tz.strftime('%d.%m.%Y')
    time_str = moscow_tz.strftime('%H:%M:%S')
    
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

# ======== НАПОМИНАНИЕ О ПРАВИЛЕ ========
async def remind_r_rule(context: ContextTypes.DEFAULT_TYPE):
    text = (
        "⚠️ *НАПОМИНАНИЕ:* если в целевой игре или догоне есть #R, "
        "прогноз сдвигается на +2 игры (попытка не тратится)."
    )
    await context.bot.send_message(
        chat_id=OUTPUT_CHANNEL_ID,
        text=text,
        parse_mode='Markdown'
    )

# ======== ОБРАБОТЧИК НОВЫХ СООБЩЕНИЙ ========
async def handle_new_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        # Определяем тип сообщения
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
        
        logger.info(f"📊 Игра #{game['num']}")
        logger.info(f"   Игрок: {game['left']}")
        logger.info(f"   Банкир: {game['right']}")
        logger.info(f"   Теги: R={game['has_r']}, X={game['has_x']}, 🔰={game['is_tie']}")
        
        # Сохраняем или обновляем игру
        storage.games[game['num']] = game
        
        # Проверяем активные прогнозы
        await check_predictions(game, context)
        
        # Если это ничья — не стартуем новые цепочки
        if game['is_tie']:
            logger.info("⏭️ Ничья — не стартуем")
            return
        
        # Проверяем, подходит ли игра для старта цепочки
        if game['has_digit_figure'] and game['start_suit'] and not game['has_draw_arrow']:
            logger.info(f"✅ Подходит для старта: начальная масть {game['start_suit']}")
            
            # Запоминаем, что ждём повтор этой масти у банкира
            next_game = game['num'] + 1
            storage.pending_starts[next_game] = {
                'start_num': game['num'],
                'start_suit': game['start_suit'],
                'waiting_for': 'repeat'
            }
            logger.info(f"⏳ Ждём игру #{next_game} для поиска повтора")
        
        # Проверяем, не ждали ли мы эту игру для повтора
        if game['num'] in storage.pending_starts:
            pending = storage.pending_starts.pop(game['num'])
            
            if pending['waiting_for'] == 'repeat':
                # Ищем повтор масти у банкира
                if pending['start_suit'] in game['right']:
                    logger.info(f"✅ Повтор масти {pending['start_suit']} в игре #{game['num']}")
                    
                    # Запоминаем X
                    repeat_num = game['num']
                    
                    # Теперь ждём появление новой масти у игрока
                    next_game = game['num'] + 1
                    storage.pending_starts[next_game] = {
                        'start_num': pending['start_num'],
                        'start_suit': pending['start_suit'],
                        'repeat_num': repeat_num,
                        'waiting_for': 'player'
                    }
                    logger.info(f"⏳ Ждём игру #{next_game} для появления новой масти у игрока")
                else:
                    logger.info(f"❌ Повтора маски {pending['start_suit']} нет в игре #{game['num']}")
                    # Можно сбросить или ждать дальше — пока просто сбрасываем
            
            elif pending['waiting_for'] == 'player':
                # Ищем новую масть у игрока (после смены)
                start_suit = pending['start_suit']
                new_suit = SUIT_CHANGE_RULES.get(start_suit)
                
                if new_suit and new_suit in game['left']:
                    logger.info(f"✅ Новая масть {new_suit} появилась у игрока в игре #{game['num']}")
                    
                    # Получаем данные стартовой игры
                    start_game = storage.games.get(pending['start_num'])
                    repeat_game = storage.games.get(pending['repeat_num'])
                    
                    if start_game and repeat_game:
                        await create_prediction(start_game, repeat_game, game, context)
                else:
                    logger.info(f"❌ Новая масть {new_suit} не появилась в игре #{game['num']}")
                    # Можно сбросить или ждать дальше
        
        # Чистим старые pending
        for n in list(storage.pending_starts.keys()):
            if n < game['num'] - 50:
                del storage.pending_starts[n]
        
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")

# ======== ERROR HANDLER ========
async def error_handler(update, context):
    try:
        if isinstance(context.error, Conflict):
            logger.warning("⚠️ Конфликт, выходим")
            release_lock()
            sys.exit(1)
    except:
        pass

# ======== MAIN ========
def main():
    print("\n" + "="*60)
    print("🤖 БОТ 3 (ИТОГОВАЯ ВЕРСИЯ) ЗАПУЩЕН")
    print("="*60)
    print("✅ Работа с редактируемыми сообщениями")
    print("✅ Учёт #R и #X (перенос на +2)")
    print("✅ Полная цепочка: старт → повтор → смена → появление → отступ")
    print("✅ Ежедневная статистика в 23:59 МСК")
    print("✅ Напоминание о правиле каждый час")
    print("="*60)
    
    if not acquire_lock():
        sys.exit(1)
    
    if not check_bot_token():
        release_lock()
        sys.exit(1)
    
    app = Application.builder().token(TOKEN).build()
    
    # Добавляем обработчики
    app.add_error_handler(error_handler)
    app.add_handler(MessageHandler(
        filters.Chat(INPUT_CHANNEL_ID) & filters.TEXT,
        handle_new_game
    ))
    
    # Планировщик задач
    job_queue = app.job_queue
    
    # Ежедневная статистика в 23:59 МСК
    job_queue.run_daily(daily_stats, time=time(23, 59, 0))
    
    # Напоминание о правиле каждый час
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
