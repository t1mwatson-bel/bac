# -*- coding: utf-8 -*-
import logging
import re
import sqlite3
from datetime import datetime
from telegram import Update
from telegram.ext import (
    Application,
    MessageHandler,
    filters,
    ContextTypes
)
import pytz

# ======== НАСТРОЙКИ ========
TOKEN = "1163348874:AAFgZEXveILvD4MbhQ8jiLTwIxs4puYhmq0"
INPUT_CHANNEL_ID = -1003469691743
OUTPUT_CHANNEL_ID = -1003842401391
DB_FILE = "predictions.db"
# ===========================

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ===== ТАБЛИЦА МАСТЕЙ ПО НОМЕРУ ИГРЫ =====
SUIT_BY_LAST_DIGIT = {
    1: '♠️', 5: '♠️',
    2: '♥️', 6: '♥️',
    3: '♦️', 7: '♦️',
    4: '♣️', 8: '♣️'
}

def get_expected_suit(game_num):
    """Возвращает масть по последней цифре номера игры"""
    last_digit = game_num % 10
    return SUIT_BY_LAST_DIGIT.get(last_digit)

# ===== БАЗА ДАННЫХ =====
class Database:
    def __init__(self, db_file):
        self.conn = sqlite3.connect(db_file, check_same_thread=False)
        self.create_tables()
    
    def create_tables(self):
        self.conn.execute('''
            CREATE TABLE IF NOT EXISTS games (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_num INTEGER UNIQUE,
                player_cards TEXT,
                dealer_cards TEXT,
                has_r_tag BOOLEAN,
                has_x_tag BOOLEAN,
                is_complete BOOLEAN,
                game_time TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        self.conn.commit()
    
    def add_game(self, game_data):
        try:
            self.conn.execute('''
                INSERT OR IGNORE INTO games (
                    game_num, player_cards, dealer_cards,
                    has_r_tag, has_x_tag, is_complete, game_time
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                game_data['game_num'],
                str(game_data['player_cards']),
                str(game_data['dealer_cards']),
                game_data['has_r_tag'],
                game_data['has_x_tag'],
                game_data['is_complete'],
                game_data['timestamp']
            ))
            self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"Ошибка сохранения игры: {e}")
            return False

# ===== ПАРСИНГ КАРТ =====
def normalize_suit(s):
    if not s:
        return None
    s = str(s).strip()
    s = re.sub(r'[\uFE0F\u20E3]', '', s)
    
    if s in ('♥', '❤', '♡'):
        return '♥️'
    if s in ('♠', '♤'):
        return '♠️'
    if s in ('♣', '♧'):
        return '♣️'
    if s in ('♦', '♢'):
        return '♦️'
    return None

def clean_text_from_tags(text):
    """Удаляет все хэштеги и спецсимволы, оставляя только карты"""
    text = re.sub(r'#\w+', '', text)
    text = re.sub(r'[🔵🔴🟢🟡🟣⚫⚪🔘🟠🟤🟩]', '', text)
    text = text.replace('✅', '').replace('☑️', '').replace('🔰', '')
    return text

def parse_cards_from_text(text):
    cards = []
    i = 0
    while i < len(text):
        if text[i].isspace():
            i += 1
            continue
        
        value = None
        if i < len(text) and text[i].isdigit():
            if i+1 < len(text) and text[i+1].isdigit():
                value = text[i:i+2]
                i += 2
            else:
                value = text[i]
                i += 1
        elif i < len(text) and text[i] in 'JQKA':
            value = text[i]
            i += 1
        else:
            i += 1
            continue
        
        if i < len(text):
            suit = normalize_suit(text[i])
            if suit:
                cards.append({'value': value, 'suit': suit})
            i += 1
    
    return cards

def parse_game_data(text):
    """Парсит игру и собирает все признаки"""
    match = re.search(r'#N(\d+)', text)
    if not match:
        return None
    game_num = int(match.group(1))
    
    is_complete = '✅' in text or '☑️' in text or '🔰' in text
    has_r_tag = '#R' in text
    has_x_tag = '#X' in text
    
    # Если есть #X или #R - игра не подходит для прогнозов
    if has_x_tag or has_r_tag:
        logger.info(f"⏭ Игра #{game_num} пропущена: #X={has_x_tag}, #R={has_r_tag}")
    
    parts = text.split('-')
    if len(parts) < 2:
        return None
    
    left_part = parts[0]
    right_part = parts[1].split('#')[0]
    
    left_part = clean_text_from_tags(left_part)
    right_part = clean_text_from_tags(right_part)
    
    player_cards = parse_cards_from_text(left_part)
    dealer_cards = parse_cards_from_text(right_part)
    
    logger.info(f"📊 Парсинг #{game_num}: игрок {[c['value']+c['suit'] for c in player_cards]}, дилер {[c['value']+c['suit'] for c in dealer_cards]}, завершена={is_complete}")
    
    return {
        'game_num': game_num,
        'player_cards': player_cards,
        'dealer_cards': dealer_cards,
        'has_r_tag': has_r_tag,
        'has_x_tag': has_x_tag,
        'is_complete': is_complete,
        'timestamp': datetime.now(pytz.timezone('Europe/Moscow'))
    }

