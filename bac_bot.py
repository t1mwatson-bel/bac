import asyncio
import logging
import re
import fcntl
import os
import signal
import sys
from datetime import datetime
from typing import Dict, List, Optional

from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

# ====================== НАСТРОЙКИ ======================
TOKEN = "1163348874:AAFgZEXveILvD4MbhQ8jiLTwIxs4puYhmq0"
INPUT_CHANNEL_ID = -1003469691743
OUTPUT_CHANNEL_ID = -1003842401391
ADMIN_ID = 683219603
LOCK_FILE = '/tmp/redred_v2.lock'

# ✅ НОВЫЙ ДИАПАЗОН 10-19/30-39...1140
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

# ✅ ПРАВИЛА: ♦️↔♥️ ♠️↔♣️
SUIT_CHANGE_RULES = {
    '♦️': '♥️', '♥️': '♦️',
    '♠️': '♣️', '♣️': '♠️'
}

SUIT_MAP = {'♠': '♠️', '♣': '♣️', '♥': '♥️', '♦': '♦️'}

# Логирование
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ====================== ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ======================
storage = None
application = None

class RedRedStorage:
    def __init__(self):
        self.patterns = {}
        self.predictions = {}
        self.stats = {'wins': 0, 'losses': 0}
        self.prediction_counter = 0
        self.lock_fd = None

