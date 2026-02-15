# -*- coding: utf-8 -*-
import logging
import re
import random
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

# === НАСТРОЙКИ ===
TOKEN = "1163348874:AAFgZEXveILvD4MbhQ8jiLTwIxs4puYhmq0"
INPUT_CHANNEL_ID = -1003469691743
OUTPUT_CHANNEL_ID = -1003842401391
ADMIN_ID = 683219603

# Уникальный lock-файл для этого бота
LOCK_FILE = f'/tmp/bot_{TOKEN[-10:]}.lock'

MAX_GAME_NUMBER = 1440

# Время ожидания добора карт (в секундах)
DRAW_WAIT_TIME = 30

FUNNY_PHRASES = [
    "🎰 ВА-БАНК! ОБНАРУЖЕН СУПЕР ПАТТЕРН! 🎰",
    "🚀 РАКЕТА ЗАПУЩЕНА! ЛЕТИМ ЗА ПОБЕДОЙ! 🚀",
    "💎 АЛМАЗНЫЙ СИГНАЛ ПРИЛЕТЕЛ! 💎",
    "🎯 СНАЙПЕР В ЦЕЛИ! ТОЧНЫЙ РАСЧЕТ! 🎯",
    "🔥 ГОРИМ ЖЕЛАНИЕМ ПОБЕДИТЬ! 🔥"
]

WIN_PHRASES = [
    "🎉 УРА! СТРАТЕГИЯ СРАБОТАЛА! 🎉",
    "💰 КАЗИНО В ШОКЕ! МЫ ВЫИГРАЛИ! 💰",
    "🥇 ЗОЛОТАЯ ПОБЕДА! ТОЧНО В ЦЕЛЬ! 🥇",
    "🏅 ОЛИМПИЙСКАЯ ТОЧНОСТЬ! ПОБЕДА! 🏅",
    "🎯 БИНГО! ПОПАДАНИЕ В ЯБЛОЧКО! 🎯"
]

LOSS_PHRASES = [
    "😔 УВЫ, НЕ СЕГОДНЯ...",
    "🌧️ НЕБО ПЛАЧЕТ, И МЫ ТОЖЕ...",
    "🍀 НЕ ПОВЕЗЛО В ЭТОТ РАЗ...",
    "🎭 ДРАМА... НО МЫ НЕ СДАЕМСЯ!",
    "🤡 ЦИРК ВЕРНУЛСЯ... ШУТКА НЕ УДАЛАСЬ"
]

DRAW_PHRASES = [
    "🔄 ИГРОК ДОБИРАЕТ КАРТУ! ЖДЕМ РЕЗУЛЬТАТ...",
    "🎴 ДОБОР! СМОТРИМ, ЧТО ВЫПАДЕТ...",
    "🤞 ИГРОК РИСКУЕТ И ДОБИРАЕТ!",
    "⚡️ ВОЛНУЮЩИЙ МОМЕНТ - ДОБОР КАРТЫ!"
]

SUITS = ["♥️", "♠️", "♣️", "♦️"]

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

pending_games = {}
prediction_messages = {}
lock_fd = None

# Статистика замен
card_stats = defaultdict(lambda: defaultdict(int))

# Хранилище для отслеживания игр в процессе добора
pending_draws = {}

