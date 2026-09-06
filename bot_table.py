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

HISTORY_HOURS = 48
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

MIN_MS_MATCHES = 2
MIN_ID1_MATCHES = 3
MIN_ID2_MATCHES = 2
MIN_SEQUENCE_MATCHES = 2

MIN_FORECAST_PROBABILITY = 0.25
MIN_LEADER_GAP = 0.01
MIN_ACTIVE_METHODS = 1

PREDICTION_COOLDOWN_SECONDS = 2

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": f"{BASE_URL}/ru/live/twentyone/1643503-twentyone-game",
    "Cookie": "platform_type=desktop; lng=ru; cookies_agree_type=3; tzo=3; is12h=0"
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

# =====================================================================
# CARD MAPS
# =====================================================================

SUITS_NAMES = {0: "♠️", 1: "♣️", 2: "♦️", 3: "♥️"}

RANKS = {
    2: "2", 3: "3", 4: "4", 5: "5", 6: "6",
    7: "7", 8: "8", 9: "9", 10: "10",
    11: "J", 12: "Q", 13: "K", 14: "A"
}

# =====================================================================
# GLOBALS
# =====================================================================

history = []
predictions = []
games_cache = {}
# Трекер игр для надёжного отслеживания финального STATE=5
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
        "0": "♠", "♠": "♠", "spade": "♠", "spades": "♠", "s": "♠",
        "1": "♣", "♣": "♣", "club": "♣", "clubs": "♣", "c": "♣",
        "2": "♦", "♦": "♦", "diamond": "♦", "diamonds": "♦", "d": "♦",
        "3": "♥", "♥": "♥", "heart": "♥", "hearts": "♥", "h": "♥"
    }

    return mapping.get(t)


def normalize_rank(v):
    if v is None or isinstance(v, bool):
        return None

    if isinstance(v, int):
        return RANKS.get(v)

    t = str(v).strip().upper().replace("А", "A")

    if t in {"2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"}:
        return t

    try:
        return RANKS.get(int(t))
    except:
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
    return text if text in TARGET_CARDS else None


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
        print(f"⚠️ Ошибка чтения {filename}: {e}", flush=True)
        return default


def atomic_save_json(filename, data):
    tmp = filename + ".tmp"

    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        os.replace(tmp, filename)
        return True

    except Exception as e:
        print(f"⚠️ Ошибка сохранения {filename}: {e}", flush=True)
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except:
            pass
        return False


# =====================================================================
# HISTORY
# =====================================================================

def parse_history_timestamp(game):
    """Возвращает datetime игры для окна последних 48 часов."""
    value = game.get("timestamp") or game.get("timestamp_iso")
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = MOSCOW_TZ.localize(dt)
        return dt.astimezone(MOSCOW_TZ)
    except Exception:
        return None


def cleanup_history_by_time(save=True):
    """Оставляет только завершённые игры за последние HISTORY_HOURS часов."""
    global history

    now = datetime.now(MOSCOW_TZ)
    cutoff = now - timedelta(hours=HISTORY_HOURS)

    clean = []
    for game in history:
        if not isinstance(game, dict) or not game.get("game_id"):
            continue
        dt = parse_history_timestamp(game)
        if dt is not None and dt >= cutoff:
            clean.append(game)

    clean.sort(key=lambda g: parse_history_timestamp(g) or now)

    changed = len(clean) != len(history)
    history = clean

    if changed and save:
        atomic_save_json(DATA_FILE, history)
        print(f"♻️ История очищена по времени: осталось {len(history)} игр за {HISTORY_HOURS}ч", flush=True)

    return changed


def load_history():
    global history

    data = load_json_file(DATA_FILE, [])
    if not isinstance(data, list):
        data = []

    history = [g for g in data if isinstance(g, dict) and g.get("game_id")]
    cleanup_history_by_time(save=False)

    # Старые записи без полного timestamp считаются неактуальными.
    atomic_save_json(DATA_FILE, history)
    return history


def load_predictions():
    data = load_json_file(PREDICTIONS_FILE, [])
    return data if isinstance(data, list) else []


def find_game_index(gid):
    gid = str(gid)
    for i, game in enumerate(history):
        if str(game.get("game_id")) == gid:
            return i
    return -1


def game_exists(gid):
    return find_game_index(gid) != -1


# =====================================================================
# DEEP PARSING
# =====================================================================

