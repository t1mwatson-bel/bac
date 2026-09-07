import os
import sys
import json
import time
import requests
import pytz
import re

from datetime import datetime, timedelta
from collections import defaultdict, Counter


# =====================================================================
# ENV
# =====================================================================

BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("BOT_TOKEN_PROGNOZ")

CHANNEL_PROGNOZ = os.getenv("CHAT_ID_21") or os.getenv("CHANNEL_PROGNOZ")
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
# История
# ---------------------------------------------------------------------

HISTORY_HOURS = 96

# ---------------------------------------------------------------------
# Догоны
# ---------------------------------------------------------------------

DOGON_GAMES = 4

# ---------------------------------------------------------------------
# Цикл
# ---------------------------------------------------------------------

POLL_INTERVAL = 2.0
PREDICTION_COOLDOWN_SECONDS = 2

# ---------------------------------------------------------------------
# Целевые карты
# ---------------------------------------------------------------------

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

# Новый широкий сканер паттернов
WEIGHT_PATTERN_SCANNER = 2.20

MIN_MS_MATCHES = 2
MIN_ID1_MATCHES = 3
MIN_ID2_MATCHES = 2
MIN_SEQUENCE_MATCHES = 2

# Микро-паттерны
MIN_PATTERN_MATCHES = 3

# Минимум методов
MIN_ACTIVE_METHODS = 1

# Фильтр прогноза
MIN_FORECAST_PROBABILITY = 0.25
MIN_LEADER_GAP = 0.01

# Минимальная поддержка карты разными паттернами
MIN_PATTERN_SUPPORTERS = 1


# =====================================================================
# TELEGRAM
# =====================================================================

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"


# =====================================================================
# HEADERS / SESSION
# =====================================================================

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": f"{BASE_URL}/ru/live/twentyone/1643503-twentyone-game",
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
# GLOBALS
# =====================================================================

history = []
predictions = []
games_cache = {}

tracked_games = {}

last_prediction_time = 0


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

    if not card:
        return ""

    rank = normalize_rank(card.get("rank"))
    suit = normalize_suit(card.get("suit"))

    if not rank or not suit:
        return ""

    return f"{rank}{suit}\ufe0f"


def card_dict_to_target(card):

    text = card_to_text(card)

    if text in TARGET_CARDS:
        return text

    return None


# =====================================================================
# CARD POINTS
# =====================================================================

def card_points(rank):

    rank = normalize_rank(rank)

    if rank == "A":
        return 11

    if rank in {"J", "Q", "K"}:
        return 10

    try:
        return int(rank)
    except Exception:
        return 0


