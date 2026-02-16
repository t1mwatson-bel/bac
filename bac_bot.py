import os
import re
import asyncio
import logging
from datetime import datetime
from typing import Dict, List, Optional
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
import fcntl
import json

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ====================== КОНФИГУРАЦИЯ ======================
TOKEN = "1163348874:AAFgZEXveILvD4MbhQ8jiLTwIxs4puYhmq0"
INPUT_CHANNEL_ID = -1003469691743
OUTPUT_CHANNEL_ID = -1003842401391
ADMIN_ID = 683219603
BOT_USERNAME = "@Tim48bot"

MAX_GAME_NUMBER = 1440

# ✅ ПОЛНЫЙ СПИСОК ДИАПАЗОНОВ
VALID_RANGES = [
    (1, 9), (20, 29), (40, 49), (60, 69), (80, 89),
    (100, 109), (120, 129), (140, 149), (160, 169), (180, 189),
    (200, 209), (220, 229), (240, 249), (260, 269), (280, 289),
    (300, 309), (320, 329), (340, 349), (360, 369), (380, 389),
    (400, 409), (420, 429), (440, 449), (460, 469), (480, 489),
    (500, 509), (520, 529), (540, 549), (560, 569), (580, 589),
    (600, 609), (620, 629), (640, 649), (660, 669), (680, 689),
    (700, 709), (720, 729), (740, 749), (760, 769), (780, 789),
    (800, 809), (820, 829), (840, 849), (860, 869), (880, 889),
    (900, 909), (920, 929), (940, 949), (960, 969), (980, 989),
    (1000, 1009), (1020, 1029), (1040, 1049), (1060, 1069), (1080, 1089),
    (1100, 1109), (1120, 1129), (1140, 1149), (1160, 1169), (1180, 1189),
    (1200, 1209), (1220, 1229), (1240, 1249), (1260, 1269), (1280, 1289),
    (1300, 1309), (1320, 1329), (1340, 1349), (1360, 1369), (1380, 1389),
    (1400, 1409), (1420, 1429), (1440, 1440)
]

# Правила смены мастей ♠️→♣️ ♥️→♦️
SUIT_CHANGE_RULES = {
    '♠️': '♣️',
    '♣️': '♦️', 
    '♥️': '♦️',
    '♦️': '♥️'
}

# Масти для сравнения
SUIT_MAP = {'♠': '♠️', '♣': '♣️', '♥': '♥️', '♦': '♦️'}

# ====================== ГЛОБАЛЬНОЕ ХРАНИЛИЩЕ ======================
class Storage:
    def __init__(self):
        self.patterns: Dict[int, Dict] = {}
        self.strategy2_predictions: Dict[int, Dict] = {}
        self.strategy2_counter = 0
        self.lock_file = None

storage = Storage()

