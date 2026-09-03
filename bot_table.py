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

MAX_HISTORY_GAMES = 3000
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

# Минимум совпадений для метода

MIN_MS_MATCHES = 2
MIN_ID1_MATCHES = 3
MIN_ID2_MATCHES = 2
MIN_SEQUENCE_MATCHES = 2

# Минимальный итоговый процент лидера

MIN_FORECAST_PROBABILITY = 0.29

# Лидер должен быть сильнее второго

MIN_LEADER_GAP = 0.06

# Минимум независимых методов,

# которые дали данные

MIN_ACTIVE_METHODS = 2

# Максимум игр между прогнозами

PREDICTION_COOLDOWN_SECONDS = 20

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

last_update_id = 0
last_prediction_time = 0

games_cache = {}

seen_games = set()

# =====================================================================

# NORMALIZATION

# =====================================================================

def normalize_suit(v):

```
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
```

def normalize_rank(v):

```
if v is None or isinstance(v, bool):
    return None

if isinstance(v, int):
    return RANKS.get(v)

t = str(v).strip().upper().replace("А", "A")

if t in {
    "2", "3", "4", "5", "6",
    "7", "8", "9", "10",
    "J", "Q", "K", "A"
}:
    return t

try:
    return RANKS.get(int(t))
except:
    return None
```

def card_to_text(card):

```
if not card:
    return ""

rank = normalize_rank(card.get("rank"))
suit = normalize_suit(card.get("suit"))

if not rank or not suit:
    return ""

return f"{rank}{suit}\ufe0f"
```

def card_dict_to_target(card):

```
text = card_to_text(card)

if text in TARGET_CARDS:
    return text

return None
```

# =====================================================================

# JSON

# =====================================================================

def load_json_file(filename, default):

```
try:

    if not os.path.exists(filename):
        return default

    with open(
        filename,
        "r",
        encoding="utf-8"
    ) as f:
        return json.load(f)

except Exception as e:

    print(
        f"⚠️ Ошибка чтения {filename}: {e}",
        flush=True
    )

    return default
```

def atomic_save_json(filename, data):

```
tmp = filename + ".tmp"

try:

    with open(
        tmp,
        "w",
        encoding="utf-8"
    ) as f:

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
    except:
        pass

    return False
```

# =====================================================================

# HISTORY

# =====================================================================

def load_history():

```
data = load_json_file(DATA_FILE, [])

if not isinstance(data, list):
    data = []

# Удаляем пустые / битые записи
clean = []

for g in data:

    if not isinstance(g, dict):
        continue

    if not g.get("game_id"):
        continue

    clean.append(g)

if len(clean) > MAX_HISTORY_GAMES:

    clean = clean[-MAX_HISTORY_GAMES:]

    atomic_save_json(
        DATA_FILE,
        clean
    )

return clean
```

def load_predictions():

```
data = load_json_file(
    PREDICTIONS_FILE,
    []
)

if not isinstance(data, list):
    return []

return data
```

def find_game_index(gid):

```
gid = str(gid)

for i, game in enumerate(history):

    if str(game.get("game_id")) == gid:
        return i

return -1
```

def game_exists(gid):

```
return find_game_index(gid) != -1
```

# =====================================================================

# DEEP PARSING

# =====================================================================

def deep_find_value(obj, keys):

```
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
```

def extract_card_from_dict(obj):

```
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
```

def classify_key(key):

```
key = str(key).strip().lower()

if (
    key in ["p", "pcards", "playercards"]
    or key.startswith("player")
    or "playercard" in key
):
    return "player"

if (
    key in ["d", "dcards", "dealercards"]
    or key.startswith("dealer")
    or "dealercard" in key
):
    return "dealer"

return None
```

def find_cards_recursive(
obj,
context=None,
player=None,
dealer=None
):

