import os
import sys
import requests
import json
import re
import time
from datetime import datetime, timedelta
import pytz

print("START", flush=True)

# ========== ENV ==========
TOKEN = os.getenv("BOT_TOKEN") or os.getenv("BOT_TOKEN_PROGNOZ")
CH_STATS = os.getenv("CHANNEL_STATS")
CH_PROG = os.getenv("CHANNEL_PROGNOZ")
COOKIE = os.getenv("LIVE_COOKIE", "")

if not TOKEN or not CH_STATS or not CH_PROG:
    print("ENV ERROR", flush=True)
    sys.exit(1)

print("ENV OK", flush=True)

# ========== SETTINGS ==========
MOSCOW_TZ = pytz.timezone("Europe/Moscow")
BASE_URL = "https://1xlite-36553.pro"
OFFSET = 1
DOGON = 4
MIN_CONF = 28.0
MAX_TRAIN = 3000

HEADERS = {"User-Agent": "Mozilla/5.0"}
if COOKIE:
    HEADERS["Cookie"] = COOKIE

# ========== RANK TABLE ==========
RANK_TABLE = {
    "93-95": {"10":18,"J":16,"Q":15,"K":14,"A":13,"9":12,"8":12,"7":0,"6":0},
    "95-97": {"J":18,"Q":16,"K":15,"A":14,"10":13,"9":12,"8":12,"7":0,"6":0},
    "97-99": {"K":18,"A":17,"Q":16,"J":15,"10":14,"9":10,"8":10,"7":0,"6":0},
    "99-101": {"A":19,"K":18,"Q":16,"J":15,"10":14,"9":10,"8":8,"7":0,"6":0},
    "101-103": {"Q":18,"K":17,"A":16,"J":15,"10":14,"9":10,"8":10,"7":0,"6":0},
    "103-105": {"K":19,"Q":18,"A":17,"J":16,"10":15,"9":8,"8":7,"7":0,"6":0},
    "105+": {"A":20,"K":19,"Q":18,"J":17,"10":16,"9":5,"8":5,"7":0,"6":0},
}

print("SETUP OK", flush=True)

# ========== STATE ==========
STATE_FILE = "rank_state.json"
OFFSET_FILE = "rank_offset.txt"

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

def load_state():
    try:
        with open(STATE_FILE, "r") as f:
            data = json.load(f)
            for k in default_state():
                if k not in data:
                    data[k] = default_state()[k]
            return data
    except:
        return default_state()

def save_state(data):
    with open(STATE_FILE, "w") as f:
        json.dump(data, f, indent=2)

state = load_state()
all_messages = []

# ========== HELPERS ==========
def get_range(lat):
    if lat < 95: return "93-95"
    if lat < 97: return "95-97"
    if lat < 99: return "97-99"
    if lat < 101: return "99-101"
    if lat < 103: return "101-103"
    if lat < 105: return "103-105"
    return "105+"

def learn_error(lat, pred, actual):
    if state["total_games"] >= MAX_TRAIN:
        state["learning_active"] = False
        save_state(state)
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
    save_state(state)
    print(f"learn: {pred}->{actual} ({rng})")

# ========== TELEGRAM ==========
def send(txt):
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": CH_PROG, "text": txt, "parse_mode": "HTML"},
            timeout=10
        )
        if r.status_code == 200:
            return r.json()["result"]["message_id"]
    except:
        pass
    return None

def edit(mid, txt):
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TOKEN}/editMessageText",
            json={"chat_id": CH_PROG, "message_id": mid, "text": txt, "parse_mode": "HTML"},
            timeout=10
        )
        return r.status_code == 200
    except:
        return False

def get_updates(offset):
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{TOKEN}/getUpdates",
            params={"offset": offset, "timeout": 30},
            timeout=35
        )
        return r.json() if r.status_code == 200 else {}
    except:
        return {}

print("TELEGRAM OK", flush=True)

# ========== PARSING ==========
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
                i += 1  # skip suit
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

# ========== API ==========
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

# ========== PREDICTION ==========
def predict_rank(lat):
    rng = get_range(lat)
    if rng not in RANK_TABLE:
        return None, 0.0
    probs = RANK_TABLE[rng]
    if not probs:
        return None, 0.0
    best = max(probs.items(), key=lambda x: x[1])
    return best[0], best[1]

# ========== STORAGE ==========
games = {}

