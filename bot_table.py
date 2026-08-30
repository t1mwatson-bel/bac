import os
import sys
import requests
import json
import re
import time
from datetime import datetime, timedelta
import pytz

print("=" * 70, flush=True)
print("🃏 RANK BOT (ТОП-1, С ОЖИДАНИЕМ)", flush=True)
print("📌 Прогноз топ-1 ранга по задержке", flush=True)
print("🎯 Минимальная уверенность: 28%", flush=True)
print("🎯 OFFSET: +1", flush=True)
print("=" * 70, flush=True)

# =====================================================================
# ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ
# =====================================================================
BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("BOT_TOKEN_PROGNOZ")
CHANNEL_STATS = os.getenv("CHANNEL_STATS")
CHANNEL_PROGNOZ = os.getenv("CHANNEL_PROGNOZ")
LIVE_COOKIE = os.getenv("LIVE_COOKIE", "")

if not BOT_TOKEN or not CHANNEL_STATS or not CHANNEL_PROGNOZ:
    print("❌ Ошибка: BOT_TOKEN, CHANNEL_STATS или CHANNEL_PROGNOZ не заданы!", flush=True)
    sys.exit(1)

print("✅ Все переменные заданы!", flush=True)

# =====================================================================
# НАСТРОЙКИ
# =====================================================================
MOSCOW_TZ = pytz.timezone("Europe/Moscow")
BASE_URL = os.getenv("BASE_URL", "https://1xlite-36553.pro")
OFFSET = 1
DOGON_GAMES = 4
MIN_CONFIDENCE = 28.0

HEADERS = {"User-Agent": "Mozilla/5.0"}
if LIVE_COOKIE:
    HEADERS["Cookie"] = LIVE_COOKIE

# =====================================================================
# ТАБЛИЦА РАНГОВ
# =====================================================================
RANK_TABLE = {
    (93, 95): {"10": 28.3, "J": 24.5, "Q": 23.5, "K": 23.7},
    (95, 97): {"J": 29.1, "Q": 28.7, "K": 21.5, "10": 20.7},
    (97, 99): {"K": 26.7, "10": 25.9, "Q": 24.5, "J": 22.9},
    (99, 101): {"Q": 27.4, "K": 25.3, "J": 24.2, "10": 23.1},
    (101, 103): {"10": 26.5, "J": 25.1, "Q": 24.8, "K": 23.6},
    (103, 105): {"Q": 27.8, "K": 24.6, "10": 24.2, "J": 23.4},
    (105, 200): {"J": 27.6, "10": 25.9, "Q": 24.3, "K": 22.2},
}

# =====================================================================
# СОСТОЯНИЕ
# =====================================================================
STATE_FILE = "rank_state.json"
OFFSET_FILE = "rank_offset.txt"

state = {
    "predictions": [],
    "total": 0,
    "wins": 0,
    "losses": 0,
}
all_messages = []

def save_state():
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

def load_state():
    try:
        with open(STATE_FILE, "r") as f:
            data = json.load(f)
            for k in state:
                if k not in data:
                    data[k] = state[k]
            return data
    except:
        return state

state = load_state()

# =====================================================================
# ТЕЛЕГРАМ
# =====================================================================
def send(msg):
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHANNEL_PROGNOZ, "text": msg, "parse_mode": "HTML"},
            timeout=10
        )
        return r.json()["result"]["message_id"] if r.status_code == 200 else None
    except:
        return None

def edit(mid, msg):
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText",
            json={"chat_id": CHANNEL_PROGNOZ, "message_id": mid, "text": msg, "parse_mode": "HTML"},
            timeout=10
        )
        return r.status_code == 200
    except:
        return False

def get_updates(offset):
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
            params={"offset": offset, "timeout": 30},
            timeout=35
        )
        return r.json() if r.status_code == 200 else {}
    except:
        return {}

# =====================================================================
# ПАРСИНГ
# =====================================================================
def parse_game(text):
    try:
        m = re.search(r"#N(\d+)", text)
        if not m:
            return None
        num = int(m.group(1))
        sep = "◀️" if "◀️" in text else "▶️" if "▶️" in text else "-" if "-" in text else "—" if "—" in text else None
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
                i += 1  # пропускаем масть
                if rank:
                    cards.append({"rank": rank})
            return cards

        return {
            "number": num,
            "player": parse_cards(parts[0]),
            "dealer": parse_cards(parts[1]),
            "text": text,
        }
    except:
        return None

