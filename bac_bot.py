import asyncio
import logging
import sqlite3
from datetime import datetime
import json
import re
from collections import defaultdict
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler

# ======================= КОНФИГУРАЦИЯ =======================
TOKEN = "1163348874:AAFgZEXveILvD4MbhQ8jiLTwIxs4puYhmq0"
INPUT_CHANNEL_ID = -1003469691743
OUTPUT_CHANNEL_ID = -1003842401391
ADMIN_ID = 683219603
# ============================================================

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

ENTER_GAME, PREDICT_GAME = range(2)

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

class BaccaratParser:
    def __init__(self):
        self.suit_map = {'♠️': 'пики', '♣️': 'трефы', '♥️': 'черви', '♦️': 'бубны'}
        self.card_values = {'A':1,'2':2,'3':3,'4':4,'5':5,'6':6,'7':7,'8':8,'9':9,'10':0,'J':0,'Q':0,'K':0}
    
    def parse_game(self, text):
        try:
            game_num_match = re.search(r'#N(\d+)', text)
            total_match = re.search(r'#T(\d+)', text)
            if not game_num_match or not total_match: return None
            
            game_num = int(game_num_match.group(1))
            total_points = int(total_match.group(1))
            
            hands = re.search(r'([✅🔰]?\d+)\(([^)]+)\)\s*-\s*([✅🔰]?\d+)\(([^)]+)\)', text)
            if not hands: return None
            
            h1_raw, h1_cards, h2_raw, h2_cards = hands.groups()
            h1_score = int(re.sub(r'[✅🔰]', '', h1_raw))
            h2_score = int(re.sub(r'[✅🔰]', '', h2_raw))
            
            if '✅' in h1_raw or '🔰' in h1_raw: winner = 'hand1'
            elif '✅' in h2_raw or '🔰' in h2_raw: winner = 'hand2'
            else: winner = 'hand1' if h1_score > h2_score else 'hand2' if h2_score > h1_score else 'tie'
            
            h1_cards_parsed = self.parse_cards(h1_cards)
            h2_cards_parsed = self.parse_cards(h2_cards)
            first_suit = h1_cards_parsed[0]['suit'] if h1_cards_parsed else None
            
            return {
                'game_number': game_num, 'hand1_score': h1_score, 'hand1_cards': h1_cards_parsed,
                'hand2_score': h2_score, 'hand2_cards': h2_cards_parsed, 'total_points': total_points,
                'winner': winner, 'first_suit': first_suit, 'raw_data': text
            }
        except Exception as e:
            print(f"Parse error: {e}")
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

analyzer = BaccaratParser()