def add_game(text):
    g = parse_game(text)
    if g:
        games[g["number"]] = g
    return g

def find_game(n):
    return games.get(n)

# ========== CHECK RESULTS ==========
def check_results():
    global all_messages
    for entry in state["predictions"]:
        if entry["status"] != "pending":
            continue
        target = entry["target"]
        pred = entry["selected_prediction"]
        mid = entry["message_id"]
        orig = entry["message_text"]
        lat = entry.get("latency", 100)
        if not pred or not mid:
            continue
        for i in range(DOGON):
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
                edit(mid, orig + suffix)
                entry["message_text"] = orig + suffix
                save_state(state)
                return
        print(f"RANK {pred} NOT FOUND in {DOGON} games")
        entry["status"] = "lose"
        state["losses"] += 1
        state["total_predictions"] += 1
        if state["learning_active"]:
            learn_error(lat, pred, actual)
        suffix = f"\n\n❌ НЕ ЗАШЛО (проверено {DOGON} игр)"
        edit(mid, orig + suffix)
        entry["message_text"] = orig + suffix
        save_state(state)

# ========== SCHEDULER ==========
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
    save_state(state)
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
            entry["status"] = "skipped"
            entry["confidence"] = conf
            entry["latency"] = lat
            save_state(state)
            continue
        entry["selected_prediction"] = rank
        entry["latency"] = lat
        entry["confidence"] = conf
        entry["status"] = "pending"
        msg = f"🔮 RANK BOT\n🃏 Rank: {rank}\n🎯 Confidence: {conf:.1f}%\n🎯 Target: #N{target}\n📈 Dogon: {DOGON-1} games\n"
        p = src.get("player_cards", [])
        if p:
            msg += "📌 " + " ".join([c["rank"] for c in p[:3]]) + "\n"
        msg += "⏰ " + datetime.now(MOSCOW_TZ).strftime("%H:%M:%S")
        mid = send(msg)
        if mid:
            entry["message_id"] = mid
            entry["message_text"] = msg
            save_state(state)
            print(f"PREDICTED #{target} -> {rank} ({conf:.1f}%)")
        else:
            entry["status"] = "scheduled"
            save_state(state)

# ========== STATS ==========
def stats_text():
    total = state["total_predictions"]
    wins = state["wins"]
    losses = state["losses"]
    acc = f"{wins/total*100:.1f}%" if total else "—"
    return f"📊 RANK BOT STATS\n⏰ {datetime.now(MOSCOW_TZ).strftime('%d.%m.%Y %H:%M:%S')}\n==============================\n📈 Total: {total}\n✅ Wins: {wins}\n❌ Losses: {losses}\n🎯 Accuracy: {acc}\n📚 Games learned: {state['total_games']}/{MAX_TRAIN}\n🎯 Min conf: {MIN_CONF}%"

# ========== OFFSET ==========
def load_offset():
    try:
        with open(OFFSET_FILE, "r") as f:
            return int(f.read().strip())
    except:
        return 0

def save_offset(o):
    with open(OFFSET_FILE, "w") as f:
        f.write(str(o))

# ========== MAIN ==========
def main():
    global all_messages
    print("BOT STARTED", flush=True)
    send("✅ BOT STARTED")
    offset = load_offset()
    last_stats = time.time()
    while True:
        try:
            now = time.time()
            check_results()
            process_scheduled()
            if now - last_stats > 3600:
                send(stats_text())
                last_stats = now
            updates = get_updates(offset)
            for upd in updates.get("result", []):
                if "update_id" in upd:
                    offset = upd["update_id"] + 1
                    save_offset(offset)
                post = upd.get("channel_post") or upd.get("edited_channel_post")
                if not post:
                    continue
                if str(post.get("chat", {}).get("id", "")) != str(CH_STATS):
                    continue
                text = post.get("text", "")
                if "#N" not in text:
                    continue
                if text not in all_messages:
                    all_messages.append(text)
                    if len(all_messages) > 500:
                        all_messages = all_messages[-500:]
                g = add_game(text)
                if not g:
                    continue
                n = g["number"]
                print(f"Game #{n} | {'finished' if is_finished(text) else 'not finished'}")
                if not is_finished(text):
                    schedule_for_game(n)
            check_results()
            process_scheduled()
            time.sleep(1)
        except Exception as e:
            print(f"ERROR: {e}")
            time.sleep(10)

if __name__ == "__main__":
    main()