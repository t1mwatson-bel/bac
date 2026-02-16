import telebot
from telebot import types
import sqlite3
from datetime import datetime
import json
import re
from collections import defaultdict

# Конфигурация - ЗАМЕНИТЕ НА СВОЙ ТОКЕН!
TOKEN = "1163348874:AAFgZEXveILvD4MbhQ8jiLTwIxs4puYhmq0"
INPUT_CHANNEL_ID = -1003469691743
OUTPUT_CHANNEL_ID = -1003842401391
ADMIN_ID = 683219603

# Инициализация базы данных
def init_db():
    conn = sqlite3.connect('baccarat_stats.db', check_same_thread=False)
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS games_analysis
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER,
                  game_number INTEGER,
                  game_date TIMESTAMP,
                  hand1_score INTEGER,
                  hand1_cards TEXT,
                  hand2_score INTEGER,
                  hand2_cards TEXT,
                  total_points INTEGER,
                  winner TEXT,
                  first_suit TEXT,
                  predicted_suit TEXT,
                  is_confirmation BOOLEAN DEFAULT 0,
                  raw_data TEXT)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS suit_stats
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER,
                  game_id INTEGER,
                  suit TEXT,
                  count INTEGER,
                  hand_position TEXT)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS signals
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER,
                  from_game INTEGER,
                  to_game INTEGER,
                  suit TEXT,
                  is_active BOOLEAN DEFAULT 1,
                  is_confirmed BOOLEAN DEFAULT 0,
                  created_at TIMESTAMP,
                  confirmed_at TIMESTAMP)''')
    
    conn.commit()
    conn.close()

# Класс для парсинга формата "Макс Москва"
class BaccaratParser:
    def __init__(self):
        self.suit_map = {
            '♠️': 'пики', '♣️': 'трефы', '♥️': 'черви', '♦️': 'бубны'
        }
        self.card_values = {
            'A': 1, '2': 2, '3': 3, '4': 4, '5': 5, '6': 6, '7': 7, '8': 8, '9': 9,
            '10': 0, 'J': 0, 'Q': 0, 'K': 0
        }
    
    def parse_game(self, text):
        try:
            game_num_match = re.search(r'#N(\d+)', text)
            game_number = int(game_num_match.group(1)) if game_num_match else 0
            
            total_points_match = re.search(r'#T(\d+)', text)
            total_points = int(total_points_match.group(1)) if total_points_match else 0
            
            hands_pattern = r'([✅🔰]?\d+)\(([^)]+)\)\s*-\s*([✅🔰]?\d+)\(([^)]+)\)'
            hands_match = re.search(hands_pattern, text)
            
            if not hands_match:
                return None
            
            hand1_raw = hands_match.group(1)
            hand1_cards_str = hands_match.group(2)
            hand1_score = int(re.sub(r'[✅🔰]', '', hand1_raw))
            
            hand2_raw = hands_match.group(3)
            hand2_cards_str = hands_match.group(4)
            hand2_score = int(re.sub(r'[✅🔰]', '', hand2_raw))
            
            if '✅' in hand1_raw or '✅' in hand2_raw:
                winner = 'hand1' if '✅' in hand1_raw else 'hand2'
            elif '🔰' in hand1_raw or '🔰' in hand2_raw:
                winner = 'hand1' if '🔰' in hand1_raw else 'hand2'
            else:
                winner = 'hand1' if hand1_score > hand2_score else ('hand2' if hand2_score > hand1_score else 'tie')
            
            hand1_cards = self.parse_cards(hand1_cards_str)
            hand2_cards = self.parse_cards(hand2_cards_str)
            first_suit = hand1_cards[0]['suit'] if hand1_cards else None
            
            calculated_hand1_points = self.calculate_hand_points(hand1_cards)
            calculated_hand2_points = self.calculate_hand_points(hand2_cards)
            
            return {
                'game_number': game_number,
                'hand1_score': hand1_score,
                'hand1_cards': hand1_cards,
                'hand1_calculated': calculated_hand1_points,
                'hand2_score': hand2_score,
                'hand2_cards': hand2_cards,
                'hand2_calculated': calculated_hand2_points,
                'total_points': total_points,
                'winner': winner,
                'first_suit': first_suit,
                'raw_data': text
            }
        except Exception as e:
            print(f"Ошибка парсинга: {e}")
            return None
    
    def parse_cards(self, cards_str):
        cards = []
        card_items = cards_str.strip().split()
        for item in card_items:
            if len(item) >= 2:
                if item.startswith('10'):
                    value = '10'
                    suit_symbol = item[2:]
                else:
                    value = item[0]
                    suit_symbol = item[1:]
                
                suit = self.suit_map.get(suit_symbol, 'unknown')
                points = self.card_values.get(value, 0)
                cards.append({'value': value, 'suit': suit, 'points': points, 'symbol': suit_symbol})
        return cards
    
    def calculate_hand_points(self, cards):
        return sum(card['points'] for card in cards) % 10

# Класс алгоритма сигналов
class SignalAlgorithm:
    def __init__(self):
        self.parser = BaccaratParser()
    
    def is_even_decade(self, game_number):
        return (game_number // 10) % 2 == 0
    
    def get_signal_suit(self, game_number, first_suit):
        if self.is_even_decade(game_number):
            rules = {'пики': 'бубны', 'бубны': 'пики', 'черви': 'трефы', 'трефы': 'черви'}
        else:
            rules = {'пики': 'трефы', 'трефы': 'пики', 'черви': 'бубны', 'бубны': 'черви'}
        return rules.get(first_suit, first_suit)
    
    def process_game_signal(self, user_id, game_number, first_suit):
        conn = sqlite3.connect('baccarat_stats.db', check_same_thread=False)
        c = conn.cursor()
        
        c.execute('SELECT game_number, first_suit FROM games_analysis WHERE user_id = ? AND game_number < ? ORDER BY game_number DESC LIMIT 1', 
                  (user_id, game_number))
        prev_game = c.fetchone()
        
        signals = []
        predicted_suit = None
        is_confirmation = False
        
        c.execute('SELECT id, from_game, suit FROM signals WHERE user_id = ? AND to_game = ? AND is_active = 1', 
                  (user_id, game_number))
        active_signal = c.fetchone()
        
        if active_signal:
            signal_id, from_game, expected_suit = active_signal
            if expected_suit == first_suit:
                is_confirmation = True
                c.execute('UPDATE signals SET is_confirmed = 1, is_active = 0, confirmed_at = ? WHERE id = ?', 
                         (datetime.now(), signal_id))
                next_game = game_number + 1
                c.execute('INSERT INTO signals (user_id, from_game, to_game, suit, is_active, created_at) VALUES (?, ?, ?, ?, 1, ?)',
                         (user_id, game_number, next_game, expected_suit, datetime.now()))
                signals.append({'type': 'confirmation', 'from_game': from_game, 'to_game': game_number, 'suit': expected_suit, 'next_signal': next_game})
                predicted_suit = expected_suit
            else:
                c.execute('UPDATE signals SET is_active = 0 WHERE id = ?', (signal_id,))
                signals.append({'type': 'failure', 'from_game': from_game, 'to_game': game_number, 'expected': expected_suit, 'actual': first_suit})
        
        if first_suit:
            signal_suit = self.get_signal_suit(game_number, first_suit)
            target_game = game_number + 3
            c.execute('SELECT id FROM signals WHERE user_id = ? AND to_game = ? AND is_active = 1', (user_id, target_game))
            if not c.fetchone():
                c.execute('INSERT INTO signals (user_id, from_game, to_game, suit, is_active, created_at) VALUES (?, ?, ?, ?, 1, ?)',
                         (user_id, game_number, target_game, signal_suit, datetime.now()))
                signals.append({'type': 'new_signal', 'from_game': game_number, 'to_game': target_game, 'suit': signal_suit})
                if not predicted_suit:
                    predicted_suit = signal_suit

        conn.commit()
        conn.close()
        return signals, predicted_suit, is_confirmation

# Основной анализатор
class GameAnalyzer:
    def __init__(self):
        self.parser = BaccaratParser()
        self.signal_algorithm = SignalAlgorithm()
    
    def process_game_data(self, text, user_id):
        parsed = self.parser.parse_game(text)
        if not parsed:
            return None, "❌ Не удалось распарсить данные. Проверьте формат."
        
        points_warning = ""
        if parsed['hand1_score'] != parsed['hand1_calculated']:
            points_warning += f"⚠️ 1-я рука: указано {parsed['hand1_score']}, по картам {parsed['hand1_calculated']}\n"
        if parsed['hand2_score'] != parsed['hand2_calculated']:
            points_warning += f"⚠️ 2-я рука: указано {parsed['hand2_score']}, по картам {parsed['hand2_calculated']}\n"
        
        suit_analysis = self.analyze_suits(parsed)
        signals, predicted_suit, is_confirmation = self.signal_algorithm.process_game_signal(
            user_id, parsed['game_number'], parsed['first_suit']
        )
        
        conn = sqlite3.connect('baccarat_stats.db', check_same_thread=False)
        c = conn.cursor()
        c.execute('''INSERT INTO games_analysis 
                     (user_id, game_number, game_date, hand1_score, hand1_cards, hand2_score, hand2_cards, 
                      total_points, winner, first_suit, predicted_suit, is_confirmation, raw_data)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                  (user_id, parsed['game_number'], datetime.now(), parsed['hand1_score'],
                   json.dumps(parsed['hand1_cards'], ensure_ascii=False), parsed['hand2_score'],
                   json.dumps(parsed['hand2_cards'], ensure_ascii=False), parsed['total_points'],
                   parsed['winner'], parsed['first_suit'], predicted_suit, is_confirmation, parsed['raw_data']))
        
        game_id = c.lastrowid
        for suit, count in suit_analysis.items():
            c.execute('INSERT INTO suit_stats (user_id, game_id, suit, count, hand_position) VALUES (?, ?, ?, ?, ?)',
                     (user_id, game_id, suit, count, 'both'))
        
        conn.commit()
        conn.close()
        return parsed, self.generate_analysis(parsed, signals, points_warning)
    
    def analyze_suits(self, parsed_data):
        suit_stats = defaultdict(int)
        for card in parsed_data['hand1_cards'] + parsed_data['hand2_cards']:
            suit_stats[card['suit']] += 1
        return dict(suit_stats)
    
    def generate_analysis(self, parsed, signals, points_warning=""):
        result = ["🔍 АНАЛИЗ ИГРЫ Макс Москва", "═" * 50, f"🎮 Игра #{parsed['game_number']}", ""]
        
        if points_warning:
            result.extend(["⚠️ ВНИМАНИЕ! Несоответствие очков:", points_warning, ""])
        
        cards1 = [f"{c['value']}{c['symbol']}" for c in parsed['hand1_cards']]
        result.extend(["🤚 ПЕРВАЯ РУКА:", f"   Карты: {' '.join(cards1)}", 
                      f"   Очки: {parsed['hand1_score']}", f"   Первая масть: {parsed['first_suit']} ⭐", ""])
        
        cards2 = [f"{c['value']}{c['symbol']}" for c in parsed['hand2_cards']]
        result.extend(["✋ ВТОРАЯ РУКА:", f"   Карты: {' '.join(cards2)}", f"   Очки: {parsed['hand2_score']}"])
        
        winner_text = {'hand1': 'ПЕРВАЯ РУКА ✓', 'hand2': 'ВТОРАЯ РУКА ✓', 'tie': 'НИЧЬЯ'}.get(parsed['winner'], '')
        result.extend([f"🏆 ПОБЕДИТЕЛЬ: {winner_text}", "", "📊 АНАЛИЗ СИГНАЛОВ", "─" * 30])
        
        for signal in signals:
            if signal['type'] == 'confirmation':
                result.extend([f"✅ ПОДТВЕРЖДЕНИЕ СИГНАЛА!", f"   #{signal['from_game']} → {signal['suit']} ✓",
                              f"   🔄 Новый сигнал на #{signal['next_signal']}: {signal['suit']}"])
            elif signal['type'] == 'failure':
                result.extend([f"❌ СИГНАЛ НЕ СОВПАЛ", f"   Ожидалось: {signal['expected']}", f"   Было: {signal['actual']}"])
            elif signal['type'] == 'new_signal':
                result.extend([f"🆕 НОВЫЙ СИГНАЛ", f"   #{signal['from_game']} → #{signal['to_game']}: {signal['suit']}"])
        
        decade_type = "ЧЕТНЫЙ" if self.signal_algorithm.is_even_decade(parsed['game_number']) else "НЕЧЕТНЫЙ"
        rules = "пики↔бубны, черви↔трефы" if decade_type == "ЧЕТНЫЙ" else "пики↔трефы, черви↔бубны"
        result.extend(["", f"📌 ДЕСЯТОК: {decade_type}", f"   Правило: {rules}"])
        
        return '\n'.join(result)

