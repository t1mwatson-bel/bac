Макс Москва, [16.02.2026 19:02]
import telebot
from telebot import types
import sqlite3
from datetime import datetime
import matplotlib.pyplot as plt
import io
import re
from collections import defaultdict, Counter
import json
import math

# Конфигурация
TOKEN = "1163348874:AAFgZEXveILvD4MbhQ8jiLTwIxs4puYhmq0"
INPUT_CHANNEL_ID = -1003469691743
OUTPUT_CHANNEL_ID = -1003842401391
ADMIN_ID = 683219603

# Инициализация базы данных
def init_db():
    conn = sqlite3.connect('baccarat_stats.db')
    c = conn.cursor()
    
    # Таблица для игр в вашем формате
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
    
    # Таблица для статистики мастей
    c.execute('''CREATE TABLE IF NOT EXISTS suit_stats
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER,
                  game_id INTEGER,
                  suit TEXT,
                  count INTEGER,
                  hand_position TEXT,
                  FOREIGN KEY (game_id) REFERENCES games_analysis (id))''')
    
    # Таблица для отслеживания сигналов
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

# Класс для парсинга вашего формата
class BaccaratParser:
    def init(self):
        # Соответствие мастей
        self.suit_map = {
            '♠️': 'пики',
            '♣️': 'трефы',
            '♥️': 'черви',
            '♦️': 'бубны'
        }
        
        # Значения карт в очках баккары
        self.card_values = {
            'A': 1, '2': 2, '3': 3, '4': 4, '5': 5, '6': 6, '7': 7, '8': 8, '9': 9,
            '10': 0, 'J': 0, 'Q': 0, 'K': 0
        }
    
    def parse_game(self, text):
        """Парсит строку в формате #N803. 0(2♠️ J♥️ A♥️) - ✅6(J♦️ 6♦️) #T9 🟩"""
        try:
            # Извлекаем номер игры
            game_num_match = re.search(r'#N(\d+)', text)
            game_number = int(game_num_match.group(1)) if game_num_match else 0
            
            # Извлекаем общее количество очков
            total_points_match = re.search(r'#T(\d+)', text)
            total_points = int(total_points_match.group(1)) if total_points_match else 0
            
            # Разделяем на две руки
            # Ищем паттерн с возможным ✅ или 🔰 в любой руке
            hands_pattern = r'([✅🔰]?\d+)\(([^)]+)\)\s*-\s*([✅🔰]?\d+)\(([^)]+)\)'
            hands_match = re.search(hands_pattern, text)
            
            if not hands_match:
                return None
            
            # Парсим первую руку
            hand1_raw = hands_match.group(1)
            hand1_cards_str = hands_match.group(2)
            hand1_score = int(re.sub(r'[✅🔰]', '', hand1_raw))
            
            # Парсим вторую руку
            hand2_raw = hands_match.group(3)
            hand2_cards_str = hands_match.group(4)
            hand2_score = int(re.sub(r'[✅🔰]', '', hand2_raw))
            
            # Определяем победителя
            if '✅' in hand1_raw or '✅' in hand2_raw:

Макс Москва, [16.02.2026 19:02]
winner = 'hand1' if '✅' in hand1_raw else 'hand2'
            elif '🔰' in hand1_raw or '🔰' in hand2_raw:
                winner = 'hand1' if '🔰' in hand1_raw else 'hand2'
            else:
                if hand1_score > hand2_score:
                    winner = 'hand1'
                elif hand2_score > hand1_score:
                    winner = 'hand2'
                else:
                    winner = 'tie'
            
            # Парсим карты первой руки
            hand1_cards = self.parse_cards(hand1_cards_str)
            # Парсим карты второй руки
            hand2_cards = self.parse_cards(hand2_cards_str)
            
            # Определяем первую масть (первая карта в первой руке)
            first_suit = hand1_cards[0]['suit'] if hand1_cards else None
            
            # Вычисляем очки
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
        """Парсит строку с картами вида '2♠️ J♥️ A♥️'"""
        cards = []
        # Разделяем по пробелам
        card_items = cards_str.strip().split()
        
        for item in card_items:
            if len(item) >= 2:
                # Значение может быть одной цифрой/буквой или '10'
                if item.startswith('10'):
                    value = '10'
                    suit_symbol = item[2:]
                else:
                    value = item[0]
                    suit_symbol = item[1:]
                
                suit = self.suit_map.get(suit_symbol, 'unknown')
                points = self.card_values.get(value, 0)
                
                cards.append({
                    'value': value,
                    'suit': suit,
                    'points': points,
                    'symbol': suit_symbol
                })
        
        return cards
    
    def calculate_hand_points(self, cards):
        """Вычисляет очки руки в баккаре"""
        total = sum(card['points'] for card in cards)
        return total % 10