def calculate_hand_points(cards):
    """
    Blackjack/21 логика.
    Туз сначала 11.
    Если перебор — каждый туз может стать 1.
    """

    if not cards:
        return 0

    total = 0
    aces = 0

    for card in cards:

        rank = normalize_rank(card.get("rank"))

        if not rank:
            continue

        if rank == "A":
            total += 11
            aces += 1
        else:
            total += card_points(rank)

    while total > 21 and aces > 0:
        total -= 10
        aces -= 1

    return total


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
# HISTORY TIME
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
    Удаляет ТОЛЬКО игры,
    про которые точно известно,
    что они старше HISTORY_HOURS.

    Старые записи без timestamp НЕ удаляются.
    """

    global history

    now = datetime.now(MOSCOW_TZ)

    cutoff = now - timedelta(
        hours=HISTORY_HOURS
    )

    clean = []

    for game in history:

        if (
            not isinstance(game, dict)
            or not game.get("game_id")
        ):
            continue

        dt = parse_history_timestamp(game)

        # ВАЖНО:
        # Если timestamp отсутствует —
        # игру НЕ удаляем.
        if dt is None:
            clean.append(game)
            continue

        if dt >= cutoff:
            clean.append(game)

    changed = len(clean) != len(history)

    if changed:

        history = clean

        if save:
            atomic_save_json(
                DATA_FILE,
                history
            )

        print(
            f"♻️ Очистка истории: "
            f"{len(history)} игр "
            f"(окно {HISTORY_HOURS}ч)",
            flush=True
        )

    return changed


def load_history():

    global history

    data = load_json_file(
        DATA_FILE,
        []
    )

    if not isinstance(data, list):

        print(
            "⚠️ Неверный формат базы",
            flush=True
        )

        history = []
        return history

    # -------------------------------------------------------------
    # Загружаем всю существующую базу.
    #
    # ПРИ ЗАПУСКЕ НИЧЕГО НЕ ПЕРЕЗАПИСЫВАЕМ.
    # -------------------------------------------------------------

    history = [
        g
        for g in data
        if isinstance(g, dict)
        and g.get("game_id")
    ]

    print(
        f"📚 Загружена существующая база: "
        f"{len(history)} игр",
        flush=True
    )

    return history


def load_predictions():

    data = load_json_file(
        PREDICTIONS_FILE,
        []
    )

    if isinstance(data, list):
        return data

    return []


def find_game_index(gid):

    gid = str(gid)

    for i, game in enumerate(history):

        if str(game.get("game_id")) == gid:
            return i

    return -1


def game_exists(gid):

    return find_game_index(gid) != -1


# =====================================================================
# GAME FEATURES
# =====================================================================

def get_winner(player_points, dealer_points):

    player_bust = player_points > 21
    dealer_bust = dealer_points > 21

    if player_bust and dealer_bust:
        return "DRAW"

    if player_bust:
        return "DEALER"

    if dealer_bust:
        return "PLAYER"

    if player_points > dealer_points:
        return "PLAYER"

    if dealer_points > player_points:
        return "DEALER"

    return "DRAW"


def get_rank_chain(cards, length=None):

    if not cards:
        return ()

    ranks = []

    for card in cards:

        rank = normalize_rank(
            card.get("rank")
        )

        if rank:
            ranks.append(rank)

    if length:
        ranks = ranks[:length]

    return tuple(ranks)


def get_card_chain(cards, length=None):

    if not cards:
        return ()

    result = []

    for card in cards:

        text = card_to_text(card)

        if text:
            result.append(text)

    if length:
        result = result[:length]

    return tuple(result)


def points_bucket(points):

    try:
        points = int(points)
    except Exception:
        return "?"

    if points <= 10:
        return "0-10"

    if points <= 14:
        return "11-14"

    if points <= 16:
        return "15-16"

    if points <= 18:
        return "17-18"

    if points <= 20:
        return "19-20"

    if points == 21:
        return "21"

    return "BUST"


def diff_bucket(diff):

    try:
        diff = int(diff)
    except Exception:
        return "?"

    if diff <= -6:
        return "-6+"

    if diff <= -3:
        return "-3:-5"

    if diff == -2:
        return "-2"

    if diff == -1:
        return "-1"

    if diff == 0:
        return "0"

    if diff == 1:
        return "+1"

    if diff == 2:
        return "+2"

    if diff <= 5:
        return "+3:+5"

    return "+6+"


def enrich_game_features(game):
    """
    Добавляет все новые признаки.
    Работает и с уже сохранёнными играми.
    """

    if not isinstance(game, dict):
        return game

    player = game.get("player_cards", []) or []
    dealer = game.get("dealer_cards", []) or []

    player_points = calculate_hand_points(player)
    dealer_points = calculate_hand_points(dealer)

    points_sum = (
        player_points
        + dealer_points
    )

    points_diff = (
        player_points
        - dealer_points
    )

    winner = get_winner(
        player_points,
        dealer_points
    )

    player_count = len(player)
    dealer_count = len(dealer)

    game.update({

        # Очки
        "player_points": player_points,
        "dealer_points": dealer_points,
        "points_sum": points_sum,
        "points_diff": points_diff,

        # Победитель
        "winner": winner,

        # Количество карт
        "player_card_count": player_count,
        "dealer_card_count": dealer_count,

        # Первая карта
        "first_player_card": (
            player[0]
            if player
            else None
        ),

        "first_dealer_card": (
            dealer[0]
            if dealer
            else None
        ),

        # Ранги первых карт
        "first_player_rank": (
            normalize_rank(player[0].get("rank"))
            if player
            else None
        ),

        "first_dealer_rank": (
            normalize_rank(dealer[0].get("rank"))
            if dealer
            else None
        ),

        # Цепочки игрока
        "player_chain_2":
            list(get_rank_chain(player, 2)),

        "player_chain_3":
            list(get_rank_chain(player, 3)),

        "player_chain_4":
            list(get_rank_chain(player, 4)),

        # Цепочки дилера
        "dealer_chain_2":
            list(get_rank_chain(dealer, 2)),

        "dealer_chain_3":
            list(get_rank_chain(dealer, 3)),

        "dealer_chain_4":
            list(get_rank_chain(dealer, 4)),

        # Полная цепочка
        "player_rank_chain":
            list(get_rank_chain(player)),

        "dealer_rank_chain":
            list(get_rank_chain(dealer)),

        # Бакеты
        "player_points_bucket":
            points_bucket(player_points),

        "dealer_points_bucket":
            points_bucket(dealer_points),

        "points_sum_bucket":
            points_bucket(points_sum),

        "points_diff_bucket":
            diff_bucket(points_diff),

        # Структура
        "structure":
            f"P{player_count}_D{dealer_count}",

        "score_structure":
            (
                f"{player_points}-"
                f"{dealer_points}"
            ),

        "count_score_structure":
            (
                f"P{player_count}D{dealer_count}_"
                f"{points_bucket(player_points)}_"
                f"{points_bucket(dealer_points)}"
            )
    })

    return game


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


# =====================================================================
# PARSE API CARDS
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

    root = raw.get("Value", raw)

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
# PARSE GAME
# =====================================================================

def build_sequence(player, dealer):

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

    return all_cards, sequence


def parse_game_data(game_id, raw):

    if not raw:
        return None

    player, dealer, state = extract_game_sc(raw)

    if str(state) != "5":
        return None

    if not player and not dealer:
        return None

    now = datetime.now(MOSCOW_TZ)

    all_cards, sequence = build_sequence(
        player,
        dealer
    )

    game = {
        "game_id": str(game_id),

        "timestamp":
            now.isoformat(),

        "timestamp_msk":
            now.strftime("%H:%M:%S.%f")[:-3],

        "state": state,

        "player_cards": player,
        "dealer_cards": dealer,

        "player_suits":
            [c["suit"] for c in player],

        "player_ranks":
            [c["rank"] for c in player],

        "dealer_suits":
            [c["suit"] for c in dealer],

        "dealer_ranks":
            [c["rank"] for c in dealer],

        "all_suits":
            [c["suit"] for c in all_cards],

        "all_ranks":
            [c["rank"] for c in all_cards],

        "sequence":
            sequence,

        "total_cards":
            len(all_cards),

        "id_last_digit":
            str(game_id)[-1],

        "id_last_two":
            (
                str(game_id)[-2:]
                if len(str(game_id)) >= 2
                else ""
            )
    }

    return enrich_game_features(game)


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

    idx = find_game_index(gid)

    if idx != -1:
        return False

    # Обогащаем новую игру
    game = enrich_game_features(game)

    history.append(game)

    # Удаляем только реально старые игры
    cleanup_history_by_time(save=False)

    # Сохраняем всю текущую базу
    atomic_save_json(
        DATA_FILE,
        history
    )

    print(
        f"💾 Новая игра | ID={gid} | "
        f"P={len(game.get('player_cards', []))} | "
        f"D={len(game.get('dealer_cards', []))} | "
        f"Счёт={game.get('player_points')}-"
        f"{game.get('dealer_points')} | "
        f"Winner={game.get('winner')} | "
        f"База={len(history)}",
        flush=True
    )

    return True


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

        target = card_dict_to_target(card)

        if target:
            result.append(target)

    return result


# =====================================================================
# DISTRIBUTION
# =====================================================================

def normalize_distribution(counter):

    if not counter:
        return {}

    total = sum(counter.values())

    if total <= 0:
        return {}

    return {
        card: count / total
        for card, count
        in counter.items()
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
        "distribution":
            normalize_distribution(counter)
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
        "distribution":
            normalize_distribution(counter)
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
        "distribution":
            normalize_distribution(counter)
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
        "distribution":
            normalize_distribution(counter)
    }


# =====================================================================
# METHOD 5 — SEQUENCE
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

    pattern_counter = Counter()

    for game in recent:

        sig = get_game_signature(game)

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
        "distribution":
            normalize_distribution(counter)
    }


# =====================================================================
# PATTERN FEATURE EXTRACTION
# =====================================================================

def feature_value(game, feature):

    try:

        if feature == "winner":
            return game.get("winner")

        if feature == "first_player_rank":
            return game.get(
                "first_player_rank"
            )

        if feature == "first_dealer_rank":
            return game.get(
                "first_dealer_rank"
            )

        if feature == "player_count":
            return game.get(
                "player_card_count"
            )

        if feature == "dealer_count":
            return game.get(
                "dealer_card_count"
            )

        if feature == "structure":
            return game.get("structure")

        if feature == "player_points":
            return game.get(
                "player_points"
            )

        if feature == "dealer_points":
            return game.get(
                "dealer_points"
            )

        if feature == "points_sum":
            return game.get(
                "points_sum"
            )

        if feature == "points_diff":
            return game.get(
                "points_diff"
            )

        if feature == "player_points_bucket":
            return game.get(
                "player_points_bucket"
            )

        if feature == "dealer_points_bucket":
            return game.get(
                "dealer_points_bucket"
            )

        if feature == "points_sum_bucket":
            return game.get(
                "points_sum_bucket"
            )

        if feature == "points_diff_bucket":
            return game.get(
                "points_diff_bucket"
            )

        if feature == "player_chain_2":
            value = game.get(
                "player_chain_2",
                []
            )
            return tuple(value)

        if feature == "player_chain_3":
            value = game.get(
                "player_chain_3",
                []
            )
            return tuple(value)

        if feature == "dealer_chain_2":
            value = game.get(
                "dealer_chain_2",
                []
            )
            return tuple(value)

        if feature == "dealer_chain_3":
            value = game.get(
                "dealer_chain_3",
                []
            )
            return tuple(value)

        if feature == "score_structure":
            return game.get(
                "score_structure"
            )

        if feature == "count_score_structure":
            return game.get(
                "count_score_structure"
            )

    except Exception:
        return None

    return None


# =====================================================================
# MICRO PATTERN WEIGHT
# =====================================================================

def micro_pattern_weight(matches):
    """
    Именно логика 3-6 совпадений.

    3 = слабый, но используется
    4 = средний
    5 = сильный
    6+ = максимальный
    """

    if matches < 3:
        return 0.0

    if matches == 3:
        return 0.70

    if matches == 4:
        return 1.00

    if matches == 5:
        return 1.30

    if matches == 6:
        return 1.60

    return 1.80


# =====================================================================
# METHOD 6 — EXPANDED MICRO PATTERN SCANNER
# =====================================================================

def method_pattern_scanner():

    if len(history) < 10:
        return {}

    # --------------------------------------------------------------
    # Берём последние игры как текущий контекст
    # --------------------------------------------------------------

    context_games = history[-5:]

    if not context_games:
        return {}

    # --------------------------------------------------------------
    # Все признаки
    # --------------------------------------------------------------

    features = [

        # Победа
        "winner",

        # Первые карты
        "first_player_rank",
        "first_dealer_rank",

        # Количество карт
        "player_count",
        "dealer_count",
        "structure",

        # Точные очки
        "player_points",
        "dealer_points",
        "points_sum",
        "points_diff",

        # Диапазоны очков
        "player_points_bucket",
        "dealer_points_bucket",
        "points_sum_bucket",
        "points_diff_bucket",

        # Цепочки
        "player_chain_2",
        "player_chain_3",

        "dealer_chain_2",
        "dealer_chain_3",

        # Комбинации
        "score_structure",
        "count_score_structure"
    ]

    # --------------------------------------------------------------
    # Комбинированные признаки
    # --------------------------------------------------------------

    combinations = [

        (
            "winner",
            "first_player_rank"
        ),

        (
            "winner",
            "first_dealer_rank"
        ),

        (
            "first_player_rank",
            "first_dealer_rank"
        ),

        (
            "player_count",
            "dealer_count"
        ),

        (
            "player_points_bucket",
            "dealer_points_bucket"
        ),

        (
            "points_diff_bucket",
            "winner"
        ),

        (
            "structure",
            "winner"
        ),

        (
            "first_player_rank",
            "player_count"
        ),

        (
            "first_dealer_rank",
            "dealer_count"
        ),

        (
            "first_player_rank",
            "points_diff_bucket"
        ),

        (
            "first_dealer_rank",
            "points_diff_bucket"
        ),

        (
            "winner",
            "structure",
            "points_diff_bucket"
        )
    ]

    # --------------------------------------------------------------
    # Все исторические игры,
    # кроме последних контекстных
    # --------------------------------------------------------------

    search_history = history[:-5]

    if len(search_history) < MIN_PATTERN_MATCHES:
        search_history = history

    pattern_results = []

    # ==============================================================
    # ОДИНОЧНЫЕ ПРИЗНАКИ
    # ==============================================================

    for feature in features:

        context_values = set()

        for context_game in context_games:

            value = feature_value(
                context_game,
                feature
            )

            if value not in (
                None,
                "",
                (),
                []
            ):
                context_values.add(value)

        for context_value in context_values:

            counter = Counter()
            matches = 0

            for record in search_history:

                record_value = feature_value(
                    record,
                    feature
                )

                if record_value != context_value:
                    continue

                cards = get_target_cards_from_record(
                    record
                )

                if not cards:
                    continue

                matches += 1

                for card in cards:
                    counter[card] += 1

            if matches < MIN_PATTERN_MATCHES:
                continue

            dist = normalize_distribution(
                counter
            )

            if not dist:
                continue

            top_card, top_prob = (
                get_top_from_distribution(
                    dist
                )
            )

            if not top_card:
                continue

            pattern_results.append({

                "pattern":
                    f"{feature}={context_value}",

                "matches":
                    matches,

                "distribution":
                    dist,

                "top":
                    top_card,

                "top_probability":
                    top_prob,

                "weight":
                    micro_pattern_weight(matches)
            })

    # ==============================================================
    # КОМБИНИРОВАННЫЕ ПРИЗНАКИ
    # ==============================================================

    for combo in combinations:

        context_values = set()

        for context_game in context_games:

            values = tuple(
                feature_value(
                    context_game,
                    f
                )
                for f in combo
            )

            if any(
                v in (
                    None,
                    "",
                    (),
                    []
                )
                for v in values
            ):
                continue

            context_values.add(values)

        for values in context_values:

            counter = Counter()
            matches = 0

            for record in search_history:

                record_values = tuple(
                    feature_value(
                        record,
                        f
                    )
                    for f in combo
                )

                if record_values != values:
                    continue

                cards = get_target_cards_from_record(
                    record
                )

                if not cards:
                    continue

                matches += 1

                for card in cards:
                    counter[card] += 1

            if matches < MIN_PATTERN_MATCHES:
                continue

            dist = normalize_distribution(
                counter
            )

            if not dist:
                continue

            top_card, top_prob = (
                get_top_from_distribution(
                    dist
                )
            )

            pattern_results.append({

                "pattern":
                    f"COMBO:{'+'.join(combo)}={values}",

                "matches":
                    matches,

                "distribution":
                    dist,

                "top":
                    top_card,

                "top_probability":
                    top_prob,

                "weight":
                    micro_pattern_weight(matches)
            })

    if not pattern_results:
        return {}

    # ==============================================================
    # СОБИРАЕМ ГОЛОСА ПАТТЕРНОВ
    # ==============================================================

    scores = defaultdict(float)

    card_supporters = defaultdict(list)

    for result in pattern_results:

        weight = result["weight"]

        matches = result["matches"]

        # Чем сильнее лидер внутри паттерна,
        # тем больше его влияние
        dominance = result[
            "top_probability"
        ]

        for card, probability in (
            result["distribution"].items()
        ):

            score = (
                probability
                * weight
                * dominance
            )

            scores[card] += score

        # Запоминаем кто поддерживает топ
        top_card = result["top"]

        card_supporters[top_card].append({
            "pattern":
                result["pattern"],
            "matches":
                matches,
            "probability":
                result["top_probability"]
        })

    if not scores:
        return {}

    total = sum(scores.values())

    distribution = {
        card: score / total
        for card, score
        in scores.items()
    }

    top_patterns = sorted(
        pattern_results,
        key=lambda x: (
            x["weight"]
            * x["top_probability"]
        ),
        reverse=True
    )[:20]

    return {

        "name":
            "PATTERN",

        "weight":
            WEIGHT_PATTERN_SCANNER,

        "matches":
            len(pattern_results),

        "distribution":
            distribution,

        "patterns":
            top_patterns,

        "supporters":
            dict(card_supporters)
    }


# =====================================================================
# HYBRID ENGINE
# =====================================================================

def build_hybrid_prediction(
    game_id,
    timestamp_msk
):

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

        # Новый широкий сканер
        method_pattern_scanner()
    ]

    active = []

    scores = defaultdict(float)

    method_details = {}

    pattern_supporters = defaultdict(list)

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
        weight = result["weight"]

        top_card, top_prob = (
            get_top_from_distribution(dist)
        )

        method_details[name] = {

            "top":
                top_card,

            "probability":
                top_prob,

            "matches":
                result.get(
                    "matches",
                    0
                )
        }

        # Детали паттернов
        if name == "PATTERN":

            method_details[name][
                "patterns"
            ] = result.get(
                "patterns",
                []
            )

            method_details[name][
                "supporters"
            ] = result.get(
                "supporters",
                {}
            )

            for card, supporters in (
                result.get(
                    "supporters",
                    {}
                ).items()
            ):

                pattern_supporters[
                    card
                ].extend(supporters)

        active.append(name)

        for card, probability in dist.items():

            scores[card] += (
                probability
                * weight
            )

    if len(active) < MIN_ACTIVE_METHODS:

        print(
            f"⏭️ Недостаточно методов: {active}",
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

    best_card, best_probability = ranking[0]

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

    for name, info in (
        method_details.items()
    ):

        if info.get("top") == best_card:
            supporters.append(name)

    best_pattern_supporters = (
        pattern_supporters.get(
            best_card,
            []
        )
    )

    return {

        "card":
            best_card,

        "probability":
            best_probability,

        "second_card":
            second_card,

        "second_probability":
            second_probability,

        "gap":
            gap,

        "active_methods":
            active,

        "supporters":
            supporters,

        "pattern_supporters":
            best_pattern_supporters,

        "pattern_support_count":
            len(best_pattern_supporters),

        "method_details":
            method_details,

        "ranking":
            ranking[:5]
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

    pattern_count = result.get(
        "pattern_support_count",
        0
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

    # Если работает расширенный сканер,
    # смотрим количество микро-паттернов
    if (
        "PATTERN" in supporters
        and pattern_count < MIN_PATTERN_SUPPORTERS
    ):

        print(
            "🚫 Недостаточно "
            "микро-паттернов",
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
            (now - start).total_seconds()
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
# TELEGRAM SEND
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
                "chat_id":
                    chat_id,

                "text":
                    text,

                "parse_mode":
                    "HTML",

                "disable_web_page_preview":
                    True
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

                "chat_id":
                    chat_id,

                "message_id":
                    message_id,

                "text":
                    text,

                "parse_mode":
                    "HTML"
            },

            timeout=10
        )

        return bool(
            response.json().get("ok")
        )

    except Exception:
        return False


# =====================================================================
# MESSAGES
# =====================================================================

def make_prediction_message(entry):

    result = entry["hybrid"]

    card1 = result["card"]
    prob1 = result["probability"]

    card2 = (
        result.get("second_card")
        or "—"
    )

    prob2 = result.get(
        "second_probability",
        0.0
    )

    pattern_count = result.get(
        "pattern_support_count",
        0
    )

    text = (

        f"🎯 Игра: "
        f"#N{entry['target_number']}\n"

        f"🃏 {card1} — "
        f"{prob1 * 100:.1f}%\n"

        f"🥈 {card2} — "
        f"{prob2 * 100:.1f}%"
    )

    if pattern_count > 0:

        text += (
            f"\n🔗 Паттернов: "
            f"{pattern_count}"
        )

    return text


# =====================================================================
# PARSE CARDS FROM TELEGRAM MESSAGE
# =====================================================================

def parse_cards_from_message(text):

    if not text:
        return None

    # Игра должна быть завершена
    if not re.search(
        r"[✅🔰]",
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

        for match_text in matches:

            found_inner = re.findall(
                r"(10|[2-9AJQK])([♠♣♦♥])\ufe0f?",
                match_text
            )

            for rank, suit in found_inner:

                cards.append(
                    f"{rank}{suit}\ufe0f"
                )

    cards = list(
        dict.fromkeys(cards)
    )

    return {

        "game_number":
            game_number,

        "cards":
            cards
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

        if entry.get("status") != "pending":
            continue

        target = entry.get(
            "target_number"
        )

        predicted_cards = [
            c
            for c in entry.get(
                "predicted_cards",
                []
            )
            if c
        ]

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
        ):
            continue

        found = None

        all_available = True

        # ----------------------------------------------------------
        # Основная игра + DOGON_GAMES
        # ----------------------------------------------------------

        for dogon in range(
            DOGON_GAMES + 1
        ):

            num = add_game_offset(
                target,
                dogon
            )

            text = games_cache.get(num)

            if not text:

                all_available = False
                continue

            parsed = parse_cards_from_message(
                text
            )

            if not parsed:
                continue

            actual_cards = parsed.get(
                "cards",
                []
            )

            for card in predicted_cards:

                if card in actual_cards:

                    found = {

                        "num":
                            num,

                        "dogon":
                            dogon,

                        "card":
                            card
                    }

                    break

            if found:
                break

        # ----------------------------------------------------------
        # WIN
        # ----------------------------------------------------------

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
                f"✅ ЗАШЛО "
                f"#{found['num']} | "
                f"догон {found['dogon']} | "
                f"{found['card']}",
                flush=True
            )

            if msg_id and original_text:

                lines = (
                    original_text.split("\n")
                )

                lines[0] = (
                    f"🎯 Игра: "
                    f"#N{target} ✅"
                )

                new_text = "\n".join(
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

        # ----------------------------------------------------------
        # Ещё не все игры доступны
        # ----------------------------------------------------------

        if not all_available:
            continue

        # ----------------------------------------------------------
        # LOSE
        # ----------------------------------------------------------

        entry["status"] = "lose"

        changed = True

        print(
            f"❌ НЕ ЗАШЛО: "
            f"догоны 0-{DOGON_GAMES} "
            f"для #{target}",
            flush=True
        )

        if msg_id and original_text:

            lines = (
                original_text.split("\n")
            )

            lines[0] = (
                f"🎯 Игра: "
                f"#N{target} ❌"
            )

            new_text = "\n".join(
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
            "💾 Результаты прогнозов обновлены",
            flush=True
        )


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
                "offset":
                    offset,

                "timeout":
                    3,

                "limit":
                    50
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

                offset = update_id + 1

                save_offset(offset)

            post = (
                update.get("channel_post")
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

            # ------------------------------------------------------
            # ЗАВЕРШЁННАЯ ИГРА
            # ------------------------------------------------------

            parsed = parse_cards_from_message(
                text
            )

            if parsed:

                games_cache[
                    parsed["game_number"]
                ] = text

                print(
                    f"💾 КЭШ: "
                    f"#{parsed['game_number']} "
                    f"-> {parsed['cards']}",
                    flush=True
                )

                # --------------------------------------------------
                # ВАЖНО:
                # Сразу проверяем прогноз.
                # Не ждём конца API цикла.
                # --------------------------------------------------

                check_predictions()

            # ------------------------------------------------------
            # НОВАЯ ИГРА
            # ------------------------------------------------------

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

                if (
                    id_match
                    and num_match
                ):

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
                            ) == game_number

                            and entry.get(
                                "status"
                            ) == "pending"
                        ):

                            has_prediction = True
                            break

                    if not has_prediction:

                        print(
                            f"\n🆕 НОВАЯ ИГРА: "
                            f"#N{game_number} | "
                            f"ID={game_id}",
                            flush=True
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

                            message_id = telegram_send(
                                message
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
                                    f"на #N{game_number}",
                                    flush=True
                                )

    except Exception as e:

        print(
            f"⚠️ Updates error: {e}",
            flush=True
        )

    return offset


# =====================================================================
# CREATE PREDICTION
# =====================================================================

def create_hybrid_prediction(
    game_id,
    game_number
):

    global last_prediction_time
    global predictions

    game_id = str(game_id)

    # Уже есть прогноз?
    for entry in predictions:

        if (
            entry.get(
                "target_number"
            ) == game_number

            and entry.get(
                "status"
            ) == "pending"
        ):

            print(
                f"⏭️ Прогноз на "
                f"#N{game_number} уже есть",
                flush=True
            )

            return None

    now_ts = time.time()

    if (
        now_ts
        - last_prediction_time
        < PREDICTION_COOLDOWN_SECONDS
    ):

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
        "\n"
        "══════════════════════════════════",
        flush=True
    )

    print(
        f"🧠 HYBRID АНАЛИЗ | "
        f"ID={game_id} | "
        f"#N{game_number}",
        flush=True
    )

    print(
        f"📚 База: {len(history)} игр",
        flush=True
    )

    result = build_hybrid_prediction(
        game_id,
        timestamp_msk
    )

    if not result:

        print(
            "⏭️ Нет результата",
            flush=True
        )

        return None

    print(
        f"🥇 {result['card']} "
        f"{result['probability']:.1%}",
        flush=True
    )

    print(
        f"🥈 {result['second_card']} "
        f"{result['second_probability']:.1%}",
        flush=True
    )

    print(
        f"📏 Gap: "
        f"{result['gap']:.1%}",
        flush=True
    )

    print(
        f"🤝 Методы: "
        f"{result['supporters']}",
        flush=True
    )

    print(
        f"🔗 Микро-паттернов: "
        f"{result['pattern_support_count']}",
        flush=True
    )

    if not prediction_passes_filter(
        result
    ):

        print(
            "🚫 ПРОГНОЗ ОТМЕНЁН",
            flush=True
        )

        return None

    entry = {

        "target_game_id":
            game_id,

        "target_number":
            game_number,

        "timestamp_msk":
            timestamp_msk,

        "hybrid":
            result,

        "predicted_card":
            result["card"],

        # Оставляем TOP-2
        "predicted_cards": [

            result["card"],

            result.get(
                "second_card"
            )
        ],

        "status":
            "pending",

        "current_dogon":
            0,

        "created_at":
            datetime.now(
                MOSCOW_TZ
            ).isoformat(),

        "message_id":
            None,

        "original_text":
            "",

        "result_game":
            None,

        "found_card":
            None
    }

    predictions.append(entry)

    atomic_save_json(
        PREDICTIONS_FILE,
        predictions
    )

    last_prediction_time = now_ts

    print(
        f"🔮 ПРОГНОЗ СОЗДАН: "
        f"{result['card']} "
        f"для #N{game_number}",
        flush=True
    )

    return entry


# =====================================================================
# API GET GAME
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
# GAME TRACKER
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

            "game_id":
                str(gid),

            "first_seen":
                datetime.now(
                    MOSCOW_TZ
                ).isoformat(),

            "last_state":
                None,

            "player":
                [],

            "dealer":
                [],

            "game_number":
                None,

            "final_attempts":
                0
        }
    )

    if active_game:

        game_number = active_game.get(
            "gameNumber",
            active_game.get("number")
        )

        if game_number is not None:
            info["game_number"] = (
                game_number
            )

    info["last_state"] = str(state)

    info["player"] = player
    info["dealer"] = dealer

    info["last_seen"] = (
        datetime.now(
            MOSCOW_TZ
        ).isoformat()
    )

    print(

        f"🎮 ID={gid} | "
        f"STATE={state} | "
        f"P={len(player)} | "
        f"D={len(dealer)}"

        + (
            " | финальная проверка"
            if final_attempt
            else ""
        ),

        flush=True
    )

    return raw, state


# =====================================================================
# BUILD GAME FROM CACHE
# =====================================================================

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

    now = datetime.now(
        MOSCOW_TZ
    )

    all_cards, sequence = build_sequence(
        player,
        dealer
    )

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

    game = {

        "game_id":
            str(gid),

        "timestamp":
            now.isoformat(),

        "timestamp_msk":
            now.strftime(
                "%H:%M:%S.%f"
            )[:-3],

        "state":
            str(
                info.get(
                    "last_state",
                    "4"
                )
            ),

        "game_number":
            game_number,

        "player_cards":
            player,

        "dealer_cards":
            dealer,

        "player_suits":
            [
                c["suit"]
                for c in player
            ],

        "player_ranks":
            [
                c["rank"]
                for c in player
            ],

        "dealer_suits":
            [
                c["suit"]
                for c in dealer
            ],

        "dealer_ranks":
            [
                c["rank"]
                for c in dealer
            ],

        "all_suits":
            [
                c["suit"]
                for c in all_cards
            ],

        "all_ranks":
            [
                c["rank"]
                for c in all_cards
            ],

        "sequence":
            sequence,

        "total_cards":
            len(all_cards),

        "id_last_digit":
            str(gid)[-1],

        "id_last_two":
            (
                str(gid)[-2:]
                if len(str(gid)) >= 2
                else ""
            )
    }

    return enrich_game_features(game)


# =====================================================================
# SAVE FINISHED GAME
# =====================================================================

def save_finished_game(
    gid,
    raw=None,
    active_game=None,
    from_cache=False
):

    if game_exists(gid):

        tracked_games.pop(
            str(gid),
            None
        )

        return False

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

            parsed["game_number"] = (
                int(game_number)
                if game_number is not None
                else parsed.get(
                    "game_number",
                    get_game_number()
                )
            )

        except Exception:
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


# =====================================================================
# PROCESS GAME
# =====================================================================

def process_game(active_game):

    gid = str(
        active_game.get(
            "id",
            ""
        )
    )

    if not gid:
        return

    if game_exists(gid):
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
            f"STATE=5",
            flush=True
        )

        save_finished_game(
            gid,
            raw,
            active_game
        )

    elif str(state) == "4":

        print(
            f"📌 ID={gid} "
            f"STATE=4 — ждём исчезновения",
            flush=True
        )


# =====================================================================
# FINALIZE DISAPPEARED
# =====================================================================

def finalize_disappeared_games(
    active_ids
):

    for gid in list(
        tracked_games.keys()
    ):

        if gid in active_ids:
            continue

        if game_exists(gid):
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

        # ----------------------------------------------------------
        # STATE=4
        # ----------------------------------------------------------

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
                    f"сохранена из кэша",
                    flush=True
                )

            continue

        # ----------------------------------------------------------
        # Финальные попытки
        # ----------------------------------------------------------

        attempts = int(
            info.get(
                "final_attempts",
                0
            )
        )

        if attempts >= 3:

            print(
                f"⚠️ ID={gid} исчезла "
                f"STATE={last_state}",
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
# CLEANUP PREDICTIONS
# =====================================================================

def cleanup_predictions():

    global predictions

    if len(predictions) > 1000:

        predictions = predictions[-1000:]

        atomic_save_json(
            PREDICTIONS_FILE,
            predictions
        )


# =====================================================================
# ENRICH OLD HISTORY
# =====================================================================

def enrich_existing_history():
    """
    Старые игры не удаляются.

    Просто добавляем новые поля
    в память для работы Pattern Scanner.

    Файл здесь специально не перезаписываем.
    """

    global history

    count = 0

    for game in history:

        try:

            before = (
                "player_points"
                in game
            )

            enrich_game_features(game)

            if not before:
                count += 1

        except Exception:
            pass

    print(
        f"🧠 Обогащено старых игр: "
        f"{count}",
        flush=True
    )


# =====================================================================
# MAIN
# =====================================================================

def main():

    global history
    global predictions

    print(
        "\n"
        "=================================================="
    )

    print(
        "🚀 OLD HYBRID PATTERN BOT"
    )

    print(
        "=================================================="
    )

    # --------------------------------------------------------------
    # Загружаем существующую базу
    # НИЧЕГО НЕ УДАЛЯЕМ ПРИ СТАРТЕ
    # --------------------------------------------------------------

    history = load_history()

    # Добавляем признаки в память
    enrich_existing_history()

    predictions = load_predictions()

    print(
        f"📚 История: "
        f"{len(history)} игр"
    )

    print(
        f"📊 Прогнозов: "
        f"{len(predictions)}"
    )

    print(
        f"⏱ Окно истории: "
        f"{HISTORY_HOURS} часов"
    )

    print(
        "🧠 Pattern Scanner: "
        "АКТИВЕН"
    )

    print(
        "🔗 Микро-паттерны: "
        "3+ совпадения"
    )

    print(
        "==================================================\n"
    )

    offset = get_offset()

    print(
        f"📌 Telegram offset: "
        f"{offset}",
        flush=True
    )

    while True:

        start = time.time()

        try:

            # ======================================================
            # 1. API ИСТОРИЯ
            # ======================================================

            games = get_active_games()

            if games:

                print(
                    f"📡 API: "
                    f"{len(games)} игр",
                    flush=True
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

            # ======================================================
            # 2. ИСЧЕЗНУВШИЕ ИГРЫ
            # ======================================================

            finalize_disappeared_games(
                active_ids
            )

            # ======================================================
            # 3. ЧИСТИМ ЗАВИСШИЙ TRACKER
            # ======================================================

            cutoff_tracker = (
                datetime.now(MOSCOW_TZ)
                - timedelta(minutes=15)
            )

            for gid in list(
                tracked_games.keys()
            ):

                try:

                    seen = (
                        datetime.fromisoformat(
                            tracked_games[gid].get(
                                "last_seen",
                                tracked_games[gid][
                                    "first_seen"
                                ]
                            )
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

            # ======================================================
            # 4. TELEGRAM
            #
            # Внутри сразу вызывается check_predictions()
            # после получения завершённой игры
            # ======================================================

            offset = process_telegram_updates(
                offset
            )

            # ======================================================
            # 5. ДОПОЛНИТЕЛЬНАЯ ПРОВЕРКА ПРОГНОЗОВ
            # ======================================================

            check_predictions()

            # ======================================================
            # 6. CLEANUP
            # ======================================================

            cleanup_predictions()

            # Старше 96 часов удаляем.
            # При старте база НЕ перезаписывается.
            cleanup_history_by_time()

            elapsed = (
                time.time()
                - start
            )

            time.sleep(
                max(
                    0.1,
                    POLL_INTERVAL - elapsed
                )
            )

        except KeyboardInterrupt:

            print(
                "\n🛑 Бот остановлен"
            )

            break

        except Exception as e:

            print(
                f"❌ Критическая ошибка: {e}",
                flush=True
            )

            time.sleep(3)


# =====================================================================
# START
# =====================================================================

if __name__ == "__main__":
    main()