# ═══════════════════════════════════════════════════════════════════════════════
# КОМАНДЫ БОТА "МАКС МОСКВА"
# ═══════════════════════════════════════════════════════════════════════════════

@bot.message_handler(commands=['start'])
def start(message):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add('📊 Ввести игру', '📈 Статистика')
    markup.add('🔔 Сигналы', '🔮 Прогноз')
    markup.add('📋 История', '🧪 Тест алгоритма')
    markup.add('ℹ️ Помощь')
    
    bot.send_message(message.chat.id,
        "🎰 <b>МАКС МОСКВА - БОТ БАККАРЫ</b>\n\n"
        "🔥 Система сигналов:\n"
        "• N → N+3 (первичный)\n"
        "• N+1 (повтор при подтверждении)\n\n"
        "Выберите действие:", parse_mode='HTML', reply_markup=markup)

@bot.message_handler(func=lambda m: 'Ввести игру' in m.text)
def enter_game(message):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True).add('◀️ Назад')
    bot.send_message(message.chat.id,
        "📝 <b>ФОРМАТ:</b>\n"
        "#N803. 0(2♠️ J♥️ A♥️) - ✅6(J♦️ 6♦️) #T9\n\n"
        "Введите данные игры:", parse_mode='HTML', reply_markup=markup)
    bot.register_next_step_handler(message, process_game_input)

