import os
import sys
import requests
import json
import re
import time
from pathlib import Path
from datetime import datetime, timedelta
import pytz

print("=" * 70)
print("RANK BOT - ONLY RANKS, NO SUITS")
print("=" * 70)

# ---------- ENV ----------
BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("BOT_TOKEN_PROGNOZ")
CHANNEL_STATS = os.getenv("CHANNEL_STATS")
CHANNEL_PROGNOZ = os.getenv("CHANNEL_PROGNOZ")
LIVE_COOKIE = os.getenv("LIVE_COOKIE", "")

if not BOT_TOKEN or not CHANNEL_STATS or not CHANNEL_PROGNOZ:
    print("ERROR: missing env vars")
    sys.exit(1)

# ---------- SETTINGS ----------
MOSCOW_TZ = pytz.timezone("Europe/Moscow")
BASE_URL = "https://1xlite-36553.pro"
OFFSET = 1
DOGON_GAMES = 4
MIN_CONF = 28.0
MAX_TRAIN = 3000

STATE_DIR = Path(".")
STATE_FILE = STATE_DIR / "rank_state.json"
OFFSET_FILE = STATE_DIR / "rank_offset.txt"

HEADERS = {"User-Agent": "Mozilla/5.0"}
if LIVE_COOKIE:
    HEADERS["Cookie"] = LIVE_COOKIE

# ---------- RANK TABLE (NO SUITS) ----------
RANK_TABLE = {
    "93-95":  {"10":18,"J":16,"Q":15,"K":14,"A":13,"9":12,"8":12,"7":0,"6":0},
    "95-97":  {"J":18,"Q":16,"K":15,"A":14,"10":13,"9":12,"8":12,"7":0,"6":0},
    "97-99":  {"K":18,"A":17,"Q":16,"J":15,"10":14,"9":10,"8":10,"7":0,"6":0},
    "99-101": {"A":19,"K":18,"Q":16,"J":15,"10":14,"9":10,"8":8,"7":0,"6":0},
    "101-103":{"Q":18,"K":17,"A":16,"J":15,"10":14,"9":10,"8":10,"7":0,"6":0},
    "103-105":{"K":19,"Q":18,"A":17,"J":16,"10":15,"9":8,"8":7,"7":0,"6":0},
    "105+":   {"A":20,"K":19,"Q":18,"J":17,"10":16,"9":5,"8":5,"7":0,"6":0},
}

# ---------- STATE ----------
def default_state():
    return {
        "predictions": [],
        "total_games": 0,
        "total_predictions": 0,
        "wins": 0,
        "losses": 0,
        "rank_table": RANK_TABLE,
        "learning_active": True,
    }

def load_json(path, default):
    if not path.exists():
        return default
    try:
        with open(path, "r") as f:
            data = json.load(f)
            for k in default:
                if k not in data:
                    data[k] = default[k]
            return data
    except:
        return default

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

state = load_json(STATE_FILE, default_state())
all_messages = []
if "rank_table" in state:
    RANK_TABLE = state["rank_table"]

# ---------- HELPERS ----------
def get_range(lat):
    if lat < 95: return "93-95"
    if lat < 97: return "95-97"
    if lat < 99: return "97-99"
    if lat < 101: return "99-101"
    if lat < 103: return "101-103"
    if lat < 105: return "103-105"
    return "105+"

def learn_from_error(lat, pred, actual):
    if state["total_games"] >= MAX_TRAIN:
        state["learning_active"] = False
        save_json(STATE_FILE, state)
        return
    rng = get_range(lat)
    if rng not in state["rank_table"]:
        return
    tbl = state["rank_table"][rng]
    if pred in tbl:
        tbl[pred] = max(0, tbl[pred] - 1.0)
    if actual in tbl:
        tbl[actual] = min(100, tbl[actual] + 0.5)
    total = sum(tbl.values())
    if total > 0:
        for k in tbl:
            tbl[k] = round(tbl[k] / total * 100, 1)
    state["total_games"] += 1
    save_json(STATE_FILE, state)
    print(f"learn: {pred}->{actual} ({rng})")

# ---------- TELEGRAM ----------
def send_msg(text):
    try:
        r = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                          json={"chat_id": CHANNEL_PROGNOZ, "text": text, "parse_mode": "HTML"}, timeout=10)
        if r.status_code == 200:
            return r.json()["result"]["message_id"]
    except:
        pass
    return None

def edit_msg(mid, text):
    try:
        r = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText",
                          json={"chat_id": CHANNEL_PROGNOZ, "message_id": mid, "text": text, "parse_mode": "HTML"}, timeout=10)
        return r.status_code == 200
    except:
        return False

def get_updates(offset):
    try:
        r = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
                         params={"offset": offset, "timeout": 30}, timeout=35)
        return r.json() if r.status_code == 200 else {}
    except:
        return {}

# ---------- PARSING (RANKS ONLY) ----------
def parse_game(text):
    try:
        m = re.search(r"#N(\d+)", text)
        if not m:
            return None
        num = int(m.group(1))
        sep = None
        if "◀️" in text: sep = "◀️"
        elif "▶️" in text: sep = "▶️"
        elif "-" in text: sep = "-"
        elif "—" in text: sep = "—"
        if not sep:
            return None
        parts = text.split(sep, 1)
        if len(parts) < 2:
            return None
        def parse_cards(part):
            m2 = re.search(r"\(([^)]*)\)", part)
            if not m2:
                return []
            s = m2.group(1)
            cards = []
            i = 0
            while i < len(s):
                if s[i].isspace():
                    i += 1
                    continue
                if s.startswith("10", i):
                    rank = "10"
                    i += 2
                elif s[i] in "AKQJ":
                    rank = s[i]
                    i += 1
                elif s[i].isdigit():
                    rank = s[i]
                    i += 1
                else:
                    i += 1
                    continue
                # skip suit
                i += 1
                if rank:
                    cards.append({"rank": rank})
            return cards
        return {
            "number": num,
            "player_cards": parse_cards(parts[0]),
            "dealer_cards": parse_cards(parts[1]),
            "text": text
        }
    except:
        return None