```
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
            "p", "p1", "p2",
            "p3", "p4", "p5",
            "p6", "p7", "p8",
            "p9"
        }:
            ctx = "player"

        if kl in {
            "d", "d1", "d2",
            "d3", "d4", "d5",
            "d6", "d7", "d8",
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
```

def clean_cards(cards):

```
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

    # Не допускаем дубль одной и той же ссылки
    if key in seen:
        continue

    seen.add(key)

    result.append({
        "rank": rank,
        "suit": f"{suit}\ufe0f"
    })

return result
```

# =====================================================================

# PARSE GAME

# =====================================================================

def parse_game_data(game_id, raw):

```
if not raw:
    return None

player, dealer = find_cards_recursive(raw)

player = clean_cards(player)
dealer = clean_cards(dealer)

if not player:
    return None

now = datetime.now(MOSCOW_TZ)

state = deep_find_value(
    raw,
    [
        "state",
        "STATE",
        "status",
        "STATUS"
    ]
)

if state is not None:
    state = str(state)

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

return {
    "game_id": str(game_id),

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

    "sequence": sequence,

    "total_cards": len(all_cards),

    "first_player_card":
        player[0] if player else None,

    "id_last_digit":
        str(game_id)[-1],

    "id_last_two":
        str(game_id)[-2:]
        if len(str(game_id)) >= 2
        else ""
}
```

# =====================================================================

# MERGE / SAVE GAME

# =====================================================================

def add_or_update_game(game):

```
global history

gid = str(game.get("game_id"))

if not gid:
    return False

idx = find_game_index(gid)

if idx != -1:

    old = history[idx]

    # Новая запись заменяет старую,
    # если карт стало больше
    old_cards = (
        len(old.get("player_cards", []))
        +
        len(old.get("dealer_cards", []))
    )

    new_cards = (
        len(game.get("player_cards", []))
        +
        len(game.get("dealer_cards", []))
    )

    if new_cards >= old_cards:

        history[idx] = game

        atomic_save_json(
            DATA_FILE,
            history
        )

        print(
            f"🔄 Игра обновлена | ID={gid} | карт={new_cards}",
            flush=True
        )

    return False

history.append(game)

if len(history) > MAX_HISTORY_GAMES:

    history = history[-MAX_HISTORY_GAMES:]

    print(
        f"♻️ История ограничена {MAX_HISTORY_GAMES}",
        flush=True
    )

atomic_save_json(
    DATA_FILE,
    history
)

print(
    f"💾 Новая игра | ID={gid} | "
    f"P1={card_to_text(game.get('first_player_card'))} | "
    f"Всего={len(history)}",
    flush=True
)

return True
```

# =====================================================================

# TARGET CARDS FROM RECORD

# =====================================================================

def get_target_cards_from_record(record):

```
result = []

all_cards = (
    record.get("player_cards", [])
    +
    record.get("dealer_cards", [])
)

for card in all_cards:

    target = card_dict_to_target(card)

    if target:
        result.append(target)

return result
```

# =====================================================================

# DISTRIBUTION HELPERS

# =====================================================================

def normalize_distribution(counter):

```
if not counter:
    return {}

total = sum(counter.values())

if total <= 0:
    return {}

return {
    card: count / total
    for card, count in counter.items()
}
```

def get_top_from_distribution(dist):

```
if not dist:
    return None, 0.0

sorted_items = sorted(
    dist.items(),
    key=lambda x: x[1],
    reverse=True
)

return sorted_items[0]
```

# =====================================================================

# METHOD 1 — MILLISECONDS

# =====================================================================

def method_milliseconds(timestamp_msk):

```
if not timestamp_msk:
    return {}

try:

    if "." not in timestamp_msk:
        return {}

    target_ms = int(
        timestamp_msk.split(".")[1]
    )

except:
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

    except:
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

dist = normalize_distribution(counter)

return {
    "name": "MS",
    "weight": WEIGHT_MS,
    "matches": matches,
    "distribution": dist
}
```

# =====================================================================

# METHOD 2 — LAST ID DIGIT

# =====================================================================

