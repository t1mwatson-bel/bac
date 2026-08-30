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
            module_name = package.replace("-", "_")

            try:
                importlib.import_module(module_name)

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
# НАСТРОЙКИ
# =====================================================================

MOSCOW_TZ = pytz.timezone("Europe/Moscow")

BASE_URL = "https://1xlite-0687.pro"

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


# =====================================================================
# ЗНАЧЕНИЯ КАРТ
# =====================================================================

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
    13: "K",
    14: "A"
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

last_prediction = None

last_games_debug = []

telegram_offset = 0


# =====================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =====================================================================

def safe_int(value, default=0):
    try:
        return int(value)
    except Exception:
        try:
            return int(float(value))
        except Exception:
            return default


def safe_str(value):
    if value is None:
        return ""

    return str(value).strip()


def normalize_suit(value):
    if value is None:
        return "?"

    value = str(value).strip()

    if value in SUITS:
        return value

    mapping = {
        "0": "♠️",
        "1": "♣️",
        "2": "♦️",
        "3": "♥️",

        "S": "♠️",
        "C": "♣️",
        "D": "♦️",
        "H": "♥️",

        "s": "♠️",
        "c": "♣️",
        "d": "♦️",
        "h": "♥️",

        "spades": "♠️",
        "clubs": "♣️",
        "diamonds": "♦️",
        "hearts": "♥️"
    }

    return mapping.get(value, "?")


def normalize_rank(value):
    if value is None:
        return "?"

    value = str(value).strip()

    if value in RANK_VALUES:
        return value

    number = safe_int(value, -1)

    if number in RANKS:
        return RANKS[number]

    return value


def card_to_text(card):
    if not isinstance(card, dict):
        return "?"

    rank = card.get("rank", "?")
    suit = card.get("suit", "?")

    return f"{rank}{suit}"


def cards_to_text(cards):
    if not cards:
        return ""

    return " ".join(
        card_to_text(card)
        for card in cards
    )


def now_moscow():
    return datetime.now(MOSCOW_TZ)


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

        if response.status_code != 200:
            print(
                f"❌ Telegram getUpdates: "
                f"{response.status_code}",
                flush=True
            )
            return {}

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

            result = response.json()

            if result.get("ok"):
                return result["result"]["message_id"]

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

                data = json.load(f)

                if isinstance(data, list):
                    return data

        except Exception as e:

            print(
                f"⚠️ Ошибка чтения {DATA_FILE}: {e}",
                flush=True
            )

    return []


