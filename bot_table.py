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
            print(
                f"❌ Ошибка установки {package}: {e}",
                flush=True
            )
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

        print("=" * 60, flush=True)

        return True

    if not check_and_install_dependencies():
        sys.exit(1)

except Exception as e:
    print(
        f"⚠️ Ошибка проверки зависимостей: {e}",
        flush=True
    )


# =====================================================================
# ML
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
# НАСТРОЙКИ БАККАРЫ
# =====================================================================

MOSCOW_TZ = pytz.timezone("Europe/Moscow")

BASE_URL = "https://1xlite-0687.pro"

# ЛИГА БАККАРЫ
BACCARAT_LEAGUE_ID = 2050671

DATA_FILE = "baccarat_data.json"
HISTORY_FILE = "baccarat_history.json"
ML_MODEL_FILE = "baccarat_model.pkl"
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
# ВАЖНО: ПОРОГ 25%
# ============================================================

ML_CONFIDENCE_THRESHOLD = 0.25


# =====================================================================
# МАСТИ
# =====================================================================

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
    "♠️": "ПИКИ",
    "♣️": "ТРЕФЫ",
    "♦️": "БУБНЫ",
    "♥️": "ЧЕРВЫ"
}


TARGET_SUITS = [
    "♠️",
    "♣️",
    "♦️",
    "♥️"
]


