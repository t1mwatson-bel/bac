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

LOCK_FILE = f'/tmp/master_bot_{TOKEN[-10:]}.lock'

# ======== АДАПТИВНЫЕ ВЕСА ========
class AdaptiveWeights:
    def __init__(self):
        self.weights = {
            'banker_combo': 1.0,
            'doubtful': -2.0,
            'history_trend': 0.5,
            'time_pattern': 0.3,
            'suit_distance': 0.4,
            'r_tag_nearby': -1.0
        }
        self.performance = defaultdict(list)
        self.total_signals = 0
        self.successful = 0
    
    def update(self, condition, success):
        if condition not in self.weights:
            return
        self.performance[condition].append(1 if success else 0)
        if len(self.performance[condition]) >= 10:
            recent = self.performance[condition][-10:]
            rate = sum(recent) / len(recent)
            if rate > 0.7:
                self.weights[condition] *= 1.05
            elif rate < 0.3:
                self.weights[condition] *= 0.95
    
    def get_score(self, conditions):
        score = 0.0
        details = []
        for cond, met in conditions.items():
            if met and cond in self.weights:
                score += self.weights[cond]
                details.append(f"{cond}: {self.weights[cond]:+.1f}")
        return round(score, 1), details
    
    def get_stats(self):
        return {
            'total': self.total_signals,
            'success': self.successful,
            'percent': int(self.successful / max(1, self.total_signals) * 100),
            'weights': self.weights.copy()
        }

# ======== РЕЖИМ ОСТОРОЖНОСТИ ========
class CautiousMode:
    def __init__(self):
        self.losses = 0
        self.cooldown = 0
        self.max_losses = 3
        self.cooldown_games = 5
        self.history = deque(maxlen=10)
    
    def update(self, result, game_num):
        self.history.append({'game': game_num, 'result': result})
        if result == 'loss':
            self.losses += 1
            if self.losses >= self.max_losses:
                self.cooldown = self.cooldown_games
                logger.info(f"🛑 Режим осторожности: {self.cooldown} игр")
        else:
            self.losses = 0
        if self.cooldown > 0:
            self.cooldown -= 1
    
    def can_play(self):
        if self.cooldown > 0:
            return False, f"осторожность {self.cooldown}"
        if self.losses >= self.max_losses - 1:
            return False, f"предупреждение {self.losses + 1}/3"
        return True, "ok"
    
    def get_status(self):
        return {
            'losses': self.losses,
            'cooldown': self.cooldown,
            'active': self.cooldown > 0
        }