def process_game_input(message):
    if message.text == '◀️ Назад':
        start(message)
        return
    
    analyzer = GameAnalyzer()
    parsed, analysis = analyzer.process_game_data(message.text, message.from_user.id)
    
    bot.send_message(message.chat.id, analysis, parse_mode='HTML')
    start(message)

@bot.message_handler(func=lambda m: 'Сигналы' in m.text)
def show_signals(message):
    conn = sqlite3.connect('baccarat_stats.db', check_same_thread=False)
    c = conn.cursor()
    c.execute('SELECT from_game, to_game, suit, created_at FROM signals WHERE user_id = ? AND is_active = 1 ORDER BY to_game', 
              (message.from_user.id,))
    signals = c.fetchall()
    conn.close()
    
    if not signals:
        bot.send_message(message.chat.id, "📭 Нет активных сигналов")
        return
    
    result = ["🔔 <b>АКТИВНЫЕ СИГНАЛЫ</b>", "═" * 30]
    for from_game, to_game, suit, created_at in signals:
        created = datetime.fromisoformat(created_at).strftime('%H:%M')
        result.extend([f"#{from_game} → <b>#{to_game}</b>: {suit}", f"   ⏰ {created}", ""])
    
    bot.send_message(message.chat.id, '\n'.join(result), parse_mode='HTML')