def is_finished(text):
    return "✅" in text or "🔰" in text

# =====================================================================
# API
# =====================================================================
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
        return None, None

def get_latency():
    for g in get_active():
        _, lat = get_game_data(str(g["id"]))
        if lat is not None:
            return lat
    return None

def get_game_number():
    now = datetime.now(MOSCOW_TZ)
    start = now.replace(hour=3, minute=0, second=0, microsecond=0)
    if now < start:
        start -= timedelta(days=1)
    return int((now - start).total_seconds() / 60) // 2 % 720 + 1

# =====================================================================
# ПРОГНОЗ (ТОП-1 РАНГ)
# =====================================================================
def predict_rank(lat):
    for (low, high), probs in RANK_TABLE.items():
        if low <= lat < high:
            best = max(probs.items(), key=lambda x: x[1])
            return best[0], best[1] / 100.0
    return None, 0.0

# =====================================================================
# ХРАНИЛИЩЕ
# =====================================================================
games = {}

def add_game(text):
    g = parse_game(text)
    if g:
        games[g["number"]] = g
    return g

def find_game(n):
    return games.get(n)

# =====================================================================
# ПРОВЕРКА РЕЗУЛЬТАТА (ТОЛЬКО КОГДА ИГРА ЗАВЕРШЕНА)
# =====================================================================
def check_results():
    for entry in state["predictions"]:
        if entry["status"] != "pending":
            continue
        target = entry["target"]
        rank = entry["rank"]
        mid = entry["mid"]
        orig = entry["text"]
        if not rank or not mid:
            continue
        for i in range(DOGON_GAMES):
            gnum = target + i
            found = None
            for m in all_messages:
                if f"#N{gnum}" in m and is_finished(m):
                    found = m
                    break
            if not found:
                print(f"⏳ Ждём завершения #N{gnum} для проверки ранга {rank}")
                continue
            data = parse_game(found)
            if not data:
                continue
            # Проверяем у игрока
            rank_found = False
            for card in data["player"]:
                if card["rank"] == rank:
                    rank_found = True
                    break
            if rank_found:
                print(f"🎯 РАНГ {rank} НАЙДЕН у игрока в #N{gnum}!")
                entry["status"] = "win"
                state["wins"] += 1
                state["total"] += 1
                suffix = f"\n\n✅ ЗАШЛО в #N{gnum}" if i == 0 else f"\n\n✅ ЗАШЛО на догоне {i} в #N{gnum}"
                edit(mid, orig + suffix)
                save_state()
                return
        print(f"❌ Ранг {rank} НЕ НАЙДЕН у игрока за {DOGON_GAMES} игр")
        entry["status"] = "lose"
        state["losses"] += 1
        state["total"] += 1
        suffix = f"\n\n❌ НЕ ЗАШЛО (проверено {DOGON_GAMES} игр)"
        edit(mid, orig + suffix)
        save_state()

# =====================================================================
# ПЛАНИРОВЩИК (ОЖИДАНИЕ ДО ПОСЛЕДНЕЙ ИГРЫ)
# =====================================================================
def schedule(n):
    target = n + OFFSET
    for e in state["predictions"]:
        if e["target"] == target and e["status"] in ("scheduled", "pending"):
            return
    state["predictions"].append({
        "source": target - 1,
        "target": target,
        "rank": None,
        "confidence": 0.0,
        "status": "scheduled",
        "mid": None,
        "text": "",
    })
    save_state()
    print(f"📅 Запланировано: #{n} → #{target} (+{OFFSET})")

