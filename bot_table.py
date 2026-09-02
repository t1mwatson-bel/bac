import os
import sys
import json
import re
import time
import pickle
import traceback
from datetime import datetime, timedelta
from collections import defaultdict

import numpy as np
import requests
import pytz


# =====================================================================
# ENV
# =====================================================================

BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("BOT_TOKEN_PROGNOZ")

CHANNEL_STATS = (
    os.getenv("CHANNEL_STATS_21")
    or os.getenv("CHANNEL_STATS")
)

CHANNEL_PROGNOZ = (
    os.getenv("CHANNEL_PROGNOZ_21")
    or os.getenv("CHAT_ID_21")
    or os.getenv("CHAT_ID")
)

if not BOT_TOKEN:
    print(
        "❌ ОШИБКА: BOT_TOKEN не задан!",
        flush=True
    )
    sys.exit(1)

if not CHANNEL_STATS:
    print(
        "❌ ОШИБКА: CHANNEL_STATS_21 не задан!",
        flush=True
    )
    sys.exit(1)

if not CHANNEL_PROGNOZ:
    print(
        "❌ ОШИБКА: CHANNEL_PROGNOZ_21 / CHAT_ID_21 не задан!",
        flush=True
    )
    sys.exit(1)

CHANNEL_STATS = str(CHANNEL_STATS).strip()
CHANNEL_PROGNOZ = str(CHANNEL_PROGNOZ).strip()

MOSCOW_TZ = pytz.timezone("Europe/Moscow")


# =====================================================================
# API
# =====================================================================

BASE_URL = "https://1xlite-36553.pro"

SPORT_ID = 146
LIGA_ID = 1643503
GR = 415

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/139.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": (
        f"{BASE_URL}/ru/live/twentyone/"
        f"{LIGA_ID}-twentyone-game"
    ),
    "Cookie": (
        "platform_type=desktop; "
        "lng=ru; "
        "cookies_agree_type=3; "
        "tzo=3; "
        "is12h=0"
    ),
}


# =====================================================================
# FILES
# =====================================================================

DATA_FILE = "twentyone_data_full.json"

PREDICTIONS_FILE = "twentyone_predictions.json"

OFFSET_FILE = "twentyone_offset.txt"

SCANNER_FILE = "twentyone_pattern_scanner.pkl"


# =====================================================================
# SETTINGS
# =====================================================================

DOGON_GAMES = 4

CHECK_INTERVAL = 5

# ВАЖНО:
# Историческая база не должна неожиданно обрезаться.
# Оставляем большой лимит.
MAX_RECORDS = 10000

MIN_TRAIN_SAMPLES = 50

ANALYTICS_INTERVAL = 3600


# =====================================================================
# TARGET CARDS
# =====================================================================

TARGET_RANKS = [
    "J",
    "Q",
    "K",
    "A",
]

TARGET_SUITS = [
    "♠️",
    "♣️",
    "♦️",
    "♥️",
]

TARGET_CARDS = [
    f"{rank}{suit}"
    for rank in TARGET_RANKS
    for suit in TARGET_SUITS
]

TARGET_CARD_INDEX = {
    card: i
    for i, card in enumerate(TARGET_CARDS)
}


# =====================================================================
# PATTERN SCANNER SETTINGS
# =====================================================================

PATTERN_MIN_SUPPORT = 12

PATTERN_MIN_PRECISION = 0.30

PATTERN_MIN_LIFT = 1.05

PATTERN_MAX_FEATURES = 300

PATTERN_LAGS = (
    1,
    2,
    3,
    4,
    5,
    6,
    8,
    10,
)

PATTERN_WINDOWS = (
    3,
    5,
    8,
    12,
    20,
)


# =====================================================================
# GLOBALS
# =====================================================================

predictions = []

# =====================================================================
# ВАЖНО:
#
# games_cache используется ТОЛЬКО для результатов,
# которые реально пришли из CHANNEL_STATS в текущем процессе.
#
# Старые записи из twentyone_data_full.json сюда НЕ загружаются.
#
# Это исправляет проблему:
#
# вчера #N1140
#      ↓
# сегодня прогноз #N1140
#      ↓
# старая #N1140 ошибочно воспринималась как сегодняшняя.
# =====================================================================

games_cache = {}

scanner_patterns = []

scanner_last_train_count = 0

last_analytics_time = 0


# =====================================================================
# STATS
# =====================================================================

stats = {
    "total": 0,
    "win": 0,
    "lose": 0,

    "by_dogon": {
        0: 0,
        1: 0,
        2: 0,
        3: 0,
    },

    "card_hits": defaultdict(int),
}


# =====================================================================
# TELEGRAM
# =====================================================================

def telegram_request(
    method,
    payload=None,
    timeout=20
):
    url = (
        f"https://api.telegram.org/"
        f"bot{BOT_TOKEN}/{method}"
    )

    try:
        response = requests.post(
            url,
            json=payload or {},
            timeout=timeout
        )

        if response.status_code != 200:
            print(
                f"⚠️ Telegram HTTP {response.status_code}",
                flush=True
            )
            return None

        data = response.json()

        if not data.get("ok"):
            print(
                f"⚠️ Telegram API ошибка: "
                f"{data.get('description', 'unknown')}",
                flush=True
            )
            return None

        return data

    except Exception as e:
        print(
            f"⚠️ Telegram ошибка: {e}",
            flush=True
        )
        return None


def send_message(
    chat_id,
    text
):
    result = telegram_request(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
        },
        timeout=15
    )

    if not result:
        return None

    return (
        result
        .get("result", {})
        .get("message_id")
    )


def edit_message(
    message_id,
    text
):
    result = telegram_request(
        "editMessageText",
        {
            "chat_id": CHANNEL_PROGNOZ,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML",
        },
        timeout=15
    )

    return bool(result)


# =====================================================================
# DATA LOAD / SAVE
# =====================================================================