@bot.message_handler(func=lambda m: 'Прогноз' in m.text)
def predict_game(message):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True).add('◀️ Назад')
    bot.send_message(message.chat.id, "🔮 Номер игры для прогноза:", reply_markup=markup)
    bot.register_next_step_handler(message, process_predict)

def process_predict(message):
    if message.text == '◀️ Назад':
        start(message)
        return
    
    try:
        game_num = int(message.text)
        conn = sqlite3.connect('baccarat_stats.db', check_same_thread=False)
        c = conn.cursor()
        c.execute('SELECT from_game, suit FROM signals WHERE user_id = ? AND to_game = ? AND is_active = 1', 
                 (message.from_user.id, game_num))
        signals = c.fetchall()
        
        c.execute('SELECT first_suit FROM games_analysis WHERE user_id = ? AND game_number = ?', 
                 (message.from_user.id, game_num))
        game_data = c.fetchone()
        conn.close()
        
        if not signals:
            bot.send_message(message.chat.id, f"📭 Нет сигналов на игру #{game_num}")
        else:
            result = [f"🔮 <b>ПРОГНОЗ #{game_num}</b>", "═" * 30]
            for from_game, suit in signals:
                result.extend([f"💎 #{from_game} → <b>{suit}</b>", ""])
            if game_data:
                actual = game_data[0]
                status = "✅ УДАЧНО!" if actual == signals[0][1] else "❌ НЕ СОВПАЛО"
                result.append(f"📊 Факт: <b>{actual}</b> {status}")
            bot.send_message(message.chat.id, '\n'.join(result), parse_mode='HTML')
    except:
        bot.send_message(message.chat.id, "❌ Неверный номер игры")
    
    start(message)

