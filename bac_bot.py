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
from datetime import datetime
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

LOCK_FILE = f'/tmp/bot_new_{TOKEN[-10:]}.lock'

# ======== ПРАВИЛА СМЕНЫ МАСТЕЙ ========
SUIT_CHANGE_RULES = {
    # Красные преимущества
    ('♠️', 'red'): '♦️',
    ('♣️', 'red'): '♥️',
    ('♥️', 'red'): '♦️',
    ('♦️', 'red'): '♥️',
    # Чёрные преимущества
    ('♥️', 'black'): '♣️',
    ('♦️', 'black'): '♠️',
    ('♠️', 'black'): '♣️',
    ('♣️', 'black'): '♠️',
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
        self.games = {}
        self.pending = {}
        self.predictions = {}
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

# ======== ИЗВЛЕЧЕНИЕ ЛЕВОЙ ЧАСТИ ========
def extract_left_part(text):
    separators = [' 👈 ', '👈', ' - ', ' – ', '—', '-', '👉👈', '👈👉', '🔰']
    for sep in separators:
        if sep in text:
            parts = text.split(sep, 1)
            left = re.sub(r'#N\d+\.?\s*', '', parts[0].strip())
            return left
    return text.strip()

# ======== ПАРСИНГ ИГРЫ ========
def parse_game(text):
    match = re.search(r'#N(\d+)', text)
    if not match:
        return None
    
    game_num = int(match.group(1))
    
    has_r = '#R' in text
    has_x = '#X' in text or '#X🟡' in text
    
    left_raw = extract_left_part(text)
    
    suits_left = []
    patterns = {'♥️': r'[♥❤♡]', '♠️': r'[♠♤]', '♣️': r'[♣♧]', '♦️': r'[♦♢]'}
    for suit, pat in patterns.items():
        matches = re.findall(pat, left_raw)
        suits_left.extend([suit] * len(matches))
    
    if not suits_left:
        return None
    
    right_raw = text.split('👈')[-1] if '👈' in text else ''
    
    digits = re.findall(r'\d+[♠♣♥♦]', right_raw)
    figures = re.findall(r'[JQKA][♠♣♥♦]', right_raw)
    
    has_digit_figure = len(digits) >= 1 and len(figures) >= 1
    
    start_suit = None
    if digits:
        start_suit = digits[0][-1]
        if start_suit in '♥❤♡':
            start_suit = '♥️'
        elif start_suit in '♠♤':
            start_suit = '♠️'
        elif start_suit in '♣♧':
            start_suit = '♣️'
        elif start_suit in '♦♢':
            start_suit = '♦️'
    
    return {
        'num': game_num,
        'left': suits_left,
        'right_digits': digits,
        'right_figures': figures,
        'has_digit_figure': has_digit_figure,
        'start_suit': start_suit,
        'has_r': has_r,
        'has_x': has_x,
        'raw': text
    }

# ======== СРАВНЕНИЕ МАСТЕЙ ========
def compare_suits(s1, s2):
    if not s1 or not s2:
        return False
    s1 = s1.replace('️', '').replace('\ufe0f', '').strip()
    s2 = s2.replace('️', '').replace('\ufe0f', '').strip()
    return s1 == s2

# ======== ЦВЕТ КАРТЫ ========
def suit_color(suit):
    if suit in ('♥️', '♦️'):
        return 'red'
    return 'black'

# ======== ПРОВЕРКА ПРОГНОЗОВ ========
async def check_predictions(current_game, context):
    logger.info(f"\n🔍 ПРОВЕРКА ПРОГНОЗОВ (текущая игра #{current_game['num']})")
    
    for pred_id, pred in list(storage.predictions.items()):
        if pred['status'] != 'pending':
            continue
        
        target = pred['target']
        
        if current_game['num'] == target + 1:
            logger.info(f"✅ Прогноз #{pred_id}: игра #{target} завершена, проверяем")
            
            game_data = storage.games.get(target)
            if not game_data:
                logger.warning(f"⚠️ Данные игры #{target} не найдены")
                continue
            
            suit_found = any(compare_suits(pred['suit'], s) for s in game_data['left'])
            
            if suit_found:
                logger.info(f"✅ ПРОГНОЗ #{pred_id} ЗАШЁЛ (масть {pred['suit']})")
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
async def create_prediction(game_n, game_n1, context):
    red = sum(1 for s in game_n1['left'] if suit_color(s) == 'red')
    black = sum(1 for s in game_n1['left'] if suit_color(s) == 'black')
    
    if red > black:
        adv = 'red'
    elif black > red:
        adv = 'black'
    else:
        adv = None
    
    start = game_n['start_suit']
    
    if adv is None:
        new_suit = start
        logger.info(f"⚖️ Равенство цветов, масть не меняется: {start}")
    else:
        new_suit = SUIT_CHANGE_RULES.get((start, adv))
        if not new_suit:
            logger.error(f"❌ Нет правила для ({start}, {adv})")
            return
        logger.info(f"🔄 Смена: {start} + {adv} → {new_suit}")
    
    target_game = game_n['num'] + 2
    doggens = [target_game, target_game + 1, target_game + 2]
    
    storage.prediction_counter += 1
    pred_id = storage.prediction_counter
    
    pred = {
        'id': pred_id,
        'suit': new_suit,
        'target': target_game,
        'doggens': doggens,
        'attempt': 0,
        'status': 'pending',
        'created': datetime.now(),
        'msg_id': None
    }
    
    storage.predictions[pred_id] = pred
    logger.info(f"🤖 НОВЫЙ ПРОГНОЗ #{pred_id}: {new_suit} в игре #{target_game}")
    
    await send_prediction(pred, context)

# ======== ОТПРАВКА ПРОГНОЗА ========
async def send_prediction(pred, context):
    try:
        text = (
            f"🎯 *БОТ НОВЫЙ — ПРОГНОЗ #{pred['id']}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📊 *ДЕТАЛИ:*\n"
            f"┣ 🎯 Целевая игра: #{pred['target']}\n"
            f"┣ 🃏 Масть: {pred['suit']}\n"
            f"┣ 🔄 Догон 1: #{pred['doggens'][1]}\n"
            f"┣ 🔄 Догон 2: #{pred['doggens'][2]}\n"
            f"┗ ⏱ {datetime.now().strftime('%H:%M:%S')}"
        )
        msg = await context.bot.send_message(
            chat_id=OUTPUT_CHANNEL_ID,
            text=text,
            parse_mode='Markdown'
        )
        pred['msg_id'] = msg.message_id
    except Exception as e:
        logger.error(f"Ошибка отправки: {e}")

# ======== ОБНОВЛЕНИЕ СООБЩЕНИЯ ========
async def update_prediction_message(pred, context):
    if not pred.get('msg_id'):
        return
    try:
        text = (
            f"🔄 *БОТ НОВЫЙ — ДОГОН {pred['attempt']}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🎯 Прогноз #{pred['id']}\n"
            f"┣ 🎯 Новая цель: #{pred['target']}\n"
            f"┣ 🃏 Масть: {pred['suit']}\n"
            f"┗ ⏱ {datetime.now().strftime('%H:%M:%S')}"
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
async def send_result(pred, game_num, result, context):
    if not pred.get('msg_id'):
        return
    try:
        emoji = "✅" if result == 'win' else "❌"
        status = "ЗАШЁЛ" if result == 'win' else "НЕ ЗАШЁЛ"
        text = (
            f"{emoji} *БОТ НОВЫЙ — ПРОГНОЗ #{pred['id']} {status}!*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📊 *ИТОГ:*\n"
            f"┣ 🎯 Целевая игра: #{pred['target']}\n"
            f"┣ 🃏 Масть: {pred['suit']}\n"
            f"┣ 🔄 Попытка: {['основная','догон1','догон2'][pred['attempt']]}\n"
            f"┣ 🎮 Проверено в игре: #{game_num}\n"
            f"┣ 📊 Статистика: {storage.stats['wins']}✅ / {storage.stats['losses']}❌\n"
            f"┗ ⏱ {datetime.now().strftime('%H:%M:%S')}"
        )
        await context.bot.edit_message_text(
            chat_id=OUTPUT_CHANNEL_ID,
            message_id=pred['msg_id'],
            text=text,
            parse_mode='Markdown'
        )
    except:
        pass

# ======== ОБРАБОТЧИК НОВЫХ СООБЩЕНИЙ ========
async def handle_new_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        # УНИВЕРСАЛЬНЫЙ ПРИЁМ — ловим и channel_post, и message
        message = None
        if update.channel_post:
            message = update.channel_post
        elif update.message:
            message = update.message
        else:
            logger.info("⏭️ Не сообщение из канала, пропускаем")
            return

        text = message.text
        if not text:
            return

        logger.info(f"\n{'='*60}")
        logger.info(f"📥 Получено: {text[:150]}...")
        
        game = parse_game(text)
        if not game:
            return
        
        logger.info(f"📊 Игра #{game['num']}")
        logger.info(f"   Игрок: {game['left']}")
        logger.info(f"   Банкир: цифры={game['right_digits']}, фигуры={game['right_figures']}")
        logger.info(f"   Теги: R={game['has_r']}, X={game['has_x']}")
        
        storage.games[game['num']] = game
        
        await check_predictions(game, context)
        
        if game['has_digit_figure'] and game['start_suit']:
            logger.info(f"✅ Подходит для старта: начальная масть {game['start_suit']}")
            next_game_num = game['num'] + 1
            storage.pending[next_game_num] = {
                'start_game': game['num'],
                'start_suit': game['start_suit'],
                'created': datetime.now()
            }
            logger.info(f"⏳ Ждём игру #{next_game_num} для определения преимущества")
        
        if game['num'] in storage.pending:
            pending = storage.pending.pop(game['num'])
            start_game = pending['start_game']
            start_suit = pending['start_suit']
            
            logger.info(f"🎯 Получена игра #{game['num']} для определения преимущества")
            
            start_data = storage.games.get(start_game)
            if start_data:
                await create_prediction(start_data, game, context)
        
        for n in list(storage.pending.keys()):
            if n < game['num'] - 50:
                del storage.pending[n]
        
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
    print("🤖 БОТ НОВЫЙ (ТВОЯ ЛОГИКА) ЗАПУЩЕН")
    print("="*60)
    print("✅ Настройки от БОТА 1")
    print("✅ Логика: банкир → игрок → смена → проверка")
    print("✅ Проверка только когда пришла следующая игра")
    print("✅ Догоны 2 игры")
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
    
    try:
        app.run_polling(allowed_updates=['channel_post', 'message'], drop_pending_updates=True)
    finally:
        release_lock()

if __name__ == "__main__":
    main()