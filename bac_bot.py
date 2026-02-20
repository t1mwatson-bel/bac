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

# ======== НАСТРОЙКИ ========
TOKEN = "1163348874:AAFgZEXveILvD4MbhQ8jiLTwIxs4puYhmq0"
INPUT_CHANNEL_ID = -1003469691743
OUTPUT_CHANNEL_ID = -1003842401391

LOCK_FILE = f'/tmp/bot3_{TOKEN[-10:]}.lock'

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

def parse_game(text):
    match = re.search(r'#N(\d+)', text)
    if not match:
        return None
    game_num = int(match.group(1))
    
    has_r = '#R' in text
    has_x = '#X' in text or '#X🟡' in text
    has_draw_arrow = '👉' in text or '👈' in text
    is_tie = '🔰' in text
    
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

def compare_suits(s1, s2):
    if not s1 or not s2:
        return False
    return normalize_suit(s1) == normalize_suit(s2)

# ======== ПРОВЕРКА ПРОГНОЗОВ ========
async def check_predictions(current_game, context):
    logger.info(f"\n🔍 ПРОВЕРКА ПРОГНОЗОВ (текущая игра #{current_game['num']})")
    
    for pred_id, pred in list(storage.predictions.items()):
        if pred['status'] != 'pending':
            continue
        
        target = pred['target']
        
        if current_game['num'] == target:
            logger.info(f"✅ Игра #{target} — проверяем")
            
            suit_found = any(compare_suits(pred['suit'], s) for s in current_game['left'])
            has_r = current_game['has_r']
            
            if has_r:
                if suit_found:
                    logger.info(f"✅ ПРОГНОЗ #{pred_id} ВЫИГРАЛ (несмотря на #R)")
                    pred['status'] = 'win'
                    storage.stats['wins'] += 1
                    await send_result(pred, target, 'win', context, note="несмотря на #R")
                else:
                    new_target = target + 2
                    logger.info(f"⏭️ #R без масти → перенос на #{new_target}")
                    pred['target'] = new_target
                    await send_shift_notice(pred, target, new_target, context)
            else:
                if suit_found:
                    logger.info(f"✅ ПРОГНОЗ #{pred_id} ВЫИГРАЛ")
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
    new_suit = SUIT_CHANGE_RULES.get(start_suit)
    
    if not new_suit:
        return
    
    offset = player_game['num'] - repeat_game['num']
    target_game = player_game['num'] + offset
    
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
        'source': start_game['num'],
        'repeat': repeat_game['num'],
        'player_appearance': player_game['num'],
        'offset': offset,
        'created': datetime.now(),
        'msg_id': None
    }
    
    storage.predictions[pred_id] = pred
    logger.info(f"🤖 НОВЫЙ ПРОГНОЗ #{pred_id}: {new_suit} в игре #{target_game}")
    
    await send_prediction(pred, context)

# ======== ОТПРАВКА ПРОГНОЗА ========
async def send_prediction(pred, context):
    try:
        time_str = msk_now().strftime('%H:%M:%S')
        
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

# ======== ОБНОВЛЕНИЕ СООБЩЕНИЯ О ДОГОНЕ ========
async def update_prediction_message(pred, context):
    if not pred.get('msg_id'):
        return
    try:
        time_str = msk_now().strftime('%H:%M:%S')
        
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
        time_str = msk_now().strftime('%H:%M:%S')
        
        if result == 'win':
            emoji = "✅"
            status = "ЗАШЁЛ"
        else:
            emoji = "❌"
            status = "НЕ ЗАШЁЛ"
        
        attempt_names = ["основная", "догон 1", "догон 2"]
        note_text = f"\n✅ {note}" if note else ""
        
        text = (
            f"{emoji} *БОТ 3 — ПРОГНОЗ #{pred['id']} {status}!*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📊 *ИСТОЧНИК:* #{pred['source']}\n"
            f"🎯 *ЦЕЛЬ:* #{pred['target']}\n"
            f"🃏 *МАСТЬ:* {pred['suit']}\n"
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

# ======== НАПОМИНАНИЕ ========
async def remind_r_rule(context: ContextTypes.DEFAULT_TYPE):
    text = (
        "⚠️ *НАПОМИНАНИЕ:* если в игре есть #R — перенос на +2. "
        "#X — обычная проверка."
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
        
        logger.info(f"📊 Игра #{game['num']}")
        logger.info(f"   Игрок: {game['left']}")
        logger.info(f"   Банкир: {game['right']}")
        logger.info(f"   Теги: R={game['has_r']}, X={game['has_x']}, 🔰={game['is_tie']}")
        logger.info(f"   Старт: цифра+фигура={game['has_digit_figure']}, масть={game['start_suit']}")
        
        storage.games[game['num']] = game
        await check_predictions(game, context)
        
        if game['is_tie']:
            logger.info("⏭️ Ничья — не стартуем")
            return
        
        if game['has_digit_figure'] and game['start_suit'] and not game['has_draw_arrow']:
            logger.info(f"✅ Подходит для старта: масть {game['start_suit']}")
            storage.pending_starts[game['num'] + 1] = {
                'start_num': game['num'],
                'start_suit': game['start_suit'],
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
    print("🤖 БОТ 3 — ИСПРАВЛЕННАЯ ВЕРСИЯ")
    print("="*60)
    print("✅ #R → перенос на +2")
    print("✅ #X → обычная проверка")
    print("✅ Время МСК")
    print("="*60)
    
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