# ===== ПРОГНОЗЫ =====
class PredictionBot:
    def __init__(self, db):
        self.db = db
        self.predictions = {}  # target_game -> pred
        self.next_id = 1
        self.stats = {'total': 0, 'wins': 0, 'losses': 0}
    
    def analyze_game(self, game_data):
        """Анализирует игру по табличному алгоритму"""
        # Не берем игры с #X или #R
        if game_data['has_x_tag'] or game_data['has_r_tag']:
            return None
            
        game_num = game_data['game_num']
        expected_suit = get_expected_suit(game_num)
        if not expected_suit:
            return None
        
        # Ищем первую картинку нужной масти у игрока
        picture_position = None
        picture_value = None
        
        for i, card in enumerate(game_data['player_cards'], 1):
            if card.get('suit') == expected_suit and card.get('value') in ['J', 'Q', 'K', 'A']:
                picture_position = i
                picture_value = card['value']
                break
        
        if not picture_position:
            return None
        
        # Цель = номер игры + позиция картинки
        target_game = game_num + picture_position
        
        # Создаем прогноз
        if target_game not in self.predictions:
            pred = {
                'id': self.next_id,
                'source': game_num,
                'suit': expected_suit,
                'picture': picture_value,
                'position': picture_position,
                'targets': [target_game, target_game + 1, target_game + 2],
                'attempt': 0,
                'status': 'pending',
                'msg_id': None
            }
            self.predictions[target_game] = pred
            self.next_id += 1
            logger.info(f"📊 ПРОГНОЗ #{pred['id']}: игра #{game_num} (поз.{picture_position}) → цель #{target_game} масть {expected_suit}")
            return pred
        
        return None
    
    def check_game(self, game_num, game_data):
        """Проверяет игру на совпадение с активными прогнозами"""
        results = []
        
        for target, pred in list(self.predictions.items()):
            if int(target) != int(game_num):
                continue
            
            # Ищем любую карту нужной масти ТОЛЬКО у игрока
            found = False
            for card in game_data['player_cards']:
                if card.get('suit') == pred['suit']:
                    found = True
                    break
            
            if found:
                pred['status'] = 'win'
                pred['win_game'] = game_num
                self.stats['wins'] += 1
                self.stats['total'] += 1
                results.append(('win', pred))
                del self.predictions[target]
            
            elif pred['attempt'] < 2:
                pred['attempt'] += 1
                next_target = pred['targets'][pred['attempt']]
                self.predictions[next_target] = pred
                del self.predictions[target]
                results.append(('dogon', pred))
            
            else:
                pred['status'] = 'loss'
                self.stats['losses'] += 1
                self.stats['total'] += 1
                results.append(('loss', pred))
                del self.predictions[target]
        
        return results

# ===== ФОРМАТИРОВАНИЕ СООБЩЕНИЙ =====
def format_prediction(pred):
    text = f"🎯 *ПРОГНОЗ #{pred['id']}*\n━━━━━━━━━━━━━━━━━━━━━━\n\n"
    text += f"📊 *Анализ игры* #{pred['source']}\n"
    text += f"📌 *Позиция картинки:* {pred['position']}\n"
    text += f"🎯 *Цель:* игра #{pred['targets'][0]}\n\n"
    text += f"🃏 *Ждём масть:* {pred['suit']}\n\n"
    text += f"🔄 *Догоны:*\n"
    text += f"  • #{pred['targets'][1]}\n"
    text += f"  • #{pred['targets'][2]}\n\n"
    text += f"⏱ {datetime.now(pytz.timezone('Europe/Moscow')).strftime('%H:%M')} МСК"
    return text

def format_dogon(pred):
    current_target = pred['targets'][pred['attempt']]
    text = f"🔄 *ДОГОН #{pred['id']}*\n━━━━━━━━━━━━━━━━━━━━━━\n\n"
    text += f"Попытка {pred['attempt'] + 1}/3\n"
    text += f"🎯 *Цель:* игра #{current_target}\n\n"
    text += f"🃏 *Ждём масть:* {pred['suit']}\n\n"
    text += f"⏱ {datetime.now(pytz.timezone('Europe/Moscow')).strftime('%H:%M')} МСК"
    return text

