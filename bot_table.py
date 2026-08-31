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

MAX_RECORDS = 10000
CHECK_INTERVAL = 5
MIN_TRAIN_SAMPLES = 100
MAX_HISTORY = 2000
MAX_GAME_HISTORY = 30
DOGON_GAMES = 4
LATENCY_CACHE_MAX_SIZE = 2000

FORECAST_OFFSET = -1
MIN_FORECAST_PROBABILITY = 0.29

# НАСТРОЙКИ ДЛЯ ДВОЙНОГО ЗАМЕРА
LATENCY_MEASUREMENTS = 2  # количество замеров
LATENCY_INTERVAL = 2     # интервал между замерами (секунд)


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
# РАБОТА С ДАННЫМИ
# =====================================================================

def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
        except Exception as e:
            print(f"⚠️ Ошибка загрузки данных: {e}", flush=True)
    return []


def save_data(record):
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
# ИСТОРИЧЕСКИЙ ПОИСК (С ДВОЙНЫМ ЗАМЕРОМ)
# =====================================================================

def find_similar_historical_games(latency_1, latency_2, current_game_num):
    data = load_data()
    if not data:
        return []

    matches = []
    for record in data:
        historical_latency_1 = record.get("latency_ms_1", None)
        historical_latency_2 = record.get("latency_ms_2", None)
        historical_game_num = record.get("game_number", None)

        if historical_latency_1 is None or historical_latency_2 is None:
            continue
        if historical_game_num is None:
            continue

        try:
            historical_latency_1 = float(historical_latency_1)
            historical_latency_2 = float(historical_latency_2)
            historical_game_num = int(historical_game_num)
        except:
            continue

        cards = get_target_cards_from_record(record)
        if not cards:
            continue

        # Сравниваем ОБА замера
        latency_distance_1 = abs(latency_1 - historical_latency_1)
        latency_distance_2 = abs(latency_2 - historical_latency_2)

        # Если хоть одна задержка сильно отличается - пропускаем
        if latency_distance_1 > 150 or latency_distance_2 > 150:
            continue

        game_distance = circular_game_distance(current_game_num, historical_game_num)
        if game_distance > 10:
            continue

        # Считаем схожесть по обоим замерам
        latency_score_1 = max(0.0, 1.0 - latency_distance_1 / 150)
        latency_score_2 = max(0.0, 1.0 - latency_distance_2 / 150)
        latency_score = (latency_score_1 + latency_score_2) / 2

        game_score = max(0.0, 1.0 - game_distance / 11)
        if game_distance == 0:
            game_score += 0.75

        similarity = latency_score * 0.60 + game_score * 0.40

        matches.append({
            "record": record,
            "cards": cards,
            "latency_distance_1": latency_distance_1,
            "latency_distance_2": latency_distance_2,
            "game_distance": game_distance,
            "similarity": similarity
        })

    matches.sort(key=lambda x: x["similarity"], reverse=True)
    return matches[:300]


def historical_prediction(latency_1, latency_2, game_num):
    matches = find_similar_historical_games(latency_1, latency_2, game_num)

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

    print("\n📚 ИСТОРИЧЕСКИЙ ПОИСК (ДВОЙНОЙ ЗАМЕР)", flush=True)
    print(f"   Найдено аналогов: {len(matches)}", flush=True)
    print(f"   Замер #1: {latency_1:.1f}мс", flush=True)
    print(f"   Замер #2: {latency_2:.1f}мс", flush=True)
    for i, (card, prob) in enumerate(sorted_cards[:3], 1):
        print(f"   {i}. {card} — {prob * 100:.2f}%", flush=True)

    return sorted_cards[:2], probabilities, len(matches)


# =====================================================================
# ML ПРОГНОЗ (С ДВОЙНЫМ ЗАМЕРОМ)
# =====================================================================

def build_training_features_double(record):
    latency_1 = record.get("latency_ms_1", 0)
    latency_2 = record.get("latency_ms_2", 0)
    game_num = record.get("game_number", 0)

    if not game_num:
        return None

    features = {
        "latency_1": float(latency_1),
        "latency_2": float(latency_2),
        "latency_diff": float(latency_2 - latency_1),
        "latency_ratio": float(latency_2 / latency_1 if latency_1 > 0 else 1),
        "game_num": float(game_num),
        "game_num_sin": math.sin(2 * math.pi * int(game_num) / 1440),
        "game_num_cos": math.cos(2 * math.pi * int(game_num) / 1440),
    }
    return features


