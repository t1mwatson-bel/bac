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
from collections import deque, defaultdict
import warnings
import gc
import math
import traceback

warnings.filterwarnings("ignore")


# =====================================================================
# АВТОУСТАНОВКА ЗАВИСИМОСТЕЙ
# =====================================================================

try:
    import subprocess
    import importlib

    REQUIRED_PACKAGES = [
        "numpy",
        "scikit-learn",
        "requests",
        "pytz"
    ]

    def install_package(package):
        print(f"📦 Устанавливаю: {package}...", flush=True)
        try:
            subprocess.check_call([
                sys.executable,
                "-m",
                "pip",
                "install",
                package,
                "--quiet"
            ])
            print(f"✅ {package} установлен!", flush=True)
            return True
        except Exception as e:
            print(f"❌ Ошибка установки {package}: {e}", flush=True)
            return False

    def check_and_install_dependencies():
        print("=" * 60, flush=True)
        print("🔍 ПРОВЕРКА ЗАВИСИМОСТЕЙ...", flush=True)
        print("=" * 60, flush=True)

        missing = []
        for package in REQUIRED_PACKAGES:
            try:
                importlib.import_module(package.replace("-", "_"))
                print(f"✅ {package} - уже установлен", flush=True)
            except ImportError:
                print(f"⚠️ {package} - НЕ НАЙДЕН", flush=True)
                missing.append(package)

        if missing:
            print(f"\n📦 Нужно установить: {', '.join(missing)}", flush=True)
            for package in missing:
                if not install_package(package):
                    return False

        print("\n✅ ВСЕ ЗАВИСИМОСТИ УСТАНОВЛЕНЫ!", flush=True)
        print("=" * 60, flush=True)
        return True

    if not check_and_install_dependencies():
        sys.exit(1)

except Exception as e:
    print(f"⚠️ Ошибка проверки зависимостей: {e}", flush=True)


# =====================================================================
# ML
# =====================================================================

ML_AVAILABLE = False
ML_LIB = None

try:
    from sklearn.ensemble import RandomForestClassifier
    ML_AVAILABLE = True
    ML_LIB = "randomforest"
    print("✅ RandomForest загружен!", flush=True)
except ImportError:
    print("⚠️ RandomForest не установлен. Работаем только на истории.", flush=True)


# =====================================================================
# ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ
# =====================================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    BOT_TOKEN = os.getenv("BOT_TOKEN_PROGNOZ")

CHANNEL_STATS = os.getenv("CHANNEL_STATS")
CHANNEL_PROGNOZ = os.getenv("CHANNEL_PROGNOZ")

if not BOT_TOKEN or not CHANNEL_STATS or not CHANNEL_PROGNOZ:
    print("❌ ОШИБКА: переменные окружения не заданы!", flush=True)
    print("Нужны: BOT_TOKEN, CHANNEL_STATS, CHANNEL_PROGNOZ", flush=True)
    sys.exit(1)


# =====================================================================
# НОРМАЛИЗАЦИЯ
# =====================================================================

CHANNEL_STATS = str(CHANNEL_STATS).strip()
CHANNEL_PROGNOZ = str(CHANNEL_PROGNOZ).strip()


# =====================================================================
# НАСТРОЙКИ
# =====================================================================

MOSCOW_TZ = pytz.timezone("Europe/Moscow")
BASE_URL = "https://1xlite-0687.pro"

DATA_FILE = "cards_data_double.json"
HISTORY_FILE = "cards_history_double.json"
OFFSET_FILE = "cards_offset_double.txt"
GAME_HISTORY_FILE = "cards_game_history_double.json"
GAME_LATENCY_CACHE_FILE = "game_latency_cache_double.json"
HYBRID_DATA_FILE = "hybrid_state.json"

MAX_RECORDS = 10000
CHECK_INTERVAL = 5
MIN_TRAIN_SAMPLES = 50
MAX_HISTORY = 2000
MAX_GAME_HISTORY = 30
DOGON_GAMES = 4
LATENCY_CACHE_MAX_SIZE = 2000

FORECAST_OFFSET = 0  # ← OFFSET = 0
MIN_FORECAST_PROBABILITY = 0.29

LATENCY_MEASUREMENTS = 2
LATENCY_INTERVAL = 2


# =====================================================================
# КАРТЫ
# =====================================================================

TARGET_CARDS = [
    "J♠️", "J♣️", "J♦️", "J♥️",
    "Q♠️", "Q♣️", "Q♦️", "Q♥️",
    "K♠️", "K♣️", "K♦️", "K♥️",
    "A♠️", "A♣️", "A♦️", "A♥️"
]

SUITS_NAMES = {
    0: "♠️",
    1: "♣️",
    2: "♦️",
    3: "♥️"
}

RANKS = {
    1: "A",
    2: "2",
    3: "3",
    4: "4",
    5: "5",
    6: "6",
    7: "7",
    8: "8",
    9: "9",
    10: "10",
    11: "J",
    12: "Q",
    13: "K"
}


# =====================================================================
# HTTP HEADERS
# =====================================================================

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": f"{BASE_URL}/ru/live/twentyone/1643503-twentyone-game",
    "Cookie": "platform_type=desktop; lng=ru; cookies_agree_type=3; tzo=3; is12h=0"
}


# =====================================================================
# ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ
# =====================================================================

ml_model = None
ml_initialized = False
ml_feature_names = None

collection_active = True
game_history = deque(maxlen=MAX_GAME_HISTORY)
game_latency_cache = {}
processed_games = set()
finished_games = set()
predictions = []