# === КОНФИГУРАЦИЯ БОТА ===
# Здесь можно переключать между разными стратегиями
BOT_CONFIG = {
    # Для первого бота (старые правила)
    'mode': 'bot1',  # 'bot1' или 'bot2'
    
    # Правила для bot1
    'bot1': {
        'suit_rules': {
            '♥️': '♣️',  # Черва -> Трефа
            '♣️': '♥️',  # Трефа -> Черва
            '♦️': '♠️',  # Бубна -> Пики
            '♠️': '♦️'   # Пики -> Бубна
        },
        'ranges': [
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
    },
    
    # Правила для bot2
    'bot2': {
        'suit_rules': {
            '♥️': '♦️',  # Черва -> Бубна
            '♦️': '♥️',  # Бубна -> Черва
            '♠️': '♣️',  # Пики -> Трефа
            '♣️': '♠️'   # Трефа -> Пики
        },
        'ranges': [
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
            (1110, 1119), (1130, 1139), (1150, 1159), (1170, 1179), (1190, 1199),
            (1210, 1219), (1230, 1239), (1250, 1259), (1270, 1279), (1290, 1299),
            (1310, 1319), (1330, 1339), (1350, 1359), (1370, 1379), (1390, 1399),
            (1410, 1419), (1430, 1439)
        ]
    }
}

# Активная конфигурация
active_config = BOT_CONFIG[BOT_CONFIG['mode']]
SUIT_CHANGE_RULES = active_config['suit_rules']
VALID_RANGES = active_config['ranges']

def is_valid_game(game_num):
    """Проверяет, входит ли номер игры в допустимые диапазоны"""
    for start, end in VALID_RANGES:
        if start <= game_num <= end:
            return True
    return False

def acquire_lock():
    """Блокировка для предотвращения множественных запусков"""
    global lock_fd
    try:
        lock_fd = open(LOCK_FILE, 'w')
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        logger.info(f"🔒 Блокировка получена: {LOCK_FILE}")
        return True
    except (IOError, OSError):
        logger.error(f"❌ Бот уже запущен (lock файл: {LOCK_FILE})")
        return False

def release_lock():
    """Освобождение блокировки"""
    global lock_fd
    if lock_fd:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()
            if os.path.exists(LOCK_FILE):
                os.unlink(LOCK_FILE)
            logger.info("🔓 Блокировка освобождена")
        except Exception as e:
            logger.error(f"❌ Ошибка при освобождении блокировки: {e}")

def check_bot_token():
    """Проверка токена бота"""
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/getMe"
        req = urllib.request.Request(url, method='GET')
        
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode('utf-8'))
            if data.get('ok'):
                bot_info = data['result']
                logger.info(f"✅ Бот @{bot_info['username']} авторизован")
                return True
            else:
                logger.error(f"❌ Ошибка авторизации: {data}")
                return False
    except Exception as e:
        logger.error(f"❌ Ошибка при проверке токена: {e}")
        return False

def extract_left_part(text):
    """Извлекает левую часть сообщения (руку игрока)"""
    # Ищем разделители, после которых идет правая рука
    separators = [' 👈 ', '👈', ' - ', ' – ', '—', '-', '👉👈', '👈👉', '🔰']
    
    for sep in separators:
        if sep in text:
            parts = text.split(sep, 1)
            left_part = parts[0].strip()
            # Убираем номер игры из левой части если он там есть
            left_part = re.sub(r'#N\d+\.?\s*', '', left_part)
            return left_part
    
    return text.strip()

def parse_game_data(text):
    """Парсит данные игры из текста - ТОЛЬКО ЛЕВАЯ РУКА (ИГРОК)"""
    # Ищем номер игры
    match = re.search(r'#N(\d+)', text)
    if not match:
        return None
    
    game_num = int(match.group(1))
    
    # Проверяем, входит ли игра в нужные диапазоны
    if not is_valid_game(game_num):
        logger.info(f"⏭️ Игра #{game_num} не в целевом диапазоне, пропускаем")
        return None
    
    has_r_tag = '#R' in text
    has_x_tag = '#X' in text or '#X🟡' in text
    has_check = '✅' in text
    has_t = re.search(r'#T\d+', text) is not None
    
    is_completed = has_r_tag or has_x_tag or has_check or has_t
    
    # Извлекаем ТОЛЬКО левую часть (руку ИГРОКА)
    left_part = extract_left_part(text)
    logger.info(f"👈 Левая рука (ИГРОК): {left_part}")
    
    # Ищем масти ТОЛЬКО в левой части
    suits = []
    suit_patterns = {
        '♥️': r'[♥❤♡]',
        '♠️': r'[♠♤]',
        '♣️': r'[♣♧]',
        '♦️': r'[♦♢]'
    }
    
    for suit, pattern in suit_patterns.items():
        matches = re.findall(pattern, left_part)
        for _ in matches:
            suits.append(suit)
    
    if not suits:
        logger.warning(f"⚠️ В левой руке игры #{game_num} не найдено мастей")
        return None
    
    # Определяем первую и вторую карту (только из левой руки)
    first_suit = suits[0] if len(suits) > 0 else None
    second_suit = suits[1] if len(suits) > 1 else None
    
    logger.info(f"📊 Левая рука (ИГРОК) игры #{game_num}: карты {suits}")
    logger.info(f"📊 Теги: #R={has_r_tag}, #X={has_x_tag}")
    
    # Проверяем наличие правой руки для информации
    right_part = text.split('👈')[-1] if '👈' in text else ""
    logger.info(f"👉 Правая рука (БАНКИР): {right_part} (ИГНОРИРУЕМ)")
    
    return {
        'game_num': game_num,
        'first_suit': first_suit,
        'second_suit': second_suit,
        'all_suits': suits,
        'left_cards': suits,
        'has_r_tag': has_r_tag,
        'has_x_tag': has_x_tag,
        'has_check': has_check,
        'has_t': has_t,
        'is_completed': is_completed
    }

