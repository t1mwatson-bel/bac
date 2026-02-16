import asyncio
import logging
import sqlite3
from datetime import datetime
import json
import re
from collections import defaultdict
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler

# Включаем логирование
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Состояния разговора
ENTER_GAME, PREDICT_GAME = range(2)

# Токен бота - ЗАМЕНИТЕ!
TOKEN = "1163348874:AAFgZEXveILvD4MbhQ8jiLTwIxs4puYhmq0"
INPUT_CHANNEL_ID = -1003469691743
OUTPUT_CHANNEL_ID = -1003842401391
ADMIN_ID = 683219603'

# Инициализация БД
def init_db():
    conn = sqlite3.connect('baccarat_stats.db', check_same_thread=False)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS games_analysis
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, game_number INTEGER,
                  game_date TIMESTAMP, hand1_score INTEGER, hand1_cards TEXT, hand2_score INTEGER,
                  hand2_cards TEXT, total_points INTEGER, winner TEXT, first_suit TEXT,
                  predicted_suit TEXT, is_confirmation BOOLEAN DEFAULT 0, raw_data TEXT)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS signals
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, from_game INTEGER,
                  to_game INTEGER, suit TEXT, is_active BOOLEAN DEFAULT 1, is_confirmed BOOLEAN DEFAULT 0,
                  created_at TIMESTAMP, confirmed_at TIMESTAMP)''')
    conn.commit()
    conn.close()

# Парсер формата "Макс Москва"
class BaccaratParser:
    def __init__(self):
        self.suit_map = {'♠️': 'пики', '♣️': 'трефы', '♥️': 'черви', '♦️': 'бубны'}
        self.card_values = {'A':1,'2':2,'3':3,'4':4,'5':5,'6':6,'7':7,'8':8,'9':9,'10':0,'J':0,'Q':0,'K':0}
    
    def parse_game(self, text):
        try:
            game_num = int(re.search(r'#N(\d+)', text).group(1))
            total_points = int(re.search(r'#T(\d+)', text).group(1))
            
            hands = re.search(r'([✅🔰]?\d+)\(([^)]+)\)\s*-\s*([✅🔰]?\d+)\(([^)]+)\)', text)
            if not hands: return None
            
            h1_raw, h1_cards, h2_raw, h2_cards = hands.groups()
            h1_score = int(re.sub(r'[✅🔰]', '', h1_raw))
            h2_score = int(re.sub(r'[✅🔰]', '', h2_raw))
            
            winner = 'hand1' if '✅' in h1_raw or '🔰' in h1_raw else 'hand2' if '✅' in h2_raw or '🔰' in h2_raw else \
                    ('hand1' if h1_score > h2_score else 'hand2' if h2_score > h1_score else 'tie')
            
            h1_cards_parsed = self.parse_cards(h1_cards)
            h2_cards_parsed = self.parse_cards(h2_cards)
            first_suit = h1_cards_parsed[0]['suit'] if h1_cards_parsed else None
            
            return {
                'game_number': game_num, 'hand1_score': h1_score, 'hand1_cards': h1_cards_parsed,
                'hand2_score': h2_score, 'hand2_cards': h2_cards_parsed, 'total_points': total_points,
                'winner': winner, 'first_suit': first_suit, 'raw_data': text
            }
        except:
            return None
    
    def parse_cards(self, cards_str):
        cards = []
        for item in cards_str.split():
            if len(item) >= 2:
                value = '10' if item.startswith('10') else item[0]
                suit_symbol = item[2:] if item.startswith('10') else item[1:]
                suit = self.suit_map.get(suit_symbol, 'unknown')
                cards.append({'value': value, 'suit': suit, 'symbol': suit_symbol})
        return cards

# Алгоритм сигналов
class SignalAlgorithm:
    def __init__(self):
        self.parser = BaccaratParser()
    
    def is_even_decade(self, game_num): return (game_num // 10) % 2 == 0
    
    def get_signal_suit(self, game_num, first_suit):
        rules = {True: {'пики':'бубны','бубны':'пики','черви':'трефы','трефы':'черви'},
                False: {'пики':'трефы','трефы':'пики','черви':'бубны','бубны':'черви'}}
        return rules[self.is_even_decade(game_num)].get(first_suit, first_suit)
    
    async def process_signal(self, user_id, game_num, first_suit):
        conn = sqlite3.connect('baccarat_stats.db', check_same_thread=False)
        c = conn.cursor()
        
        signals, predicted, confirmed = [], None, False
        
        # Проверяем активный сигнал
        c.execute('SELECT id, from_game, suit FROM signals WHERE user_id=? AND to_game=? AND is_active=1', (user_id, game_num))
        active = c.fetchone()
        if active:
            sid, from_g, suit = active
            if suit == first_suit:
                confirmed = True
                c.execute('UPDATE signals SET is_confirmed=1, is_active=0, confirmed_at=? WHERE id=?', 
                         (datetime.now(), sid))
                c.execute('INSERT INTO signals(user_id,from_game,to_game,suit,is_active,created_at) VALUES(?,?,?,?,1,?)',
                         (user_id, game_num, game_num+1, suit, datetime.now()))
                signals.append(f"✅<b>ПОДТВЕРЖДЕНИЕ!</b> #{from_g}→#{game_num} {suit} ✓")
                predicted = suit
            else:
                c.execute('UPDATE signals SET is_active=0 WHERE id=?', (sid,))
                signals.append(f"❌ #{from_g}→#{game_num}: <i>{suit}≠{first_suit}</i>")
        
        # Новый сигнал N→N+3
        if first_suit:
            signal_suit = self.get_signal_suit(game_num, first_suit)
            target = game_num + 3
            c.execute('SELECT id FROM signals WHERE user_id=? AND to_game=? AND is_active=1', (user_id, target))
            if not c.fetchone():
                c.execute('INSERT INTO signals(user_id,from_game,to_game,suit,is_active,created_at) VALUES(?,?,?,?,1,?)',
                         (user_id, game_num, target, signal_suit, datetime.now()))
                signals.append(f"🆕<b>СИГНАЛ!</b> #{game_num}→#{target} <b>{signal_suit}</b>")
                if not predicted: predicted = signal_suit
        
        conn.commit()
        conn.close()
        return signals, predicted, confirmed

# Анализатор
analyzer = SignalAlgorithm()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [['📊 Игра', '📈 Статистика'], ['🔔 Сигналы', '🔮 Прогноз'], 
                ['📋 История', '🧪 Тест'], ['ℹ️ Помощь']]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        '🎰 <b>МАКС МОСКВА - БАККАРА БОТ</b>\n\n'
        '🔥 Сигналы: N→N+3 | Повтор: N+1 при ✓\n\n'
        'Выберите:', reply_markup=reply_markup, parse_mode='HTML')

async def enter_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        '📝 <b>ФОРМАТ Макс Москва:</b>\n'
        '#N803. 0(2♠️ J♥️ A♥️) - ✅6(J♦️ 6♦️) #T9\n\n'
        'Введите игру:', parse_mode='HTML')
    return ENTER_GAME

async def process_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    
    parsed = BaccaratParser().parse_game(text)
    if not parsed:
        keyboard = [['◀️ Главное меню']]
        await update.message.reply_text('❌ Неверный формат!', reply_markup=ReplyKeyboardMarkup([['◀️ Главное меню']], resize_keyboard=True))
        return ENTER_GAME
    
    signals, predicted, confirmed = await analyzer.process_signal(user_id, parsed['game_number'], parsed['first_suit'])
    
    # Сохраняем в БД
    conn = sqlite3.connect('baccarat_stats.db', check_same_thread=False)
    c = conn.cursor()
    c.execute('''INSERT INTO games_analysis(user_id,game_number,game_date,hand1_score,hand1_cards,
                 hand2_score,hand2_cards,total_points,winner,first_suit,predicted_suit,is_confirmation,raw_data)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''', (user_id, parsed['game_number'], datetime.now(),
                 parsed['hand1_score'], json.dumps(parsed['hand1_cards']), parsed['hand2_score'],
                 json.dumps(parsed['hand2_cards']), parsed['total_points'], parsed['winner'],
                 parsed['first_suit'], predicted, confirmed, text))
    conn.commit()
    conn.close()
    
    # Формируем ответ
    h1_cards = ' '.join(f"{c['value']}{c['symbol']}" for c in parsed['hand1_cards'])
    h2_cards = ' '.join(f"{c['value']}{c['symbol']}" for c in parsed['hand2_cards'])
    winner = {'hand1':'1-я рука ✓', 'hand2':'2-я рука ✓', 'tie':'Ничья'}[parsed['winner']]
    
    result = [f'🎮 <b>ИГРА #{parsed["game_number"]}</b>',
              f'🤚 1: {h1_cards} ({parsed["hand1_score"]}) ⭐{parsed["first_suit"]}',
              f'✋ 2: {h2_cards} ({parsed["hand2_score"]})',
              f'🏆 <b>{winner}</b>', '']
    result.extend(signals)
    
    keyboard = [['◀️ Главное меню']]
    await update.message.reply_text('\n'.join(result), reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True), parse_mode='HTML')
    return ConversationHandler.END

async def predict_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('🔮 Номер игры для прогноза:')
    return PREDICT_GAME

async def process_predict(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        game_num = int(update.message.text)
        user_id = update.effective_user.id
        
        conn = sqlite3.connect('baccarat_stats.db', check_same_thread=False)
        c = conn.cursor()
        c.execute('SELECT from_game,suit FROM signals WHERE user_id=? AND to_game=? AND is_active=1', (user_id, game_num))
        signals = c.fetchall()
        c.execute('SELECT first_suit FROM games_analysis WHERE user_id=? AND game_number=?', (user_id, game_num))
        actual = c.fetchone()
        conn.close()
        
        if not signals:
            await update.message.reply_text(f'📭 Нет сигналов на #{game_num}')
        else:
            result = [f'🔮 <b>ПРОГНОЗ #{game_num}</b>']
            for from_g, suit in signals:
                result.append(f'💎 #{from_g} → <b>{suit}</b>')
            if actual:
                status = '✅ ✓' if actual[0] == signals[0][1] else '❌ ✗'
                result.append(f'📊 Факт: <b>{actual[0]}</b> {status}')
            await update.message.reply_text('\n'.join(result), parse_mode='HTML')
    except:
        await update.message.reply_text('❌ Неверный номер!')
    
    keyboard = [['◀️ Главное меню']]
    await update.message.reply_text('◀️ Главное меню', reply_markup=ReplyKeyboardMarkup([['◀️ Главное меню']], resize_keyboard=True))
    return ConversationHandler.END

async def show_signals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = sqlite3.connect('baccarat_stats.db', check_same_thread=False)
    c = conn.cursor()
    c.execute('SELECT from_game,to_game,suit,created_at FROM signals WHERE user_id=? AND is_active=1 ORDER BY to_game', (user_id,))
    signals = c.fetchall()
    conn.close()
    
    if not signals:
        await update.message.reply_text('📭 Нет активных сигналов')
        return
    
    result = ['🔔 <b>АКТИВНЫЕ СИГНАЛЫ</b>']
    for from_g, to_g, suit, created in signals:
        result.append(f'#{from_g} → <b>#{to_g}: {suit}</b>')
    await update.message.reply_text('\n'.join(result), parse_mode='HTML')

async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = sqlite3.connect('baccarat_stats.db', check_same_thread=False)
    c = conn.cursor()
    c.execute('SELECT COUNT(*),SUM(CASE WHEN winner="hand1" THEN 1 ELSE 0 END),SUM(CASE WHEN winner="hand2" THEN 1 ELSE 0 END) FROM games_analysis WHERE user_id=?', (user_id,))
    games = c.fetchone() or (0,0,0)
    c.execute('SELECT COUNT(*),SUM(is_confirmed) FROM signals WHERE user_id=?', (user_id,))
    sigs = c.fetchone() or (0,0)
    conn.close()
    
    await update.message.reply_text(
        f'📈 <b>СТАТИСТИКА</b>\n'
        f'Игр: <b>{games[0]}</b>\n'
        f'1-я: <b>{games[1]}</b> | 2-я: <b>{games[2]}</b>\n\n'
        f'🔔 Сигналов: <b>{sigs[0]}</b>\n'
        f'✅ ✓: <b>{sigs[1]}</b>', parse_mode='HTML')

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [['◀️ Главное меню']]
    await update.message.reply_text('Отменено.', reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))
    return ConversationHandler.END

def main():
    init_db()
    app = Application.builder().token(TOKEN).build()
    
    # Conversation handlers
    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('^(📊 Игра|Ввести игру|Игра)$'), enter_game)],
        states={
            ENTER_GAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_game)],
            PREDICT_GAME: [MessageHandler(filters.Regex('^\d+$'), process_predict)],
        },
        fallbacks=[CommandHandler('cancel', cancel), MessageHandler(filters.Regex('^◀️'), cancel)]
    )
    
    app.add_handler(CommandHandler('start', start))
    app.add_handler(conv_handler)
    app.add_handler(MessageHandler(filters.Regex('^(📈 Статистика|Статистика)$'), show_stats))
    app.add_handler(MessageHandler(filters.Regex('^(🔔 Сигналы|Сигналы)$'), show_signals))
    app.add_handler(MessageHandler(filters.Regex('^(🔮 Прогноз|Прогноз)$'), predict_game))
    app.add_handler(MessageHandler(filters.Regex('^◀️ Главное меню$'), start))
    
    print("🤖 Макс Москва v20.7 запущен!")
    app.run_polling()

if __name__ == '__main__':
    main()
