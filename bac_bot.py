# -*- coding: utf-8 -*-
import logging
import re
import random
import asyncio
import os
import sys
import fcntl
import signal
import urllib.request
import urllib.error
import json
from datetime import datetime
from collections import defaultdict
from telegram import Update
from telegram.ext import (
    Application, MessageHandler, filters, ContextTypes
)
from telegram.error import Conflict

# === НАСТРОЙКИ ===
TOKEN = "1163348874:AAFgZEXveILvD4MbhQ8jiLTwIxs4puYhmq0"
INPUT_CHANNEL_ID = -1003469691743
OUTPUT_CHANNEL_ID = -1003842401391
ADMIN_ID = 683219603
LOCK_FILE = f'/tmp/bot1_{TOKEN[-10:]}.lock'
MAX_GAME_NUMBER = 1440

# НОВЫЕ ПРАВИЛА (Красная->Красная, Черная->Черная)
SUIT_CHANGE_RULES = {
    '♥️': '♦️', '♦️': '♥️',  # КРАСНЫЕ
    '♠️': '♣️', '♣️': '♠️'   # ЧЕРНЫЕ
}

VALID_RANGES = [
    (10, 19), (30, 39), (50, 59), (70, 79), (90, 99),
    (110, 119), (130, 139), (150, 159), (170, 179), (190, 199),
    (210, 219), (230, 239), (250, 259), (270, 279), (290, 299),
    (310, 319), (330, 339), (350, 359), (370, 379), (390, 399),
    (410, 419), (430, 439), (450, 459), (470, 479), (490, 499),
    (510, 519), (530, 539), (550, 559), (570, 579), (590, 599),
    (610, 619), (630, 639), (650, 659), (670, 679), (690, 699),
    (710, 719), (730, 739), (750, 759), (770, 779), (790, 799),
    (810, 819), (830, 839), (850, 859), (870, 879), (890, 899),
    (910, 919), (930, 939), (950, 959), (970, 979), (990, 999),
    (1010, 1019), (1030, 1039), (1050, 1059), (1070, 1079), (1090, 1099),
    (1110, 1119), (1130, 1139), (1150, 1159), (1170, 1179), (1190, 1199)
]

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

lock_fd = None
storage = None

def is_valid_game(game_num):
    """✅ Диапазоны для НЕЧЕТНЫХ игр"""
    return any(start <= game_num <= end for start, end in VALID_RANGES)

def acquire_lock():
    """🔒 Lock файл ИСПРАВЛЕН"""
    global lock_fd
    try:
        lock_fd = open(LOCK_FILE, 'w')
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        logger.info(f"🔒 Lock: {LOCK_FILE}")
        return True
    except (IOError, OSError):
        logger.error(f"❌ Уже запущен: {LOCK_FILE}")
        return False

def release_lock():
    """🔓 Graceful unlock"""
    global lock_fd
    if lock_fd:
        try:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
            lock_fd.close()
            os.unlink(LOCK_FILE)
        except: pass

def clear_telegram_queue():
    """🧹 Очистка очереди"""
    try:
        import urllib.request
        urllib.request.urlopen(f"https://api.telegram.org/bot{TOKEN}/getUpdates?offset=-1", timeout=5)
        logger.info("🧹 Telegram очищен")
    except: pass

def extract_left_part(text):
    """👈 Только левая рука"""
    separators = [' 👈 ', '👈', ' - ', ' – ', '—', '-', '🔰']
    for sep in separators:
        if sep in text:
            return text.split(sep, 1)[0].strip()
    return text.strip()

def parse_game_data(text):
    """📊 Парсинг ЛЕВОЙ РУКИ"""
    match = re.search(r'#N(\d+)', text)
    if not match: return None
    
    game_num = int(match.group(1))
    left_part = extract_left_part(text)
    
    suits = []
    suit_patterns = {
        '♥️': r'[♥❤♡]', '♠️': r'[♠♤]', 
        '♣️': r'[♣♧]', '♦️': r'[♦♢]'
    }
    
    for suit, pattern in suit_patterns.items():
        matches = re.findall(pattern, left_part)
        suits.extend([suit] * len(matches))
    
    if not suits: 
        logger.warning(f"⚠️ #{game_num}: нет мастей")
        return None
    
    logger.info(f"👈 #{game_num}: {suits}")
    return {
        'game_num': game_num,
        'first_suit': suits[0],
        'all_suits': suits
    }

class Storage:
    def __init__(self):
        self.game_history = {}
        self.patterns = {}  # check_game → {'suit': '♥️', 'source_game': 1143}
        self.strategy2_predictions = {}
        self.strategy2_counter = 0
        self.strategy2_stats = {'wins': 0, 'losses': 0}

storage = Storage()

def compare_suits(suit1, suit2):
    """Сравнение мастей"""
    suit_map = {'♥': '♥️', '♠': '♠️', '♣': '♣️', '♦': '♦️'}
    return suit_map.get(suit1.replace('️', ''), 'X') == suit_map.get(suit2.replace('️', ''), 'X')