# ======== MASTER СТРАТЕГИЯ ========
class MasterStrategy:
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
        self.completed_signals = []
        self.weights = AdaptiveWeights()
        self.cautious = CautiousMode()
        self.last_prediction_time = None
        self.min_time_between = 120
        self.signal_counter = 0
        self.suit_history = defaultdict(list)
        self.hourly_stats = defaultdict(lambda: {'total': 0, 'wins': 0})
        
    def analyze_time(self):
        hour = datetime.now(pytz.timezone('Europe/Moscow')).hour
        stats = self.hourly_stats[hour]
        if stats['total'] >= 5:
            rate = stats['wins'] / stats['total'] * 100
            if rate < 40:
                return True, f"{hour}:00 ({rate:.0f}%)"
        return False, ""
    
    def analyze_suit_trend(self, window=20):
        games = list(storage.games.values())[-window:]
        counts = defaultdict(int)
        total = 0
        for game in games:
            # Считаем только карты игрока (слева)
            for card in game.get('player_cards', []):
                counts[card['suit']] += 1
                total += 1
        if total == 0:
            return {}
        percents = {s: round(c/total*100) for s, c in counts.items()}
        hot = [s for s, p in percents.items() if p > 30]
        cold = [s for s, p in percents.items() if p < 15]
        return {'percents': percents, 'hot': hot, 'cold': cold}
    
    def analyze_distance(self, suit):
        if suit not in self.suit_history or len(self.suit_history[suit]) < 3:
            return None
        gaps = []
        sorted_games = sorted(self.suit_history[suit])
        for i in range(1, len(sorted_games)):
            gaps.append(sorted_games[i] - sorted_games[i-1])
        return sum(gaps) / len(gaps)
    
    def is_doubtful(self, game_data):
        banker = game_data.get('banker_cards', [])
        reasons = []
        
        if len(banker) >= 2 and banker[0]['suit'] == banker[1]['suit']:
            reasons.append(f"две {banker[0]['suit']}")
        if len(banker) == 3:
            if banker[1]['suit'] == banker[2]['suit']:
                reasons.append(f"вторая=третья {banker[1]['suit']}")
            if banker[0]['suit'] == banker[1]['suit'] == banker[2]['suit']:
                reasons.append(f"все три {banker[0]['suit']}")
        
        if reasons:
            return True, "; ".join(reasons)
        return False, ""
    
    def check_banker_combo(self, cards):
        if len(cards) != 2:
            return False, None
        c1, c2 = cards
        if c1['suit'] == c2['suit']:
            return False, None
        if (c1['value'] in self.picture_values and c2['value'] in self.number_values):
            return True, c2['suit']
        if (c2['value'] in self.picture_values and c1['value'] in self.number_values):
            return True, c1['suit']
        return False, None
    
    def get_dogon_plan(self, suit, attempt):
        targets = self.color_map.get(suit, {})
        intervals = [2, 3, 4]
        if attempt >= len(intervals):
            return None
        if attempt == 0:
            return {'suit': targets.get('same', suit), 'interval': 2, 'strategy': 'цвет'}
        elif attempt == 1:
            return {'suit': targets.get('opposite', suit), 'interval': 3, 'strategy': 'против'}
        else:
            return {'suit': targets.get('direct', suit), 'interval': 4, 'strategy': 'прямая'}
    
    def check_signal(self, game_data):
        # Проверка режима
        can, reason = self.cautious.can_play()
        if not can:
            logger.info(f"🛑 Режим: {reason}")
            return None
        
        # Проверка времени
        skip_time, time_reason = self.analyze_time()
        if skip_time:
            logger.info(f"⏰ Пропуск по времени: {time_reason}")
            return None
        
        # Проверка комбинации
        valid, suit = self.check_banker_combo(game_data.get('banker_cards', []))
        if not valid:
            return None
        
        # Проверка таймера
        now = datetime.now(pytz.timezone('Europe/Moscow'))
        if self.last_prediction_time:
            diff = (now - self.last_prediction_time).seconds
            if diff < self.min_time_between:
                logger.info(f"⏳ Таймер: {diff}/{self.min_time_between}")
                return None
        
        # Оценка ситуации
        doubtful, doubt_reason = self.is_doubtful(game_data)
        conditions = {
            'banker_combo': True,
            'doubtful': doubtful,
            'history_trend': len(storage.games) > 10,
            'time_pattern': not skip_time,
            'suit_distance': self.analyze_distance(suit) is not None,
            'r_tag_nearby': False
        }
        
        score, details = self.weights.get_score(conditions)
        
        if score < 0:
            logger.info(f"⏭️ Низкий score: {score}")
            return None
        
        # Создаем сигнал
        self.signal_counter += 1
        signal_id = self.signal_counter
        
        dogon_plans = []
        for i in range(3):
            plan = self.get_dogon_plan(suit, i)
            if plan:
                dogon_plans.append(plan)
        
        # Анализ тренда
        trend = self.analyze_suit_trend()
        
        signal = {
            'id': signal_id,
            'source_game': game_data['game_num'],
            'target_game': game_data['game_num'] + 2,
            'suit': suit,
            'dogon_plans': dogon_plans,
            'status': 'pending',
            'attempt': 0,
            'timestamp': now,
            'msg_id': None,
            'score': score,
            'conditions': conditions,
            'doubtful': doubtful,
            'doubt_reason': doubt_reason if doubtful else '',
            'trend': trend.get('percents', {}).get(suit, 0),
            'hot': suit in trend.get('hot', []),
            'cold': suit in trend.get('cold', [])
        }
        
        self.active_signals.append(signal)
        self.last_prediction_time = now
        self.weights.total_signals += 1
        
        logger.info(f"⚜️ Сигнал #{signal_id} на {suit} score={score}")
        return signal
    
    def check_predictions(self, game_num, game_data):
        results = []
        # Берем ТОЛЬКО карты игрока (слева)
        player_suits = [c['suit'] for c in game_data.get('player_cards', [])]
        
        for signal in self.active_signals:
            if signal['status'] != 'pending':
                continue
            if signal['target_game'] > game_num:
                continue
            
            # Проверяем заход ТОЛЬКО по картам игрока
            succeeded = signal['suit'] in player_suits
            actual_game = game_num if succeeded else None
            
            if succeeded:
                signal['status'] = 'win'
                signal['actual_game'] = actual_game
                self.completed_signals.append(signal)
                self.weights.successful += 1
                self.cautious.update('win', game_num)
                
                # Обновляем историю мастей
                for suit in player_suits:
                    self.suit_history[suit].append(game_num)
                
                # Обновляем веса
                for cond in signal['conditions']:
                    self.weights.update(cond, True)
                
                # Обновляем статистику по часам
                hour = signal['timestamp'].hour
                self.hourly_stats[hour]['total'] += 1
                self.hourly_stats[hour]['wins'] += 1
                
                results.append(('win', signal))
                logger.info(f"✅ Сигнал #{signal['id']} зашел в игре #{game_num} (игрок: {player_suits})")
                
            elif signal['attempt'] < len(signal['dogon_plans']):
                plan = signal['dogon_plans'][signal['attempt']]
                signal['attempt'] += 1
                signal['target_game'] = game_num + plan['interval']
                signal['suit'] = plan['suit']
                results.append(('dogon', signal, plan))
                logger.info(f"🔄 Сигнал #{signal['id']} догон {signal['attempt']} на игру #{signal['target_game']}")
                
            else:
                signal['status'] = 'loss'
                signal['actual_game'] = None
                self.completed_signals.append(signal)
                self.cautious.update('loss', game_num)
                
                for cond in signal['conditions']:
                    self.weights.update(cond, False)
                
                hour = signal['timestamp'].hour
                self.hourly_stats[hour]['total'] += 1
                
                results.append(('loss', signal))
                logger.info(f"❌ Сигнал #{signal['id']} не зашел (игрок: {player_suits})")
        
        # Очистка старых сигналов
        self.active_signals = [s for s in self.active_signals if s['status'] == 'pending']
        
        return results
    
    def format_signal(self, signal):
        dogon_lines = []
        for i, p in enumerate(signal['dogon_plans']):
            arrow = "→" if i < signal['attempt'] else "•"
            status = " (текущий)" if i == signal['attempt'] - 1 and signal['attempt'] > 0 else ""
            dogon_lines.append(f"  {arrow} #{signal['source_game'] + p['interval']} (+{p['interval']}) — {p['suit']} ({p['strategy']}){status}")
        
        trend_info = ""
        if signal['hot']:
            trend_info = "🔥 горячая"
        elif signal['cold']:
            trend_info = "❄️ холодная"
        
        doubt_info = f"\n⚠️ Сомнительно: {signal['doubt_reason']}" if signal['doubtful'] else ""
        
        return (
            f"⚜️ MASTER СИГНАЛ #{signal['id']} ⚜️\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🎯 ВХОД: #{signal['target_game']} (+2) — {signal['suit']} {trend_info}\n\n"
            f"🔄 ДОГОНЫ:\n{dogon_lines}\n"
            f"{doubt_info}\n"
            f"📊 АНАЛИЗ:\n"
            f"• Score: {signal['score']}\n"
            f"• Частота {signal['suit']}: {signal['trend']}%\n"
            f"⏱ От игры #{signal['source_game']}"
        )
    
    def format_status(self, signal):
        dogon_lines = []
        for i, p in enumerate(signal['dogon_plans']):
            if i < signal['attempt']:
                dogon_lines.append(f"  ✓ #{signal['source_game'] + p['interval']} (+{p['interval']}) — {p['suit']} (было)")
            else:
                dogon_lines.append(f"  • #{signal['source_game'] + p['interval']} (+{p['interval']}) — {p['suit']} ({p['strategy']})")
        
        return (
            f"⚜️ MASTER СИГНАЛ #{signal['id']} ⚜️\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📊 СТАТУС: Догон {signal['attempt']}/3\n\n"
            f"🎯 ЦЕЛЬ: #{signal['target_game']} (+{signal['dogon_plans'][signal['attempt']-1]['interval']})\n"
            f"🃏 МАСТЬ: {signal['suit']}\n\n"
            f"🔄 ОСТАЛОСЬ:\n{dogon_lines}\n\n"
            f"⏱ От игры #{signal['source_game']}"
        )
    
    def format_result(self, signal):
        emoji = "✅" if signal['status'] == 'win' else "❌"
        status = "ЗАШЁЛ" if signal['status'] == 'win' else "НЕ ЗАШЁЛ"
        
        stats = self.weights.get_stats()
        cautious = self.cautious.get_status()
        
        # Получаем карты игрока из целевой игры
        target_game = storage.games.get(signal['target_game'], {})
        player_cards = target_game.get('player_cards', [])
        player_suits = [c['suit'] for c in player_cards]
        
        text = (
            f"{emoji} MASTER СИГНАЛ #{signal['id']} {status}!\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🎯 БЫЛО: #{signal['target_game']} — {signal['suit']}\n"
        )
        
        if signal['status'] == 'win':
            text += f"✅ НАЙДЕН В ИГРЕ: #{signal['actual_game']} (игрок: {', '.join(player_suits)})\n\n"
        else:
            text += f"📊 ВЫПАЛО У ИГРОКА: {', '.join(player_suits) if player_suits else 'нет карт'}\n\n"
        
        if signal['doubtful'] and signal['status'] == 'loss':
            text += f"🧠 АНАЛИЗ ОШИБКИ:\n"
            text += f"• {signal['doubt_reason']}\n"
            if signal['cold']:
                text += f"• Масть была холодной ({signal['trend']}%)\n"
            text += f"\n"
        
        text += (
            f"📊 СТАТИСТИКА:\n"
            f"• Всего сигналов: {stats['total']}\n"
            f"• Успешно: {stats['success']}\n"
            f"• Процент: {stats['percent']}%\n"
        )
        
        if cautious['active']:
            text += f"⚠️ РЕЖИМ: осторожность {cautious['cooldown']} игр\n"
        
        text += f"⏱ {datetime.now(pytz.timezone('Europe/Moscow')).strftime('%H:%M')} МСК"
        
        return text
    
    def format_stats(self):
        stats = self.weights.get_stats()
        cautious = self.cautious.get_status()
        trend = self.analyze_suit_trend(50)
        
        # Статистика по часам
        hour_lines = []
        for h in sorted(self.hourly_stats.keys()):
            s = self.hourly_stats[h]
            if s['total'] > 0:
                rate = s['wins'] / s['total'] * 100
                marker = "✅" if rate > 60 else "⚠️" if rate > 40 else "❌"
                hour_lines.append(f"  {marker} {h}:00 — {s['wins']}/{s['total']} ({rate:.0f}%)")
        
        # Горячие/холодные масти
        hot_line = f"🔥 {', '.join(trend.get('hot', []))}" if trend.get('hot') else "🔥 нет"
        cold_line = f"❄️ {', '.join(trend.get('cold', []))}" if trend.get('cold') else "❄️ нет"
        
        # Веса условий
        weight_lines = []
        for cond, w in sorted(stats['weights'].items(), key=lambda x: abs(x[1]), reverse=True)[:5]:
            emoji = "✅" if w > 0 else "⚠️" if w < 0 else "⚪"
            weight_lines.append(f"  {emoji} {cond}: {w:+.2f}")
        
        text = (
            f"📊 СТАТИСТИКА MASTER БОТА\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📈 ОБЩАЯ:\n"
            f"• Сигналов: {stats['total']}\n"
            f"• Успешно: {stats['success']}\n"
            f"• Процент: {stats['percent']}%\n\n"
            f"⚠️ РЕЖИМ:\n"
            f"• Поражений подряд: {cautious['losses']}\n"
            f"• Осторожность: {'вкл' if cautious['active'] else 'выкл'}\n\n"
            f"🔥 ТРЕНДЫ МАСТЕЙ:\n"
            f"{hot_line}\n"
            f"{cold_line}\n\n"
            f"🕐 ПО ЧАСАМ:\n"
            f"{chr(10).join(hour_lines[:5])}\n\n"
            f"🧠 ВЕСА УСЛОВИЙ:\n"
            f"{chr(10).join(weight_lines)}\n\n"
            f"⏱ {datetime.now(pytz.timezone('Europe/Moscow')).strftime('%H:%M')} МСК"
        )
        
        return text