@bot.message_handler(func=lambda m: 'Статистика' in m.text)
def show_stats(message):
    conn = sqlite3.connect('baccarat_stats.db', check_same_thread=False)
    c = conn.cursor()
    
    c.execute('SELECT COUNT(*), SUM(winner = "hand1"), SUM(winner = "hand2"), SUM(winner = "tie") FROM games_analysis WHERE user_id = ?', 
              (message.from_user.id,))
    total, h1, h2, ties = c.fetchone() or (0,0,0,0)
    
    c.execute('SELECT COUNT(*), SUM(is_confirmed) FROM signals WHERE user_id = ?', (message.from_user.id,))
    sig_total, sig_conf = c.fetchone() or (0,0)
    conn.close()
    
    stats = f"""📈 <b>СТАТИСТИКА МАКС МОСКВА</b>
{'═' * 25}
🎮 Игр: <b>{total}</b>
1-я рука: <b>{h1}</b> ({h1/total*100:.0f}%)
2-я рука: <b>{h2}</b> ({h2/total*100:.0f}%)
Ничьи: <b>{ties}</b>

🔔 Сигналов: <b>{sig_total}</b>
✅ Подтверждено: <b>{sig_conf}</b> ({sig_conf/sig_total*100:.0f}%)
"""
    bot.send_message(message.chat.id, stats, parse_mode='HTML')

@bot.message_handler(func=lambda m: m.text in ['📋 История', 'История'])
def show_history(message):
    conn = sqlite3.connect('baccarat_stats.db', check_same_thread=False)
    c = conn.cursor()
    c.execute('SELECT game_number, first_suit, is_confirmation, predicted_suit FROM games_analysis WHERE user_id = ? ORDER BY game_number DESC LIMIT 15', 
              (message.from_user.id,))
    games = c.fetchall()
    conn.close()
    
    if not games:
        bot.send_message(message.chat.id, "📭 Нет игр")
        return
    
    result = ["📋 <b>ПОСЛЕДНИЕ ИГРЫ</b>", "─" * 25]
    for num, suit, conf, pred in games:
        mark = "✅" if conf else ""
        pred_text = f" → <b>{pred}</b>" if pred else ""
        result.append(f"#{num}: <b>{suit}</b>{mark}{pred_text}")
    
    bot.send_message(message.chat.id, '\n'.join(result), parse_mode='HTML')

@bot.message_handler(func=lambda m: 'Тест алгоритма' in m.text)
def test_algorithm(message):
    conn = sqlite3.connect('baccarat_stats.db', check_same_thread=False)
    c = conn.cursor()
    c.execute('SELECT game_number, first_suit FROM games_analysis WHERE user_id = ? AND first_suit IS NOT NULL ORDER BY game_number', 
              (message.from_user.id,))
    games = c.fetchall()
    conn.close()
    
    if len(games) < 4:
        bot.send_message(message.chat.id, "Нужно минимум 4 игры для теста")
        return
    
    algorithm = SignalAlgorithm()
    correct, total = 0, 0
    
    for i in range(len(games)-3):
        curr_num, curr_suit = games[i]
        target = curr_num + 3
        if next((g for g in games[i+3:] if g[0] == target), None):
            expected = algorithm.get_signal_suit(curr_num, curr_suit)
            actual = next(g[1] for g in games if g[0] == target)
            total += 1
            if expected == actual: correct += 1
    
    accuracy = correct/total*100 if total else 0
    bot.send_message(message.chat.id, 
        f"🧪 <b>ТЕСТ АЛГОРИТМА</b>\n"
        f"Проверок: {total}\n"
        f"✅ Совпадений: {correct}\n"
        f"📊 Точность: <b>{accuracy:.1f}%</b>", parse_mode='HTML')

@bot.message_handler(func=lambda m: 'Помощь' in m.text)
def show_help(message):
    help_text = """ℹ️ <b>МАКС МОСКВА - ПОМОЩЬ</b>

🎯 <b>СИСТЕМА СИГНАЛОВ:</b>
• Первичный: игра N → N+3
• Повторный: при ✓ → N+1 (та же масть)

🔢 <b>ПРАВИЛА ДЕСЯТКОВ:</b>
ЧЕТНЫЕ (20,40,60,80):
• пики ↔ бубны
• черви ↔ трефы

НЕЧЕТНЫЕ (10,30,50,70):
• пики ↔ трефы
• черви ↔ бубны

📝 <b>ФОРМАТ ВВОДА:</b>
#N803. 0(2♠️ J♥️ A♥️) - ✅6(J♦️ 6♦️) #T9"""
    
    bot.send_message(message.chat.id, help_text, parse_mode='HTML')

@bot.message_handler(func=lambda m: m.text == '◀️ Назад')
def go_back(message):
    start(message)

if __name__ == '__main__':
    init_db()
    print("🤖 Макс Москва - Бот Баккары запущен!")
    print("📱 Сигналы: N → N+3 | Повтор: N+1")
    bot.infinity_polling()
