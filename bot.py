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

# ======== MASTER СТРАТЕГИЯ С ИНТЕЛЛЕКТОМ ========
class MasterStrategy:
    """Твоя стратегия с анализом сомнительных ситуаций"""
    
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
        self.last_prediction_time = None
        self.min_time_between = 120  # 2 минуты между сигналами
        
        # Статистика для анализа
        self.doubtful_stats = {
            'skipped': 0,
            'taken': 0,
            'success_from_doubtful': 0
        }
        
    def is_doubtful_situation(self, game_data):
        """Проверяет сомнительную ситуацию по критериям"""
        banker_cards = game_data.get('banker_cards', [])
        player_cards = game_data.get('player_cards', [])
        
        reasons = []
        
        # 1️⃣ ДВЕ ОДИНАКОВЫЕ МАСТИ В ПЕРВОЙ ИГРЕ
        if len(banker_cards) >= 2:
            if banker_cards[0]['suit'] == banker_cards[1]['suit']:
                reasons.append(f"две одинаковые масти {banker_cards[0]['suit']}")
        
        # 2️⃣ ТРЕТЬЯ ДОБИРАЕТСЯ ОДИНАКОВАЯ
        if len(banker_cards) == 3:
            if banker_cards[2]['suit'] == banker_cards[1]['suit']:
                reasons.append(f"третья добирается той же мастью {banker_cards[2]['suit']}")
        
        # 3️⃣ ВТОРАЯ И ТРЕТЬЯ С ДОБОРОМ - ОДИНАКОВЫЕ
        if len(banker_cards) == 3:
            if banker_cards[1]['suit'] == banker_cards[2]['suit']:
                reasons.append(f"вторая и третья одинаковые {banker_cards[1]['suit']}")
        
        # 4️⃣ ОГРАНИЧЕНИЕ ДОБОРА (2+3 карты)
        if len(banker_cards) == 3 and len(player_cards) == 2:
            reasons.append("добор в первой игре (2+3)")
        
        # 5️⃣ ВСЕ ОДИНАКОВЫЕ МАСТИ
        if len(banker_cards) == 3:
            if banker_cards[0]['suit'] == banker_cards[1]['suit'] == banker_cards[2]['suit']:
                reasons.append(f"все три одинаковые масти {banker_cards[0]['suit']}")
        
        # 🔥 НОВОЕ: ДВЕ КАРТИНКИ ПОДРЯД (K, Q, J)
        if len(banker_cards) >= 2:
            if (banker_cards[0]['value'] in self.picture_values and 
                banker_cards[1]['value'] in self.picture_values):
                reasons.append(f"две картинки подряд ({banker_cards[0]['value']} и {banker_cards[1]['value']})")
        
        # 🔥 НОВОЕ: ЕСЛИ ЕСТЬ И МАСТИ И КАРТИНКИ - ТОЖЕ ПРОПУСКАЕМ
        if len(reasons) >= 2:
            reasons.append("комбо из нескольких аномалий")
        
        if reasons:
            return True, "; ".join(reasons)
        return False, ""
    
    def check_banker_combo(self, banker_cards):
        """Проверяет комбинацию банкира"""
        if len(banker_cards) != 2:
            return False, None
        
        card1, card2 = banker_cards
        
        # Если масти одинаковые - пропускаем
        if card1['suit'] == card2['suit']:
            logger.info(f"⏭️ Пропускаем: обе карты банкира одной масти {card1['suit']}")
            return False, None
        
        # Проверяем что одна карта - картинка, другая - цифра
        if (card1['value'] in self.picture_values and card2['value'] in self.number_values):
            return True, card2['suit']
        elif (card2['value'] in self.picture_values and card1['value'] in self.number_values):
            return True, card1['suit']
        
        return False, None
    
    def get_dogon_plan(self, original_suit, attempt):
        """Возвращает план догона (ТЕПЕРЬ ТОЛЬКО 2 ПОПЫТКИ)"""
        targets = self.color_map.get(original_suit, {})
        intervals = [2, 3]  # БЫЛО [2, 3, 4] - убрали третью попытку
        
        if attempt >= len(intervals):
            return None
        
        if attempt == 0:
            new_suit = targets.get('same', original_suit)
            strategy = 'цвет'
        elif attempt == 1:
            new_suit = targets.get('opposite', original_suit)
            strategy = 'против'
        else:
            return None  # Больше нет третьей попытки
        
        return {
            'suit': new_suit,
            'interval': intervals[attempt],
            'strategy': strategy,
            'attempt': attempt + 1
        }
    
    def check_signal(self, game_data):
        """Проверяет условия для входа с учетом сомнительных ситуаций"""
        banker_cards = game_data.get('banker_cards', [])
        
        # Проверяем сомнительную ситуацию
        is_doubtful, reason = self.is_doubtful_situation(game_data)
        
        if is_doubtful:
            logger.info(f"⚠️ Сомнительная ситуация в игре #{game_data['game_num']}: {reason}")
            self.doubtful_stats['skipped'] += 1
            return None
        
        is_valid, suit = self.check_banker_combo(banker_cards)
        
        if not is_valid:
            return None
        
        # Проверяем таймер
        current_time = datetime.now(pytz.timezone('Europe/Moscow'))
        if self.last_prediction_time:
            time_diff = (current_time - self.last_prediction_time).seconds
            if time_diff < self.min_time_between:
                logger.info(f"⏳ Таймер: {time_diff}с, нужно {self.min_time_between}с")
                return None
        
        self.signal_counter += 1
        signal_id = self.signal_counter
        
        # Вход через +2
        interval = 2
        target_game = game_data['game_num'] + interval
        
        # Формируем план догона (теперь максимум 2 попытки)
        dogon_plans = []
        for i in range(2):  # БЫЛО range(3) - теперь 2 попытки
            plan = self.get_dogon_plan(suit, i)
            if plan:
                dogon_plans.append(plan)
        
        signal = {
            'id': signal_id,
            'source_game': game_data['game_num'],
            'target_game': target_game,
            'suit': suit,
            'interval': interval,
            'dogon_plans': dogon_plans,
            'status': 'pending',
            'attempt': 0,
            'timestamp': current_time,
            'msg_id': None,
            'from_doubtful': is_doubtful
        }
        
        self.active_signals.append(signal)
        self.last_prediction_time = current_time
        self.doubtful_stats['taken'] += 1
        logger.info(f"⚜️ MASTER сигнал #{signal_id} от игры #{game_data['game_num']} на масть {suit}")
        return signal
    
    def check_predictions(self, current_game_num, game_data):
        """Проверяет активные сигналы по завершенной игре"""
        results = []
        
        for signal in self.active_signals:
            if signal['status'] != 'pending':
                continue
            
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
                
                if signal.get('from_doubtful'):
                    self.doubtful_stats['success_from_doubtful'] += 1
                
                results.append(('win', signal))
                logger.info(f"✅ MASTER сигнал #{signal['id']} зашел в игре #{actual_game}")
            else:
                if signal['attempt'] < len(signal['dogon_plans']):
                    plan = signal['dogon_plans'][signal['attempt']]
                    signal['attempt'] += 1
                    signal['target_game'] = current_game_num + plan['interval']
                    signal['suit'] = plan['suit']
                    signal['status'] = 'pending'
                    results.append(('dogon', signal, plan))
                    logger.info(f"🔄 MASTER сигнал #{signal['id']} догон {signal['attempt']} на игру #{signal['target_game']}")
                else:
                    signal['status'] = 'loss'
                    self.stats['total'] += 1
                    self.stats['failures'].append({
                        'game': current_game_num,
                        'signal': signal['id']
                    })
                    results.append(('loss', signal))
                    logger.info(f"❌ MASTER сигнал #{signal['id']} не зашел после 2 попыток")  # БЫЛО "после 3 попыток"
        
        return results
    
    def get_active_count(self):
        return len([s for s in self.active_signals if s['status'] == 'pending'])
    
    def get_stats(self):
        """Возвращает полную статистику"""
        total = self.stats['total']
        success = self.stats['success']
        percent = int(success / max(1, total) * 100) if total > 0 else 0
        
        doubtful_percent = 0
        if self.doubtful_stats['taken'] > 0:
            doubtful_percent = int(self.doubtful_stats['success_from_doubtful'] / self.doubtful_stats['taken'] * 100)
        
        return {
            'total': total,
            'success': success,
            'percent': percent,
            'doubtful': {
                'skipped': self.doubtful_stats['skipped'],
                'taken': self.doubtful_stats['taken'],
                'success': self.doubtful_stats['success_from_doubtful'],
                'percent': doubtful_percent
            }
        }
    
    def _format_signal(self, signal):
        """Форматирует сигнал для вывода"""
        dogon_lines = []
        for i, plan in enumerate(signal['dogon_plans']):
            dogon_lines.append(f"  • #{signal['target_game'] + plan['interval']} (+{plan['interval']}) — {plan['suit']} ({plan['strategy']})")
        
        dogon_text = "\n".join(dogon_lines)
        
        return (
            f"⚜️ *MASTER СИГНАЛ #{signal['id']}* ⚜️\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🎯 *Вход:* #{signal['target_game']} (+{signal['interval']}) — {signal['suit']}\n\n"
            f"🔄 *Догон:* \n{dogon_text}\n\n"
            f"⏱ От игры #{signal['source_game']}"
        )
    
    def _format_status(self, signal):
        """Форматирует текущий статус сигнала"""
        status_text = f"⚜️ *MASTER СИГНАЛ #{signal['id']}* ⚜️\n"
        status_text += f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        
        if signal['attempt'] == 0:
            status_text += f"📊 *Статус:* Ожидание входа в игре #{signal['target_game']}\n"
            status_text += f"🎯 *Масть:* {signal['suit']}\n\n"
        else:
            status_text += f"📊 *Статус:* Догон {signal['attempt']}/2\n"  # БЫЛО /3
            status_text += f"🎯 *Следующая цель:* #{signal['target_game']}\n"
            status_text += f"🃏 *Масть:* {signal['suit']}\n\n"
        
        if signal['attempt'] < len(signal['dogon_plans']):
            status_text += f"🔄 *Осталось догонов:*\n"
            for i in range(signal['attempt'], len(signal['dogon_plans'])):
                plan = signal['dogon_plans'][i]
                status_text += f"  • #{signal['target_game'] + plan['interval']} (+{plan['interval']}) — {plan['suit']} ({plan['strategy']})\n"
        
        status_text += f"\n⏱ От игры #{signal['source_game']}"
        return status_text
    
    def _format_result(self, signal):
        """Форматирует результат"""
        emoji = "✅" if signal['status'] == 'win' else "❌"
        status = "ЗАШЁЛ" if signal['status'] == 'win' else "НЕ ЗАШЁЛ"
        
        stats = self.get_stats()
        
        text = (
            f"{emoji} *MASTER СИГНАЛ #{signal['id']} {status}!*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📊 *ИСТОЧНИК:* #{signal['source_game']}\n"
            f"🎯 *ЦЕЛЬ:* #{signal['target_game']}\n"
            f"🃏 *МАСТЬ:* {signal['suit']}\n"
        )
        
        if signal['status'] == 'win':
            text += f"🎯 *НАЙДЕНО В ИГРЕ:* #{signal.get('actual_game', '?')}\n\n"
        else:
            text += f"\n"
        
        text += (
            f"📊 *СТАТИСТИКА:*\n"
            f"• Всего сигналов: {stats['total']}\n"
            f"• Успешно: {stats['success']}\n"
            f"• Процент: {stats['percent']}%\n"
            f"• Пропущено сомнительных: {stats['doubtful']['skipped']}\n"
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
                                text=storage.strategy._format_result(signal),
                                parse_mode='Markdown'
                            )
                        except:
                            msg = await context.bot.send_message(
                                chat_id=OUTPUT_CHANNEL_ID,
                                text=storage.strategy._format_result(signal),
                                parse_mode='Markdown'
                            )
                            signal['msg_id'] = msg.message_id
                    else:
                        msg = await context.bot.send_message(
                            chat_id=OUTPUT_CHANNEL_ID,
                            text=storage.strategy._format_result(signal),
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
                                text=storage.strategy._format_status(signal),
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
                        text=storage.strategy._format_signal(signal),
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
                                    text=storage.strategy._format_result(signal),
                                    parse_mode='Markdown'
                                )
                            except:
                                msg = await context.bot.send_message(
                                    chat_id=OUTPUT_CHANNEL_ID,
                                    text=storage.strategy._format_result(signal),
                                    parse_mode='Markdown'
                                )
                                signal['msg_id'] = msg.message_id
                        else:
                            msg = await context.bot.send_message(
                                chat_id=OUTPUT_CHANNEL_ID,
                                text=storage.strategy._format_result(signal),
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
                                    text=storage.strategy._format_status(signal),
                                    parse_mode='Markdown'
                                )
                            except:
                                pass
            
            del pending_games[game_num]

