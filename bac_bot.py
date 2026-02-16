import asyncio
import logging
import re
import fcntl
import os
from datetime import datetime
from typing import Dict, List, Optional

from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

# ====================== НАСТРОЙКИ ======================
TOKEN = "1163348874:AAFgZEXveILvD4MbhQ8jiLTwIxs4puYhmq0"
INPUT_CHANNEL_ID = -1003469691743
OUTPUT_CHANNEL_ID = -1003842401391
ADMIN_ID = 683219603
LOCK_FILE = f'/tmp/redred_v2_{TOKEN[-10:]}.lock'

# ✅ НОВЫЙ ДИАПАЗОН: 10-19,30-39,50-59...1140 (57 диапазонов)
RED_RED_RANGES = [
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
    (1110, 1119), (1130, 1139), (1140, 1140)
]

# ✅ НОВЫЕ ПРАВИЛА: ♦️↔♥️ ♠️↔♣️
SUIT_CHANGE_RULES = {
    '♦️': '♥️',    # Бубна → Черва
    '♥️': '♦️',    # Черва → Бубна  
    '♠️': '♣️',    # Пики → Трефа
    '♣️': '♠️'     # Трефа → Пики
}

SUIT_MAP = {'♠': '♠️', '♣': '♣️', '♥': '♥️', '♦': '♦️'}

# Логирование
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class RedRedStorage:
    def __init__(self):
        self.patterns: Dict[int, Dict] = {}
        self.predictions: Dict[int, Dict] = {}
        self.stats = {'wins': 0, 'losses': 0}
        self.prediction_counter = 0
        self.lock_fd = None

storage = RedRedStorage()