def process_scheduled():
    current_num = get_game_number()
    for entry in state["predictions"]:
        if entry["status"] != "scheduled":
            continue
        target = entry["target"]
        games_left = target - current_num
        # Ждём, пока до целевой игры не останется 1 игра
        if games_left != 1:
            continue
        print(f"🔥 До цели #{target} осталась 1 игра! Делаю прогноз...", flush=True)
        src = find_game(target - 1)
        if not src:
            continue
        lat = get_latency()
        if lat is None:
            print("⏳ Нет задержки")
            continue
        rank, conf = predict_rank(lat)
        if rank is None or conf < MIN_CONFIDENCE / 100.0:
            print(f"⏭️ {conf*100:.1f}% < {MIN_CONFIDENCE}%")
            entry["status"] = "skipped"
            save_state()
            continue
        # Проверяем, что ранг не занят P1, D1, P2, D2
        player_cards = src.get("player", [])
        dealer_cards = src.get("dealer", [])
        check_cards = []
        if len(player_cards) > 0:
            check_cards.append(player_cards[0]["rank"])
        if len(dealer_cards) > 0:
            check_cards.append(dealer_cards[0]["rank"])
        if len(player_cards) > 1:
            check_cards.append(player_cards[1]["rank"])
        if len(dealer_cards) > 1:
            check_cards.append(dealer_cards[1]["rank"])
        if rank in check_cards:
            print(f"⏭️ Ранг {rank} уже есть среди P1, D1, P2, D2 → пропускаю прогноз для #{target}")
            entry["status"] = "skipped"
            save_state()
            continue
        entry["rank"] = rank
        entry["confidence"] = conf
        entry["status"] = "pending"
        msg = f"🔮 RANK BOT\n\n"
        msg += f"🎯 Целевая игра: #N{target} (+{OFFSET})\n"
        msg += f"🃏 Ранг: {rank}\n"
        msg += f"🎯 Уверенность: {conf*100:.1f}%\n"
        msg += f"📈 Догон: {DOGON_GAMES - 1} игр\n"
        msg += f"📍 Ищем: у игрока\n"
        msg += f"⏰ {datetime.now(MOSCOW_TZ).strftime('%H:%M:%S')}"
        # Добавляем последовательность, если есть
        seq_str = ""
        if len(player_cards) > 0:
            seq_str += f"P1:{player_cards[0]['rank']} "
        if len(dealer_cards) > 0:
            seq_str += f"D1:{dealer_cards[0]['rank']} "
        if len(player_cards) > 1:
            seq_str += f"P2:{player_cards[1]['rank']} "
        if len(dealer_cards) > 1:
            seq_str += f"D2:{dealer_cards[1]['rank']}"
        if seq_str:
            msg += f"\n📌 {seq_str}"
        mid = send(msg)
        if mid:
            entry["mid"] = mid
            entry["text"] = msg
            save_state()
            print(f"✅ ПРОГНОЗ ОТПРАВЛЕН: #{target} → {rank} ({conf*100:.1f}%)", flush=True)

# =====================================================================
# СТАТИСТИКА
# =====================================================================
def stats():
    t = state["total"]
    w = state["wins"]
    l = state["losses"]
    acc = f"{w/t*100:.1f}%" if t else "—"
    return f"📊 RANK BOT (ТОП-1)\n⏰ {datetime.now(MOSCOW_TZ).strftime('%d.%m.%Y %H:%M:%S')}\n==============================\n📈 Всего: {t}\n✅ Зашло: {w}\n❌ Не зашло: {l}\n🎯 Точность: {acc}"

# =====================================================================
# OFFSET
# =====================================================================
def load_offset():
    try:
        with open(OFFSET_FILE, "r") as f:
            return int(f.read().strip())
    except:
        return 0

def save_offset(o):
    with open(OFFSET_FILE, "w") as f:
        f.write(str(o))

# =====================================================================
# MAIN
# =====================================================================
def main():
    global all_messages
    send("🚀 RANK BOT STARTED")
    offset = load_offset()
    last_stats = time.time()
    print("🚀 БОТ ГОТОВ")
    while True:
        try:
            now = time.time()
            check_results()
            process_scheduled()
            if now - last_stats > 3600:
                send(stats())
                last_stats = now
            updates = get_updates(offset)
            for upd in updates.get("result", []):
                uid = upd.get("update_id")
                if uid is not None:
                    offset = uid + 1
                    save_offset(offset)
                post = upd.get("channel_post") or upd.get("edited_channel_post")
                if not post:
                    continue
                if str(post.get("chat", {}).get("id", "")) != str(CHANNEL_STATS):
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
                print(f"📥 Игра #{n} | {'завершена' if is_finished(text) else 'не завершена'}")
                if not is_finished(text):
                    schedule(n)
            check_results()
            process_scheduled()
            time.sleep(1)
        except Exception as e:
            print(f"❌ Ошибка: {e}")
            time.sleep(10)

if __name__ == "__main__":
    main()