import os
import sys
import json
import time
import requests
import pytz
import re
import math

from datetime import datetime, timedelta
from collections import defaultdict, Counter

# =====================================================================
# ENV
# =====================================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    BOT_TOKEN = os.getenv("BOT_TOKEN_PROGNOZ")

CHANNEL_PROGNOZ = os.getenv("CHAT_ID_21")
if not CHANNEL_PROGNOZ:
    CHANNEL_PROGNOZ = os.getenv("CHANNEL_PROGNOZ")

CHANNEL_STATS = os.getenv("CHANNEL_STATS")

if not BOT_TOKEN or not CHANNEL_PROGNOZ:
    print("❌ Ошибка: BOT_TOKEN или CHANNEL_PROGNOZ не заданы!", flush=True)
    sys.exit(1)


# =====================================================================
# CONFIG
# =====================================================================

MOSCOW_TZ = pytz.timezone("Europe/Moscow")

BASE_URL = "https://1xlite-36553.pro"
LEAGUE_ID = 1643503

DATA_FILE = "twentyone_data_full.json"
PREDICTIONS_FILE = "twentyone_predictions.json"
OFFSET_FILE = "hybrid_offset.txt"

# ---------------------------------------------------------------------
# ИСТОРИЯ
# ---------------------------------------------------------------------

HISTORY_HOURS = 96

DOGON_GAMES = 4
POLL_INTERVAL = 2.0

TARGET_RANKS = {"J", "Q", "K", "A"}

TARGET_CARDS = [
    "J♠️", "J♣️", "J♦️", "J♥️",
    "Q♠️", "Q♣️", "Q♦️", "Q♥️",
    "K♠️", "K♣️", "K♦️", "K♥️",
    "A♠️", "A♣️", "A♦️", "A♥️"
]


# =====================================================================
# HYBRID WEIGHTS
# =====================================================================

WEIGHT_MS = 1.00
WEIGHT_ID1 = 0.80
WEIGHT_ID2 = 1.20
WEIGHT_SEQUENCE = 1.10
WEIGHT_FREQUENCY = 0.50

# Новый расширенный Scanner
WEIGHT_SCANNER = 1.60

MIN_MS_MATCHES = 2
MIN_ID1_MATCHES = 3
MIN_ID2_MATCHES = 2
MIN_SEQUENCE_MATCHES = 2

# ---------------------------------------------------------------------
# SCANNER CONFIG
# ---------------------------------------------------------------------

# Сколько предыдущих игр Scanner анализирует отдельно
SCANNER_LAGS = [1, 2, 3, 4, 5, 7, 10, 15, 20]

# Размеры окон для последовательностей
SCANNER_WINDOWS = [2, 3, 5, 8]

# Минимальная статистика паттерна
SCANNER_MIN_SUPPORT = 2

# Минимальная точность отдельного паттерна
SCANNER_MIN_PRECISION = 0.05

# Минимальный lift
SCANNER_MIN_LIFT = 0.70

# Максимальное количество сильных признаков
SCANNER_MAX_FEATURES = 5000

# Сколько признаков максимум использовать для одной цели
SCANNER_MAX_ACTIVE_FEATURES = 250

# Минимальный итоговый вес Scanner
SCANNER_MIN_MATCHES = 2


# =====================================================================
# FORECAST FILTER
# =====================================================================

MIN_FORECAST_PROBABILITY = 0.25
MIN_LEADER_GAP = 0.01
MIN_ACTIVE_METHODS = 1

PREDICTION_COOLDOWN_SECONDS = 2


# =====================================================================
# TELEGRAM
# =====================================================================

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": (
        f"{BASE_URL}/ru/live/twentyone/"
        "1643503-twentyone-game"
    ),
    "Cookie": (
        "platform_type=desktop; "
        "lng=ru; "
        "cookies_agree_type=3; "
        "tzo=3; "
        "is12h=0"
    )
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


# =====================================================================
# CARD MAPS
# =====================================================================

SUITS_NAMES = {
    0: "♠️",
    1: "♣️",
    2: "♦️",
    3: "♥️"
}

RANKS = {
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
# GLOBALS
# =====================================================================

history = []
predictions = []
games_cache = {}

tracked_games = {}

last_prediction_time = 0
processed_numbers = set()


# =====================================================================
# NORMALIZATION
# =====================================================================

def normalize_suit(v):
    if v is None or isinstance(v, bool):
        return None

    if isinstance(v, int):
        s = SUITS_NAMES.get(v)
        if s:
            return s.replace("\ufe0f", "")

    t = str(v).strip().replace("\ufe0f", "")

    mapping = {
        "0": "♠",
        "♠": "♠",
        "spade": "♠",
        "spades": "♠",
        "s": "♠",

        "1": "♣",
        "♣": "♣",
        "club": "♣",
        "clubs": "♣",
        "c": "♣",

        "2": "♦",
        "♦": "♦",
        "diamond": "♦",
        "diamonds": "♦",
        "d": "♦",

        "3": "♥",
        "♥": "♥",
        "heart": "♥",
        "hearts": "♥",
        "h": "♥"
    }

    return mapping.get(t)


def normalize_rank(v):
    if v is None or isinstance(v, bool):
        return None

    if isinstance(v, int):
        return RANKS.get(v)

    t = str(v).strip().upper().replace("А", "A")

    if t in {
        "2", "3", "4", "5",
        "6", "7", "8", "9",
        "10", "J", "Q", "K", "A"
    }:
        return t

    try:
        return RANKS.get(int(t))
    except Exception:
        return None


def card_to_text(card):
    if not card or not isinstance(card, dict):
        return ""

    rank = normalize_rank(card.get("rank"))
    suit = normalize_suit(card.get("suit"))

    if not rank or not suit:
        return ""

    return f"{rank}{suit}\ufe0f"


def card_dict_to_target(card):
    text = card_to_text(card)
    return text if text in TARGET_CARDS else None


# =====================================================================
# CARD VALUES / POINTS
# =====================================================================

def card_point_value(rank):
    """
    Значение карты для 21.
    A считаем как 11 с последующей корректировкой.
    """

    rank = normalize_rank(rank)

    if rank is None:
        return 0

    if rank in {"J", "Q", "K"}:
        return 10

    if rank == "A":
        return 11

    try:
        return int(rank)
    except Exception:
        return 0


def calculate_hand_points(cards):
    """
    Подсчёт очков руки.

    Тузы сначала считаются как 11.
    Если перебор > 21, каждый туз по очереди
    уменьшается с 11 до 1.
    """

    if not cards:
        return 0

    total = 0
    aces = 0

    for card in cards:
        rank = normalize_rank(card.get("rank"))

        if not rank:
            continue

        total += card_point_value(rank)

        if rank == "A":
            aces += 1

    while total > 21 and aces > 0:
        total -= 10
        aces -= 1

    return total


def determine_winner(player_points, dealer_points):
    """
    Определяем результат игры.
    """

    if player_points > 21 and dealer_points > 21:
        return "DRAW"

    if player_points > 21:
        return "DEALER"

    if dealer_points > 21:
        return "PLAYER"

    if player_points > dealer_points:
        return "PLAYER"

    if dealer_points > player_points:
        return "DEALER"

    return "DRAW"


# =====================================================================
# JSON
# =====================================================================

def load_json_file(filename, default):
    try:
        if not os.path.exists(filename):
            return default

        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)

    except Exception as e:
        print(
            f"⚠️ Ошибка чтения {filename}: {e}",
            flush=True
        )
        return default


def atomic_save_json(filename, data):
    tmp = filename + ".tmp"

    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(
                data,
                f,
                ensure_ascii=False,
                indent=2
            )

        os.replace(tmp, filename)
        return True

    except Exception as e:
        print(
            f"⚠️ Ошибка сохранения {filename}: {e}",
            flush=True
        )

        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass

        return False


# =====================================================================
# HISTORY
# =====================================================================

def parse_history_timestamp(game):
    value = (
        game.get("timestamp")
        or game.get("timestamp_iso")
    )

    if not value:
        return None

    try:
        dt = datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        )

        if dt.tzinfo is None:
            dt = MOSCOW_TZ.localize(dt)

        return dt.astimezone(MOSCOW_TZ)

    except Exception:
        return None


def cleanup_history_by_time(save=True):
    """
    Оставляет только игры последних HISTORY_HOURS часов.
    """

    global history

    now = datetime.now(MOSCOW_TZ)

    cutoff = now - timedelta(
        hours=HISTORY_HOURS
    )

    clean = []

    for game in history:
        if not isinstance(game, dict):
            continue

        if not game.get("game_id"):
            continue

        dt = parse_history_timestamp(game)

        if dt is not None and dt >= cutoff:
            clean.append(game)

    clean.sort(
        key=lambda g: (
            parse_history_timestamp(g)
            or now
        )
    )

    changed = len(clean) != len(history)

    history = clean

    if changed and save:
        atomic_save_json(DATA_FILE, history)

        print(
            f"♻️ История очищена: "
            f"{len(history)} игр за "
            f"{HISTORY_HOURS}ч",
            flush=True
        )

    return changed


