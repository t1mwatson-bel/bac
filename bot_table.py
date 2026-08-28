import os
import sys
import requests
import json
import re
import time
import pickle
import numpy as np
from datetime import datetime, timedelta
import pytz
from collections import Counter, defaultdict
import warnings
warnings.filterwarnings('ignore')

# =====================================================================
# ML-библиотеки
# =====================================================================
try:
    import lightgbm as lgb
    ML_AVAILABLE = True
    print("✅ LightGBM загружен", flush=True)
except ImportError:
    ML_AVAILABLE = False
    print("⚠️ LightGBM не установлен. Работаем без ML.", flush=True)

# =====================================================================
# ПРИНУДИТЕЛЬНЫЙ ВЫВОД ЛОГОВ
# =====================================================================
sys.stdout.flush()
print("=" * 60, flush=True)
print("🃏 ГИБРИДНЫЙ БОТ: АНАЛИЗАТОР + ML + ПРОГНОЗЫ", flush=True)
print("=" * 60, flush=True)

# =====================================================================
# ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ
# =====================================================================
BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    BOT_TOKEN = os.getenv('BOT_TOKEN_PROGNOZ')

CHANNEL_STATS = os.getenv('CHANNEL_STATS')
CHANNEL_PROGNOZ = os.getenv('CHANNEL_PROGNOZ')

print("🔍 ДИАГНОСТИКА ПЕРЕМЕННЫХ:", flush=True)
print(f"BOT_TOKEN: {BOT_TOKEN[:5] if BOT_TOKEN else 'НЕ ЗАДАН'}", flush=True)
print(f"CHANNEL_STATS: {CHANNEL_STATS if CHANNEL_STATS else 'НЕ ЗАДАН'}", flush=True)
print(f"CHANNEL_PROGNOZ: {CHANNEL_PROGNOZ if CHANNEL_PROGNOZ else 'НЕ ЗАДАН'}", flush=True)
print("=" * 60, flush=True)

if not BOT_TOKEN or not CHANNEL_STATS or not CHANNEL_PROGNOZ:
    print("❌ ОШИБКА: переменные окружения не заданы!", flush=True)
    exit(1)

print("✅ Все переменные заданы!", flush=True)

# =====================================================================
# НАСТРОЙКИ
# =====================================================================
MOSCOW_TZ = pytz.timezone('Europe/Moscow')
BASE_URL = "https://1xlite-36553.pro"

# Файлы
DATA_FILE = "hybrid_data.json"           # Все собранные игры
HISTORY_FILE = "hybrid_history.json"     # История прогнозов
ML_MODEL_FILE = "hybrid_model.pkl"       # ML-модель
OFFSET_FILE = "hybrid_offset.txt"        # Смещение для Telegram

# Настройки
MAX_RECORDS = 10000          # МАКСИМАЛЬНОЕ КОЛИЧЕСТВО ИГР ДЛЯ СБОРА
STOP_COLLECT_AT = 10000      # ОСТАНОВИТЬ СБОР ПОСЛЕ 10000 ИГР
CHECK_INTERVAL = 10
OFFSET = 10
TRAIN_EVERY = 30
MIN_TRAIN_SAMPLES = 200
MAX_HISTORY = 3000

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": f"{BASE_URL}/ru/live/twentyone/2092323-21-classics",
    "Cookie": "platform_type=desktop; SESSION=34219176f69eace1b636911e2de9a15e; lng=ru; cookies_agree_type=3; tzo=3; is12h=0; auid=uaJbk2qIgo2M+6ofAxNqAg==; _ym_isad=2; mdd=1; _ga_7JGWL9SV66=GS2.1.s1787337341$o4$g1$t1787337359$j42$l0$h1608459194; window_width=150; referral_values=%7B%22type%22%3A%22reflinkid%22%2C%22val%22%3A%22s_50970m_355c_%22%2C%22additional%22%3A%7B%22name_tag%22%3A%22tag%22%7D%7D; fatman_uuid=45f69ff0-ecb1-67d4-3ff2-3a45baafc739; che_g=777dc1b9-efbf-4728-947a-4a2992ef6da5; sh.session.id=684214c4-f09e-42da-9c1a-ea61b9aca91b; _ym_uid=1786989905737338437; _ym_d=1786989905; _ga=GA1.1.547872848.1786989906"
}

SUITS = ["♠️", "♣️", "♦️", "♥️"]
SUITS_NAMES = {0: "♠️", 1: "♣️", 2: "♦️", 3: "♥️"}
RANK_VALUES = {'6': 6, '7': 7, '8': 8, '9': 9, '10': 10, 'J': 11, 'Q': 12, 'K': 13, 'A': 14}
RANKS = {1: "A", 2: "2", 3: "3", 4: "4", 5: "5", 6: "6", 7: "7", 8: "8", 9: "9", 10: "10", 11: "J", 12: "Q", 13: "K"}

# =====================================================================
# ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ
# =====================================================================
ml_model = None
ml_initialized = False
ml_training_data = []
processed_games = set()
finished_games = set()
game_counter = 0
last_train_count = 0
collection_active = True  # Флаг: собираем данные или только прогнозируем