def deep_find_value(obj, keys):
    wanted = {str(x).lower() for x in keys}

    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).lower() in wanted:
                return v

        for v in obj.values():
            result = deep_find_value(v, keys)
            if result is not None:
                return result

    elif isinstance(obj, list):
        for item in obj:
            result = deep_find_value(item, keys)
            if result is not None:
                return result

    return None


def extract_card_from_dict(obj):
    if not isinstance(obj, dict):
        return None

    rank = None
    suit = None

    rank_keys = {"rank", "value", "v", "cardvalue", "card_value", "nominal", "denomination"}
    suit_keys = {"suit", "s", "card_suit", "cardsuit", "mast", "color"}

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
        return {"rank": rank, "suit": f"{suit}\ufe0f"}

    return None


def classify_key(key):
    key = str(key).strip().lower()

    if key in ["p", "pcards", "playercards"] or key.startswith("player") or "playercard" in key:
        return "player"

    if key in ["d", "dcards", "dealercards"] or key.startswith("dealer") or "dealercard" in key:
        return "dealer"

    return None


def find_cards_recursive(obj, context=None, player=None, dealer=None):
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

            if kl in {"p", "p1", "p2", "p3", "p4", "p5", "p6", "p7", "p8", "p9"}:
                ctx = "player"

            if kl in {"d", "d1", "d2", "d3", "d4", "d5", "d6", "d7", "d8", "d9"}:
                ctx = "dealer"

            find_cards_recursive(v, ctx, player, dealer)

    elif isinstance(obj, list):
        for item in obj:
            find_cards_recursive(item, context, player, dealer)

    return player, dealer


def clean_cards(cards):
    result = []
    seen = set()

    for card in cards:
        if not card:
            continue

        rank = normalize_rank(card.get("rank"))
        suit = normalize_suit(card.get("suit"))

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
    """Преобразует P1/P2 из SC->S в список словарей rank/suit."""
    if not value or value == "[]":
        return []

    try:
        cards = json.loads(value) if isinstance(value, str) else value
    except Exception:
        return []

    if not isinstance(cards, list):
        return []

    result = []
    suit_map = {0: "♠️", 1: "♣️", 2: "♦️", 3: "♥️"}
    rank_map = {1: "A", 6: "6", 7: "7", 8: "8", 9: "9", 10: "10", 11: "J", 12: "Q", 13: "K", 14: "A"}

    for card in cards:
        if not isinstance(card, dict):
            continue
        try:
            cs = int(card.get("CS"))
            cv = int(card.get("CV"))
        except (TypeError, ValueError):
            continue

        suit = suit_map.get(cs)
        rank = rank_map.get(cv)
        if rank and suit:
            result.append({"rank": rank, "suit": suit})

    return result


def extract_game_sc(raw):
    """Достаёт P1, P2 и STATE напрямую из Value -> SC -> S."""
    if not isinstance(raw, dict):
        return [], [], None

    root = raw.get("Value", raw)
    if not isinstance(root, dict):
        return [], [], None

    sc = root.get("SC", {})
    items = sc.get("S", []) if isinstance(sc, dict) else []
    if not isinstance(items, list):
        return [], [], None

    p1_value = None
    p2_value = None
    state = None

    for item in items:
        if not isinstance(item, dict):
            continue
        key = str(item.get("Key", "")).upper()
        value = item.get("Value")
        if key == "P1":
            p1_value = value
        elif key == "P2":
            p2_value = value
        elif key == "STATE":
            state = str(value) if value is not None else None

    return get_cards_from_api_value(p1_value), get_cards_from_api_value(p2_value), state