# ====================== LOCK & UTILS ======================
async def acquire_lock():
    """🔒 Блокировка запуска"""
    try:
        storage.lock_fd = open(LOCK_FILE, 'w')
        fcntl.flock(storage.lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        logger.info(f"🔒 RedRed_v2 Lock: {LOCK_FILE}")
        return True
    except (IOError, OSError):
        logger.error(f"❌ RedRed_v2 уже запущен!")
        return False

def release_lock():
    """🔓 Освобождение"""
    if storage.lock_fd:
        try:
            fcntl.flock(storage.lock_fd.fileno(), fcntl.LOCK_UN)
            storage.lock_fd.close()
            os.unlink(LOCK_FILE)
            logger.info("🔓 Lock освобожден")
        except: pass

def is_valid_redred_game(game_num: int) -> bool:
    """✅ Проверка диапазона 10-19/30-39...1140"""
    return any(start <= game_num <= end for start, end in RED_RED_RANGES)

def parse_suits(text: str) -> List[str]:
    """Извлечение ВСЕХ мастей"""
    suits = []
    suit_pattern = r'[A2-9TJQK][♠♣♥♦]'
    matches = re.findall(suit_pattern, text)
    for match in matches:
        suit_char = match[-1]
        suits.append(SUIT_MAP.get(suit_char, suit_char))
    return suits

def extract_game_number(text: str) -> Optional[int]:
    """#N123 или #123"""
    match = re.search(r'#N?(\d+)', text)
    return int(match.group(1)) if match else None

def parse_game_data(text: str) -> Dict:
    """👈 Левая рука игрока 0(...)"""
    game_num = extract_game_number(text)
    if not game_num or not is_valid_redred_game(game_num):
        return {}
    
    # Левая рука: 0(К♥️ 10♠️ ...)
    left_hand_pattern = r'0\\(([A2-9TJQK♠♣♥♦\s]+)\\)'
    left_match = re.search(left_hand_pattern, text)
    
    all_suits = []
    first_suit = None
    
    if left_match:
        left_cards = left_match.group(1)
        all_suits = parse_suits(left_cards)
        first_suit = all_suits[0] if all_suits else None
    
    logger.info(f"📥 RedRed #{game_num}: first={first_suit}, all={all_suits}")
    return {
        'game_num': game_num,
        'first_suit': first_suit,
        'all_suits': all_suits,
        'text': text
    }

# ====================== ПАТТЕРНЫ ======================
async def check_patterns(game_num: int, game_ Dict, context: ContextTypes.DEFAULT_TYPE):
    """🔍 +3 паттерны Красная→Красная"""
    first_suit = game_data.get('first_suit')
    if not first_suit:
        logger.info(f"⏭️ RedRed #{game_num}: нет масти")
        return
    
    logger.info(f"\n🔍 RedRed #{game_num} ({first_suit})")
    
    # 1️⃣ ПРОВЕРКА паттерна (1-я ИЛИ 2-я карта)
    if game_num in storage.patterns:
        pattern = storage.patterns[game_num]
        all_suits = game_data['all_suits']
        
        # ✅ 1-я ИЛИ 2-я карта!
        suit_found = (
            (len(all_suits) >= 1 and all_suits[0] == pattern['suit']) or
            (len(all_suits) >= 2 and all_suits[1] == pattern['suit'])
        )
        
        logger.info(f"   Проверяем паттерн #{pattern['source']}({pattern['suit']})")
        logger.info(f"   Карты: {all_suits} | Найдено: {suit_found}")
        
        if suit_found:
            logger.info(f"✅ ПАТТЕРН #{pattern['source']}({pattern['suit']}) → #{game_num}")
            
            predicted_suit = SUIT_CHANGE_RULES.get(pattern['suit'])
            if predicted_suit:
                target_game = game_num + 1
                storage.prediction_counter += 1
                pred_id = storage.prediction_counter
                
                prediction = {
                    'id': pred_id,
                    'source_game': pattern['source'],
                    'pattern_game': game_num,
                    'target_game': target_game,
                    'suit': predicted_suit,
                    'check_games': [target_game, target_game+1, target_game+2],
                    'status': 'pending',
                    'attempt': 0
                }
                storage.predictions[pred_id] = prediction
                await send_redred_prediction(prediction, context)
            else:
                logger.warning(f"⚠️ Нет правила смены для {pattern['suit']}")
        
        del storage.patterns[game_num]
    
    # 2️⃣ ✅ СОЗДАНИЕ НОВОГО ПАТТЕРНА +3
    check_game = game_num + 3
    if is_valid_redred_game(check_game):
        storage.patterns[check_game] = {
            'suit': first_suit,
            'source': game_num
        }
        logger.info(f"📝 #{game_num}({first_suit}) → #{check_game} (+3)")

# ====================== ПРОГНОЗЫ ======================
async def check_predictions(game_num: int, game_ Dict, context: ContextTypes.DEFAULT_TYPE):
    """🎯 Проверка ВСЕХ 3 догонов"""
    all_suits = game_data['all_suits']
    if not all_suits:
        return
    
    predictions_to_check = []
    for pred_id, prediction in storage.predictions.items():
        if (prediction['status'] == 'pending' and 
            game_num in prediction['check_games']):
            predictions_to_check.append((pred_id, prediction))
    
    for pred_id, prediction in predictions_to_check:
        predicted_suit = prediction['suit']
        check_idx = prediction['check_games'].index(game_num)
        
        # ✅ ПРОВЕРКА ВСЕХ КАРТ ИГРОКА!
        suit_found = predicted_suit in all_suits
        
        logger.info(f"   Прогноз #{pred_id}: {predicted_suit} | Карты: {all_suits} | Найдено: {suit_found}")
        
        if suit_found:
            logger.info(f"🎉 ✅ RedRed #{pred_id} ЗАШЁЛ #{game_num}!")
            prediction['status'] = 'win'
            prediction['win_game'] = game_num
            storage.stats['wins'] += 1
            await send_redred_win(pred_id, prediction, game_data)
            del storage.predictions[pred_id]
        elif check_idx == 2:  # Последний догон
            logger.info(f"❌ RedRed #{pred_id} ПРОИГРАЛ")
            prediction['status'] = 'lose'
            storage.stats['losses'] += 1
            await send_redred_lose(pred_id, prediction)
            del storage.predictions[pred_id]

# ====================== ОТПРАВКА СООБЩЕНИЙ ======================
async def send_redred_prediction(prediction: Dict, context: ContextTypes.DEFAULT_TYPE):
    """🚀 Отправка прогноза"""
    pred_id = prediction['id']
    suit = prediction['suit']
    target = prediction['target_game']
    
    message = (
        f"\n🆕 <b>КРАСНАЯ→КРАСНАЯ #{pred_id}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📊 <b>ПАТТЕРН:</b>\n"
        f"┣ #{prediction['source_game']}\n"
        f"┣ → #{prediction['pattern_game']}\n\n"
        f"🔄 <b>ПРОГНОЗ:</b> <b>{suit}</b> #{target}\n"
        f"┣ 🔄 Догон1: #{prediction['check_games'][1]}\n"
        f"┗ 🔄 Догон2: #{prediction['check_games'][2]}\n\n"
        f"⚡ <b>v2: ♦️♥️ ♠️♣️ +3</b>\n"
        f"⏱ {datetime.now().strftime('%H:%M:%S')}"
    )
    
    try:
        msg = await context.bot.send_message(
            chat_id=INPUT_CHANNEL_ID,
            text=message,
            parse_mode='HTML',
            disable_web_page_preview=True
        )
        prediction['msg_id'] = msg.message_id
        logger.info(f"🚀 RedRed #{pred_id} отправлен!")
    except Exception as e:
        logger.error(f"❌ Отправка прогноза #{pred_id}: {e}")

async def send_redred_win(pred_id: int, prediction: Dict, game_ Dict):
    """✅ Выигрыш"""
    message = (
        f"\n🎉 <b>✅ КРАСНАЯ→КРАСНАЯ #{pred_id} ВЫИГРЫШ!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 #{prediction['source_game']} → #{prediction['pattern_game']}\n"
        f"🎯 <b>{prediction['suit']} #{prediction['win_game']} ✅</b>\n\n"
        f"📈 СТАТИСТИКА: {storage.stats['wins']}✅ / {storage.stats['losses']}❌\n\n"
        f"⚡ <b>v2 СИСТЕМА РАБОТАЕТ!</b>"
    )
    await application.bot.send_message(
        chat_id=INPUT_CHANNEL_ID,
        text=message,
        parse_mode='HTML'
    )

async def send_redred_lose(pred_id: int, prediction: Dict):
    """❌ Проигрыш"""
    message = (
        f"\n❌ <b>КРАСНАЯ→КРАСНАЯ #{pred_id} ПРОИГРАЛ</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 #{prediction['source_game']} → #{prediction['pattern_game']}\n"
        f"💥 <b>{prediction['suit']} не зашла</b>\n\n"
        f"📈 {storage.stats['wins']}✅ / {storage.stats['losses']}❌"
    )
    await application.bot.send_message(
        chat_id=INPUT_CHANNEL_ID,
        text=message,
        parse_mode='HTML'
    )

# ====================== ГЛАВНЫЙ ОБРАБОТЧИК ======================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """📥 Обработка сообщений канала"""
    if not update.channel_post or update.channel_post.chat.id != INPUT_CHANNEL_ID:
        return
    
    text = update.channel_post.text or ""
    game_data = parse_game_data(text)
    
    if game_
        game_num = game_data['game_num']
        
        # ✅ Параллельная проверка паттернов + прогнозов
        await asyncio.gather(
            check_patterns(game_num, game_data, context),
            check_predictions(game_num, game_data, context)
        )

# ====================== MAIN ======================
async def main():
    global application
    
    print("="*70)
    print("🤖 КРАСНАЯ→КРАСНАЯ v2 - ПОЛНЫЙ КОД")
    print("📊 Диапазон: 10-19/30-39/50-59...1140")
    print("🔄 Правила: ♦️→♥️ ♠️→♣️ ♣️→♠️ ♥️→♦️")
    print("✅ +3 паттерны + ВСЕ 3 карты + 3 догона")
    print("="*70)
    
    # 🔒 Lock
    if not await acquire_lock():
        print("❌ Бот уже запущен!")
        return
    
    # Создание приложения
    application = Application.builder().token(TOKEN).build()
    
    # ✅ Обработчик ТОЛЬКО для твоего канала
    application.add_handler(MessageHandler(
        filters.Chat(chat_id=INPUT_CHANNEL_ID) & filters.TEXT,
        handle_message
    ))
    
    # ✅ Запуск
    await application.bot.delete_webhook()
    await application.initialize()
    await application.start()
    logger.info("🚀 КРАСНАЯ→КРАСНАЯ v2 ЗАПУЩЕН!")
    
    # Polling
    await application.updater.start_polling(
        drop_pending_updates=True,
        allowed_updates=['channel_post']
    )
    await asyncio.Event().wait()  # Бесконечный цикл

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Остановлен пользователем")
    finally:
        release_lock()