def save_data(record):

    global collection_active

    data = load_data()

    existing_index = None

    for i, r in enumerate(data):

        if str(r.get("game_id")) == str(
            record.get("game_id")
        ):

            existing_index = i
            break

    if existing_index is not None:

        data[existing_index] = record

    else:

        if len(data) >= MAX_RECORDS:

            collection_active = False

            return data

        data.append(record)

        stats["games_collected"] += 1

    if len(data) >= MAX_RECORDS:

        collection_active = False

        print(
            f"⏸️ СБОР ДАННЫХ ОСТАНОВЛЕН! "
            f"Достигнут лимит {MAX_RECORDS}",
            flush=True
        )

    try:

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

    except Exception as e:

        print(
            f"❌ Ошибка сохранения данных: {e}",
            flush=True
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

                data = json.load(f)

                if isinstance(data, list):
                    return data

        except Exception:
            pass

    return []


def save_history(history):

    if len(history) > MAX_HISTORY:
        history = history[-MAX_HISTORY:]

    try:

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

    except Exception as e:

        print(
            f"⚠️ Ошибка сохранения истории: {e}",
            flush=True
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

                if isinstance(data, list):

                    return deque(
                        data,
                        maxlen=MAX_GAME_HISTORY
                    )

        except Exception:
            pass

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

    try:

        with open(
            OFFSET_FILE,
            "w"
        ) as f:

            f.write(
                str(offset)
            )

    except Exception:
        pass


# =====================================================================
# ЗАГРУЗКА СОХРАНЁННЫХ ID
# =====================================================================

def restore_processed_games():

    global processed_games
    global finished_games

    data = load_data()

    for record in data:

        game_id = record.get("game_id")

        if game_id is None:
            continue

        game_id = str(game_id)

        processed_games.add(game_id)

        if record.get("finished"):
            finished_games.add(game_id)

    print(
        f"💾 Загружено сохранённых игр: "
        f"{len(processed_games)}",
        flush=True
    )


# =====================================================================
# STARTUP
# =====================================================================

def send_startup_message():

    data_count = len(load_data())

    now = now_moscow()

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
            timeout=15
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

        try:
            data = response.json()

        except Exception as e:

            print(
                f"❌ API вернул не JSON: {e}",
                flush=True
            )

            print(
                response.text[:2000],
                flush=True
            )

            return []

        games = []

        if isinstance(data, dict):

            for key in [
                "Value",
                "value",
                "Games",
                "games",
                "Data",
                "data"
            ]:

                candidate = data.get(key)

                if isinstance(candidate, list):

                    games = candidate
                    break

                if isinstance(candidate, dict):

                    for nested_key in [
                        "Value",
                        "Games",
                        "games",
                        "Data",
                        "data"
                    ]:

                        nested = candidate.get(
                            nested_key
                        )

                        if isinstance(
                            nested,
                            list
                        ):

                            games = nested
                            break

                    if games:
                        break

        elif isinstance(data, list):

            games = data

        print(
            f"📥 API вернул объектов: "
            f"{len(games)}",
            flush=True
        )

        if not games:

            print(
                "⚠️ API не вернул список игр.",
                flush=True
            )

            print(
                "🔍 Ответ API:",
                str(data)[:3000],
                flush=True
            )

            return []

        active_games = []

        for game in games:

            if not isinstance(game, dict):
                continue

            game_id = (
                game.get("id")
                or game.get("Id")
                or game.get("ID")
                or game.get("gameId")
                or game.get("GameId")
            )

            if game_id is None:
                continue

            game_id = str(game_id)

            liga = game.get("liga")

            if not isinstance(liga, dict):
                liga = {}

            liga_id = (
                liga.get("id")
                or liga.get("Id")
                or liga.get("ID")
            )

            liga_name = (
                safe_str(liga.get("name"))
                or safe_str(liga.get("nameEng"))
            )

            game_name = (
                safe_str(game.get("name"))
                or safe_str(game.get("Name"))
            )

            # ---------------------------------------------------------
            # Сначала проверяем точную лигу
            # ---------------------------------------------------------

            exact_liga = (
                str(liga_id)
                ==
                str(BACCARAT_LEAGUE_ID)
            )

            # ---------------------------------------------------------
            # Дополнительное определение баккары
            # ---------------------------------------------------------

            text_for_check = (
                f"{liga_name} "
                f"{game_name}"
            ).lower()

            baccarat_match = (
                "baccarat" in text_for_check
                or
                "баккар" in text_for_check
            )

            # ---------------------------------------------------------
            # Если точная лига совпала — принимаем
            # ---------------------------------------------------------

            if exact_liga:

                active_games.append(game)
                continue

            # ---------------------------------------------------------
            # Если название явно Baccarat — тоже принимаем.
            # Это спасает ситуацию, когда API меняет liga.id.
            # ---------------------------------------------------------

            if baccarat_match:

                active_games.append(game)
                continue

        print(
            f"🎰 Баккара: найдено игр "
            f"{len(active_games)}",
            flush=True
        )

        # -------------------------------------------------------------
        # ДИАГНОСТИКА
        # -------------------------------------------------------------

        if not active_games:

            print(
                "⚠️ Баккарных игр не найдено.",
                flush=True
            )

            print(
                "🔍 Первые найденные объекты:",
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

                if not isinstance(
                    liga,
                    dict
                ):
                    liga = {}

                print(
                    f"   game={game.get('id')} "
                    f"liga_id={liga.get('id')} "
                    f"liga={liga.get('name')} "
                    f"name={game.get('name')}",
                    flush=True
                )

                shown += 1

                if shown >= 15:
                    break

        return active_games

    except Exception as e:

        print(
            f"❌ Ошибка API Баккары: {e}",
            flush=True
        )

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
            timeout=12
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

        try:

            data = response.json()

        except Exception:

            print(
                f"❌ Game API {game_id}: "
                f"не JSON",
                flush=True
            )

            return (
                None,
                latency,
                start_time,
                end_time
            )

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
# РЕКУРСИВИВНЫЙ ПОИСК КЛЮЧА
# =====================================================================

def find_key_recursive(obj, wanted_keys):

    if isinstance(obj, dict):

        for key, value in obj.items():

            if str(key).lower() in wanted_keys:
                return value

            result = find_key_recursive(
                value,
                wanted_keys
            )

            if result is not None:
                return result

    elif isinstance(obj, list):

        for item in obj:

            result = find_key_recursive(
                item,
                wanted_keys
            )

            if result is not None:
                return result

    return None


# =====================================================================
# БЕЗОПАСНЫЙ JSON PARSE
# =====================================================================

def parse_possible_json(value):

    if isinstance(value, (list, dict)):
        return value

    if not isinstance(value, str):
        return value

    value = value.strip()

    if not value:
        return value

    try:
        return json.loads(value)

    except Exception:
        pass

    # Иногда JSON лежит внутри строки
    # с лишними символами.

    first_obj = value.find("{")
    first_arr = value.find("[")

    positions = [
        p for p in [
            first_obj,
            first_arr
        ]
        if p >= 0
    ]

    if not positions:
        return value

    start = min(positions)

    for end in range(
        len(value),
        start + 1,
        -1
    ):

        fragment = value[start:end]

        try:
            return json.loads(fragment)

        except Exception:
            continue

    return value


# =====================================================================
# ПОЛУЧЕНИЕ P1 / P2 / STATE
# =====================================================================

def parse_cards_and_state(data):

    player_cards_raw = []
    dealer_cards_raw = []
    state = None

    if not data:
        return [], [], None

    # -------------------------------------------------------------
    # ОСНОВНОЙ ФОРМАТ:
    #
    # Value -> SC -> S -> Key=P1/P2/STATE
    # -------------------------------------------------------------

    sc = None

    if isinstance(data, dict):

        value = data.get("Value")

        if isinstance(value, dict):

            sc = value.get("SC")

    if isinstance(sc, dict):

        items = sc.get("S", [])

        if isinstance(items, list):

            for item in items:

                if not isinstance(
                    item,
                    dict
                ):
                    continue

                key = safe_str(
                    item.get("Key")
                ).upper()

                value = item.get(
                    "Value",
                    ""
                )

                if key == "P1":

                    parsed = parse_possible_json(
                        value
                    )

                    if isinstance(
                        parsed,
                        list
                    ):
                        player_cards_raw = parsed

                elif key == "P2":

                    parsed = parse_possible_json(
                        value
                    )

                    if isinstance(
                        parsed,
                        list
                    ):
                        dealer_cards_raw = parsed

                elif key == "STATE":

                    state = value

    # -------------------------------------------------------------
    # ДОПОЛНИТЕЛЬНЫЙ ПОИСК
    # -------------------------------------------------------------

    if not player_cards_raw:

        value = find_key_recursive(
            data,
            {
                "p1",
                "playercards",
                "player_cards",
                "player"
            }
        )

        value = parse_possible_json(value)

        if isinstance(value, list):
            player_cards_raw = value

    if not dealer_cards_raw:

        value = find_key_recursive(
            data,
            {
                "p2",
                "dealercards",
                "dealer_cards",
                "dealer",
                "banker"
            }
        )

        value = parse_possible_json(value)

        if isinstance(value, list):
            dealer_cards_raw = value

    if state is None:

        state = find_key_recursive(
            data,
            {
                "state",
                "gamestate",
                "game_state"
            }
        )

    # -------------------------------------------------------------
    # КОНВЕРТАЦИЯ КАРТ
    # -------------------------------------------------------------

    player_cards = []

    for card in player_cards_raw:

        converted = convert_api_card(card)

        if converted["rank"] != "?":

            player_cards.append(
                converted
            )

    dealer_cards = []

    for card in dealer_cards_raw:

        converted = convert_api_card(card)

        if converted["rank"] != "?":

            dealer_cards.append(
                converted
            )

    return (
        player_cards,
        dealer_cards,
        state
    )


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

    # Возможные названия ранга

    cv = (
        card.get("CV")
        if "CV" in card
        else card.get("cv")
    )

    if cv is None:
        cv = (
            card.get("Value")
            or card.get("value")
            or card.get("Rank")
            or card.get("rank")
        )

    # Возможные названия масти

    cs = (
        card.get("CS")
        if "CS" in card
        else card.get("cs")
    )

    if cs is None:
        cs = (
            card.get("Suit")
            or card.get("suit")
            or card.get("SuitId")
            or card.get("suitId")
        )

    rank = normalize_rank(cv)

    suit = normalize_suit(cs)

    return {
        "rank": rank,
        "suit": suit
    }


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
# ПРОВЕРКА, ЧТО ИГРА ЗАВЕРШЕНА
# =====================================================================

def is_game_finished(
    player_cards,
    dealer_cards,
    state
):

    state_text = safe_str(
        state
    ).lower()

    finished_words = [
        "finish",
        "finished",
        "complete",
        "completed",
        "close",
        "closed",
        "end",
        "ended",
        "result",
        "done",
        "settled"
    ]

    for word in finished_words:

        if word in state_text:
            return True

    # В баккаре обычно есть минимум
    # 2 карты у игрока и 2 у дилера.

    if (
        len(player_cards) >= 2
        and
        len(dealer_cards) >= 2
    ):

        return True

    return False


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
        "latency": latency or 0,

        "player_suits":
            player_suits,

        "dealer_suits":
            dealer_suits,

        "game_num":
            game_num,

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


# =====================================================================
# ПРИЗНАКИ ИГРЫ
# =====================================================================

def get_game_features(
    player_cards,
    dealer_cards,
    latency
):

    features = []

    # -------------------------------------------------------------
    # Количество карт
    # -------------------------------------------------------------

    features.append(
        len(player_cards)
    )

    features.append(
        len(dealer_cards)
    )

    # -------------------------------------------------------------
    # Значения карт игрока
    # -------------------------------------------------------------

    player_values = [
        RANK_VALUES.get(
            card.get("rank"),
            0
        )
        for card in player_cards
    ]

    dealer_values = [
        RANK_VALUES.get(
            card.get("rank"),
            0
        )
        for card in dealer_cards
    ]

    for i in range(3):

        if i < len(player_values):
            features.append(
                player_values[i]
            )
        else:
            features.append(0)

    for i in range(3):

        if i < len(dealer_values):
            features.append(
                dealer_values[i]
            )
        else:
            features.append(0)

    # -------------------------------------------------------------
    # Масти игрока
    # -------------------------------------------------------------

    player_suits = [
        card.get("suit")
        for card in player_cards
    ]

    for suit in TARGET_SUITS:

        features.append(
            player_suits.count(suit)
        )

    # -------------------------------------------------------------
    # Масти дилера
    # -------------------------------------------------------------

    dealer_suits = [
        card.get("suit")
        for card in dealer_cards
    ]

    for suit in TARGET_SUITS:

        features.append(
            dealer_suits.count(suit)
        )

    # -------------------------------------------------------------
    # Время ответа API
    # -------------------------------------------------------------

    features.append(
        float(latency or 0)
    )

    # -------------------------------------------------------------
    # Временные признаки
    # -------------------------------------------------------------

    now = datetime.now(
        MOSCOW_TZ
    )

    features.append(
        now.hour
    )

    features.append(
        now.minute
    )

    features.append(
        now.weekday()
    )

    # -------------------------------------------------------------
    # История
    # -------------------------------------------------------------

    history_features = get_history_features()

    features.append(
        history_features.get(
            "prev_latency",
            0
        )
    )

    features.append(
        history_features.get(
            "latency_delta",
            0
        )
    )

    features.append(
        history_features.get(
            "latency_trend",
            0
        )
    )

    features.append(
        history_features.get(
            "prev_suit",
            -1
        )
    )

    return features


# =====================================================================
# ОБУЧЕНИЕ ML
# =====================================================================

def train_ml():

    global ml_model
    global ml_initialized

    if not ML_AVAILABLE:

        return False

    data = load_data()

    # Нужны завершённые игры с картами

    usable = []

    for record in data:

        player_cards = record.get(
            "player_cards",
            []
        )

        dealer_cards = record.get(
            "dealer_cards",
            []
        )

        target_suit = record.get(
            "target_suit"
        )

        if (
            player_cards
            and
            dealer_cards
            and
            target_suit in TARGET_SUITS
        ):

            usable.append(record)

    if len(usable) < MIN_TRAIN_SAMPLES:

        print(
            f"🧠 ML: недостаточно данных "
            f"{len(usable)}/{MIN_TRAIN_SAMPLES}",
            flush=True
        )

        return False

    X = []
    y = []

    for record in usable:

        try:

            features = record.get(
                "features"
            )

            if not features:

                features = get_game_features(
                    record.get(
                        "player_cards",
                        []
                    ),
                    record.get(
                        "dealer_cards",
                        []
                    ),
                    record.get(
                        "latency",
                        0
                    )
                )

            X.append(features)

            y.append(
                TARGET_SUITS.index(
                    record["target_suit"]
                )
            )

        except Exception:
            continue

    if len(X) < MIN_TRAIN_SAMPLES:

        return False

    try:

        model = RandomForestClassifier(
            n_estimators=200,
            max_depth=12,
            min_samples_leaf=2,
            random_state=42,
            n_jobs=-1,
            class_weight="balanced"
        )

        model.fit(
            np.array(X),
            np.array(y)
        )

        ml_model = model

        ml_initialized = True

        try:

            with open(
                ML_MODEL_FILE,
                "wb"
            ) as f:

                pickle.dump(
                    ml_model,
                    f
                )

        except Exception as e:

            print(
                f"⚠️ Не удалось сохранить ML: {e}",
                flush=True
            )

        print(
            f"🧠 ML обучена на "
            f"{len(X)} играх",
            flush=True
        )

        return True

    except Exception as e:

        print(
            f"❌ Ошибка обучения ML: {e}",
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

            model = pickle.load(f)

        ml_model = model

        ml_initialized = True

        print(
            "🧠 Сохранённая ML-модель загружена",
            flush=True
        )

        return True

    except Exception as e:

        print(
            f"⚠️ Не удалось загрузить ML: {e}",
            flush=True
        )

        ml_model = None
        ml_initialized = False

        return False


# =====================================================================
# СТАТИСТИЧЕСКИЙ ПРОГНОЗ
# =====================================================================

def statistical_suit_prediction():

    counts = {
        suit: 0
        for suit in TARGET_SUITS
    }

    data = load_data()

    for record in data:

        suit = record.get(
            "target_suit"
        )

        if suit in counts:

            counts[suit] += 1

    # Если истории ещё нет
    if sum(counts.values()) == 0:

        return {
            suit: 0.25
            for suit in TARGET_SUITS
        }

    total = sum(
        counts.values()
    )

    probabilities = {}

    for suit in TARGET_SUITS:

        probabilities[suit] = (
            counts[suit] / total
        )

    return probabilities


# =====================================================================
# ПРОГНОЗ МАСТИ
# =====================================================================

def predict_suit(
    player_cards,
    dealer_cards,
    latency
):

    global ml_model

    features = get_game_features(
        player_cards,
        dealer_cards,
        latency
    )

    probabilities = {
        suit: 0.25
        for suit in TARGET_SUITS
    }

    ml_used = False

    # -------------------------------------------------------------
    # ML
    # -------------------------------------------------------------

    if (
        ml_initialized
        and
        ml_model is not None
    ):

        try:

            proba = ml_model.predict_proba(
                np.array([features])
            )[0]

            classes = ml_model.classes_

            for cls, probability in zip(
                classes,
                proba
            ):

                cls = int(cls)

                if 0 <= cls < len(
                    TARGET_SUITS
                ):

                    probabilities[
                        TARGET_SUITS[cls]
                    ] = float(
                        probability
                    )

            ml_used = True

        except Exception as e:

            print(
                f"⚠️ ML прогноз невозможен: {e}",
                flush=True
            )

    # -------------------------------------------------------------
    # Статистика
    # -------------------------------------------------------------

    if not ml_used:

        probabilities = (
            statistical_suit_prediction()
        )

    # -------------------------------------------------------------
    # Нормализация
    # -------------------------------------------------------------

    total = sum(
        probabilities.values()
    )

    if total <= 0:

        probabilities = {
            suit: 0.25
            for suit in TARGET_SUITS
        }

    else:

        probabilities = {
            suit:
                probabilities[suit] / total
            for suit in TARGET_SUITS
        }

    best_suit = max(
        probabilities,
        key=probabilities.get
    )

    confidence = probabilities[
        best_suit
    ]

    return {
        "suit": best_suit,
        "confidence": confidence,
        "probabilities": probabilities,
        "ml_used": ml_used,
        "features": features
    }


# =====================================================================
# ФОРМАТ КАРТ
# =====================================================================

def format_cards(cards):

    if not cards:

        return "нет карт"

    return " ".join(
        card_to_text(card)
        for card in cards
    )


# =====================================================================
# СОХРАНЕНИЕ ИГРЫ
# =====================================================================

def save_finished_game(
    game_id,
    game_num,
    player_cards,
    dealer_cards,
    state,
    latency,
    prediction=None
):

    target_suit = None

    if player_cards:

        first_suit = player_cards[0].get(
            "suit"
        )

        if first_suit in TARGET_SUITS:

            target_suit = first_suit

    record = {
        "game_id": str(game_id),

        "game_num": game_num,

        "timestamp":
            datetime.now(
                MOSCOW_TZ
            ).isoformat(),

        "player_cards":
            player_cards,

        "dealer_cards":
            dealer_cards,

        "player_cards_text":
            format_cards(player_cards),

        "dealer_cards_text":
            format_cards(dealer_cards),

        "state":
            state,

        "latency":
            latency or 0,

        "target_suit":
            target_suit,

        "finished":
            True
    }

    if prediction:

        record["prediction_suit"] = (
            prediction.get("suit")
        )

        record["prediction_confidence"] = (
            prediction.get("confidence", 0)
        )

        record["prediction_probabilities"] = (
            prediction.get(
                "probabilities",
                {}
            )
        )

        record["ml_used"] = (
            prediction.get(
                "ml_used",
                False
            )
        )

        record["features"] = (
            prediction.get(
                "features",
                []
            )
        )

    save_data(record)

    return record


# =====================================================================
# ПРОВЕРКА ПРОГНОЗА
# =====================================================================

def check_prediction(
    prediction,
    actual_suit
):

    if not prediction:
        return None

    predicted_suit = prediction.get(
        "suit"
    )

    if (
        predicted_suit not in TARGET_SUITS
        or
        actual_suit not in TARGET_SUITS
    ):

        return None

    win = (
        predicted_suit
        ==
        actual_suit
    )

    stats["total"] += 1

    if win:

        stats["win"] += 1

        stats["suit_hits"][
            actual_suit
        ] += 1

    else:

        stats["lose"] += 1

    if prediction.get(
        "ml_used"
    ):

        if win:
            stats["ml_wins"] += 1
        else:
            stats["ml_losses"] += 1

    return win


# =====================================================================
# ТЕКСТ ПРОГНОЗА
# =====================================================================

def build_prediction_message(
    game_num,
    prediction
):

    suit = prediction["suit"]

    confidence = (
        prediction["confidence"]
        * 100
    )

    probabilities = (
        prediction["probabilities"]
    )

    lines = []

    lines.append(
        "🎯 <b>ПРОГНОЗ МАСТИ</b>"
    )

    lines.append("")

    lines.append(
        f"🎰 Игра: <b>#{game_num}</b>"
    )

    lines.append(
        f"🃏 Прогноз: "
        f"<b>{suit} "
        f"{SUIT_NAMES_RU.get(suit, '')}</b>"
    )

    lines.append(
        f"📊 Уверенность: "
        f"<b>{confidence:.1f}%</b>"
    )

    lines.append("")

    for target in TARGET_SUITS:

        p = (
            probabilities.get(
                target,
                0
            )
            * 100
        )

        lines.append(
            f"{target} "
            f"{SUIT_NAMES_RU[target]}: "
            f"{p:.1f}%"
        )

    lines.append("")

    if prediction.get("ml_used"):

        lines.append(
            "🧠 Метод: <b>ML</b>"
        )

    else:

        lines.append(
            "📊 Метод: <b>статистика</b>"
        )

    lines.append("")

    lines.append(
        "⚠️ Прогноз показывает "
        "вероятностный выбор масти."
    )

    return "\n".join(lines)


# =====================================================================
# ТЕКСТ РЕЗУЛЬТАТА
# =====================================================================

def build_result_message(
    game_num,
    prediction,
    actual_suit,
    player_cards,
    dealer_cards
):

    predicted = prediction.get(
        "suit"
    ) if prediction else None

    win = (
        predicted == actual_suit
        if predicted and actual_suit
        else None
    )

    if win is True:

        result = "✅ ПОПАДАНИЕ"

    elif win is False:

        result = "❌ ПРОМАХ"

    else:

        result = "ℹ️ РЕЗУЛЬТАТ"

    text = []

    text.append(
        f"<b>{result}</b>"
    )

    text.append("")

    text.append(
        f"🎰 Игра: <b>#{game_num}</b>"
    )

    if predicted:

        text.append(
            f"🎯 Прогноз: "
            f"{predicted} "
            f"{SUIT_NAMES_RU.get(predicted, '')}"
        )

    if actual_suit:

        text.append(
            f"🎴 Факт: "
            f"<b>{actual_suit} "
            f"{SUIT_NAMES_RU.get(actual_suit, '')}</b>"
        )

    text.append("")

    text.append(
        f"👤 Игрок: "
        f"<b>{format_cards(player_cards)}</b>"
    )

    text.append(
        f"🏦 Дилер: "
        f"<b>{format_cards(dealer_cards)}</b>"
    )

    return "\n".join(text)


# =====================================================================
# ОБРАБОТКА ОДНОЙ ИГРЫ
# =====================================================================

def process_game(game):

    global last_prediction

    if not isinstance(game, dict):
        return False

    game_id = (
        game.get("id")
        or game.get("Id")
        or game.get("ID")
        or game.get("gameId")
        or game.get("GameId")
    )

    if game_id is None:
        return False

    game_id = str(game_id)

    game_num = get_game_number_by_time()

    data, latency, start_time, end_time = (
        get_game_data(game_id)
    )

    if data is None:

        return False

    (
        player_cards,
        dealer_cards,
        state
    ) = parse_cards_and_state(data)

    # -------------------------------------------------------------
    # ВАЖНАЯ ДИАГНОСТИКА
    # -------------------------------------------------------------

    print(
        f"🎮 Игра {game_id}: "
        f"P1={len(player_cards)} "
        f"P2={len(dealer_cards)} "
        f"STATE={state}",
        flush=True
    )

    if player_cards:

        print(
            f"   👤 Игрок: "
            f"{format_cards(player_cards)}",
            flush=True
        )

    if dealer_cards:

        print(
            f"   🏦 Дилер: "
            f"{format_cards(dealer_cards)}",
            flush=True
        )

    # -------------------------------------------------------------
    # Если карт нет — НЕ сохраняем пустую игру
    # -------------------------------------------------------------

    if (
        not player_cards
        and
        not dealer_cards
    ):

        print(
            f"⚠️ Игра {game_id}: "
            f"карты API пока не найдены",
            flush=True
        )

        return False

    finished = is_game_finished(
        player_cards,
        dealer_cards,
        state
    )

    # -------------------------------------------------------------
    # Если игра ещё не закончена,
    # просто обновляем историю API.
    # -------------------------------------------------------------

    if not finished:

        print(
            f"⏳ Игра {game_id} ещё идёт",
            flush=True
        )

        return False

    # -------------------------------------------------------------
    # Не обрабатываем завершённую игру повторно
    # -------------------------------------------------------------

    if game_id in finished_games:

        return False

    # -------------------------------------------------------------
    # Сначала добавляем историю
    # -------------------------------------------------------------

    update_game_history(
        latency,
        player_cards,
        dealer_cards,
        game_num
    )

    # -------------------------------------------------------------
    # Прогноз следующей масти
    # -------------------------------------------------------------

    prediction = predict_suit(
        player_cards,
        dealer_cards,
        latency
    )

    last_prediction = {
        "game_id": game_id,
        "game_num": game_num,
        **prediction
    }

    # -------------------------------------------------------------
    # Фактическая масть игрока
    # -------------------------------------------------------------

    actual_suit = None

    for card in player_cards:

        suit = card.get(
            "suit"
        )

        if suit in TARGET_SUITS:

            actual_suit = suit

            break

    # -------------------------------------------------------------
    # Сохраняем игру
    # -------------------------------------------------------------

    record = save_finished_game(
        game_id,
        game_num,
        player_cards,
        dealer_cards,
        state,
        latency,
        prediction
    )

    finished_games.add(
        game_id
    )

    processed_games.add(
        game_id
    )

    # -------------------------------------------------------------
    # Проверяем прогноз
    # -------------------------------------------------------------

    result = check_prediction(
        prediction,
        actual_suit
    )

    # -------------------------------------------------------------
    # Вывод результата
    # -------------------------------------------------------------

    if result is True:

        print(
            f"✅ ПОПАДАНИЕ: "
            f"{prediction['suit']} "
            f"== {actual_suit}",
            flush=True
        )

    elif result is False:

        print(
            f"❌ ПРОМАХ: "
            f"{prediction['suit']} "
            f"!= {actual_suit}",
            flush=True
        )

    # -------------------------------------------------------------
    # Отправляем результат в статистический канал
    # -------------------------------------------------------------

    result_message = build_result_message(
        game_num,
        prediction,
        actual_suit,
        player_cards,
        dealer_cards
    )

    send_message(
        CHANNEL_STATS,
        result_message
    )

    # -------------------------------------------------------------
    # Следующий прогноз
    # -------------------------------------------------------------

    next_game_num = game_num + 1

    prediction_message = build_prediction_message(
        next_game_num,
        prediction
    )

    send_message(
        CHANNEL_PROGNOZ,
        prediction_message
    )

    # -------------------------------------------------------------
    # Периодическое переобучение
    # -------------------------------------------------------------

    data_count = len(
        load_data()
    )

    if (
        ML_AVAILABLE
        and
        data_count % 50 == 0
        and
        data_count >= MIN_TRAIN_SAMPLES
    ):

        train_ml()

    return True


# =====================================================================
# ПРИНУДИТЕЛЬНОЕ ПОЛУЧЕНИЕ ИГР
# =====================================================================

def collect_games():

    global last_games_debug

    games = get_active_games()

    last_games_debug = games

    if not games:

        print(
            "⚠️ collect_games(): игр нет",
            flush=True
        )

        return 0

    print(
        f"🎮 НАЧИНАЮ ОБРАБОТКУ "
        f"{len(games)} ИГР",
        flush=True
    )

    processed_count = 0

    # -------------------------------------------------------------
    # Новые игры обрабатываем первыми
    # -------------------------------------------------------------

    games_sorted = sorted(
        games,
        key=lambda x: safe_int(
            x.get("id", 0),
            0
        )
    )

    for game in games_sorted:

        try:

            if process_game(game):

                processed_count += 1

        except Exception as e:

            print(
                f"❌ Ошибка process_game: {e}",
                flush=True
            )

            traceback.print_exc()

        time.sleep(0.15)

    print(
        f"📊 За цикл обработано: "
        f"{processed_count}",
        flush=True
    )

    return processed_count


# =====================================================================
# СТАТИСТИКА
# =====================================================================

def get_statistics_text():

    data = load_data()

    total = stats["total"]
    wins = stats["win"]
    losses = stats["lose"]

    if total > 0:

        percent = (
            wins / total
        ) * 100

    else:

        percent = 0

    suit_counts = {
        suit: 0
        for suit in TARGET_SUITS
    }

    for record in data:

        suit = record.get(
            "target_suit"
        )

        if suit in suit_counts:

            suit_counts[suit] += 1

    text = []

    text.append(
        "📊 <b>СТАТИСТИКА БАККАРЫ</b>"
    )

    text.append("")

    text.append(
        f"🎮 Всего сохранено игр: "
        f"<b>{len(data)}</b>"
    )

    text.append(
        f"🎯 Проверено прогнозов: "
        f"<b>{total}</b>"
    )

    text.append(
        f"✅ Попаданий: "
        f"<b>{wins}</b>"
    )

    text.append(
        f"❌ Промахов: "
        f"<b>{losses}</b>"
    )

    text.append(
        f"📈 Процент: "
        f"<b>{percent:.1f}%</b>"
    )

    text.append("")

    text.append(
        "🎴 <b>МАСТИ:</b>"
    )

    for suit in TARGET_SUITS:

        text.append(
            f"{suit} "
            f"{SUIT_NAMES_RU[suit]}: "
            f"{suit_counts[suit]}"
        )

    text.append("")

    text.append(
        f"🧠 ML: "
        f"<b>{'АКТИВНА' if ml_initialized else 'НЕТ'}</b>"
    )

    text.append(
        f"🎰 Лига: "
        f"<b>{BACCARAT_LEAGUE_ID}</b>"
    )

    return "\n".join(text)


# =====================================================================
# КОМАНДЫ TELEGRAM
# =====================================================================

def process_telegram_updates():

    global telegram_offset

    updates = get_updates(
        telegram_offset
    )

    if not updates:
        return

    if not updates.get("ok"):
        return

    results = updates.get(
        "result",
        []
    )

    for update in results:

        update_id = update.get(
            "update_id"
        )

        if update_id is not None:

            telegram_offset = (
                update_id + 1
            )

            save_offset(
                telegram_offset
            )

        message = update.get(
            "message"
        )

        if not message:
            continue

        text = safe_str(
            message.get("text")
        )

        chat_id = message.get(
            "chat",
            {}
        ).get("id")

        if not text or chat_id is None:
            continue

        command = text.lower().strip()

        if command == "/stats":

            send_message(
                chat_id,
                get_statistics_text()
            )

        elif command == "/games":

            data = load_data()

            last = data[-10:]

            if not last:

                send_message(
                    chat_id,
                    "🎮 Игр пока нет."
                )

                continue

            lines = [
                "🎮 <b>ПОСЛЕДНИЕ ИГРЫ</b>",
                ""
            ]

            for record in reversed(last):

                game_num = record.get(
                    "game_num",
                    "?"
                )

                player = record.get(
                    "player_cards_text",
                    ""
                )

                dealer = record.get(
                    "dealer_cards_text",
                    ""
                )

                lines.append(
                    f"#{game_num} "
                    f"👤 {player} "
                    f"🏦 {dealer}"
                )

            send_message(
                chat_id,
                "\n".join(lines)
            )

        elif command == "/debug":

            games = last_games_debug

            lines = [
                "🔍 <b>DEBUG API</b>",
                "",
                f"Найдено игр: {len(games)}",
                ""
            ]

            for game in games[:10]:

                game_id = (
                    game.get("id")
                    or game.get("Id")
                )

                liga = game.get(
                    "liga",
                    {}
                )

                if not isinstance(
                    liga,
                    dict
                ):
                    liga = {}

                lines.append(
                    f"🎮 {game_id} | "
                    f"liga={liga.get('id')} | "
                    f"{liga.get('name')}"
                )

            send_message(
                chat_id,
                "\n".join(lines)
            )

        elif command == "/ml":

            data_count = len(
                load_data()
            )

            if (
                data_count
                >= MIN_TRAIN_SAMPLES
            ):

                success = train_ml()

                if success:

                    send_message(
                        chat_id,
                        "🧠 ML успешно переобучена."
                    )

                else:

                    send_message(
                        chat_id,
                        "⚠️ ML не удалось обучить."
                    )

            else:

                send_message(
                    chat_id,
                    f"🧠 Пока мало данных.\n"
                    f"Есть: {data_count}\n"
                    f"Нужно: {MIN_TRAIN_SAMPLES}"
                )

        elif command == "/start":

            send_message(
                chat_id,
                "🎰 <b>Баккара-бот запущен.</b>\n\n"
                "/stats — статистика\n"
                "/games — последние игры\n"
                "/debug — диагностика API\n"
                "/ml — переобучение ML"
            )


# =====================================================================
# ПРОВЕРКА API
# =====================================================================

def api_diagnostic():

    print("=" * 60, flush=True)
    print(
        "🔍 ПЕРВИЧНАЯ ПРОВЕРКА API...",
        flush=True
    )
    print("=" * 60, flush=True)

    games = get_active_games()

    if not games:

        print(
            "⚠️ ПЕРВИЧНАЯ ПРОВЕРКА: "
            "игры не найдены.",
            flush=True
        )

        return False

    print(
        f"✅ ПЕРВИЧНАЯ ПРОВЕРКА: "
        f"найдено {len(games)} игр.",
        flush=True
    )

    # Проверяем первые 3 игры

    for game in games[:3]:

        game_id = (
            game.get("id")
            or game.get("Id")
            or game.get("ID")
        )

        if not game_id:
            continue

        print(
            f"🔎 Проверяю игру {game_id}...",
            flush=True
        )

        data, latency, _, _ = get_game_data(
            game_id
        )

        if data is None:

            print(
                f"❌ Данные игры "
                f"{game_id} не получены.",
                flush=True
            )

            continue

        player_cards, dealer_cards, state = (
            parse_cards_and_state(data)
        )

        print(
            f"   P1: {format_cards(player_cards)}",
            flush=True
        )

        print(
            f"   P2: {format_cards(dealer_cards)}",
            flush=True
        )

        print(
            f"   STATE: {state}",
            flush=True
        )

        print(
            f"   LATENCY: {latency:.1f} ms"
            if latency
            else "   LATENCY: ?",
            flush=True
        )

    print("=" * 60, flush=True)

    return True


# =====================================================================
# ИНИЦИАЛИЗАЦИЯ
# =====================================================================

def initialize():

    global game_history
    global telegram_offset

    print("=" * 60, flush=True)
    print(
        "🚀 ИНИЦИАЛИЗАЦИЯ БОТА",
        flush=True
    )
    print("=" * 60, flush=True)

    # -------------------------------------------------------------
    # История
    # -------------------------------------------------------------

    game_history = load_game_history()

    print(
        f"💾 История API: "
        f"{len(game_history)} игр",
        flush=True
    )

    # -------------------------------------------------------------
    # Уже обработанные игры
    # -------------------------------------------------------------

    restore_processed_games()

    # -------------------------------------------------------------
    # Telegram offset
    # -------------------------------------------------------------

    telegram_offset = get_offset()

    print(
        f"📨 Telegram offset: "
        f"{telegram_offset}",
        flush=True
    )

    # -------------------------------------------------------------
    # ML
    # -------------------------------------------------------------

    load_ml_model()

    data_count = len(
        load_data()
    )

    if (
        ML_AVAILABLE
        and
        not ml_initialized
        and
        data_count >= MIN_TRAIN_SAMPLES
    ):

        train_ml()

    # -------------------------------------------------------------
    # API
    # -------------------------------------------------------------

    api_diagnostic()

    # -------------------------------------------------------------
    # Startup
    # -------------------------------------------------------------

    send_startup_message()

    print("=" * 60, flush=True)
    print(
        "✅ ИНИЦИАЛИЗАЦИЯ ЗАВЕРШЕНА",
        flush=True
    )
    print("=" * 60, flush=True)


# =====================================================================
# MAIN LOOP
# =====================================================================

def main():

    initialize()

    cycle = 0

    while True:

        cycle += 1

        print(
            "\n"
            + "=" * 60,
            flush=True
        )

        print(
            f"🔄 ЦИКЛ #{cycle}",
            flush=True
        )

        print(
            f"⏰ "
            f"{datetime.now(MOSCOW_TZ).strftime('%d.%m.%Y %H:%M:%S')}",
            flush=True
        )

        print(
            "=" * 60,
            flush=True
        )

        try:

            # -----------------------------------------------------
            # Telegram
            # -----------------------------------------------------

            process_telegram_updates()

            # -----------------------------------------------------
            # Игры
            # -----------------------------------------------------

            collect_games()

            # -----------------------------------------------------
            # Периодическая очистка памяти
            # -----------------------------------------------------

            if cycle % 100 == 0:

                gc.collect()

            # -----------------------------------------------------
            # Переобучение ML
            # -----------------------------------------------------

            if cycle % 200 == 0:

                data_count = len(
                    load_data()
                )

                if (
                    ML_AVAILABLE
                    and
                    data_count >= MIN_TRAIN_SAMPLES
                ):

                    train_ml()

            # -----------------------------------------------------
            # Пауза
            # -----------------------------------------------------

            print(
                f"😴 Следующая проверка "
                f"через {CHECK_INTERVAL} сек.",
                flush=True
            )

            time.sleep(
                CHECK_INTERVAL
            )

        except KeyboardInterrupt:

            print(
                "\n🛑 БОТ ОСТАНОВЛЕН",
                flush=True
            )

            break

        except Exception as e:

            print(
                f"❌ ОШИБКА MAIN: {e}",
                flush=True
            )

            traceback.print_exc()

            print(
                "🔄 Продолжаю работу...",
                flush=True
            )

            time.sleep(
                CHECK_INTERVAL
            )


# =====================================================================
# ЗАПУСК
# =====================================================================

if __name__ == "__main__":

    try:

        main()

    except KeyboardInterrupt:

        print(
            "\n🛑 Завершение...",
            flush=True
        )

    except Exception as e:

        print(
            f"❌ КРИТИЧЕСКАЯ ОШИБКА: {e}",
            flush=True
        )

        traceback.print_exc()