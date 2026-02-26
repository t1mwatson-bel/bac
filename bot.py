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

# ======== MASTER СТРАТЕГИЯ (ТВОЙ АЛГОРИТМ) ========
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
        self.last_prediction_time = None
        self.min_time_between = 120  # 2 минуты между сигналами
        
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
    
    def get_dogon_plan(self, original_suit, attempt):
        """Возвращает план догона"""
        targets = self.color_map.get(original_suit, {})
        
        # Интервалы: +2 для 1-го, +3 для 2-го, +4 для 3-го
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
    
    def check_signal(self, game_data):
        """Проверяет условия для входа"""
        banker_cards = game_data.get('banker_cards', [])
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
        
        # Формируем план догона
        dogon_plans = []
        for i in range(3):
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
            'timestamp': current_time
        }
        
        self.active_signals.append(signal)
        self.last_prediction_time = current_time
        return signal
    
    def check_predictions(self, current_game_num, game_data):
        """Проверяет активные сигналы по завершенной игре"""
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
                logger.info(f"✅ Сигнал #{signal['id']} зашел в игре #{actual_game}")
            else:
                if signal['attempt'] < len(signal['dogon_plans']):
                    plan = signal['dogon_plans'][signal['attempt']]
                    signal['attempt'] += 1
                    signal['target_game'] = current_game_num + plan['interval']
                    signal['suit'] = plan['suit']
                    signal['status'] = 'pending'
                    results.append(('dogon', signal, plan))
                    logger.info(f"🔄 Сигнал #{signal['id']} догон {signal['attempt']} на игру #{signal['target_game']}")
                else:
                    signal['status'] = 'loss'
                    self.stats['total'] += 1
                    self.stats['failures'].append({
                        'game': current_game_num,
                        'signal': signal['id']
                    })
                    results.append(('loss', signal))
                    logger.info(f"❌ Сигнал #{signal['id']} не зашел после 3 попыток")
        
        return results
    
    def _format_signal(self, signal):
        """Форматирует сигнал для вывода"""
        # Формируем строку догонов
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
    
    def _format_result(self, signal, result):
        """Форматирует результат"""
        emoji = "✅" if result == 'win' else "❌"
        status = "ЗАШЁЛ" if result == 'win' else "НЕ ЗАШЁЛ"
        
        # Считаем проценты
        total = self.stats['total']
        success = self.stats['success']
        percent = int(success / max(1, total) * 100) if total > 0 else 0
        
        text = (
            f"{emoji} *MASTER СИГНАЛ #{signal['id']} {status}!*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📊 *ИСТОЧНИК:* #{signal['source_game']}\n"
            f"🎯 *ЦЕЛЬ:* #{signal['target_game']}\n"
            f"🃏 *МАСТЬ:* {signal['suit']}\n"
        )
        
        if result == 'win':
            text += f"🎯 *НАЙДЕНО В ИГРЕ:* #{signal.get('actual_game', '?')}\n\n"
        else:
            text += f"\n"
        
        text += (
            f"📊 *СТАТИСТИКА:*\n"
            f"• Всего: {total}\n"
            f"• Успешно: {success}\n"
            f"• Процент: {percent}%\n"
            f"⏱ {datetime.now(pytz.timezone('Europe/Moscow')).strftime('%H:%M')} МСК"
        )
        
        return text
    
    def _format_dogon(self, signal, plan):
        """Форматирует догон"""
        return (
            f"🔄 *MASTER ДОГОН #{signal['id']} — ПОПЫТКА {signal['attempt']}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📊 *ИСТОЧНИК:* #{signal['source_game']}\n"
            f"🎯 *ЦЕЛЬ:* #{signal['target_game']}\n"
            f"🃏 *МАСТЬ:* {signal['suit']} ({plan['strategy']})\n"
            f"📈 *ИНТЕРВАЛ:* +{plan['interval']}\n"
            f"⏱ {datetime.now(pytz.timezone('Europe/Moscow')).strftime('%H:%M')} МСК"
        )

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
    
    # Игра завершена если есть ✅ или 🟩 или 🔰
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
            
            # Проверяем прогнозы если игра завершена
            if game_data['is_complete']:
                results = storage.strategy.check_predictions(game_num, game_data)
                for result in results:
                    if result[0] == 'win':
                        await context.bot.send_message(
                            chat_id=OUTPUT_CHANNEL_ID,
                            text=storage.strategy._format_result(result[1], 'win'),
                            parse_mode='Markdown'
                        )
                    elif result[0] == 'loss':
                        await context.bot.send_message(
                            chat_id=OUTPUT_CHANNEL_ID,
                            text=storage.strategy._format_result(result[1], 'loss'),
                            parse_mode='Markdown'
                        )
                    elif result[0] == 'dogon':
                        await context.bot.send_message(
                            chat_id=OUTPUT_CHANNEL_ID,
                            text=storage.strategy._format_dogon(result[1], result[2]),
                            parse_mode='Markdown'
                        )
            
            if game_num in pending_games:
                del pending_games[game_num]
            
            return
        
        if game_data['player_draws'] or game_data['banker_draws']:
            logger.info(f"⏳ Игра #{game_num}: ожидание третьей карты")
            pending_games[game_num] = PendingGame(game_data, datetime.now())
            storage.games[game_num] = game_data
            
            # Проверяем сигнал (таймер внутри check_signal)
            signal = storage.strategy.check_signal(game_data)
            if signal:
                await context.bot.send_message(
                    chat_id=OUTPUT_CHANNEL_ID,
                    text=storage.strategy._format_signal(signal),
                    parse_mode='Markdown'
                )
            
            return
        
        if not game_data['player_draws'] and not game_data['banker_draws']:
            if game_num in pending_games:
                logger.info(f"✅ Игра #{game_num}: получена полная версия")
                del pending_games[game_num]
            else:
                logger.info(f"✅ Игра #{game_num}: полная версия сразу")
            
            storage.games[game_num] = game_data
            
            # ВАЖНО: Проверяем прогнозы если игра завершена
            if game_data['is_complete']:
                logger.info(f"🔍 Игра #{game_num} завершена, проверяем прогнозы")
                results = storage.strategy.check_predictions(game_num, game_data)
                
                for result in results:
                    if result[0] == 'win':
                        await context.bot.send_message(
                            chat_id=OUTPUT_CHANNEL_ID,
                            text=storage.strategy._format_result(result[1], 'win'),
                            parse_mode='Markdown'
                        )
                    elif result[0] == 'loss':
                        await context.bot.send_message(
                            chat_id=OUTPUT_CHANNEL_ID,
                            text=storage.strategy._format_result(result[1], 'loss'),
                            parse_mode='Markdown'
                        )
                    elif result[0] == 'dogon':
                        await context.bot.send_message(
                            chat_id=OUTPUT_CHANNEL_ID,
                            text=storage.strategy._format_dogon(result[1], result[2]),
                            parse_mode='Markdown'
                        )
            
            # Проверяем новый сигнал
            signal = storage.strategy.check_signal(game_data)
            if signal:
                await context.bot.send_message(
                    chat_id=OUTPUT_CHANNEL_ID,
                    text=storage.strategy._format_signal(signal),
                    parse_mode='Markdown'
                )
        
        # Очистка старых данных
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
                # Проверяем прогнозы для зависшей игры
                game_data = storage.games[game_num]
                results = storage.strategy.check_predictions(game_num, game_data)
                
                for result in results:
                    if result[0] == 'win':
                        await context.bot.send_message(
                            chat_id=OUTPUT_CHANNEL_ID,
                            text=storage.strategy._format_result(result[1], 'win'),
                            parse_mode='Markdown'
                        )
                    elif result[0] == 'loss':
                        await context.bot.send_message(
                            chat_id=OUTPUT_CHANNEL_ID,
                            text=storage.strategy._format_result(result[1], 'loss'),
                            parse_mode='Markdown'
                        )
                    elif result[0] == 'dogon':
                        await context.bot.send_message(
                            chat_id=OUTPUT_CHANNEL_ID,
                            text=storage.strategy._format_dogon(result[1], result[2]),
                            parse_mode='Markdown'
                        )
            
            del pending_games[game_num]

def main():
    print("\n" + "="*60)
    print("⚜️ MASTER СТРАТЕГИЯ - ТВОЙ АЛГОРИТМ")
    print("="*60)
    print("✅ Банкир: картинка (K,Q,J) + цифра")
    print("✅ Масть берем от цифры")
    print("✅ Вход через +2 игры")
    print("✅ Догон 3 шага: +2 (цвет), +3 (против), +4 (прямая)")
    print("✅ Таймер 2 минуты между сигналами")
    print("✅ ПРОВЕРКА РЕЗУЛЬТАТОВ в завершенных играх")
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