async def check_patterns(game_num, game_data, context):
    """🎯 ПАТТЕРНЫ + ПРОГНОЗЫ"""
    logger.info(f"\n🔍🔍🔍 ПАТТЕРНЫ #{game_num} 🔍🔍🔍")
    
    # 1️⃣ ПРОВЕРЯЕМ ПАТТЕРН
    if game_num in storage.patterns:
        pattern = storage.patterns[game_num]
        expected_suit = pattern['suit']
        source_game = pattern['source_game']
        
        # Проверяем 1-ю ИЛИ 2-ю карту
        suit_found = False
        if len(game_data['all_suits']) >= 1 and compare_suits(expected_suit, game_data['all_suits'][0]):
            suit_found = True
            logger.info(f"✅ #{source_game}→#{game_num}: 1-я карта!")
        elif len(game_data['all_suits']) >= 2 and compare_suits(expected_suit, game_data['all_suits'][1]):
            suit_found = True
            logger.info(f"✅ #{source_game}→#{game_num}: 2-я карта!")
        
        if suit_found:
            # 🎯 СОЗДАЕМ ПРОГНОЗ!
            target_game = game_num + 1
            predicted_suit = SUIT_CHANGE_RULES.get(expected_suit)
            
            logger.info(f"🎯 ПАТТЕРН ПОДТВЕРЖДЕН! #{source_game}({expected_suit})→#{game_num}")
            logger.info(f"📤 ПРОГНОЗ: {predicted_suit} на #{target_game}")
            
            if predicted_suit:
                storage.strategy2_counter += 1
                pred_id = storage.strategy2_counter
                
                prediction = {
                    'id': pred_id, 'target_game': target_game,
                    'original_suit': predicted_suit, 'status': 'pending',
                    'attempt': 0, 'source_game': source_game,
                    'check_games': [target_game, target_game+1, target_game+2]
                }
                storage.strategy2_predictions[pred_id] = prediction
                
                await send_prediction_to_channel(prediction, context)
            else:
                logger.error(f"❌ Нет правила для {expected_suit}")
        
        del storage.patterns[game_num]
    
    # 2️⃣ НОВЫЙ ПАТТЕРН (только НЕЧЕТНЫЕ в диапазоне)
    if game_num % 2 == 1 and is_valid_game(game_num):
        check_game = game_num + 2  # 1143→1145? Ждем четную 1146!
        storage.patterns[check_game] = {
            'suit': game_data['first_suit'],
            'source_game': game_num
        }
        logger.info(f"📝 Новый паттерн #{game_num}({game_data['first_suit']})→#{check_game}")

async def send_prediction_to_channel(prediction, context):
    """📤 BOT1 формат"""
    try:
        text = (
            f"🎯 *BOT1 - НОВЫЙ ПРОГНОЗ #{prediction['id']}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📊 *ДЕТАЛИ:*\n"
            f"┣ 🎯 #{prediction['source_game']}→#{prediction['target_game']}\n"
            f"┣ 🃏 {prediction['original_suit']}\n"
            f"┣ 🔄 Догоны: #{prediction['check_games'][1]}, #{prediction['check_games'][2]}\n"
            f"┗ ⏱ {datetime.now().strftime('%H:%M:%S')}"
        )
        message = await context.bot.send_message(
            OUTPUT_CHANNEL_ID, text, parse_mode='Markdown'
        )
        prediction['channel_message_id'] = message.message_id
        logger.info(f"✅ ПРОГНОЗ #{prediction['id']} ОТПРАВЛЕН!")
    except Exception as e:
        logger.error(f"❌ Отправка: {e}")

async def handle_new_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """📥 Главный обработчик"""
    if update.effective_chat.id != INPUT_CHANNEL_ID: return
    
    text = update.channel_post.text or ""
    logger.info(f"\n📥 #{text[:100]}...")
    
    game_data = parse_game_data(text)
    if not game_data: return
    
    game_num = game_data['game_num']
    storage.game_history[game_num] = game_data
    
    # 1️⃣ ПАТТЕРНЫ (создание/проверка)
    await check_patterns(game_num, game_data, context)
    
    # Очистка
    if len(storage.game_history) > 100:
        oldest = min(storage.game_history)
        del storage.game_history[oldest]

def signal_handler(sig, frame):
    """🛑 Graceful shutdown"""
    logger.info(f"🛑 SIG{sig}")
    release_lock()
    sys.exit(0)

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """❌ Обработка ошибок"""
    if isinstance(context.error, Conflict):
        logger.error("❌ Конфликт инстансов!")
        release_lock()
        sys.exit(1)
    logger.error(f"❌ {context.error}")

def main():
    global storage
    
    # 🔒 Lock
    if not acquire_lock(): sys.exit(1)
    
    # 🧹 Очистка
    clear_telegram_queue()
    
    # 🛑 Signals
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    print("\n" + "="*60)
    print("🤖 BOT1 КРАСНАЯ→КРАСНАЯ, ЧЕРНАЯ→ЧЕРНАЯ")
    print(f"📡 {len(VALID_RANGES)} диапазонов")
    print("🎯 1143(♥️)→1146(♥️)→ПРОГНОЗ♦️ 1147")
    print("="*60)
    
    app = Application.builder().token(TOKEN).build()
    app.add_error_handler(error_handler)
    app.add_handler(MessageHandler(filters.Chat(INPUT_CHANNEL_ID) & filters.TEXT, handle_new_game))
    
    try:
        app.run_polling(drop_pending_updates=True, allowed_updates=['channel_post'])
    finally:
        release_lock()

if __name__ == "__main__":
    main()