def game_quality(game):
    """
    Определяет качество записи.

    Более полная версия одной игры
    может заменить старую.
    """

    if not isinstance(game, dict):
        return 0

    player_cards = len(
        game.get("player_cards", [])
    )

    dealer_cards = len(
        game.get("dealer_cards", [])
    )

    total_cards = player_cards + dealer_cards

    score = total_cards * 100

    state = str(
        game.get("state", "")
    )

    if state == "5":
        score += 10000

    elif state == "4":
        score += 5000

    if game.get("sequence"):
        score += 500

    if game.get("timestamp"):
        score += 100

    if game.get("player_points") is not None:
        score += 50

    if game.get("dealer_points") is not None:
        score += 50

    if game.get("winner"):
        score += 50

    return score


def load_history():
    global history

    data = load_json_file(
        DATA_FILE,
        []
    )

    if not isinstance(data, list):
        data = []

    unique = {}

    for game in data:
        if not isinstance(game, dict):
            continue

        gid = str(
            game.get("game_id", "")
        )

        if not gid:
            continue

        # Если в старом файле дубль —
        # оставляем наиболее полную запись.
        if gid not in unique:
            unique[gid] = game
        else:
            if (
                game_quality(game)
                > game_quality(unique[gid])
            ):
                unique[gid] = game

    history = list(unique.values())

    cleanup_history_by_time(
        save=False
    )

    # Сохраняем очищенную 96-часовую базу.
    atomic_save_json(
        DATA_FILE,
        history
    )

    return history


def load_predictions():
    data = load_json_file(
        PREDICTIONS_FILE,
        []
    )

    return data if isinstance(data, list) else []


def find_game_index(gid):
    gid = str(gid)

    for i, game in enumerate(history):
        if str(
            game.get("game_id")
        ) == gid:
            return i

    return -1


def game_exists(gid):
    return find_game_index(gid) != -1


# =====================================================================
# DEEP PARSING
# =====================================================================

def deep_find_value(obj, keys):
    wanted = {
        str(x).lower()
        for x in keys
    }

    if isinstance(obj, dict):

        for k, v in obj.items():
            if str(k).lower() in wanted:
                return v

        for v in obj.values():
            result = deep_find_value(
                v,
                keys
            )

            if result is not None:
                return result

    elif isinstance(obj, list):

        for item in obj:
            result = deep_find_value(
                item,
                keys
            )

            if result is not None:
                return result

    return None


def extract_card_from_dict(obj):
    if not isinstance(obj, dict):
        return None

    rank = None
    suit = None

    rank_keys = {
        "rank",
        "value",
        "v",
        "cardvalue",
        "card_value",
        "nominal",
        "denomination"
    }

    suit_keys = {
        "suit",
        "s",
        "card_suit",
        "cardsuit",
        "mast",
        "color"
    }

    for k, v in obj.items():

        kl = str(k).strip().lower()

        if kl in rank_keys:
            r = normalize_rank(v)

            if r:
                rank = r

        if kl in suit_keys:
            s = normalize_suit(v)

            if s:
                suit = s

    if rank and suit:
        return {
            "rank": rank,
            "suit": f"{suit}\ufe0f"
        }

    return None


def classify_key(key):
    key = str(key).strip().lower()

    if (
        key in [
            "p",
            "pcards",
            "playercards"
        ]
        or key.startswith("player")
        or "playercard" in key
    ):
        return "player"

    if (
        key in [
            "d",
            "dcards",
            "dealercards"
        ]
        or key.startswith("dealer")
        or "dealercard" in key
    ):
        return "dealer"

    return None


def find_cards_recursive(
    obj,
    context=None,
    player=None,
    dealer=None
):
    if player is None:
        player = []

    if dealer is None:
        dealer = []

    if isinstance(obj, dict):

        own = extract_card_from_dict(obj)

        if own:

            if context == "player":
                player.append(own)

            elif context == "dealer":
                dealer.append(own)

        for k, v in obj.items():

            ctx = context

            detected = classify_key(k)

            if detected:
                ctx = detected

            kl = str(k).strip().lower()

            if kl in {
                "p",
                "p1",
                "p2",
                "p3",
                "p4",
                "p5",
                "p6",
                "p7",
                "p8",
                "p9"
            }:
                ctx = "player"

            if kl in {
                "d",
                "d1",
                "d2",
                "d3",
                "d4",
                "d5",
                "d6",
                "d7",
                "d8",
                "d9"
            }:
                ctx = "dealer"

            find_cards_recursive(
                v,
                ctx,
                player,
                dealer
            )

    elif isinstance(obj, list):

        for item in obj:
            find_cards_recursive(
                item,
                context,
                player,
                dealer
            )

    return player, dealer


def clean_cards(cards):

    result = []
    seen = set()

    for card in cards:

        if not card:
            continue

        rank = normalize_rank(
            card.get("rank")
        )

        suit = normalize_suit(
            card.get("suit")
        )

        if not rank or not suit:
            continue

        key = f"{rank}{suit}"

        if key in seen:
            continue

        seen.add(key)

        result.append({
            "rank": rank,
            "suit": f"{suit}\ufe0f"
        })

    return result


# =====================================================================
# PARSE GAME
# =====================================================================