RANK_VALUES = {
    "6": 6,
    "7": 7,
    "8": 8,
    "9": 9,
    "10": 10,
    "J": 11,
    "Q": 12,
    "K": 13,
    "A": 14
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
# API HEADERS
# =====================================================================

HEADERS = {
    "User-Agent":
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36",

    "Accept":
        "application/json, text/plain, */*",

    "Referer":
        f"{BASE_URL}/ru/live/baccarat/"
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
            f"{response.status_code} "
            f"{response.text[:300]}",
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


# =====================================================================
# STARTUP
# =====================================================================

def send_startup_message():

    data_count = len(load_data())

    now = datetime.now(MOSCOW_TZ)

    msg = f"""
🎰 <b>БАККАРА — ПРОГНОЗ МАСТИ</b>

📊 Собрано игр: {data_count}/{MAX_RECORDS}
🧠 ML: {'✅ АКТИВНА' if ml_initialized else '⏳ ОЖИДАЕТ'}

🎯 Смещение: +{OFFSET} игр
📈 Догон: {DOGON_GAMES - 1} игр

⚡ Порог ML: {int(ML_CONFIDENCE_THRESHOLD * 100)}%
🎴 Мастей для прогноза: {len(TARGET_SUITS)}

🎰 Лига Баккара: {BACCARAT_LEAGUE_ID}

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
# ФАЙЛЫ
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

        except Exception as e:

            print(
                f"⚠️ Ошибка чтения {DATA_FILE}: {e}",
                flush=True
            )

            return []

    return []


def save_data(record):

    global collection_active

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


    if len(data) >= MAX_RECORDS:

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

        except:

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

        except:

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

    except:

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

        except:

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
# API БАККАРЫ
# =====================================================================

def get_active_games():

    try:

        url = (
            f"{BASE_URL}/service-api/main-live-feed/"
            f"v3/games1x2"
            f"?cfView=3"
            f"&count=100"
            f"&fcountry=190"
            f"&gr=415"
            f"&grMode=4"
            f"&lng=ru"
            f"&ref=7"
            f"&selectedMs=1.146.{BACCARAT_LEAGUE_ID},"
            f"10.146.{BACCARAT_LEAGUE_ID}"
        )


        print(
            "🔎 API БАККАРЫ:",
            url,
            flush=True
        )


        response = requests.get(
            url,
            headers=HEADERS,
            timeout=10
        )


        print(
            f"📡 API status: "
            f"{response.status_code}",
            flush=True
        )


        if response.status_code != 200:

            print(
                f"❌ API Баккары вернул "
                f"{response.status_code}",
                flush=True
            )

            print(
                response.text[:1000],
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

            print(
                "⚠️ Неизвестный формат API",
                flush=True
            )

            print(
                str(data)[:2000],
                flush=True
            )

            return []


        if not isinstance(games, list):

            print(
                "⚠️ Value API не является списком",
                flush=True
            )

            return []


        print(
            f"📥 API вернул игр: "
            f"{len(games)}",
            flush=True
        )


        active_games = []


        for game in games:

            if not isinstance(game, dict):

                continue


            liga = game.get(
                "liga",
                {}
            )


            liga_id = liga.get(
                "id"
            )


            game_id = game.get(
                "id"
            )


            # ========================================================
            # ИЩЕМ ИМЕННО БАККАРУ
            # ========================================================

            if str(liga_id) == str(
                BACCARAT_LEAGUE_ID
            ):

                if game_id:

                    active_games.append(
                        game
                    )


        print(
            f"🎰 Баккара: найдено игр "
            f"{len(active_games)}",
            flush=True
        )


        # ============================================================
        # ДИАГНОСТИКА, ЕСЛИ 0
        # ============================================================

        if not active_games and games:

            print(
                "⚠️ Игр с нужной лигой не найдено.",
                flush=True
            )

            print(
                "🔍 Первые найденные лиги:",
                flush=True
            )

            shown = 0

            for game in games:

                if not isinstance(
                    game,
                    dict
                ):

                    continue

                liga = game.get(
                    "liga",
                    {}
                )

                print(
                    f"   game={game.get('id')} "
                    f"liga_id={liga.get('id')} "
                    f"name={liga.get('name')} "
                    f"nameEng={liga.get('nameEng')}",
                    flush=True
                )

                shown += 1

                if shown >= 10:

                    break


        return active_games


    except Exception as e:

        print(
            f"❌ Ошибка API Баккары: {e}",
            flush=True
        )

        import traceback

        traceback.print_exc()

        return []


# =====================================================================
# ПОЛУЧЕНИЕ ДАННЫХ КОНКРЕТНОЙ ИГРЫ
# =====================================================================

def get_game_data(game_id):

    url = (
        f"{BASE_URL}/service-api/LiveFeed/"
        f"GetGameZip"
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
            timeout=8
        )


        end_time = time.time()


        latency = (
            end_time - start_time
        ) * 1000


        if response.status_code != 200:

            print(
                f"❌ Game API {game_id}: "
                f"HTTP {response.status_code}",
                flush=True
            )

            return (
                None,
                None,
                None,
                None
            )


        data = response.json()


        return (
            data,
            latency,
            start_time,
            end_time
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
# ПАРСЕР API
# =====================================================================

def parse_cards_and_state(data):

    if not data or not isinstance(data, dict):

        return [], [], None


    sc = data.get(
        "Value",
        {}
    )


    if not isinstance(sc, dict):

        return [], [], None


    sc = sc.get(
        "SC",
        {}
    )


    if not isinstance(sc, dict):

        return [], [], None


    player_cards = []

    dealer_cards = []

    state = None


    for item in sc.get(
        "S",
        []
    ):

        if not isinstance(
            item,
            dict
        ):

            continue


        key = item.get(
            "Key"
        )


        value = item.get(
            "Value",
            ""
        )


        if key == "P1":

            try:

                if isinstance(
                    value,
                    str
                ):

                    player_cards = json.loads(
                        value
                    )

                elif isinstance(
                    value,
                    list
                ):

                    player_cards = value

            except Exception:

                player_cards = []


        elif key == "P2":

            try:

                if isinstance(
                    value,
                    str
                ):

                    dealer_cards = json.loads(
                        value
                    )

                elif isinstance(
                    value,
                    list
                ):

                    dealer_cards = value

            except Exception:

                dealer_cards = []


        elif key == "STATE":

            state = value


    return (
        player_cards,
        dealer_cards,
        state
    )


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

        start -= timedelta(
            days=1
        )


    diff_minutes = (
        now - start
    ).total_seconds() / 60


    return (
        int(diff_minutes) % 1440
    ) + 1


# =====================================================================
# КАРТА ИЗ API
# =====================================================================

def convert_api_card(card):

    if not isinstance(
        card,
        dict
    ):

        return {
            "rank": "?",
            "suit": "?"
        }


    cv = card.get(
        "CV",
        0
    )

    cs = card.get(
        "CS",
        0
    )


    rank = RANKS.get(
        cv,
        "?"
    )

    suit = SUITS_NAMES.get(
        cs,
        "?"
    )


    return {
        "rank": rank,
        "suit": suit
    }


# =====================================================================
# ИСТОРИЯ
# =====================================================================

def update_game_history(
    latency,
    player_cards,
    dealer_cards,
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

            player_suits.append(
                suit
            )


    dealer_suits = []

    for card in dealer_cards:

        suit = card.get(
            "suit",
            ""
        )

        if suit in SUITS:

            dealer_suits.append(
                suit
            )


    game_history.append({
        "latency": latency,
        "player_suits": player_suits,
        "dealer_suits": dealer_suits,
        "game_num": game_num,
        "timestamp":
            datetime.now(
                MOSCOW_TZ
            ).isoformat()
    })


    save_game_history()


def get_history_features():

    features = {}


    if len(game_history) >= 2:

        latencies = [
            g.get(
                "latency",
                0
            )
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


    # ================================================================
    # ПОСЛЕДНЯЯ МАСТЬ ИГРОКА
    # ================================================================

    player_suits = []

    for game in game_history:

        player_suits.extend(
            game.get(
                "player_suits",
                []
            )
        )


    if player_suits:

        last_suit = player_suits[-1]

        if last_suit in TARGET_SUITS:

            features["prev_suit"] = (
                TARGET_SUITS.index(
                    last_suit
                )
            )

    else:

        features["prev_suit"] = -1


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