# Класс для алгоритма сигналов
class SignalAlgorithm:
    def init(self):
        self.parser = BaccaratParser()
    
    def is_even_decade(self, game_number):
        """Проверяет, является ли десяток четным"""
        decade = game_number // 10
        return decade % 2 == 0
    
    def get_signal_suit(self, game_number, first_suit):
        """
        Определяет масть для сигнала по алгоритму
        
        Правила:
        - В четных десятках: пики ↔ бубны, черви ↔ трефы
        - В нечетных десятках: пики ↔ трефы, черви ↔ бубны
        """
        if self.is_even_decade(game_number):
            # Четные десятки
            rules = {
                'пики': 'бубны',
                'бубны': 'пики',
                'черви': 'трефы',
                'трефы': 'черви'
            }
        else:
            # Нечетные десятки
            rules = {
                'пики': 'трефы',
                'трефы': 'пики',
                'черви': 'бубны',
                'бубны': 'черви'
            }
        
        return rules.get(first_suit, first_suit)

Макс Москва, [16.02.2026 19:02]
def process_game_signal(self, user_id, game_number, first_suit):
        """
        Обрабатывает игру и создает/обновляет сигналы
        """
        conn = sqlite3.connect('baccarat_stats.db')
        c = conn.cursor()
        
        # Получаем предыдущую игру
        c.execute('''SELECT game_number, first_suit FROM games_analysis 
                     WHERE user_id = ? AND game_number < ?
                     ORDER BY game_number DESC LIMIT 1''', (user_id, game_number))
        prev_game = c.fetchone()
        
        signals = []
        predicted_suit = None
        is_confirmation = False
        
        # Проверяем, был ли активный сигнал на эту игру
        c.execute('''SELECT id, from_game, suit FROM signals 
                     WHERE user_id = ? AND to_game = ? AND is_active = 1''', 
                  (user_id, game_number))
        active_signal = c.fetchone()
        
        if active_signal:
            signal_id, from_game, expected_suit = active_signal
            
            # Проверяем, совпала ли масть
            if expected_suit == first_suit:
                # Сигнал подтвердился!
                is_confirmation = True
                
                # Обновляем сигнал
                c.execute('''UPDATE signals 
                             SET is_confirmed = 1, is_active = 0, confirmed_at = ? 
                             WHERE id = ?''', (datetime.now(), signal_id))
                
                # Даем повторный сигнал на следующую игру (+1)
                next_game = game_number + 1
                c.execute('''INSERT INTO signals (user_id, from_game, to_game, suit, is_active, created_at)
                             VALUES (?, ?, ?, ?, 1, ?)''',
                          (user_id, game_number, next_game, expected_suit, datetime.now()))
                
                signals.append({
                    'type': 'confirmation',
                    'from_game': from_game,
                    'to_game': game_number,
                    'suit': expected_suit,
                    'next_signal': next_game
                })
                
                predicted_suit = expected_suit
            else:
                # Сигнал не подтвердился
                c.execute('''UPDATE signals SET is_active = 0 WHERE id = ?''', (signal_id,))
                
                signals.append({
                    'type': 'failure',
                    'from_game': from_game,
                    'to_game': game_number,
                    'expected': expected_suit,
                    'actual': first_suit
                })
        
        # Создаем новый сигнал по алгоритму (от текущей игры)
        if first_suit:
            signal_suit = self.get_signal_suit(game_number, first_suit)
            
            # Сигнал дается на игру +3 (как в вашем примере 803→806)
            target_game = game_number + 3
            
            # Проверяем, нет ли уже активного сигнала на эту игру
            c.execute('''SELECT id FROM signals 
                         WHERE user_id = ? AND to_game = ? AND is_active = 1''',
                      (user_id, target_game))
            existing = c.fetchone()
            
            if not existing:
                c.execute('''INSERT INTO signals (user_id, from_game, to_game, suit, is_active, created_at)
                             VALUES (?, ?, ?, ?, 1, ?)''',
                          (user_id, game_number, target_game, signal_suit, datetime.now()))
                
                signals.append({
                    'type': 'new_signal',
                    'from_game': game_number,
                    'to_game': target_game,
                    'suit': signal_suit
                })
                
                if not predicted_suit:
                    predicted_suit = signal_suit