# ======== ХРАНИЛИЩЕ ========
class GameStorage:
    def __init__(self):
        self.games = {}
        self.strategy = MasterStrategy()

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
    
    # Разделяем левую (игрок) и правую (банкир) части
    separators = [' 👈 ', '👈', ' - ', ' – ', '—', '-', '👉👈', '👈👉']
    left_part = text
    right_part = ""
    
    for sep in separators:
        if sep in text:
            parts = text.split(sep, 1)
            left_part = parts[0].strip()
            right_part = parts[1].strip() if len(parts) > 1 else ""
            break
    
    # Очищаем левую часть от #N
    left_part = re.sub(r'#N\d+\.?\s*', '', left_part)
    left_part = re.sub(r'[()]', '', left_part)  # убираем скобки
    
    # Парсим карты игрока (только слева)
    player_cards = []
    card_pattern = r'(\d+|A|J|Q|K)\s*([♥️♦️♠️♣️])'
    
    for match in re.finditer(card_pattern, left_part):
        value, suit = match.groups()
        suit = normalize_suit(suit)
        if suit and value:
            player_cards.append({'value': value, 'suit': suit})
    
    # Парсим карты банкира (справа)
    banker_cards = []
    right_part = re.sub(r'[()]', '', right_part)
    
    for match in re.finditer(card_pattern, right_part):
        value, suit = match.groups()
        suit = normalize_suit(suit)
        if suit and value:
            banker_cards.append({'value': value, 'suit': suit})
    
    # Ограничиваем до 3 карт
    if len(player_cards) > 3:
        player_cards = player_cards[:3]
    
    if len(banker_cards) > 3:
        banker_cards = banker_cards[:3]
    
    # Определяем победителя
    winner = None
    if '✅' in text:
        winner = 'banker'
    elif '🔰' in text:
        winner = 'tie'
    elif '🟩' in text:
        winner = 'player'
    
    total_match = re.search(r'#T(\d+)', text)
    total_sum = int(total_match.group(1)) if total_match else 0
    
    # Извлекаем очки
    player_score = 0
    banker_score = 0
    
    score_match = re.search(r'\((\d+)\)', left_part)
    if score_match:
        player_score = int(score_match.group(1))
    
    score_match = re.search(r'\((\d+)\)', right_part)
    if score_match:
        banker_score = int(score_match.group(1))
    
    # Первая масть игрока для анализа
    first_suit = player_cards[0]['suit'] if player_cards else None
    
    return {
        'game_num': game_num,
        'first_suit': first_suit,
        'all_suits': [c['suit'] for c in player_cards],
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
        
        # СОХРАНЯЕМ ИГРУ
        storage.games[game_num] = game_data
        
        # СНАЧАЛА ПРОВЕРЯЕМ РЕЗУЛЬТАТЫ
        if game_data['is_complete']:
            logger.info(f"🔍 Игра #{game_num} завершена, проверяем прогнозы")
            
            results = storage.strategy.check_predictions(game_num, game_data)
            
            for result in results:
                if result[0] in ['win', 'loss']:
                    signal = result[1]
                    if signal['msg_id']:
                        try:
                            await context.bot.edit_message_text(
                                chat_id=OUTPUT_CHANNEL_ID,
                                message_id=signal['msg_id'],
                                text=storage.strategy.format_result(signal),
                                parse_mode='Markdown'
                            )
                        except:
                            msg = await context.bot.send_message(
                                chat_id=OUTPUT_CHANNEL_ID,
                                text=storage.strategy.format_result(signal),
                                parse_mode='Markdown'
                            )
                            signal['msg_id'] = msg.message_id
                    else:
                        msg = await context.bot.send_message(
                            chat_id=OUTPUT_CHANNEL_ID,
                            text=storage.strategy.format_result(signal),
                            parse_mode='Markdown'
                        )
                        signal['msg_id'] = msg.message_id
                
                elif result[0] == 'dogon':
                    signal = result[1]
                    if signal['msg_id']:
                        try:
                            await context.bot.edit_message_text(
                                chat_id=OUTPUT_CHANNEL_ID,
                                message_id=signal['msg_id'],
                                text=storage.strategy.format_status(signal),
                                parse_mode='Markdown'
                            )
                        except:
                            pass
            
            # ТОЛЬКО ПОСЛЕ ПРОВЕРКИ смотрим новый сигнал
            if storage.strategy.get_active_count() == 0:
                signal = storage.strategy.check_signal(game_data)
                if signal:
                    msg = await context.bot.send_message(
                        chat_id=OUTPUT_CHANNEL_ID,
                        text=storage.strategy.format_signal(signal),
                        parse_mode='Markdown'
                    )
                    signal['msg_id'] = msg.message_id
        
        # ОБРАБОТКА ДОБОРА
        if game_data['player_draws'] or game_data['banker_draws']:
            logger.info(f"⏳ Игра #{game_num}: ожидание третьей карты")
            pending_games[game_num] = PendingGame(game_data, datetime.now())
            return
        
        # ПОЛНАЯ ИГРА
        if not game_data['player_draws'] and not game_data['banker_draws']:
            if game_num in pending_games:
                logger.info(f"✅ Игра #{game_num}: получена полная версия")
                del pending_games[game_num]
        
        # ОЧИСТКА
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
                game_data = storage.games[game_num]
                results = storage.strategy.check_predictions(game_num, game_data)
                
                for result in results:
                    if result[0] in ['win', 'loss']:
                        signal = result[1]
                        if signal['msg_id']:
                            try:
                                await context.bot.edit_message_text(
                                    chat_id=OUTPUT_CHANNEL_ID,
                                    message_id=signal['msg_id'],
                                    text=storage.strategy.format_result(signal),
                                    parse_mode='Markdown'
                                )
                            except:
                                msg = await context.bot.send_message(
                                    chat_id=OUTPUT_CHANNEL_ID,
                                    text=storage.strategy.format_result(signal),
                                    parse_mode='Markdown'
                                )
                                signal['msg_id'] = msg.message_id
                        else:
                            msg = await context.bot.send_message(
                                chat_id=OUTPUT_CHANNEL_ID,
                                text=storage.strategy.format_result(signal),
                                parse_mode='Markdown'
                            )
                            signal['msg_id'] = msg.message_id
                    
                    elif result[0] == 'dogon':
                        signal = result[1]
                        if signal['msg_id']:
                            try:
                                await context.bot.edit_message_text(
                                    chat_id=OUTPUT_CHANNEL_ID,
                                    message_id=signal['msg_id'],
                                    text=storage.strategy.format_status(signal),
                                    parse_mode='Markdown'
                                )
                            except:
                                pass
            
            del pending_games[game_num]

async def send_stats(context: ContextTypes.DEFAULT_TYPE):
    """Отправляет статистику каждые 3 часа"""
    text = storage.strategy.format_stats()
    
    await context.bot.send_message(
        chat_id=OUTPUT_CHANNEL_ID,
        text=text,
        parse_mode='Markdown'
    )

def main():
    print("\n" + "="*60)
    print("⚜️ MASTER БОТ - ИНТЕЛЛЕКТУАЛЬНЫЙ")
    print("="*60)
    print("✅ Анализ только по картам игрока")
    print("✅ Адаптивные веса условий")
    print("✅ Режим осторожности после проигрышей")
    print("✅ Статистика по часам")
    print("✅ Горячие/холодные масти")
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
        app.job_queue.run_repeating(send_stats, interval=10800, first=10)
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