def train_ml_model_double():
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
        features = build_training_features_double(record)
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


def load_ml_model_double():
    global ml_model, ml_initialized, ml_feature_names

    if not ML_AVAILABLE:
        return False

    model_file = "cards_model_double.pkl"
    if not os.path.exists(model_file):
        return False

    try:
        with open(model_file, "rb") as f:
            saved = pickle.load(f)
        ml_model = saved["model"]
        ml_feature_names = saved.get("feature_names")
        if not ml_feature_names:
            ml_feature_names = sorted(build_training_features_double({"latency_ms_1": 100, "latency_ms_2": 100, "game_number": 1}).keys())
        ml_initialized = True
        print("✅ ML модель загружена", flush=True)
        return True
    except Exception as e:
        print(f"⚠️ Ошибка загрузки ML: {e}", flush=True)
        ml_model = None
        ml_initialized = False
        return False


def predict_ml_double(latency_1, latency_2, game_num):
    if not ml_initialized or ml_model is None:
        return None, {}

    try:
        features = {
            "latency_1": float(latency_1),
            "latency_2": float(latency_2),
            "latency_diff": float(latency_2 - latency_1),
            "latency_ratio": float(latency_2 / latency_1 if latency_1 > 0 else 1),
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
# ИТОГОВЫЙ ПРОГНОЗ (ДВОЙНОЙ ЗАМЕР)
# =====================================================================

def get_prediction_double(latency_1, latency_2, game_num):
    historical_top, historical_probs, matches_count = historical_prediction(latency_1, latency_2, game_num)
    ml_top, ml_probs = predict_ml_double(latency_1, latency_2, game_num)

    print("\n🤖 ML ПРОГНОЗ (ДВОЙНОЙ ЗАМЕР)", flush=True)
    if ml_top:
        for i, (card, prob) in enumerate(ml_top, 1):
            print(f"   {i}. {card} — {prob * 100:.2f}%", flush=True)
    else:
        print("   ML пока недоступна", flush=True)

    combined_scores = defaultdict(float)

    HISTORICAL_WEIGHT = 0.65
    ML_WEIGHT = 0.35

    for card, prob in historical_probs.items():
        combined_scores[card] += prob * HISTORICAL_WEIGHT

    for card, prob in ml_probs.items():
        combined_scores[card] += prob * ML_WEIGHT

    if not ml_probs and historical_probs:
        combined_scores = defaultdict(float, historical_probs)
    if not historical_probs and ml_probs:
        combined_scores = defaultdict(float, ml_probs)

    if not combined_scores:
        print("⚠️ Нет данных для прогноза", flush=True)
        return None, None, None, matches_count, None, None

    total = sum(combined_scores.values())
    if total <= 0:
        return None, None, None, matches_count, None, None

    normalized_probs = {}
    for card, score in combined_scores.items():
        normalized_probs[card] = score / total

    normalized = sorted(normalized_probs.items(), key=lambda x: x[1], reverse=True)

    if len(normalized) < 1:
        return None, None, None, matches_count, None, None

    top_card_1, top_prob_1 = normalized[0]

    print(f"\n🔥 ЛИДЕР АНАЛИЗА: {top_card_1} — {top_prob_1 * 100:.2f}%", flush=True)

    if len(normalized) >= 2:
        top_card_2, top_prob_2 = normalized[1]
        print(f"   2. {top_card_2} — {top_prob_2 * 100:.2f}%", flush=True)

        # Если проценты равны и оба выше минимума
        if math.isclose(top_prob_1, top_prob_2, rel_tol=1e-9, abs_tol=1e-9):
            if top_prob_1 >= MIN_FORECAST_PROBABILITY and top_prob_2 >= MIN_FORECAST_PROBABILITY:
                print(f"✅ ОБЕ КАРТЫ ВЫШЕ МИНИМУМА ({MIN_FORECAST_PROBABILITY * 100:.0f}%)", flush=True)
                result_cards = [(top_card_1, top_prob_1), (top_card_2, top_prob_2)]
                return result_cards, "history+ml+double", top_prob_1, matches_count, top_card_1, top_prob_1

    if top_prob_1 < MIN_FORECAST_PROBABILITY:
        print(f"⛔ ЛИДЕР НИЖЕ МИНИМУМА ({MIN_FORECAST_PROBABILITY * 100:.0f}%)", flush=True)
        return None, None, None, matches_count, top_card_1, top_prob_1

    result_cards = [(top_card_1, top_prob_1)]
    return result_cards, "history+ml+double", top_prob_1, matches_count, top_card_1, top_prob_1


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
# СОЗДАНИЕ ПРОГНОЗА (С ДВОЙНЫМ ЗАМЕРОМ)
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

            # =====================================================
            # ДЕЛАЕМ ДВОЙНОЙ ЗАМЕР
            # =====================================================
            print("📡 ДВОЙНОЙ ЗАМЕР ЗАДЕРЖКИ...", flush=True)

            latencies = []
            for i in range(LATENCY_MEASUREMENTS):
                (_, measured_latency, _, _) = get_game_data(game_id)

                if measured_latency is not None:
                    latencies.append(measured_latency)
                    cache_game_latency(game_id, measured_latency, scheduled_game_num, i + 1)
                    print(f"   Замер #{i + 1}: {measured_latency:.1f}мс", flush=True)
                else:
                    latencies.append(500.0)
                    print(f"   Замер #{i + 1}: 500.0мс (по умолчанию)", flush=True)

                if i < LATENCY_MEASUREMENTS - 1:
                    time.sleep(LATENCY_INTERVAL)

            latency_1 = latencies[0] if len(latencies) > 0 else 500.0
            latency_2 = latencies[1] if len(latencies) > 1 else 500.0

            update_game_history(latency_1, scheduled_game_num)
            update_game_history(latency_2, scheduled_game_num)

            # =====================================================
            # ПРОГНОЗ НА ОСНОВЕ ДВОЙНОГО ЗАМЕРА
            # =====================================================
            (predicted_cards, method, confidence, matches_count, base_card, base_probability) = get_prediction_double(
                latency_1, latency_2, target_game_num
            )

            if not predicted_cards:
                print(f"⏭️ Нет прогноза для #{target_game_num}", flush=True)
                continue

            cards_list = [card for card, _ in predicted_cards]

            # =====================================================
            # ФОРМИРУЕМ СООБЩЕНИЕ
            # =====================================================
            msg = (
                "🔮 ТОЧНАЯ КАРТА (ДВОЙНОЙ ЗАМЕР)\n\n"
                f"🎯 Целевая игра: #N{target_game_num}\n"
                f"📌 От запланированной: {FORECAST_OFFSET:+d} (#{scheduled_game_num} → #{target_game_num})\n"
                "🤖 Метод: История + ML (двойной замер)\n"
                f"📊 Замер #1: {latency_1:.1f}мс\n"
                f"📊 Замер #2: {latency_2:.1f}мс\n"
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

            # =====================================================
            # СОХРАНЯЕМ ПРОГНОЗ
            # =====================================================
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
                "latency_1": latency_1,
                "latency_2": latency_2,
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
# MAIN
# =====================================================================

def main():
    global predictions, game_history, collection_active, ml_initialized

    print("=" * 60, flush=True)
    print("🔮 ТОЧНАЯ КАРТА (ДВОЙНОЙ ЗАМЕР)", flush=True)
    print(f"📌 Прогноз: {FORECAST_OFFSET:+d} от запланированной игры", flush=True)
    print(f"📌 Количество замеров: {LATENCY_MEASUREMENTS}", flush=True)
    print(f"📌 Интервал между замерами: {LATENCY_INTERVAL} сек", flush=True)
    print("=" * 60, flush=True)

    # Загружаем данные
    existing_data = load_data()
    print(f"📊 Уже собрано игр: {len(existing_data)}", flush=True)

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

    # ML
    if len(existing_data) >= MIN_TRAIN_SAMPLES:
        if not load_ml_model_double():
            train_ml_model_double()
        else:
            train_ml_model_double()
    else:
        print(f"⏳ ML ждёт {MIN_TRAIN_SAMPLES} игр", flush=True)

    offset = get_offset()
    print(f"📌 Telegram offset: {offset}", flush=True)

    print("=" * 60, flush=True)
    print("🚀 БОТ ГОТОВ!", flush=True)
    print("=" * 60, flush=True)

    last_upcoming_check = 0

    while True:
        try:
            current_time = time.time()

            if current_time - last_upcoming_check >= 10:
                check_upcoming_games()
                last_upcoming_check = current_time

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