def get_cards_from_api_value(value):

    if not value or value == "[]":
        return []

    try:
        cards = (
            json.loads(value)
            if isinstance(value, str)
            else value
        )

    except Exception:
        return []

    if not isinstance(cards, list):
        return []

    result = []

    suit_map = {
        0: "♠️",
        1: "♣️",
        2: "♦️",
        3: "♥️"
    }

    rank_map = {
        1: "A",
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

    for card in cards:

        if not isinstance(card, dict):
            continue

        try:
            cs = int(card.get("CS"))
            cv = int(card.get("CV"))

        except (
            TypeError,
            ValueError
        ):
            continue

        suit = suit_map.get(cs)
        rank = rank_map.get(cv)

        if rank and suit:

            result.append({
                "rank": rank,
                "suit": suit
            })

    return result


def extract_game_sc(raw):

    if not isinstance(raw, dict):
        return [], [], None

    root = raw.get(
        "Value",
        raw
    )

    if not isinstance(root, dict):
        return [], [], None

    sc = root.get("SC", {})

    items = (
        sc.get("S", [])
        if isinstance(sc, dict)
        else []
    )

    if not isinstance(items, list):
        return [], [], None

    p1_value = None
    p2_value = None
    state = None

    for item in items:

        if not isinstance(item, dict):
            continue

        key = str(
            item.get("Key", "")
        ).upper()

        value = item.get("Value")

        if key == "P1":
            p1_value = value

        elif key == "P2":
            p2_value = value

        elif key == "STATE":
            state = (
                str(value)
                if value is not None
                else None
            )

    return (
        get_cards_from_api_value(p1_value),
        get_cards_from_api_value(p2_value),
        state
    )


# =====================================================================
# BUILD FULL GAME RECORD
# =====================================================================

def build_full_game_record(
    game_id,
    player,
    dealer,
    state,
    game_number=None
):
    """
    Центральная функция формирования записи игры.

    Здесь рассчитываются ВСЕ данные,
    которые потом видит Scanner.
    """

    now = datetime.now(MOSCOW_TZ)

    player = player or []
    dealer = dealer or []

    all_cards = []
    sequence = []

    pos = 1

    for i in range(
        max(
            len(player),
            len(dealer)
        )
    ):

        if i < len(player):

            card = player[i]

            all_cards.append(card)

            sequence.append({
                "position": pos,
                "who": "P",
                "rank": card["rank"],
                "suit": card["suit"]
            })

            pos += 1

        if i < len(dealer):

            card = dealer[i]

            all_cards.append(card)

            sequence.append({
                "position": pos,
                "who": "D",
                "rank": card["rank"],
                "suit": card["suit"]
            })

            pos += 1

    # -------------------------------------------------
    # POINTS
    # -------------------------------------------------

    player_points = calculate_hand_points(
        player
    )

    dealer_points = calculate_hand_points(
        dealer
    )

    points_diff = (
        player_points
        - dealer_points
    )

    points_abs_diff = abs(
        points_diff
    )

    points_sum = (
        player_points
        + dealer_points
    )

    winner = determine_winner(
        player_points,
        dealer_points
    )

    # -------------------------------------------------
    # RECORD
    # -------------------------------------------------

    record = {

        "game_id": str(game_id),

        "timestamp": now.isoformat(),

        "timestamp_msk": now.strftime(
            "%H:%M:%S.%f"
        )[:-3],

        "state": str(state),

        "game_number": game_number,

        # ---------------------------------------------
        # CARDS
        # ---------------------------------------------

        "player_cards": player,
        "dealer_cards": dealer,

        "player_suits": [
            c["suit"]
            for c in player
        ],

        "player_ranks": [
            c["rank"]
            for c in player
        ],

        "dealer_suits": [
            c["suit"]
            for c in dealer
        ],

        "dealer_ranks": [
            c["rank"]
            for c in dealer
        ],

        "all_suits": [
            c["suit"]
            for c in all_cards
        ],

        "all_ranks": [
            c["rank"]
            for c in all_cards
        ],

        "sequence": sequence,

        # ---------------------------------------------
        # CARD COUNTS
        # ---------------------------------------------

        "player_card_count": len(player),

        "dealer_card_count": len(dealer),

        "total_cards": len(all_cards),

        # ---------------------------------------------
        # POINTS
        # ---------------------------------------------

        "player_points": player_points,

        "dealer_points": dealer_points,

        "points_diff": points_diff,

        "points_abs_diff": points_abs_diff,

        "points_sum": points_sum,

        "winner": winner,

        # ---------------------------------------------
        # FIRST CARDS
        # ---------------------------------------------

        "first_player_card": (
            player[0]
            if player else None
        ),

        "second_player_card": (
            player[1]
            if len(player) > 1
            else None
        ),

        "third_player_card": (
            player[2]
            if len(player) > 2
            else None
        ),

        "first_dealer_card": (
            dealer[0]
            if dealer else None
        ),

        "second_dealer_card": (
            dealer[1]
            if len(dealer) > 1
            else None
        ),

        "third_dealer_card": (
            dealer[2]
            if len(dealer) > 2
            else None
        ),

        # ---------------------------------------------
        # ID
        # ---------------------------------------------

        "id_last_digit": str(
            game_id
        )[-1],

        "id_last_two": (
            str(game_id)[-2:]
            if len(str(game_id)) >= 2
            else ""
        )
    }

    return record


def parse_game_data(game_id, raw):

    if not raw:
        return None

    player, dealer, state = extract_game_sc(
        raw
    )

    if str(state) != "5":
        return None

    if not player and not dealer:
        return None

    return build_full_game_record(
        game_id=game_id,
        player=player,
        dealer=dealer,
        state=state
    )


# =====================================================================
# MERGE / SAVE GAME
# =====================================================================

def add_or_update_game(game):

    global history

    gid = str(
        game.get("game_id") or ""
    )

    if not gid:
        return False

    cleanup_history_by_time(
        save=False
    )

    idx = find_game_index(gid)

    # -------------------------------------------------
    # НОВАЯ ИГРА
    # -------------------------------------------------

    if idx == -1:

        history.append(game)

        history.sort(
            key=lambda g: (
                parse_history_timestamp(g)
                or datetime.now(MOSCOW_TZ)
            )
        )

        cleanup_history_by_time(
            save=False
        )

        atomic_save_json(
            DATA_FILE,
            history
        )

        print(
            f"💾 Новая завершённая игра | "
            f"ID={gid} | "
            f"P={len(game.get('player_cards', []))} | "
            f"D={len(game.get('dealer_cards', []))} | "
            f"Счёт={game.get('player_points')}:{game.get('dealer_points')} | "
            f"Победитель={game.get('winner')} | "
            f"База={len(history)} игр/{HISTORY_HOURS}ч",
            flush=True
        )

        return True

    # -------------------------------------------------
    # ОБНОВЛЕНИЕ СТАРОЙ ИГРЫ
    # -------------------------------------------------

    old_game = history[idx]

    old_quality = game_quality(
        old_game
    )

    new_quality = game_quality(
        game
    )

    if new_quality > old_quality:

        history[idx] = game

        atomic_save_json(
            DATA_FILE,
            history
        )

        print(
            f"🔄 Игра обновлена | "
            f"ID={gid} | "
            f"{old_quality} -> {new_quality}",
            flush=True
        )

        return True

    return False


# =====================================================================
# TARGET CARDS
# =====================================================================

def get_target_cards_from_record(record):

    result = []

    all_cards = (
        record.get("player_cards", [])
        + record.get("dealer_cards", [])
    )

    for card in all_cards:

        target = card_dict_to_target(
            card
        )

        if target:
            result.append(target)

    return result


# =====================================================================
# DISTRIBUTION HELPERS
# =====================================================================

def normalize_distribution(counter):

    if not counter:
        return {}

    total = sum(counter.values())

    if total <= 0:
        return {}

    return {
        card: count / total
        for card, count in counter.items()
    }


def get_top_from_distribution(dist):

    if not dist:
        return None, 0.0

    sorted_items = sorted(
        dist.items(),
        key=lambda x: x[1],
        reverse=True
    )

    return sorted_items[0]


# =====================================================================
# METHOD 1 — MILLISECONDS
# =====================================================================

def method_milliseconds(timestamp_msk):

    if not timestamp_msk:
        return {}

    try:

        if "." not in timestamp_msk:
            return {}

        target_ms = int(
            timestamp_msk.split(".")[1]
        )

    except Exception:
        return {}

    counter = Counter()
    matches = 0

    for record in history:

        record_time = record.get(
            "timestamp_msk",
            ""
        )

        if "." not in record_time:
            continue

        try:
            ms = int(
                record_time.split(".")[1]
            )

        except Exception:
            continue

        if ms != target_ms:
            continue

        cards = get_target_cards_from_record(
            record
        )

        if not cards:
            continue

        matches += 1

        for card in cards:
            counter[card] += 1

    if matches < MIN_MS_MATCHES:
        return {}

    return {
        "name": "MS",
        "weight": WEIGHT_MS,
        "matches": matches,
        "distribution": normalize_distribution(
            counter
        )
    }


# =====================================================================
# METHOD 2 — LAST ID DIGIT
# =====================================================================

def method_id1(game_id):

    game_id = str(game_id)

    if not game_id:
        return {}

    digit = game_id[-1]

    counter = Counter()
    matches = 0

    for record in history:

        rid = str(
            record.get("game_id", "")
        )

        if not rid:
            continue

        if rid == game_id:
            continue

        if rid[-1] != digit:
            continue

        cards = get_target_cards_from_record(
            record
        )

        if not cards:
            continue

        matches += 1

        for card in cards:
            counter[card] += 1

    if matches < MIN_ID1_MATCHES:
        return {}

    return {
        "name": "ID1",
        "weight": WEIGHT_ID1,
        "matches": matches,
        "distribution": normalize_distribution(
            counter
        )
    }


# =====================================================================
# METHOD 3 — LAST 2 ID DIGITS
# =====================================================================

def method_id2(game_id):

    game_id = str(game_id)

    if len(game_id) < 2:
        return {}

    suffix = game_id[-2:]

    counter = Counter()
    matches = 0

    for record in history:

        rid = str(
            record.get("game_id", "")
        )

        if len(rid) < 2:
            continue

        if rid == game_id:
            continue

        if rid[-2:] != suffix:
            continue

        cards = get_target_cards_from_record(
            record
        )

        if not cards:
            continue

        matches += 1

        for card in cards:
            counter[card] += 1

    if matches < MIN_ID2_MATCHES:
        return {}

    return {
        "name": "ID2",
        "weight": WEIGHT_ID2,
        "matches": matches,
        "distribution": normalize_distribution(
            counter
        )
    }


# =====================================================================
# METHOD 4 — LOCAL FREQUENCY
# =====================================================================

def method_frequency():

    counter = Counter()

    recent = history[-150:]

    matches = 0

    for record in recent:

        cards = get_target_cards_from_record(
            record
        )

        if not cards:
            continue

        matches += 1

        for card in cards:
            counter[card] += 1

    if not counter:
        return {}

    return {
        "name": "FREQ",
        "weight": WEIGHT_FREQUENCY,
        "matches": matches,
        "distribution": normalize_distribution(
            counter
        )
    }


# =====================================================================
# METHOD 5 — ORIGINAL SEQUENCE
# =====================================================================

def get_game_signature(game):

    sequence = game.get(
        "sequence",
        []
    )

    if not sequence:
        return ()

    signature = []

    for item in sequence[:4]:

        who = item.get("who", "")

        rank = normalize_rank(
            item.get("rank")
        )

        if who and rank:
            signature.append(
                f"{who}:{rank}"
            )

    return tuple(signature)


def method_sequence():

    if len(history) < 10:
        return {}

    recent = history[-5:]

    if not recent:
        return {}

    pattern_counter = Counter()

    for g in recent:

        sig = get_game_signature(g)

        if sig:
            pattern_counter[sig] += 1

    if not pattern_counter:
        return {}

    counter = Counter()
    matches = 0

    for record in history[:-5]:

        sig = get_game_signature(record)

        if not sig:
            continue

        if sig not in pattern_counter:
            continue

        cards = get_target_cards_from_record(
            record
        )

        if not cards:
            continue

        matches += 1

        for card in cards:
            counter[card] += 1

    if matches < MIN_SEQUENCE_MATCHES:
        return {}

    return {
        "name": "SEQ",
        "weight": WEIGHT_SEQUENCE,
        "matches": matches,
        "distribution": normalize_distribution(
            counter
        )
    }


# =====================================================================
# EXTENDED SCANNER
# =====================================================================

def add_feature(features, name, value):

    if value is None:
        return

    if isinstance(value, str):
        if not value:
            return

    features.add(
        f"{name}={value}"
    )


def get_card_feature_parts(prefix, card, features):

    if not card:
        return

    rank = normalize_rank(
        card.get("rank")
    )

    suit = normalize_suit(
        card.get("suit")
    )

    text = card_to_text(card)

    if text:
        add_feature(
            features,
            f"{prefix}_CARD",
            text
        )

    if rank:
        add_feature(
            features,
            f"{prefix}_RANK",
            rank
        )

    if suit:
        add_feature(
            features,
            f"{prefix}_SUIT",
            suit
        )


def build_scanner_feature_map(game):
    """
    Главная функция Scanner.

    Создаёт максимально широкий набор
    признаков одной игры.
    """

    features = set()

    if not isinstance(game, dict):
        return features

    player = game.get(
        "player_cards",
        []
    ) or []

    dealer = game.get(
        "dealer_cards",
        []
    ) or []

    # =================================================
    # BASIC RESULT
    # =================================================

    add_feature(
        features,
        "WINNER",
        game.get("winner")
    )

    # =================================================
    # POINTS
    # =================================================

    pp = game.get(
        "player_points"
    )

    dp = game.get(
        "dealer_points"
    )

    diff = game.get(
        "points_diff"
    )

    abs_diff = game.get(
        "points_abs_diff"
    )

    points_sum = game.get(
        "points_sum"
    )

    add_feature(
        features,
        "PLAYER_POINTS",
        pp
    )

    add_feature(
        features,
        "DEALER_POINTS",
        dp
    )

    # Точная пара счёта
    if pp is not None and dp is not None:

        add_feature(
            features,
            "EXACT_SCORE",
            f"{pp}:{dp}"
        )

        # Обратная категория без порядка
        lo = min(pp, dp)
        hi = max(pp, dp)

        add_feature(
            features,
            "SCORE_PAIR",
            f"{lo}:{hi}"
        )

    # Разница со знаком
    add_feature(
        features,
        "POINTS_DIFF",
        diff
    )

    # Абсолютная разница
    add_feature(
        features,
        "POINTS_ABS_DIFF",
        abs_diff
    )

    # Сумма
    add_feature(
        features,
        "POINTS_SUM",
        points_sum
    )

    # Диапазоны суммы
    if points_sum is not None:

        add_feature(
            features,
            "POINTS_SUM_BIN_5",
            (int(points_sum) // 5) * 5
        )

    # Диапазоны разницы
    if abs_diff is not None:

        add_feature(
            features,
            "POINTS_DIFF_BIN_2",
            (int(abs_diff) // 2) * 2
        )

    # =================================================
    # CARD COUNTS
    # =================================================

    player_count = len(player)
    dealer_count = len(dealer)

    total_count = (
        player_count
        + dealer_count
    )

    add_feature(
        features,
        "PLAYER_CARD_COUNT",
        player_count
    )

    add_feature(
        features,
        "DEALER_CARD_COUNT",
        dealer_count
    )

    add_feature(
        features,
        "TOTAL_CARD_COUNT",
        total_count
    )

    add_feature(
        features,
        "CARD_COUNT_PAIR",
        f"{player_count}:{dealer_count}"
    )

    add_feature(
        features,
        "CARD_COUNT_DIFF",
        player_count - dealer_count
    )

    # =================================================
    # PLAYER POSITIONS
    # =================================================

    for i, card in enumerate(player):

        position = i + 1

        get_card_feature_parts(
            f"P_POS_{position}",
            card,
            features
        )

        # Также отдельно общая позиция ранга
        rank = normalize_rank(
            card.get("rank")
        )

        suit = normalize_suit(
            card.get("suit")
        )

        if rank:
            add_feature(
                features,
                f"P_RANK_POS_{position}",
                rank
            )

        if suit:
            add_feature(
                features,
                f"P_SUIT_POS_{position}",
                suit
            )

    # =================================================
    # DEALER POSITIONS
    # =================================================

    for i, card in enumerate(dealer):

        position = i + 1

        get_card_feature_parts(
            f"D_POS_{position}",
            card,
            features
        )

        rank = normalize_rank(
            card.get("rank")
        )

        suit = normalize_suit(
            card.get("suit")
        )

        if rank:
            add_feature(
                features,
                f"D_RANK_POS_{position}",
                rank
            )

        if suit:
            add_feature(
                features,
                f"D_SUIT_POS_{position}",
                suit
            )

    # =================================================
    # PLAYER CARD CHAINS
    # =================================================

    player_text = [
        card_to_text(c)
        for c in player
        if card_to_text(c)
    ]

    player_ranks = [
        normalize_rank(c.get("rank"))
        for c in player
        if normalize_rank(c.get("rank"))
    ]

    player_suits = [
        normalize_suit(c.get("suit"))
        for c in player
        if normalize_suit(c.get("suit"))
    ]

    # Переходы
    for i in range(
        len(player_text) - 1
    ):

        add_feature(
            features,
            "P_CARD_TRANSITION",
            f"{player_text[i]}->{player_text[i + 1]}"
        )

    for i in range(
        len(player_ranks) - 1
    ):

        add_feature(
            features,
            "P_RANK_TRANSITION",
            f"{player_ranks[i]}->{player_ranks[i + 1]}"
        )

    for i in range(
        len(player_suits) - 1
    ):

        add_feature(
            features,
            "P_SUIT_TRANSITION",
            f"{player_suits[i]}->{player_suits[i + 1]}"
        )

    # Цепочки 2-4
    for size in [2, 3, 4]:

        if len(player_ranks) >= size:

            chain = "_".join(
                player_ranks[:size]
            )

            add_feature(
                features,
                f"P_RANK_CHAIN_{size}",
                chain
            )

        if len(player_suits) >= size:

            chain = "_".join(
                player_suits[:size]
            )

            add_feature(
                features,
                f"P_SUIT_CHAIN_{size}",
                chain
            )

    # =================================================
    # DEALER CARD CHAINS
    # =================================================

    dealer_text = [
        card_to_text(c)
        for c in dealer
        if card_to_text(c)
    ]

    dealer_ranks = [
        normalize_rank(c.get("rank"))
        for c in dealer
        if normalize_rank(c.get("rank"))
    ]

    dealer_suits = [
        normalize_suit(c.get("suit"))
        for c in dealer
        if normalize_suit(c.get("suit"))
    ]

    for i in range(
        len(dealer_text) - 1
    ):

        add_feature(
            features,
            "D_CARD_TRANSITION",
            f"{dealer_text[i]}->{dealer_text[i + 1]}"
        )

    for i in range(
        len(dealer_ranks) - 1
    ):

        add_feature(
            features,
            "D_RANK_TRANSITION",
            f"{dealer_ranks[i]}->{dealer_ranks[i + 1]}"
        )

    for i in range(
        len(dealer_suits) - 1
    ):

        add_feature(
            features,
            "D_SUIT_TRANSITION",
            f"{dealer_suits[i]}->{dealer_suits[i + 1]}"
        )

    for size in [2, 3, 4]:

        if len(dealer_ranks) >= size:

            chain = "_".join(
                dealer_ranks[:size]
            )

            add_feature(
                features,
                f"D_RANK_CHAIN_{size}",
                chain
            )

        if len(dealer_suits) >= size:

            chain = "_".join(
                dealer_suits[:size]
            )

            add_feature(
                features,
                f"D_SUIT_CHAIN_{size}",
                chain
            )

    # =================================================
    # INTERLEAVED SEQUENCE
    # =================================================

    sequence = game.get(
        "sequence",
        []
    ) or []

    sequence_cards = []
    sequence_ranks = []
    sequence_suits = []
    sequence_who = []

    for item in sequence:

        rank = normalize_rank(
            item.get("rank")
        )

        suit = normalize_suit(
            item.get("suit")
        )

        who = item.get("who")

        if rank and suit:

            sequence_cards.append(
                f"{rank}{suit}\ufe0f"
            )

            sequence_ranks.append(rank)

            sequence_suits.append(suit)

            sequence_who.append(who)

    # Полные первые цепочки
    for size in [2, 3, 4, 5, 6]:

        if len(sequence_ranks) >= size:

            add_feature(
                features,
                f"SEQ_RANK_CHAIN_{size}",
                "_".join(
                    sequence_ranks[:size]
                )
            )

        if len(sequence_suits) >= size:

            add_feature(
                features,
                f"SEQ_SUIT_CHAIN_{size}",
                "_".join(
                    sequence_suits[:size]
                )
            )

        if len(sequence_who) >= size:

            add_feature(
                features,
                f"SEQ_SIDE_CHAIN_{size}",
                "_".join(
                    sequence_who[:size]
                )
            )

    # Переходы полной раздачи
    for i in range(
        len(sequence_ranks) - 1
    ):

        add_feature(
            features,
            "SEQ_RANK_TRANSITION",
            f"{sequence_ranks[i]}->{sequence_ranks[i + 1]}"
        )

    for i in range(
        len(sequence_suits) - 1
    ):

        add_feature(
            features,
            "SEQ_SUIT_TRANSITION",
            f"{sequence_suits[i]}->{sequence_suits[i + 1]}"
        )

    # =================================================
    # CROSS FEATURES
    # =================================================

    if player and dealer:

        p_first = card_to_text(
            player[0]
        )

        d_first = card_to_text(
            dealer[0]
        )

        if p_first and d_first:

            add_feature(
                features,
                "FIRST_CARDS_PAIR",
                f"{p_first}|{d_first}"
            )

        p_rank = normalize_rank(
            player[0].get("rank")
        )

        d_rank = normalize_rank(
            dealer[0].get("rank")
        )

        if p_rank and d_rank:

            add_feature(
                features,
                "FIRST_RANK_PAIR",
                f"{p_rank}|{d_rank}"
            )

        p_suit = normalize_suit(
            player[0].get("suit")
        )

        d_suit = normalize_suit(
            dealer[0].get("suit")
        )

        if p_suit and d_suit:

            add_feature(
                features,
                "FIRST_SUIT_PAIR",
                f"{p_suit}|{d_suit}"
            )

    # =================================================
    # RESULT COMBINATIONS
    # =================================================

    if game.get("winner"):

        if pp is not None:

            add_feature(
                features,
                "WINNER_PLAYER_POINTS",
                f"{game['winner']}:{pp}"
            )

        if dp is not None:

            add_feature(
                features,
                "WINNER_DEALER_POINTS",
                f"{game['winner']}:{dp}"
            )

        if diff is not None:

            add_feature(
                features,
                "WINNER_DIFF",
                f"{game['winner']}:{diff}"
            )

        if points_sum is not None:

            add_feature(
                features,
                "WINNER_SUM",
                f"{game['winner']}:{points_sum}"
            )

    return features


# =====================================================================
# SCANNER CONTEXT
# =====================================================================

def build_context_features(history_list, index):
    """
    Создаёт признаки из предыдущих игр.

    Для каждой lag-игры Scanner получает
    весь набор её признаков.
    """

    features = set()

    if index <= 0:
        return features

    # -------------------------------------------------
    # LAGS
    # -------------------------------------------------

    for lag in SCANNER_LAGS:

        prev_index = index - lag

        if prev_index < 0:
            continue

        game = history_list[
            prev_index
        ]

        game_features = (
            build_scanner_feature_map(
                game
            )
        )

        for feature in game_features:

            features.add(
                f"LAG{lag}|{feature}"
            )

    # -------------------------------------------------
    # WINDOWS / CHAINS
    # -------------------------------------------------

    for window in SCANNER_WINDOWS:

        start = index - window

        if start < 0:
            continue

        games = history_list[
            start:index
        ]

        if len(games) != window:
            continue

        # Последовательность победителей
        winners = []

        # Последовательность очков
        player_points = []
        dealer_points = []

        # Разница
        diffs = []

        # Сумма
        sums = []

        # Первая карта игрока
        first_player_ranks = []

        # Первая карта дилера
        first_dealer_ranks = []

        # Количество карт
        player_counts = []
        dealer_counts = []

        for game in games:

            winners.append(
                str(
                    game.get(
                        "winner",
                        "NONE"
                    )
                )
            )

            player_points.append(
                str(
                    game.get(
                        "player_points",
                        "X"
                    )
                )
            )

            dealer_points.append(
                str(
                    game.get(
                        "dealer_points",
                        "X"
                    )
                )
            )

            diffs.append(
                str(
                    game.get(
                        "points_diff",
                        "X"
                    )
                )
            )

            sums.append(
                str(
                    game.get(
                        "points_sum",
                        "X"
                    )
                )
            )

            p_cards = (
                game.get(
                    "player_cards",
                    []
                )
                or []
            )

            d_cards = (
                game.get(
                    "dealer_cards",
                    []
                )
                or []
            )

            if p_cards:

                first_player_ranks.append(
                    normalize_rank(
                        p_cards[0].get(
                            "rank"
                        )
                    )
                    or "X"
                )

            else:
                first_player_ranks.append(
                    "X"
                )

            if d_cards:

                first_dealer_ranks.append(
                    normalize_rank(
                        d_cards[0].get(
                            "rank"
                        )
                    )
                    or "X"
                )

            else:
                first_dealer_ranks.append(
                    "X"
                )

            player_counts.append(
                str(len(p_cards))
            )

            dealer_counts.append(
                str(len(d_cards))
            )

        # Цепочки
        add_feature(
            features,
            f"WINDOW{window}_WINNERS",
            "_".join(winners)
        )

        add_feature(
            features,
            f"WINDOW{window}_PLAYER_POINTS",
            "_".join(player_points)
        )

        add_feature(
            features,
            f"WINDOW{window}_DEALER_POINTS",
            "_".join(dealer_points)
        )

        add_feature(
            features,
            f"WINDOW{window}_DIFFS",
            "_".join(diffs)
        )

        add_feature(
            features,
            f"WINDOW{window}_SUMS",
            "_".join(sums)
        )

        add_feature(
            features,
            f"WINDOW{window}_P_FIRST_RANKS",
            "_".join(first_player_ranks)
        )

        add_feature(
            features,
            f"WINDOW{window}_D_FIRST_RANKS",
            "_".join(first_dealer_ranks)
        )

        add_feature(
            features,
            f"WINDOW{window}_P_COUNTS",
            "_".join(player_counts)
        )

        add_feature(
            features,
            f"WINDOW{window}_D_COUNTS",
            "_".join(dealer_counts)
        )

    return features


# =====================================================================
# SCANNER TRAIN
# =====================================================================

def calculate_baseline_distribution(history_list):

    counter = Counter()

    for game in history_list:

        cards = get_target_cards_from_record(
            game
        )

        for card in cards:
            counter[card] += 1

    return normalize_distribution(
        counter
    )


def train_scanner_patterns():
    """
    Строит статистику:

    FEATURE
        ↓
    сколько раз встречался
        ↓
    какие карты после него встречались
        ↓
    precision
        ↓
    lift
    """

    if len(history) < 15:
        return {}, {}

    feature_stats = defaultdict(
        lambda: Counter()
    )

    feature_support = Counter()

    baseline = (
        calculate_baseline_distribution(
            history
        )
    )

    # ---------------------------------------------
    # Для каждой игры i
    # контекст предыдущих игр
    # -> цель = карты текущей игры
    # ---------------------------------------------

    for i in range(1, len(history)):

        context_features = (
            build_context_features(
                history,
                i
            )
        )

        if not context_features:
            continue

        target_cards = (
            get_target_cards_from_record(
                history[i]
            )
        )

        if not target_cards:
            continue

        # Уникальные карты внутри одной игры
        target_cards = list(
            set(target_cards)
        )

        for feature in context_features:

            feature_support[
                feature
            ] += 1

            for card in target_cards:

                feature_stats[
                    feature
                ][card] += 1

    # ---------------------------------------------
    # Отбрасываем полностью мусорные
    # ---------------------------------------------

    filtered = {}

    sorted_features = sorted(
        feature_support.items(),
        key=lambda x: x[1],
        reverse=True
    )

    for feature, support in sorted_features:

        if support < SCANNER_MIN_SUPPORT:
            continue

        if feature not in feature_stats:
            continue

        card_counter = feature_stats[
            feature
        ]

        card_info = {}

        for card, count in card_counter.items():

            precision = count / support

            base_probability = baseline.get(
                card,
                0.000001
            )

            lift = (
                precision
                / base_probability
            )

            # Храним даже относительно слабые,
            # но статистически заметные паттерны.
            if (
                precision >= SCANNER_MIN_PRECISION
                and lift >= SCANNER_MIN_LIFT
            ):

                card_info[card] = {
                    "count": count,
                    "support": support,
                    "precision": precision,
                    "lift": lift
                }

        if card_info:

            filtered[
                feature
            ] = {
                "support": support,
                "cards": card_info
            }

        if len(filtered) >= SCANNER_MAX_FEATURES:
            break

    return filtered, baseline


# =====================================================================
# SCANNER PREDICT
# =====================================================================

def scanner_predict():
    """
    Находит текущие активные признаки
    последних игр и ищет их статистику.
    """

    if len(history) < 15:
        return {}

    patterns, baseline = (
        train_scanner_patterns()
    )

    if not patterns:
        return {}

    # Текущий контекст = последние игры.
    current_features = (
        build_context_features(
            history,
            len(history)
        )
    )

    if not current_features:
        return {}

    scores = defaultdict(float)

    active_patterns = []

    # ---------------------------------------------
    # Какие признаки сейчас совпали
    # ---------------------------------------------

    for feature in current_features:

        pattern = patterns.get(
            feature
        )

        if not pattern:
            continue

        support = pattern.get(
            "support",
            0
        )

        if support < SCANNER_MIN_SUPPORT:
            continue

        active_patterns.append(
            feature
        )

        for card, info in pattern[
            "cards"
        ].items():

            precision = info.get(
                "precision",
                0
            )

            lift = info.get(
                "lift",
                0
            )

            count = info.get(
                "count",
                0
            )

            # -----------------------------------------
            # WEIGHT
            #
            # support + precision + lift
            #
            # lift не даёт случайной базовой частоте
            # полностью доминировать.
            # -----------------------------------------

            support_weight = math.log1p(
                support
            )

            lift_weight = min(
                lift,
                5.0
            )

            score = (
                precision
                * support_weight
                * lift_weight
                * (1 + math.log1p(count))
            )

            scores[card] += score

    if not scores:
        return {}

    total_score = sum(
        scores.values()
    )

    if total_score <= 0:
        return {}

    distribution = {
        card: score / total_score
        for card, score in scores.items()
    }

    active_count = len(
        active_patterns
    )

    return {
        "name": "SCANNER",
        "weight": WEIGHT_SCANNER,
        "matches": active_count,
        "distribution": distribution,
        "active_features": active_patterns[
            :SCANNER_MAX_ACTIVE_FEATURES
        ],
        "patterns_total": len(patterns)
    }


# =====================================================================
# HYBRID ENGINE
# =====================================================================

def build_hybrid_prediction(
    game_id,
    timestamp_msk
):

    # Scanner обучается на текущей истории
    # и смотрит активный контекст.
    scanner_result = scanner_predict()

    results = [

        method_milliseconds(
            timestamp_msk
        ),

        method_id1(
            game_id
        ),

        method_id2(
            game_id
        ),

        method_frequency(),

        method_sequence(),

        scanner_result
    ]

    active = []

    scores = defaultdict(float)

    method_details = {}

    for result in results:

        if not result:
            continue

        dist = result.get(
            "distribution",
            {}
        )

        if not dist:
            continue

        name = result["name"]

        weight = result[
            "weight"
        ]

        top_card, top_prob = (
            get_top_from_distribution(
                dist
            )
        )

        method_details[name] = {

            "top": top_card,

            "probability": top_prob,

            "matches": result.get(
                "matches",
                0
            )
        }

        active.append(name)

        for card, probability in dist.items():

            scores[card] += (
                probability
                * weight
            )

    if len(active) < MIN_ACTIVE_METHODS:

        print(
            f"⏭️ Недостаточно методов: "
            f"{active}",
            flush=True
        )

        return None

    if not scores:
        return None

    total_score = sum(
        scores.values()
    )

    probabilities = {

        card: score / total_score

        for card, score
        in scores.items()
    }

    ranking = sorted(
        probabilities.items(),
        key=lambda x: x[1],
        reverse=True
    )

    if not ranking:
        return None

    best_card, best_probability = (
        ranking[0]
    )

    second_card = None
    second_probability = 0.0

    if len(ranking) > 1:

        second_card = ranking[1][0]

        second_probability = ranking[1][1]

    gap = (
        best_probability
        - second_probability
    )

    supporters = []

    for name, info in method_details.items():

        if info.get("top") == best_card:
            supporters.append(name)

    return {

        "card": best_card,

        "probability": best_probability,

        "second_card": second_card,

        "second_probability": (
            second_probability
        ),

        "gap": gap,

        "active_methods": active,

        "supporters": supporters,

        "method_details": method_details,

        "ranking": ranking[:5]
    }


# =====================================================================
# FILTER
# =====================================================================

def prediction_passes_filter(result):

    if not result:
        return False

    probability = result.get(
        "probability",
        0
    )

    gap = result.get(
        "gap",
        0
    )

    supporters = result.get(
        "supporters",
        []
    )

    if probability < MIN_FORECAST_PROBABILITY:

        print(
            f"🚫 Лидер слабый: "
            f"{probability:.1%}",
            flush=True
        )

        return False

    if gap < MIN_LEADER_GAP:

        print(
            f"🚫 Нет преимущества: "
            f"gap={gap:.1%}",
            flush=True
        )

        return False

    if len(supporters) < MIN_ACTIVE_METHODS:

        print(
            f"🚫 Мало поддержки: "
            f"{supporters}",
            flush=True
        )

        return False

    return True


# =====================================================================
# GAME NUMBER
# =====================================================================

def get_game_number():

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
        start -= timedelta(days=1)

    return (
        int(
            (
                now - start
            ).total_seconds()
            // 60
        )
        % 1440
    ) + 1


def add_game_offset(
    number,
    offset
):
    return (
        (
            int(number)
            - 1
            + int(offset)
        )
        % 1440
    ) + 1


# =====================================================================
# TELEGRAM
# =====================================================================

def telegram_send(
    text,
    chat_id=None
):

    if not chat_id:
        chat_id = CHANNEL_PROGNOZ

    try:

        response = SESSION.post(

            f"{TELEGRAM_API}/sendMessage",

            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            },

            timeout=10
        )

        data = response.json()

        if data.get("ok"):
            return data[
                "result"
            ]["message_id"]

        print(
            f"❌ Telegram: {data}",
            flush=True
        )

    except Exception as e:

        print(
            f"❌ Telegram ошибка: {e}",
            flush=True
        )

    return None


def telegram_edit(
    message_id,
    text,
    chat_id=None
):

    if not message_id:
        return False

    if not chat_id:
        chat_id = CHANNEL_PROGNOZ

    try:

        response = SESSION.post(

            f"{TELEGRAM_API}/editMessageText",

            json={
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "parse_mode": "HTML"
            },

            timeout=10
        )

        return bool(
            response.json().get(
                "ok"
            )
        )

    except Exception:
        return False


# =====================================================================
# MESSAGES
# =====================================================================

def make_prediction_message(entry):

    result = entry["hybrid"]

    card1 = result["card"]

    prob1 = result[
        "probability"
    ]

    card2 = (
        result.get("second_card")
        or "—"
    )

    prob2 = result.get(
        "second_probability",
        0.0
    )

    text = (
        f"🎯 Игра: #N{entry['target_number']}\n"
        f"🃏 {card1} — {prob1*100:.1f}%\n"
        f"🥈 {card2} — {prob2*100:.1f}%"
    )

    return text


# =====================================================================
# PARSE CARDS FROM MESSAGE
# =====================================================================

def parse_cards_from_message(text):

    if not text:
        return None

    if not re.search(
        r'[✅🔰]',
        text
    ):
        return None

    match = re.search(
        r"#N(\d+)",
        text
    )

    if not match:
        return None

    game_number = int(
        match.group(1)
    )

    found = re.findall(
        r"(10|[2-9AJQK])([♠♣♦♥])\ufe0f?",
        text
    )

    if not found:

        found = re.findall(
            r"(10|[2-9AJQK])([♠♣♦♥])",
            text
        )

    cards = [

        f"{rank}{suit}\ufe0f"

        for rank, suit
        in found
    ]

    if not cards:

        matches = re.findall(
            r"\((.*?)\)",
            text
        )

        for match in matches:

            found_inner = re.findall(
                r"(10|[2-9AJQK])([♠♣♦♥])\ufe0f?",
                match
            )

            for rank, suit in found_inner:

                cards.append(
                    f"{rank}{suit}\ufe0f"
                )

    cards = list(
        dict.fromkeys(cards)
    )

    return {
        "game_number": game_number,
        "cards": cards,
    }


# =====================================================================
# OFFSET
# =====================================================================

def get_offset():

    try:

        if os.path.exists(
            OFFSET_FILE
        ):

            with open(
                OFFSET_FILE,
                "r"
            ) as f:

                return int(
                    f.read().strip()
                )

    except Exception:
        pass

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
# PROCESS TELEGRAM UPDATES
# =====================================================================

def process_telegram_updates(offset):

    global predictions
    global games_cache

    if not CHANNEL_STATS:
        return offset

    try:

        response = SESSION.get(

            f"{TELEGRAM_API}/getUpdates",

            params={
                "offset": offset,
                "timeout": 3,
                "limit": 50
            },

            timeout=10
        )

        data = response.json()

        if not data.get("ok"):
            return offset

        for update in data.get(
            "result",
            []
        ):

            update_id = update.get(
                "update_id"
            )

            if update_id is not None:

                offset = (
                    update_id + 1
                )

                save_offset(offset)

            post = (
                update.get(
                    "channel_post"
                )
                or update.get(
                    "edited_channel_post"
                )
            )

            if not post:
                continue

            chat_id = str(
                post.get(
                    "chat",
                    {}
                ).get(
                    "id",
                    ""
                )
            )

            if chat_id != str(
                CHANNEL_STATS
            ):
                continue

            text = post.get(
                "text",
                ""
            )

            parsed = (
                parse_cards_from_message(
                    text
                )
            )

            if parsed:

                games_cache[
                    parsed[
                        "game_number"
                    ]
                ] = text

                print(
                    f"💾 КЭШ: "
                    f"#{parsed['game_number']} "
                    f"-> {parsed['cards']}"
                )

            if (
                "⏳ Ожидание игры"
                in text
            ):

                id_match = re.search(
                    r"ID:\s*(\d+)",
                    text
                )

                num_match = re.search(
                    r"#N(\d+)",
                    text
                )

                if id_match and num_match:

                    game_id = (
                        id_match.group(1)
                    )

                    game_number = int(
                        num_match.group(1)
                    )

                    has_prediction = False

                    for entry in predictions:

                        if (
                            entry.get(
                                "target_number"
                            )
                            == game_number
                            and entry.get(
                                "status"
                            )
                            == "pending"
                        ):

                            has_prediction = True
                            break

                    if not has_prediction:

                        print(
                            f"\n🆕 НОВАЯ ИГРА: "
                            f"#{game_number} | "
                            f"ID={game_id}"
                        )

                        prediction = (
                            create_hybrid_prediction(
                                game_id,
                                game_number
                            )
                        )

                        if prediction:

                            message = (
                                make_prediction_message(
                                    prediction
                                )
                            )

                            prediction[
                                "original_text"
                            ] = message

                            message_id = (
                                telegram_send(
                                    message
                                )
                            )

                            if message_id:

                                prediction[
                                    "message_id"
                                ] = message_id

                                atomic_save_json(
                                    PREDICTIONS_FILE,
                                    predictions
                                )

                                print(
                                    f"📤 ОТПРАВЛЕНО: "
                                    f"{prediction['predicted_card']} "
                                    f"на #{game_number}"
                                )

    except Exception as e:

        print(
            f"⚠️ Updates error: {e}"
        )

    return offset


# =====================================================================
# CHECK PREDICTIONS
# =====================================================================

def check_predictions():

    global predictions

    if not predictions:
        return

    if not CHANNEL_STATS:
        return

    changed = False

    for entry in predictions:

        if entry.get(
            "status"
        ) != "pending":
            continue

        target = entry.get(
            "target_number"
        )

        predicted_cards = entry.get(
            "predicted_cards",
            []
        )

        msg_id = entry.get(
            "message_id"
        )

        original_text = entry.get(
            "original_text",
            ""
        )

        if (
            not target
            or not predicted_cards
            or not msg_id
        ):
            continue

        found = None
        all_available = True

        for dogon in range(
            DOGON_GAMES + 1
        ):

            num = add_game_offset(
                target,
                dogon
            )

            text = games_cache.get(
                num
            )

            if not text:

                all_available = False
                continue

            parsed = (
                parse_cards_from_message(
                    text
                )
            )

            if not parsed:
                continue

            actual_cards = parsed.get(
                "cards",
                []
            )

            for card in predicted_cards:

                if (
                    card
                    and card in actual_cards
                ):

                    found = {
                        "num": num,
                        "dogon": dogon,
                        "card": card
                    }

                    break

            if found:
                break

        if found:

            entry["status"] = "win"

            entry["result_game"] = (
                found["num"]
            )

            entry["found_card"] = (
                found["card"]
            )

            entry["current_dogon"] = (
                found["dogon"]
            )

            changed = True

            print(
                f"✅ ЗАШЛО на "
                f"#{found['num']} | "
                f"догон {found['dogon']} | "
                f"{found['card']}"
            )

            if msg_id and original_text:

                lines = (
                    original_text.split(
                        '\n'
                    )
                )

                lines[0] = (
                    f"🎯 Игра: "
                    f"#N{target} ✅"
                )

                new_text = '\n'.join(
                    lines
                )

                telegram_edit(
                    msg_id,
                    new_text
                )

            atomic_save_json(
                PREDICTIONS_FILE,
                predictions
            )

            continue

        if not all_available:
            continue

        entry["status"] = "lose"

        changed = True

        print(
            f"❌ НЕ ЗАШЛО: "
            f"догоны 0-{DOGON_GAMES} "
            f"для #{target}"
        )

        if msg_id and original_text:

            lines = (
                original_text.split(
                    '\n'
                )
            )

            lines[0] = (
                f"🎯 Игра: "
                f"#N{target} ❌"
            )

            new_text = '\n'.join(
                lines
            )

            telegram_edit(
                msg_id,
                new_text
            )

        atomic_save_json(
            PREDICTIONS_FILE,
            predictions
        )

    if changed:
        print(
            "💾 Прогнозы обновлены"
        )


# =====================================================================
# CREATE PREDICTION
# =====================================================================

def create_hybrid_prediction(
    game_id,
    game_number
):

    global last_prediction_time

    game_id = str(game_id)

    for entry in predictions:

        if (
            entry.get(
                "target_number"
            )
            == game_number
            and entry.get(
                "status"
            )
            == "pending"
        ):

            print(
                f"⏭️ Прогноз на "
                f"#{game_number} уже существует"
            )

            return None

    now_ts = time.time()

    if (
        now_ts
        - last_prediction_time
        < PREDICTION_COOLDOWN_SECONDS
    ):

        print(
            f"⏭️ Cooldown "
            f"{PREDICTION_COOLDOWN_SECONDS} сек"
        )

        return None

    now = datetime.now(
        MOSCOW_TZ
    )

    timestamp_msk = (
        now.strftime(
            "%H:%M:%S.%f"
        )[:-3]
    )

    print(
        "\n══════════════════════════════════"
    )

    print(
        f"🧠 HYBRID + EXTENDED SCANNER | "
        f"ID={game_id} | "
        f"#N{game_number}"
    )

    print(
        f"📚 История Scanner: "
        f"{len(history)} игр / "
        f"{HISTORY_HOURS}ч"
    )

    result = (
        build_hybrid_prediction(
            game_id,
            timestamp_msk
        )
    )

    if not result:

        print(
            "⏭️ Гибрид не дал результата"
        )

        return None

    print(
        f"🥇 {result['card']} "
        f"{result['probability']:.1%}"
    )

    print(
        f"🥈 {result['second_card']} "
        f"{result['second_probability']:.1%}"
    )

    print(
        f"📏 Gap: "
        f"{result['gap']:.1%}"
    )

    print(
        f"🤝 Поддержка: "
        f"{result['supporters']}"
    )

    if "SCANNER" in result[
        "method_details"
    ]:

        scanner_info = result[
            "method_details"
        ]["SCANNER"]

        print(
            f"🧠 Scanner активных "
            f"паттернов: "
            f"{scanner_info.get('matches', 0)}"
        )

    if not prediction_passes_filter(
        result
    ):

        print(
            "🚫 ПРОГНОЗ ОТМЕНЁН ФИЛЬТРОМ"
        )

        return None

    entry = {

        "target_game_id": game_id,

        "target_number": game_number,

        "timestamp_msk": timestamp_msk,

        "hybrid": result,

        "predicted_card": result["card"],

        "predicted_cards": [
            result["card"],
            result.get(
                "second_card"
            )
        ],

        "status": "pending",

        "current_dogon": 0,

        "created_at": datetime.now(
            MOSCOW_TZ
        ).isoformat(),

        "message_id": None,

        "original_text": "",

        "result_game": None,

        "found_card": None
    }

    predictions.append(
        entry
    )

    atomic_save_json(
        PREDICTIONS_FILE,
        predictions
    )

    last_prediction_time = now_ts

    print(
        f"🔮 ПРОГНОЗ СОЗДАН: "
        f"{result['card']} "
        f"для #{game_number}"
    )

    return entry


# =====================================================================
# API
# =====================================================================

def get_game_data(game_id):

    url = (
        f"{BASE_URL}/service-api/"
        "LiveFeed/GetGameZip"
        f"?id={game_id}"
        "&isSubGames=true"
        "&GroupEvents=true"
        "&countevents=250"
        "&grMode=4"
        "&partner=7"
        "&topGroups="
        "&country=190"
        "&marketType=1"
        "&isNewBuilder=true"
    )

    try:

        response = SESSION.get(
            url,
            timeout=7
        )

        if response.status_code == 200:
            return response.json()

    except Exception:
        pass

    return None


# =====================================================================
# API ACTIVE GAMES
# =====================================================================

def get_active_games():

    url = (
        f"{BASE_URL}/service-api/"
        "main-live-feed/v3/games1x2"
        "?cfView=3"
        "&count=40"
        "&fcountry=190"
        "&gr=415"
        "&grMode=4"
        "&lng=ru"
        "&ref=7"
        "&selectedMs=10.146.1643503"
    )

    try:

        response = SESSION.get(
            url,
            timeout=10
        )

        if response.status_code != 200:
            return []

        data = response.json()

        if isinstance(data, list):

            games = data

        elif isinstance(data, dict):

            games = data.get(
                "Value",
                []
            )

        else:
            games = []

        result = []

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

            if str(
                liga.get(
                    "id",
                    ""
                )
            ) != str(LEAGUE_ID):
                continue

            if not game.get("id"):
                continue

            result.append(game)

        return result

    except Exception as e:

        print(
            f"❌ API games: {e}",
            flush=True
        )

        return []


# =====================================================================
# PROCESS GAME
# =====================================================================

def inspect_game_state(
    gid,
    active_game=None,
    final_attempt=False
):

    raw = get_game_data(gid)

    if not raw:
        return None

    player, dealer, state = (
        extract_game_sc(raw)
    )

    if state is None:
        return None

    info = tracked_games.setdefault(

        str(gid),

        {
            "game_id": str(gid),

            "first_seen": datetime.now(
                MOSCOW_TZ
            ).isoformat(),

            "last_state": None,

            "player": [],

            "dealer": [],

            "game_number": None,

            "final_attempts": 0,
        }
    )

    if active_game:

        game_number = active_game.get(
            "gameNumber",
            active_game.get("number")
        )

        if game_number is not None:
            info[
                "game_number"
            ] = game_number

    info["last_state"] = str(state)

    info["player"] = player

    info["dealer"] = dealer

    info["last_seen"] = datetime.now(
        MOSCOW_TZ
    ).isoformat()

    print(

        f"🎮 ID={gid} | "
        f"STATE={state} | "
        f"P1={len(player)} | "
        f"P2={len(dealer)}"

        + (
            " | финальная проверка"
            if final_attempt
            else ""
        ),

        flush=True
    )

    return raw, state


def build_game_from_cached_cards(
    gid,
    info
):

    player = (
        info.get("player", [])
        or []
    )

    dealer = (
        info.get("dealer", [])
        or []
    )

    if not player and not dealer:
        return None

    game_number = info.get(
        "game_number"
    )

    try:

        game_number = (
            int(game_number)
            if game_number is not None
            else get_game_number()
        )

    except (
        TypeError,
        ValueError
    ):

        game_number = get_game_number()

    return build_full_game_record(

        game_id=gid,

        player=player,

        dealer=dealer,

        state=str(
            info.get(
                "last_state",
                "4"
            )
        ),

        game_number=game_number
    )


def save_finished_game(
    gid,
    raw=None,
    active_game=None,
    from_cache=False
):

    # -------------------------------------------------
    # ВАЖНО:
    # Не выходим сразу если ID существует.
    # Новая версия может быть качественнее
    # и add_or_update_game сама решит.
    # -------------------------------------------------

    info = tracked_games.get(
        str(gid),
        {}
    )

    if from_cache:

        parsed = (
            build_game_from_cached_cards(
                gid,
                info
            )
        )

    else:

        parsed = parse_game_data(
            gid,
            raw
        )

    if not parsed:
        return False

    if active_game:

        game_number = active_game.get(
            "gameNumber",
            active_game.get("number")
        )

        try:

            parsed[
                "game_number"
            ] = (

                int(game_number)

                if game_number is not None

                else parsed.get(
                    "game_number",
                    get_game_number()
                )
            )

        except (
            TypeError,
            ValueError
        ):
            pass

    ok = add_or_update_game(
        parsed
    )

    if ok:
        tracked_games.pop(
            str(gid),
            None
        )

    return ok


def process_game(active_game):

    gid = str(
        active_game.get(
            "id",
            ""
        )
    )

    if not gid:
        return

    # Если уже есть финальная STATE=5,
    # повторно API не мучаем.
    idx = find_game_index(gid)

    if idx != -1:

        old_state = str(
            history[idx].get(
                "state",
                ""
            )
        )

        if old_state == "5":
            return

    result = inspect_game_state(
        gid,
        active_game=active_game
    )

    if not result:
        return

    raw, state = result

    if str(state) == "5":

        print(
            f"🏁 ID={gid} "
            f"завершена STATE=5",
            flush=True
        )

        save_finished_game(
            gid,
            raw,
            active_game
        )

    elif str(state) == "4":

        print(
            f"📌 ID={gid} STATE=4 — "
            f"финальные карты зафиксированы",
            flush=True
        )


def finalize_disappeared_games(active_ids):

    for gid in list(
        tracked_games.keys()
    ):

        if gid in active_ids:
            continue

        info = tracked_games.get(
            gid,
            {}
        )

        last_state = str(
            info.get(
                "last_state",
                ""
            )
        )

        if last_state == "4":

            print(

                f"🏁 ID={gid} исчезла "
                f"после STATE=4 — "
                f"сохраняем кэш",

                flush=True
            )

            if save_finished_game(
                gid,
                from_cache=True
            ):

                print(
                    f"✅ ID={gid} "
                    f"сохранена из STATE=4",
                    flush=True
                )

            continue

        attempts = int(
            info.get(
                "final_attempts",
                0
            )
        )

        if attempts >= 3:

            print(

                f"⚠️ ID={gid} исчезла "
                f"с STATE={last_state}",

                flush=True
            )

            tracked_games.pop(
                gid,
                None
            )

            continue

        info["final_attempts"] = (
            attempts + 1
        )

        result = inspect_game_state(
            gid,
            final_attempt=True
        )

        if not result:
            continue

        raw, state = result

        if str(state) == "5":

            save_finished_game(
                gid,
                raw
            )

        elif str(state) == "4":

            info["last_state"] = "4"


# =====================================================================
# CLEANUP
# =====================================================================

def cleanup_predictions():

    global predictions

    if len(predictions) > 1000:

        predictions = predictions[
            -1000:
        ]

        atomic_save_json(
            PREDICTIONS_FILE,
            predictions
        )


# =====================================================================
# MAIN
# =====================================================================

def main():

    global history
    global predictions

    print(
        "\n=================================================="
    )

    print(
        "🚀 БОТ — HYBRID + EXTENDED PATTERN SCANNER"
    )

    print(
        "=================================================="
    )

    history = load_history()

    predictions = load_predictions()

    print(
        f"📚 История: "
        f"{len(history)} игр"
    )

    print(
        f"🕒 Окно истории: "
        f"{HISTORY_HOURS} часов"
    )

    print(
        f"📊 Прогнозов: "
        f"{len(predictions)}"
    )

    print(
        "🧠 Scanner:"
    )

    print(
        "   • карты и позиции"
    )

    print(
        "   • ранги и масти"
    )

    print(
        "   • переходы карт"
    )

    print(
        "   • цепочки раздач"
    )

    print(
        "   • победитель"
    )

    print(
        "   • очки игрока/дилера"
    )

    print(
        "   • разница очков"
    )

    print(
        "   • сумма очков"
    )

    print(
        "   • количество карт"
    )

    print(
        "   • lag-паттерны"
    )

    print(
        "   • window-последовательности"
    )

    print(
        "==================================================\n"
    )

    offset = get_offset()

    print(
        f"📌 Telegram offset: "
        f"{offset}"
    )

    while True:

        start = time.time()

        try:

            # =================================================
            # 1. API / HISTORY
            # =================================================

            games = get_active_games()

            if games:

                print(
                    f"📡 API: "
                    f"{len(games)} игр"
                )

            active_ids = set()

            for game in games:

                try:

                    gid = str(
                        game.get(
                            "id",
                            ""
                        )
                    )

                    if gid:
                        active_ids.add(gid)

                    process_game(game)

                except Exception as e:

                    print(
                        f"❌ Ошибка API: {e}",
                        flush=True
                    )

            finalize_disappeared_games(
                active_ids
            )

            # =================================================
            # TRACKER CLEANUP
            # =================================================

            cutoff_tracker = (
                datetime.now(MOSCOW_TZ)
                - timedelta(minutes=15)
            )

            for gid in list(
                tracked_games.keys()
            ):

                try:

                    seen = datetime.fromisoformat(

                        tracked_games[
                            gid
                        ].get(

                            "last_seen",

                            tracked_games[
                                gid
                            ][
                                "first_seen"
                            ]
                        )
                    )

                    if seen.tzinfo is None:

                        seen = MOSCOW_TZ.localize(
                            seen
                        )

                    if seen < cutoff_tracker:

                        tracked_games.pop(
                            gid,
                            None
                        )

                except Exception:
                    pass

            # =================================================
            # 2. TELEGRAM
            # =================================================

            offset = (
                process_telegram_updates(
                    offset
                )
            )

            # =================================================
            # 3. CHECK PREDICTIONS
            # =================================================

            check_predictions()

            # =================================================
            # 4. CLEANUP
            # =================================================

            cleanup_predictions()

            # Скользящая база последних 96 часов
            cleanup_history_by_time()

            elapsed = (
                time.time()
                - start
            )

            time.sleep(
                max(
                    0.1,
                    POLL_INTERVAL
                    - elapsed
                )
            )

        except KeyboardInterrupt:

            print(
                "\n🛑 Бот остановлен"
            )

            break

        except Exception as e:

            print(
                f"❌ Критическая ошибка: "
                f"{e}"
            )

            time.sleep(3)


# =====================================================================
# START
# =====================================================================

if __name__ == "__main__":
    main()