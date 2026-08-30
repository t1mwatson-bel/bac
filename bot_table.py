```python
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
                importlib.import_module(
                    package.replace("-", "_")
                )

                print(
                    f"✅ {package} - уже установлен",
                    flush=True
                )

            except ImportError:
                print(
                    f"⚠️ {package} - НЕ НАЙДЕН",
                    flush=True
                )

                missing.append(package)

        if missing:
            print(
                f"\n📦 Нужно установить: {', '.join(missing)}",
                flush=True
            )

            for package in missing:
                if not install_package(package):
                    print(
                        f"❌ Не удалось установить {package}",
                        flush=True
                    )
                    return False

            print(
                "\n✅ ВСЕ ЗАВИСИМОСТИ УСТАНОВЛЕНЫ!",
                flush=True
            )

        else:
            print(
                "\n✅ ВСЕ ЗАВИСИМОСТИ УСТАНОВЛЕНЫ!",
                flush=True
            )

        print("=" * 60, flush=True)

        return True


    if not check_and_install_dependencies():
        print(
            "❌ ОШИБКА: Невозможно продолжить работу",
            flush=True
        )
        sys.exit(1)

except Exception as e:
    print(
        f"⚠️ Ошибка при проверке зависимостей: {e}",
        flush=True
    )


# =====================================================================
# ML-БИБЛИОТЕКА
# =====================================================================

ML_AVAILABLE = False
ML_LIB = None

try:
    from sklearn.ensemble import RandomForestClassifier

    ML_AVAILABLE = True
    ML_LIB = "randomforest"

    print(
        "✅ RandomForest загружен!",
        flush=True
    )

except ImportError:
    print(
        "⚠️ RandomForest не установлен. Работаем без ML.",
        flush=True
    )


# =====================================================================
# ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ
# =====================================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    BOT_TOKEN = os.getenv("BOT_TOKEN_PROGNOZ")

CHANNEL_STATS = os.getenv("CHANNEL_STATS")
CHANNEL_PROGNOZ = os.getenv("CHANNEL_PROGNOZ")

print("=" * 60, flush=True)
print("🔍 ДИАГНОСТИКА ПЕРЕМЕННЫХ:", flush=True)

print(
    f"BOT_TOKEN: "
    f"{BOT_TOKEN[:5] if BOT_TOKEN else 'НЕ ЗАДАН'}...",
    flush=True
)

print(
    f"CHANNEL_STATS: "
    f"{CHANNEL_STATS if CHANNEL_STATS else 'НЕ ЗАДАН'}",
    flush=True
)

print(
    f"CHANNEL_PROGNOZ: "
    f"{CHANNEL_PROGNOZ if CHANNEL_PROGNOZ else 'НЕ ЗАДАН'}",
    flush=True
)

print("=" * 60, flush=True)

if not BOT_TOKEN or not CHANNEL_STATS or not CHANNEL_PROGNOZ:
    print(
        "❌ ОШИБКА: переменные окружения не заданы!",
        flush=True
    )
    sys.exit(1)


# =====================================================================
# НАСТРОЙКИ БАККАРА
# =====================================================================

MOSCOW_TZ = pytz.timezone("Europe/Moscow")

BASE_URL = "https://1xlite-0687.pro"

BACCARAT_LEAGUE_ID = 2050671

DATA_FILE = "baccarat_data.json"
HISTORY_FILE = "baccarat_history.json"
ML_MODEL_FILE = "baccarat_suit_model.pkl"
OFFSET_FILE = "baccarat_offset.txt"
GAME_HISTORY_FILE = "baccarat_game_history.json"

MAX_RECORDS = 10000

CHECK_INTERVAL = 5

OFFSET = 1

MIN_TRAIN_SAMPLES = 300

MAX_HISTORY = 2000

MAX_GAME_HISTORY = 10

DOGON_GAMES = 4

# ============================================================
# Порог теперь 25%
# ============================================================

ML_CONFIDENCE_THRESHOLD = 0.25


# =====================================================================
# ЦЕЛЬ ML — ТОЛЬКО МАСТИ
# =====================================================================

TARGET_SUITS = [
    "♠️",
    "♣️",
    "♦️",
    "♥️"
]

SUITS = [
    "♠️",
    "♣️",
    "♦️",
    "♥️"
]

SUITS_NAMES = {
    0: "♠️",
    1: "♣️",
    2: "♦️",
    3: "♥️"
}

SUIT_NAMES_RU = {
    "♠️": "Пики",
    "♣️": "Крести",
    "♦️": "Бубны",
    "♥️": "Червы"
}


# =====================================================================
# API БАККАРА
# =====================================================================

GAMES_URL = (
    f"{BASE_URL}/service-api/main-live-feed/v3/games1x2"
    "?cfView=3"
    "&count=40"
    "&fcountry=190"
    "&gr=415"
    "&grMode=4"
    "&lng=ru"
    "&ref=7"
    f"&selectedMs=1.236.{BACCARAT_LEAGUE_ID},"
    f"10.236.{BACCARAT_LEAGUE_ID}"
)


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),

    "Accept": (
        "application/json, text/plain, */*"
    ),

    "Referer": f"{BASE_URL}/",

    "Cookie": (
        "platform_type=desktop; "
        "lng=ru; "
        "cookies_agree_type=3; "
        "tzo=3; "
        "is12h=0"
    )
}


# =====================================================================
# КАРТЫ
# =====================================================================

RANK_VALUES = {
    "A": 14,
    "2": 2,
    "3": 3,
    "4": 4,
    "5": 5,
    "6": 6,
    "7": 7,
    "8": 8,
    "9": 9,
    "10": 10,
    "J": 11,
    "Q": 12,
    "K": 13
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
    13: "K",
    14: "A"
}


# =====================================================================
# ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ
# =====================================================================

ml_model = None

ml_initialized = False

collection_active = True

game_history = deque(
    maxlen=MAX_GAME_HISTORY
)


stats = {
    "total": 0,
    "win": 0,
    "lose": 0,

    "by_dogon": {
        0: 0,
        1: 0,
        2: 0,
        3: 0
    },

    "ml_wins": 0,
    "ml_losses": 0,

    "games_collected": 0,

    "last_report": time.time(),

    "suit_hits": defaultdict(int)
}


processed_games = set()

finished_games = set()

all_messages = []

predictions = []


# =====================================================================
# TELEGRAM
# =====================================================================

def get_updates(offset):
    url = (
        f"https://api.telegram.org/"
        f"bot{BOT_TOKEN}/getUpdates"
    )

    params = {
        "offset": offset,
        "timeout": 30
    }

    try:
        response = requests.get(
            url,
            params=params,
            timeout=35
        )

        return response.json()

    except Exception as e:
        print(
            f"❌ Ошибка getUpdates: {e}",
            flush=True
        )

        return {}


def send_message(chat_id, text):
    url = (
        f"https://api.telegram.org/"
        f"bot{BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML"
    }

    try:
        response = requests.post(
            url,
            json=payload,
            timeout=10
        )

        if response.status_code == 200:
            return response.json()["result"]["message_id"]

        print(
            f"❌ Ошибка отправки: "
            f"{response.status_code}",
            flush=True
        )

        return None

    except Exception as e:
        print(
            f"❌ Ошибка отправки: {e}",
            flush=True
        )

        return None


def edit_message(message_id, text):
    url = (
        f"https://api.telegram.org/"
        f"bot{BOT_TOKEN}/editMessageText"
    )

    payload = {
        "chat_id": CHANNEL_PROGNOZ,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML"
    }

    try:
        response = requests.post(
            url,
            json=payload,
            timeout=10
        )

        return response.status_code == 200

    except Exception as e:
        print(
            f"❌ Ошибка редактирования: {e}",
            flush=True
        )

        return False


def send_startup_message():
    data_count = len(load_data())

    now = datetime.now(MOSCOW_TZ)

    msg = f"""
🃏 МАСТЬ ИГРОКА (ML ТОП-2)

🎰 Игра: БАККАРА
🎯 Прогноз: масть игрока
📊 Собрано игр: {data_count}/{MAX_RECORDS}
🧠 ML: {'✅ АКТИВНА' if ml_initialized else '⏳ ОЖИДАЕТ'}
🎯 Смещение: +{OFFSET} игр
📈 Догон: {DOGON_GAMES - 1} игр
⚡️ Порог ML: {ML_CONFIDENCE_THRESHOLD * 100:.0f}%
🎴 Вариантов масти: {len(TARGET_SUITS)}
⏰ {now.strftime('%d.%m.%Y %H:%M:%S')}
"""

    send_message(
        CHANNEL_PROGNOZ,
        msg
    )

    print(
        "🚀 БОТ БАККАРА ЗАПУЩЕН!",
        flush=True
    )


# =====================================================================
# ДАННЫЕ
# =====================================================================

def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(
                DATA_FILE,
                "r",
                encoding="utf-8"
            ) as f:
                return json.load(f)

        except Exception:
            return []

    return []


def save_data(record):
    global collection_active, stats

    data = load_data()

    if len(data) >= MAX_RECORDS:
        collection_active = False
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

    if len(data) >= MAX_RECORDS and collection_active:
        collection_active = False

        print(
            f"⏸️ СБОР ДАННЫХ ОСТАНОВЛЕН! "
            f"Достигнут лимит {MAX_RECORDS}",
            flush=True
        )

    with open(
        DATA_FILE,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            data,
            f,
            indent=2,
            ensure_ascii=False
        )

    return data


def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(
                HISTORY_FILE,
                "r",
                encoding="utf-8"
            ) as f:
                return json.load(f)

        except Exception:
            return []

    return []


def save_history(history):
    if len(history) > MAX_HISTORY:
        history = history[-MAX_HISTORY:]

    with open(
        HISTORY_FILE,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            history,
            f,
            indent=2,
            ensure_ascii=False
        )


def load_game_history():
    if os.path.exists(GAME_HISTORY_FILE):
        try:
            with open(
                GAME_HISTORY_FILE,
                "r",
                encoding="utf-8"
            ) as f:

                data = json.load(f)

                return deque(
                    data,
                    maxlen=MAX_GAME_HISTORY
                )

        except Exception:
            return deque(
                maxlen=MAX_GAME_HISTORY
            )

    return deque(
        maxlen=MAX_GAME_HISTORY
    )


def save_game_history():
    try:
        with open(
            GAME_HISTORY_FILE,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                list(game_history),
                f,
                indent=2,
                ensure_ascii=False
            )

    except Exception:
        pass


def get_offset():
    if os.path.exists(OFFSET_FILE):
        try:
            with open(
                OFFSET_FILE,
                "r"
            ) as f:
                return int(
                    f.read().strip()
                )

        except Exception:
            return 0

    return 0


def save_offset(offset):
    with open(
        OFFSET_FILE,
        "w"
    ) as f:

        f.write(
            str(offset)
        )


# =====================================================================
# API БАККАРА
# =====================================================================

def get_active_games():
    try:
        response = requests.get(
            GAMES_URL,
            headers=HEADERS,
            timeout=10
        )

        if response.status_code != 200:
            print(
                f"⚠️ API Баккара: "
                f"HTTP {response.status_code}",
                flush=True
            )

            return []

        data = response.json()

        if isinstance(data, dict):
            games = data.get(
                "Value",
                []
            )

        elif isinstance(data, list):
            games = data

        else:
            return []

        active_games = []

        for game in games:

            league_id = (
                game
                .get("liga", {})
                .get("id")
            )

            if league_id != BACCARAT_LEAGUE_ID:
                continue

            game_id = game.get("id")

            if game_id:
                active_games.append(game)

        return active_games

    except Exception as e:
        print(
            f"❌ Ошибка API Баккара: {e}",
            flush=True
        )

        return []


def get_game_data(game_id):
    url = (
        f"{BASE_URL}/service-api/LiveFeed/GetGameZip"
        f"?id={game_id}"
        f"&isSubGames=true"
        f"&GroupEvents=true"
        f"&countevents=250"
        f"&grMode=4"
        f"&partner=7"
        f"&topGroups="
        f"&country=190"
        f"&marketType=1"
        f"&isNewBuilder=true"
    )

    try:
        start_time = time.time()

        response = requests.get(
            url,
            headers=HEADERS,
            timeout=5
        )

        end_time = time.time()

        latency = (
            end_time - start_time
        ) * 1000

        if response.status_code == 200:
            return (
                response.json(),
                latency,
                start_time,
                end_time
            )

        return (
            None,
            None,
            None,
            None
        )

    except Exception as e:
        print(
            f"❌ Ошибка игры {game_id}: {e}",
            flush=True
        )

        return (
            None,
            None,
            None,
            None
        )


# =====================================================================
# ПАРСИНГ КАРТ БАККАРА
# =====================================================================

def normalize_suit(suit):
    if suit in SUITS:
        return suit

    if suit in ["♠", "♣", "♦", "♥"]:
        return {
            "♠": "♠️",
            "♣": "♣️",
            "♦": "♦️",
            "♥": "♥️"
        }[suit]

    return "?"


def convert_api_card(card):
    if not isinstance(card, dict):
        return {
            "rank": "?",
            "suit": "?"
        }

    rank_id = (
        card.get("CV")
        if "CV" in card
        else card.get("R")
    )

    suit_id = (
        card.get("CS")
        if "CS" in card
        else card.get("S")
    )

    rank = RANKS.get(
        rank_id,
        str(rank_id) if rank_id is not None else "?"
    )

    suit = SUITS_NAMES.get(
        suit_id,
        normalize_suit(suit_id)
    )

    return {
        "rank": rank,
        "suit": suit
    }


def parse_cards_and_state(data):
    """
    Парсинг Баккара.

    Основной вариант:
        SC -> S -> P1 / P2 / STATE

    Резервный вариант:
        scores -> statistic -> main -> P / B
    """

    if not data or not isinstance(data, dict):
        return [], [], None

    # ==========================================================
    # ВАРИАНТ 1 — GetGameZip / SC
    # ==========================================================

    value = data.get("Value", {})

    if isinstance(value, dict):

        sc = value.get("SC", {})

        if isinstance(sc, dict):

            player_cards_raw = []
            banker_cards_raw = []
            state = None

            for item in sc.get("S", []):

                if not isinstance(item, dict):
                    continue

                key = item.get("Key")
                item_value = item.get(
                    "Value",
                    ""
                )

                if key == "P1":
                    try:
                        player_cards_raw = json.loads(
                            item_value
                        )
                    except Exception:
                        player_cards_raw = []

                elif key == "P2":
                    try:
                        banker_cards_raw = json.loads(
                            item_value
                        )
                    except Exception:
                        banker_cards_raw = []

                elif key == "STATE":
                    state = item_value

            if player_cards_raw or banker_cards_raw:

                player_cards = [
                    convert_api_card(card)
                    for card in player_cards_raw
                ]

                banker_cards = [
                    convert_api_card(card)
                    for card in banker_cards_raw
                ]

                return (
                    player_cards,
                    banker_cards,
                    state
                )

    # ==========================================================
    # ВАРИАНТ 2 — scores/statistic/main
    # ==========================================================

    scores = data.get(
        "scores",
        {}
    )

    statistic = scores.get(
        "statistic",
        {}
    )

    main = statistic.get(
        "main",
        {}
    )

    if isinstance(main, dict):

        player_raw = main.get(
            "P",
            "[]"
        )

        banker_raw = main.get(
            "B",
            "[]"
        )

        state = main.get(
            "S"
        )

        try:
            player_cards_raw = json.loads(
                player_raw
            )
        except Exception:
            player_cards_raw = []

        try:
            banker_cards_raw = json.loads(
                banker_raw
            )
        except Exception:
            banker_cards_raw = []

        player_cards = [
            convert_api_card(card)
            for card in player_cards_raw
        ]

        banker_cards = [
            convert_api_card(card)
            for card in banker_cards_raw
        ]

        return (
            player_cards,
            banker_cards,
            state
        )

    return [], [], None


# =====================================================================
# НОМЕР ИГРЫ
# =====================================================================

def get_game_number_by_time():
    now = datetime.now(
        MOSCOW_TZ
    )

    start = now.replace(
        hour=3,
        minute=0,
        second=0,
        microsecond=0
    )

    if now < start:
        start = start - timedelta(
            days=1
        )

    diff_minutes = (
        now - start
    ).total_seconds() / 60

    game_number = (
        int(diff_minutes) % 1440
    ) + 1

    return game_number


# =====================================================================
# ИСТОРИЯ ИГР
# =====================================================================

def update_game_history(
    latency,
    player_cards,
    banker_cards,
    game_num
):
    global game_history

    player_suits = []

    for card in player_cards:

        suit = card.get(
            "suit",
            ""
        )

        if suit in SUITS:
            player_suits.append(suit)

    banker_suits = []

    for card in banker_cards:

        suit = card.get(
            "suit",
            ""
        )

        if suit in SUITS:
            banker_suits.append(suit)

    game_history.append({
        "latency": latency,
        "player_suits": player_suits,
        "banker_suits": banker_suits,
        "game_num": game_num,
        "timestamp": datetime.now(
            MOSCOW_TZ
        ).isoformat()
    })

    save_game_history()


def get_history_features():
    features = {}

    # ==========================================================
    # Предыдущая задержка
    # ==========================================================

    if len(game_history) >= 2:

        latencies = [
            g.get("latency", 0)
            for g in game_history
        ]

        features["prev_latency"] = (
            latencies[-2]
        )

        features["latency_delta"] = (
            latencies[-1]
            -
            latencies[-2]
        )

        if len(latencies) >= 5:

            recent = latencies[-5:]

            features["latency_trend"] = (
                recent[-1]
                -
                recent[0]
            ) / 5

    # ==========================================================
    # Последняя масть игрока
    # ==========================================================

    if game_history:

        previous_player_suits = (
            game_history[-1]
            .get(
                "player_suits",
                []
            )
        )

        if previous_player_suits:

            last_suit = (
                previous_player_suits[-1]
            )

            if last_suit in TARGET_SUITS:

                features["prev_suit"] = (
                    TARGET_SUITS.index(
                        last_suit
                    )
                )

    now = datetime.now(
        MOSCOW_TZ
    )

    features["hour"] = now.hour

    features["minute"] = now.minute

    features["day_of_week"] = (
        now.weekday()
    )

    features["is_weekend"] = (
        1
        if now.weekday() >= 5
        else 0
    )

    return features


# =====================================================================
# ML FEATURES
# =====================================================================

def extract_features_from_game(
    game_data,
    latency,
    game_num
):

    if not game_data:
        return None

    player_cards = game_data.get(
        "player_cards",
        []
    )

    banker_cards = game_data.get(
        "banker_cards",
        []
    )

    features = {
        "latency": latency or 0,

        "game_num": game_num % 100,

        "p1_rank_val": 0,
        "p1_suit": -1,

        "p2_rank_val": 0,
        "p2_suit": -1,

        "p3_rank_val": 0,
        "p3_suit": -1,

        "b1_rank_val": 0,
        "b1_suit": -1,

        "b2_rank_val": 0,
        "b2_suit": -1,

        "player_total": 0,
        "banker_total": 0,

        "player_count": len(
            player_cards
        ),

        "banker_count": len(
            banker_cards
        ),

        "prev_latency": 0,
        "latency_delta": 0,
        "latency_trend": 0,

        "prev_suit": -1,

        "hour": 0,
        "minute": 0,
        "day_of_week": 0,
        "is_weekend": 0,
    }

    # ==========================================================
    # PLAYER
    # ==========================================================

    for i, card in enumerate(
        player_cards[:3]
    ):

        rank = card.get(
            "rank",
            ""
        )

        suit = card.get(
            "suit",
            ""
        )

        if rank in RANK_VALUES:

            features[
                f"p{i + 1}_rank_val"
            ] = RANK_VALUES[rank]

        if suit in SUITS:

            features[
                f"p{i + 1}_suit"
            ] = SUITS.index(suit)

    # ==========================================================
    # BANKER
    # ==========================================================

    for i, card in enumerate(
        banker_cards[:2]
    ):

        rank = card.get(
            "rank",
            ""
        )

        suit = card.get(
            "suit",
            ""
        )

        if rank in RANK_VALUES:

            features[
                f"b{i + 1}_rank_val"
            ] = RANK_VALUES[rank]

        if suit in SUITS:

            features[
                f"b{i + 1}_suit"
            ] = SUITS.index(suit)

    # ==========================================================
    # ИСТОРИЯ
    # ==========================================================

    history_features = (
        get_history_features()
    )

    for key, value in history_features.items():

        if key in features:
            features[key] = value

    # ==========================================================
    # БАККАРА: СУММА
    # ==========================================================

    player_total = 0

    for card in player_cards:

        rank = card.get(
            "rank",
            ""
        )

        if rank in RANK_VALUES:

            value = RANK_VALUES[rank]

            if value >= 10:
                value = 0

            elif value == 14:
                value = 1

            player_total += value

    features["player_total"] = (
        player_total % 10
    )

    banker_total = 0

    for card in banker_cards:

        rank = card.get(
            "rank",
            ""
        )

        if rank in RANK_VALUES:

            value = RANK_VALUES[rank]

            if value >= 10:
                value = 0

            elif value == 14:
                value = 1

            banker_total += value

    features["banker_total"] = (
        banker_total % 10
    )

    return features


# =====================================================================
# ОБУЧЕНИЕ ML
# =====================================================================

def train_ml_model():
    global ml_model
    global ml_initialized

    if not ML_AVAILABLE:
        return False

    data = load_data()

    if len(data) < MIN_TRAIN_SAMPLES:

        print(
            f"⚠️ ML: недостаточно данных "
            f"({len(data)}/{MIN_TRAIN_SAMPLES})",
            flush=True
        )

        return False

    X = []
    y = []

    feature_names = None

    print(
        f"🧠 ML: обучение на {len(data)} играх...",
        flush=True
    )

    for game in data:

        player_cards = game.get(
            "player_cards",
            []
        )

        # ==========================================================
        # Для обучения нужна карта PLAYER
        # ==========================================================

        if not player_cards:
            continue

        first_player_card = (
            player_cards[0]
        )

        target_suit = first_player_card.get(
            "suit",
            ""
        )

        if target_suit not in TARGET_SUITS:
            continue

        features = extract_features_from_game(
            game,
            game.get("latency_ms", 0),
            0
        )

        if not features:
            continue

        feature_vector = []

        sorted_keys = sorted(
            features.keys()
        )

        if feature_names is None:
            feature_names = sorted_keys

        for key in sorted_keys:
            feature_vector.append(
                features[key]
            )

        X.append(
            feature_vector
        )

        y.append(
            TARGET_SUITS.index(
                target_suit
            )
        )

    if len(X) < MIN_TRAIN_SAMPLES:

        print(
            f"⚠️ ML: недостаточно "
            f"примеров ({len(X)}/{MIN_TRAIN_SAMPLES})",
            flush=True
        )

        return False

    print(
        f"🧠 ML: обучение на "
        f"{len(X)} примерах "
        f"из {len(data)} игр...",
        flush=True
    )

    print(
        f"📊 Признаков: {len(feature_names)}",
        flush=True
    )

    X = np.array(X)

    y = np.array(y)

    if ML_LIB == "randomforest":

        model = RandomForestClassifier(
            n_estimators=300,
            max_depth=12,
            random_state=42,
            n_jobs=1,
            class_weight="balanced"
        )

    else:
        return False

    model.fit(
        X,
        y
    )

    ml_model = model

    ml_initialized = True

    try:

        with open(
            ML_MODEL_FILE,
            "wb"
        ) as f:

            pickle.dump(
                {
                    "model": model,
                    "feature_count": len(
                        X[0]
                    ),
                    "train_samples": len(X),
                    "total_games": len(data),
                    "feature_names": feature_names,
                    "target_type": "PLAYER_SUIT"
                },
                f
            )

        print(
            f"✅ Модель мастей сохранена! "
            f"Обучено на {len(X)} примерах",
            flush=True
        )

        return True

    except Exception as e:

        print(
            f"⚠️ Ошибка сохранения модели: {e}",
            flush=True
        )

        return False


# =====================================================================
# ЗАГРУЗКА ML
# =====================================================================

def load_ml_model():
    global ml_model
    global ml_initialized

    if not ML_AVAILABLE:
        return False

    if not os.path.exists(
        ML_MODEL_FILE
    ):
        return False

    try:

        with open(
            ML_MODEL_FILE,
            "rb"
        ) as f:

            data = pickle.load(f)

            # ======================================================
            # Защита от старой модели 21
            # ======================================================

            if data.get(
                "target_type"
            ) != "PLAYER_SUIT":

                print(
                    "⚠️ Найдена старая модель. "
                    "Она не используется для Баккара.",
                    flush=True
                )

                return False

            ml_model = data["model"]

            ml_initialized = True

            print(
                f"✅ ML модель мастей загружена "
                f"({data.get('train_samples', 0)} примеров)",
                flush=True
            )

            return True

    except Exception as e:

        print(
            f"⚠️ Не удалось загрузить ML модель: {e}",
            flush=True
        )

        return False


# =====================================================================
# ML ПРОГНОЗ
# =====================================================================

def predict_ml(features):
    global ml_model
    global ml_initialized

    if not ml_initialized or ml_model is None:
        return None, None

    try:

        feature_vector = []

        for key in sorted(
            features.keys()
        ):

            feature_vector.append(
                features[key]
            )

        feature_vector = np.array(
            [feature_vector]
        )

        probs = ml_model.predict_proba(
            feature_vector
        )[0]

        classes = ml_model.classes_

        available = []

        for class_index, probability in zip(
            classes,
            probs
        ):

            class_index = int(
                class_index
            )

            if 0 <= class_index < len(
                TARGET_SUITS
            ):

                available.append(
                    (
                        TARGET_SUITS[class_index],
                        probability
                    )
                )

        available.sort(
            key=lambda x: x[1],
            reverse=True
        )

        top_suits = available[:2]

        if not top_suits:
            return None, None

        confidence = (
            top_suits[0][1]
        )

        return (
            top_suits,
            confidence
        )

    except Exception as e:

        print(
            f"⚠️ Ошибка ML-прогноза: {e}",
            flush=True
        )

        return None, None


# =====================================================================
# ПОЛУЧЕНИЕ ПРОГНОЗА
# =====================================================================

def get_prediction(
    latency,
    current_game_data
):

    if not ml_initialized:

        print(
            "⏳ ML модель не инициализирована",
            flush=True
        )

        return None, None, None

    if not current_game_data:

        print(
            "⏳ Нет данных о текущей игре",
            flush=True
        )

        return None, None, None

    features = extract_features_from_game(
        current_game_data,
        latency,
        0
    )

    if not features:

        print(
            "⏳ Не удалось извлечь признаки",
            flush=True
        )

        return None, None, None

    ml_suits, confidence = predict_ml(
        features
    )

    if ml_suits and confidence is not None:

        print(
            "📊 ML: ТОП-2 МАСТИ ИГРОКА:",
            flush=True
        )

        for i, (
            suit,
            probability
        ) in enumerate(
            ml_suits,
            1
        ):

            print(
                f"   {i}. "
                f"{suit} "
                f"({SUIT_NAMES_RU.get(suit, '')}) "
                f"— {probability * 100:.1f}%",
                flush=True
            )

        print(
            f"   Максимальная уверенность: "
            f"{confidence * 100:.1f}%",
            flush=True
        )

        print(
            f"   Порог: "
            f"{ML_CONFIDENCE_THRESHOLD * 100:.0f}%",
            flush=True
        )

        if confidence >= ML_CONFIDENCE_THRESHOLD:

            print(
                f"✅ Уверенность "
                f"{confidence * 100:.1f}% >= "
                f"{ML_CONFIDENCE_THRESHOLD * 100:.0f}% "
                f"→ ДАЮ ПРОГНОЗ!",
                flush=True
            )

            return (
                ml_suits,
                "ml",
                confidence
            )

        print(
            f"⏭️ Уверенность "
            f"{confidence * 100:.1f}% < "
            f"{ML_CONFIDENCE_THRESHOLD * 100:.0f}% "
            f"→ ПРОПУСКАЮ",
            flush=True
        )

    else:

        print(
            "⏭️ ML не выдал масти",
            flush=True
        )

    return None, None, None


# =====================================================================
# ПАРСИНГ СООБЩЕНИЯ ИГРЫ ИЗ TELEGRAM
# =====================================================================

def parse_game_from_text(text):

    try:

        game_match = re.search(
            r"#N(\d+)",
            text
        )

        if not game_match:
            return None

        game_number = int(
            game_match.group(1)
        )

        parts = None

        if "◀️" in text:
            parts = text.split("◀️")

        elif "▶️" in text:
            parts = text.split("▶️")

        elif "-" in text:
            parts = text.split("-")

        elif "—" in text:
            parts = text.split("—")

        else:
            return None

        if not parts or len(parts) < 2:
            return None

        player_part = parts[0].strip()

        banker_part = parts[1].strip()

        def parse_cards_from_part(part):

            cards_match = re.search(
                r"\(([^)]+)\)",
                part
            )

            if not cards_match:
                return []

            cards_str = (
                cards_match
                .group(1)
                .strip()
            )

            cards = []

            i = 0

            while i < len(cards_str):

                if cards_str[i] == " ":
                    i += 1
                    continue

                rank = ""

                if (
                    i + 1 < len(cards_str)
                    and cards_str[i:i + 2] == "10"
                ):

                    rank = "10"
                    i += 2

                elif cards_str[i] in "AKQJ":

                    rank = cards_str[i]
                    i += 1

                elif cards_str[i].isdigit():

                    rank = cards_str[i]
                    i += 1

                else:

                    i += 1
                    continue

                suit = ""

                if i < len(cards_str):

                    if cards_str[i:i + 2] == "♠️":
                        suit = "♠️"
                        i += 2

                    elif cards_str[i:i + 2] == "♣️":
                        suit = "♣️"
                        i += 2

                    elif cards_str[i:i + 2] == "♦️":
                        suit = "♦️"
                        i += 2

                    elif cards_str[i:i + 2] == "♥️":
                        suit = "♥️"
                        i += 2

                    elif cards_str[i] in "♠♣♦♥":

                        suit = (
                            cards_str[i]
                            .replace("♠", "♠️")
                            .replace("♣", "♣️")
                            .replace("♦", "♦️")
                            .replace("♥", "♥️")
                        )

                        i += 1

                    else:

                        i += 1
                        continue

                if rank and suit:

                    cards.append({
                        "rank": rank,
                        "suit": suit
                    })

            return cards

        player_cards = (
            parse_cards_from_part(
                player_part
            )
        )

        banker_cards = (
            parse_cards_from_part(
                banker_part
            )
        )

        return {
            "number": game_number,
            "player_cards": player_cards,
            "banker_cards": banker_cards,
            "text": text
        }

    except Exception as e:

        print(
            f"❌ Ошибка парсинга: {e}",
            flush=True
        )

        return None


def is_finished_game_text(text):
    return (
        "✅" in text
        or
        "🔰" in text
    )


# =====================================================================
# ПРОВЕРКА РЕЗУЛЬТАТОВ
# =====================================================================

def check_results():
    global predictions
    global stats

    for entry in predictions:

        if entry.get(
            "status"
        ) != "pending":

            continue

        target = entry.get(
            "target"
        )

        predicted_suits = entry.get(
            "suits",
            []
        )

        message_id = entry.get(
            "message_id"
        )

        original_text = entry.get(
            "original_text",
            ""
        )

        if (
            not predicted_suits
            or not message_id
        ):
            continue

        max_games_to_check = (
            DOGON_GAMES
        )

        for i in range(
            max_games_to_check
        ):

            game_to_check = (
                target + i
            )

            game_msg = None

            for msg in all_messages:

                if isinstance(
                    msg,
                    tuple
                ):

                    text = msg[0]

                else:

                    text = msg

                if (
                    f"#N{game_to_check}" in text
                    and is_finished_game_text(text)
                ):

                    game_msg = text
                    break

            if not game_msg:
                continue

            game_data = (
                parse_game_from_text(
                    game_msg
                )
            )

            if not game_data:
                continue

            # ======================================================
            # ВАЖНО:
            # ПРОВЕРЯЕМ ТОЛЬКО PLAYER
            # ======================================================

            player_cards = game_data.get(
                "player_cards",
                []
            )

            if not player_cards:
                continue

            actual_player_suits = []

            for card in player_cards:

                suit = card.get(
                    "suit",
                    ""
                )

                if suit in SUITS:
                    actual_player_suits.append(
                        suit
                    )

            if not actual_player_suits:
                continue

            found = False
            found_suit = None

            for suit in actual_player_suits:

                if suit in predicted_suits:

                    found = True
                    found_suit = suit
                    break

            # ======================================================
            # ПОПАДАНИЕ
            # ======================================================

            if found:

                print(
                    f"🎯 МАСТЬ НАЙДЕНА! "
                    f"{found_suit} "
                    f"({SUIT_NAMES_RU.get(found_suit, '')}) "
                    f"у PLAYER в игре #{game_to_check} "
                    f"(догон {i})",
                    flush=True
                )

                stats["total"] += 1

                stats["win"] += 1

                stats["by_dogon"][i] = (
                    stats["by_dogon"].get(i, 0) + 1
                )

                stats["ml_wins"] += 1

                stats["suit_hits"][found_suit] += 1

                if i == 0:

                    result_text = (
                        f"\n\n"
                        f"✅ ЗАШЛО в целевой игре: "
                        f"#{game_to_check}\n"
                        f"   Масть у игрока: "
                        f"{found_suit} "
                        f"({SUIT_NAMES_RU.get(found_suit, '')})"
                    )

                else:

                    result_text = (
                        f"\n\n"
                        f"✅ ЗАШЛО на догоне {i}: "
                        f"#{game_to_check}\n"
                        f"   Масть у игрока: "
                        f"{found_suit} "
                        f"({SUIT_NAMES_RU.get(found_suit, '')})"
                    )

                edit_message(
                    message_id,
                    original_text + result_text
                )

                entry["status"] = "win"

                entry["result_game"] = (
                    game_to_check
                )

                entry["dogon"] = i

                entry["found_suit"] = (
                    found_suit
                )

                save_history(
                    predictions
                )

                return

            # ======================================================
            # НЕ ПОПАЛИ
            # ======================================================

            if i == max_games_to_check - 1:

                print(
                    f"❌ Масти "
                    f"{', '.join(predicted_suits)} "
                    f"НЕ НАЙДЕНЫ у PLAYER "
                    f"за {max_games_to_check} игр",
                    flush=True
                )

                actual_suit = (
                    actual_player_suits[0]
                    if actual_player_suits
                    else None
                )

                stats["total"] += 1

                stats["lose"] += 1

                stats["ml_losses"] += 1

                if actual_suit:

                    print(
                        f"📘 ОШИБКА: "
                        f"ждали {', '.join(predicted_suits)}, "
                        f"у игрока выпала "
                        f"{actual_suit}",
                        flush=True
                    )

                    result_text = (
                        f"\n\n"
                        f"❌ НЕ ЗАШЛО "
                        f"(проверено "
                        f"{max_games_to_check} игр)\n"
                        f"   У игрока: "
                        f"{actual_suit} "
                        f"({SUIT_NAMES_RU.get(actual_suit, '')})"
                    )

                else:

                    result_text = (
                        f"\n\n"
                        f"❌ НЕ ЗАШЛО "
                        f"(нет масти игрока)"
                    )

                edit_message(
                    message_id,
                    original_text + result_text
                )

                entry["status"] = "lose"

                entry["actual_suit"] = (
                    actual_suit
                )

                save_history(
                    predictions
                )

                return


# =====================================================================
# ПЛАНИРОВЩИК
# =====================================================================

def schedule_for_game(game_number):
    global predictions

    target = (
        game_number + OFFSET
    )

    for entry in predictions:

        if (
            entry.get("target") == target
            and entry.get("status")
            in ("scheduled", "pending")
        ):

            return

    source = (
        target - 1
    )

    predictions.append({
        "source": source,
        "target": target,
        "offset": OFFSET,
        "status": "scheduled",
        "created": datetime.now(
            MOSCOW_TZ
        ).isoformat()
    })

    if len(predictions) > 200:
        predictions = predictions[-200:]

    save_history(
        predictions
    )

    print(
        f"📅 Запланирован прогноз: "
        f"#{source} → #{target} "
        f"(+{OFFSET})",
        flush=True
    )


# =====================================================================
# ПРОГНОЗ ИЗ API
# =====================================================================

def check_and_predict():
    global predictions
    global game_history

    for entry in predictions:

        if entry.get(
            "status"
        ) != "scheduled":

            continue

        target = entry.get(
            "target"
        )

        current_num = (
            get_game_number_by_time()
        )

        games_left = (
            target - current_num
        )

        if (
            games_left != 2
            and games_left != 1
        ):
            continue

        print(
            f"🔥 До цели #{target} "
            f"осталось {games_left} игр! "
            f"Делаю прогноз масти...",
            flush=True
        )

        latency = None

        current_game_data = None

        # ==========================================================
        # ТЕКУЩАЯ ИГРА БЕРЁМ ИЗ API
        # ==========================================================

        active_games = (
            get_active_games()
        )

        for game in active_games:

            game_id = str(
                game.get("id")
            )

            data, measured_latency, _, _ = (
                get_game_data(
                    game_id
                )
            )

            if not data:
                continue

            (
                player_cards,
                banker_cards,
                state
            ) = parse_cards_and_state(
                data
            )

            if (
                not player_cards
                and not banker_cards
            ):
                continue

            current_game_data = {
                "number": current_num,
                "player_cards": player_cards,
                "banker_cards": banker_cards
            }

            latency = (
                measured_latency
            )

            break

        if latency is None:

            print(
                "⏳ Не удалось получить задержку",
                flush=True
            )

            continue

        if not current_game_data:

            print(
                f"⏳ Нет данных API "
                f"о текущей игре #{current_num}",
                flush=True
            )

            continue

        # ==========================================================
        # ПРОГНОЗ
        # ==========================================================

        predicted_suits, method, confidence = (
            get_prediction(
                latency,
                current_game_data
            )
        )

        if (
            not predicted_suits
            or len(predicted_suits) < 2
        ):

            print(
                f"⏭️ Нет прогноза ML "
                f"для #{target}",
                flush=True
            )

            continue

        suit_names = [
            suit
            for suit, prob
            in predicted_suits
        ]

        total_prob = sum(
            prob
            for suit, prob
            in predicted_suits
        )

        # ==========================================================
        # ИСТОРИЯ
        # ==========================================================

        update_game_history(
            latency,
            current_game_data.get(
                "player_cards",
                []
            ),
            current_game_data.get(
                "banker_cards",
                []
            ),
            current_num
        )

        # ==========================================================
        # СООБЩЕНИЕ
        # ==========================================================

        msg = (
            "🔮 МАСТЬ ИГРОКА "
            "(ML ТОП-2)\n\n"
        )

        msg += (
            f"🎰 Игра: БАККАРА\n"
        )

        msg += (
            f"🎯 Целевая игра: "
            f"#N{target} (+{OFFSET})\n"
        )

        msg += (
            f"🤖 Метод: ML "
            f"(увер. {confidence * 100:.1f}%)\n"
        )

        msg += (
            f"⚡ Порог: "
            f"{ML_CONFIDENCE_THRESHOLD * 100:.0f}%\n"
        )

        msg += (
            f"⏰ Прогноз: "
            f"{datetime.now(MOSCOW_TZ).strftime('%H:%M:%S')}\n\n"
        )

        msg += (
            "🎴 Топ-2 масти игрока:\n"
        )

        cards_list = []

        for i, (
            suit,
            prob
        ) in enumerate(
            predicted_suits,
            1
        ):

            cards_list.append(
                suit
            )

            msg += (
                f"  {i}️⃣ "
                f"{suit} "
                f"({SUIT_NAMES_RU.get(suit, '')}) "
                f"— {prob * 100:.1f}%\n"
            )

        msg += (
            f"\n📊 Суммарная вероятность: "
            f"{total_prob * 100:.1f}%\n"
        )

        msg += (
            f"📈 Догон: "
            f"{DOGON_GAMES - 1} игр\n"
        )

        msg += (
            "📍 Ищем: ТОЛЬКО У ИГРОКА"
        )

        # ==========================================================
        # ТЕКУЩИЕ КАРТЫ PLAYER
        # ==========================================================

        player_cards = (
            current_game_data.get(
                "player_cards",
                []
            )
        )

        if player_cards:

            player_sequence = []

            for card in player_cards[:3]:

                rank = card.get(
                    "rank",
                    "?"
                )

                suit = card.get(
                    "suit",
                    "?"
                )

                player_sequence.append(
                    f"{rank}{suit}"
                )

            if player_sequence:

                msg += (
                    "\n📌 Player: "
                    +
                    " ".join(
                        player_sequence
                    )
                )

        message_id = send_message(
            CHANNEL_PROGNOZ,
            msg
        )

        if message_id:

            entry["suits"] = cards_list

            entry["method"] = method

            entry["message_id"] = (
                message_id
            )

            entry["original_text"] = (
                msg
            )

            entry["status"] = "pending"

            entry["latency"] = (
                latency
            )

            entry["confidence"] = (
                confidence
            )

            save_history(
                predictions
            )

            print(
                f"✅ ПРОГНОЗ ОТПРАВЛЕН: "
                f"#{target} → "
                f"{', '.join(cards_list)} "
                f"(ML, уверенность "
                f"{confidence * 100:.1f}%)",
                flush=True
            )


# =====================================================================
# СБОР ДАННЫХ ИЗ API
# =====================================================================

def collect_game_data():
    global collection_active
    global finished_games

    if not collection_active:
        return

    active_games = (
        get_active_games()
    )

    if not active_games:
        return

    data = load_data()

    if len(data) >= MAX_RECORDS:

        collection_active = False

        return

    for game in active_games:

        game_id = str(
            game.get("id")
        )

        if game_id in finished_games:
            continue

        (
            game_data,
            latency,
            start_time,
            end_time
        ) = get_game_data(
            game_id
        )

        if (
            not game_data
            or not isinstance(
                game_data,
                dict
            )
        ):
            continue

        (
            player_cards,
            banker_cards,
            state
        ) = parse_cards_and_state(
            game_data
        )

        if (
            player_cards
            or banker_cards
        ):

            timestamp = (
                datetime.fromtimestamp(
                    start_time,
                    MOSCOW_TZ
                )
                if start_time
                else datetime.now(
                    MOSCOW_TZ
                )
            )

            timestamp_msk_str = (
                timestamp.strftime(
                    "%H:%M:%S.%f"
                )[:-3]
            )

            sequence = []

            max_len = max(
                len(player_cards),
                len(banker_cards)
            )

            for i in range(
                max_len
            ):

                if i < len(
                    player_cards
                ):

                    pc = player_cards[i]

                    sequence.append({
                        "position": i * 2 + 1,
                        "who": "P",
                        "rank": pc.get(
                            "rank",
                            "?"
                        ),
                        "suit": pc.get(
                            "suit",
                            "?"
                        )
                    })

                if i < len(
                    banker_cards
                ):

                    bc = banker_cards[i]

                    sequence.append({
                        "position": i * 2 + 2,
                        "who": "B",
                        "rank": bc.get(
                            "rank",
                            "?"
                        ),
                        "suit": bc.get(
                            "suit",
                            "?"
                        )
                    })

            record = {
                "game_id": game_id,

                "timestamp_msk": (
                    timestamp_msk_str
                ),

                "latency_ms": (
                    round(
                        latency,
                        2
                    )
                    if latency
                    else 0
                ),

                "state": state,

                "player_cards": (
                    player_cards
                ),

                "banker_cards": (
                    banker_cards
                ),

                "sequence": sequence
            }

            data = save_data(
                record
            )

            # ======================================================
            # ЗАВЕРШЁННАЯ ИГРА
            # ======================================================

            if state in ["4", "5"]:

                finished_games.add(
                    game_id
                )

                print(
                    f"🏁 Игра {game_id} "
                    f"завершена "
                    f"(state={state}), "
                    f"сохранена",
                    flush=True
                )

                game_number = (
                    get_game_number_by_time()
                )

                print(
                    f"📥 API: получена "
                    f"игра #{game_number}",
                    flush=True
                )

                schedule_for_game(
                    game_number
                )

            if len(data) >= MAX_RECORDS:

                collection_active = False

                return

        time.sleep(0.5)


# =====================================================================
# СТАТИСТИКА
# =====================================================================

def send_stats_report():
    now = datetime.now(
        MOSCOW_TZ
    )

    win_percent = 0

    if stats["total"] > 0:

        win_percent = (
            stats["win"]
            /
            stats["total"]
            *
            100
        )

    data_count = len(
        load_data()
    )

    msg = f"""
📊 СТАТИСТИКА
🃏 БАККАРА — МАСТЬ ИГРОКА ML ТОП-2

⏰ {now.strftime('%d.%m.%Y %H:%M:%S')}
══════════════════════════════════════════
📊 Собрано игр: {data_count}/{MAX_RECORDS}

📈 Всего прогнозов: {stats['total']}
✅ Зашло: {stats['win']} ({win_percent:.1f}%)
❌ Не зашло: {stats['lose']}

🤖 ML: {stats['ml_wins']}✅ / {stats['ml_losses']}❌

По догонам ({DOGON_GAMES - 1} игр):
  Догон 0: {stats['by_dogon'].get(0, 0)}
  Догон 1: {stats['by_dogon'].get(1, 0)}
  Догон 2: {stats['by_dogon'].get(2, 0)}
  Догон 3: {stats['by_dogon'].get(3, 0)}

🎴 ТОП-4 МАСТИ:
"""

    if stats["suit_hits"]:

        sorted_suits = sorted(
            dict(
                stats["suit_hits"]
            ).items(),
            key=lambda x: x[1],
            reverse=True
        )

        for suit, count in sorted_suits:

            msg += (
                f"  {suit} "
                f"({SUIT_NAMES_RU.get(suit, '')}): "
                f"{count}\n"
            )

    else:

        msg += (
            "  (пока нет данных)\n"
        )

    if ml_initialized:

        msg += (
            "\n🤖 ML: АКТИВНА"
        )

    else:

        msg += (
            f"\n🤖 ML: ОЖИДАЕТ "
            f"({data_count}/"
            f"{MIN_TRAIN_SAMPLES})"
        )

    send_message(
        CHANNEL_STATS,
        msg
    )


# =====================================================================
# ОСНОВНОЙ ЦИКЛ
# =====================================================================

def main():

    global predictions
    global all_messages
    global stats
    global game_history
    global collection_active

    print(
        "🔄 БАККАРА — МАСТЬ ИГРОКА "
        "(ML ТОП-2) ЗАПУЩЕН",
        flush=True
    )

    print(
        f"📁 Данные в {DATA_FILE}",
        flush=True
    )

    print(
        f"📊 Максимум записей: "
        f"{MAX_RECORDS}",
        flush=True
    )

    print(
        f"🎯 Смещение: +{OFFSET} игр",
        flush=True
    )

    print(
        f"📈 Догон: "
        f"{DOGON_GAMES - 1} игр",
        flush=True
    )

    print(
        f"⚡ Порог ML: "
        f"{ML_CONFIDENCE_THRESHOLD * 100:.0f}%",
        flush=True
    )

    print(
        f"🎴 Мастей для прогноза: "
        f"{len(TARGET_SUITS)}",
        flush=True
    )

    print(
        f"🎰 Лига Баккара: "
        f"{BACCARAT_LEAGUE_ID}",
        flush=True
    )

    print(
        "=" * 60,
        flush=True
    )

    existing_data = load_data()

    print(
        f"📊 Уже собрано записей: "
        f"{len(existing_data)}",
        flush=True
    )

    if len(existing_data) >= MAX_RECORDS:

        collection_active = False

        print(
            f"⏸️ СБОР ДАННЫХ ОТКЛЮЧЁН "
            f"(лимит {MAX_RECORDS})",
            flush=True
        )

    game_history = (
        load_game_history()
    )

    print(
        f"📈 Загружено истории: "
        f"{len(game_history)} игр",
        flush=True
    )

    predictions = load_history()

    # ==========================================================
    # ML
    # ==========================================================

    model_loaded = load_ml_model()

    if not model_loaded:

        print(
            "🧠 Старая/отсутствующая "
            "модель мастей.",
            flush=True
        )

        train_ml_model()

    else:

        # Переобучаем актуальную модель
        train_ml_model()

    stats["games_collected"] = (
        len(existing_data)
    )

    send_startup_message()

    # ==========================================================
    # Загружаем старые сообщения канала
    # ==========================================================

    try:

        url = (
            f"https://api.telegram.org/"
            f"bot{BOT_TOKEN}/getUpdates"
        )

        params = {
            "chat_id": CHANNEL_STATS,
            "limit": 100
        }

        response = requests.get(
            url,
            params=params,
            timeout=10
        )

        if response.status_code == 200:

            telegram_data = (
                response.json()
            )

            for update in telegram_data.get(
                "result",
                []
            ):

                post = update.get(
                    "channel_post"
                )

                if (
                    post
                    and post.get("text")
                ):

                    all_messages.append(
                        (
                            post.get("text"),
                            time.time()
                        )
                    )

    except Exception:
        pass

    print(
        f"📥 Загружено сообщений: "
        f"{len(all_messages)}",
        flush=True
    )

    last_stats_time = time.time()

    last_train_time = time.time()

    last_check_time = time.time()

    offset = get_offset()

    print(
        "🚀 БОТ ГОТОВ К РАБОТЕ!",
        flush=True
    )

    print(
        "=" * 60,
        flush=True
    )

    while True:

        try:

            current_time = time.time()

            # ======================================================
            # API — ОСНОВНОЙ ИСТОЧНИК
            # ======================================================

            collect_game_data()

            # ======================================================
            # TELEGRAM
            # ======================================================

            updates = get_updates(
                offset
            )

            for update in updates.get(
                "result",
                []
            ):

                offset = (
                    update["update_id"]
                    + 1
                )

                save_offset(
                    offset
                )

                channel_post = update.get(
                    "channel_post"
                )

                edited_post = update.get(
                    "edited_channel_post"
                )

                post = (
                    channel_post
                    if channel_post
                    else edited_post
                )

                if not post:
                    continue

                chat_id = (
                    post
                    .get("chat", {})
                    .get("id")
                )

                if str(chat_id) != str(
                    CHANNEL_STATS
                ):

                    continue

                text = post.get(
                    "text",
                    ""
                )

                if (
                    not text
                    or "#N" not in text
                ):

                    continue

                all_messages.append(
                    (
                        text,
                        time.time()
                    )
                )

                if len(all_messages) > 500:

                    all_messages = (
                        all_messages[-500:]
                    )

                # Telegram используется
                # только для проверки результата

                check_results()

            # ======================================================
            # ПРОГНОЗ
            # ======================================================

            if (
                current_time
                -
                last_check_time
                >= CHECK_INTERVAL
            ):

                check_and_predict()

                last_check_time = (
                    current_time
                )

            # ======================================================
            # ПРОВЕРКА РЕЗУЛЬТАТОВ
            # ======================================================

            check_results()

            # ======================================================
            # ПЕРЕОБУЧЕНИЕ КАЖДЫЕ 3 МИНУТЫ
            # ======================================================

            if (
                current_time
                -
                last_train_time
                > 180
            ):

                data_count = len(
                    load_data()
                )

                if data_count >= (
                    MIN_TRAIN_SAMPLES
                ):

                    print(
                        f"🔄 ЗАПУСК ПЕРЕОБУЧЕНИЯ "
                        f"(всего игр: "
                        f"{data_count})...",
                        flush=True
                    )

                    train_ml_model()

                    last_train_time = (
                        current_time
                    )

                    gc.collect()

            # ======================================================
            # СТАТИСТИКА
            # ======================================================

            if (
                current_time
                -
                last_stats_time
                > 3600
            ):

                send_stats_report()

                last_stats_time = (
                    current_time
                )

            # ======================================================
            # ОЧИСТКА
            # ======================================================

            if len(
                processed_games
            ) > 500:

                processed_games.clear()

            if len(
                predictions
            ) > 200:

                predictions = (
                    predictions[-200:]
                )

                save_history(
                    predictions
                )

            time.sleep(
                CHECK_INTERVAL
            )

        except KeyboardInterrupt:

            print(
                "🛑 Бот остановлен",
                flush=True
            )

            data_count = len(
                load_data()
            )

            print(
                f"📊 Всего собрано игр: "
                f"{data_count}",
                flush=True
            )

            break

        except Exception as e:

            print(
                f"❌ Ошибка: {e}",
                flush=True
            )

            import traceback

            traceback.print_exc()

            time.sleep(30)


# =====================================================================
# ЗАПУСК
# =====================================================================

if __name__ == "__main__":
    main()
```