def format_result(pred, result_type):
    if result_type == 'win':
        text = f"✅ *ПРОГНОЗ #{pred['id']} ЗАШЁЛ!*\n━━━━━━━━━━━━━━━━━━━━━━\n\n"
        text += f"📊 *Анализ игры* #{pred['source']}\n"
        text += f"🎯 *Зашло в игре* #{pred['win_game']}✅\n\n"
        text += f"🃏 *Масть {pred['suit']} появилась у игрока*\n\n"
    else:
        text = f"❌ *ПРОГНОЗ #{pred['id']} НЕ ЗАШЁЛ*\n━━━━━━━━━━━━━━━━━━━━━━\n\n"
        text += f"📊 *Анализ игры* #{pred['source']}\n"
        text += f"🎯 *Масть {pred['suit']} не появилась у игрока за 3 игры*\n\n"
    
    text += f"⏱ {datetime.now(pytz.timezone('Europe/Moscow')).strftime('%H:%M')} МСК"
    return text

# ===== ОБРАБОТЧИК СООБЩЕНИЙ =====
async def handle_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if update.channel_post:
            message = update.channel_post
        elif update.edited_channel_post:
            message = update.edited_channel_post
        else:
            return
        
        text = message.text
        if not text:
            return
        
        game_data = parse_game_data(text)
        if not game_data:
            return
        
        game_num = game_data['game_num']
        logger.info(f"📥 Игра #{game_num}: R={game_data['has_r_tag']}, X={game_data['has_x_tag']}, complete={game_data['is_complete']}")
        
        # Сохраняем в базу
        context.bot_data['db'].add_game(game_data)
        
        # Проверяем активные прогнозы
        results = context.bot_data['predictor'].check_game(game_num, game_data)
        
        for result in results:
            result_type, pred = result
            
            if result_type in ['win', 'loss']:
                text = format_result(pred, result_type)
                if pred.get('msg_id'):
                    try:
                        await context.bot.edit_message_text(
                            chat_id=OUTPUT_CHANNEL_ID,
                            message_id=pred['msg_id'],
                            text=text,
                            parse_mode='Markdown'
                        )
                        logger.info(f"✏️ Прогноз #{pred['id']} обновлён")
                    except Exception as e:
                        logger.error(f"Ошибка редактирования: {e}")
                else:
                    msg = await context.bot.send_message(
                        chat_id=OUTPUT_CHANNEL_ID,
                        text=text,
                        parse_mode='Markdown'
                    )
                    pred['msg_id'] = msg.message_id
            
            elif result_type == 'dogon':
                if pred.get('msg_id'):
                    try:
                        await context.bot.edit_message_text(
                            chat_id=OUTPUT_CHANNEL_ID,
                            message_id=pred['msg_id'],
                            text=format_dogon(pred),
                            parse_mode='Markdown'
                        )
                        logger.info(f"✏️ Догон #{pred['id']} обновлён")
                    except Exception as e:
                        logger.error(f"Ошибка редактирования догона: {e}")
        
        # Создаём новые прогнозы (только для завершенных игр)
        if game_data['is_complete']:
            new_pred = context.bot_data['predictor'].analyze_game(game_data)
            if new_pred:
                text = format_prediction(new_pred)
                msg = await context.bot.send_message(
                    chat_id=OUTPUT_CHANNEL_ID,
                    text=text,
                    parse_mode='Markdown'
                )
                new_pred['msg_id'] = msg.message_id
                logger.info(f"📤 Новый прогноз #{new_pred['id']}")
        
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}", exc_info=True)

def main():
    print("\n" + "="*70)
    print("🤖 БОТ - ТАБЛИЧНЫЙ АЛГОРИТМ")
    print("="*70)
    print(f"📥 Вход: {INPUT_CHANNEL_ID}")
    print(f"📤 Выход: {OUTPUT_CHANNEL_ID}")
    print("\n✅ АЛГОРИТМ:")
    print("  • Смотрим масть по таблице (последняя цифра)")
    print("  • Ищем ПЕРВУЮ картинку (J,Q,K,A) этой масти у игрока")
    print("  • Цель = номер игры + позиция картинки")
    print("  • Прогноз: любая карта этой масти у игрока")
    print("  • Догоны: цель+1, цель+2")
    print("\n❌ ЗАПРЕТ:")
    print("  • Игры с #X или #R не дают прогнозы")
    print("  • При проверке теги не важны")
    print("="*70 + "\n")
    
    db = Database(DB_FILE)
    predictor = PredictionBot(db)
    
    app = Application.builder().token(TOKEN).build()
    app.bot_data['db'] = db
    app.bot_data['predictor'] = predictor
    
    app.add_handler(MessageHandler(
        filters.Chat(INPUT_CHANNEL_ID) & filters.TEXT,
        handle_game
    ))
    
    try:
        app.run_polling(
            allowed_updates=['channel_post', 'edited_channel_post'],
            drop_pending_updates=True
        )
    except KeyboardInterrupt:
        logger.info("👋 Бот остановлен")

if __name__ == "__main__":
    main()