def compare_suits(suit1, suit2):
    """Сравнивает две масти"""
    if not suit1 or not suit2:
        return False
    
    suit_map = {
        '♥️': '♥', '♥': '♥', '❤': '♥', '♡': '♥',
        '♠️': '♠', '♠': '♠', '♤': '♠',
        '♣️': '♣', '♣': '♣', '♧': '♣',
        '♦️': '♦', '♦': '♦', '♢': '♦'
    }
    
    s1 = suit_map.get(suit1, suit1)
    s2 = suit_map.get(suit2, suit2)
    
    s1 = s1.replace('\ufe0f', '').replace('️', '').strip()
    s2 = s2.replace('\ufe0f', '').replace('️', '').strip()
    
    return s1 == s2

class SuitAnalyzer:
    def __init__(self):
        self.suit_history = []
        self.frequency = defaultdict(int)
        
    def add_suit(self, suit):
        if suit:
            if '♥' in suit or '❤' in suit or '♡' in suit:
                normalized = '♥️'
            elif '♠' in suit or '♤' in suit:
                normalized = '♠️'
            elif '♣' in suit or '♧' in suit:
                normalized = '♣️'
            elif '♦' in suit or '♢' in suit:
                normalized = '♦️'
            else:
                return
            
            self.suit_history.append(normalized)
            self.frequency[normalized] += 1
            
            if len(self.suit_history) > 20:
                removed_suit = self.suit_history.pop(0)
                self.frequency[removed_suit] -= 1
                if self.frequency[removed_suit] == 0:
                    del self.frequency[removed_suit]
    
    def predict_next_suit(self):
        if not self.suit_history:
            suit = random.choice(SUITS)
            confidence = 0.5
        else:
            total = sum(self.frequency.values())
            weights = [self.frequency[s] / total if total > 0 else 0.25 for s in SUITS]
            suit = random.choices(SUITS, weights=weights, k=1)[0]
            confidence = 0.6
        
        logger.info(f"🤖 AI выбрал: {suit} ({confidence*100:.1f}%)")
        return suit, confidence