Макс Москва, [16.02.2026 19:02]
conn.commit()
        conn.close()
        
        return signals, predicted_suit, is_confirmation
    
    def get_active_signals(self, user_id):
        """Получает все активные сигналы"""
        conn = sqlite3.connect('baccarat_stats.db')
        c = conn.cursor()
        
        c.execute('''SELECT from_game, to_game, suit, created_at 
                     FROM signals 
                     WHERE user_id = ? AND is_active = 1
                     ORDER BY to_game''', (user_id,))
        
        signals = c.fetchall()
        conn.close()
        
        return signals
    
    def check_game_signals(self, user_id, game_number):
        """Проверяет, есть ли сигналы на указанную игру"""
        conn = sqlite3.connect('baccarat_stats.db')
        c = conn.cursor()
        
        c.execute('''SELECT from_game, suit FROM signals 
                     WHERE user_id = ? AND to_game = ? AND is_active = 1''', 
                  (user_id, game_number))
        
        signals = c.fetchall()
        conn.close()
        
        return signals

# Основной класс для обработки игр
class GameAnalyzer:
    def init(self):
        self.parser = BaccaratParser()
        self.signal_algorithm = SignalAlgorithm()
    
    def process_game_data(self, text, user_id):
        """Обрабатывает данные игры"""
        
        # Парсим данные
        parsed = self.parser.parse_game(text)
        
        if not parsed:
            return None, "Не удалось распарсить данные. Проверьте формат."
        
        # Проверяем соответствие очков
        points_warning = ""
        
        if parsed['hand1_score'] != parsed['hand1_calculated']:
            points_warning += f"⚠️ В первой руке указано {parsed['hand1_score']} очков, но по картам получается {parsed['hand1_calculated']}\n"
        
        if parsed['hand2_score'] != parsed['hand2_calculated']:
            points_warning += f"⚠️ Во второй руке указано {parsed['hand2_score']} очков, но по картам получается {parsed['hand2_calculated']}\n"
        
        total_calculated = (parsed['hand1_calculated'] + parsed['hand2_calculated']) % 10
        if parsed['total_points'] != total_calculated:
            points_warning += f"⚠️ Общее количество очков #T{parsed['total_points']}, но сумма очков рук = {total_calculated}\n"
        
        # Анализируем масти
        suit_analysis = self.analyze_suits(parsed)
        
        # Обрабатываем сигналы
        signals, predicted_suit, is_confirmation = self.signal_algorithm.process_game_signal(
            user_id, parsed['game_number'], parsed['first_suit']
        )
        
        # Сохраняем в базу данных
        conn = sqlite3.connect('baccarat_stats.db')
        c = conn.cursor()
        
        # Сохраняем игру
        c.execute('''INSERT INTO games_analysis 
                     (user_id, game_number, game_date, hand1_score, hand1_cards, 
                      hand2_score, hand2_cards, total_points, winner, first_suit, 
                      predicted_suit, is_confirmation, raw_data)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                  (user_id, 
                   parsed['game_number'],
                   datetime.now(),
                   parsed['hand1_score'],
                   json.dumps(parsed['hand1_cards'], ensure_ascii=False),
                   parsed['hand2_score'],
                   json.dumps(parsed['hand2_cards'], ensure_ascii=False),
                   parsed['total_points'],
                   parsed['winner'],
                   parsed['first_suit'],
                   predicted_suit,
                   is_confirmation,
                   parsed['raw_data']))
        
        game_id = c.lastrowid
        
        # Сохраняем статистику мастей
        for suit, count in suit_analysis.items():

Макс Москва, [16.02.2026 19:02]
c.execute('''INSERT INTO suit_stats (user_id, game_id, suit, count, hand_position)
                         VALUES (?, ?, ?, ?, ?)''',
                      (user_id, game_id, suit, count, 'both'))
        
        conn.commit()
        conn.close()
        
        # Формируем результат анализа
        analysis_result = self.generate_analysis(parsed, signals, points_warning)
        
        return parsed, analysis_result
    
    def analyze_suits(self, parsed_data):
        """Анализирует распределение мастей в игре"""
        suit_stats = defaultdict(int)
        
        for card in parsed_data['hand1_cards'] + parsed_data['hand2_cards']:
            suit_stats[card['suit']] += 1
        
        return dict(suit_stats)
    
    def generate_analysis(self, parsed, signals, points_warning=""):
        """Генерирует анализ игры"""
        
        result = []
        result.append("🔍 АНАЛИЗ ИГРЫ")
        result.append("=" * 50)
        result.append(f"🎮 Игра #{parsed['game_number']}")
        result.append("")
        
        if points_warning:
            result.append("⚠️ ВНИМАНИЕ! Несоответствие очков:")
            result.append(points_warning)
            result.append("")
        
        # Рука 1
        result.append("🤚 ПЕРВАЯ РУКА:")
        cards_str = [f"{c['value']}{c['symbol']}" for c in parsed['hand1_cards']]
        result.append(f"   Карты: {' '.join(cards_str)}")
        result.append(f"   Очки: {parsed['hand1_score']}")
        result.append(f"   Первая масть: {parsed['first_suit']} ⭐")
        result.append("")
        
        # Рука 2
        result.append("✋ ВТОРАЯ РУКА:")
        cards_str = [f"{c['value']}{c['symbol']}" for c in parsed['hand2_cards']]
        result.append(f"   Карты: {' '.join(cards_str)}")
        result.append(f"   Очки: {parsed['hand2_score']}")
        result.append("")
        
        # Победитель
        winner_text = {
            'hand1': 'ПЕРВАЯ РУКА ✓',
            'hand2': 'ВТОРАЯ РУКА ✓',
            'tie': 'НИЧЬЯ'
        }.get(parsed['winner'], '')
        result.append(f"🏆 ПОБЕДИТЕЛЬ: {winner_text}")
        result.append("")
        
        # АНАЛИЗ СИГНАЛОВ
        result.append("📊 АНАЛИЗ СИГНАЛОВ")
        result.append("-" * 30)
        
        for signal in signals:
            if signal['type'] == 'confirmation':
                result.append(f"✅ ПОДТВЕРЖДЕНИЕ СИГНАЛА!")
                result.append(f"   Сигнал от игры #{signal['from_game']} на масть {signal['suit']}")
                result.append(f"   ✅ СОВПАЛО! Масть {signal['suit']} подтверждена")
                result.append(f"   🔄 ДАЮ ПОВТОРНЫЙ СИГНАЛ на игру #{signal['next_signal']}")
                result.append(f"   Ожидаемая масть: {signal['suit']} (повтор)")
            
            elif signal['type'] == 'failure':
                result.append(f"❌ СИГНАЛ НЕ ПОДТВЕРДИЛСЯ")
                result.append(f"   Ожидалась масть: {signal['expected']}")
                result.append(f"   Получена масть: {signal['actual']}")
            
            elif signal['type'] == 'new_signal':
                result.append(f"🆕 НОВЫЙ СИГНАЛ")
                result.append(f"   От игры #{signal['from_game']} → на игру #{signal['to_game']}")
                result.append(f"   Ожидаемая масть: {signal['suit']}")
        
        if not signals:
            result.append("   Нет активных сигналов")
        
        # Информация о десятке
        result.append("")
        result.append("📌 ИНФОРМАЦИЯ О ДЕСЯТКЕ:")
        decade_type = "ЧЕТНЫЙ" if self.signal_algorithm.is_even_decade(parsed['game_number']) else "НЕЧЕТНЫЙ"
        result.append(f"   Игра #{parsed['game_number']} - {decade_type} десяток")
        
        if self.signal_algorithm.is_even_decade(parsed['game_number']):
            result.append("   Правило четного десятка: пики↔бубны, черви↔трефы")
        else:
            result.append("   Правило нечетного десятка: пики↔трефы, черви↔бубны")
        
        return "\n".join(result)

Макс Москва, [16.02.2026 19:02]
# Команды бота
@bot.message_handler(commands=['start'])
def start(message):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add('📊 Ввести игру', '📈 Моя статистика')
    markup.add('📊 Активные сигналы', '📊 Проверка алгоритма')
    markup.add('📋 История игр', '🔮 Прогноз на игру')
    markup.add('ℹ️ Помощь', '📝 Пример формата')
    
    bot.send_message(
        message.chat.id,
        "👋 Добро пожаловать в бота-анализатор баккары!\n\n"
        "СИСТЕМА СИГНАЛОВ:\n"
        "• Первый сигнал: от игры N → на игру N+3\n"
        "• При подтверждении: повторный сигнал на N+1\n"
        "• Пример: #803(пики) → сигнал на #806(бубны)\n"
        "• #806(бубны) подтверждение → повторный сигнал на #807(бубны)\n\n"
        "Выберите действие:",
        reply_markup=markup
    )

@bot.message_handler(func=lambda message: message.text == '📊 Ввести игру')
def enter_game(message):
    msg = bot.send_message(
        message.chat.id,
        "📝 Введите данные игры в вашем формате:\n\n"
        "Пример 1: #N803. 0(2♠️ J♥️ A♥️) - ✅6(J♦️ 6♦️) #T9 🟩\n"
        "Пример 2: #N806. 8(5♠️ 3♦️) 🔰 8(3♠️ 5♦️) #T16 #X🟡 #R\n\n"
        "Где:\n"
        "#N803 - номер игры\n"
        "✅ или 🔰 - обозначение победителя\n"
        "#T9 - общее количество очков",
        reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('◀️ Назад')
    )
    bot.register_next_step_handler(msg, process_game_input)

def process_game_input(message):
    if message.text == '◀️ Назад':
        start(message)
        return
    
    analyzer = GameAnalyzer()
    parsed, analysis = analyzer.process_game_data(message.text, message.from_user.id)
    
    if parsed:
        bot.send_message(message.chat.id, analysis, parse_mode='HTML')
    else:
        bot.send_message(
            message.chat.id,
            "❌ Ошибка в формате. Попробуйте снова или нажмите '📝 Пример формата'"
        )
    
    start(message)

@bot.message_handler(func=lambda message: message.text == '📊 Активные сигналы')
def show_active_signals(message):
    algorithm = SignalAlgorithm()
    signals = algorithm.get_active_signals(message.from_user.id)
    
    if not signals:
        bot.send_message(message.chat.id, "📭 Нет активных сигналов")
        return
    
    result = ["📊 АКТИВНЫЕ СИГНАЛЫ", "=" * 40, ""]
    
    for from_game, to_game, suit, created_at in signals:
        created = datetime.fromisoformat(created_at).strftime('%d.%m %H:%M')
        result.append(f"🆓 Сигнал от игры #{from_game}")
        result.append(f"   → на игру #{to_game}")
        result.append(f"   Ожидаемая масть: {suit}")
        result.append(f"   Создан: {created}")
        result.append("")
    
    bot.send_message(message.chat.id, "\n".join(result))

@bot.message_handler(func=lambda message: message.text == '🔮 Прогноз на игру')
def predict_for_game(message):
    msg = bot.send_message(
        message.chat.id,
        "Введите номер игры для прогноза:",
        reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add('◀️ Назад')
    )
    bot.register_next_step_handler(msg, process_prediction_request)

def process_prediction_request(message):
    if message.text == '◀️ Назад':
        start(message)
        return
    
    try:
        game_number = int(message.text)
    except:
        bot.send_message(message.chat.id, "❌ Введите корректный номер игры")
        start(message)
        return
    
    algorithm = SignalAlgorithm()
    signals = algorithm.check_game_signals(message.from_user.id, game_number)
    
    if not signals:
        bot.send_message(
            message.chat.id,
            f"📭 Нет активных сигналов на игру #{game_number}"
        )
    else:
        result = [f"🔮 ПРОГНОЗ НА ИГРУ #{game_number}", "=" * 40, ""]
        for from_game, suit in signals:

Макс Москва, [16.02.2026 19:02]
result.append(f"🆓 Сигнал от игры #{from_game}")
            result.append(f"   Ожидаемая масть: {suit}")
            result.append("")
        
        # Проверяем, есть ли запись этой игры
        conn = sqlite3.connect('baccarat_stats.db')
        c = conn.cursor()
        c.execute('''SELECT first_suit FROM games_analysis 
                     WHERE user_id = ? AND game_number = ?''', 
                  (message.from_user.id, game_number))
        game_data = c.fetchone()
        conn.close()
        
        if game_data:
            actual_suit = game_data[0]
            result.append(f"📊 Игра уже сыграна:")
            result.append(f"   Фактическая масть: {actual_suit}")
            
            if actual_suit == suit:
                result.append("   ✅ ПРОГНОЗ ПОДТВЕРДИЛСЯ!")
            else:
                result.append("   ❌ ПРОГНОЗ НЕ ПОДТВЕРДИЛСЯ")
        
        bot.send_message(message.chat.id, "\n".join(result))
    
    start(message)

@bot.message_handler(func=lambda message: message.text == '📊 Проверка алгоритма')
def check_algorithm(message):
    """Проверяет работу алгоритма на истории игр"""
    conn = sqlite3.connect('baccarat_stats.db')
    c = conn.cursor()
    
    # Получаем все игры с первой мастью
    c.execute('''SELECT game_number, first_suit 
                 FROM games_analysis 
                 WHERE user_id = ? AND first_suit IS NOT NULL
                 ORDER BY game_number''', (message.from_user.id,))
    
    games = c.fetchall()
    conn.close()
    
    if len(games) < 3:
        bot.send_message(message.chat.id, "Недостаточно данных для проверки. Нужно минимум 3 игры.")
        return
    
    algorithm = SignalAlgorithm()
    game_dict = {num: suit for num, suit in games}
    
    results = []
    correct_predictions = 0
    total_predictions = 0
    
    for i in range(len(games) - 1):
        current_num, current_suit = games[i]
        
        # Первый сигнал (N → N+3)
        target1 = current_num + 3
        if target1 in game_dict:
            expected = algorithm.get_signal_suit(current_num, current_suit)
            actual = game_dict[target1]
            
            is_correct = (expected == actual)
            if is_correct:
                correct_predictions += 1
            total_predictions += 1
            
            results.append({
                'from': current_num,
                'to': target1,
                'type': 'первичный',
                'expected': expected,
                'actual': actual,
                'correct': is_correct
            })
            
            # Если первичный сигнал подтвердился, проверяем повторный (на +1)
            if is_correct:
                target2 = target1 + 1
                if target2 in game_dict:
                    expected_repeat = expected  # Та же масть
                    actual_repeat = game_dict[target2]
                    
                    is_repeat_correct = (expected_repeat == actual_repeat)
                    if is_repeat_correct:
                        correct_predictions += 1
                    total_predictions += 1
                    
                    results.append({
                        'from': target1,
                        'to': target2,
                        'type': 'повторный',
                        'expected': expected_repeat,
                        'actual': actual_repeat,
                        'correct': is_repeat_correct
                    })
    
    if total_predictions == 0:
        bot.send_message(message.chat.id, "Нет пар для проверки")
        return
    
    report = ["📊 ПРОВЕРКА АЛГОРИТМА", "=" * 50, ""]
    
    for r in results:
        mark = "✅" if r['correct'] else "❌"
        report.append(f"{mark} {r['type'].upper()} сигнал: {r['from']} → {r['to']}")

Макс Москва, [16.02.2026 19:02]
report.append(f"   Ожидалось: {r['expected']}, Факт: {r['actual']}")
        report.append("")
    
    accuracy = (correct_predictions / total_predictions * 100)
    report.append("📈 ИТОГИ:")
    report.append(f"   Всего проверок: {total_predictions}")
    report.append(f"   Совпадений: {correct_predictions}")
    report.append(f"   Точность: {accuracy:.1f}%")
    
    bar = '█' * int(accuracy / 5) + '░' * (20 - int(accuracy / 5))
    report.append(f"   [{bar}]")
    
    bot.send_message(message.chat.id, "\n".join(report))

@bot.message_handler(func=lambda message: message.text == '📈 Моя статистика')
def show_stats(message):
    conn = sqlite3.connect('baccarat_stats.db')
    c = conn.cursor()
    
    # Общая статистика
    c.execute('''SELECT COUNT(*), 
                        SUM(CASE WHEN winner = 'hand1' THEN 1 ELSE 0 END),
                        SUM(CASE WHEN winner = 'hand2' THEN 1 ELSE 0 END),
                        SUM(CASE WHEN winner = 'tie' THEN 1 ELSE 0 END)
                 FROM games_analysis WHERE user_id = ?''', (message.from_user.id,))
    
    total, h1_wins, h2_wins, ties = c.fetchone()
    
    if not total:
        bot.send_message(message.chat.id, "Нет данных")
        conn.close()
        return
    
    # Статистика сигналов
    c.execute('''SELECT COUNT(*), SUM(is_confirmed) FROM signals WHERE user_id = ?''', 
              (message.from_user.id,))
    total_signals, confirmed = c.fetchone()
    
    stats = f"📊 ВАША СТАТИСТИКА\n"
    stats += "=" * 40 + "\n"
    stats += f"Всего игр: {total}\n"
    stats += f"Победы 1-й руки: {h1_wins} ({h1_wins/total*100:.1f}%)\n"
    stats += f"Победы 2-й руки: {h2_wins} ({h2_wins/total*100:.1f}%)\n"
    stats += f"Ничьи: {ties} ({ties/total*100:.1f}%)\n\n"
    
    if total_signals:
        stats += f"📊 СТАТИСТИКА СИГНАЛОВ\n"
        stats += f"Всего сигналов: {total_signals}\n"
        stats += f"Подтверждено: {confirmed or 0}\n"
        if total_signals > 0:
            stats += f"Точность: {(confirmed or 0)/total_signals*100:.1f}%\n"
    
    bot.send_message(message.chat.id, stats)
    conn.close()

@bot.message_handler(func=lambda message: message.text == '📋 История игр')
def show_history(message):
    conn = sqlite3.connect('baccarat_stats.db')
    c = conn.cursor()
    
    c.execute('''SELECT game_number, first_suit, is_confirmation, predicted_suit, raw_data
                 FROM games_analysis 
                 WHERE user_id = ? 
                 ORDER BY game_number DESC 
                 LIMIT 20''', (message.from_user.id,))
    
    games = c.fetchall()
    conn.close()
    
    if not games:
        bot.send_message(message.chat.id, "Нет игр")
        return
    
    history = ["📋 ИСТОРИЯ ИГР", "=" * 50, ""]
    
    for game_num, first_suit, is_conf, pred_suit, raw in games:
        confirm_mark = " ✓" if is_conf else ""
        pred_mark = f" → прогноз {pred_suit}" if pred_suit else ""
        history.append(f"#{game_num}: {first_suit}{confirm_mark}{pred_mark}")
        history.append(f"   {raw[:60]}...")
        history.append("")
    
    bot.send_message(message.chat.id, "\n".join(history))

@bot.message_handler(func=lambda message: message.text == '📝 Пример формата')
def show_example(message):
    example = """
📝 ПРИМЕРЫ ФОРМАТА:

1. Обычная игра:
#N803. 0(2♠️ J♥️ A♥️) - ✅6(J♦️ 6♦️) #T9 🟩

2. Игра с подтверждением:
#N806. 8(5♠️ 3♦️) 🔰 8(3♠️ 5♦️) #T16 #X🟡 #R

СИСТЕМА СИГНАЛОВ:

🔵 ПЕРВИЧНЫЙ СИГНАЛ:
• Игра #803: первая масть ПИКИ
• Четный десяток (80) → пики дают бубны
• Сигнал на игру #806: БУБНЫ

🟢 ПОДТВЕРЖДЕНИЕ:
• Игра #806: первая масть БУБНЫ ✓
• Сигнал подтвердился!
• ДАЕМ ПОВТОРНЫЙ СИГНАЛ на #807: БУБНЫ

ПРАВИЛА:
Четные десятки: пики↔бубны, черви↔трефы
Нечетные десятки: пики↔трефы, черви↔бубны
    """
    bot.send_message(message.chat.id, example)

@bot.message_handler(func=lambda message: message.text == 'ℹ️ Помощь')
def help_command(message):
    help_text = """
🤖 БОТ-АНАЛИЗАТОР БАККАРЫ
    С СИСТЕМОЙ СИГНАЛОВ

Макс Москва, [16.02.2026 19:02]
ОСНОВНЫЕ ФУНКЦИИ:
• 📊 Ввести игру - запись игры в вашем формате
• 📈 Моя статистика - общая статистика
• 📊 Активные сигналы - текущие ожидаемые сигналы
• 🔮 Прогноз на игру - проверить сигналы на конкретную игру
• 📊 Проверка алгоритма - тест на истории
• 📋 История игр - последние 20 игр

СИСТЕМА СИГНАЛОВ:

1️⃣ ПЕРВИЧНЫЙ СИГНАЛ:
   От игры N → на игру N+3
   Масть определяется по правилам десятков

2️⃣ ПРИ ПОДТВЕРЖДЕНИИ:
   Если сигнал совпал → повторный сигнал на N+1
   С той же мастью!

ПРАВИЛА ПО ДЕСЯТКАМ:

ЧЕТНЫЕ десятки (20,40,60,80...):
• пики ↔ бубны
• черви ↔ трефы

НЕЧЕТНЫЕ десятки (10,30,50,70...):
• пики ↔ трефы
• черви ↔ бубны

ПРИМЕР:
#803(пики) → сигнал на #806(бубны)
#806(бубны) подтверждение → сигнал на #807(бубны)
    """
    bot.send_message(message.chat.id, help_text)

@bot.message_handler(func=lambda message: message.text == '◀️ Назад')
def go_back(message):
    start(message)

# Запуск бота
if name == 'main':
    init_db()
    print("🤖 Бот запущен с СИСТЕМОЙ СИГНАЛОВ...")
    print("📊 Первичный сигнал: N → N+3")
    print("📊 Повторный сигнал: при подтверждении N+1")
    print("Пример: 803(пики) → 806(бубны) → 807(бубны)")
    bot.polling(none_stop=True)