def method_id1(game_id):

```
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
```

# =====================================================================

# METHOD 3 — LAST 2 ID DIGITS

# =====================================================================

def method_id2(game_id):

```
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
```

# =====================================================================

# METHOD 4 — LOCAL FREQUENCY

# =====================================================================

def method_frequency():

```
counter = Counter()

# Последние 150 игр
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
```

# =====================================================================

# METHOD 5 — SEQUENCE PATTERN

# =====================================================================

def get_game_signature(game):

```
sequence = game.get("sequence", [])

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
```

def method_sequence():

```
if len(history) < 10:
    return {}

# Используем последние завершённые игры
recent = history[-5:]

if not recent:
    return {}

pattern_counter = Counter()

# Сигнатуры последних игр
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
    "distribution":
        normalize_distribution(counter)
}
```

# =====================================================================

# HYBRID ENGINE

# =====================================================================

def build_hybrid_prediction(
game_id,
timestamp_msk
):

```
methods = []

# Независимые методы
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

    method_sequence()

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
    weight = result["weight"]

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
            probability * weight
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
    for card, score in scores.items()
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
    -
    second_probability
)

# Считаем сколько методов поддержали лидера
supporters = []

for name, info in method_details.items():

    if info.get("top") == best_card:
        supporters.append(name)

return {
    "card": best_card,

    "probability": best_probability,

    "second_card": second_card,

    "second_probability":
        second_probability,

    "gap": gap,

    "active_methods": active,

    "supporters": supporters,

    "method_details":
        method_details,

    "ranking": ranking[:5]
}
```

# =====================================================================

# FILTER

# =====================================================================

def prediction_passes_filter(result):

```
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

# Минимальная вероятность
if probability < MIN_FORECAST_PROBABILITY:

    print(
        f"🚫 Лидер слабый: "
        f"{probability:.1%}",
        flush=True
    )

    return False

# Лидер недостаточно оторвался
if gap < MIN_LEADER_GAP:

    print(
        f"🚫 Нет преимущества: "
        f"gap={gap:.1%}",
        flush=True
    )

    return False

# Хотя бы один метод должен
# явно выбрать эту карту
if len(supporters) < 1:

    print(
        "🚫 Нет прямой поддержки лидера",
        flush=True
    )

    return False

return True
```

# =====================================================================

# GAME NUMBER

# =====================================================================

def get_game_number():

```
now = datetime.now(MOSCOW_TZ)

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
    \+ 1
)
```

def add_game_offset(number, offset):

```
return (
    (int(number) - 1 + int(offset))
    % 1440
    \+ 1
)
```

# =====================================================================

# TELEGRAM

# =====================================================================

def telegram_send(
text,
chat_id=None
):

```
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
```

def telegram_edit(
message_id,
text,
chat_id=None
):

```
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
        response.json().get("ok")
    )

except:
    return False
```

# =====================================================================

# MESSAGES

# =====================================================================

def make_prediction_message(entry):

```
result = entry["hybrid"]

text = (
    "🔮 <b>ТОЧНАЯ КАРТА — HYBRID</b>\n\n"
)

text += (
    f"🎯 Игра: "
    f"<b>#N{entry['target_number']}</b>\n"
)

text += (
    f"🃏 <b>{result['card']}</b> — "
    f"<b>{result['probability'] * 100:.1f}%</b>\n\n"
)

text += (
    f"🥈 {result.get('second_card', '—')} — "
    f"{result.get('second_probability', 0) * 100:.1f}%\n\n"
)

text += "🤖 <b>Сигналы:</b>\n"

details = result.get(
    "method_details",
    {}
)

names = {
    "MS": "⏱ Миллисекунды",
    "ID1": "🔢 ID ×1",
    "ID2": "🔢 ID ×2",
    "SEQ": "🧩 Последовательность",
    "FREQ": "📚 Частота"
}

for key in [
    "MS",
    "ID1",
    "ID2",
    "SEQ",
    "FREQ"
]:

    if key not in details:
        continue

    info = details[key]

    marker = (
        "✅"
        if info.get("top") == result["card"]
        else "▫️"
    )

    text += (
        f"{marker} {names[key]}: "
        f"<b>{info.get('top')}</b> "
        f"({info.get('probability', 0) * 100:.1f}%)\n"
    )

text += (
    f"\n📊 Методов: "
    f"{len(result.get('active_methods', []))}"
)

text += (
    f"\n🎯 Подтвердили: "
    f"{', '.join(result.get('supporters', []))}"
)

text += (
    f"\n📈 Догон: {DOGON_GAMES - 1} игр"
)

return text
```