class PatternStorage:
    def __init__(self):
        self.games = {}  # История игр
        self.patterns = {}  # Ожидающие паттерны: {check_game: {'suit': suit, 'source_game': source_game}}
        self.predictions = {}  # Активные прогнозы
        self.stats = {'wins': 0, 'losses': 0}
        self.prediction_counter = 0
        self.analyzer = SuitAnalyzer()
        
    def add_to_history(self, game_data):
        game_num = game_data['game_num']
        self.games[game_num] = game_data
        
        # Добавляем в анализатор для обучения
        if game_data['all_suits']:
            for suit in game_data['all_suits']:
                self.analyzer.add_suit(suit)
        
        # Ограничиваем размер истории
        if len(self.games) > 200:
            oldest = min(self.games.keys())
            del self.games[oldest]
    
    async def check_patterns(self, game_num, game_data, context):
        """Проверяет ожидающие паттерны и создает прогнозы"""
        first_suit = game_data['first_suit']
        second_suit = game_data['second_suit']
        
        if not first_suit:
            return
        
        # Проверяем, четная или нечетная игра
        is_odd = game_num % 2 != 0
        
        # Проверяем, есть ли паттерн для этой игры
        if game_num in self.patterns:
            pattern = self.patterns[game_num]
            expected_suit = pattern['suit']
            
            # Проверяем ИЛИ в первой карте, ИЛИ во второй (только левая рука)
            suit_found = False
            if compare_suits(expected_suit, first_suit):
                suit_found = True
                logger.info(f"✅ Нашли масть {expected_suit} в первой карте левой руки игры #{game_num}")
            elif second_suit and compare_suits(expected_suit, second_suit):
                suit_found = True
                logger.info(f"✅ Нашли масть {expected_suit} во второй карте левой руки игры #{game_num}")
            
            if suit_found:
                # Паттерн подтвердился! Создаем прогноз
                target_game = game_num + 1
                predicted_suit = SUIT_CHANGE_RULES.get(expected_suit)
                
                if predicted_suit:
                    self.prediction_counter += 1
                    pred_id = self.prediction_counter
                    
                    # Игры для догона (следующие 3 игры после целевой)
                    check_games = [
                        target_game,
                        target_game + 1,
                        target_game + 2
                    ]
                    
                    prediction = {
                        'id': pred_id,
                        'suit': predicted_suit,
                        'target': target_game,
                        'check_games': check_games,
                        'status': 'pending',
                        'attempt': 0,
                        'created': datetime.now(),
                        'channel_message_id': None,
                        'checked_games': [],
                        'found_in_cards': []
                    }
                    
                    self.predictions[pred_id] = prediction
                    
                    logger.info(f"🎯 ПАТТЕРН ПОДТВЕРЖДЕН!")
                    logger.info(f"   Исходная игра #{pattern['source_game']} (НЕЧЕТНАЯ): масть {pattern['suit']}")
                    logger.info(f"   Проверочная игра #{game_num}: масть найдена в левой руке")
                    logger.info(f"🤖 НОВЫЙ ПРОГНОЗ #{pred_id}: {predicted_suit} в игре #{target_game}")
                    logger.info(f"📋 Проверка: {check_games}")
                    
                    # Отправляем прогноз в канал
                    await self.send_prediction(prediction, context)
            else:
                logger.info(f"❌ Паттерн не подтвержден: в левой руке игры #{game_num} нет масти {expected_suit}")
            
            # Удаляем обработанный паттерн
            del self.patterns[game_num]
        
        # Создаем новый паттерн ТОЛЬКО от НЕЧЕТНЫХ игр
        if is_odd:
            check_game = game_num + 3
            self.patterns[check_game] = {
                'suit': first_suit,
                'source_game': game_num,
                'created': datetime.now()
            }
            
            logger.info(f"📝 Создан паттерн от НЕЧЕТНОЙ игры #{game_num}({first_suit}) -> проверка в #{check_game} (ищем в 1й или 2й карте левой руки)")
    
    async def check_predictions(self, game_num, game_data, context):
        """Проверяет активные прогнозы"""
        for pred_id, pred in list(self.predictions.items()):
            if pred['status'] != 'pending':
                continue
            
            if game_num in pred['check_games']:
                if game_num not in pred['checked_games']:
                    pred['checked_games'].append(game_num)
                
                game_idx = pred['check_games'].index(game_num)
                
                if game_idx == pred['attempt']:
                    # Проверяем, есть ли нужная масть в картах левой руки (ИГРОК)
                    suit_found = pred['suit'] in game_data['all_suits']
                    
                    if suit_found:
                        logger.info(f"✅ ПРОГНОЗ #{pred_id} ВЫИГРАЛ в игре #{game_num} (нашли масть {pred['suit']} в левой руке)")
                        
                        # Записываем в каких картах нашли
                        found_cards = []
                        for idx, found_suit in enumerate(game_data['all_suits']):
                            if compare_suits(pred['suit'], found_suit):
                                found_cards.append(idx + 1)
                        pred['found_in_cards'] = found_cards
                        
                        pred['status'] = 'win'
                        self.stats['wins'] += 1
                        await self.update_prediction_result(pred, game_num, 'win', context)
                    else:
                        logger.info(f"❌ Прогноз #{pred_id} не выиграл в игре #{game_num} - масть {pred['suit']} не найдена в левой руке")
                        
                        if pred['attempt'] >= len(pred['check_games']) - 1:
                            pred['status'] = 'loss'
                            self.stats['losses'] += 1
                            await self.update_prediction_result(pred, game_num, 'loss', context)
                        else:
                            pred['attempt'] += 1
                            next_game = pred['check_games'][pred['attempt']]
                            logger.info(f"🔄 Прогноз #{pred_id} переходит к догону {pred['attempt']}, следующая игра: #{next_game}")
                            await self.update_prediction_message(pred, context)
    
    async def send_prediction(self, prediction, context):
        """Отправляет прогноз в канал"""
        try:
            mode_name = "BOT1" if BOT_CONFIG['mode'] == 'bot1' else "BOT2"
            
            text = (
                f"🎯 *{mode_name} - НОВЫЙ ПРОГНОЗ #{prediction['id']}*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📊 *ДЕТАЛИ:*\n"
                f"┣ 🎯 Целевая игра: #{prediction['target']}\n"
                f"┣ 🃏 Прогнозируемая масть: {prediction['suit']}\n"
                f"┣ 🔄 Догон 1: #{prediction['check_games'][1]}\n"
                f"┣ 🔄 Догон 2: #{prediction['check_games'][2]}\n"
                f"┗ ⏱ {datetime.now().strftime('%H:%M:%S')}"
            )
            
            message = await context.bot.send_message(
                chat_id=OUTPUT_CHANNEL_ID,
                text=text,
                parse_mode='Markdown'
            )
            
            prediction['channel_message_id'] = message.message_id
            
        except Exception as e:
            logger.error(f"❌ Ошибка при отправке прогноза: {e}")
    
    async def update_prediction_result(self, prediction, game_num, result, context):
        """Обновляет сообщение с результатом прогноза"""
        try:
            if not prediction.get('channel_message_id'):
                return
            
            mode_name = "BOT1" if BOT_CONFIG['mode'] == 'bot1' else "BOT2"
            
            if result == 'win':
                emoji = "✅"
                status = "ЗАШЁЛ"
                result_emoji = "🏆"
                result_text = f"Масть {prediction['suit']} найдена в левой руке!"
            else:
                emoji = "❌"
                status = "НЕ ЗАШЁЛ"
                result_emoji = "💔"
                result_text = f"Масть {prediction['suit']} не найдена в левой руке"
            
            attempt_names = ["основная", "догон 1", "догон 2"]
            attempt_text = attempt_names[prediction['attempt']]
            
            cards_info = ""
            if prediction.get('found_in_cards'):
                cards_list = ", ".join([f"#{card}" for card in prediction['found_in_cards']])
                cards_info = f"┣ 🃏 Найдена в картах: {cards_list}\n"
            
            text = (
                f"{emoji} *{mode_name} - ПРОГНОЗ #{prediction['id']} {status}!* {result_emoji}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📊 *РЕЗУЛЬТАТ:*\n"
                f"┣ 🎯 Целевая игра: #{prediction['target']}\n"
                f"┣ 🃏 Масть: {prediction['suit']}\n"
                f"┣ 🔄 Попытка: {attempt_text}\n"
                f"┣ 🎮 Проверено в игре: #{game_num}\n"
                f"{cards_info}"
                f"┣ {result_text}\n"
                f"┣ 📊 Статистика: {self.stats['wins']}✅ / {self.stats['losses']}❌\n"
                f"┗ ⏱ {datetime.now().strftime('%H:%M:%S')}"
            )
            
            await context.bot.edit_message_text(
                chat_id=OUTPUT_CHANNEL_ID,
                message_id=prediction['channel_message_id'],
                text=text,
                parse_mode='Markdown'
            )
            
        except Exception as e:
            logger.error(f"❌ Ошибка при обновлении результата: {e}")
    
    async def update_prediction_message(self, prediction, context):
        """Обновляет сообщение о догоне"""
        try:
            if not prediction.get('channel_message_id'):
                return
            
            mode_name = "BOT1" if BOT_CONFIG['mode'] == 'bot1' else "BOT2"
            next_game = prediction['check_games'][prediction['attempt']]
            
            text = (
                f"🔄 *{mode_name} - ПРОГНОЗ #{prediction['id']} - ДОГОН {prediction['attempt']}*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📊 *ДЕТАЛИ:*\n"
                f"┣ 🎯 Целевая игра: #{prediction['target']}\n"
                f"┣ 🃏 Масть: {prediction['suit']}\n"
                f"┣ 🔄 Текущая попытка: {prediction['attempt']}/2\n"
                f"┣ 🎯 Следующая игра: #{next_game}\n"
                f"┗ ⏱ {datetime.now().strftime('%H:%M:%S')}"
            )
            
            await context.bot.edit_message_text(
                chat_id=OUTPUT_CHANNEL_ID,
                message_id=prediction['channel_message_id'],
                text=text,
                parse_mode='Markdown'
            )
            
        except Exception as e:
            logger.error(f"❌ Ошибка при обновлении сообщения: {e}")

