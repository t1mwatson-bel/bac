import asyncio
import logging
import sqlite3
from datetime import datetime
import re
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ======================= КОНФИГУРАЦИЯ =======================
TOKEN = "1163348874:AAFgZEXveILvD4MbhQ8jiLTwIxs4puYhmq0"
INPUT_CHANNEL_ID = -1003469691743
OUTPUT_CHANNEL_ID = -1003842401391
ADMIN_ID = 683219603

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

def init_db():
    conn = sqlite3.connect('signals.db', check_same_thread=False)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS signals
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                  from_game INTEGER, to_game INTEGER, suit TEXT, 
                  status TEXT DEFAULT 'pending', created_at TIMESTAMP)''')
    c.execute('''CREATE TABLE IF NOT EXISTS games
                 (game_number INTEGER PRIMARY KEY, first_suit TEXT, suit_emoji TEXT, raw_data TEXT)''')
    conn.commit()
    conn.close()

class BaccaratParser:
    SUIT_MAP = {
        '♥️': ('♥️', 'червы'), '♥': ('♥️', 'червы'), '❤': ('♥️', 'червы'), '♡': ('♥️', 'червы'),
        '♠️': ('♠️', 'пики'), '♠': ('♠️', 'пики'), '♤': ('♠️', 'пики'),
        '♣️': ('♣️', 'трефы'), '♣': ('♣️', 'трефы'), '♧': ('♣️', 'трефы'),
        '♦️': ('♦️', 'бубны'), '♦': ('♦️', 'бубны'), '♢': ('♦️', 'бубны')
    }
    
    @staticmethod
    def parse_game(text):
        """👨‍💼 Парсит ТОЛЬКО ЛЕВУЮ РУКУ ИГРОКА"""
        game_match = re.search(r'#N(\d+)', text)
        if not game_match: return None
        
        game_num = int(game_match.group(1))
        hand_match = re.search(r'(\d+)\s*\(([^\)]+)\)', text)
        if not hand_match: return None
        
        player_cards_str = hand_match.group(2)
        
        # Ищем первую масть в ЛЕВОЙ РУКИ
        for symbol, (emoji, name) in BaccaratParser.SUIT_MAP.items():
            if re.search(re.escape(symbol), player_cards_str):
                return {
                    'game_number': game_num,
                    'suit_emoji': emoji,
                    'suit_name': name,
                    'raw_data': text
                }
        return None

analyzer = BaccaratParser()

class SignalProcessor:
    SUIT_EMOJI_MAP = {
        'пики': '♠️', 'бубны': '♦️', 
        'червы': '♥️', 'трефы': '♣️'
    }
    
    @staticmethod
    def is_even_decade(game_num):
        return (game_num // 10) % 2 == 0
    
    @staticmethod
    def get_next_suit(current_suit, game_num):
        """🎲 Алгоритм смены мастей"""
        rules_even = {
            'пики': 'бубны', 'бубны': 'пики',
            'червы': 'трефы', 'трефы': 'червы'
        }
        rules_odd = {
            'пики': 'трефы', 'трефы': 'пики',
            'червы': 'бубны', 'бубны': 'червы'
        }
        rules = rules_even if SignalProcessor.is_even_decade(game_num) else rules_odd
        return rules.get(current_suit, current_suit)
    
    @staticmethod
    def process_signal(game_num, first_suit):
        """✅ Только статус сигнала + новый сигнал"""
        conn = sqlite3.connect('signals.db', check_same_thread=False)
        c = conn.cursor()
        
        signals_text = []
        
        # 1️⃣ ПРОВЕРЯЕМ ВХОДЯЩИЙ сигнал
        c.execute('SELECT from_game, suit FROM signals WHERE to_game=? AND status="pending"', (game_num,))
        incoming_signal = c.fetchone()
        
        if incoming_signal:
            from_game, expected_suit = incoming_signal
            if expected_suit == first_suit:
                signals_text.append(f"✅ Сигнал #{from_game}→#{game_num} ПОДТВЕРЖДЕН")
                c.execute('UPDATE signals SET status="confirmed" WHERE to_game=?', (game_num,))
            else:
                signals_text.append(f"❌ Сигнал #{from_game}→#{game_num} НЕ ПОДТВЕРЖДЕН")
                c.execute('UPDATE signals SET status="failed" WHERE to_game=?', (game_num,))
        
        # 2️⃣ Создаем НОВЫЙ сигнал
        new_suit = SignalProcessor.get_next_suit(first_suit, game_num)
        target_game = game_num + 3
        
        new_suit_emoji = SignalProcessor.SUIT_EMOJI_MAP[new_suit]
        
        c.execute('INSERT INTO signals (from_game, to_game, suit, created_at) VALUES (?, ?, ?, ?)',
                 (game_num, target_game, new_suit, datetime.now()))
        signals_text.append(f"🆕 Сигнал #{game_num}→#{target_game}: {new_suit_emoji}")
        
        conn.commit()
        conn.close()
        return signals_text

async def handle_channel_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """📡 Автообработка из канала"""
    if not (update.channel_post and update.channel_post.chat.id == INPUT_CHANNEL_ID):
        return
    
    text = update.channel_post.text or ""
    parsed = analyzer.parse_game(text)
    
    if not parsed: return
    
    game_num = parsed['game_number']
    suit_emoji = parsed['suit_emoji']
    suit_name = parsed['suit_name']
    
    # 💾 Сохраняем игру
    conn = sqlite3.connect('signals.db', check_same_thread=False)
    c = conn.cursor()
    c.execute('INSERT OR REPLACE INTO games (game_number, first_suit, suit_emoji, raw_data) VALUES (?, ?, ?, ?)',
             (game_num, suit_name, suit_emoji, text))
    conn.commit()
    
    # 🔍 Обрабатываем сигналы
    signals = SignalProcessor.process_signal(game_num, suit_name)
    conn.close()
    
    # 🎨 ИДЕАЛЬНЫЙ формат вывода
    output_text = f"""🎮 *ИГРА #{game_num}*

👨‍💼 *ИГРОК* (левая рука): ⭐{suit_emoji} {suit_name}

📡 *СИГНАЛЫ:*
{chr(10).join(signals)}"""
    
    try:
        await context.bot.send_message(
            chat_id=OUTPUT_CHANNEL_ID, 
            text=output_text, 
            parse_mode='Markdown'
        )
        logger.info(f"📤 Отчет #{game_num}: {signals}")
    except Exception as e:
        logger.error(f"❌ Ошибка отправки: {e}")

# ==================== КОМАНДЫ ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [['📊 Статистика']]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        """🎰 *🤖 МАКС МОСКВА* 🎯

👨‍💼 *ЛЕВАЯ РУКА ИГРОКА*
• ♥️ = *червы*
• ♦️ = *бубны*  
• ♠️ = *пики*
• ♣️ = *трефы*

🔄 *ЛОГИКА СИГНАЛОВ*:
N → N+3 → статус → новый сигнал

📡 *АВТОРАБОТА* в канале""",
        reply_markup=reply_markup, 
        parse_mode='Markdown'
    )

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect('signals.db', check_same_thread=False)
    c = conn.cursor()
    
    c.execute('SELECT COUNT(*) FROM games')
    games = c.fetchone()[0]
    
    c.execute('SELECT COUNT(*) FROM signals WHERE status="confirmed"')
    confirmed = c.fetchone()[0]
    
    c.execute('SELECT COUNT(*) FROM signals WHERE status="failed"')
    failed = c.fetchone()[0]
    
    c.execute('SELECT COUNT(*) FROM signals')
    total_signals = c.fetchone()[0]
    
    conn.close()
    
    accuracy = (confirmed / total_signals * 100) if total_signals else 0
    
    await update.message.reply_text(
        f"""📊 *СТАТИСТИКА МАКС МОСКВА* 🎯

🎮 Игр обработано: *{games}*
📡 Всего сигналов: *{total_signals}*
✅ Подтверждено: *{confirmed}*
❌ Не подтверждено: *{failed}*
🎯 Точность: *{accuracy:.1f}%*""",
        parse_mode='Markdown'
    )

def main():
    init_db()
    print("🤖 *МАКС МОСКВА* - ИДЕАЛЬНЫЙ БОТ! 🎰✨")
    print(f"📥 Канал входа: {INPUT_CHANNEL_ID}")
    print(f"📤 Канал выхода: {OUTPUT_CHANNEL_ID}")
    
    app = Application.builder().token(TOKEN).build()
    
    # Команды
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Regex(r'^(📊 Статистика)$'), stats))
    
    # ГЛАВНЫЙ обработчик канала
    app.add_handler(MessageHandler(
        filters.Chat(chat_id=INPUT_CHANNEL_ID) & filters.TEXT,
        handle_channel_message
    ))
    
    app.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