def make_result_text(
entry,
status,
found_card=None,
result_game=None
):

```
original = entry.get(
    "original_text",
    ""
)

if status == "win":

    return (
        original
        +
        "\n\n════════════════════\n"
        "✅ <b>ЗАШЛО</b>\n"
        "════════════════════\n"
        f"🎯 Игра: #{result_game}\n"
        f"🃏 Выпала: <b>{found_card}</b>"
    )

return (
    original
    +
    "\n\n════════════════════\n"
    "❌ <b>НЕ ЗАШЛО</b>\n"
    "════════════════════"
)
```

# =====================================================================

# PREDICTIONS

# =====================================================================

def has_pending_prediction():

```
for entry in predictions:

    if entry.get("status") == "pending":
        return True

return False
```

def prediction_exists(target_game_id):

```
target_game_id = str(target_game_id)

for entry in predictions:

    if (
        str(
            entry.get(
                "target_game_id",
                ""
            )
        )
        == target_game_id
    ):
        return True

return False
```

def create_hybrid_prediction(
game_id,
game_number
):

```
global last_prediction_time

game_id = str(game_id)

if prediction_exists(game_id):
    return None

if has_pending_prediction():

    print(
        "⏳ Есть активный прогноз",
        flush=True
    )

    return None

now_ts = time.time()

if (
    now_ts - last_prediction_time
    <
    PREDICTION_COOLDOWN_SECONDS
):
    return None

now = datetime.now(MOSCOW_TZ)

timestamp_msk = (
    now.strftime("%H:%M:%S.%f")[:-3]
)

print(
    "\n"
    "══════════════════════════════════",
    flush=True
)

print(
    f"🧠 HYBRID АНАЛИЗ | ID={game_id}",
    flush=True
)

print(
    f"⏱ Timestamp={timestamp_msk}",
    flush=True
)

result = build_hybrid_prediction(
    game_id,
    timestamp_msk
)

if not result:

    print(
        "⏭️ Гибрид не дал результата",
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
    f"📏 Gap: {result['gap']:.1%}",
    flush=True
)

print(
    f"🤝 Поддержка: "
    f"{result['supporters']}",
    flush=True
)

if not prediction_passes_filter(result):

    print(
        "🚫 ПРОГНОЗ ОТМЕНЁН ФИЛЬТРОМ",
        flush=True
    )

    return None

entry = {

    "target_game_id": game_id,

    "target_number":
        game_number,

    "timestamp_msk":
        timestamp_msk,

    "hybrid": result,

    "predicted_card":
        result["card"],

    "status": "pending",

    "created_at":
        datetime.now(MOSCOW_TZ).isoformat(),

    "message_id": None,

    "original_text": "",

    "result_game": None,

    "found_card": None

}

predictions.append(entry)

atomic_save_json(
    PREDICTIONS_FILE,
    predictions
)

last_prediction_time = now_ts

print(
    f"🔮 ПРОГНОЗ СОЗДАН: "
    f"{result['card']}",
    flush=True
)

return entry
```

# =====================================================================

# PARSE CHANNEL RESULT

# =====================================================================

def parse_cards_from_message(text):