# Создаем хранилище
storage = PatternStorage()

async def handle_new_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает входящие сообщения"""
    try:
        if not update.channel_post:
            return
        
        text = update.channel_post.text
        if not text:
            return
        
        logger.info(f"\n{'='*60}")
        logger.info(f"📥 Получено: {text[:150]}...")
        
        # Парсим данные игры (только левая рука)
        game_data = parse_game_data(text)
        if not game_data:
            return
        
        game_num = game_data['game_num']
        first_suit = game_data['first_suit']
        second_suit = game_data['second_suit']
        
        mode_name = "BOT1" if BOT_CONFIG['mode'] == 'bot1' else "BOT2"
        logger.info(f"🤖 {mode_name} обрабатывает игру #{game_num}")
        logger.info(f"📊 Левая рука (ИГРОК): 1-я карта {first_suit}, 2-я карта {second_suit}")
        
        # Сохраняем игру в историю
        storage.add_to_history(game_data)
        
        # Проверяем паттерны (создаем новые и проверяем существующие)
        await storage.check_patterns(game_num, game_data, context)
        
        # Проверяем активные прогнозы
        await storage.check_predictions(game_num, game_data, context)
        
        # Очищаем старые паттерны (> 50 игр)
        for check_game in list(storage.patterns.keys()):
            if check_game < game_num - 50:
                logger.info(f"🗑️ Удаляем старый паттерн для игры #{check_game}")
                del storage.patterns[check_game]
        
    except Exception as e:
        logger.error(f"❌ Ошибка в handle_new_game: {e}")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает ошибки"""
    try:
        if isinstance(context.error, Conflict):
            logger.warning("⚠️ Обнаружен конфликт с другим экземпляром бота")
            release_lock()
            sys.exit(1)
        else:
            logger.error(f"❌ Ошибка: {context.error}")
    except Exception as e:
        logger.error(f"❌ Ошибка в error_handler: {e}")