def parse_game_data(game_id, raw):
    """Парсит завершённую игру напрямую из API SC -> S -> P1/P2/STATE."""
    if not raw:
        return None

    player, dealer, state = extract_game_sc(raw)

    # В историю попадает только финальная версия завершённой игры.
    if str(state) != "5":
        return None

    if not player and not dealer:
        return None

    now = datetime.now(MOSCOW_TZ)
    all_cards = []
    sequence = []
    pos = 1

    # Сохраняем порядок раздачи в существующем формате истории.
    for i in range(max(len(player), len(dealer))):
        if i < len(player):
            card = player[i]
            all_cards.append(card)
            sequence.append({"position": pos, "who": "P", "rank": card["rank"], "suit": card["suit"]})
            pos += 1
        if i < len(dealer):
            card = dealer[i]
            all_cards.append(card)
            sequence.append({"position": pos, "who": "D", "rank": card["rank"], "suit": card["suit"]})
            pos += 1

    return {
        "game_id": str(game_id),
        # Полная дата нужна для корректного скользящего окна 48 часов.
        "timestamp": now.isoformat(),
        # Старый формат оставляем для существующего метода миллисекунд.
        "timestamp_msk": now.strftime("%H:%M:%S.%f")[:-3],
        "state": state,
        "player_cards": player,
        "dealer_cards": dealer,
        "player_suits": [c["suit"] for c in player],
        "player_ranks": [c["rank"] for c in player],
        "dealer_suits": [c["suit"] for c in dealer],
        "dealer_ranks": [c["rank"] for c in dealer],
        "all_suits": [c["suit"] for c in all_cards],
        "all_ranks": [c["rank"] for c in all_cards],
        "sequence": sequence,
        "total_cards": len(all_cards),
        "first_player_card": player[0] if player else None,
        "id_last_digit": str(game_id)[-1],
        "id_last_two": str(game_id)[-2:] if len(str(game_id)) >= 2 else ""
    }


# =====================================================================
# MERGE / SAVE GAME
# =====================================================================

def add_or_update_game(game):
    global history

    gid = str(game.get("game_id") or "")
    if not gid:
        return False

    # Перед каждой записью очищаем всё старше 48 часов.
    cleanup_history_by_time(save=False)

    idx = find_game_index(gid)
    if idx != -1:
        # Завершённая игра с тем же ID уже есть — дубль не создаём.
        return False

    history.append(game)
    history.sort(key=lambda g: parse_history_timestamp(g) or datetime.now(MOSCOW_TZ))

    # После добавления ещё раз гарантируем окно ровно последних 48 часов.
    cleanup_history_by_time(save=False)
    atomic_save_json(DATA_FILE, history)

    print(
        f"💾 Новая завершённая игра | ID={gid} | "
        f"P1={len(game.get('player_cards', []))} | "
        f"P2={len(game.get('dealer_cards', []))} | "
        f"База={len(history)} игр/{HISTORY_HOURS}ч",
        flush=True
    )
    return True


# =====================================================================
# TARGET CARDS FROM RECORD
# =====================================================================

def get_target_cards_from_record(record):
    result = []

    all_cards = record.get("player_cards", []) + record.get("dealer_cards", [])

    for card in all_cards:
        target = card_dict_to_target(card)
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

    return {card: count / total for card, count in counter.items()}


def get_top_from_distribution(dist):
    if not dist:
        return None, 0.0

    sorted_items = sorted(dist.items(), key=lambda x: x[1], reverse=True)
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
        target_ms = int(timestamp_msk.split(".")[1])
    except:
        return {}

    counter = Counter()
    matches = 0

    for record in history:
        record_time = record.get("timestamp_msk", "")
        if "." not in record_time:
            continue

        try:
            ms = int(record_time.split(".")[1])
        except:
            continue

        if ms != target_ms:
            continue

        cards = get_target_cards_from_record(record)
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
        "distribution": normalize_distribution(counter)
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
        rid = str(record.get("game_id", ""))
        if not rid:
            continue
        if rid == game_id:
            continue
        if rid[-1] != digit:
            continue

        cards = get_target_cards_from_record(record)
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
        "distribution": normalize_distribution(counter)
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
        rid = str(record.get("game_id", ""))
        if len(rid) < 2:
            continue
        if rid == game_id:
            continue
        if rid[-2:] != suffix:
            continue

        cards = get_target_cards_from_record(record)
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
        "distribution": normalize_distribution(counter)
    }


# =====================================================================
# METHOD 4 — LOCAL FREQUENCY
# =====================================================================

def method_frequency():
    counter = Counter()

    recent = history[-150:]

    matches = 0

    for record in recent:
        cards = get_target_cards_from_record(record)
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
        "distribution": normalize_distribution(counter)
    }


# =====================================================================
# METHOD 5 — SEQUENCE PATTERN (СКАНЕР)
# =====================================================================

def get_game_signature(game):
    sequence = game.get("sequence", [])

    if not sequence:
        return ()

    signature = []

    for item in sequence[:4]:
        who = item.get("who", "")
        rank = normalize_rank(item.get("rank"))

        if who and rank:
            signature.append(f"{who}:{rank}")

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

        cards = get_target_cards_from_record(record)
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
        "distribution": normalize_distribution(counter)
    }


# =====================================================================
# HYBRID ENGINE
# =====================================================================