def is_finished(text):
    return "✅" in text or "🔰" in text

# ---------- API ----------
def get_active():
    url = f"{BASE_URL}/service-api/main-live-feed/v3/games1x2?cfView=3&count=40&fcountry=1&gr=415&grMode=4&lng=ru&ref=7&selectedMs=1.146.2092323,2.146.2092323,10.146.2092323"
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.status_code != 200:
            return []
        data = r.json()
        games = data.get("Value", []) if isinstance(data, dict) else data
        return [g for g in games if g.get("liga", {}).get("id") == 2092323 and g.get("id")]
    except:
        return []

def get_game_data(gid):
    url = f"{BASE_URL}/service-api/LiveFeed/GetGameZip?id={gid}&isSubGames=true&GroupEvents=true&countevents=250&grMode=4&partner=7&topGroups=&country=190&marketType=1&isNewBuilder=true"
    try:
        t0 = time.perf_counter()
        r = requests.get(url, headers=HEADERS, timeout=5)
        lat = (time.perf_counter() - t0) * 1000
        if r.status_code == 200:
            return r.json(), lat
    except:
        pass
    return None, None

def get_latency():
    games = get_active()
    if not games:
        return None
    for g in games:
        _, lat = get_game_data(str(g["id"]))
        if lat is not None:
            return lat
    return None

def get_game_number():
    now = datetime.now(MOSCOW_TZ)
    start = now.replace(hour=3, minute=0, second=0, microsecond=0)
    if now < start:
        start -= timedelta(days=1)
    diff = (now - start).total_seconds() / 60
    return int(diff) // 2 % 720 + 1

# ---------- PREDICTION ----------
def predict_rank(lat):
    rng = get_range(lat)
    if rng not in RANK_TABLE:
        return None, 0.0
    probs = RANK_TABLE[rng]
    if not probs:
        return None, 0.0
    best = max(probs.items(), key=lambda x: x[1])
    return best[0], best[1]

# ---------- GAME STORAGE ----------
games = {}

def add_game(text):
    g = parse_game(text)
    if g:
        games[g["number"]] = g
    return g

def find_game(n):
    return games.get(n)

# ---------- CHECK RESULTS ----------
def check_results():
    global all_messages
    for entry in state.get("predictions", []):
        if entry["status"] != "pending":
            continue
        target = entry["target"]
        pred = entry["selected_prediction"]
        mid = entry["message_id"]
        orig = entry["message_text"]
        lat = entry.get("latency", 100)
        if not pred or not mid:
            continue
        for i in range(DOGON_GAMES):
            gnum = target + i
            found = None
            for msg in all_messages:
                if f"#N{gnum}" in msg and is_finished(msg):
                    found = msg
                    break
            if not found:
                print(f"wait #N{gnum} for rank {pred}")
                continue
            gdata = parse_game(found)
            if not gdata:
                continue
            actual = None
            for c in gdata["player_cards"]:
                if c["rank"] == pred:
                    actual = pred
                    break
            if not actual and gdata["player_cards"]:
                actual = gdata["player_cards"][0]["rank"]
            if actual == pred:
                print(f"RANK {pred} FOUND in #N{gnum}!")
                entry["status"] = "win"
                entry["result_game"] = gnum
                entry["dogon"] = i
                state["wins"] += 1
                state["total_predictions"] += 1
                suffix = f"\n\n✅ ЗАШЛО в #N{gnum}" if i==0 else f"\n\n✅ ЗАШЛО на догоне {i} в #N{gnum}"
                edit_msg(mid, orig + suffix)
                entry["message_text"] = orig + suffix
                save_json(STATE_FILE, state)
                return
        print(f"RANK {pred} NOT FOUND in {DOGON_GAMES} games")
        entry["status"] = "lose"
        state["losses"] += 1
        state["total_predictions"] += 1
        if state["learning_active"]:
            learn_from_error(lat, pred, actual)
        suffix = f"\n\n❌ НЕ ЗАШЛО (проверено {DOGON_GAMES} игр)"
        edit_msg(mid, orig + suffix)
        entry["message_text"] = orig + suffix
        save_json(STATE_FILE, state)

# ---------- SCHEDULER ----------
def schedule_for_game(num):
    target = num + OFFSET
    for e in state["predictions"]:
        if e["target"] == target and e["status"] in ("scheduled","pending"):
            return
    state["predictions"].append({
        "source": target-1,
        "target": target,
        "selected_prediction": None,
        "latency": None,
        "confidence": 0.0,
        "status": "scheduled",
        "message_id": None,
        "message_text": "",
    })
    save_json(STATE_FILE, state)
    print(f"scheduled #{num} -> #{target} (+{OFFSET})")

def process_scheduled():
    for entry in state["predictions"]:
        if entry["status"] != "scheduled":
            continue
        target = entry["target"]
        src = find_game(target-1)
        if not src:
            continue
        lat = get_latency()
        if lat is None:
            print("no latency")
            continue
        rank, conf = predict_rank(lat)
        if rank is None or conf < MIN_CONF:
            print(f"{conf:.1f}% < {MIN_CONF}%")
            entry