```
if not text:
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

pattern = (
    r"(10|[2-9AJQK])"
    r"([♠♣♦♥])"
)

cards = []

for rank, suit in re.findall(
    pattern,
    text
):

    rn = normalize_rank(rank)
    sn = normalize_suit(suit)

    if rn and sn:

        cards.append(
            f"{rn}{sn}\ufe0f"
        )

return {
    "game_number": game_number,
    "cards": cards
}
```

# =====================================================================

# CACHE RESULTS

# =====================================================================

def cache_result(
game_number,
text
):

```
games_cache[int(game_number)] = text

if len(games_cache) > 2000:

    keys = sorted(
        games_cache.keys()
    )

    for key in keys[:500]:

        games_cache.pop(
            key,
            None
        )
```

# =====================================================================

# CHECK RESULTS

# =====================================================================

def check_predictions():

```
changed = False

for entry in predictions:

    if entry.get("status") != "pending":
        continue

    target = entry.get(
        "target_number"
    )

    predicted = entry.get(
        "predicted_card"
    )

    if not target or not predicted:
        continue

    found = False
    found_card = None
    found_game = None

    available = 0

    for dogon in range(
        DOGON_GAMES
    ):

        game_num = add_game_offset(
            target,
            dogon
        )

        text = games_cache.get(
            game_num
        )

        if not text:
            continue

        available += 1

        parsed = parse_cards_from_message(
            text
        )

        if not parsed:
            continue

        actual_cards = parsed.get(
            "cards",
            []
        )

        print(
            f"🔎 Проверка #{game_num}: "
            f"{actual_cards}",
            flush=True
        )

        if predicted in actual_cards:

            found = True
            found_card = predicted
            found_game = game_num

            break

    if found:

        entry["status"] = "win"

        entry["result_game"] = found_game

        entry["found_card"] = found_card

        changed = True

        print(
            f"✅ ЗАШЛО! "
            f"{predicted} "
            f"на #{found_game}",
            flush=True
        )

        if entry.get("message_id"):

            telegram_edit(
                entry["message_id"],
                make_result_text(
                    entry,
                    "win",
                    found_card,
                    found_game
                )
            )

        continue

    if available >= DOGON_GAMES:

        entry["status"] = "lose"

        changed = True

        print(
            f"❌ НЕ ЗАШЛО: "
            f"{predicted}",
            flush=True
        )

        if entry.get("message_id"):

            telegram_edit(
                entry["message_id"],
                make_result_text(
                    entry,
                    "lose"
                )
            )

if changed:

    atomic_save_json(
        PREDICTIONS_FILE,
        predictions
    )
```

# =====================================================================

# TELEGRAM UPDATES

# =====================================================================

def get_offset():

```
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

except:
    pass

return 0
```

def save_offset(offset):

```
try:

    with open(
        OFFSET_FILE,
        "w"
    ) as f:

        f.write(
            str(offset)
        )

except:
    pass
```

def process_telegram_updates(
offset
):

```
try:

    params = {
        "offset": offset,
        "timeout": 3
    }

    response = SESSION.get(
        f"{TELEGRAM_API}/getUpdates",
        params=params,
        timeout=10
    )

    data = response.json()

    if not data.get("ok"):
        return offset

    for update in data.get(
        "result",
        []
    ):

        uid = update.get(
            "update_id"
        )

        if uid is not None:

            offset = uid + 1

            save_offset(offset)

        post = (
            update.get("channel_post")
            or
            update.get(
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

        if (
            not CHANNEL_STATS
            or
            chat_id
            !=
            str(CHANNEL_STATS)
        ):
            continue

        text = post.get(
            "text",
            ""
        )

        parsed = parse_cards_from_message(
            text
        )

        if not parsed:
            continue

        num = parsed[
            "game_number"
        ]

        cache_result(
            num,
            text
        )

        print(
            f"💾 Результат из канала "
            f"#{num}",
            flush=True
        )

except Exception as e:

    print(
        f"⚠️ Updates error: {e}",
        flush=True
    )

return offset
```

# =====================================================================