# =====================================================================
# СТАТИСТИКА
# =====================================================================
stats = {
    "total": 0,
    "win": 0,
    "lose": 0,
    "by_dogon": {0: 0, 1: 0, 2: 0, 3: 0},
    "ml_wins": 0,
    "ml_losses": 0,
    "rules_wins": 0,
    "rules_losses": 0,
    "games_collected": 0,
    "last_report": time.time()
}

def update_stats(dogon_number, result, method="rules"):
    stats["total"] += 1
    if result == "win":
        stats["win"] += 1
        stats["by_dogon"][dogon_number] = stats["by_dogon"].get(dogon_number, 0) + 1
        if method == "ml":
            stats["ml_wins"] += 1
        else:
            stats["rules_wins"] += 1
    else:
        stats["lose"] += 1
        if method == "ml":
            stats["ml_losses"] += 1
        else:
            stats["rules_losses"] += 1

def send_stats_report():
    global collection_active
    now = datetime.now(MOSCOW_TZ)
    msg = f"📊 <b>СТАТИСТИКА (ГИБРИД)</b>\n"
    msg += f"⏰ {now.strftime('%d.%m.%Y %H:%M:%S')}\n"
    msg += f"{'=' * 30}\n"
    msg += f"📊 Собрано игр: {stats['games_collected']}/{MAX_RECORDS}\n"
    msg += f"{'🔄' if collection_active else '⏸️'} Сбор данных: {'АКТИВЕН' if collection_active else 'ОСТАНОВЛЕН'}\n"
    msg += f"📈 Всего прогнозов: {stats['total']}\n"
    if stats['total'] > 0:
        msg += f"✅ Зашло: {stats['win']} ({stats['win']/stats['total']*100:.1f}%)\n"
    else:
        msg += f"✅ Зашло: 0\n"
    msg += f"❌ Не зашло: {stats['lose']}\n"
    msg += f"{'=' * 30}\n"
    msg += f"<b>По методам:</b>\n"
    total_rules = stats['rules_wins'] + stats['rules_losses']
    if total_rules > 0:
        msg += f"  📌 Правила: {stats['rules_wins']}✅ / {stats['rules_losses']}❌ ({stats['rules_wins']/total_rules*100:.1f}%)\n"
    else:
        msg += f"  📌 Правила: 0✅ / 0❌\n"
    total_ml = stats['ml_wins'] + stats['ml_losses']
    if total_ml > 0:
        msg += f"  🤖 ML: {stats['ml_wins']}✅ / {stats['ml_losses']}❌ ({stats['ml_wins']/total_ml*100:.1f}%)\n"
    else:
        msg += f"  🤖 ML: 0✅ / 0❌\n"
    msg += f"{'=' * 30}\n"
    msg += f"<b>По догонам:</b>\n"
    for i in range(4):
        msg += f"  Догон {i}: {stats['by_dogon'].get(i, 0)}\n"
    
    if ml_initialized:
        msg += f"\n🤖 ML: АКТИВНА"
    else:
        msg += f"\n🤖 ML: ОЖИДАЕТ ({len(ml_training_data)}/{MIN_TRAIN_SAMPLES})"
    
    send_message(CHANNEL_STATS, msg)

# =====================================================================
# ФУНКЦИИ ТЕЛЕГРАМ
# =====================================================================
def get_updates(offset):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    params = {"offset": offset, "timeout": 30}
    try:
        response = requests.get(url, params=params, timeout=35)
        return response.json()
    except Exception as e:
        print(f"❌ Ошибка getUpdates: {e}", flush=True)
        return {}