# ====================== LOCK ======================
async def acquire_lock():
    try:
        storage.lock_fd = open(LOCK_FILE, 'w')
        fcntl.flock(storage.lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        logger.info("🔒 RedRed_v2 Lock OK")
        return True
    except:
        logger.error("❌ RedRed_v2 уже запущен")
        return False

def release_lock():
    if storage and storage.lock_fd:
        try:
            fcntl.flock(storage.lock_fd.fileno(), fcntl.LOCK_UN)
            storage.lock_fd.close()
            os.unlink(LOCK_FILE)
            logger.info("🔓 Lock освобожден")
        except:
            pass

# ====================== УТИЛИТЫ ======================
def is_valid_redred_game(game_num: int) -> bool:
    return any(start <= game_num <= end for start, end in RED_RED_RANGES)

def parse_suits(text: str) -> List[str]:
    suits = []
    suit_pattern = r'[A2-9TJQK][♠♣♥♦]'
    matches = re.findall(suit_pattern, text)
    for match in matches:
        suit_char = match[-1]
        suits.append(SUIT_MAP.get(suit_char, suit_char))
    return suits

def extract_game_number(text: str) -> Optional[int]:
    match = re.search(r'#N?(\d+)', text)
    return int(match.group(1)) if match else None

def parse_game_data(text: str) -> Dict:
    game_num = extract_game_number(text)
    if not game_num or not is_valid_redred_game(game_num):
        return {}
    
    left_hand_pattern = r'0\\(([A2-9TJQK♠♣♥♦\s]+)\\)'
    left_match = re.search(left_hand_pattern, text)
    
    all_suits = []
    first_suit = None
    
    if left_match:
        left_cards = left_match.group(1)
        all_suits = parse_suits(left_cards)
        first_suit = all_suits[0] if all_suits else None
    
    return {
        'game_num': game_num,
        'first_suit': first_suit,
        'all_suits': all_suits
    }

# ====================== ЛОГИКА ======================
async def check_patterns(game_num: int, game_ Dict, context: ContextTypes.DEFAULT_TYPE):
    first_suit = game_data.get('first_suit')
    if not first_suit:
        return
    
    # ПРОВЕРКА паттерна
    if game_num in storage.patterns:
        pattern = storage.patterns[game_num]
        all_suits = game_data['all_suits']
        
        suit_found = (
            (len(all_suits) >= 1 and all_suits[0] == pattern['suit']) or
            (len(all_suits) >= 2 and all_suits[1] == pattern['suit'])
        )
        
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
        
        del storage.patterns[game_num]
    
    # СОЗДАНИЕ паттерна +3
    check_game = game_num + 3
    if is_valid_redred_game(check_game):
        storage.patterns[check_game] = {
            'suit': first_suit,
            'source': game_num
        }
        logger.info(f"📝 #{game_num}({first_suit}) → #{check_game}")

async def check_predictions(game_num: int, game_ Dict, context: ContextTypes.DEFAULT_TYPE):
    all_suits = game_data['all_suits']
    if not all_suits:
        return
    
    for pred_id, prediction in list(storage.predictions.items()):
        if prediction['status'] != 'pending':
            continue
        
        if game_num in prediction['check_games']:
            predicted_suit = prediction['suit']
            suit_found = predicted_suit in all_suits
            
            if suit_found:
                logger.info(f"🎉 RedRed #{pred_id} ЗАШЁЛ #{game_num}!")
                prediction['status'] = 'win'
                prediction['win_game'] = game_num
                storage.stats['wins'] += 1
                await send_redred_win(pred_id, prediction, game_num)
                del storage.predictions[pred_id]
            elif game_num == prediction['check_games'][-1]:
                logger.info(f"❌ RedRed #{pred_id} ПРОИГРАЛ")
                storage.stats['losses'] += 1
                del storage.predictions[pred_id]

# ====================== ОТПРАВКА ======================
async def send_redred_prediction(prediction: Dict, context: ContextTypes.DEFAULT_TYPE):
    pred_id = prediction['id']
    message = (
        f"🆕 <b>КРАСНАЯ→КРАСНАЯ #{pred_id}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 ПАТТЕРН: #{prediction['source_game']} → #{prediction['pattern_game']}\n"
        f"🔄 ПРОГНОЗ: <b>{prediction['suit']}</b> #{prediction['target_game']}\n"
        f"🔄 Догоны: #{prediction['check_games'][1]}, #{prediction['check_games'][2]}\n"
        f"⚡ v2: ♦️♥️ ♠️♣️ +3"
    )
    
    await context.bot.send_message(
        chat_id=INPUT_CHANNEL_ID,
        text=message,
        parse_mode='HTML'
    )
    logger.info(f"🚀 Прогноз #{pred_id} отправлен!")

async def send_redred_win(pred_id: int, prediction: Dict, win_game: int):
    message = (
        f"🎉 <b>✅ КРАСНАЯ→КРАСНАЯ #{pred_id} ВЫИГРЫШ!</b>\n"
        f"📊 #{prediction['source_game']} → #{prediction['pattern_game']}\n"
        f"🎯 <b>{prediction['suit']} #{win_game} ✅</b>\n"
        f"📈 {storage.stats['wins']}✅/{storage.stats['losses']}❌"
    )
    
    await application.bot.send_message(
        chat_id=INPUT_CHANNEL_ID,
        text=message,
        parse_mode='HTML'
    )

# ====================== ОБРАБОТЧИК ======================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.channel_post or update.channel_post.chat.id != INPUT_CHANNEL_ID:
        return
    
    text = update.channel_post.text or ""
    game_data = parse_game_data(text)
    
    if game_
        game_num = game_data['game_num']
        logger.info(f"📥 RedRed #{game_num}: {game_data['all_suits']}")
        
        await asyncio.gather(
            check_patterns(game_num, game_data, context),
            check_predictions(game_num, game_data, context)
        )

# ====================== ✅ ПОЛНЫЙ MAIN С ЗАВЕРШЕНИЕМ ======================
async def main():
    global storage, application
    
    print("="*60)
    print("🤖 КРАСНАЯ→КРАСНАЯ v2 ✅ ПОЛНЫЙ КОД")
    print("📊 Диапазон: 10-19/30-39...1140")
    print("🔄 Правила: ♦️→♥️ ♠️→♣️")
    print("="*60)
    
    # Инициализация
    storage = RedRedStorage()
    
    # 🔒 Lock
    if not await acquire_lock():
        print("❌ Бот уже запущен!")
        sys.exit(1)
    
    try:
        # Создание приложения
        application = Application.builder().token(TOKEN).build()
        
        # Обработчик
        application.add_handler(MessageHandler(
            filters.Chat(chat_id=INPUT_CHANNEL_ID) & filters.TEXT,
            handle_message
        ))
        
        # Запуск
        await application.initialize()
        await application.start()
        logger.info("🚀 RedRed_v2 запущен!")
        
        # Polling БЕСКОНЕЧНЫЙ
        await application.updater.start_polling(drop_pending_updates=True)
        await asyncio.Event().wait()  # ЖДЕТ СИГНАЛ ОСТАНОВКИ
        
    except KeyboardInterrupt:
        logger.info("🛑 Остановлен пользователем (Ctrl+C)")
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}")
    finally:
        # ✅ ПОЛНОЕ ЗАВЕРШЕНИЕ
        logger.info("🛑 Завершение бота...")
        if application:
            await application.stop()
            await application.shutdown()
        release_lock()
        logger.info("✅ Бот полностью остановлен")

# ====================== СИГНАЛЫ ДЛЯ ГРАЦИОЗНОЙ ОСТАНОВКИ ======================
def signal_handler(sig, frame):
    logger.info(f"🛑 Получен сигнал SIG{sig}")
    sys.exit(0)

# ====================== ✅ ТОЧКА ВХОДА ======================
if __name__ == "__main__":
    # Обработка сигналов
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Ctrl+C")
    except Exception as e:
        logger.error(f"❌ Фатальная ошибка: {e}")
    finally:
        release_lock()
        print("👋 Бот завершил работу")