class SignalProcessor:
    @staticmethod
    def is_even_decade(game_num):
        return (game_num // 10) % 2 == 0
    
    @staticmethod
    def get_signal_suit(game_num, first_suit):
        rules_even = {'пики':'бубны','бубны':'пики','черви':'трефы','трефы':'черви'}
        rules_odd = {'пики':'трефы','трефы':'пики','черви':'бубны','бубны':'черви'}
        rules = rules_even if SignalProcessor.is_even_decade(game_num) else rules_odd
        return rules.get(first_suit, first_suit)
    
    @staticmethod
    def process_signal(user_id, game_num, first_suit):
        conn = sqlite3.connect('baccarat_stats.db', check_same_thread=False)
        c = conn.cursor()
        
        signals_text = []
        predicted_suit = None
        is_confirmed = False
        
        # Проверяем активный сигнал на эту игру
        c.execute('SELECT id, from_game, suit FROM signals WHERE user_id=? AND to_game=? AND is_active=1', 
                 (user_id, game_num))
        active_signal = c.fetchone()
        
        if active_signal:
            signal_id, from_game, expected_suit = active_signal
            
            if expected_suit == first_suit:
                # Подтверждение!
                is_confirmed = True
                c.execute('UPDATE signals SET is_confirmed=1, is_active=0, confirmed_at=? WHERE id=?', 
                         (datetime.now(), signal_id))
                
                # Повторный сигнал на следующую игру
                next_game = game_num + 1
                c.execute('INSERT INTO signals (user_id, from_game, to_game, suit, is_active, created_at) VALUES (?, ?, ?, ?, 1, ?)',
                         (user_id, game_num, next_game, expected_suit, datetime.now()))
                
                signals_text.append(f"✅ <b>ПОДТВЕРЖДЕНИЕ!</b> #{from_game}→#{game_num} {expected_suit} ✓")
                signals_text.append(f"🔄 Повтор на #{next_game}: <b>{expected_suit}</b>")
                predicted_suit = expected_suit
            else:
                c.execute('UPDATE signals SET is_active=0 WHERE id=?', (signal_id,))
                signals_text.append(f"❌ #{from_game}→#{game_num}: {expected_suit} ≠ <b>{first_suit}</b>")
        
        # Создаем новый сигнал от текущей игры
        if first_suit:
            signal_suit = SignalProcessor.get_signal_suit(game_num, first_suit)
            target_game = game_num + 3
            
            c.execute('SELECT id FROM signals WHERE user_id=? AND to_game=? AND is_active=1', (user_id, target_game))
            if not c.fetchone():
                c.execute('INSERT INTO signals (user_id, from_game, to_game, suit, is_active, created_at) VALUES (?, ?, ?, ?, 1, ?)',
                         (user_id, game_num, target_game, signal_suit, datetime.now()))
                signals_text.append(f"🆕 <b>НОВЫЙ СИГНАЛ!</b> #{game_num}→#{target_game} <b>{signal_suit}</b>")
                if not predicted_suit:
                    predicted_suit = signal_suit
        
        conn.commit()
        conn.close()
        return signals_text, predicted_suit, is_confirmed

# ==================== HANDLERS ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [['📊 Ввести игру', '🔔 Активные сигналы'], ['🔮 Прогноз', '📈 Статистика'], ['📋 История', 'ℹ️ Помощь']]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
    
    await update.message.reply_text(
        '🎰 <b>🤖 МАКС МОСКВА - БОТ БАККАРЫ</b>\n\n'
        '🔥 <b>СИСТЕМА СИГНАЛОВ:</b>\n'
        '• N → N+3 (основной)\n'
        '• N+1 (повтор при подтверждении)\n\n'
        '<b>Выберите действие:</b>', 
        reply_markup=reply_markup, parse_mode='HTML')

async def handle_game_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    
    parsed = analyzer.parse_game(text)
    if not parsed:
        await update.message.reply_text('❌ <b>Неверный формат!</b>\n\n📝 Пример:\n#N803. 0(2♠️ J♥️ A♥️) - ✅6(J♦️ 6♦️) #T9', parse_mode='HTML')
        return
    
    signals, predicted, confirmed = SignalProcessor.process_signal(user_id, parsed['game_number'], parsed['first_suit'])
    
    # Сохраняем игру
    conn = sqlite3.connect('baccarat_stats.db', check_same_thread=False)
    c = conn.cursor()
    c.execute('''INSERT INTO games_analysis 
                 (user_id, game_number, game_date, hand1_score, hand1_cards, hand2_score, hand2_cards, 
                  total_points, winner, first_suit, predicted_suit, is_confirmation, raw_data)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
             (user_id, parsed['game_number'], datetime.now(), parsed['hand1_score'],
              json.dumps(parsed['hand1_cards']), parsed['hand2_score'],
              json.dumps(parsed['hand2_cards']), parsed['total_points'], parsed['winner'],
              parsed['first_suit'], predicted, confirmed, parsed['raw_data']))
    conn.commit()
    conn.close()
    
    # Формируем красивый отчет
    h1_str = ' '.join(f"{c['value']}{c['symbol']}" for c in parsed['hand1_cards'])
    h2_str = ' '.join(f"{c['value']}{c['symbol']}" for c in parsed['hand2_cards'])
    winner_emojis = {'hand1': '🤚 1-я рука ✓', 'hand2': '✋ 2-я рука ✓', 'tie': '🤝 Ничья'}
    
    report = [
        f'🎮 <b>ИГРА #{parsed["game_number"]}</b>',
        '',
        f'🤚 <b>1-я рука:</b> {h1_str} = {parsed["hand1_score"]}',
        f'✋ <b>2-я рука:</b> {h2_str} = {parsed["hand2_score"]}',
        f'⭐ <b>Первая масть:</b> {parsed["first_suit"]}',
        f'🏆 <b>{winner_emojis[parsed["winner"]]}</b>',
        ''
    ]
    
    if signals:
        report.extend(signals)
    else:
        report.append('📭 Нет сигналов')
    
    report.append('')
    report.append('🔄 <i>Введите следующую игру или /start</i>')
    
    await update.message.reply_text('\n'.join(report), parse_mode='HTML')

async def show_signals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = sqlite3.connect('baccarat_stats.db', check_same_thread=False)
    c = conn.cursor()
    c.execute('SELECT from_game, to_game, suit FROM signals WHERE user_id=? AND is_active=1 ORDER BY to_game', (user_id,))
    signals = c.fetchall()
    conn.close()
    
    if not signals:
        await update.message.reply_text('🔔 <b>Нет активных сигналов</b>', parse_mode='HTML')
        return
    
    report = ['🔔 <b>АКТИВНЫЕ СИГНАЛЫ МАКС МОСКВА</b>', '']
    for from_game, to_game, suit in signals:
        report.append(f'💎 <b>#{from_game} → #{to_game}</b>: {suit}')
    
    await update.message.reply_text('\n'.join(report), parse_mode='HTML')

async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = sqlite3.connect('baccarat_stats.db', check_same_thread=False)
    c = conn.cursor()
    
    c.execute('SELECT COUNT(*), AVG(CASE WHEN winner="hand1" THEN 1.0 ELSE 0 END)*100 FROM games_analysis WHERE user_id=?', (user_id,))
    games_total, win1_rate = c.fetchone() or (0, 0)
    
    c.execute('SELECT COUNT(*), SUM(is_confirmed), AVG(CASE WHEN is_confirmed=1 THEN 1.0 ELSE 0 END)*100 FROM signals WHERE user_id=?', (user_id,))
    sig_total, sig_conf, sig_rate = c.fetchone() or (0, 0, 0)
    
    conn.close()
    
    stats = [
        '📈 <b>СТАТИСТИКА МАКС МОСКВА</b>',
        f'🎮 Всего игр: <b>{games_total}</b>',
        f'🤚 Победа 1-й руки: <b>{win1_rate:.1f}%</b>',
        '',
        f'🔔 Всего сигналов: <b>{sig_total}</b>',
        f'✅ Подтверждено: <b>{sig_conf}</b> ({sig_rate:.1f}%)'
    ]
    
    await update.message.reply_text('\n'.join(stats), parse_mode='HTML')

async def handle_channel_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка сообщений из INPUT_CHANNEL_ID"""
    if update.channel_post and update.channel_post.chat.id == INPUT_CHANNEL_ID:
        text = update.channel_post.text or ""
        if re.search(r'#N\d+', text):  # Если это игра
            parsed = analyzer.parse_game(text)
            if parsed:
                # Обрабатываем как обычную игру для ADMIN_ID
                signals, predicted, confirmed = SignalProcessor.process_signal(ADMIN_ID, parsed['game_number'], parsed['first_suit'])
                
                # Сохраняем в БД для админа
                conn = sqlite3.connect('baccarat_stats.db', check_same_thread=False)
                c = conn.cursor()
                c.execute('''INSERT OR IGNORE INTO games_analysis 
                            (user_id, game_number, game_date, hand1_score, hand1_cards, hand2_score, hand2_cards, 
                             total_points, winner, first_suit, predicted_suit, is_confirmation, raw_data)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                         (ADMIN_ID, parsed['game_number'], datetime.now(), parsed['hand1_score'],
                          json.dumps(parsed['hand1_cards']), parsed['hand2_score'],
                          json.dumps(parsed['hand2_cards']), parsed['total_points'], parsed['winner'],
                          parsed['first_suit'], predicted, confirmed, text))
                conn.commit()
                conn.close()
                
                # Пересылаем анализ в OUTPUT_CHANNEL_ID
                h1_str = ' '.join(f"{c['value']}{c['symbol']}" for c in parsed['hand1_cards'])
                h2_str = ' '.join(f"{c['value']}{c['symbol']}" for c in parsed['hand2_cards'])
                
                output_text = f"""🎮 <b>ИГРА #{parsed['game_number']}</b>

🤚 1: {h1_str} ({parsed['hand1_score']}) ⭐{parsed['first_suit']}
✋ 2: {h2_str} ({parsed['hand2_score']})

🏆 {'1-я ✓' if parsed['winner']=='hand1' else '2-я ✓' if parsed['winner']=='hand2' else 'Ничья'}

📊 {'✅' if confirmed else ''}"""
                
                if signals:
                    output_text += "\n\n" + "\n".join(signals)
                
                await context.bot.send_message(chat_id=OUTPUT_CHANNEL_ID, text=output_text, parse_mode='HTML')

# Главная функция
def main():
    init_db()
    print("🤖 Макс Москва v20.7 - ЗАПУЩЕН!")
    print(f"📱 Канал ВХОД: {INPUT_CHANNEL_ID}")
    print(f"📤 Канал ВЫХОД: {OUTPUT_CHANNEL_ID}")
    print(f"👑 АДМИН: {ADMIN_ID}")
    
    app = Application.builder().token(TOKEN).build()
    
    # Команды
    app.add_handler(CommandHandler("start", start))
    
    # Обработчики кнопок и сообщений
    app.add_handler(MessageHandler(filters.Regex(r'^(📊 Ввести игру|Ввести игру|Игра)$'), handle_game_input))
    app.add_handler(MessageHandler(filters.Regex(r'^(🔔 Активные сигналы|Сигналы)$'), show_signals))
    app.add_handler(MessageHandler(filters.Regex(r'^(📈 Статистика|Статистика)$'), show_stats))
    
    # Канальные сообщения
    app.add_handler(MessageHandler(filters.Chat(chat_id=INPUT_CHANNEL_ID) & filters.Text() & filters.Regex(r'#N\d+'), handle_channel_message))
    
    # Запуск
    app.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