def send_message(chat_id, text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            return response.json()["result"]["message_id"]
        else:
            print(f"❌ Ошибка отправки: {response.status_code}", flush=True)
            return None
    except Exception as e:
        print(f"❌ Ошибка отправки: {e}", flush=True)
        return None

def edit_message(message_id, text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText"
    payload = {"chat_id": CHANNEL_PROGNOZ, "message_id": message_id, "text": text, "parse_mode": "HTML"}
    try:
        response = requests.post(url, json=payload, timeout=10)
        return response.status_code == 200
    except Exception as e:
        print(f"❌ Ошибка редактирования: {e}", flush=True)
        return False

def send_startup_message():
    global collection_active
    now = datetime.now(MOSCOW_TZ)
    data_count = len(load_data())
    
    msg = f"🚀 <b>ГИБРИДНЫЙ БОТ ЗАПУЩЕН</b>\n"
    msg += f"⏰ {now.strftime('%d.%m.%Y %H:%M:%S')} (МСК)\n"
    msg += f"📌 Режим: АНАЛИЗАТОР + ML + ПРОГНОЗЫ\n"
    msg += f"🔄 Версия: 12.1 (автостоп сбора)\n"
    msg += f"📊 Данных: {data_count}/{MAX_RECORDS} игр\n"
    
    if data_count >= MAX_RECORDS:
        collection_active = False
        msg += f"⏸️ СБОР ДАННЫХ ОСТАНОВЛЕН (достигнут лимит {MAX_RECORDS})\n"
    else:
        msg += f"🔄 Сбор данных: АКТИВЕН\n"
    
    if ml_initialized:
        msg += f"🤖 ML: АКТИВНА"
    else:
        msg += f"🤖 ML: ОЖИДАЕТ ({data_count}/{MIN_TRAIN_SAMPLES})"
    
    send_message(CHANNEL_PROGNOZ, msg)
    print(f"📤 Приветствие отправлено", flush=True)

# =====================================================================
# ФУНКЦИИ ДЛЯ РАБОТЫ С ДАННЫМИ
# =====================================================================
def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return []
    return []

def save_data(record):
    global collection_active, stats
    
    data = load_data()
    
    # Проверяем, не превышен ли лимит
    if len(data) >= MAX_RECORDS:
        if collection_active:
            collection_active = False
            print(f"⏸️ СБОР ДАННЫХ ОСТАНОВЛЕН! Достигнут лимит {MAX_RECORDS} игр", flush=True)
            send_message(CHANNEL_STATS, f"⏸️ <b>СБОР ДАННЫХ ОСТАНОВЛЕН</b>\nДостигнут лимит {MAX_RECORDS} игр\n📊 Всего собрано: {len(data)}")
        return data
    
    existing_index = None
    for i, r in enumerate(data):
        if r.get("game_id") == record["game_id"]:
            existing_index = i
            break
    
    if existing_index is not None:
        data[existing_index] = record
    else:
        data.append(record)
        stats["games_collected"] += 1
    
    # Проверяем после добавления
    if len(data) >= MAX_RECORDS and collection_active:
        collection_active = False
        print(f"⏸️ СБОР ДАННЫХ ОСТАНОВЛЕН! Достигнут лимит {MAX_RECORDS} игр", flush=True)
        send_message(CHANNEL_STATS, f"⏸️ <b>СБОР ДАННЫХ ОСТАНОВЛЕН</b>\nДостигнут лимит {MAX_RECORDS} игр\n📊 Всего собрано: {len(data)}")
    
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    
    return data

def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return []
    return []

def save_history(history):
    if len(history) > MAX_HISTORY:
        history = history[-MAX_HISTORY:]
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)

def get_offset():
    if os.path.exists(OFFSET_FILE):
        try:
            with open(OFFSET_FILE, "r") as f:
                return int(f.read().strip())
        except:
            return 0
    return 0

def save_offset(offset):
    with open(OFFSET_FILE, "w") as f:
        f.write(str(offset))

# =====================================================================
# ФУНКЦИИ API
# =====================================================================
def get_active_games():
    try:
        url = f"{BASE_URL}/service-api/main-live-feed/v3/games1x2?cfView=3&count=40&fcountry=1&gr=415&grMode=4&lng=ru&ref=7&selectedMs=1.146.2092323,2.146.2092323,10.146.2092323"
        response = requests.get(url, headers=HEADERS, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, dict) and "Value" in data:
                games = data.get("Value", [])
            elif isinstance(data, list):
                games = data
            else:
                return []
            
            active_games = []
            for game in games:
                if game.get("liga", {}).get("id") == 2092323:
                    game_id = game.get("id")
                    if game_id:
                        active_games.append(game)
            return active_games
        else:
            return []
    except Exception as e:
        print(f"❌ Ошибка API: {e}", flush=True)
        return []

def get_game_data(game_id):
    url = f"{BASE_URL}/service-api/LiveFeed/GetGameZip?id={game_id}&isSubGames=true&GroupEvents=true&countevents=250&grMode=4&partner=7&topGroups=&country=190&marketType=1&isNewBuilder=true"
    try:
        start_time = time.time()
        response = requests.get(url, headers=HEADERS, timeout=5)
        end_time = time.time()
        latency = (end_time - start_time) * 1000
        
        if response.status_code == 200:
            return response.json(), latency, start_time, end_time
        else:
            return None, None, None, None
    except Exception as e:
        print(f"❌ Ошибка игры {game_id}: {e}", flush=True)
        return None, None, None, None

def parse_cards_and_state(data):
    sc = data.get("Value", {}).get("SC", {})
    player_cards = []
    dealer_cards = []
    state = None
    
    for item in sc.get("S", []):
        if item.get("Key") == "P1":
            try:
                player_cards = json.loads(item.get("Value", "[]"))
            except:
                player_cards = []
        if item.get("Key") == "P2":
            try:
                dealer_cards = json.loads(item.get("Value", "[]"))
            except:
                dealer_cards = []
        if item.get("Key") == "STATE":
            state = item.get("Value")
    
    return player_cards, dealer_cards, state

def get_game_number():
    now = datetime.now(MOSCOW_TZ)
    start = now.replace(hour=3, minute=0, second=0, microsecond=0)
    if now < start:
        start = start - timedelta(days=1)
    diff_minutes = (now - start).total_seconds() / 60
    game_number = int(diff_minutes) // 2 % 720 + 1
    return game_number

def parse_game_from_text(text):
    try:
        game_match = re.search(r'#N(\d+)', text)
        if not game_match:
            return None
        game_number = int(game_match.group(1))
        
        parts = None
        if '◀️' in text:
            parts = text.split('◀️')
        elif '▶️' in text:
            parts = text.split('▶️')
        elif '-' in text:
            parts = text.split('-')
        elif '—' in text:
            parts = text.split('—')
        else:
            return None
        
        if not parts or len(parts) < 2:
            return None
        
        player_part = parts[0].strip()
        dealer_part = parts[1].strip()
        
        def parse_cards_from_part(part):
            cards_match = re.search(r'\(([^)]+)\)', part)
            if not cards_match:
                return []
            cards_str = cards_match.group(1).strip()
            cards = []
            i = 0
            while i < len(cards_str):
                if cards_str[i] == ' ':
                    i += 1
                    continue
                rank = ''
                if i + 1 < len(cards_str) and cards_str[i:i+2] == '10':
                    rank = '10'
                    i += 2
                elif cards_str[i] in 'AKQJ':
                    rank = cards_str[i]
                    i += 1
                elif cards_str[i].isdigit():
                    rank = cards_str[i]
                    i += 1
                else:
                    i += 1
                    continue
                suit = ''
                if i < len(cards_str):
                    if cards_str[i:i+2] == '♠️':
                        suit = '♠️'
                        i += 2
                    elif cards_str[i:i+2] == '♣️':
                        suit = '♣️'
                        i += 2
                    elif cards_str[i:i+2] == '♦️':
                        suit = '♦️'
                        i += 2
                    elif cards_str[i:i+2] == '♥️':
                        suit = '♥️'
                        i += 2
                    elif cards_str[i] in '♠♣♦♥':
                        suit = cards_str[i].replace('♠', '♠️').replace('♣', '♣️').replace('♦', '♦️').replace('♥', '♥️')
                        i += 1
                    else:
                        i += 1
                        continue
                if rank and suit:
                    cards.append({"rank": rank, "suit": suit})
            return cards
        
        player_cards = parse_cards_from_part(player_part)
        dealer_cards = parse_cards_from_part(dealer_part)
        
        return {
            "number": game_number,
            "player_cards": player_cards,
            "dealer_cards": dealer_cards,
            "text": text
        }
    except Exception as e:
        print(f"❌ Ошибка парсинга: {e}", flush=True)
        return None

# =====================================================================
# ML-ФУНКЦИИ
# =====================================================================
def extract_features_from_game(game_data, latency, game_num):
    """Извлекает признаки из игры для ML"""
    if not game_data:
        return None
    
    player_cards = game_data.get("player_cards", [])
    dealer_cards = game_data.get("dealer_cards", [])
    
    features = {
        "latency": latency,
        "game_num": game_num % 100,
        
        "p1_rank_val": 0, "p1_suit": -1,
        "p2_rank_val": 0, "p2_suit": -1,
        "p3_rank_val": 0, "p3_suit": -1,
        "p4_rank_val": 0, "p4_suit": -1,
        "p5_rank_val": 0, "p5_suit": -1,
        
        "d1_rank_val": 0, "d1_suit": -1,
        "d2_rank_val": 0, "d2_suit": -1,
        "d3_rank_val": 0, "d3_suit": -1,
        "d4_rank_val": 0, "d4_suit": -1,
        
        "player_total": 0,
        "dealer_total": 0,
        "player_count": len(player_cards),
        "dealer_count": len(dealer_cards),
    }
    
    for i, card in enumerate(player_cards[:5]):
        rank = card.get("rank", "")
        suit = card.get("suit", "")
        if rank in RANK_VALUES:
            features[f"p{i+1}_rank_val"] = RANK_VALUES[rank]
        if suit in SUITS:
            features[f"p{i+1}_suit"] = SUITS.index(suit)
    
    for i, card in enumerate(dealer_cards[:4]):
        rank = card.get("rank", "")
        suit = card.get("suit", "")
        if rank in RANK_VALUES:
            features[f"d{i+1}_rank_val"] = RANK_VALUES[rank]
        if suit in SUITS:
            features[f"d{i+1}_suit"] = SUITS.index(suit)
    
    player_total = 0
    for card in player_cards:
        rank = card.get("rank", "")
        if rank in RANK_VALUES:
            val = RANK_VALUES[rank]
            if val >= 11:
                player_total += 10
            else:
                player_total += val
    features["player_total"] = player_total
    
    dealer_total = 0
    for card in dealer_cards:
        rank = card.get("rank", "")
        if rank in RANK_VALUES:
            val = RANK_VALUES[rank]
            if val >= 11:
                dealer_total += 10
            else:
                dealer_total += val
    features["dealer_total"] = dealer_total
    
    return features

def train_ml_model():
    """Обучает ML-модель на собранных данных"""
    global ml_model, ml_initialized, last_train_count
    
    if not ML_AVAILABLE:
        return False
    
    data = load_data()
    if len(data) < MIN_TRAIN_SAMPLES:
        print(f"⚠️ ML: недостаточно данных ({len(data)}/{MIN_TRAIN_SAMPLES})", flush=True)
        return False
    
    X = []
    y = []
    
    for game in data:
        player_cards = game.get("player_cards", [])
        if not player_cards:
            continue
        
        features = extract_features_from_game(game, game.get("latency_ms", 0), 0)
        if not features:
            continue
        
        target_suit = player_cards[0].get("suit")
        if not target_suit or target_suit not in SUITS:
            continue
        
        feature_vector = []
        for key in sorted(features.keys()):
            feature_vector.append(features[key])
        
        X.append(feature_vector)
        y.append(SUITS.index(target_suit))
    
    if len(X) < MIN_TRAIN_SAMPLES:
        print(f"⚠️ ML: недостаточно качественных данных ({len(X)}/{MIN_TRAIN_SAMPLES})", flush=True)
        return False
    
    if len(set(y)) < 2:
        print("⚠️ ML: только один класс, обучение невозможно", flush=True)
        return False
    
    print(f"🧠 ML: обучение на {len(X)} примерах...", flush=True)
    
    X = np.array(X)
    y = np.array(y)
    
    model = lgb.LGBMClassifier(
        n_estimators=150,
        max_depth=6,
        learning_rate=0.1,
        num_leaves=30,
        min_child_samples=15,
        random_state=42,
        n_jobs=-1,
        verbosity=-1
    )
    
    model.fit(X, y)
    
    ml_model = model
    ml_initialized = True
    last_train_count = len(X)
    
    try:
        with open(ML_MODEL_FILE, 'wb') as f:
            pickle.dump({
                'model': model,
                'feature_count': len(X[0]),
                'train_samples': len(X)
            }, f)
        print(f"✅ ML модель сохранена ({len(X)} примеров)", flush=True)
        return True
    except Exception as e:
        print(f"⚠️ Не удалось сохранить ML модель: {e}", flush=True)
        return False

def load_ml_model():
    """Загружает ML-модель с диска"""
    global ml_model, ml_initialized
    
    if not ML_AVAILABLE:
        return False
    
    if not os.path.exists(ML_MODEL_FILE):
        return False
    
    try:
        with open(ML_MODEL_FILE, 'rb') as f:
            data = pickle.load(f)
            ml_model = data['model']
            ml_initialized = True
            print(f"✅ ML модель загружена ({data.get('train_samples', 0)} примеров)", flush=True)
            return True
    except Exception as e:
        print(f"⚠️ Не удалось загрузить ML модель: {e}", flush=True)
        return False

def predict_ml(features):
    """Предсказывает масть с помощью ML"""
    global ml_model, ml_initialized
    
    if not ml_initialized or not ml_model:
        return None, None
    
    try:
        feature_vector = []
        for key in sorted(features.keys()):
            feature_vector.append(features[key])
        
        feature_vector = np.array([feature_vector])
        probs = ml_model.predict_proba(feature_vector)[0]
        pred_class = np.argmax(probs)
        pred_suit = SUITS[pred_class]
        confidence = probs[pred_class]
        
        return pred_suit, confidence
    except Exception as e:
        print(f"⚠️ Ошибка ML-прогноза: {e}", flush=True)
        return None, None

# =====================================================================
# ПРОГНОЗ
# =====================================================================
def predict_suit_by_latency(latency):
    if 93 <= latency < 95:
        return "♣️"
    elif 95 <= latency < 97:
        return "♠️"
    elif 97 <= latency < 99:
        return "♦️"
    elif 99 <= latency < 101:
        return "♥️"
    elif 101 <= latency < 103:
        return "♣️"
    elif 103 <= latency < 105:
        return "♥️"
    elif latency >= 105:
        return "♠️"
    else:
        return None

def refine_by_sequence(p1, p2, p3, base_suit, latency):
    if 93 <= latency < 95:
        if p1 and p1.get("rank") == "7" and p1.get("suit") == "♣️":
            return "♥️"
        elif p1 and p1.get("rank") == "8" and p1.get("suit") == "♠️":
            return "♣️"
        elif p1 and p1.get("rank") == "9" and p1.get("suit") == "♥️":
            return "♦️"
        elif p1 and p1.get("rank") in ["J", "Q", "K"] and p1.get("suit") == "♣️":
            return "♥️"
        elif p1 and p1.get("rank") in ["J", "Q", "K"] and p1.get("suit") == "♠️":
            return "♣️"
    
    if 95 <= latency < 97:
        if p1 and p1.get("rank") == "7" and p1.get("suit") == "♠️":
            return "♣️"
        elif p1 and p1.get("rank") == "8" and p1.get("suit") == "♣️":
            return "♥️"
        elif p1 and p1.get("rank") == "9" and p1.get("suit") == "♦️":
            return "♠️"
    
    if 97 <= latency < 99:
        if p1 and p1.get("rank") == "9" and p1.get("suit") == "♦️":
            return "♠️"
        elif p1 and p1.get("rank") == "8" and p1.get("suit") == "♠️":
            return "♦️"
        elif p1 and p1.get("rank") == "7" and p1.get("suit") == "♥️":
            return "♣️"
    
    if p1 and p2 and p1.get("suit") == p2.get("suit"):
        if p1.get("suit") == "♣️":
            return "♥️"
        elif p1.get("suit") == "♠️":
            return "♦️"
        elif p1.get("suit") == "♦️":
            return "♣️"
        elif p1.get("suit") == "♥️":
            return "♠️"
    
    return base_suit

def get_prediction(latency, current_game_data):
    """Гибридный прогноз: правила + ML"""
    
    base_suit = predict_suit_by_latency(latency)
    
    if current_game_data:
        p1 = current_game_data.get("player_cards", [])[0] if current_game_data.get("player_cards") else None
        p2 = current_game_data.get("dealer_cards", [])[0] if current_game_data.get("dealer_cards") else None
        p3 = current_game_data.get("player_cards", [])[1] if len(current_game_data.get("player_cards", [])) > 1 else None
        
        rules_pred = refine_by_sequence(p1, p2, p3, base_suit, latency)
    else:
        rules_pred = base_suit
    
    ml_pred = None
    ml_conf = None
    
    if ml_initialized and current_game_data:
        features = extract_features_from_game(current_game_data, latency, 0)
        if features:
            ml_pred, ml_conf = predict_ml(features)
    
    if ml_pred and ml_conf and ml_conf > 0.8:
        if ml_pred != rules_pred and rules_pred:
            print(f"🤖 ML {ml_pred} ({ml_conf:.2f}) vs RULES {rules_pred} — используем ML", flush=True)
            return ml_pred, "ml", ml_conf
        elif rules_pred:
            return rules_pred, "both", ml_conf
        else:
            return ml_pred, "ml", ml_conf
    
    if ml_pred and ml_conf and ml_conf > 0.6 and rules_pred is None:
        print(f"🤖 ML {ml_pred} ({ml_conf:.2f}) — правила не дали прогноз", flush=True)
        return ml_pred, "ml", ml_conf
    
    if rules_pred:
        return rules_pred, "rules", None
    elif ml_pred:
        return ml_pred, "ml", ml_conf
    else:
        return None, None, None

# =====================================================================
# ПРОВЕРКА РЕЗУЛЬТАТОВ
# =====================================================================
def check_results(history, all_messages):
    for entry in history:
        if entry.get("status") != "pending":
            continue
        
        target = entry.get("target")
        predicted_suit = entry.get("suit")
        message_id = entry.get("message_id")
        method = entry.get("method", "rules")
        
        if not predicted_suit or not message_id:
            continue
        
        max_games_to_check = 4
        
        for i in range(max_games_to_check):
            game_to_check = target + i
            
            game_msg = None
            for msg in all_messages:
                if f"#N{game_to_check}" in msg and ('✅' in msg or '🔰' in msg):
                    game_msg = msg
                    break
            
            if not game_msg:
                continue
            
            game_data = parse_game_from_text(game_msg)
            if not game_data:
                continue
            
            suit_found = False
            player_cards = game_data.get("player_cards", [])
            
            for card in player_cards:
                if card.get("suit") == predicted_suit:
                    suit_found = True
                    break
            
            if suit_found:
                print(f"🎯 МАСТЬ {predicted_suit} НАЙДЕНА в игре #N{game_to_check}!", flush=True)
                dogon_number = i
                update_stats(dogon_number, "win", method)
                
                original_text = f"🔮 <b>ТЕСТ: ГИБРИД (+10)</b>\n"
                original_text += f"🃏 Масть: {predicted_suit}\n"
                original_text += f"🎯 Целевая игра: #N{target}\n"
                original_text += f"📈 3 игры догон\n"
                original_text += f"🤖 Метод: {method.upper()}\n"
                original_text += f"⏰ {entry.get('time', '')[:16]}"
                
                if dogon_number == 0:
                    result_text = f"\n\n✅ <b>ЗАШЛО</b> в целевой игре: #N{game_to_check}"
                else:
                    result_text = f"\n\n✅ <b>ЗАШЛО</b> на догоне {dogon_number}: #N{game_to_check}"
                
                edit_message(message_id, original_text + result_text)
                entry["status"] = "win"
                entry["result_game"] = game_to_check
                entry["dogon"] = dogon_number
                save_history(history)
                return
            
            if i == max_games_to_check - 1:
                print(f"❌ Масть {predicted_suit} НЕ НАЙДЕНА за {max_games_to_check} игр", flush=True)
                update_stats(0, "lose", method)
                
                original_text = f"🔮 <b>ТЕСТ: ГИБРИД (+10)</b>\n"
                original_text += f"🃏 Масть: {predicted_suit}\n"
                original_text += f"🎯 Целевая игра: #N{target}\n"
                original_text += f"📈 3 игры догон\n"
                original_text += f"🤖 Метод: {method.upper()}\n"
                original_text += f"⏰ {entry.get('time', '')[:16]}"
                result_text = f"\n\n❌ <b>НЕ ЗАШЛО</b> (проверено {max_games_to_check} игр)"
                
                edit_message(message_id, original_text + result_text)
                entry["status"] = "lose"
                save_history(history)
                return

# =====================================================================
# СБОР ДАННЫХ (АНАЛИЗАТОР)
# =====================================================================
def collect_game_data():
    """Собирает данные об играх (работает пока collection_active = True)"""
    global collection_active, finished_games, game_counter
    
    if not collection_active:
        return
    
    active_games = get_active_games()
    if not active_games:
        return
    
    data = load_data()
    if len(data) >= MAX_RECORDS:
        collection_active = False
        print(f"⏸️ СБОР ДАННЫХ ОСТАНОВЛЕН! Достигнут лимит {MAX_RECORDS}", flush=True)
        return
    
    for game in active_games:
        game_id = str(game.get("id"))
        
        if game_id in finished_games:
            continue
        
        game_data, latency, start_time, end_time = get_game_data(game_id)
        if not game_data:
            continue
        
        player_cards, dealer_cards, state = parse_cards_and_state(game_data)
        
        if player_cards or dealer_cards:
            timestamp = datetime.fromtimestamp(start_time, MOSCOW_TZ) if start_time else datetime.now(MOSCOW_TZ)
            timestamp_msk_str = timestamp.strftime('%H:%M:%S.%f')[:-3]
            
            sequence = []
            max_len = max(len(player_cards), len(dealer_cards))
            for i in range(max_len):
                if i < len(player_cards):
                    pc = player_cards[i]
                    rank = RANKS.get(pc.get("CV", 0), "?")
                    suit = SUITS_NAMES.get(pc.get("CS", 0), "?")
                    sequence.append({"position": i*2+1, "who": "P", "rank": rank, "suit": suit})
                if i < len(dealer_cards):
                    dc = dealer_cards[i]
                    rank = RANKS.get(dc.get("CV", 0), "?")
                    suit = SUITS_NAMES.get(dc.get("CS", 0), "?")
                    sequence.append({"position": i*2+2, "who": "D", "rank": rank, "suit": suit})
            
            def calc_score(cards):
                score = 0
                for card in cards:
                    cv = card.get("CV", 0)
                    if cv == 14:
                        score += 11
                    elif cv == 13:
                        score += 4
                    elif cv == 12:
                        score += 3
                    elif cv == 11:
                        score += 2
                    elif 6 <= cv <= 10:
                        score += cv
                return score
            
            player_score = calc_score(player_cards)
            dealer_score = calc_score(dealer_cards)
            
            record = {
                "game_id": game_id,
                "timestamp_msk": timestamp_msk_str,
                "latency_ms": round(latency, 2) if latency else 0,
                "state": state,
                "player_score": player_score,
                "dealer_score": dealer_score,
                "player_cards": [{"rank": RANKS.get(c.get("CV", 0), "?"), "suit": SUITS_NAMES.get(c.get("CS", 0), "?")} for c in player_cards],
                "dealer_cards": [{"rank": RANKS.get(c.get("CV", 0), "?"), "suit": SUITS_NAMES.get(c.get("CS", 0), "?")} for c in dealer_cards],
                "sequence": sequence
            }
            
            data = save_data(record)
            game_counter += 1
            
            if state in ["4", "5"]:
                finished_games.add(game_id)
                print(f"🏁 Игра {game_id} завершена (state={state}), сохранена", flush=True)
            
            # Проверяем лимит после сохранения
            if len(data) >= MAX_RECORDS:
                collection_active = False
                print(f"⏸️ СБОР ДАННЫХ ОСТАНОВЛЕН! Достигнут лимит {MAX_RECORDS}", flush=True)
                return
        
        time.sleep(0.5)

# =====================================================================
# ОСНОВНОЙ ЦИКЛ
# =====================================================================
def main():
    global collection_active, stats, processed_games
    
    print("🔄 ГИБРИДНЫЙ БОТ ЗАПУЩЕН", flush=True)
    print(f"📁 Данные в {DATA_FILE}", flush=True)
    print(f"📊 Максимум записей: {MAX_RECORDS}", flush=True)
    print(f"⏱️ Интервал: {CHECK_INTERVAL} сек", flush=True)
    print("=" * 60, flush=True)
    
    # Загружаем данные
    existing_data = load_data()
    print(f"📊 Уже собрано записей: {len(existing_data)}", flush=True)
    
    if len(existing_data) >= MAX_RECORDS:
        collection_active = False
        print(f"⏸️ СБОР ДАННЫХ ОТКЛЮЧЁН (лимит {MAX_RECORDS} достигнут)", flush=True)
    
    # Загружаем ML модель
    load_ml_model()
    
    # Загружаем историю
    history = load_history()
    offset = get_offset()
    
    # Отправляем стартовое сообщение
    send_startup_message()
    
    # Собираем последние сообщения
    all_messages = []
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
        params = {"chat_id": CHANNEL_STATS, "limit": 100}
        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            for update in data.get("result", []):
                post = update.get("channel_post")
                if post and post.get("text"):
                    all_messages.append(post.get("text"))
    except:
        pass
    
    print(f"📥 Загружено сообщений: {len(all_messages)}", flush=True)
    
    last_stats_time = time.time()
    last_cleanup_time = time.time()
    last_forced_check = time.time()
    last_train_time = time.time()
    
    print("🚀 БОТ ГОТОВ К РАБОТЕ!", flush=True)
    print("=" * 60, flush=True)
    
    while True:
        try:
            current_time = time.time()
            
            # Отправка статистики каждый час
            if current_time - last_stats_time > 3600:
                send_stats_report()
                last_stats_time = current_time
            
            # Автообучение ML
            if current_time - last_train_time > 300:  # Каждые 5 минут
                data_count = len(load_data())
                if data_count >= MIN_TRAIN_SAMPLES:
                    train_ml_model()
                last_train_time = current_time
            
            # Сбор данных (если активен)
            collect_game_data()
            
            # Проверка результатов
            if current_time - last_forced_check > 30:
                check_results(history, all_messages)
                last_forced_check = current_time
            
            # Обработка сообщений из Telegram
            updates = get_updates(offset)
            for update in updates.get("result", []):
                offset = update["update_id"] + 1
                save_offset(offset)
                
                channel_post = update.get("channel_post")
                edited_post = update.get("edited_channel_post")
                post = channel_post if channel_post else edited_post
                if not post:
                    continue
                
                chat_id = post.get("chat", {}).get("id")
                if str(chat_id) != str(CHANNEL_STATS):
                    continue
                
                text = post.get("text", "")
                if not text or "#N" not in text:
                    continue
                
                game_id_match = re.search(r'#N(\d+)', text)
                if not game_id_match:
                    continue
                game_number = int(game_id_match.group(1))
                
                all_messages.append(text)
                if len(all_messages) > 500:
                    all_messages = all_messages[-500:]
                
                print(f"📥 Получена игра #N{game_number}", flush=True)
                
                if '✅' in text or '🔰' in text:
                    check_results(history, all_messages)
                    continue
                
                if game_number in processed_games:
                    continue
                
                # Проверяем, нужно ли делать прогноз
                current_num = get_game_number()
                target_game = current_num + OFFSET
                games_left = target_game - game_number
                
                if games_left == 1:
                    print(f"🔥 Время прогноза: #N{game_number} → #N{target_game} (+{OFFSET})", flush=True)
                    
                    # Получаем задержку
                    latency = None
                    active_games = get_active_games()
                    for g in active_games:
                        gid = str(g.get("id"))
                        data, measured_latency, _, _ = get_game_data(gid)
                        if data:
                            latency = measured_latency
                            break
                    
                    if latency is None:
                        print("⏳ Не удалось получить задержку", flush=True)
                        continue
                    
                    # Получаем данные текущей игры
                    current_game_data = None
                    for msg_text in all_messages:
                        if f"#N{game_number}" in msg_text:
                            current_game_data = parse_game_from_text(msg_text)
                            break
                    
                    # Делаем прогноз
                    predicted_suit, method, confidence = get_prediction(latency, current_game_data)
                    
                    if not predicted_suit:
                        print(f"⏭️ Нет прогноза для #N{target_game}", flush=True)
                        continue
                    
                    # Формируем сообщение
                    msg = f"🔮 <b>ТЕСТ: ГИБРИД (+10)</b>\n"
                    msg += f"🃏 Масть: {predicted_suit}\n"
                    if method == "ml":
                        msg += f"🤖 Метод: ML (увер. {confidence:.2f})\n"
                    elif method == "both":
                        msg += f"🤖 Метод: ML + ПРАВИЛА\n"
                    else:
                        msg += f"📌 Метод: ПРАВИЛА\n"
                    
                    if current_game_data:
                        p1 = current_game_data.get("player_cards", [])[0] if current_game_data.get("player_cards") else None
                        p2 = current_game_data.get("dealer_cards", [])[0] if current_game_data.get("dealer_cards") else None
                        p3 = current_game_data.get("player_cards", [])[1] if len(current_game_data.get("player_cards", [])) > 1 else None
                        
                        seq_str = ""
                        if p1:
                            seq_str += f"P1:{p1['rank']}{p1['suit']} "
                        if p2:
                            seq_str += f"D2:{p2['rank']}{p2['suit']} "
                        if p3:
                            seq_str += f"P3:{p3['rank']}{p3['suit']}"
                        
                        if seq_str:
                            msg += f"📌 {seq_str}\n"
                    
                    msg += f"🎯 Целевая игра: #N{target_game}\n"
                    msg += "📈 3 игры догон\n"
                    msg += f"⏰ {datetime.now(MOSCOW_TZ).strftime('%H:%M:%S')}"
                    
                    message_id = send_message(CHANNEL_PROGNOZ, msg)
                    
                    if message_id:
                        history.append({
                            "from_game": game_number,
                            "target": target_game,
                            "offset": OFFSET,
                            "suit": predicted_suit,
                            "method": method,
                            "time": datetime.now(MOSCOW_TZ).isoformat(),
                            "message_id": message_id,
                            "status": "pending"
                        })
                        save_history(history)
                        print(f"✅ ПРОГНОЗ ОТПРАВЛЕН: #N{target_game} → {predicted_suit} ({method})", flush=True)
                
                processed_games.add(game_number)
                if len(processed_games) > 500:
                    processed_games.clear()
            
            # Очистка
            if current_time - last_cleanup_time > 3600:
                if len(history) > MAX_HISTORY:
                    history = history[-MAX_HISTORY:]
                    save_history(history)
                last_cleanup_time = current_time
            
            time.sleep(CHECK_INTERVAL)
            
        except KeyboardInterrupt:
            print("🛑 Бот остановлен", flush=True)
            data_count = len(load_data())
            print(f"📊 Всего собрано записей: {data_count}", flush=True)
            break
        except Exception as e:
            print(f"❌ Ошибка: {e}", flush=True)
            import traceback
            traceback.print_exc()
            time.sleep(30)

if __name__ == "__main__":
    main()