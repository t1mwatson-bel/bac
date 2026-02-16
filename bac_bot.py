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
LOCK_FILE = f'/tmp/bot1_{TOKEN[-10:]}.lock'

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

# НОВЫЕ ПРАВИЛА СМЕНЫ МАСТЕЙ (Красная -> Красная, Черная -> Черная)
SUIT_CHANGE_RULES = {
    '♥️': '♦️',  # Черва (красная) -> Бубна (красная)
    '♦️': '♥️',  # Бубна (красная) -> Черва (красная)
    '♠️': '♣️',  # Пики (черная) -> Трефа (черная)
    '♣️': '♠️'   # Трефа (черная) -> Пики (черная)
}

# НОВЫЙ ДИАПАЗОН (10-19, 30-39, 50-59 и т.д.)
VALID_RANGES = [
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

def is_valid_game(game_num):
    """Проверяет, входит ли номер игры в допустимые диапазоны (для создания паттернов)"""
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
    """Парсит данные игры из текста - ТОЛЬКО ЛЕВАЯ РУКА"""
    # Ищем номер игры
    match = re.search(r'#N(\d+)', text)
    if not match:
        return None
    
    game_num = int(match.group(1))
    
    # Проверяем наличие специальных тегов
    has_r_tag = '#R' in text
    has_x_tag = '#X' in text or '#X🟡' in text
    has_check = '✅' in text
    has_t = re.search(r'#T\d+', text) is not None
    
    # Извлекаем ТОЛЬКО левую часть (руку игрока)
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
    
    # Определяем первую карту
    first_suit = suits[0] if len(suits) > 0 else None
    
    logger.info(f"📊 Левая рука игры #{game_num}: карты {suits}")
    logger.info(f"📊 Теги: #R={has_r_tag}, #X={has_x_tag}")
    
    return {
        'game_num': game_num,
        'first_suit': first_suit,
        'all_suits': suits,
        'left_cards': suits,
        'has_r_tag': has_r_tag,
        'has_x_tag': has_x_tag,
        'has_check': has_check,
        'has_t': has_t
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

class Storage:
    def __init__(self):
        self.analyzer = SuitAnalyzer()
        self.game_history = {}
        self.strategy2_predictions = {}
        self.strategy2_counter = 0
        self.strategy2_stats = {'total': 0, 'wins': 0, 'losses': 0}
        self.patterns = {}  # Ожидающие паттерны
        self.predictions = {}  # Активные прогнозы
        
    def add_to_history(self, game_data):
        game_num = game_data['game_num']
        
        # Обновляем историю, сохраняя все карты игры
        if game_num in self.game_history:
            existing = self.game_history[game_num]
            existing['all_suits'] = game_data['all_suits']
            existing['last_update'] = datetime.now()
        else:
            self.game_history[game_num] = game_data
        
        # Добавляем все карты в анализатор для обучения
        if game_data['all_suits']:
            for suit in game_data['all_suits']:
                self.analyzer.add_suit(suit)
        
        # Ограничиваем размер истории
        if len(self.game_history) > 200:
            oldest_key = min(self.game_history.keys())
            del self.game_history[oldest_key]
    
    def is_game_already_in_predictions(self, game_num):
        for pred in self.strategy2_predictions.values():
            if pred['status'] == 'pending' and game_num in pred['check_games']:
                return True
        return False
    
    def was_game_in_finished_predictions(self, game_num):
        for pred in self.strategy2_predictions.values():
            if pred['status'] in ['win', 'loss'] and game_num in pred['check_games']:
                return True
        return False
    
    def check_deal_before_game(self, game_num):
        prev_game_num = get_next_game_number(game_num, -1)
        if prev_game_num in self.game_history:
            prev_game = self.game_history[prev_game_num]
            if prev_game.get('has_r_tag', False):
                return True
        return False
    
    def predict_suit_for_card(self, card_value):
        if card_value not in card_stats or not card_stats[card_value]:
            return random.choice(SUITS), 0.5
        
        total = sum(card_stats[card_value].values())
        if total == 0:
            return random.choice(SUITS), 0.5
        
        best_suit = max(card_stats[card_value].items(), key=lambda x: x[1])
        probability = best_suit[1] / total
        
        return best_suit[0], probability

# ===== ГЛОБАЛЬНЫЙ STORAGE =====
storage = Storage()

def get_next_game_number(current_game, increment=1):
    next_game = current_game + increment
    while next_game > MAX_GAME_NUMBER:
        next_game -= MAX_GAME_NUMBER
    while next_game < 1:
        next_game += MAX_GAME_NUMBER
    return next_game

def get_funny_phrase():
    return random.choice(FUNNY_PHRASES)

def get_win_phrase():
    return random.choice(WIN_PHRASES)

def get_loss_phrase():
    return random.choice(LOSS_PHRASES)

def get_draw_phrase():
    return random.choice(DRAW_PHRASES)

async def check_predictions(current_game_num, game_data, context):
    """Проверяет активные прогнозы ТОЛЬКО когда пришла следующая игра"""
    logger.info(f"\n{'🔍'*30}")
    logger.info(f"🔍 ПРОВЕРКА ПРОГНОЗОВ для игры #{current_game_num}")
    logger.info(f"{'🔍'*30}")
    
    # Показываем все активные прогнозы
    active_preds = [p for p in storage.strategy2_predictions.values() if p['status'] == 'pending']
    logger.info(f"📊 Активных прогнозов: {len(active_preds)}")
    
    for pred_id, pred in list(storage.strategy2_predictions.items()):
        if pred['status'] != 'pending':
            continue
        
        target_game = pred['target_game']
        logger.info(f"\n🎯 Прогноз #{pred_id}: целевая игра #{target_game}, ищем масть {pred['original_suit']}")
        
        # Проверяем, является ли текущая игра следующей после целевой
        if current_game_num == target_game + 1:
            logger.info(f"✅ Игра #{current_game_num} - это следующая игра после целевой #{target_game}")
            logger.info(f"   Значит, игра #{target_game} уже завершена и можно проверять результат")
            
            # Получаем данные целевой игры из истории
            target_game_data = storage.game_history.get(target_game)
            
            if not target_game_data:
                logger.info(f"⚠️ Данные игры #{target_game} еще не сохранены, пропускаем")
                continue
            
            target_cards = target_game_data.get('all_suits', [])
            logger.info(f"🃏 Карты левой руки целевой игры #{target_game}: {target_cards}")
            
            # Проверяем ТОЛЬКО первую карту
            suit_found = False
            if target_cards and compare_suits(pred['original_suit'], target_cards[0]):
                suit_found = True
                logger.info(f"   ✅✅✅ НАШЛИ в первой карте игры #{target_game}: {target_cards[0]}")
            
            # Также проверяем теги в целевой игре
            has_r_tag = target_game_data.get('has_r_tag', False)
            has_x_tag = target_game_data.get('has_x_tag', False)
            has_check = target_game_data.get('has_check', False)
            
            if suit_found or has_r_tag or has_x_tag or has_check:
                if suit_found:
                    logger.info(f"✅ ПРОГНОЗ #{pred_id} ВЫИГРАЛ! Нашли масть {pred['original_suit']} в первой карте игры #{target_game}")
                else:
                    logger.info(f"✅ ПРОГНОЗ #{pred_id} ВЫИГРАЛ по тегу в игре #{target_game}!")
                
                pred['status'] = 'win'
                storage.strategy2_stats['wins'] += 1
                await update_prediction_result(pred, target_game, 'win', context)
            else:
                logger.info(f"❌ Масть {pred['original_suit']} не найдена в первой карте игры #{target_game}")
                
                # Проверяем, есть ли еще попытки (догоны)
                if pred['attempt'] >= 2:  # Всего 3 попытки (0,1,2)
                    logger.info(f"💔 Все попытки исчерпаны")
                    pred['status'] = 'loss'
                    storage.strategy2_stats['losses'] += 1
                    await update_prediction_result(pred, target_game, 'loss', context)
                else:
                    # Переходим к следующей попытке (догону)
                    pred['attempt'] += 1
                    # Сдвигаем целевую игру для догона
                    pred['target_game'] = pred['check_games'][pred['attempt']]
                    logger.info(f"🔄 Прогноз #{pred_id} переходит к догону {pred['attempt']}, новая целевая игра: #{pred['target_game']}")
                    await update_dogon_message(pred, context)

async def check_patterns(game_num, game_data, context):
    """Проверяет ожидающие паттерны и создает прогнозы (ТОЛЬКО для нужных диапазонов)"""
    first_suit = game_data['first_suit']
    all_suits = game_data['all_suits']  # Все масти в руке игрока
    
    if not first_suit:
        return
    
    # Проверяем, четная или нечетная игра
    is_odd = game_num % 2 != 0
    
    # Проверяем, есть ли паттерн для этой игры (ждем подтверждения)
    if game_num in storage.patterns:
        pattern = storage.patterns[game_num]
        expected_suit = pattern['suit']
        
        # Проверяем ПЕРВУЮ ИЛИ ВТОРУЮ карту (хотя бы одна из первых двух имеет нужную масть)
        suit_found = False
        found_position = None
        
        if len(all_suits) >= 1:
            if compare_suits(expected_suit, all_suits[0]):
                suit_found = True
                found_position = "первой"
                logger.info(f"✅ Нашли масть {expected_suit} в ПЕРВОЙ карте левой руки игры #{game_num}")
        
        if not suit_found and len(all_suits) >= 2:
            if compare_suits(expected_suit, all_suits[1]):
                suit_found = True
                found_position = "второй"
                logger.info(f"✅ Нашли масть {expected_suit} в ВТОРОЙ карте левой руки игры #{game_num}")
        
        if suit_found:
            # Паттерн подтвердился! Создаем прогноз
            target_game = game_num + 1
            predicted_suit = SUIT_CHANGE_RULES.get(expected_suit)
            
            if predicted_suit:
                storage.strategy2_counter += 1
                pred_id = storage.strategy2_counter
                
                # Игры для догона (следующие 3 игры после целевой)
                check_games = [
                    target_game,
                    target_game + 1,
                    target_game + 2
                ]
                
                prediction = {
                    'id': pred_id,
                    'game_num': pattern['source_game'],
                    'target_game': target_game,
                    'original_suit': predicted_suit,
                    'confidence': 0.8,
                    'check_games': check_games,
                    'status': 'pending',
                    'attempt': 0,
                    'created_at': datetime.now(),
                    'result_game': None,
                    'channel_message_id': None,
                    'checked_games': [],
                    'found_in_cards': [],
                    'win_announced': False
                }
                
                storage.strategy2_predictions[pred_id] = prediction
                
                logger.info(f"🎯 ПАТТЕРН ПОДТВЕРЖДЕН!")
                logger.info(f"   Исходная игра #{pattern['source_game']}: масть {pattern['suit']}")
                logger.info(f"   Проверочная игра #{game_num}: масть найдена в {found_position} карте")
                logger.info(f"🤖 НОВЫЙ ПРОГНОЗ #{pred_id}: {predicted_suit} в игре #{target_game}")
                
                # Отправляем прогноз в канал
                await send_prediction_to_channel(prediction, context)
        else:
            logger.info(f"❌ Паттерн не подтвержден: в 1-й или 2-й карте игры #{game_num} нет масти {expected_suit}")
            if len(all_suits) >= 2:
                logger.info(f"   Карты в руке: {all_suits[0]}, {all_suits[1]}")
            elif len(all_suits) >= 1:
                logger.info(f"   Карта в руке: {all_suits[0]}")
            else:
                logger.info(f"   В руке нет карт")
        
        # Удаляем обработанный паттерн
        del storage.patterns[game_num]
    
    # Создаем новый паттерн ТОЛЬКО от НЕЧЕТНЫХ игр и ТОЛЬКО в нужных диапазонах
    # ЧЕРЕЗ 2 ИГРЫ
    if is_odd and is_valid_game(game_num):
        check_game = game_num + 2  # Проверка через 2 игры
        storage.patterns[check_game] = {
            'suit': first_suit,
            'source_game': game_num,
            'created': datetime.now()
        }
        
        logger.info(f"📝 Создан паттерн от НЕЧЕТНОЙ игры #{game_num}({first_suit}) -> проверка через 2 игры в #{check_game}")
        logger.info(f"   Условие: в игре #{check_game} 1-я ИЛИ 2-я карта должна быть {first_suit}")
    elif is_odd and not is_valid_game(game_num):
        logger.info(f"⏭️ Игра #{game_num} НЕЧЕТНАЯ, но вне диапазона - паттерн не создаем")
    else:
        logger.info(f"⏭️ Игра #{game_num} ЧЕТНАЯ - пропускаем создание паттерна")

async def send_prediction_to_channel(prediction, context):
    """Отправляет прогноз в канал"""
    try:
        text = (
            f"🎯 *BOT1 - НОВЫЙ ПРОГНОЗ #{prediction['id']}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📊 *ДЕТАЛИ:*\n"
            f"┣ 🎯 Целевая игра: #{prediction['target_game']}\n"
            f"┣ 🃏 Прогнозируемая масть: {prediction['original_suit']}\n"
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

async def update_dogon_message(prediction, context):
    """Обновляет сообщение о догоне"""
    try:
        if prediction['attempt'] == 1:
            dogon_text = "🔄 *ПЕРЕХОД К ДОГОНУ 1*"
            previous_attempt = 0
        else:
            dogon_text = "🔄 *ПЕРЕХОД К ДОГОНУ 2*"
            previous_attempt = 1
        
        next_game = prediction['target_game']
        
        text = (
            f"{dogon_text}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🎯 *BOT1 - ПРОГНОЗ #{prediction['id']} ПРОДОЛЖАЕТСЯ*\n\n"
            f"📊 *СТАТУС:*\n"
            f"┣ 🔄 Текущий догон: {prediction['attempt']}/2\n"
            f"┣ 🎮 Предыдущая игра: #{prediction['check_games'][previous_attempt]}\n"
            f"┣ 🎲 Искали масть: {prediction['original_suit']} в левой руке\n"
            f"┣ ❌ Результат: не найдена\n"
            f"┣ 🎯 Следующая игра: #{next_game}\n"
            f"┗ 🎲 Ищем масть: {prediction['original_suit']} в левой руке\n\n"
            f"⏳ *ОЖИДАЕМ РЕЗУЛЬТАТ...*"
        )
        
        if prediction.get('channel_message_id'):
            await context.bot.edit_message_text(
                chat_id=OUTPUT_CHANNEL_ID,
                message_id=prediction['channel_message_id'],
                text=text,
                parse_mode='Markdown'
            )
        
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")

async def update_prediction_result(prediction, game_num, result, context):
    """Обновляет результат прогноза"""
    try:
        if not prediction.get('channel_message_id'):
            return
        
        if result == 'win':
            emoji = "✅"
            status = "ЗАШЁЛ"
            result_emoji = "🏆"
            
            text = (
                f"{emoji} *BOT1 - ПРОГНОЗ #{prediction['id']} {status}!* {result_emoji}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📊 *РЕЗУЛЬТАТ:*\n"
                f"┣ 🎯 Целевая игра: #{prediction['target_game']}\n"
                f"┣ 🃏 Масть: {prediction['original_suit']}\n"
                f"┣ 🔄 Попытка: {['основная', 'догон 1', 'догон 2'][prediction['attempt']]}\n"
                f"┣ 🎮 Проверено в игре: #{game_num}\n"
                f"┣ 📊 Статистика: {storage.strategy2_stats['wins']}✅ / {storage.strategy2_stats['losses']}❌\n"
                f"┗ ⏱ {datetime.now().strftime('%H:%M:%S')}"
            )
        else:
            loss_phrase = get_loss_phrase()
            text = (
                f"{loss_phrase}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"😔 *BOT1 - ПРОГНОЗ #{prediction['id']} НЕ ЗАШЁЛ*\n\n"
                f"💔 *РЕЗУЛЬТАТ:*\n"
                f"┣ 🎯 Масть {prediction['original_suit']} не появилась в первой карте\n"
                f"┣ 🎮 Проверено игр: {len(prediction['check_games'])}\n"
                f"┣ 🔄 Попыток: {prediction['attempt'] + 1}\n"
                f"┣ 📊 Статистика: {storage.strategy2_stats['wins']}✅ / {storage.strategy2_stats['losses']}❌\n"
                f"┗ ⏱ {datetime.now().strftime('%H:%M:%S')}"
            )
        
        await context.bot.edit_message_text(
            chat_id=OUTPUT_CHANNEL_ID,
            message_id=prediction['channel_message_id'],
            text=text,
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")

async def handle_new_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает входящие сообщения"""
    try:
        message = update.channel_post or update.message
        if not message or not message.text:
            return
        
        if update.effective_chat.id != INPUT_CHANNEL_ID:
            return
        
        text = message.text
        logger.info(f"\n{'='*60}")
        logger.info(f"📥 Получено: {text[:150]}...")
        
        # Парсим данные игры
        game_data = parse_game_data(text)
        if not game_data:
            return
        
        game_num = game_data['game_num']
        first_suit = game_data['first_suit']
        
        logger.info(f"📊 Игра #{game_num} ({'НЕЧЕТНАЯ' if game_num%2 else 'ЧЕТНАЯ'}): первая карта {first_suit}")
        logger.info(f"📊 Все карты левой руки: {game_data['all_suits']}")
        logger.info(f"📊 Теги: #R={game_data.get('has_r_tag', False)}, #X={game_data.get('has_x_tag', False)}")
        
        # Сохраняем в историю (ВСЕГДА)
        storage.add_to_history(game_data)
        
        # 1. Проверяем активные прогнозы
        await check_predictions(game_num, game_data, context)
        
        # 2. ПОТОМ проверяем паттерны и создаем новые прогнозы (только для нужных диапазонов)
        await check_patterns(game_num, game_data, context)
        
        # Ограничиваем историю
        if len(storage.game_history) > 200:
            oldest = min(storage.game_history.keys())
            del storage.game_history[oldest]
        
        # Очищаем старые паттерны (> 50 игр)
        for check_game in list(storage.patterns.keys()):
            if check_game < game_num - 50:
                logger.info(f"🗑️ Удаляем старый паттерн для игры #{check_game}")
                del storage.patterns[check_game]
        
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")

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
    print("\n" + "="*60)
    print("🤖 BOT1 (КРАСНАЯ->КРАСНАЯ, ЧЕРНАЯ->ЧЕРНАЯ) ЗАПУЩЕН")
    print("="*60)
    print(f"✅ Диапазоны для создания паттернов: 10-19, 30-39, 50-59... до 1440")
    print(f"✅ Всего диапазонов: {len(VALID_RANGES)}")
    print("✅ ПРОВЕРЯЕТ ПРОГНОЗЫ ТОЛЬКО ПОСЛЕ ЗАВЕРШЕНИЯ ИГРЫ")
    print("✅ ПАТТЕРН: проверка через 2 игры (1-я ИЛИ 2-я карта)")
    print("✅ ПРОГНОЗ: проверка только первой карты")
    print("✅ Новые правила смены мастей:")
    print("   - Черва (♥️) -> Бубна (♦️) (красная -> красная)")
    print("   - Бубна (♦️) -> Черва (♥️) (красная -> красная)")
    print("   - Пики (♠️) -> Трефа (♣️) (черная -> черная)")
    print("   - Трефа (♣️) -> Пики (♠️) (черная -> черная)")
    print("✅ Выходной канал: -1003842401391")
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