def main():
    mode_name = "BOT1" if BOT_CONFIG['mode'] == 'bot1' else "BOT2"
    rules_text = "Черва->Трефа, Трефа->Черва, Бубна->Пики, Пики->Бубна" if BOT_CONFIG['mode'] == 'bot1' else "Черва->Бубна, Бубна->Черва, Пики->Трефа, Трефа->Пики"
    
    print("\n" + "="*60)
    print(f"🤖 {mode_name} ЗАПУЩЕН")
    print("="*60)
    print(f"✅ Диапазонов игр: {len(VALID_RANGES)}")
    print(f"✅ Правила смены мастей: {rules_text}")
    print("✅ Анализирует ТОЛЬКО левую руку (ИГРОК)")
    print("✅ Игнорирует правую руку (БАНКИР)")
    print("✅ Создает паттерны ТОЛЬКО от НЕЧЕТНЫХ игр")
    print("✅ Ждет подтверждения через 3 игры")
    print("✅ Проверяет в 1й или 2й карте левой руки")
    print("✅ Догон на 2 игры")
    print("="*60)
    
    # Проверяем блокировку
    if not acquire_lock():
        logger.error("❌ Не удалось получить блокировку. Возможно бот уже запущен.")
        sys.exit(1)
    
    # Проверяем токен
    if not check_bot_token():
        logger.error("❌ Ошибка авторизации бота")
        release_lock()
        sys.exit(1)
    
    # Создаем приложение
    application = Application.builder().token(TOKEN).build()
    
    # Добавляем обработчик ошибок
    application.add_error_handler(error_handler)
    
    # Добавляем обработчик сообщений
    application.add_handler(MessageHandler(
        filters.Chat(INPUT_CHANNEL_ID) & filters.TEXT,
        handle_new_game
    ))
    
    try:
        # Запускаем бота
        application.run_polling(
            allowed_updates=['channel_post'],
            drop_pending_updates=True
        )
    except Conflict:
        logger.error("❌ Конфликт при запуске")
        release_lock()
        sys.exit(1)
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}")
        release_lock()
        sys.exit(1)
    finally:
        release_lock()

if __name__ == "__main__":
    main()