# ====================== УТИЛИТЫ ======================
def lock_bot():
    """🔒 Блокировка множественных запусков"""
    lock_file = f"/tmp/bot1_{TOKEN.split(':')[1][-10:]}.lock"
    storage.lock_file = open(lock_file, 'w')
    try:
        fcntl.flock(storage.lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        logger.info(f"🔒 Lock: {lock_file}")
    except IOError:
        logger.error("❌ Бот уже запущен!")
        exit(1)

def is_valid_game(game_num: int) -> bool:
    """Проверка валидности игры"""
    return any(start <= game_num <= end for start, end in VALID_RANGES)

def parse_suits(text: str) -> List[str]:
    """Извлечение мастей из текста"""
    suits = []
    suit_pattern = r'[A2-9TJQK][♠♣♥♦]'
    matches = re.findall(suit_pattern, text)
    for match in matches:
        suit_char = match[-1]
        suits.append(SUIT_MAP.get(suit_char, suit_char))
    return suits

def compare_suits(suit1: str, suit2: str) -> bool:
    """Сравнение мастей"""
    return suit1 == suit2

def extract_game_number(text: str) -> Optional[int]:
    """Извлечение номера игры"""
    match = re.search(r'#N?(\d+)', text)
    return int(match.group(1)) if match else None

def parse_game_data(text: str) -> Dict:
    """Парсинг данных игры"""
    game_num = extract_game_number(text)
    if not game_num:
        return {}
    
    # Извлекаем масти Левой руки (Игрок)
    left_hand_pattern = r'0\\(([A2-9TJQK♠♣♥♦\s]+)\\)'
    left_match = re.search(left_hand_pattern, text)
    
    all_suits = []
    first_suit = None
    
    if left_match:
        left_cards = left_match.group(1)
        all_suits = parse_suits(left_cards)
        if all_suits:
            first_suit = all_suits[0]
    
    return {
        'game_num': game_num,
        'first_suit': first_suit,
        'all_suits': all_suits,
        'text': text
    }

# ====================== ЛОГИКА ПАТТЕРНОВ ======================
async def check_patterns(game_num: int, game_ Dict, context: ContextTypes.DEFAULT_TYPE):
    """🔍 Проверка и создание паттернов"""
    logger.info(f"\n🔍 ПАТТЕРНЫ #{game_num}")
    
    first_suit = game_data.get('first_suit')
    if not first_suit:
        logger.info(f"⏭️ Нет first_suit для #{game_num}")
        return
    
    # 1️⃣ ПРОВЕРКА СУЩЕСТВУЮЩЕГО ПАТТЕРНА (1-я/2-я карта)
    if game_num in storage.patterns:
        logger.info(f"✅ НАЙДЕН ПАТТЕРН для #{game_num}")
        pattern = storage.patterns[game_num]
        all_suits = game_data['all_suits']
        
        # ✅ ТОЛЬКО 1-я ИЛИ 2-я карта!
        suit_found = (
            (len(all_suits) >= 1 and compare_suits(pattern['suit'], all_suits[0])) or
            (len(all_suits) >= 2 and compare_suits(pattern['suit'], all_suits[1]))
        )
        
        logger.info(f"   Ожидали: {pattern['suit']} | Карты: {all_suits} | Найдено: {suit_found}")
        
        if suit_found:
            logger.info(f"🎯 ✅ ПАТТЕРН #{pattern['source_game']}→#{game_num}")
            
            # Смена масти по правилам
            predicted_suit = SUIT_CHANGE_RULES.get(pattern['suit'])
            if predicted_suit:
                target_game = game_num + 1
                storage.strategy2_counter += 1
                pred_id = storage.strategy2_counter
                
                prediction = {
                    'id': pred_id,
                    'source_game': pattern['source_game'],
                    'pattern_game': game_num,
                    'target_game': target_game,
                    'original_suit': predicted_suit,
                    'check_games': [target_game, target_game+1, target_game+2],
                    'status': 'pending',
                    'attempt': 0,
                    'channel_message_id': None
                }
                storage.strategy2_predictions[pred_id] = prediction
                await send_prediction_to_channel(prediction, context)
        else:
            logger.info(f"❌ ПАТТЕРН НЕ СОВПАЛ #{game_num}")
        
        del storage.patterns[game_num]
    
    # 2️⃣ ✅ СОЗДАНИЕ НОВОГО ПАТТЕРНА от НЕЧЕТНЫХ (+3)
    is_odd = game_num % 2 != 0
    logger.info(f"   #{game_num} is_odd={is_odd}")
    
    if is_odd and first_suit and is_valid_game(game_num):
        check_game = game_num + 3
        storage.patterns[check_game] = {
            'suit': first_suit,  # ✅ ТОЛЬКО 1-я карта!
            'source_game': game_num
        }
        logger.info(f"📝 ✅ #{game_num}({first_suit}) → #{check_game} (+3!)")

# ====================== ЛОГИКА ПРОГНОЗОВ ======================
async def check_predictions(game_num: int, game_ Dict, context: ContextTypes.DEFAULT_TYPE):
    """🎯 Проверка прогнозов - ВСЕ 3 карты!"""
    logger.info(f"\n🔍 ПРОВЕРКА ПРОГНОЗОВ #{game_num}")
    
    player_cards = game_data['all_suits']
    if not player_cards:
        return
    
    # Проверяем все активные прогнозы
    predictions_to_check = []
    for pred_id, prediction in storage.strategy2_predictions.items():
        if prediction['status'] == 'pending' and game_num in prediction['check_games']:
            predictions_to_check.append((pred_id, prediction))
    
    for pred_id, prediction in predictions_to_check:
        predicted_suit = prediction['original_suit']
        target_game = prediction['target_game']
        attempt = prediction['attempt']
        
        logger.info(f"   Прогноз #{pred_id}: {predicted_suit} #{target_game} (попытка {attempt+1})")
        
        # ✅ ПРОВЕРКА ВСЕХ КАРТ ИГРОКА (2 или 3)
        suit_found = any(
            compare_suits(predicted_suit, card) 
            for card in player_cards
        )
        
        logger.info(f"      Карты: {player_cards} | Ожидали: {predicted_suit} | Найдено: {suit_found}")
        
        if suit_found:
            logger.info(f"🎉 ✅ ПРОГНОЗ #{pred_id} ЗАШЁЛ #{game_num}!")
            prediction['status'] = 'win'
            prediction['win_game'] = game_num
            await send_win_notification(pred_id, prediction, game_data)
            del storage.strategy2_predictions[pred_id]
        else:
            prediction['attempt'] += 1
            if prediction['attempt'] >= 3:
                logger.info(f"💥 ПРОГНОЗ #{pred_id} ПРОИГРАЛ")
                prediction['status'] = 'lose'
                del storage.strategy2_predictions[pred_id]

# ====================== ОТПРАВКА СООБЩЕНИЙ ======================
async def send_prediction_to_channel(prediction: Dict, context: ContextTypes.DEFAULT_TYPE):
    """🚀 Отправка прогноза в канал"""
    pred_id = prediction['id']
    suit = prediction['original_suit']
    target_game = prediction['target_game']
    
    message = (
        f"🎯 <b>СТРАТЕГИЯ 2 #{pred_id}</b>\n\n"
        f"📊 <b>ПАТТЕРН:</b> #{prediction['source_game']}({pattern_suit}) → #{prediction['pattern_game']}\n"
        f"🔄 <b>ПРОГНОЗ:</b> <b>{suit}</b> #{target_game}\n"
        f"🔄 Догоны: #{target_game+1}, #{target_game+2}\n\n"
        f"⚡ <b>КРАСНАЯ→КРАСНАЯ +3</b>"
    )
    
    try:
        msg = await context.bot.send_message(
            chat_id=INPUT_CHANNEL_ID,
            text=message,
            parse_mode='HTML',
            disable_web_page_preview=True
        )
        prediction['channel_message_id'] = msg.message_id
        logger.info(f"🚀 ПРОГНОЗ #{pred_id} ОТПРАВЛЕН!")
    except Exception as e:
        logger.error(f"❌ Ошибка отправки: {e}")

async def send_win_notification(pred_id: Dict, prediction: Dict, game_ Dict):
    """✅ Уведомление о выигрыше"""
    win_game = prediction['win_game']
    suit = prediction['original_suit']
    
    message = (
        f"🎉 <b>✅ ВЫИГРЫШ! СТРАТЕГИЯ 2 #{pred_id}</b>\n\n"
        f"📊 ПАТТЕРН: #{prediction['source_game']} → #{prediction['pattern_game']}\n"
        f"🎯 ПРОГНОЗ: <b>{suit}</b> #{win_game}\n"
        f"✅ <b>{suit} ЗАШЛА!</b>\n\n"
        f"⚡ <b>КРАСНАЯ→КРАСНАЯ +3 ✅</b>"
    )
    
    await context.bot.send_message(
        chat_id=INPUT_CHANNEL_ID,
        text=message,
        parse_mode='HTML'
    )

# ====================== ОБРАБОТКА СООБЩЕНИЙ ======================
async def handle_channel_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """📥 Обработка сообщений из канала"""
    if update.channel_post and update.channel_post.chat.id == INPUT_CHANNEL_ID:
        text = update.channel_post.text or ""
        game_data = parse_game_data(text)
        
        if game_
            game_num = game_data['game_num']
            logger.info(f"\n📥 #{game_num}. {game_data['text']}")
            logger.info(f"👈 #{game_num}: {game_data['all_suits']}")
            
            # Параллельная проверка паттернов и прогнозов
            await asyncio.gather(
                check_patterns(game_num, game_data, context),
                check_predictions(game_num, game_data, context)
            )

# ====================== ГЛАВНАЯ ФУНКЦИЯ ======================
async def main():
    """🚀 Запуск бота"""
    lock_bot()
    
    print("="*60)
    print(f"🤖 {BOT_USERNAME}")
    print("="*60)
    print("🎯 КРАСНАЯ→КРАСНАЯ v20.x")
    print("📊 Логика: #1125♠️→#1128♠️→♣️#1129-1131")
    print("✅ +3 паттерн + ВСЕ 3 карты!")
    print("="*60)
    
    # Создание приложения
    application = Application.builder().token(TOKEN).build()
    
    # Обработчики
    application.add_handler(MessageHandler(filters.Chat(chat_id=INPUT_CHANNEL_ID) & filters.Text(), handle_channel_message))
    
    # Удаление webhook + polling
    await application.bot.delete_webhook()
    await application.initialize()
    await application.start()
    logger.info("✅ Application started")
    
    # Запуск polling
    await application.updater.start_polling(drop_pending_updates=True)
    await asyncio.Event().wait()  # Бесконечный цикл

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Бот остановлен")
    finally:
        if storage.lock_file:
            storage.lock_file.close()
