# -*- coding: utf-8 -*-
import logging
import re
import random
import asyncio
import os
import sys
import fcntl
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

LOCK_FILE = f'/tmp/bot1_{TOKEN[-10:]}.lock'
MAX_GAME_NUMBER = 1440

# ✅ ПРАВИЛА (Красная→Красная, Черная→Черная)
SUIT_CHANGE_RULES = {
    '♥️': '♦️', '♦️': '♥️',  # КРАСНЫЕ
    '♠️': '♣️', '♣️': '♠️'   # ЧЕРНЫЕ
}

# ✅ ДИАПАЗОНЫ (10-19, 30-39...)
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

def is_valid_game(game_num):
    return any(start <= game_num <= end for start, end in VALID_RANGES)

def acquire_lock():
    """🔒 ИСПРАВЛЕН fcntl"""
    global lock_fd
    try:
        lock_fd = open(LOCK_FILE, 'w')
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # ✅ .fileno()
        logger.info(f"🔒 Lock: {LOCK_FILE}")
        return True
    except (IOError, OSError):
        logger.error(f"❌ Уже запущен: {LOCK_FILE}")
        return False

def release_lock():
    global lock_fd
    if lock_fd:
        try:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
            lock_fd.close()
            os.unlink(LOCK_FILE)
        except: pass

def extract_left_part(text):
    separators = [' 👈 ', '👈', ' - ', ' – ', '—', '-', '🔰']
    for sep in separators:
        if sep in text:
            return text.split(sep, 1)[0].strip()
    return text.strip()

def parse_game_data(text):
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

def compare_suits(suit1, suit2):
    suit_map = {
        '♥️': '♥', '♥': '♥', '❤': '♥', '♡': '♥',
        '♠️': '♠', '♠': '♠', '♤': '♠',
        '♣️': '♣', '♣': '♣', '♧': '♣',
        '♦️': '♦', '♦': '♦', '♢': '♦'
    }
    s1 = suit_map.get(suit1.replace('️', '').strip(), suit1)
    s2 = suit_map.get(suit2.replace('️', '').strip(), suit2)
    return s1 == s2

class Storage:
    def __init__(self):
        self.game_history = {}
        self.patterns = {}
        self.strategy2_predictions = {}
        self.strategy2_counter = 0
        self.strategy2_stats = {'wins': 0, 'losses': 0}

storage = Storage()

async def check_patterns(game_num, game_data, context):
    """🎯 ПАТТЕРНЫ + ПРОГНОЗЫ"""
    logger.info(f"\n🔍 ПАТТЕРНЫ #{game_num}")
    
    first_suit = game_data['first_suit']
    all_suits = game_data['all_suits']
    
    if not first_suit: return
    
    # 1️⃣ ПРОВЕРКА ПАТТЕРНА
    if game_num in storage.patterns:
        pattern = storage.patterns[game_num]
        expected_suit = pattern['suit']
        source_game = pattern['source_game']
        
        # 1-я ИЛИ 2-я карта
        suit_found = False
        if len(all_suits) >= 1 and compare_suits(expected_suit, all_suits[0]):
            suit_found = True
            logger.info(f"✅ #{source_game}→#{game_num}: 1-я {expected_suit}")
        elif len(all_suits) >= 2 and compare_suits(expected_suit, all_suits[1]):
            suit_found = True
            logger.info(f"✅ #{source_game}→#{game_num}: 2-я {expected_suit}")
        
        if suit_found:
            # 🎯 ПРОГНОЗ!
            target_game = game_num + 1
            predicted_suit = SUIT_CHANGE_RULES.get(expected_suit)
            
            logger.info(f"🎯 ПАТТЕРН ✓ #{source_game}({expected_suit})→#{game_num}")
            logger.info(f"📤 ПРОГНОЗ {predicted_suit} #{target_game}")
            
            if predicted_suit:
                storage.strategy2_counter += 1
                pred_id = storage.strategy2_counter
                
                prediction = {
                    'id': pred_id,
                    'source_game': source_game,
                    'target_game': target_game,
                    'original_suit': predicted_suit,
                    'check_games': [target_game, target_game+1, target_game+2],
                    'status': 'pending',
                    'attempt': 0
                }
                storage.strategy2_predictions[pred_id] = prediction
                await send_prediction_to_channel(prediction, context)
        
        del storage.patterns[game_num]
    
    # 2️⃣ ✅ НОВЫЙ ПАТТЕРН +3!
    is_odd = game_num % 2 != 0
    if is_odd and is_valid_game(game_num):
        check_game = game_num + 3  # ✅ ИСПРАВЛЕНО: +3 вместо +2!
        storage.patterns[check_game] = {
            'suit': first_suit,
            'source_game': game_num
        }
        logger.info(f"📝 #{game_num}({first_suit}) → #{check_game}")

async def send_prediction_to_channel(prediction, context):
    """📤 BOT1 формат"""
    try:
        text = (
            f"🎯 *BOT1 - НОВЫЙ ПРОГНОЗ #{prediction['id']}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📊 *ДЕТАЛИ:*\n"
            f"┣ 🎯 #{prediction['source_game']}→#{prediction['target_game']}\n"
            f"┣ 🃏 {prediction['original_suit']}\n"
            f"┣ 🔄 #{prediction['check_games'][1]}\n"
            f"┣ 🔄 #{prediction['check_games'][2]}\n"
            f"┗ ⏱ {datetime.now().strftime('%H:%M:%S')}"
        )
        message = await context.bot.send_message(
            OUTPUT_CHANNEL_ID, text, parse_mode='Markdown'
        )
        prediction['channel_message_id'] = message.message_id
        logger.info(f"✅ ПРОГНОЗ #{prediction['id']} ОТПРАВЛЕН!")
    except Exception as e:
        logger.error(f"❌ {e}")

async def handle_new_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """📥 Обработчик"""
    if update.effective_chat.id != INPUT_CHANNEL_ID: return
    
    text = update.channel_post.text or ""
    logger.info(f"\n📥 {text[:100]}...")
    
    game_data = parse_game_data(text)
    if not game_data: return
    
    game_num = game_data['game_num']
    storage.game_history[game_num] = game_data
    
    await check_patterns(game_num, game_data, context)
    
    if len(storage.game_history) > 100:
        oldest = min(storage.game_history)
        del storage.game_history[oldest]

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if isinstance(context.error, Conflict):
        logger.error("❌ Конфликт!")
        release_lock()
        sys.exit(1)
    logger.error(f"❌ {context.error}")

def main():
    # 🔒 LOCK
    if not acquire_lock(): sys.exit(1)
    
    # ✅ ТОКЕН
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/getMe"
        import urllib.request
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            if data.get('ok'):
                logger.info(f"✅ @{data['result']['username']}")
            else:
                logger.error("❌ Токен!")
                release_lock()
                sys.exit(1)
    except:
        logger.error("❌ Токен!")
        release_lock()
        sys.exit(1)
    
    print("\n" + "="*60)
    print("🤖 BOT1 КРАСНАЯ→КРАСНАЯ ✓")
    print("🎯 Логика: 1189♥️→1192♥️→♦️ 1193")
    print("✅ +3 вместо +2!")
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