# API

# =====================================================================

def get_active_games():

```
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

        if (
            str(
                liga.get(
                    "id",
                    ""
                )
            )
            !=
            str(LEAGUE_ID)
        ):
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
```

def get_game_data(game_id):

```
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

except:
    pass

return None
```

# =====================================================================

# PROCESS GAME

# =====================================================================

def process_game(
active_game
):

```
gid = str(
    active_game.get(
        "id",
        ""
    )
)

if not gid:
    return

# Получаем данные игры
raw = get_game_data(gid)

parsed = None

if raw:

    parsed = parse_game_data(
        gid,
        raw
    )

# Новая игра
is_new = not game_exists(gid)

if is_new:

    print(
        "\n"
        "══════════════════════════════════",
        flush=True
    )

    print(
        f"🆕 НОВАЯ ИГРА | ID={gid}",
        flush=True
    )

    game_number = get_game_number()

    prediction = create_hybrid_prediction(
        gid,
        game_number
    )

    if prediction:

        message = make_prediction_message(
            prediction
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

# Сохраняем / обновляем игру
if parsed:

    add_or_update_game(
        parsed
    )
```

# =====================================================================

# CLEANUP

# =====================================================================

def cleanup_predictions():

```
global predictions

if len(predictions) > 1000:

    predictions = predictions[-1000:]

    atomic_save_json(
        PREDICTIONS_FILE,
        predictions
    )
```

# =====================================================================

# MAIN

# =====================================================================

def main():

```
global history
global predictions

print(
    "\n"
    "==================================================",
    flush=True
)

print(
    "🚀 OLD BOT — ТОЧНАЯ КАРТА HYBRID",
    flush=True
)

print(
    "==================================================",
    flush=True
)

print(
    f"📚 История: "
    f"{len(history)} игр",
    flush=True
)

print(
    "🧠 Методы:",
    flush=True
)

print(
    "   ⏱ MS — миллисекунды",
    flush=True
)

print(
    "   🔢 ID1 — последняя цифра",
    flush=True
)

print(
    "   🔢 ID2 — последние 2 цифры",
    flush=True
)

print(
    "   🧩 SEQ — последовательность",
    flush=True
)

print(
    "   📚 FREQ — частота",
    flush=True
)

print(
    f"🎯 Точных карт: "
    f"{len(TARGET_CARDS)}",
    flush=True
)

print(
    f"📈 Догон: "
    f"{DOGON_GAMES} игры",
    flush=True
)

print(
    f"📊 Мин. вероятность: "
    f"{MIN_FORECAST_PROBABILITY:.0%}",
    flush=True
)

print(
    f"📏 Мин. отрыв: "
    f"{MIN_LEADER_GAP:.0%}",
    flush=True
)

print(
    "==================================================\n",
    flush=True
)

offset = get_offset()

print(
    f"📌 Telegram offset: {offset}",
    flush=True
)

while True:

    start = time.time()

    try:

        games = get_active_games()

        if games:

            print(
                f"📡 API игр: "
                f"{len(games)}",
                flush=True
            )

        for game in games:

            try:

                process_game(game)

            except Exception as e:

                print(
                    f"❌ Ошибка игры: {e}",
                    flush=True
                )

        # Результаты из Telegram
        offset = process_telegram_updates(
            offset
        )

        # Проверка прогнозов
        check_predictions()

        cleanup_predictions()

        elapsed = (
            time.time()
            -
            start
        )

        time.sleep(
            max(
                0.1,
                POLL_INTERVAL
                -
                elapsed
            )
        )

    except KeyboardInterrupt:

        print(
            "\n🛑 Бот остановлен",
            flush=True
        )

        break

    except Exception as e:

        print(
            f"❌ Критическая ошибка: {e}",
            flush=True
        )

        time.sleep(3)
```

# =====================================================================

# START

# =====================================================================

if **name** == "**main**":

```
history = load_history()

predictions = load_predictions()

main()
```