def load_data():

    if not os.path.exists(DATA_FILE):
        return []

    try:

        with open(
            DATA_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(f)

        if isinstance(data, list):
            return data

        if isinstance(data, dict):

            for key in (
                "data",
                "games",
                "records",
                "items",
                "history",
            ):

                if isinstance(
                    data.get(key),
                    list
                ):

                    return data[key]

    except Exception as e:

        print(
            f"⚠️ Ошибка загрузки {DATA_FILE}: {e}",
            flush=True
        )

    return []


def save_data(data):

    try:

        # Не режем историческую базу,
        # если она меньше MAX_RECORDS.
        if len(data) > MAX_RECORDS:
            data = data[-MAX_RECORDS:]

        tmp_file = DATA_FILE + ".tmp"

        with open(
            tmp_file,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                data,
                f,
                indent=2,
                ensure_ascii=False
            )

        os.replace(
            tmp_file,
            DATA_FILE
        )

        return True

    except Exception as e:

        print(
            f"⚠️ Ошибка сохранения {DATA_FILE}: {e}",
            flush=True
        )

        return False


def load_predictions():

    if not os.path.exists(
        PREDICTIONS_FILE
    ):
        return []

    try:

        with open(
            PREDICTIONS_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(f)

        return (
            data
            if isinstance(data, list)
            else []
        )

    except Exception as e:

        print(
            f"⚠️ Ошибка загрузки прогнозов: {e}",
            flush=True
        )

        return []


def save_predictions():

    try:

        tmp_file = (
            PREDICTIONS_FILE
            + ".tmp"
        )

        with open(
            tmp_file,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                predictions,
                f,
                indent=2,
                ensure_ascii=False
            )

        os.replace(
            tmp_file,
            PREDICTIONS_FILE
        )

        return True

    except Exception as e:

        print(
            f"⚠️ Ошибка сохранения прогнозов: {e}",
            flush=True
        )

        return False


# =====================================================================
# TELEGRAM OFFSET
# =====================================================================

def get_offset():

    try:

        with open(
            OFFSET_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            return int(
                f.read().strip()
            )

    except Exception:

        return 0


def save_offset(offset):

    try:

        with open(
            OFFSET_FILE,
            "w",
            encoding="utf-8"
        ) as f:

            f.write(
                str(offset)
            )

    except Exception:

        pass


# =====================================================================
# NORMALIZATION
# =====================================================================

def normalize_suit(suit):

    if not suit:
        return None

    s = (
        str(suit)
        .replace("\ufe0f", "")
        .strip()
    )

    return {
        "♠": "♠️",
        "♣": "♣️",
        "♦": "♦️",
        "♥": "♥️",
    }.get(s)


def normalize_rank(rank):

    if rank is None:
        return None

    value = str(
        rank
    ).strip().upper()

    if value == "10":
        return "10"

    if value in (
        "2",
        "3",
        "4",
        "5",
        "6",
        "7",
        "8",
        "9",
        "J",
        "Q",
        "K",
        "A",
    ):
        return value

    return None


def make_card(
    rank,
    suit
):

    rank = normalize_rank(
        rank
    )

    suit = normalize_suit(
        suit
    )

    if not rank or not suit:
        return None

    return f"{rank}{suit}"


# =====================================================================
# CARD PARSER
# =====================================================================

CARD_REGEX = re.compile(
    r"(10|[2-9JQKA])([♠♣♦♥])"
)


def parse_cards_string(text):

    if not text:
        return []

    clean = (
        str(text)
        .replace("\ufe0f", "")
    )

    result = []

    for rank, suit in CARD_REGEX.findall(
        clean
    ):

        normalized_suit = normalize_suit(
            suit
        )

        if not normalized_suit:
            continue

        result.append(
            {
                "rank": rank,
                "suit": normalized_suit,
            }
        )

    return result


# =====================================================================
# TELEGRAM STATS PARSER
#
# Пример:
#
# #N476. ✅17(K♥️Q♠️K♦️6♣️) - 25(K♣️J♥️8♦️A♠️) #T42
#
# Первая скобка  = игрок
# Вторая скобка  = дилер
# =====================================================================

def parse_stats_message(text):

    if not text:
        return None

    clean = (
        str(text)
        .replace("\ufe0f", "")
        .replace("\n", " ")
    )

    game_match = re.search(
        r"#N\s*(\d+)",
        clean,
        re.IGNORECASE
    )

    if not game_match:
        return None

    game_number = int(
        game_match.group(1)
    )

    groups = re.findall(
        r"-?\d+\(([^)]*)\)",
        clean
    )

    if len(groups) < 2:
        return None

    player_cards = parse_cards_string(
        groups[0]
    )

    dealer_cards = parse_cards_string(
        groups[1]
    )

    if not player_cards and not dealer_cards:
        return None

    all_cards = (
        player_cards
        + dealer_cards
    )

    all_suits = [
        card["suit"]
        for card in all_cards
    ]

    all_ranks = [
        card["rank"]
        for card in all_cards
    ]

    exact_cards = [
        make_card(
            card["rank"],
            card["suit"]
        )
        for card in all_cards
    ]

    exact_cards = [
        card
        for card in exact_cards
        if card
    ]

    return {
        "game_number":
            game_number,

        "player_cards":
            player_cards,

        "dealer_cards":
            dealer_cards,

        "all_cards":
            all_cards,

        "all_suits":
            all_suits,

        "all_ranks":
            all_ranks,

        "exact_cards":
            exact_cards,

        "source":
            "telegram_stats",

        "raw_text":
            text,

        "received_at":
            datetime.now(
                MOSCOW_TZ
            ).isoformat(),
    }


# =====================================================================
# RECORD HELPERS
# =====================================================================

def cards_from_record(
    record,
    field
):

    if not isinstance(
        record,
        dict
    ):
        return []

    cards = record.get(
        field,
        []
    )

    if not isinstance(
        cards,
        list
    ):
        return []

    result = []

    for card in cards:

        if isinstance(
            card,
            dict
        ):

            rank = normalize_rank(
                card.get("rank")
            )

            suit = normalize_suit(
                card.get("suit")
            )

            if rank and suit:

                result.append(
                    {
                        "rank": rank,
                        "suit": suit,
                    }
                )

        elif isinstance(
            card,
            str
        ):

            parsed = parse_cards_string(
                card
            )

            result.extend(
                parsed
            )

    return result


def record_all_cards(record):

    player = cards_from_record(
        record,
        "player_cards"
    )

    dealer = cards_from_record(
        record,
        "dealer_cards"
    )

    return player + dealer


def record_exact_cards(record):

    result = []

    for card in record_all_cards(
        record
    ):

        exact = make_card(
            card["rank"],
            card["suit"]
        )

        if exact:
            result.append(exact)

    return result


def record_has_card(
    record,
    target_card
):

    return target_card in set(
        record_exact_cards(record)
    )


# =====================================================================
# ADD STATS GAME TO DATA
# =====================================================================

def add_stats_game_to_data(
    parsed
):

    if not parsed:
        return False

    game_number = parsed.get(
        "game_number"
    )

    if game_number is None:
        return False

    data = load_data()

    new_exact_cards = set(
        parsed.get(
            "exact_cards",
            []
        )
    )

    # =============================================================
    # Ищем игру по номеру.
    #
    # Это только база данных.
    # На результат прогноза это НЕ влияет.
    # =============================================================

    for old in data:

        old_num = old.get(
            "game_number"
        )

        if old_num is None:

            try:

                old_num = int(
                    old.get(
                        "game_id"
                    )
                )

            except Exception:

                old_num = None

        if old_num != game_number:
            continue

        old_cards = set(
            record_exact_cards(old)
        )

        # Полностью одинаковая запись.
        if old_cards == new_exact_cards:

            # Если старая запись уже была от Telegram,
            # ничего делать не надо.
            if old.get("source") == "telegram_stats":
                return False

            # Если старая запись была исторической,
            # всё равно можем пометить её источником Telegram.
            old["source"] = "telegram_stats"

            old["raw_text"] = parsed.get(
                "raw_text",
                ""
            )

            old["received_at"] = parsed.get(
                "received_at"
            )

            save_data(data)

            return True

        # =========================================================
        # Существующая запись была неполной.
        # Обновляем её реальными данными статистики.
        # =========================================================

        old["player_cards"] = parsed.get(
            "player_cards",
            []
        )

        old["dealer_cards"] = parsed.get(
            "dealer_cards",
            []
        )

        old["all_cards"] = parsed.get(
            "all_cards",
            []
        )

        old["all_suits"] = parsed.get(
            "all_suits",
            []
        )

        old["all_ranks"] = parsed.get(
            "all_ranks",
            []
        )

        old["exact_cards"] = parsed.get(
            "exact_cards",
            []
        )

        old["raw_text"] = parsed.get(
            "raw_text",
            ""
        )

        old["source"] = "telegram_stats"

        old["received_at"] = parsed.get(
            "received_at"
        )

        save_data(data)

        return True

    # =============================================================
    # Новая игра.
    # =============================================================

    now = datetime.now(
        MOSCOW_TZ
    )

    record = {
        "game_id":
            str(game_number),

        "game_number":
            int(game_number),

        "timestamp_msk":
            now.strftime(
                "%H:%M:%S"
            ),

        "recorded_at":
            now.isoformat(),

        "state":
            "telegram",

        "player_cards":
            parsed.get(
                "player_cards",
                []
            ),

        "dealer_cards":
            parsed.get(
                "dealer_cards",
                []
            ),

        "all_cards":
            parsed.get(
                "all_cards",
                []
            ),

        "all_suits":
            parsed.get(
                "all_suits",
                []
            ),

        "all_ranks":
            parsed.get(
                "all_ranks",
                []
            ),

        "exact_cards":
            parsed.get(
                "exact_cards",
                []
            ),

        "raw_text":
            parsed.get(
                "raw_text",
                ""
            ),

        "source":
            "telegram_stats",

        "received_at":
            parsed.get(
                "received_at"
            ),
    }

    data.append(
        record
    )

    return save_data(
        data
    )


# =====================================================================
# CACHE STATS RESULT
#
# ВАЖНО:
# games_cache пополняется только из CHANNEL_STATS.
# =====================================================================

def cache_result(
    parsed
):

    if not parsed:
        return

    num = parsed.get(
        "game_number"
    )

    if num is None:
        return

    num = int(num)

    # -------------------------------------------------------------
    # Защита от повторного Telegram-сообщения.
    #
    # Если пришёл тот же результат второй раз,
    # просто заменяем тем же содержимым.
    # -------------------------------------------------------------

    games_cache[num] = parsed

    # Ограничиваем cache.
    if len(games_cache) > 2000:

        keys = sorted(
            games_cache.keys()
        )

        for key in keys[:-1000]:

            games_cache.pop(
                key,
                None
            )


# =====================================================================
# GAME NUMBER
# =====================================================================

def get_game_number_from_timestamp(
    ts
):

    if ts is None:
        return None

    try:

        if isinstance(
            ts,
            (int, float)
        ):

            dt = datetime.fromtimestamp(
                ts,
                MOSCOW_TZ
            )

        else:

            value = str(ts)

            if value.endswith("Z"):

                value = (
                    value[:-1]
                    + "+00:00"
                )

            dt = datetime.fromisoformat(
                value
            )

            if dt.tzinfo is None:

                dt = MOSCOW_TZ.localize(
                    dt
                )

            else:

                dt = dt.astimezone(
                    MOSCOW_TZ
                )

    except Exception:

        return None

    start = dt.replace(
        hour=3,
        minute=0,
        second=0,
        microsecond=0
    )

    if dt < start:

        start -= timedelta(
            days=1
        )

    minutes = int(
        (
            dt - start
        ).total_seconds()
        // 60
    )

    return (
        minutes % 1440
    ) + 1


def add_game_offset(
    num,
    offset
):

    return (
        (
            int(num)
            - 1
            + int(offset)
        )
        % 1440
    ) + 1


# =====================================================================
# SIGNATURE
# =====================================================================

def card_counter(
    cards
):

    result = defaultdict(int)

    for card in cards:

        exact = make_card(
            card.get("rank"),
            card.get("suit")
        )

        if exact:

            result[
                exact
            ] += 1

    return result


def rank_counter(
    cards
):

    result = defaultdict(int)

    for card in cards:

        rank = normalize_rank(
            card.get("rank")
        )

        if rank:

            result[
                rank
            ] += 1

    return result


def suit_counter(
    cards
):

    result = defaultdict(int)

    for card in cards:

        suit = normalize_suit(
            card.get("suit")
        )

        if suit:

            result[
                suit
            ] += 1

    return result


def record_signature(
    record
):

    player_cards = cards_from_record(
        record,
        "player_cards"
    )

    dealer_cards = cards_from_record(
        record,
        "dealer_cards"
    )

    all_cards = (
        player_cards
        + dealer_cards
    )

    exact_counts = card_counter(
        all_cards
    )

    player_exact_counts = card_counter(
        player_cards
    )

    dealer_exact_counts = card_counter(
        dealer_cards
    )

    rank_counts = rank_counter(
        all_cards
    )

    player_rank_counts = rank_counter(
        player_cards
    )

    dealer_rank_counts = rank_counter(
        dealer_cards
    )

    suit_counts = suit_counter(
        all_cards
    )

    player_suit_counts = suit_counter(
        player_cards
    )

    dealer_suit_counts = suit_counter(
        dealer_cards
    )

    return {
        "player_cards":
            player_cards,

        "dealer_cards":
            dealer_cards,

        "all_cards":
            all_cards,

        "exact_counts":
            exact_counts,

        "player_exact_counts":
            player_exact_counts,

        "dealer_exact_counts":
            dealer_exact_counts,

        "rank_counts":
            rank_counts,

        "player_rank_counts":
            player_rank_counts,

        "dealer_rank_counts":
            dealer_rank_counts,

        "suit_counts":
            suit_counts,

        "player_suit_counts":
            player_suit_counts,

        "dealer_suit_counts":
            dealer_suit_counts,

        "total_cards":
            len(all_cards),

        "player_count":
            len(player_cards),

        "dealer_count":
            len(dealer_cards),

        "game_num":
            record.get(
                "game_number"
            ),

        "state":
            str(
                record.get(
                    "state",
                    ""
                )
            ),
    }


# =====================================================================
# STREAK
# =====================================================================

def tail_streak(
    values
):

    count = 0

    for value in reversed(
        values
    ):

        if value:

            count += 1

        else:

            break

    return count


# =====================================================================
# PATTERN FEATURES
# =====================================================================

def build_scanner_feature_map(
    data,
    idx
):

    features = {}

    if idx <= 0:
        return features

    history = [
        record_signature(
            record
        )
        for record in data[:idx]
    ]

    if not history:
        return features

    current = history[-1]

    # =============================================================
    # LAGS
    # =============================================================

    for lag in PATTERN_LAGS:

        position = (
            len(history)
            - lag
        )

        if position < 0:
            continue

        historical = history[
            position
        ]

        # ---------------------------------------------------------
        # ТОЧНЫЕ КАРТЫ
        # ---------------------------------------------------------

        for card in TARGET_CARDS:

            features[
                f"lag{lag}_exact_{card}"
            ] = int(
                historical[
                    "exact_counts"
                ].get(
                    card,
                    0
                ) > 0
            )

            features[
                f"lag{lag}_player_{card}"
            ] = int(
                historical[
                    "player_exact_counts"
                ].get(
                    card,
                    0
                ) > 0
            )

            features[
                f"lag{lag}_dealer_{card}"
            ] = int(
                historical[
                    "dealer_exact_counts"
                ].get(
                    card,
                    0
                ) > 0
            )

        # ---------------------------------------------------------
        # РАНГИ
        # ---------------------------------------------------------

        for rank in (
            "2",
            "3",
            "4",
            "5",
            "6",
            "7",
            "8",
            "9",
            "10",
            "J",
            "Q",
            "K",
            "A",
        ):

            features[
                f"lag{lag}_rank_{rank}"
            ] = int(
                historical[
                    "rank_counts"
                ].get(
                    rank,
                    0
                ) > 0
            )

            features[
                f"lag{lag}_player_rank_{rank}"
            ] = int(
                historical[
                    "player_rank_counts"
                ].get(
                    rank,
                    0
                ) > 0
            )

            features[
                f"lag{lag}_dealer_rank_{rank}"
            ] = int(
                historical[
                    "dealer_rank_counts"
                ].get(
                    rank,
                    0
                ) > 0
            )

        # ---------------------------------------------------------
        # МАСТИ
        # ---------------------------------------------------------

        for suit in TARGET_SUITS:

            features[
                f"lag{lag}_suit_{suit}"
            ] = int(
                historical[
                    "suit_counts"
                ].get(
                    suit,
                    0
                ) > 0
            )

            features[
                f"lag{lag}_player_suit_{suit}"
            ] = int(
                historical[
                    "player_suit_counts"
                ].get(
                    suit,
                    0
                ) > 0
            )

            features[
                f"lag{lag}_dealer_suit_{suit}"
            ] = int(
                historical[
                    "dealer_suit_counts"
                ].get(
                    suit,
                    0
                ) > 0
            )

        # ---------------------------------------------------------
        # КОЛИЧЕСТВО КАРТ
        # ---------------------------------------------------------

        features[
            f"lag{lag}_total_cards"
        ] = historical[
            "total_cards"
        ]

        features[
            f"lag{lag}_player_count"
        ] = historical[
            "player_count"
        ]

        features[
            f"lag{lag}_dealer_count"
        ] = historical[
            "dealer_count"
        ]

    # =============================================================
    # WINDOWS
    # =============================================================

    for window in PATTERN_WINDOWS:

        sequence = history[
            -window:
        ]

        if not sequence:
            continue

        # ---------------------------------------------------------
        # ТОЧНЫЕ КАРТЫ
        # ---------------------------------------------------------

        for card in TARGET_CARDS:

            values = [
                int(
                    item[
                        "exact_counts"
                    ].get(
                        card,
                        0
                    ) > 0
                )
                for item in sequence
            ]

            player_values = [
                int(
                    item[
                        "player_exact_counts"
                    ].get(
                        card,
                        0
                    ) > 0
                )
                for item in sequence
            ]

            dealer_values = [
                int(
                    item[
                        "dealer_exact_counts"
                    ].get(
                        card,
                        0
                    ) > 0
                )
                for item in sequence
            ]

            features[
                f"win{window}_cnt_{card}"
            ] = sum(values)

            features[
                f"win{window}_rate_{card}"
            ] = (
                sum(values)
                / len(values)
            )

            features[
                f"win{window}_last_{card}"
            ] = values[-1]

            features[
                f"win{window}_streak_{card}"
            ] = tail_streak(
                values
            )

            features[
                f"win{window}_player_cnt_{card}"
            ] = sum(
                player_values
            )

            features[
                f"win{window}_dealer_cnt_{card}"
            ] = sum(
                dealer_values
            )

        # ---------------------------------------------------------
        # РАНГИ
        # ---------------------------------------------------------

        for rank in (
            "2",
            "3",
            "4",
            "5",
            "6",
            "7",
            "8",
            "9",
            "10",
            "J",
            "Q",
            "K",
            "A",
        ):

            values = [
                int(
                    item[
                        "rank_counts"
                    ].get(
                        rank,
                        0
                    ) > 0
                )
                for item in sequence
            ]

            features[
                f"win{window}_rank_cnt_{rank}"
            ] = sum(values)

            features[
                f"win{window}_rank_rate_{rank}"
            ] = (
                sum(values)
                / len(values)
            )

            features[
                f"win{window}_rank_last_{rank}"
            ] = values[-1]

        # ---------------------------------------------------------
        # МАСТИ
        # ---------------------------------------------------------

        for suit in TARGET_SUITS:

            values = [
                int(
                    item[
                        "suit_counts"
                    ].get(
                        suit,
                        0
                    ) > 0
                )
                for item in sequence
            ]

            features[
                f"win{window}_suit_cnt_{suit}"
            ] = sum(values)

            features[
                f"win{window}_suit_rate_{suit}"
            ] = (
                sum(values)
                / len(values)
            )

            features[
                f"win{window}_suit_last_{suit}"
            ] = values[-1]

            features[
                f"win{window}_suit_streak_{suit}"
            ] = tail_streak(
                values
            )

        # ---------------------------------------------------------
        # ОБЩИЕ ПАРАМЕТРЫ
        # ---------------------------------------------------------

        features[
            f"win{window}_avg_total_cards"
        ] = (
            sum(
                item["total_cards"]
                for item in sequence
            )
            / len(sequence)
        )

        features[
            f"win{window}_avg_player_cards"
        ] = (
            sum(
                item["player_count"]
                for item in sequence
            )
            / len(sequence)
        )

        features[
            f"win{window}_avg_dealer_cards"
        ] = (
            sum(
                item["dealer_count"]
                for item in sequence
            )
            / len(sequence)
        )

    # =============================================================
    # PREVIOUS GAME
    # =============================================================

    features[
        "prev_total_cards"
    ] = current[
        "total_cards"
    ]

    features[
        "prev_player_count"
    ] = current[
        "player_count"
    ]

    features[
        "prev_dealer_count"
    ] = current[
        "dealer_count"
    ]

    # =============================================================
    # PREVIOUS GAME EXACT CARDS
    # =============================================================

    previous_cards = set(
        current[
            "exact_counts"
        ].keys()
    )

    for card in TARGET_CARDS:

        features[
            f"prev_has_{card}"
        ] = int(
            card in previous_cards
        )

    # =============================================================
    # EXACT CARD TRANSITIONS
    # =============================================================

    if len(history) >= 2:

        previous_2 = history[-2]

        previous_2_cards = set(
            previous_2[
                "exact_counts"
            ].keys()
        )

        for card_a in TARGET_CARDS:

            if card_a not in previous_2_cards:
                continue

            for card_b in TARGET_CARDS:

                if card_b in previous_cards:

                    features[
                        f"transition_{card_a}_{card_b}"
                    ] = 1

    # =============================================================
    # HIGH CARD DENSITY
    # =============================================================

    high_count = 0

    for card in current[
        "all_cards"
    ]:

        if card.get(
            "rank"
        ) in TARGET_RANKS:

            high_count += 1

    features[
        "prev_high_card_count"
    ] = high_count

    if current["total_cards"] > 0:

        features[
            "prev_high_card_rate"
        ] = (
            high_count
            / current["total_cards"]
        )

    else:

        features[
            "prev_high_card_rate"
        ] = 0.0

    return features


# =====================================================================
# TARGET PRESENCE
# =====================================================================

def target_presence(
    record
):

    actual_cards = set(
        record_exact_cards(record)
    )

    return np.array(
        [
            1
            if card in actual_cards
            else 0
            for card in TARGET_CARDS
        ],
        dtype=int
    )


# =====================================================================
# TRAIN PATTERN SCANNER
# =====================================================================

def train_pattern_scanner(
    data
):

    global scanner_patterns
    global scanner_last_train_count

    if len(data) < MIN_TRAIN_SAMPLES:

        print(
            f"⏳ Pattern Scanner: "
            f"{len(data)}/{MIN_TRAIN_SAMPLES}",
            flush=True
        )

        return False

    feature_rows = []

    targets = []

    for i in range(
        1,
        len(data)
    ):

        features = build_scanner_feature_map(
            data,
            i
        )

        if not features:
            continue

        feature_rows.append(
            features
        )

        targets.append(
            target_presence(
                data[i]
            )
        )

    if len(feature_rows) < MIN_TRAIN_SAMPLES:

        print(
            f"⏳ Pattern Scanner: "
            f"обучающих строк "
            f"{len(feature_rows)}",
            flush=True
        )

        return False

    names = sorted(
        {
            key
            for row in feature_rows
            for key in row
        }
    )

    target_array = np.array(
        targets,
        dtype=int
    )

    baseline = np.mean(
        target_array,
        axis=0
    )

    discovered = []

    for name in names:

        values = np.array(
            [
                float(
                    row.get(
                        name,
                        0.0
                    )
                )
                for row in feature_rows
            ],
            dtype=float
        )

        if np.all(
            values == values[0]
        ):
            continue

        mask = values > 0

        support = int(
            mask.sum()
        )

        if support < PATTERN_MIN_SUPPORT:
            continue

        if (
            support
            < len(values) * 0.02
        ):
            continue

        for class_idx, card in enumerate(
            TARGET_CARDS
        ):

            base = float(
                baseline[class_idx]
            )

            if base <= 0:
                continue

            precision = float(
                np.mean(
                    target_array[
                        mask,
                        class_idx
                    ]
                )
            )

            lift = (
                precision
                / base
            )

            if (
                precision
                >= PATTERN_MIN_PRECISION
                and
                lift
                >= PATTERN_MIN_LIFT
            ):

                discovered.append(
                    {
                        "feature":
                            name,

                        "card":
                            card,

                        "support":
                            support,

                        "precision":
                            precision,

                        "lift":
                            lift,

                        "baseline":
                            base,
                    }
                )

    discovered.sort(
        key=lambda item: (
            (
                item["lift"]
                - 1.0
            )
            * item["precision"]
            * np.log1p(
                item["support"]
            )
        ),
        reverse=True
    )

    scanner_patterns = discovered[
        :PATTERN_MAX_FEATURES
    ]

    scanner_last_train_count = len(
        data
    )

    try:

        with open(
            SCANNER_FILE,
            "wb"
        ) as f:

            pickle.dump(
                scanner_patterns,
                f
            )

    except Exception as e:

        print(
            f"⚠️ Ошибка сохранения Pattern Scanner: {e}",
            flush=True
        )

    print(
        f"🔎 Pattern Scanner: "
        f"найдено {len(scanner_patterns)} "
        f"рабочих паттернов | "
        f"16 точных карт",
        flush=True
    )

    return True


# =====================================================================
# LOAD PATTERN SCANNER
# =====================================================================

def load_pattern_scanner():

    global scanner_patterns

    try:

        with open(
            SCANNER_FILE,
            "rb"
        ) as f:

            patterns = pickle.load(
                f
            )

        if isinstance(
            patterns,
            list
        ):

            valid = []

            for pattern in patterns:

                if not isinstance(
                    pattern,
                    dict
                ):
                    continue

                if pattern.get(
                    "card"
                ) not in TARGET_CARDS:
                    continue

                valid.append(
                    pattern
                )

            scanner_patterns = valid

            print(
                f"🔎 Pattern Scanner загружен: "
                f"{len(scanner_patterns)} паттернов",
                flush=True
            )

            return True

    except Exception:

        pass

    scanner_patterns = []

    return False


# =====================================================================
# SCANNER FEATURES FOR FUTURE GAME
# =====================================================================

def scanner_feature_vector(
    data,
    target_record
):

    temp = list(
        data
    )

    target_copy = dict(
        target_record
    )

    # =============================================================
    # ПРИНЦИПИАЛЬНО:
    #
    # Целевая игра НЕ должна попадать в признаки.
    # =============================================================

    target_copy[
        "player_cards"
    ] = []

    target_copy[
        "dealer_cards"
    ] = []

    target_copy[
        "all_cards"
    ] = []

    target_copy[
        "exact_cards"
    ] = []

    temp.append(
        target_copy
    )

    return build_scanner_feature_map(
        temp,
        len(temp) - 1
    )


# =====================================================================
# SCANNER PREDICTION
# =====================================================================

def scanner_predict(
    data,
    target_record
):

    if not scanner_patterns:

        return {
            card: 0.0
            for card in TARGET_CARDS
        }, 0

    features = scanner_feature_vector(
        data,
        target_record
    )

    scores = defaultdict(float)

    active_patterns = 0

    for pattern in scanner_patterns:

        feature_name = pattern.get(
            "feature"
        )

        card = pattern.get(
            "card"
        )

        if (
            not feature_name
            or card not in TARGET_CARDS
        ):
            continue

        value = float(
            features.get(
                feature_name,
                0.0
            )
        )

        if value <= 0:
            continue

        active_patterns += 1

        support = float(
            pattern.get(
                "support",
                0
            )
        )

        precision = float(
            pattern.get(
                "precision",
                0
            )
        )

        lift = float(
            pattern.get(
                "lift",
                0
            )
        )

        weight = (
            max(
                0.0,
                lift - 1.0
            )
            * precision
            * np.log1p(
                support
            )
        )

        scores[
            card
        ] += (
            value
            * weight
        )

    if active_patterns <= 0:

        return {
            card: 0.0
            for card in TARGET_CARDS
        }, 0

    total_score = sum(
        scores.values()
    )

    if total_score <= 0:

        return {
            card: 0.0
            for card in TARGET_CARDS
        }, active_patterns

    probabilities = {
        card: (
            scores.get(
                card,
                0.0
            )
            / total_score
        )
        for card in TARGET_CARDS
    }

    return (
        probabilities,
        active_patterns
    )


# =====================================================================
# MODEL PREDICTION
# =====================================================================

def get_model_prediction(
    timestamp_msk,
    target_record
):

    data = load_data()

    target_meta = dict(
        target_record
    )

    target_meta[
        "timestamp_msk"
    ] = timestamp_msk

    probabilities, active_patterns = (
        scanner_predict(
            data,
            target_meta
        )
    )

    if not probabilities:

        return {
            "predicted_card":
                None,

            "probability":
                0.0,

            "probabilities":
                {},

            "active_patterns":
                0,
        }

    predicted_card = max(
        probabilities,
        key=probabilities.get
    )

    probability = probabilities.get(
        predicted_card,
        0.0
    )

    return {
        "predicted_card":
            predicted_card,

        "probability":
            probability,

        "probabilities":
            probabilities,

        "active_patterns":
            active_patterns,
    }


# =====================================================================
# UPCOMING GAMES
# =====================================================================

def get_upcoming_games():

    try:

        url = (
            f"{BASE_URL}"
            "/service-api/main-live-feed/v3/"
            "leftMenuSports"
            "?fcountry=1"
            f"&gr={GR}"
            "&lng=ru"
            "&ref=7"
            f"&selectedMs=1.{SPORT_ID}.{LIGA_ID},"
            f"10.{SPORT_ID}.{LIGA_ID}"
        )

        response = requests.get(
            url,
            headers=HEADERS,
            timeout=10
        )

        if response.status_code != 200:

            print(
                f"⚠️ API HTTP {response.status_code}",
                flush=True
            )

            return []

        data = response.json()

        if not isinstance(
            data,
            list
        ):
            return []

        now = datetime.now(
            MOSCOW_TZ
        )

        games = []

        for section in data:

            if section.get(
                "menuSectionId"
            ) != 10:
                continue

            for sport in section.get(
                "sports",
                []
            ):

                try:

                    sport_id = int(
                        sport.get("id")
                    )

                except Exception:

                    continue

                if sport_id != SPORT_ID:
                    continue

                for liga in sport.get(
                    "ligas",
                    []
                ):

                    try:

                        liga_id = int(
                            liga.get("id")
                        )

                    except Exception:

                        continue

                    if liga_id != LIGA_ID:
                        continue

                    for game in liga.get(
                        "games",
                        []
                    ):

                        if game.get(
                            "nonStarted"
                        ) is not True:
                            continue

                        start_ts = game.get(
                            "startTs"
                        )

                        if not start_ts:
                            continue

                        try:

                            start_time = (
                                datetime.fromtimestamp(
                                    start_ts,
                                    MOSCOW_TZ
                                )
                            )

                        except Exception:

                            continue

                        minutes = (
                            start_time - now
                        ).total_seconds() / 60

                        if not (
                            0
                            < minutes
                            <= 20
                        ):
                            continue

                        game_number = (
                            get_game_number_from_timestamp(
                                start_ts
                            )
                        )

                        if game_number is None:
                            continue

                        games.append(
                            {
                                "game_id":
                                    str(
                                        game.get(
                                            "id"
                                        )
                                    ),

                                "game_num":
                                    game_number,

                                "start_ts":
                                    start_ts,

                                "start_time":
                                    start_time.isoformat(),

                                "minutes_until":
                                    minutes,
                            }
                        )

        games.sort(
            key=lambda x: x[
                "start_ts"
            ]
        )

        return games

    except Exception as e:

        print(
            f"❌ Ошибка будущих игр: {e}",
            flush=True
        )

        return []


# =====================================================================
# PREDICTION EXISTS
#
# ВАЖНО:
#
# Проверяем ВСЕ прогнозы:
#
# pending
# win
# lose
#
# Поэтому после получения ❌ бот не создаст новый прогноз
# на тот же #N.
# =====================================================================

def has_prediction_for_target(
    target
):

    try:

        target = int(
            target
        )

    except Exception:

        return False

    for prediction in predictions:

        try:

            prediction_target = int(
                prediction.get(
                    "target"
                )
            )

        except Exception:

            continue

        if prediction_target == target:

            return True

    return False


# =====================================================================
# DUPLICATE PREDICTION CLEANUP
#
# Если по какой-то причине старый файл уже содержит несколько
# прогнозов на один и тот же #N, оставляем только один.
# =====================================================================

def remove_duplicate_predictions():

    global predictions

    if not predictions:
        return

    result = []

    seen = set()

    # Сначала оставляем pending,
    # затем завершённые.
    ordered = sorted(
        predictions,
        key=lambda item: (
            0
            if item.get("status") == "pending"
            else 1
        )
    )

    removed = 0

    for entry in ordered:

        try:

            target = int(
                entry.get(
                    "target"
                )
            )

        except Exception:

            result.append(
                entry
            )

            continue

        if target in seen:

            removed += 1

            continue

        seen.add(
            target
        )

        result.append(
            entry
        )

    if removed > 0:

        predictions = result

        save_predictions()

        print(
            f"🧹 Удалено дублей прогнозов: "
            f"{removed}",
            flush=True
        )


# =====================================================================
# ANALYSIS
# =====================================================================

def build_analysis_text(
    target_num,
    result
):

    predicted = result.get(
        "predicted_card"
    )

    probability = result.get(
        "probability",
        0.0
    )

    active_patterns = result.get(
        "active_patterns",
        0
    )

    return (
        f"🔎 #{target_num} Pattern Scanner | "
        f"Карта={predicted} | "
        f"Вероятность={probability * 100:.1f}% | "
        f"Паттернов={active_patterns}"
    )


# =====================================================================
# CREATE PREDICTION
# =====================================================================

def check_upcoming_games():

    upcoming = get_upcoming_games()

    if not upcoming:
        return

    for game in upcoming:

        target_num = game.get(
            "game_num"
        )

        game_id = game.get(
            "game_id"
        )

        if (
            target_num is None
            or not game_id
        ):
            continue

        try:

            target_num = int(
                target_num
            )

        except Exception:

            continue

        # =============================================================
        # КРИТИЧЕСКАЯ ЗАЩИТА ОТ ДУБЛЯ
        #
        # Не важно pending / win / lose.
        # Если #N уже есть в predictions — второй прогноз НЕ создаём.
        # =============================================================

        if has_prediction_for_target(
            target_num
        ):

            continue

        now = datetime.now(
            MOSCOW_TZ
        )

        timestamp = now.strftime(
            "%H:%M:%S"
        )

        target_meta = {
            "game_id":
                game_id,

            "game_number":
                target_num,

            "timestamp_msk":
                timestamp,

            "start_ts":
                game.get(
                    "start_ts"
                ),
        }

        result = get_model_prediction(
            timestamp,
            target_meta
        )

        predicted_card = result.get(
            "predicted_card"
        )

        probability = result.get(
            "probability",
            0.0
        )

        active_patterns = result.get(
            "active_patterns",
            0
        )

        # =============================================================
        # Нет паттернов — пропуск.
        # =============================================================

        if (
            not predicted_card
            or active_patterns <= 0
        ):

            print(
                f"⏭️ #{target_num} ПРОПУСК | "
                f"нет активных Pattern",
                flush=True
            )

            continue

        # =============================================================
        # ПРОГНОЗ
        # =============================================================

        print(
            "\n"
            + "=" * 65
            + "\n"
            + "🔮 PATTERN SCANNER\n"
            + build_analysis_text(
                target_num,
                result
            )
            + "\n"
            + f"🎯 ПРОГНОЗ: #{target_num}: "
            f"{predicted_card}"
            + "\n"
            + "=" * 65,
            flush=True
        )

        msg = (
            f"🎯 Игра: "
            f"<b>#N{target_num}</b>: "
            f"<b>{predicted_card}</b>"
        )

        msg_id = send_message(
            CHANNEL_PROGNOZ,
            msg
        )

        if not msg_id:

            print(
                f"⚠️ Не удалось отправить "
                f"прогноз #{target_num}",
                flush=True
            )

            continue

        # =============================================================
        # Дополнительная защита:
        #
        # Перед сохранением ещё раз проверяем,
        # не успел ли другой цикл создать такой же прогноз.
        # =============================================================

        if has_prediction_for_target(
            target_num
        ):

            print(
                f"⚠️ #N{target_num} уже существует "
                f"в predictions — "
                f"новая запись не добавлена",
                flush=True
            )

            continue

        entry = {
            "target":
                int(target_num),

            "source":
                int(target_num),

            "game_id":
                str(game_id),

            "message_id":
                msg_id,

            "original_text":
                msg,

            "status":
                "pending",

            "predicted_card":
                predicted_card,

            "probability":
                probability,

            "probabilities":
                result.get(
                    "probabilities",
                    {}
                ),

            "active_patterns":
                active_patterns,

            "timestamp_msk":
                timestamp,

            "start_ts":
                game.get(
                    "start_ts"
                ),

            "created":
                now.isoformat(),

            "checked_games":
                [],

            # =====================================================
            # Какие игры реально пришли из CHANNEL_STATS
            # =====================================================

            "received_games":
                [],

            "result_game":
                None,

            "dogon":
                None,

            "found_card":
                None,
        }

        predictions.append(
            entry
        )

        save_predictions()

        print(
            f"🔥 ПРОГНОЗ СОХРАНЁН | "
            f"#{target_num} | "
            f"{predicted_card} | "
            f"{probability * 100:.1f}%",
            flush=True
        )


# =====================================================================
# RESULT CHECK
#
# КРИТИЧЕСКИ ВАЖНО:
#
# games_cache содержит ТОЛЬКО игры, пришедшие из CHANNEL_STATS.
#
# Если #N1140 ещё НЕ пришла:
#
# games_cache[1140] отсутствует.
#
# Тогда бот НЕ имеет права ставить ❌.
#
# Для проигрыша должны реально присутствовать:
#
# #N1140
# #N1141
# #N1142
# #N1143
#
# из CHANNEL_STATS.
# =====================================================================

def check_prediction_against_game(
    predicted_card,
    parsed_game
):

    if not predicted_card:
        return False

    if not parsed_game:
        return False

    actual_cards = set(
        parsed_game.get(
            "exact_cards",
            []
        )
    )

    return (
        predicted_card
        in actual_cards
    )


# =====================================================================
# GET DOGON RESULT
# =====================================================================

def get_dogon_result(
    entry
):

    target = entry.get(
        "target"
    )

    predicted_card = entry.get(
        "predicted_card"
    )

    if target is None:
        return None

    if not predicted_card:
        return None

    try:

        target = int(
            target
        )

    except Exception:

        return None

    received_games = []

    # =============================================================
    # Проверяем строго последовательно.
    # =============================================================

    for dogon in range(
        DOGON_GAMES
    ):

        game_number = add_game_offset(
            target,
            dogon
        )

        # ---------------------------------------------------------
        # КРИТИЧЕСКИ ВАЖНО:
        #
        # Если игры нет в games_cache,
        # значит бот ЕЩЁ НЕ ПОЛУЧИЛ её из CHANNEL_STATS.
        #
        # Нельзя считать её проигрышем.
        # ---------------------------------------------------------

        parsed = games_cache.get(
            game_number
        )

        if not parsed:

            print(
                f"⏳ #N{target} | "
                f"ждём реальную игру "
                f"#N{game_number} "
                f"из CHANNEL_STATS",
                flush=True
            )

            return None

        received_games.append(
            game_number
        )

        # ---------------------------------------------------------
        # Проверяем карту.
        # ---------------------------------------------------------

        if check_prediction_against_game(
            predicted_card,
            parsed
        ):

            return {
                "status":
                    "win",

                "game":
                    game_number,

                "dogon":
                    dogon,

                "parsed":
                    parsed,

                "received_games":
                    received_games.copy(),
            }

    # =============================================================
    # Сюда мы попадём ТОЛЬКО если:
    #
    # 1140 есть
    # 1141 есть
    # 1142 есть
    # 1143 есть
    #
    # и карта нигде не найдена.
    # =============================================================

    return {
        "status":
            "lose",

        "game":
            None,

        "dogon":
            None,

        "parsed":
            None,

        "received_games":
            received_games.copy(),
    }


# =====================================================================
# RESULT CHECKING
# =====================================================================

def check_results():

    if not predictions:
        return

    changed = False

    for entry in predictions:

        if entry.get(
            "status"
        ) != "pending":

            continue

        target = entry.get(
            "target"
        )

        msg_id = entry.get(
            "message_id"
        )

        predicted_card = entry.get(
            "predicted_card"
        )

        if (
            target is None
            or not msg_id
            or not predicted_card
        ):
            continue

        result = get_dogon_result(
            entry
        )

        # ---------------------------------------------------------
        # Недостаточно реальных игр.
        #
        # НИЧЕГО НЕ МЕНЯЕМ.
        # ---------------------------------------------------------

        if result is None:

            continue

        # =========================================================
        # WIN
        # =========================================================

        if result["status"] == "win":

            stats["total"] += 1

            stats["win"] += 1

            dogon = result.get(
                "dogon"
            )

            if dogon in stats[
                "by_dogon"
            ]:

                stats[
                    "by_dogon"
                ][dogon] += 1

            stats["card_hits"][
                predicted_card
            ] += 1

            result_game = result.get(
                "game"
            )

            received_games = result.get(
                "received_games",
                []
            )

            # -----------------------------------------------------
            # Редактируем своё сообщение.
            # -----------------------------------------------------

            result_text = (
                f"🎯 Игра: "
                f"<b>#N{target}</b>: "
                f"<b>{predicted_card}✅</b>"
            )

            edit_message(
                msg_id,
                result_text
            )

            entry.update(
                {
                    "status":
                        "win",

                    "result_game":
                        result_game,

                    "dogon":
                        dogon,

                    "found_card":
                        predicted_card,

                    "checked_games":
                        received_games,

                    "received_games":
                        received_games,
                }
            )

            parsed = result.get(
                "parsed"
            )

            if parsed:

                add_stats_game_to_data(
                    parsed
                )

            changed = True

            print(
                f"✅ ЗАШЛО | "
                f"#{target} | "
                f"{predicted_card} | "
                f"игра #{result_game} | "
                f"догон {dogon}",
                flush=True
            )

            continue

        # =========================================================
        # LOSE
        #
        # СЮДА МОЖНО ПОПАСТЬ ТОЛЬКО ПОСЛЕ ПОЛУЧЕНИЯ ВСЕХ 4 ИГР.
        # =========================================================

        if result["status"] == "lose":

            received_games = result.get(
                "received_games",
                []
            )

            # Дополнительная защита.
            if len(
                received_games
            ) < DOGON_GAMES:

                print(
                    f"⏳ #N{target} | "
                    f"нельзя ставить ❌ | "
                    f"получено "
                    f"{len(received_games)}/"
                    f"{DOGON_GAMES}",
                    flush=True
                )

                continue

            stats["total"] += 1

            stats["lose"] += 1

            result_text = (
                f"🎯 Игра: "
                f"<b>#N{target}</b>: "
                f"<b>{predicted_card}❌</b>"
            )

            edit_message(
                msg_id,
                result_text
            )

            entry.update(
                {
                    "status":
                        "lose",

                    "checked_games":
                        received_games,

                    "received_games":
                        received_games,

                    "found_card":
                        None,

                    "result_game":
                        None,

                    "dogon":
                        None,
                }
            )

            changed = True

            print(
                f"❌ НЕ ЗАШЛО | "
                f"#{target} | "
                f"{predicted_card} | "
                f"получены реальные игры: "
                f"{received_games}",
                flush=True
            )

    if changed:

        save_predictions()


# =====================================================================
# TELEGRAM UPDATES
#
# Бот читает ТОЛЬКО CHANNEL_STATS.
# =====================================================================

def process_updates(
    updates,
    offset
):

    if not updates:
        return offset

    for update in updates.get(
        "result",
        []
    ):

        update_id = update.get(
            "update_id"
        )

        if update_id is None:
            continue

        offset = update_id + 1

        save_offset(
            offset
        )

        # =============================================================
        # Принимаем channel_post и edited_channel_post.
        # =============================================================

        post = (
            update.get(
                "channel_post"
            )
            or
            update.get(
                "edited_channel_post"
            )
        )

        if not post:
            continue

        chat = post.get(
            "chat",
            {}
        )

        chat_id = str(
            chat.get(
                "id",
                ""
            )
        )

        # =============================================================
        # ТОЛЬКО КАНАЛ СТАТИСТИКИ.
        # =============================================================

        if chat_id != CHANNEL_STATS:

            continue

        text = post.get(
            "text",
            ""
        )

        if not text:
            continue

        parsed = parse_stats_message(
            text
        )

        if not parsed:
            continue

        num = parsed.get(
            "game_number"
        )

        print(
            f"📩 СТАТИСТИКА #{num} | "
            f"игрок={len(parsed['player_cards'])} карт | "
            f"дилер={len(parsed['dealer_cards'])} карт",
            flush=True
        )

        # =============================================================
        # Сначала cache.
        #
        # Именно эта запись потом используется
        # для проверки прогнозов.
        # =============================================================

        cache_result(
            parsed
        )

        # =============================================================
        # Потом сохраняем в историю.
        # =============================================================

        added = add_stats_game_to_data(
            parsed
        )

        if added:

            print(
                f"💾 #N{num} добавлена "
                f"в {DATA_FILE}",
                flush=True
            )

        # =============================================================
        # Сразу проверяем прогнозы.
        #
        # Это позволяет обработать результат сразу после
        # появления игры в CHANNEL_STATS.
        # =============================================================

        check_results()

    return offset


# =====================================================================
# RETRAINING
# =====================================================================

def maybe_retrain():

    global scanner_last_train_count

    data = load_data()

    count = len(data)

    if count < MIN_TRAIN_SAMPLES:
        return

    if (
        scanner_last_train_count
        == count
    ):
        return

    print(
        f"🔄 Переобучение Pattern Scanner | "
        f"игр={count}",
        flush=True
    )

    train_pattern_scanner(
        data
    )


# =====================================================================
# STATS
# =====================================================================

def get_accuracy():

    total = stats[
        "total"
    ]

    if total <= 0:
        return 0.0

    return (
        stats["win"]
        / total
        * 100
    )


def build_stats_text():

    accuracy = get_accuracy()

    return (
        "📊 <b>СТАТИСТИКА OLD_bot</b>\n\n"

        "🔎 Метод: "
        "<b>Pattern Scanner</b>\n\n"

        f"✅ Зашло: "
        f"<b>{stats['win']}</b>\n"

        f"❌ Не зашло: "
        f"<b>{stats['lose']}</b>\n"

        f"🎯 Всего: "
        f"<b>{stats['total']}</b>\n"

        f"📈 Точность: "
        f"<b>{accuracy:.1f}%</b>\n\n"

        "🎴 Цели: "
        "<b>J/Q/K/A + масть</b>\n\n"

        f"0️⃣ Догон 0: "
        f"<b>{stats['by_dogon'][0]}</b>\n"

        f"1️⃣ Догон 1: "
        f"<b>{stats['by_dogon'][1]}</b>\n"

        f"2️⃣ Догон 2: "
        f"<b>{stats['by_dogon'][2]}</b>\n"

        f"3️⃣ Догон 3: "
        f"<b>{stats['by_dogon'][3]}</b>"
    )


# =====================================================================
# REBUILD STATS
# =====================================================================

def rebuild_stats_from_history():

    global stats

    stats = {
        "total": 0,
        "win": 0,
        "lose": 0,

        "by_dogon": {
            0: 0,
            1: 0,
            2: 0,
            3: 0,
        },

        "card_hits":
            defaultdict(int),
    }

    for entry in predictions:

        status = entry.get(
            "status"
        )

        if status not in (
            "win",
            "lose"
        ):

            continue

        stats["total"] += 1

        if status == "win":

            stats["win"] += 1

            dogon = entry.get(
                "dogon"
            )

            if dogon in stats[
                "by_dogon"
            ]:

                stats[
                    "by_dogon"
                ][dogon] += 1

            card = entry.get(
                "predicted_card"
            )

            if card:

                stats[
                    "card_hits"
                ][card] += 1

        else:

            stats["lose"] += 1

    print(
        f"📊 Статистика восстановлена | "
        f"Всего={stats['total']} | "
        f"Зашло={stats['win']} | "
        f"Не зашло={stats['lose']} | "
        f"Точность={get_accuracy():.1f}%",
        flush=True
    )


# =====================================================================
# ANALYTICS
# =====================================================================

def maybe_send_analytics():

    global last_analytics_time

    now = time.time()

    if (
        last_analytics_time
        and
        now - last_analytics_time
        < ANALYTICS_INTERVAL
    ):

        return

    if stats["total"] <= 0:
        return

    text = build_stats_text()

    msg_id = send_message(
        CHANNEL_PROGNOZ,
        text
    )

    if msg_id:

        last_analytics_time = now

        print(
            "📊 Статистика отправлена",
            flush=True
        )


# =====================================================================
# ВАЖНО:
#
# Мы БОЛЬШЕ НЕ загружаем historical JSON в games_cache.
#
# Почему?
#
# twentyone_data_full.json содержит историю.
#
# Например:
#
# вчера #N1140
#
# сегодня снова #N1140
#
# номер совпадает, но это разные реальные игры.
#
# Поэтому JSON используется:
#
# 1. для обучения Pattern Scanner;
# 2. для хранения истории;
#
# но НЕ для подтверждения текущего результата прогноза.
#
# games_cache = только CHANNEL_STATS.
# =====================================================================

def load_recent_results_into_cache():

    # -------------------------------------------------------------
    # Специально ничего не загружаем.
    #
    # Очищаем cache при старте для полной гарантии.
    # -------------------------------------------------------------

    games_cache.clear()

    print(
        "📦 Исторические игры НЕ загружены "
        "в result-cache",
        flush=True
    )

    print(
        "📩 result-cache заполняется "
        "ТОЛЬКО из CHANNEL_STATS",
        flush=True
    )


# =====================================================================
# CLEAN OLD PREDICTIONS
# =====================================================================

def cleanup_predictions():

    global predictions

    if len(predictions) <= 5000:
        return

    pending = [
        p
        for p in predictions
        if p.get("status")
        == "pending"
    ]

    finished = [
        p
        for p in predictions
        if p.get("status")
        != "pending"
    ]

    finished = finished[
        -4000:
    ]

    predictions = (
        finished
        + pending
    )

    save_predictions()


# =====================================================================
# MAIN
# =====================================================================

def main():

    global predictions
    global last_analytics_time

    print(
        "=" * 70,
        flush=True
    )

    print(
        "🔮 21 CLASSIC — EXACT CARD PATTERN SCANNER",
        flush=True
    )

    print(
        "🎴 ЦЕЛИ: J/Q/K/A + МАСТЬ",
        flush=True
    )

    print(
        "🎯 ПРОГНОЗ: ОДНА ТОЧНАЯ КАРТА",
        flush=True
    )

    print(
        "📩 РЕЗУЛЬТАТ: КАНАЛ СТАТИСТИКИ",
        flush=True
    )

    print(
        f"📈 ДОГОН: 0–{DOGON_GAMES - 1}",
        flush=True
    )

    print(
        "=" * 70,
        flush=True
    )

    print(
        f"📌 CHANNEL_STATS: {CHANNEL_STATS}",
        flush=True
    )

    print(
        f"📌 CHANNEL_PROGNOZ: {CHANNEL_PROGNOZ}",
        flush=True
    )

    print(
        f"📌 BASE_URL: {BASE_URL}",
        flush=True
    )

    print(
        f"📌 LIGA_ID: {LIGA_ID}",
        flush=True
    )

    print(
        "=" * 70,
        flush=True
    )

    # =============================================================
    # DATA
    # =============================================================

    data = load_data()

    print(
        f"📚 Загружено исторических игр: "
        f"{len(data)}",
        flush=True
    )

    # =============================================================
    # CACHE
    #
    # НЕ загружаем старую историю в result-cache.
    # =============================================================

    load_recent_results_into_cache()

    # =============================================================
    # PATTERN
    # =============================================================

    load_pattern_scanner()

    if len(data) >= MIN_TRAIN_SAMPLES:

        train_pattern_scanner(
            data
        )

    else:

        print(
            f"⏳ Недостаточно данных Pattern Scanner: "
            f"{len(data)}/{MIN_TRAIN_SAMPLES}",
            flush=True
        )

    # =============================================================
    # PREDICTIONS
    # =============================================================

    predictions = load_predictions()

    if not isinstance(
        predictions,
        list
    ):

        predictions = []

    print(
        f"📂 Загружено прогнозов: "
        f"{len(predictions)}",
        flush=True
    )

    # =============================================================
    # Удаляем старые дубли.
    # =============================================================

    remove_duplicate_predictions()

    # =============================================================
    # RESTORE STATS
    # =============================================================

    rebuild_stats_from_history()

    # =============================================================
    # TELEGRAM OFFSET
    # =============================================================

    offset = get_offset()

    # =============================================================
    # TIMERS
    # =============================================================

    last_upcoming = 0

    last_result = 0

    last_retrain = 0

    last_cleanup = 0

    print(
        "=" * 70,
        flush=True
    )

    print(
        "🚀 БОТ ГОТОВ!",
        flush=True
    )

    print(
        "🔎 Pattern Scanner активен",
        flush=True
    )

    print(
        "🎴 Цели: "
        + ", ".join(TARGET_CARDS),
        flush=True
    )

    print(
        "📩 Проверка результатов "
        "ТОЛЬКО через CHANNEL_STATS",
        flush=True
    )

    print(
        "🚫 Старый JSON НЕ используется "
        "для текущей проверки результатов",
        flush=True
    )

    print(
        "📌 Формат прогноза: "
        "🎯 Игра: #NXXX: Q♦️",
        flush=True
    )

    print(
        "=" * 70,
        flush=True
    )

    # =============================================================
    # MAIN LOOP
    # =============================================================

    while True:

        try:

            now = time.time()

            # =====================================================
            # TELEGRAM UPDATES
            #
            # Сначала получаем новые игры из CHANNEL_STATS.
            # =====================================================

            updates = telegram_request(
                "getUpdates",
                {
                    "offset":
                        offset,

                    "timeout":
                        5,
                },
                timeout=10
            )

            if updates:

                offset = process_updates(
                    updates,
                    offset
                )

            # =====================================================
            # RESULT CHECK
            # =====================================================

            if (
                now - last_result
                >= 2
            ):

                check_results()

                last_result = now

            # =====================================================
            # UPCOMING
            # =====================================================

            if (
                now - last_upcoming
                >= 10
            ):

                check_upcoming_games()

                last_upcoming = now

            # =====================================================
            # RETRAIN
            # =====================================================

            if (
                now - last_retrain
                >= 60
            ):

                maybe_retrain()

                last_retrain = now

            # =====================================================
            # ANALYTICS
            # =====================================================

            maybe_send_analytics()

            # =====================================================
            # CLEANUP
            # =====================================================

            if (
                now - last_cleanup
                >= 3600
            ):

                cleanup_predictions()

                last_cleanup = now

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

            time.sleep(10)


# =====================================================================
# START
# =====================================================================

if __name__ == "__main__":
    main()