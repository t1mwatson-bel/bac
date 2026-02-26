# -*- coding: utf-8 -*-
import logging
import re
import os
import sys
import fcntl
import urllib.request
import urllib.error
import json
from datetime import datetime, time, timedelta
from collections import defaultdict, deque
from telegram import Update
from telegram.ext import (
    Application,
    MessageHandler,
    filters,
    ContextTypes
)
from telegram.error import Conflict
import random
import pytz
import numpy as np
from sklearn.ensemble import (
    RandomForestClassifier,
    GradientBoostingClassifier
)
import joblib

# ======== НАСТРОЙКА ЛОГИРОВАНИЯ ========
class JsonFormatter(logging.Formatter):
    def format(self, record):
        log_record = {
            "message": record.getMessage(),
            "level": record.levelname.lower(),
            "timestamp": self.formatTime(record, "%Y-%m-%d %H:%M:%S"),
            "name": record.name
        }
        if record.exc_info:
            log_record["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_record, ensure_ascii=False)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(JsonFormatter())
logger.addHandler(handler)
logging.getLogger().handlers.clear()

# ======== НАСТРОЙКИ ========
TOKEN = "1163348874:AAFgZEXveILvD4MbhQ8jiLTwIxs4puYhmq0"
INPUT_CHANNEL_ID = -1003469691743
OUTPUT_CHANNEL_ID = -1003842401391

LOCK_FILE = f'/tmp/hybrid_bot_{TOKEN[-10:]}.lock'

# ======== ХРАНИЛИЩЕ ========
storage = None  # Будет инициализировано позже

# ======== MASTER СТРАТЕГИЯ ========
class MasterStrategy:
    """Твоя профессиональная стратегия с картинками и цифрами"""
    
    def __init__(self):
        self.picture_values = {'K', 'Q', 'J'}
        self.number_values = {'A', '2', '3', '4', '5', '6', '7', '8', '9', '10'}
        self.color_map = {
            '♥️': {'same': '♦️', 'opposite': '♠️', 'direct': '♦️'},
            '♦️': {'same': '♥️', 'opposite': '♣️', 'direct': '♥️'},
            '♠️': {'same': '♣️', 'opposite': '♥️', 'direct': '♣️'},
            '♣️': {'same': '♠️', 'opposite': '♦️', 'direct': '♠️'}
        }
        self.active_signals = []
        self.stats = {'total': 0, 'success': 0, 'failures': []}
        self.signal_counter = 0
        self.ml = None  # Будет связан с ML анализатором
        
    def set_ml(self, ml_instance):
        """Привязывает ML анализатор"""
        self.ml = ml_instance
        
    def check_banker_combo(self, banker_cards):
        """Проверяет комбинацию банкира: картинка + цифра"""
        if len(banker_cards) != 2:
            return False, None
        
        card1, card2 = banker_cards
        
        # Проверяем что одна карта - картинка, другая - цифра
        if (card1['value'] in self.picture_values and card2['value'] in self.number_values):
            return True, card2['suit']  # Берем масть от цифры
        elif (card2['value'] in self.picture_values and card1['value'] in self.number_values):
            return True, card1['suit']  # Берем масть от цифры
        else:
            return False, None
    
    def get_dogon_plan(self, original_suit, attempt, delay_mode=False):
        """Возвращает план догона"""
        targets = self.color_map.get(original_suit, {})
        
        # Интервалы: +2/+3 для входа, +4/+5 для затяжки
        if delay_mode:
            intervals = [4, 5, 5]
        else:
            intervals = [2, 3, 4]
        
        if attempt >= len(intervals):
            return None
        
        # Чередуем замены
        if attempt == 0:
            new_suit = targets.get('same', original_suit)
            strategy = 'цвет'
        elif attempt == 1:
            new_suit = targets.get('opposite', original_suit)
            strategy = 'против'
        else:
            new_suit = targets.get('direct', original_suit)
            strategy = 'прямая'
        
        return {
            'suit': new_suit,
            'interval': intervals[attempt],
            'strategy': strategy,
            'attempt': attempt + 1
        }
    
    def check_signal(self, game_data, ml_stats=None):
        """Проверяет условия для входа"""
        banker_cards = game_data.get('banker_cards', [])
        is_valid, suit = self.check_banker_combo(banker_cards)
        
        if not is_valid:
            return None
        
        self.signal_counter += 1
        signal_id = self.signal_counter
        
        # Базовый интервал +2
        interval = 2
        confidence = 85  # Базовая уверенность
        
        # Если есть ML статистика - корректируем
        if ml_stats and suit in ml_stats:
            stats = ml_stats[suit]
            if stats['total'] > 10:
                # Корректируем интервал на основе ML
                interval = stats.get('best_interval', 2)
                confidence = int(stats['success_rate'] * 100)
        
        target_game = game_data['game_num'] + interval
        
        # Формируем план догона
        dogon_plans = []
        for i in range(3):
            plan = self.get_dogon_plan(suit, i, delay_mode=(interval > 3))
            if plan:
                dogon_plans.append(plan)
        
        signal = {
            'id': signal_id,
            'source_game': game_data['game_num'],
            'target_game': target_game,
            'suit': suit,
            'interval': interval,
            'confidence': confidence,
            'dogon_plans': dogon_plans,
            'status': 'pending',
            'attempt': 0,
            'timestamp': datetime.now(pytz.timezone('Europe/Moscow'))
        }
        
        self.active_signals.append(signal)
        return signal
    
    def check_predictions(self, current_game_num, game_data):
        """Проверяет активные сигналы"""
        results = []
        
        for signal in self.active_signals:
            if signal['status'] != 'pending':
                continue
            
            # Проверяем все игры от target_game до current_game_num
            if signal['target_game'] > current_game_num:
                continue
            
            succeeded = False
            actual_game = None
            
            for game_num in range(signal['target_game'], current_game_num + 1):
                game = storage.games.get(game_num)
                if not game:
                    continue
                
                player_suits = [c['suit'] for c in game.get('player_cards', [])]
                
                if signal['suit'] in player_suits:
                    succeeded = True
                    actual_game = game_num
                    break
            
            if succeeded:
                signal['status'] = 'win'
                signal['actual_game'] = actual_game
                self.stats['total'] += 1
                self.stats['success'] += 1
                results.append(('win', signal))
                
                # Обновляем ML статистику
                if self.ml:
                    self.ml.update_stats(
                        signal['suit'],
                        signal['interval'],
                        None,
                        True
                    )
            else:
                if signal['attempt'] < len(signal['dogon_plans']):
                    plan = signal['dogon_plans'][signal['attempt']]
                    signal['attempt'] += 1
                    signal['target_game'] = current_game_num + plan['interval']
                    signal['suit'] = plan['suit']
                    signal['status'] = 'pending'
                    results.append(('dogon', signal, plan))
                else:
                    signal['status'] = 'loss'
                    self.stats['total'] += 1
                    self.stats['failures'].append({
                        'game': current_game_num,
                        'signal': signal['id']
                    })
                    results.append(('loss', signal))
                    
                    # Обновляем ML статистику
                    if self.ml:
                        self.ml.update_stats(
                            signal['suit'],
                            signal['interval'],
                            None,
                            False
                        )
        
        return results

# ======== ML АНАЛИЗАТОР ========
class MLAnalyzer:
    """Интеллектуальный анализ на основе истории"""
    
    def __init__(self):
        self.history = deque(maxlen=1000)
        self.suit_stats = {
            '♥️': {'total': 0, 'success': 0, 'intervals': defaultdict(int), 'replacements': defaultdict(int)},
            '♦️': {'total': 0, 'success': 0, 'intervals': defaultdict(int), 'replacements': defaultdict(int)},
            '♠️': {'total': 0, 'success': 0, 'intervals': defaultdict(int), 'replacements': defaultdict(int)},
            '♣️': {'total': 0, 'success': 0, 'intervals': defaultdict(int), 'replacements': defaultdict(int)}
        }
        
    def add_game(self, game_data, signal_result=None):
        """Добавляет игру в историю"""
        self.history.append({
            'game_num': game_data['game_num'],
            'player_suits': [c['suit'] for c in game_data.get('player_cards', [])],
            'banker_cards': game_data.get('banker_cards', []),
            'winner': game_data.get('winner'),
            'timestamp': game_data.get('timestamp'),
            'signal': signal_result
        })
    
    def analyze_suit(self, suit, games=500):
        """Анализирует статистику по масти"""
        recent = list(self.history)[-games:]
        if not recent:
            return {'total': 0, 'success_rate': 0, 'best_interval': 2}
        
        stats = self.suit_stats[suit]
        
        # Анализируем успешность
        total = stats['total']
        success = stats['success']
        success_rate = success / max(1, total)
        
        # Находим лучший интервал
        best_interval = 2
        max_success = 0
        for interval, count in stats['intervals'].items():
            if count > max_success:
                max_success = count
                best_interval = interval
        
        # Анализируем замены
        best_replacement = 'same'
        max_replacement = 0
        for repl, count in stats['replacements'].items():
            if count > max_replacement:
                max_replacement = count
                best_replacement = repl
        
        return {
            'total': total,
            'success': success,
            'success_rate': success_rate,
            'best_interval': best_interval,
            'best_replacement': best_replacement
        }
    
    def update_stats(self, suit, interval, replacement, succeeded):
        """Обновляет статистику по результату"""
        stats = self.suit_stats[suit]
        stats['total'] += 1
        stats['intervals'][interval] += 1
        
        if replacement:
            stats['replacements'][replacement] += 1
        
        if succeeded:
            stats['success'] += 1

# ======== ГИБРИДНЫЙ ПРЕДИКТОР ========
class HybridPredictor:
    def __init__(self):
        self.master = MasterStrategy()
        self.ml = MLAnalyzer()
        self.master.set_ml(self.ml)  # Связываем стратегию с ML
        self.last_prediction_time = None
        self.min_time_between = 120  # 2 минуты
        
    async def analyze_and_predict(self, game_data, context):
        current_time = datetime.now(pytz.timezone('Europe/Moscow'))
        
        # Сначала ПРОВЕРЯЕМ РЕЗУЛЬТАТЫ (это важно!)
        results = self.master.check_predictions(game_data['game_num'], game_data)
        
        for result in results:
            if result[0] == 'win':
                await self._send_result(result[1], 'win', context)
            elif result[0] == 'loss':
                await self._send_result(result[1], 'loss', context)
            elif result[0] == 'dogon':
                await self._send_dogon(result[1], result[2], context)
        
        # Проверяем таймер для новых сигналов
        if self.last_prediction_time:
            time_diff = (current_time - self.last_prediction_time).seconds
            if time_diff < self.min_time_between:
                logger.info(f"⏳ Таймер: {time_diff}с, нужно {self.min_time_between}с")
                # Добавляем игру в историю, но новый сигнал не даем
                self.ml.add_game(game_data)
                return
        
        # Получаем ML статистику для текущей масти
        ml_stats = {}
        for suit in ['♥️', '♦️', '♠️', '♣️']:
            ml_stats[suit] = self.ml.analyze_suit(suit)
        
        # Проверяем сигнал от MASTER стратегии
        signal = self.master.check_signal(game_data, ml_stats)
        
        if signal:
            self.last_prediction_time = current_time
            
            # Формируем сообщение
            message = self._format_signal(signal, ml_stats)
            
            try:
                await context.bot.send_message(
                    chat_id=OUTPUT_CHANNEL_ID,
                    text=message,
                    parse_mode='Markdown'
                )
                logger.info(f"⚜️ MASTER сигнал #{signal['id']} на игру #{signal['target_game']}")
            except Exception as e:
                logger.error(f"❌ Ошибка отправки: {e}")
        
        # Добавляем игру в ML историю
        self.ml.add_game(game_data)
    
    def _format_signal(self, signal, ml_stats):
        """Форматирует сигнал для вывода"""
        suit = signal['suit']
        ml = ml_stats.get(suit, {})
        
        # Формируем строку догонов
        dogon_lines = []
        for i, plan in enumerate(signal['dogon_plans']):
            strategy = f"({plan['strategy']})"
            dogon_lines.append(f"  • #{signal['target_game'] + plan['interval']} (+{plan['interval']}) — {plan['suit']} {strategy}")
        
        dogon_text = "\n".join(dogon_lines)
        
        # Формируем ML анализ
        ml_text = ""
        if ml['total'] > 10:
            ml_text = (
                f"\n🧠 *ML АНАЛИЗ* (на основе {ml['total']} игр):\n"
                f"• Интервал: +{ml['best_interval']} (оптимально, успешность {int(ml['success_rate']*100)}%)\n"
            )
        
        return (
            f"⚜️ *MASTER-ML СИГНАЛ #{signal['id']}* ⚜️\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🎯 *Вход:* #{signal['target_game']} (+{signal['interval']}) — {suit}\n"
            f"{ml_text}\n"
            f"🔄 *Догон:* \n{dogon_text}\n\n"
            f"📈 *Уверенность:* {signal['confidence']}%\n"
            f"⏱ От игры #{signal['source_game']}"
        )
    
    async def _send_result(self, signal, result, context):
        """Отправляет результат"""
        emoji = "✅" if result == 'win' else "❌"
        status = "ЗАШЁЛ" if result == 'win' else "НЕ ЗАШЁЛ"
        
        # Считаем проценты
        total = self.master.stats['total']
        success = self.master.stats['success']
        percent = int(success / max(1, total) * 100) if total > 0 else 0
        
        text = (
            f"{emoji} *MASTER СИГНАЛ #{signal['id']} {status}!*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📊 *ИСТОЧНИК:* #{signal['source_game']}\n"
            f"🎯 *ЦЕЛЬ:* #{signal['target_game']}\n"
            f"🃏 *МАСТЬ:* {signal['suit']}\n"
            f"🎲 *НАЙДЕНО В ИГРЕ:* #{signal.get('actual_game', '?')}\n\n"
            f"📊 *СТАТИСТИКА MASTER:*\n"
            f"• Всего: {total}\n"
            f"• Успешно: {success}\n"
            f"• Процент: {percent}%\n"
            f"⏱ {datetime.now(pytz.timezone('Europe/Moscow')).strftime('%H:%M')} МСК"
        )
        
        try:
            await context.bot.send_message(
                chat_id=OUTPUT_CHANNEL_ID,
                text=text,
                parse_mode='Markdown'
            )
        except:
            pass
    
    async def _send_dogon(self, signal, plan, context):
        """Отправляет догон"""
        text = (
            f"🔄 *MASTER ДОГОН #{signal['id']} — ПОПЫТКА {signal['attempt']}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📊 *ИСТОЧНИК:* #{signal['source_game']}\n"
            f"🎯 *ЦЕЛЬ:* #{signal['target_game']}\n"
            f"🃏 *МАСТЬ:* {signal['suit']} ({plan['strategy']})\n"
            f"📈 *ИНТЕРВАЛ:* +{plan['interval']}\n"
            f"⏱ {datetime.now(pytz.timezone('Europe/Moscow')).strftime('%H:%M')} МСК"
        )
        
        try:
            await context.bot.send_message(
                chat_id=OUTPUT_CHANNEL_ID,
                text=text,
                parse_mode='Markdown'
            )
        except:
            pass

# ======== ХРАНИЛИЩЕ ========
class GameStorage:
    def __init__(self):
        self.games = {}
        self.hybrid = HybridPredictor()

storage = GameStorage()
lock_fd = None

class PendingGame:
    def __init__(self, game_data, first_seen):
        self.game_data = game_data
        self.first_seen = first_seen

pending_games = {}

def acquire_lock():
    global lock_fd
    try:
        lock_fd = open(LOCK_FILE, 'w')
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        logger.info(f"🔒 Блокировка: {LOCK_FILE}")
        return True
    except:
        logger.error("❌ Бот уже запущен")
        return False

def release_lock():
    global lock_fd
    if lock_fd:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()
            if os.path.exists(LOCK_FILE):
                os.unlink(LOCK_FILE)
        except:
            pass

def check_bot_token():
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/getMe"
        req = urllib.request.Request(url, method='GET')
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            if data.get('ok'):
                logger.info(f"✅ Бот @{data['result']['username']} авторизован")
                return True
    except:
        pass
    logger.error("❌ Ошибка авторизации")
    return False

def normalize_suit(s):
    if not s:
        return None
    s = str(s).strip()
    clean_s = ''.join(c for c in s if ord(c) not in [65038, 65039])
    
    if clean_s in ('♥', '❤', '♡'):
        return '♥️'
    if clean_s in ('♠', '♤'):
        return '♠️'
    if clean_s in ('♣', '♧'):
        return '♣️'
    if clean_s in ('♦', '♢'):
        return '♦️'
    
    return None

def extract_suits(text):
    suits = []
    for ch in text:
        norm = normalize_suit(ch)
        if norm:
            suits.append(norm)
    return suits

def extract_left_part(text):
    separators = [' 👈 ', '👈', ' - ', ' – ', '—', '-', '👉👈', '👈👉', '🔰']
    for sep in separators:
        if sep in text:
            parts = text.split(sep, 1)
            left = re.sub(r'#N\d+\.?\s*', '', parts[0].strip())
            return left
    return text.strip()

def parse_game_data(text):
    text = re.sub(r'([♥️♦️♠️♣️])\1+', r'\1', text)
    
    match = re.search(r'#N(\d+)', text)
    if not match:
        return None
    
    game_num = int(match.group(1))
    
    has_r_tag = '#R' in text
    has_x_tag = '#X' in text or '#X🟡' in text
    has_check = '✅' in text
    has_green_square = '🟩' in text
    is_tie = '🔰' in text
    
    is_complete = has_check or has_green_square or is_tie
    
    player_draws = '👈' in text
    banker_draws = '👉' in text
    
    left_part = extract_left_part(text)
    left_part = re.sub(r'([♥️♦️♠️♣️])\1+', r'\1', left_part)
    
    left_suits = extract_suits(left_part)
    
    if not left_suits:
        return None
    
    first_suit = left_suits[0] if len(left_suits) > 0 else None
    second_suit = left_suits[1] if len(left_suits) > 1 else None
    
    player_cards = []
    banker_cards = []
    
    card_pattern = r'(\d+|A|J|Q|K)\s*([♥️♦️♠️♣️])'
    
    for match in re.finditer(card_pattern, left_part):
        value, suit = match.groups()
        suit = normalize_suit(suit)
        if suit and value:
            player_cards.append({'value': value, 'suit': suit})
    
    separators = [' 👈 ', '👈', ' - ', ' – ', '—', '-', '👉👈', '👈👉']
    right_part = ""
    for sep in separators:
        if sep in text:
            right_part = text.split(sep, 1)[1]
            break
    
    right_part = re.sub(r'([♥️♦️♠️♣️])\1+', r'\1', right_part)
    
    for match in re.finditer(card_pattern, right_part):
        value, suit = match.groups()
        suit = normalize_suit(suit)
        if suit and value:
            banker_cards.append({'value': value, 'suit': suit})
    
    if len(player_cards) > 3:
        player_cards = player_cards[:3]
    
    if len(banker_cards) > 3:
        banker_cards = banker_cards[:3]
    
    winner = None
    if '✅' in text:
        winner = 'banker'
    elif '🔰' in text:
        winner = 'tie'
    else:
        winner = 'player'
    
    total_match = re.search(r'#T(\d+)', text)
    total_sum = int(total_match.group(1)) if total_match else 0
    
    player_score = 0
    banker_score = 0
    
    score_match = re.search(r'(\d+)\s*\(', left_part)
    if score_match:
        player_score = int(score_match.group(1))
    
    score_match = re.search(r'(\d+)\s*\(', right_part)
    if score_match:
        banker_score = int(score_match.group(1))
    
    return {
        'game_num': game_num,
        'first_suit': first_suit,
        'second_suit': second_suit,
        'all_suits': left_suits,
        'has_r_tag': has_r_tag,
        'has_x_tag': has_x_tag,
        'has_check': has_check,
        'has_green_square': has_green_square,
        'player_draws': player_draws,
        'banker_draws': banker_draws,
        'is_complete': is_complete,
        'is_tie': is_tie,
        'player_cards': player_cards,
        'banker_cards': banker_cards,
        'player_score': player_score,
        'banker_score': banker_score,
        'winner': winner,
        'total_sum': total_sum,
        'timestamp': datetime.now(pytz.timezone('Europe/Moscow'))
    }

async def handle_new_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        message = None
        is_edit = False
        
        if update.channel_post:
            message = update.channel_post
            is_edit = False
        elif update.edited_channel_post:
            message = update.edited_channel_post
            is_edit = True
        else:
            return
        
        text = message.text
        if not text:
            return
        
        logger.info(f"\n{'='*60}")
        logger.info(f"📥 {'РЕДАКТИРОВАНИЕ' if is_edit else 'НОВОЕ'}: {text[:150]}...")
        
        game_data = parse_game_data(text)
        if not game_data:
            return
        
        game_num = game_data['game_num']
        
        logger.info(f"📊 Игра #{game_num}")
        
        player_cards_str = []
        for c in game_data['player_cards']:
            player_cards_str.append(f"{c['value']}{c['suit']}")
        logger.info(f"   Карты игрока: {player_cards_str}")
        
        banker_cards_str = []
        for c in game_data['banker_cards']:
            banker_cards_str.append(f"{c['value']}{c['suit']}")
        logger.info(f"   Карты банкира: {banker_cards_str}")
        
        logger.info(f"   Теги: R={game_data['has_r_tag']}, X={game_data['has_x_tag']}")
        logger.info(f"   Добор: игрок {'👈' if game_data['player_draws'] else 'нет'}, банкир {'👉' if game_data['banker_draws'] else 'нет'}")
        logger.info(f"   Завершена: {game_data['is_complete']}")
        
        if is_edit:
            logger.info(f"✏️ Редактирование игры #{game_num}")
            storage.games[game_num] = game_data
            await storage.hybrid.analyze_and_predict(game_data, context)
            
            if game_num in pending_games:
                del pending_games[game_num]
            
            return
        
        if game_data['player_draws'] or game_data['banker_draws']:
            logger.info(f"⏳ Игра #{game_num}: ожидание третьей карты")
            pending_games[game_num] = PendingGame(game_data, datetime.now())
            storage.games[game_num] = game_data
            await storage.hybrid.analyze_and_predict(game_data, context)
            return
        
        if not game_data['player_draws'] and not game_data['banker_draws']:
            if game_num in pending_games:
                logger.info(f"✅ Игра #{game_num}: получена полная версия")
                del pending_games[game_num]
            else:
                logger.info(f"✅ Игра #{game_num}: полная версия сразу")
            
            storage.games[game_num] = game_data
            
            if game_data['is_complete']:
                logger.info(f"🔍 Игра #{game_num} завершена, проверяем прогнозы")
            
            await storage.hybrid.analyze_and_predict(game_data, context)
        
        current_time = datetime.now()
        for pending_num in list(pending_games.keys()):
            if pending_num < game_num - 20:
                logger.info(f"🧹 Очистка ожидания игры #{pending_num}")
                del pending_games[pending_num]
        
        if len(storage.games) > 200:
            oldest = min(storage.games.keys())
            del storage.games[oldest]
        
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")

async def error_handler(update, context):
    try:
        if isinstance(context.error, Conflict):
            logger.warning("⚠️ Конфликт, выходим")
            release_lock()
            sys.exit(1)
    except:
        pass

async def check_stuck_games(context: ContextTypes.DEFAULT_TYPE):
    current_time = datetime.now()
    for game_num, pending in list(pending_games.items()):
        if (current_time - pending.first_seen).seconds > 120:
            logger.info(f"⏰ Игра #{game_num} зависла в ожидании >2 мин, проверяем")
            
            if game_num in storage.games:
                await storage.hybrid.analyze_and_predict(storage.games[game_num], context)
            
            del pending_games[game_num]

def main():
    print("\n" + "="*60)
    print("🤖 ГИБРИДНЫЙ MASTER-ML БОТ")
    print("="*60)
    print("⚜️ MASTER-СТРАТЕГИЯ: картинка+цифра, догоны по цвету")
    print("🧠 ML АНАЛИЗ: оптимизация на основе истории")
    print("✅ ПРОВЕРКА РЕЗУЛЬТАТОВ: после каждой игры")
    print("✅ Таймер 2 минуты между сигналами")
    print("🎯 Догоны 3 шага с интервалами +2/+3/+4")
    print("="*60)
    
    if not acquire_lock():
        sys.exit(1)
    
    if not check_bot_token():
        release_lock()
        sys.exit(1)
    
    app = Application.builder().token(TOKEN).build()
    
    app.add_error_handler(error_handler)
    app.add_handler(MessageHandler(
        filters.Chat(INPUT_CHANNEL_ID) & filters.TEXT,
        handle_new_game
    ))
    
    if app.job_queue:
        app.job_queue.run_repeating(check_stuck_games, interval=30, first=10)
        logger.info("✅ Планировщик запущен")
    else:
        logger.error("❌ JobQueue не доступен")
    
    try:
        app.run_polling(
            allowed_updates=['channel_post', 'edited_channel_post'],
            drop_pending_updates=True
        )
    finally:
        release_lock()

if __name__ == "__main__":
    import signal
    def signal_handler(sig, frame):
        logger.info("👋 Бот останавливается...")
        release_lock()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    main()