def build_hybrid_prediction(game_id, timestamp_msk):
    results = [
        method_milliseconds(timestamp_msk),
        method_id1(game_id),
        method_id2(game_id),
        method_frequency(),
        method_sequence()
    ]

    active = []
    scores = defaultdict(float)
    method_details = {}

    for result in results:
        if not result:
            continue

        dist = result.get("distribution", {})
        if not dist:
            continue

        name = result["name"]
        weight = result["weight"]

        top_card, top_prob = get_top_from_distribution(dist)

        method_details[name] = {
            "top": top_card,
            "probability": top_prob,
            "matches": result.get("matches", 0)
        }

        active.append(name)

        for card, probability in dist.items():
            scores[card] += probability * weight

    if len(active) < MIN_ACTIVE_METHODS:
        print(f"⏭️ Недостаточно методов: {active}", flush=True)
        return None

    if not scores:
        return None

    total_score = sum(scores.values())

    probabilities = {
        card: score / total_score
        for card, score in scores.items()
    }

    ranking = sorted(probabilities.items(), key=lambda x: x[1], reverse=True)

    if not ranking:
        return None

    best_card, best_probability = ranking[0]

    second_card = None
    second_probability = 0.0

    if len(ranking) > 1:
        second_card = ranking[1][0]
        second_probability = ranking[1][1]

    gap = best_probability - second_probability

    supporters = []

    for name, info in method_details.items():
        if info.get("top") == best_card:
            supporters.append(name)

    return {
        "card": best_card,
        "probability": best_probability,
        "second_card": second_card,
        "second_probability": second_probability,
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

    probability = result.get("probability", 0)
    gap = result.get("gap", 0)
    supporters = result.get("supporters", [])

    if probability < MIN_FORECAST_PROBABILITY:
        print(f"🚫 Лидер слабый: {probability:.1%}", flush=True)
        return False

    if gap < MIN_LEADER_GAP:
        print(f"🚫 Нет преимущества: gap={gap:.1%}", flush=True)
        return False

    if len(supporters) < MIN_ACTIVE_METHODS:
        print(f"🚫 Мало поддержки: {supporters}", flush=True)
        return False

    return True


# =====================================================================
# GAME NUMBER
# =====================================================================

def get_game_number():
    now = datetime.now(MOSCOW_TZ)
    start = now.replace(hour=3, minute=0, second=0, microsecond=0)

    if now < start:
        start -= timedelta(days=1)

    return (int((now - start).total_seconds() // 60) % 1440) + 1


def add_game_offset(number, offset):
    return ((int(number) - 1 + int(offset)) % 1440) + 1


# =====================================================================
# TELEGRAM
# =====================================================================

def telegram_send(text, chat_id=None):
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
            return data["result"]["message_id"]

        print(f"❌ Telegram: {data}", flush=True)

    except Exception as e:
        print(f"❌ Telegram ошибка: {e}", flush=True)

    return None


def telegram_edit(message_id, text, chat_id=None):
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

        return bool(response.json().get("ok"))

    except:
        return False


# =====================================================================
# MESSAGES
# =====================================================================

def make_prediction_message(entry):
    result = entry["hybrid"]
    
    card1 = result["card"]
    prob1 = result["probability"]
    card2 = result.get("second_card") or "—"
    prob2 = result.get("second_probability", 0.0)
    
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

    # ✅ ПРОВЕРЯЕМ, ЧТО ИГРА ЗАВЕРШЕНА (есть ✅ или 🔰)
    if not re.search(r'[✅🔰]', text):
        return None

    match = re.search(r"#N(\d+)", text)
    if not match:
        return None

    game_number = int(match.group(1))

    found = re.findall(r"(10|[2-9AJQK])([♠♣♦♥])\ufe0f?", text)
    if not found:
        found = re.findall(r"(10|[2-9AJQK])([♠♣♦♥])", text)

    cards = [f"{rank}{suit}\ufe0f" for rank, suit in found]

    if not cards:
        matches = re.findall(r"\((.*?)\)", text)
        for match in matches:
            found_inner = re.findall(r"(10|[2-9AJQK])([♠♣♦♥])\ufe0f?", match)
            for rank, suit in found_inner:
                cards.append(f"{rank}{suit}\ufe0f")

    cards = list(dict.fromkeys(cards))

    return {
        "game_number": game_number,
        "cards": cards,
    }


# =====================================================================
# OFFSET
# =====================================================================

def get_offset():
    try:
        if os.path.exists(OFFSET_FILE):
            with open(OFFSET_FILE, "r") as f:
                return int(f.read().strip())
    except:
        pass

    return 0


def save_offset(offset):
    try:
        with open(OFFSET_FILE, "w") as f:
            f.write(str(offset))
    except:
        pass


# =====================================================================
# PROCESS TELEGRAM UPDATES
# =====================================================================

def process_telegram_updates(offset):
    global predictions, games_cache

    if not CHANNEL_STATS:
        return offset

    try:
        response = SESSION.get(
            f"{TELEGRAM_API}/getUpdates",
            params={"offset": offset, "timeout": 3, "limit": 50},
            timeout=10,
        )

        data = response.json()
        if not data.get("ok"):
            return offset

        for update in data.get("result", []):
            update_id = update.get("update_id")
            if update_id is not None:
                offset = update_id + 1
                save_offset(offset)

            post = update.get("channel_post") or update.get("edited_channel_post")
            if not post:
                continue

            chat_id = str(post.get("chat", {}).get("id", ""))
            if chat_id != str(CHANNEL_STATS):
                continue

            text = post.get("text", "")

            # ✅ СОХРАНЯЕМ ЗАВЕРШЕННЫЕ ИГРЫ В КЭШ
            parsed = parse_cards_from_message(text)
            if parsed:
                games_cache[parsed["game_number"]] = text
                print(f"💾 КЭШ: #{parsed['game_number']} -> {parsed['cards']}")

            # ✅ ИЩЕМ НОВУЮ ИГРУ (С ОЖИДАНИЕМ)
            if "⏳ Ожидание игры" in text:
                id_match = re.search(r"ID:\s*(\d+)", text)
                num_match = re.search(r"#N(\d+)", text)

                if id_match and num_match:
                    game_id = id_match.group(1)
                    game_number = int(num_match.group(1))

                    # Проверяем, есть ли уже прогноз на этот номер
                    has_prediction = False
                    for entry in predictions:
                        if entry.get("target_number") == game_number and entry.get("status") == "pending":
                            has_prediction = True
                            break

                    if not has_prediction:
                        print(f"\n🆕 НОВАЯ ИГРА ИЗ КАНАЛА: #N{game_number} | ID={game_id}")
                        prediction = create_hybrid_prediction(game_id, game_number)

                        if prediction:
                            message = make_prediction_message(prediction)
                            prediction["original_text"] = message
                            message_id = telegram_send(message)

                            if message_id:
                                prediction["message_id"] = message_id
                                atomic_save_json(PREDICTIONS_FILE, predictions)
                                print(f"📤 ОТПРАВЛЕНО: {prediction['predicted_card']} на #N{game_number}")

    except Exception as e:
        print(f"⚠️ Updates error: {e}")

    return offset


# =====================================================================
# CHECK PREDICTIONS
# =====================================================================

def check_predictions():
    global predictions

    if not predictions:
        print("📭 Нет прогнозов для проверки")
        return

    if not CHANNEL_STATS:
        print("❌ CHANNEL_STATS не задан")
        return

    print(f"🔍 Проверяем {len(predictions)} прогнозов, кэш: {len(games_cache)}")

    changed = False

    for entry in predictions:
        if entry.get("status") != "pending":
            continue

        target = entry.get("target_number")
        predicted_cards = entry.get("predicted_cards", [])
        msg_id = entry.get("message_id")
        original_text = entry.get("original_text", "")

        if not target or not predicted_cards or not msg_id:
            continue

        found = None
        all_available = True

        for dogon in range(DOGON_GAMES + 1):
            num = add_game_offset(target, dogon)
            text = games_cache.get(num)

            if not text:
                all_available = False
                continue

            parsed = parse_cards_from_message(text)
            if not parsed:
                continue

            actual_cards = parsed.get("cards", [])

            for card in predicted_cards:
                if card in actual_cards:
                    found = {"num": num, "dogon": dogon, "card": card}
                    break

            if found:
                break

        if found:
            entry["status"] = "win"
            entry["result_game"] = found["num"]
            entry["found_card"] = found["card"]
            entry["current_dogon"] = found["dogon"]
            changed = True

            print(f"✅ ЗАШЛО на #{found['num']} | догон {found['dogon']} | {found['card']}")

            if msg_id and original_text:
                lines = original_text.split('\n')
                lines[0] = f"🎯 Игра: #N{target} ✅"
                new_text = '\n'.join(lines)
                telegram_edit(msg_id, new_text)

            atomic_save_json(PREDICTIONS_FILE, predictions)
            continue

        if not all_available:
            print(f"⏳ Ожидание #{target} (не все догоны в кэше)")
            continue

        entry["status"] = "lose"
        changed = True

        print(f"❌ НЕ ЗАШЛО: догоны 0-{DOGON_GAMES} для #{target}")

        if msg_id and original_text:
            lines = original_text.split('\n')
            lines[0] = f"🎯 Игра: #N{target} ❌"
            new_text = '\n'.join(lines)
            telegram_edit(msg_id, new_text)

        atomic_save_json(PREDICTIONS_FILE, predictions)

    if changed:
        print("💾 Прогнозы обновлены")


# =====================================================================
# CREATE PREDICTION
# =====================================================================

def create_hybrid_prediction(game_id, game_number):
    global last_prediction_time

    game_id = str(game_id)

    # Проверяем, есть ли уже прогноз на этот номер
    for entry in predictions:
        if entry.get("target_number") == game_number and entry.get("status") == "pending":
            print(f"⏭️ Прогноз на #N{game_number} уже существует")
            return None

    # Минимальная задержка
    now_ts = time.time()
    if now_ts - last_prediction_time < PREDICTION_COOLDOWN_SECONDS:
        print(f"⏭️ Cooldown {PREDICTION_COOLDOWN_SECONDS} сек")
        return None

    now = datetime.now(MOSCOW_TZ)
    timestamp_msk = now.strftime("%H:%M:%S.%f")[:-3]

    print("\n══════════════════════════════════")
    print(f"🧠 HYBRID АНАЛИЗ | ID={game_id} | #N{game_number}")
    print(f"⏱ Timestamp={timestamp_msk}")

    result = build_hybrid_prediction(game_id, timestamp_msk)

    if not result:
        print("⏭️ Гибрид не дал результата")
        return None

    print(f"🥇 {result['card']} {result['probability']:.1%}")
    print(f"🥈 {result['second_card']} {result['second_probability']:.1%}")
    print(f"📏 Gap: {result['gap']:.1%}")
    print(f"🤝 Поддержка: {result['supporters']}")

    if not prediction_passes_filter(result):
        print("🚫 ПРОГНОЗ ОТМЕНЁН ФИЛЬТРОМ")
        return None

    entry = {
        "target_game_id": game_id,
        "target_number": game_number,
        "timestamp_msk": timestamp_msk,
        "hybrid": result,
        "predicted_card": result["card"],
        "predicted_cards": [result["card"], result.get("second_card")],
        "status": "pending",
        "current_dogon": 0,
        "created_at": datetime.now(MOSCOW_TZ).isoformat(),
        "message_id": None,
        "original_text": "",
        "result_game": None,
        "found_card": None
    }

    predictions.append(entry)
    atomic_save_json(PREDICTIONS_FILE, predictions)

    last_prediction_time = now_ts

    print(f"🔮 ПРОГНОЗ СОЗДАН: {result['card']} для #N{game_number}")

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
        response = SESSION.get(url, timeout=7)

        if response.status_code == 200:
            return response.json()

    except:
        pass

    return None


# =====================================================================
# API GET ACTIVE GAMES (ДЛЯ ИСТОРИИ)
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
        response = SESSION.get(url, timeout=10)

        if response.status_code != 200:
            return []

        data = response.json()

        if isinstance(data, list):
            games = data
        elif isinstance(data, dict):
            games = data.get("Value", [])
        else:
            games = []

        result = []

        for game in games:
            if not isinstance(game, dict):
                continue

            liga = game.get("liga", {})

            if str(liga.get("id", "")) != str(LEAGUE_ID):
                continue

            if not game.get("id"):
                continue

            result.append(game)

        return result

    except Exception as e:
        print(f"❌ API games: {e}", flush=True)
        return []


# =====================================================================
# PROCESS GAME (ТОЛЬКО ДЛЯ ИСТОРИИ, БЕЗ ПРОГНОЗОВ)
# =====================================================================

def inspect_game_state(gid, active_game=None, final_attempt=False):
    """Запрашивает игру напрямую и возвращает последнее состояние P1/P2/STATE."""
    raw = get_game_data(gid)
    if not raw:
        return None

    player, dealer, state = extract_game_sc(raw)
    if state is None:
        return None

    info = tracked_games.setdefault(str(gid), {
        "game_id": str(gid),
        "first_seen": datetime.now(MOSCOW_TZ).isoformat(),
        "last_state": None,
        "player": [],
        "dealer": [],
        "game_number": None,
        "final_attempts": 0,
    })

    if active_game:
        game_number = active_game.get("gameNumber", active_game.get("number"))
        if game_number is not None:
            info["game_number"] = game_number

    info["last_state"] = str(state)
    info["player"] = player
    info["dealer"] = dealer
    info["last_seen"] = datetime.now(MOSCOW_TZ).isoformat()

    print(
        f"🎮 ID={gid} | STATE={state} | P1={len(player)} | P2={len(dealer)}"
        + (" | финальная проверка" if final_attempt else ""),
        flush=True
    )
    return raw, state


def build_game_from_cached_cards(gid, info):
    """Создаёт запись игры из последнего финального кэша P1/P2 (STATE=4)."""
    player = info.get("player", []) or []
    dealer = info.get("dealer", []) or []
    if not player and not dealer:
        return None

    now = datetime.now(MOSCOW_TZ)
    all_cards = []
    sequence = []
    pos = 1

    # Сохраняем порядок в привычном для истории формате.
    for i in range(max(len(player), len(dealer))):
        if i < len(player):
            card = player[i]
            all_cards.append(card)
            sequence.append({"position": pos, "who": "P", "rank": card["rank"], "suit": card["suit"]})
            pos += 1
        if i < len(dealer):
            card = dealer[i]
            all_cards.append(card)
            sequence.append({"position": pos, "who": "D", "rank": card["rank"], "suit": card["suit"]})
            pos += 1

    game_number = info.get("game_number")
    try:
        game_number = int(game_number) if game_number is not None else get_game_number()
    except (TypeError, ValueError):
        game_number = get_game_number()

    return {
        "game_id": str(gid),
        "timestamp": now.isoformat(),
        "timestamp_msk": now.strftime("%H:%M:%S.%f")[:-3],
        "state": str(info.get("last_state", "4")),
        "game_number": game_number,
        "player_cards": player,
        "dealer_cards": dealer,
        "player_suits": [c["suit"] for c in player],
        "player_ranks": [c["rank"] for c in player],
        "dealer_suits": [c["suit"] for c in dealer],
        "dealer_ranks": [c["rank"] for c in dealer],
        "all_suits": [c["suit"] for c in all_cards],
        "all_ranks": [c["rank"] for c in all_cards],
        "sequence": sequence,
        "total_cards": len(all_cards),
        "first_player_card": player[0] if player else None,
        "id_last_digit": str(gid)[-1],
        "id_last_two": str(gid)[-2:] if len(str(gid)) >= 2 else ""
    }


def save_finished_game(gid, raw=None, active_game=None, from_cache=False):
    """Сохраняет игру либо по STATE=5, либо из финального кэша STATE=4."""
    if game_exists(gid):
        tracked_games.pop(str(gid), None)
        return False

    info = tracked_games.get(str(gid), {})

    if from_cache:
        parsed = build_game_from_cached_cards(gid, info)
    else:
        parsed = parse_game_data(gid, raw)

    if not parsed:
        return False

    if active_game:
        game_number = active_game.get("gameNumber", active_game.get("number"))
        try:
            parsed["game_number"] = int(game_number) if game_number is not None else parsed.get("game_number", get_game_number())
        except (TypeError, ValueError):
            pass

    ok = add_or_update_game(parsed)
    if ok:
        tracked_games.pop(str(gid), None)
    return ok


def process_game(active_game):
    """Отслеживает игру. STATE=4 фиксирует финальный P1/P2, STATE=5 сохраняет сразу."""
    gid = str(active_game.get("id", ""))
    if not gid or game_exists(gid):
        return

    result = inspect_game_state(gid, active_game=active_game)
    if not result:
        return

    raw, state = result
    if str(state) == "5":
        print(f"🏁 ID={gid} завершена (STATE=5)", flush=True)
        save_finished_game(gid, raw, active_game)
    elif str(state) == "4":
        print(f"📌 ID={gid} STATE=4 — финальные P1/P2 зафиксированы, ждём исчезновения", flush=True)


def finalize_disappeared_games(active_ids):
    """При исчезновении игры сохраняет кэш STATE=4. STATE=5 остаётся мгновенным сохранением."""
    for gid in list(tracked_games.keys()):
        if gid in active_ids or game_exists(gid):
            continue

        info = tracked_games.get(gid, {})
        last_state = str(info.get("last_state", ""))

        # По фактическому поведению API STATE=5 может не появиться в live feed.
        # Если последним увиденным состоянием был 4, карты уже финальные.
        if last_state == "4":
            print(
                f"🏁 ID={gid} исчезла из feed после STATE=4 — сохраняем финальный кэш "
                f"P1={len(info.get('player', []))} | P2={len(info.get('dealer', []))}",
                flush=True
            )
            if save_finished_game(gid, from_cache=True):
                print(f"✅ ID={gid} сохранена из кэша STATE=4", flush=True)
            continue

        attempts = int(info.get("final_attempts", 0))
        if attempts >= 3:
            print(f"⚠️ ID={gid} исчезла с последним STATE={last_state}; финального STATE=4/5 нет", flush=True)
            tracked_games.pop(gid, None)
            continue

        info["final_attempts"] = attempts + 1
        result = inspect_game_state(gid, final_attempt=True)
        if not result:
            continue

        raw, state = result
        if str(state) == "5":
            print(f"🏁 ID={gid} найдена завершённой после исчезновения (STATE=5)", flush=True)
            save_finished_game(gid, raw)
        elif str(state) == "4":
            # Следующий проход цикла увидит исчезнувшую STATE=4 и сохранит кэш.
            info["last_state"] = "4"


# =====================================================================
# CLEANUP
# =====================================================================

def cleanup_predictions():
    global predictions

    if len(predictions) > 1000:
        predictions = predictions[-1000:]
        atomic_save_json(PREDICTIONS_FILE, predictions)


# =====================================================================
# MAIN
# =====================================================================

def main():
    global history, predictions

    print("\n==================================================")
    print("🚀 БОТ — ПРОГНОЗЫ ПО ID ИЗ КАНАЛА + ИСТОРИЯ ИЗ API")
    print("==================================================")

    history = load_history()
    predictions = load_predictions()

    print(f"📚 История: {len(history)} игр")
    print(f"📊 Прогнозов: {len(predictions)}")
    print("📡 Источник прогнозов: ТВОЙ КАНАЛ СТАТИСТИКИ")
    print(f"📡 Источник истории: API | окно базы: {HISTORY_HOURS} часов")
    print("==================================================\n")

    offset = get_offset()
    print(f"📌 Telegram offset: {offset}")

    while True:
        start = time.time()

        try:
            # ✅ 1. ОПРАШИВАЕМ API ДЛЯ ИСТОРИИ
            games = get_active_games()
            if games:
                print(f"📡 API: {len(games)} игр")

            active_ids = set()
            for game in games:
                try:
                    gid = str(game.get("id", ""))
                    if gid:
                        active_ids.add(gid)
                    process_game(game)  # ТОЛЬКО ДЛЯ ИСТОРИИ!
                except Exception as e:
                    print(f"❌ Ошибка API: {e}", flush=True)

            # Если STATE=5 проскочил между опросами active feed — проверяем исчезнувшие игры.
            finalize_disappeared_games(active_ids)

            # Не держим трекер бесконечно при проблемах API.
            cutoff_tracker = datetime.now(MOSCOW_TZ) - timedelta(minutes=15)
            for gid in list(tracked_games.keys()):
                try:
                    seen = datetime.fromisoformat(tracked_games[gid].get("last_seen", tracked_games[gid]["first_seen"]))
                    if seen.tzinfo is None:
                        seen = MOSCOW_TZ.localize(seen)
                    if seen < cutoff_tracker:
                        tracked_games.pop(gid, None)
                except Exception:
                    pass

            # ✅ 2. ЧИТАЕМ КАНАЛ ДЛЯ ПРОГНОЗОВ
            offset = process_telegram_updates(offset)

            # ✅ 3. ПРОВЕРЯЕМ ПРОГНОЗЫ
            check_predictions()

            # ✅ 4. ЧИСТИМ СТАРЫЕ ПРОГНОЗЫ И ИСТОРИЮ > 48 ЧАСОВ
            cleanup_predictions()
            cleanup_history_by_time()

            elapsed = time.time() - start
            time.sleep(max(0.1, POLL_INTERVAL - elapsed))

        except KeyboardInterrupt:
            print("\n🛑 Бот остановлен")
            break

        except Exception as e:
            print(f"❌ Критическая ошибка: {e}")
            time.sleep(3)


# =====================================================================
# START
# =====================================================================

if __name__ == "__main__":
    main()