seen_upcoming_games = set()

stats = {
    "total": 0,
    "win": 0,
    "lose": 0,
    "by_dogon": {0: 0, 1: 0, 2: 0, 3: 0},
    "ml_wins": 0,
    "ml_losses": 0,
    "games_collected": 0,
    "card_hits": defaultdict(int)
}


# =====================================================================
# TELEGRAM
# =====================================================================

def telegram_request(method, payload=None, timeout=20):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    try:
        response = requests.post(url, json=payload or {}, timeout=timeout)
        if response.status_code != 200:
            print(f"❌ Telegram {method}: HTTP {response.status_code}", flush=True)
            return None
        data = response.json()
        if not data.get("ok"):
            print(f"❌ Telegram {method}: {data}", flush=True)
            return None
        return data
    except Exception as e:
        print(f"❌ Telegram {method}: {e}", flush=True)
        return None


def send_message(chat_id, text):
    result = telegram_request("sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML"
    }, timeout=15)
    if not result:
        return None
    try:
        return result["result"]["message_id"]
    except:
        return None


def edit_message(message_id, text):
    result = telegram_request("editMessageText", {
        "chat_id": CHANNEL_PROGNOZ,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML"
    }, timeout=15)
    return bool(result)


# =====================================================================
# РАБОТА С ДАННЫМИ (ТОЛЬКО ЧТЕНИЕ hybrid_state.json)
# =====================================================================

def load_data():
    """Загружает данные из hybrid_state.json (только чтение, НЕ перезаписывает)"""
    
    data = []
    
    if os.path.exists(HYBRID_DATA_FILE):
        try:
            with open(HYBRID_DATA_FILE, "r", encoding="utf-8") as f:
                hybrid_data = json.load(f)
                
                if isinstance(hybrid_data, list):
                    print(f"📊 Загружено из hybrid_state.json: {len(hybrid_data)} записей", flush=True)
                    data = hybrid_data
                elif isinstance(hybrid_data, dict):
                    for key in ["data", "games", "records", "items", "history", "cards"]:
                        if key in hybrid_data and isinstance(hybrid_data[key], list):
                            data = hybrid_data[key]
                            print(f"📊 Загружено из hybrid_state.json ({key}): {len(data)} записей", flush=True)
                            break
        except Exception as e:
            print(f"⚠️ Ошибка загрузки hybrid_state.json: {e}", flush=True)
    
    if not data and os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                local_data = json.load(f)
                if isinstance(local_data, list):
                    data = local_data
                    print(f"📊 Загружено из {DATA_FILE}: {len(data)} записей", flush=True)
        except Exception as e:
            print(f"⚠️ Ошибка загрузки {DATA_FILE}: {e}", flush=True)
    
    if not data:
        print(f"⚠️ Данные не найдены! Использую пустой список.", flush=True)
        return []
    
    print(f"📊 ИТОГО загружено данных: {len(data)} записей", flush=True)
    
    return data


def save_data(record):
    """Сохраняет данные в локальный файл (НЕ трогает hybrid_state.json)"""
    global collection_active, stats
    data = load_data()
    if len(data) >= MAX_RECORDS:
        collection_active = False
        return data

    existing_index = None
    for i, r in enumerate(data):
        if r.get("game_id") == record.get("game_id"):
            existing_index = i
            break

    if existing_index is not None:
        data[existing_index] = record
    else:
        data.append(record)
        stats["games_collected"] += 1

    if len(data) >= MAX_RECORDS:
        collection_active = False
        print(f"⏸️ Достигнут лимит {MAX_RECORDS}", flush=True)

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return data


# =====================================================================
# ИСТОРИЯ ПРОГНОЗОВ
# =====================================================================

def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
        except Exception as e:
            print(f"⚠️ Ошибка истории прогнозов: {e}", flush=True)
    return []


def save_history(history):
    if len(history) > MAX_HISTORY:
        history = history[-MAX_HISTORY:]
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"⚠️ Ошибка сохранения истории: {e}", flush=True)


# =====================================================================
# ЗАДЕРЖКИ
# =====================================================================

def get_offset():
    if os.path.exists(OFFSET_FILE):
        try:
            with open(OFFSET_FILE, "r") as f:
                return int(f.read().strip())
        except:
            return 0
    return 0


def save_offset(offset):
    try:
        with open(OFFSET_FILE, "w") as f:
            f.write(str(offset))
    except Exception as e:
        print(f"⚠️ Ошибка сохранения offset: {e}", flush=True)