async def send_stats(context: ContextTypes.DEFAULT_TYPE):
    """Отправляет статистику каждые 3 часа"""
    stats = storage.strategy.get_stats()
    
    text = (
        f"📊 *СТАТИСТИКА MASTER БОТА*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📈 *ОБЩАЯ СТАТИСТИКА:*\n"
        f"• Всего сигналов: {stats['total']}\n"
        f"• Успешно: {stats['success']}\n"
        f"• Процент: {stats['percent']}%\n\n"
        f"⚠️ *СОМНИТЕЛЬНЫЕ СИТУАЦИИ:*\n"
        f"• Пропущено: {stats['doubtful']['skipped']}\n"
        f"• Взято: {stats['doubtful']['taken']}\n"
        f"• Успешно из взятых: {stats['doubtful']['success']}\n"
        f"• Процент из взятых: {stats['doubtful']['percent']}%\n\n"
        f"⏱ {datetime.now(pytz.timezone('Europe/Moscow')).strftime('%H:%M')} МСК"
    )
    
    await context.bot.send_message(
        chat_id=OUTPUT_CHANNEL_ID,
        text=text,
        parse_mode='Markdown'
    )

def main():
    print("\n" + "="*60)
    print("⚜️ MASTER БОТ - ИНТЕЛЛЕКТУАЛЬНЫЙ (ОПТИМИЗИРОВАННЫЙ)")
    print("="*60)
    print("✅ Анализ сомнительных ситуаций:")
    print("   • Две одинаковые масти")
    print("   • Третья добирается той же")
    print("   • Вторая и третья одинаковые")
    print("   • Добор 2+3")
    print("   • Все три одинаковые")
    print("   🔥 ДВЕ КАРТИНКИ ПОДРЯД (НОВОЕ)")
    print("✅ ТОЛЬКО 2 ДОГОНА (вместо 3)")
    print("✅ Статистика каждые 3 часа")
    print("✅ Таймер 2 минуты между сигналами")
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