def load_game_history():
    if os.path.exists(GAME_HISTORY_FILE):
        try:
            with open(GAME_HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return deque(data, maxlen=MAX_GAME_HISTORY)
        except:
            pass
    return deque(maxlen=MAX_GAME_HISTORY)


def save_game_history():
    try:
        with open(GAME_HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(list(game_history), f, indent=2, ensure_ascii=False)
    except:
        pass


def update_game_history(latency, game_num):
    global game_history
    game_history.append({
        "latency": latency,
        "game_num": game_num,
        "timestamp": datetime.now(MOSCOW_TZ).isoformat()
    })
    save_game_history()


def load_latency_cache():
    global game_latency_cache
    if os.path.exists(GAME_LATENCY_CACHE_FILE):
        try:
            with open(GAME_LATENCY_CACHE_FILE, "r", encoding="utf-8") as f:
                game_latency_cache = json.load(f)
                print(f"📊 Загружено задержек: {len(game_latency_cache)}", flush=True)
                return True
        except Exception as e:
            print(f"⚠️ Ошибка загрузки задержек: {e}", flush=True)
    game_latency_cache = {}
    return False


def save_latency_cache():
    global game_latency_cache
    try:
        if len(game_latency_cache) > LATENCY_CACHE_MAX_SIZE:
            sorted_items = sorted(
                game_latency_cache.items(),
                key=lambda x: x[1].get("timestamp", ""),
                reverse=True
            )[:LATENCY_CACHE_MAX_SIZE]
            game_latency_cache = dict(sorted_items)
        with open(GAME_LATENCY_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(game_latency_cache, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        print(f"⚠️ Ошибка сохранения кэша: {e}", flush=True)
        return False


def cache_game_latency(game_id, latency, game_number, measurement_number=1):
    global game_latency_cache
    if game_id not in game_latency_cache:
        game_latency_cache[game_id] = {}

    game_latency_cache[game_id][f"latency_{measurement_number}"] = latency
    game_latency_cache[game_id]["game_number"] = game_number
    game_latency_cache[game_id]["timestamp"] = datetime.now(MOSCOW_TZ).isoformat()

    save_latency_cache()
    print(f"📊 Замер #{measurement_number}: {latency:.1f}мс для #{game_number}", flush=True)
    return True


# =====================================================================
# НОМЕР ИГРЫ
# =====================================================================

def get_game_number_by_time():
    now = datetime.now(MOSCOW_TZ)
    start = now.replace(hour=3, minute=0, second=0, microsecond=0)
    if now < start:
        start -= timedelta(days=1)
    diff_minutes = (now - start).total_seconds() / 60
    game_number = (int(diff_minutes) % 1440) + 1
    return game_number


def get_game_number_from_timestamp(timestamp):
    if not timestamp:
        return None
    try:
        if isinstance(timestamp, (int, float)):
            start_time = datetime.fromtimestamp(timestamp, MOSCOW_TZ)
        else:
            start_time = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00")).astimezone(MOSCOW_TZ)
    except:
        return None

    start_day = start_time.replace(hour=3, minute=0, second=0, microsecond=0)
    if start_time < start_day:
        start_day -= timedelta(days=1)
    diff_minutes = (start_time - start_day).total_seconds() / 60
    return (int(diff_minutes) % 1440) + 1


def add_game_offset(game_num, offset):
    return ((int(game_num) - 1 + int(offset)) % 1440) + 1


def circular_game_distance(a, b):
    diff = abs(int(a) - int(b))
    return min(diff, 1440 - diff)


# =====================================================================
# API
# =====================================================================

def get_game_data(game_id):
    url = f"{BASE_URL}/service-api/LiveFeed/GetGameZip?id={game_id}&isSubGames=true&GroupEvents=true&countevents=250&grMode=4&partner=7&topGroups=&country=190&marketType=1&isNewBuilder=true"
    try:
        start_time = time.time()
        response = requests.get(url, headers=HEADERS, timeout=5)
        end_time = time.time()
        latency = (end_time - start_time) * 1000

        if response.status_code == 200:
            return response.json(), latency, start_time, end_time
        return None, None, None, None
    except Exception as e:
        print(f"❌ Ошибка игры {game_id}: {e}", flush=True)
        return None, None, None, None


def get_upcoming_games():
    try:
        url = f"{BASE_URL}/service-api/main-live-feed/v3/leftMenuSports?fcountry=1&gr=415&lng=ru&ref=7&selectedMs=10.146.1643503"
        response = requests.get(url, headers=HEADERS, timeout=10)

        if response.status_code != 200:
            return []

        data = response.json()
        upcoming_games = []
        now = datetime.now(MOSCOW_TZ)

        if not isinstance(data, list):
            return []

        for section in data:
            if section.get("menuSectionId") != 10:
                continue
            for sport in section.get("sports", []):
                if sport.get("id") != 146:
                    continue
                for liga in sport.get("ligas", []):
                    if liga.get("id") != 1643503:
                        continue
                    for game in liga.get("games", []):
                        if game.get("nonStarted") != True:
                            continue
                        start_ts = game.get("startTs")
                        if not start_ts:
                            continue
                        game_num = get_game_number_from_timestamp(start_ts)
                        if not game_num:
                            continue
                        start_time = datetime.fromtimestamp(start_ts, MOSCOW_TZ)
                        minutes_until = (start_time - now).total_seconds() / 60

                        if 0 < minutes_until <= 20:
                            upcoming_games.append({
                                "game_id": str(game.get("id")),
                                "game_num": game_num,
                                "start_time": start_time,
                                "minutes_until": minutes_until,
                                "start_ts": start_ts
                            })

        return upcoming_games
    except Exception as e:
        print(f"❌ Ошибка будущих игр: {e}", flush=True)
        return []


def parse_cards_and_state(data):
    if not data or not isinstance(data, dict):
        return [], [], None

    sc = data.get("Value", {})
    if not isinstance(sc, dict):
        return [], [], None

    sc = sc.get("SC", {})
    if not isinstance(sc, dict):
        return [], [], None

    player_cards = []
    dealer_cards = []
    state = None

    for item in sc.get("S", []):
        if not isinstance(item, dict):
            continue
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


# =====================================================================
# КАРТЫ ИЗ ЗАПИСИ
# =====================================================================

def get_target_cards_from_record(record):
    cards = []
    all_cards = record.get("player_cards", []) + record.get("dealer_cards", [])
    for card in all_cards:
        if isinstance(card, dict):
            rank = card.get("rank", "")
            suit = card.get("suit", "")
            card_str = rank + suit
        else:
            card_str = str(card)

        if card_str in TARGET_CARDS:
            cards.append(card_str)
    return list(set(cards))


# =====================================================================
# ПОИСК ПО МИЛЛИСЕКУНДАМ (НОВАЯ ЛОГИКА)
# =====================================================================

def find_by_milliseconds(timestamp_msk):
    """Ищет игры с такой же миллисекундой"""
    if not timestamp_msk:
        return []
    
    # Извлекаем миллисекунды из времени
    parts = timestamp_msk.split(".")
    if len(parts) < 2:
        return []
    
    target_ms = parts[1]  # например "349"
    
    data = load_data()
    matches = []
    
    for record in data:
        record_time = record.get("timestamp_msk", "")
        if not record_time:
            continue
        
        record_parts = record_time.split(".")
        if len(record_parts) < 2:
            continue
        
        record_ms = record_parts[1]
        
        if record_ms == target_ms:
            cards = get_target_cards_from_record(record)
            if cards:
                matches.append({
                    "record": record,
                    "cards": cards,
                    "timestamp": record_time
                })
    
    return matches


def predict_by_milliseconds(timestamp_msk):
    """Прогноз по миллисекундам"""
    matches = find_by_milliseconds(timestamp_msk)
    
    if not matches:
        print("⚠️ Совпадений по миллисекундам не найдено", flush=True)
        return None, {}, 0
    
    # Считаем частоту карт
    card_counter = defaultdict(int)
    for match in matches:
        for card in match["cards"]:
            card_counter[card] += 1
    
    if not card_counter:
        return None, {}, len(matches)
    
    total = sum(card_counter.values())
    probabilities = {}
    for card, count in card_counter.items():
        probabilities[card] = count / total
    
    sorted_cards = sorted(probabilities.items(), key=lambda x: x[1], reverse=True)
    
    print(f"\n🔍 ПОИСК ПО МИЛЛИСЕКУНДАМ", flush=True)
    print(f"   Найдено совпадений: {len(matches)}", flush=True)
    print(f"   Миллисекунда: {timestamp_msk.split('.')[1] if '.' in timestamp_msk else '?'}", flush=True)
    for i, (card, prob) in enumerate(sorted_cards[:3], 1):
        print(f"   {i}. {card} — {prob * 100:.2f}%", flush=True)
    
    return sorted_cards[:2], probabilities, len(matches)


# =====================================================================
# ИСТОРИЧЕСКИЙ ПОИСК
# =====================================================================

def find_similar_historical_games(latency, current_game_num):
    data = load_data()
    if not data:
        return []

    matches = []
    for record in data:
        historical_latency = record.get("latency_ms", None)
        historical_game_num = record.get("game_number", None)

        if historical_latency is None:
            continue
        if historical_game_num is None:
            continue

        try:
            historical_latency = float(historical_latency)
            historical_game_num = int(historical_game_num)
        except:
            continue

        cards = get_target_cards_from_record(record)
        if not cards:
            continue

        latency_distance = abs(latency - historical_latency)
        if latency_distance > 150:
            continue

        game_distance = circular_game_distance(current_game_num, historical_game_num)
        if game_distance > 10:
            continue

        latency_score = max(0.0, 1.0 - latency_distance / 150)
        if latency_distance <= 30:
            latency_score += (1.0 - latency_distance / 30) * 0.5

        game_score = max(0.0, 1.0 - game_distance / 11)
        if game_distance == 0:
            game_score += 0.75

        similarity = latency_score * 0.60 + game_score * 0.40

        matches.append({
            "record": record,
            "cards": cards,
            "latency_distance": latency_distance,
            "game_distance": game_distance,
            "similarity": similarity
        })

    matches.sort(key=lambda x: x["similarity"], reverse=True)
    return matches[:300]


def historical_prediction(latency, game_num):
    matches = find_similar_historical_games(latency, game_num)

    if not matches:
        print("⚠️ Исторических аналогов не найдено", flush=True)
        return None, {}, 0

    card_scores = defaultdict(float)
    for match in matches:
        weight = match["similarity"]
        cards = match["cards"]
        if not cards:
            continue
        per_card_weight = weight / len(cards)
        for card in cards:
            card_scores[card] += per_card_weight

    if not card_scores:
        return None, {}, len(matches)

    total_score = sum(card_scores.values())
    probabilities = {}
    for card, score in card_scores.items():
        probabilities[card] = score / total_score if total_score > 0 else 0

    sorted_cards = sorted(probabilities.items(), key=lambda x: x[1], reverse=True)

    print("\n📚 ИСТОРИЧЕСКИЙ ПОИСК", flush=True)
    print(f"   Найдено аналогов: {len(matches)}", flush=True)
    print(f"   Задержка: {latency:.1f}мс", flush=True)
    for i, (card, prob) in enumerate(sorted_cards[:3], 1):
        print(f"   {i}. {card} — {prob * 100:.2f}%", flush=True)

    return sorted_cards[:2], probabilities, len(matches)


# =====================================================================
# ML ПРОГНОЗ
# =====================================================================

def build_training_features(record):
    latency = record.get("latency_ms", 0)
    game_num = record.get("game_number", 0)

    if not game_num:
        return None

    features = {
        "latency": float(latency),
        "game_num": float(game_num),
        "game_num_sin": math.sin(2 * math.pi * int(game_num) / 1440),
        "game_num_cos": math.cos(2 * math.pi * int(game_num) / 1440),
    }
    return features


def train_ml_model():
    global ml_model, ml_initialized, ml_feature_names

    if not ML_AVAILABLE:
        return False

    data = load_data()
    if len(data) < MIN_TRAIN_SAMPLES:
        print(f"⚠️ ML недостаточно игр: {len(data)}/{MIN_TRAIN_SAMPLES}", flush=True)
        return False

    X = []
    y = []
    feature_names = None

    print(f"🧠 ML: обучение на {len(data)} исторических играх...", flush=True)

    for record in data:
        features = build_training_features(record)
        if not features:
            continue

        cards = get_target_cards_from_record(record)
        if not cards:
            continue

        if feature_names is None:
            feature_names = sorted(features.keys())

        feature_vector = [features[key] for key in feature_names]

        for card in cards:
            if card not in TARGET_CARDS:
                continue
            X.append(feature_vector)
            y.append(TARGET_CARDS.index(card))

    if len(X) < MIN_TRAIN_SAMPLES:
        print(f"⚠️ ML недостаточно примеров: {len(X)}", flush=True)
        return False

    X = np.array(X)
    y = np.array(y)

    print(f"🧠 ML обучается на {len(X)} примерах", flush=True)

    model = RandomForestClassifier(
        n_estimators=300,
        max_depth=12,
        min_samples_leaf=3,
        random_state=42,
        n_jobs=1,
        class_weight="balanced_subsample"
    )

    try:
        model.fit(X, y)
    except Exception as e:
        print(f"❌ Ошибка обучения ML: {e}", flush=True)
        return False

    ml_model = model
    ml_initialized = True
    ml_feature_names = feature_names

    print(f"✅ ML обучена на {len(X)} примерах", flush=True)
    return True


def load_ml_model():
    global ml_model, ml_initialized, ml_feature_names

    if not ML_AVAILABLE:
        return False

    model_file = "cards_model.pkl"
    if not os.path.exists(model_file):
        return False

    try:
        with open(model_file, "rb") as f:
            saved = pickle.load(f)
        ml_model = saved["model"]
        ml_feature_names = saved.get("feature_names")
        if not ml_feature_names:
            ml_feature_names = sorted(build_training_features({"latency_ms": 100, "game_number": 1}).keys())
        ml_initialized = True
        print("✅ ML модель загружена", flush=True)
        return True
    except Exception as e:
        print(f"⚠️ Ошибка загрузки ML: {e}", flush=True)
        ml_model = None
        ml_initialized = False
        return False


def predict_ml(latency, game_num):
    if not ml_initialized or ml_model is None:
        return None, {}

    try:
        features = {
            "latency": float(latency),
            "game_num": float(game_num),
            "game_num_sin": math.sin(2 * math.pi * int(game_num) / 1440),
            "game_num_cos": math.cos(2 * math.pi * int(game_num) / 1440),
        }

        if ml_feature_names:
            feature_names = ml_feature_names
        else:
            feature_names = sorted(features.keys())

        vector = np.array([[features.get(key, 0.0) for key in feature_names]])

        raw_probs = ml_model.predict_proba(vector)[0]
        class_probabilities = {}

        for class_index, probability in zip(ml_model.classes_, raw_probs):
            if 0 <= int(class_index) < len(TARGET_CARDS):
                card = TARGET_CARDS[int(class_index)]
                class_probabilities[card] = float(probability)

        sorted_cards = sorted(class_probabilities.items(), key=lambda x: x[1], reverse=True)
        return sorted_cards[:2], class_probabilities

    except Exception as e:
        print(f"⚠️ Ошибка ML прогноза: {e}", flush=True)
        return None, {}


# =====================================================================
# ИТОГОВЫЙ ПРОГНОЗ (С ПРИОРИТЕТОМ МИЛЛИСЕКУНД)
# =====================================================================

def get_prediction(latency, game_num, timestamp_msk):
    
    # 1. СНАЧАЛА ИЩЕМ ПО МИЛЛИСЕКУНДАМ (ПРИОРИТЕТ)
    ms_top, ms_probs, ms_count = predict_by_milliseconds(timestamp_msk)
    
    # 2. ПОТОМ ИСТОРИЯ
    historical_top, historical_probs, history_count = historical_prediction(latency, game_num)
    
    # 3. ПОТОМ ML
    ml_top, ml_probs = predict_ml(latency, game_num)
    
    # Если есть совпадения по миллисекундам - используем их
    if ms_top and ms_count >= 2:
        print(f"\n✅ ПРОГНОЗ ПО МИЛЛИСЕКУНДАМ (приоритет)", flush=True)
        return ms_top, "milliseconds", ms_top[0][1], ms_count, ms_top[0][0], ms_top[0][1]
    
    # Если есть исторические аналоги - используем их
    if historical_top and history_count >= 5:
        print(f"\n✅ ПРОГНОЗ ПО ИСТОРИИ", flush=True)
        return historical_top, "history", historical_top[0][1], history_count, historical_top[0][0], historical_top[0][1]
    
    # Если есть ML - используем его
    if ml_top and ml_top[0][1] >= MIN_FORECAST_PROBABILITY:
        print(f"\n✅ ПРОГНОЗ ПО ML", flush=True)
        return ml_top, "ml", ml_top[0][1], 0, ml_top[0][0], ml_top[0][1]
    
    print("⚠️ Нет данных для прогноза", flush=True)
    return None, None, None, 0, None, None


# =====================================================================
# ПРОВЕРКА ПРОГНОЗОВ
# =====================================================================

def has_prediction_for_target(target_game_num):
    for entry in predictions:
        if entry.get("target") == target_game_num:
            if entry.get("status") in ["pending", "win", "lose"]:
                return True
    return False


# =====================================================================
# СОЗДАНИЕ ПРОГНОЗА
# =====================================================================

def check_upcoming_games():
    global predictions, seen_upcoming_games

    upcoming = get_upcoming_games()
    if not upcoming:
        return

    for game in upcoming:
        scheduled_game_num = game.get("game_num")
        game_id = game.get("game_id")

        if not scheduled_game_num or not game_id:
            continue

        target_game_num = add_game_offset(scheduled_game_num, FORECAST_OFFSET)

        if game_id not in seen_upcoming_games:
            seen_upcoming_games.add(game_id)

            print(f"\n🆕 НОВАЯ ИГРА #{scheduled_game_num} появилась в лобби!", flush=True)
            print(f"⏰ До старта: {game.get('minutes_until', 0):.1f} минут", flush=True)
            print(f"🔮 Прогноз на +{FORECAST_OFFSET}: #{target_game_num}", flush=True)

            if has_prediction_for_target(target_game_num):
                print(f"⏭️ На #{target_game_num} прогноз уже существует", flush=True)
                continue

            print("📡 ЗАМЕР ЗАДЕРЖКИ...", flush=True)

            (_, measured_latency, _, _) = get_game_data(game_id)

            if measured_latency is not None:
                latency = measured_latency
                print(f"   Замер: {latency:.1f}мс", flush=True)
            else:
                latency = 500.0
                print(f"   Замер: 500.0мс (по умолчанию)", flush=True)

            update_game_history(latency, scheduled_game_num)

            # Получаем текущее время для миллисекунд
            now = datetime.now(MOSCOW_TZ)
            timestamp_msk = now.strftime("%H:%M:%S.%f")[:-3]

            (predicted_cards, method, confidence, matches_count, base_card, base_probability) = get_prediction(
                latency, target_game_num, timestamp_msk
            )

            if not predicted_cards:
                print(f"⏭️ Нет прогноза для #{target_game_num}", flush=True)
                continue

            cards_list = [card for card, _ in predicted_cards]

            msg = (
                "🔮 ТОЧНАЯ КАРТА (МИЛЛИСЕКУНДЫ)\n\n"
                f"🎯 Целевая игра: #N{target_game_num}\n"
                f"📌 От запланированной: {FORECAST_OFFSET:+d} (#{scheduled_game_num} → #{target_game_num})\n"
                "🤖 Метод: Миллисекунды + История + ML\n"
                f"📊 Задержка: {latency:.1f}мс\n"
                f"⏰ Время замера: {timestamp_msk}\n"
                f"📚 Найдено аналогов: {matches_count}\n"
                f"⏰ Прогноз: {datetime.now(MOSCOW_TZ).strftime('%H:%M:%S')}\n\n"
            )

            if base_card:
                msg += f"📊 Лидер анализа: {base_card} — {base_probability * 100:.1f}%\n\n"

            for i, card in enumerate(cards_list, 1):
                msg += f"{i}️⃣ {card}\n"

            msg += f"\n📈 Догон: {DOGON_GAMES - 1} игр"
            msg += "\n📍 Ищем: любую позицию (игрок/дилер)"

            message_id = send_message(CHANNEL_PROGNOZ, msg)

            if not message_id:
                print("❌ Не удалось отправить прогноз", flush=True)
                continue

            entry = {
                "source": scheduled_game_num,
                "target": target_game_num,
                "cards": cards_list,
                "base_card": base_card,
                "base_probability": base_probability,
                "method": method,
                "message_id": message_id,
                "channel_id": CHANNEL_PROGNOZ,
                "original_text": msg,
                "status": "pending",
                "latency": latency,
                "timestamp_msk": timestamp_msk,
                "confidence": confidence,
                "historical_matches": matches_count,
                "forecast_offset": FORECAST_OFFSET,
                "dogon_games": DOGON_GAMES,
                "created": datetime.now(MOSCOW_TZ).isoformat()
            }

            predictions.append(entry)
            if len(predictions) > 200:
                predictions = predictions[-200:]

            save_history(predictions)

            print("\n✅ ПРОГНОЗ ОТПРАВЛЕН", flush=True)
            print(f"📌 Источник: #{scheduled_game_num}", flush=True)
            print(f"🎯 Цель: #{target_game_num}", flush=True)
            print(f"🃏 Прогноз: {' + '.join(cards_list)}", flush=True)
            print(f"📢 Канал прогноза: {CHANNEL_PROGNOZ}", flush=True)


# =====================================================================
# ПРОВЕРКА РЕЗУЛЬТАТОВ
# =====================================================================

def find_stats_game_text(game_number):
    if not hasattr(find_stats_game_text, "games_cache"):
        find_stats_game_text.games_cache = {}
    return find_stats_game_text.games_cache.get(game_number)


def cache_stats_result(game_number, text):
    if not hasattr(find_stats_game_text, "games_cache"):
        find_stats_game_text.games_cache = {}
    find_stats_game_text.games_cache[game_number] = text
    
    if len(find_stats_game_text.games_cache) > 1000:
        current = get_game_number_by_time()
        sorted_items = sorted(
            find_stats_game_text.games_cache.items(),
            key=lambda x: circular_game_distance(x[0], current)
        )
        find_stats_game_text.games_cache = dict(sorted_items[:500])


def get_actual_cards_from_game(game_text):
    game_data = parse_game_from_text(game_text)
    if not game_data:
        return []

    all_cards = game_data.get("player_cards", []) + game_data.get("dealer_cards", [])
    actual_cards = []
    for card in all_cards:
        rank = card.get("rank", "")
        suit = card.get("suit", "")
        card_str = rank + suit
        if card_str:
            actual_cards.append(card_str)
    return actual_cards


def evaluate_prediction_against_game(predicted_cards, game_text):
    actual_cards = get_actual_cards_from_game(game_text)
    if not actual_cards:
        return {"matched": False, "found_card": None, "actual_cards": []}

    predicted_set = set(predicted_cards)
    for actual_card in actual_cards:
        if actual_card in predicted_set:
            return {"matched": True, "found_card": actual_card, "actual_cards": actual_cards}

    return {"matched": False, "found_card": None, "actual_cards": actual_cards}


def parse_game_from_text(text):
    try:
        game_match = re.search(r"#N(\d+)", text)
        if not game_match:
            return None
        game_number = int(game_match.group(1))

        if "◀️" in text:
            parts = text.split("◀️", 1)
        elif "▶️" in text:
            parts = text.split("▶️", 1)
        elif " - " in text:
            parts = text.split(" - ", 1)
        elif "—" in text:
            parts = text.split("—", 1)
        else:
            return None

        if len(parts) < 2:
            return None

        def parse_cards(part):
            match = re.search(r"\(([^)]*)\)", part)
            if not match:
                return []
            cards_str = match.group(1)
            cards = []
            pattern = r"(10|[2-9AJQK])([♠♣♦♥])"
            matches = re.findall(pattern, cards_str)
            for rank, suit in matches:
                suit_map = {"♠": "♠️", "♣": "♣️", "♦": "♦️", "♥": "♥️"}
                cards.append({"rank": rank, "suit": suit_map.get(suit, suit)})
            return cards

        return {
            "number": game_number,
            "player_cards": parse_cards(parts[0]),
            "dealer_cards": parse_cards(parts[1]),
            "text": text
        }
    except Exception as e:
        print(f"❌ Ошибка парсинга игры: {e}", flush=True)
        return None


def is_finished_game_text(text):
    if not text:
        return False
    return "✅" in text or "🔰" in text


def check_results():
    global predictions, stats

    if not predictions:
        return

    cache = getattr(find_stats_game_text, "games_cache", {})
    if not cache:
        return

    for entry in predictions:
        if entry.get("status") != "pending":
            continue

        target = entry.get("target")
        predicted_cards = entry.get("cards", [])
        message_id = entry.get("message_id")
        original_text = entry.get("original_text", "")

        if target is None or not predicted_cards or not message_id:
            continue

        print(f"\n🔍 ПРОВЕРКА ПРОГНОЗА", flush=True)
        print(f"🎯 Целевая игра: #{target}", flush=True)
        print(f"🃏 Ищем: {' + '.join(predicted_cards)}", flush=True)

        found = False
        found_card = None
        found_game = None
        found_dogon = None
        games_checked = []
        actual_cards_by_game = {}

        for i in range(DOGON_GAMES):
            game_to_check = add_game_offset(target, i)
            game_text = cache.get(game_to_check)

            if not game_text:
                print(f"⏳ #{game_to_check} ещё отсутствует", flush=True)
                continue

            games_checked.append(game_to_check)
            result = evaluate_prediction_against_game(predicted_cards, game_text)
            actual_cards_by_game[game_to_check] = result.get("actual_cards", [])
            print(f"🔎 #{game_to_check}: {result.get('actual_cards', [])}", flush=True)

            if result["matched"]:
                found = True
                found_card = result["found_card"]
                found_game = game_to_check
                found_dogon = i
                break

        if found:
            print(f"🎯 ПРОГНОЗ ЗАШЁЛ!", flush=True)
            stats["total"] += 1
            stats["win"] += 1
            stats["by_dogon"][found_dogon] = stats["by_dogon"].get(found_dogon, 0) + 1
            stats["ml_wins"] += 1
            stats["card_hits"][found_card] += 1

            result_text = (
                "\n\n════════════════════\n"
                f"✅ <b>ЗАШЛО</b>\n"
                "════════════════════\n"
                f"🎯 Игра: #{found_game}\n"
                f"🃏 Выпала: <b>{found_card}</b>\n"
                f"📈 Догон: <b>{found_dogon}</b>\n"
                "📊 Источник: канал статистики"
            )

            edit_message(message_id, original_text + result_text)
            entry["status"] = "win"
            entry["result_game"] = found_game
            entry["dogon"] = found_dogon
            entry["found_card"] = found_card
            entry["checked_games"] = games_checked
            entry["actual_cards_by_game"] = actual_cards_by_game
            entry["checked_at"] = datetime.now(MOSCOW_TZ).isoformat()
            save_history(predictions)
            continue

        if len(games_checked) < DOGON_GAMES:
            print(f"⏳ Прогноз #{target} ждёт результаты ({len(games_checked)}/{DOGON_GAMES})", flush=True)
            continue

        print(f"❌ ПРОГНОЗ НЕ ЗАШЁЛ", flush=True)
        stats["total"] += 1
        stats["lose"] += 1
        stats["ml_losses"] += 1

        result_text = (
            "\n\n════════════════════\n"
            f"❌ <b>НЕ ЗАШЛО</b>\n"
            "════════════════════\n"
            f"🎯 Цель: #{target}\n"
            f"🔍 Проверено игр: {DOGON_GAMES}\n"
            f"🃏 Искали: {' / '.join(predicted_cards)}\n"
            "📊 Источник: канал статистики"
        )

        edit_message(message_id, original_text + result_text)
        entry["status"] = "lose"
        entry["checked_games"] = games_checked
        entry["actual_cards_by_game"] = actual_cards_by_game
        entry["checked_at"] = datetime.now(MOSCOW_TZ).isoformat()
        save_history(predictions)


def load_old_telegram_results():
    print("\n📥 ЗАГРУЗКА СТАРЫХ РЕЗУЛЬТАТОВ", flush=True)
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
        response = requests.get(url, params={
            "limit": 100,
            "allowed_updates": json.dumps(["channel_post", "edited_channel_post"])
        }, timeout=10)

        if response.status_code != 200:
            print("⚠️ Не удалось получить старые updates", flush=True)
            return

        data = response.json()
        result_count = 0
        ignored_count = 0

        for update in data.get("result", []):
            post = update.get("channel_post") or update.get("edited_channel_post")
            if not post:
                continue

            chat = post.get("chat", {})
            chat_id = str(chat.get("id", ""))
            text = post.get("text", "")

            if chat_id != CHANNEL_STATS:
                ignored_count += 1
                continue

            if "#N" not in text or not is_finished_game_text(text):
                continue

            match = re.search(r"#N(\d+)", text)
            if not match:
                continue

            game_number = int(match.group(1))
            cache_stats_result(game_number, text)
            result_count += 1

        print(f"📊 Загружено результатов: {result_count}, игнорировано: {ignored_count}", flush=True)
    except Exception as e:
        print(f"⚠️ Ошибка загрузки старых результатов: {e}", flush=True)


def rebuild_prediction_stats():
    global stats
    stats["total"] = 0
    stats["win"] = 0
    stats["lose"] = 0
    stats["ml_wins"] = 0
    stats["ml_losses"] = 0
    stats["by_dogon"] = {0: 0, 1: 0, 2: 0, 3: 0}
    stats["card_hits"] = defaultdict(int)

    for entry in predictions:
        status = entry.get("status")
        if status == "win":
            stats["total"] += 1
            stats["win"] += 1
            dogon = entry.get("dogon", 0)
            stats["by_dogon"][dogon] = stats["by_dogon"].get(dogon, 0) + 1
            stats["ml_wins"] += 1
            found_card = entry.get("found_card")
            if found_card:
                stats["card_hits"][found_card] += 1
        elif status == "lose":
            stats["total"] += 1
            stats["lose"] += 1
            stats["ml_losses"] += 1


def process_telegram_updates(updates, offset):
    if not updates:
        return offset

    for update in updates.get("result", []):
        update_id = update.get("update_id")
        if update_id is None:
            continue

        offset = update_id + 1
        save_offset(offset)

        post = update.get("channel_post") or update.get("edited_channel_post")
        if not post:
            continue

        chat = post.get("chat", {})
        chat_id = str(chat.get("id", ""))
        text = post.get("text", "")

        if chat_id == CHANNEL_STATS and "#N" in text and is_finished_game_text(text):
            match = re.search(r"#N(\d+)", text)
            if match:
                game_number = int(match.group(1))
                cache_stats_result(game_number, text)
                print(f"💾 Сохранил результат #{game_number}", flush=True)

    return offset


def main():
    global predictions, game_history, collection_active, ml_initialized

    print("=" * 60, flush=True)
    print("🔮 ТОЧНАЯ КАРТА (МИЛЛИСЕКУНДЫ)", flush=True)
    print(f"📌 Прогноз: {FORECAST_OFFSET:+d} от запланированной игры", flush=True)
    print("📌 Приоритет: Миллисекунды → История → ML", flush=True)
    print("=" * 60, flush=True)

    existing_data = load_data()
    print(f"📊 Загружено игр: {len(existing_data)}", flush=True)

    if len(existing_data) >= MAX_RECORDS:
        collection_active = False
        print("⏸️ Сбор отключён — лимит достигнут", flush=True)

    game_history = load_game_history()
    print(f"📈 История задержек: {len(game_history)}", flush=True)

    predictions = load_history()
    if not isinstance(predictions, list):
        predictions = []
    print(f"🔮 Загружено прогнозов: {len(predictions)}", flush=True)

    load_latency_cache()

    if len(existing_data) >= MIN_TRAIN_SAMPLES:
        if not load_ml_model():
            train_ml_model()
        else:
            train_ml_model()
    else:
        print(f"⏳ ML ждёт {MIN_TRAIN_SAMPLES} игр", flush=True)

    offset = get_offset()
    print(f"📌 Telegram offset: {offset}", flush=True)

    load_old_telegram_results()

    print("=" * 60, flush=True)
    print("🚀 БОТ ГОТОВ!", flush=True)
    print("=" * 60, flush=True)

    last_upcoming_check = 0
    last_result_check = 0

    while True:
        try:
            current_time = time.time()

            if current_time - last_upcoming_check >= 10:
                check_upcoming_games()
                last_upcoming_check = current_time

            if current_time - last_result_check >= 5:
                check_results()
                last_result_check = current_time

            time.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:
            print("\n🛑 БОТ ОСТАНОВЛЕН", flush=True)
            break
        except Exception as e:
            print(f"❌ КРИТИЧЕСКАЯ ОШИБКА: {e}", flush=True)
            traceback.print_exc()
            time.sleep(30)


if __name__ == "__main__":
    main()