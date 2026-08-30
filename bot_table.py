import os
import sys
import requests
import json
import re
import time
from pathlib import Path
from datetime import datetime, timedelta
import pytz

print("=" * 70, flush=True)
print("🃏 RANK BOT (LATENCY + RANK)", flush=True)
print("📌 Прогноз ранга по задержке + уточнение по игроку", flush=True)
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
DOGON_GAMES = int(os.getenv("DOGON_GAMES", "4"))
MIN_CONFIDENCE = 28.0

STATE_DIR = Path(os.getenv("STATE_DIR", ".")).resolve()
STATE_DIR.mkdir(parents=True, exist_ok=True)

STATE_FILE = STATE_DIR / "rank_state.json"
OFFSET_FILE = STATE_DIR / "rank_offset.txt"

MAX_MESSAGES = 3000
MAX_STATE_PREDICTIONS = 3000

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": f"{BASE_URL}/ru/live/twentyone/2092323-21-classics",
}
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
# ФУНКЦИИ ДЛЯ РАБОТЫ С ФАЙЛАМИ
# =====================================================================
def default_state():
    return {
        "predictions": [],
        "training_samples": [],
        "total_predictions": 0,
        "wins": 0,
        "losses": 0,
    }

def load_json(path, default):
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            for key in default:
                if key not in data:
                    data[key] = default[key]
            return data
    except Exception as e:
        print(f"⚠️ Не удалось загрузить {path}: {e}", flush=True)
        return default

def save_json(path, data):
    tmp = Path(str(path) + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)

state = load_json(STATE_FILE, default_state())
all_messages = []

# =====================================================================
# ФУНКЦИИ ТЕЛЕГРАМ
# =====================================================================
def send_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(
            url,
            json={"chat_id": CHANNEL_PROGNOZ, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
        if r.status_code == 200:
            return r.json()["result"]["message_id"]
    except Exception as e:
        print(f"❌ sendMessage: {e}", flush=True)
    return None

def edit_message(message_id, text):
    if not message_id:
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText"
    try:
        r = requests.post(
            url,
            json={
                "chat_id": CHANNEL_PROGNOZ,
                "message_id": message_id,
                "text": text,
                "parse_mode": "HTML",
            },
            timeout=10,
        )
        return r.status_code == 200
    except Exception as e:
        print(f"❌ editMessageText: {e}", flush=True)
        return False

def telegram_get_updates(offset):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    try:
        r = requests.get(url, params={"offset": offset, "timeout": 30}, timeout=35)
        if r.status_code != 200:
            print(f"❌ Telegram getUpdates: {r.status_code} {r.text[:300]}", flush=True)
            return {}
        return r.json()
    except Exception as e:
        print(f"❌ getUpdates: {e}", flush=True)
        return {}

# =====================================================================
# ПАРСИНГ
# =====================================================================
def parse_game_from_text(text):
    try:
        m = re.search(r"#N(\d+)", text)
        if not m:
            return None
        number = int(m.group(1))

        if "◀️" in text:
            parts = text.split("◀️", 1)
        elif "▶️" in text:
            parts = text.split("▶️", 1)
        elif "—" in text:
            parts = text.split("—", 1)
        elif " - " in text:
            parts = text.split(" - ", 1)
        elif "-" in text:
            parts = text.split("-", 1)
        else:
            return None

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
                if rank:
                    cards.append({"rank": rank})
                i += 1
            return cards

        return {
            "number": number,
            "player_cards": parse_cards(parts[0]),
            "dealer_cards": parse_cards(parts[1]),
            "text": text,
        }
    except Exception as e:
        print(f"❌ parse_game: {e}", flush=True)
        return None

def is_finished_game(text):
    return "✅" in text or "🔰" in text

# =====================================================================
# LIVE API
# =====================================================================
def get_active_games():
    url = (
        f"{BASE_URL}/service-api/main-live-feed/v3/games1x2"
        "?cfView=3&count=40&fcountry=1&gr=415&grMode=4&lng=ru&ref=7"
        "&selectedMs=1.146.2092323,2.146.2092323,10.146.2092323"
    )
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.status_code != 200:
            print(f"⚠️ active games HTTP {r.status_code}", flush=True)
            return []
        data = r.json()
        games = data.get("Value", []) if isinstance(data, dict) else data
        return [
            g for g in games
            if g.get("liga", {}).get("id") == 2092323 and g.get("id")
        ]
    except Exception as e:
        print(f"❌ get_active_games: {e}", flush=True)
        return []

def get_game_data(game_id):
    url = (
        f"{BASE_URL}/service-api/LiveFeed/GetGameZip?id={game_id}"
        "&isSubGames=true&GroupEvents=true&countevents=250&grMode=4"
        "&partner=7&topGroups=&country=190&marketType=1&isNewBuilder=true"
    )
    try:
        t0 = time.perf_counter()
        r = requests.get(url, headers=HEADERS, timeout=5)
        latency = (time.perf_counter() - t0) * 1000
        if r.status_code == 200:
            return r.json(), latency
    except Exception as e:
        print(f"⚠️ get_game_data {game_id}: {e}", flush=True)
    return None, None

def get_fresh_latency():
    games = get_active_games()
    if not games:
        return None
    for g in games:
        data, latency = get_game_data(str(g.get("id")))
        if data is not None and latency is not None:
            return latency
    return None

def get_game_number():
    now = datetime.now(MOSCOW_TZ)
    start = now.replace(hour=3, minute=0, second=0, microsecond=0)
    if now < start:
        start = start - timedelta(days=1)
    diff_minutes = (now - start).total_seconds() / 60
    return int(diff_minutes) // 2 % 720 + 1

# =====================================================================
# ПРОГНОЗ РАНГА
# =====================================================================
def predict_rank_by_latency(latency):
    for (low, high), probs in RANK_TABLE.items():
        if low <= latency < high:
            sorted_ranks = sorted(probs.items(), key=lambda x: x[1], reverse=True)
            return sorted_ranks[0][0], sorted_ranks[0][1]
    return None, 0.0

# =====================================================================
# GAME STORAGE
# =====================================================================
games_by_number = {}

def add_game(text):
    game = parse_game_from_text(text)
    if not game:
        return None
    n = game["number"]
    games_by_number[n] = game
    return game

def find_game(n):
    return games_by_number.get(n)

def cleanup_games():
    if len(games_by_number) > MAX_MESSAGES:
        keys = sorted(games_by_number.keys())
        for k in keys[:-MAX_MESSAGES]:
            games_by_number.pop(k, None)

# =====================================================================
# ПРОВЕРКА РЕЗУЛЬТАТА (ИГРОК + ДИЛЕР)
# =====================================================================
def check_results():
    global all_messages, state

    for entry in state.get("predictions", []):
        if entry.get("status") != "pending":
            continue

        target = entry.get("target")
        predicted_rank = entry.get("selected_prediction")
        message_id = entry.get("message_id")
        original_text = entry.get("message_text", "")
        latency = entry.get("latency", 100)

        if not predicted_rank or not message_id:
            continue

        max_games_to_check = DOGON_GAMES

        for i in range(max_games_to_check):
            game_to_check = target + i

            game_msg = None
            for msg in all_messages:
                if f"#N{game_to_check}" in msg and ('✅' in msg or '🔰' in msg):
                    game_msg = msg
                    break

            if not game_msg:
                print(f"⏳ Ждём игру #N{game_to_check} для проверки ранга {predicted_rank}", flush=True)
                continue

            game_data = parse_game_from_text(game_msg)
            if not game_data:
                continue

            rank_found = False
            player_cards = game_data.get("player_cards", [])
            dealer_cards = game_data.get("dealer_cards", [])
            
            # Проверяем у игрока
            for card in player_cards:
                if card.get("rank") == predicted_rank:
                    rank_found = True
                    break
            
            # Если не нашли у игрока — проверяем у дилера
            if not rank_found:
                for card in dealer_cards:
                    if card.get("rank") == predicted_rank:
                        rank_found = True
                        break

            if rank_found:
                print(f"🎯 РАНГ {predicted_rank} НАЙДЕН в игре #N{game_to_check}!", flush=True)
                dogon_number = i

                entry["selected_result"] = True
                entry["evaluated"] = True
                entry["result_game"] = game_to_check
                entry["dogon"] = dogon_number
                entry["status"] = "win"

                state["wins"] = state.get("wins", 0) + 1
                state["total_predictions"] = state.get("total_predictions", 0) + 1

                suffix = f"\n\n✅ <b>ЗАШЛО</b> в игре #N{game_to_check}" if i == 0 else f"\n\n✅ <b>ЗАШЛО</b> на догоне {i}: #N{game_to_check}"
                edit_message(message_id, original_text + suffix)
                entry["message_text"] = original_text + suffix

                save_json(STATE_FILE, state)
                return

            if i == max_games_to_check - 1:
                print(f"❌ Ранг {predicted_rank} НЕ НАЙДЕН за {max_games_to_check} игр", flush=True)

                entry["selected_result"] = False
                entry["evaluated"] = True
                entry["status"] = "lose"

                state["losses"] = state.get("losses", 0) + 1
                state["total_predictions"] = state.get("total_predictions", 0) + 1

                suffix = f"\n\n❌ <b>НЕ ЗАШЛО</b> (проверено {max_games_to_check} игр)"
                edit_message(message_id, original_text + suffix)
                entry["message_text"] = original_text + suffix

                save_json(STATE_FILE, state)
                return

# =====================================================================
# ПЛАНИРОВЩИК (С УТОЧНЕНИЕМ ПО ИГРОКУ)
# =====================================================================
def schedule_for_game(game_number):
    target = game_number + OFFSET

    for e in state.get("predictions", []):
        if e.get("target") == target and e.get("status") in ("scheduled", "pending"):
            return

    source = target - 1

    state["predictions"].append({
        "source": source,
        "target": target,
        "selected_prediction": None,
        "latency": None,
        "confidence": 0.0,
        "status": "scheduled",
        "evaluated": False,
        "created": datetime.now(MOSCOW_TZ).isoformat(),
        "message_id": None,
        "message_text": "",
    })
    if len(state["predictions"]) > MAX_STATE_PREDICTIONS:
        state["predictions"] = state["predictions"][-MAX_STATE_PREDICTIONS:]
    save_json(STATE_FILE, state)

    print(f"📅 Запланировано: #{game_number} → #{target} (+{OFFSET})", flush=True)

def process_scheduled():
    for entry in state.get("predictions", []):
        if entry.get("status") != "scheduled":
            continue

        target = entry.get("target")
        source_num = entry.get("source")
        source = find_game(source_num)

        if not source:
            continue

        latency = get_fresh_latency()
        if latency is None:
            print("⏳ Нет свежей задержки — прогноз остаётся в очереди", flush=True)
            continue

        predicted_rank, confidence = predict_rank_by_latency(latency)
        if predicted_rank is None or confidence < MIN_CONFIDENCE:
            print(f"⏭️ Уверенность {confidence:.1f}% < {MIN_CONFIDENCE:.0f}% — НЕ ДАЁМ", flush=True)
            entry["status"] = "skipped"
            entry["selected_prediction"] = None
            entry["confidence"] = confidence
            entry["latency"] = latency
            save_json(STATE_FILE, state)
            continue

        # ============================================================
        # УТОЧНЕНИЕ ПО ПЕРВОЙ КАРТЕ ИГРОКА
        # ============================================================
        p = source.get("player_cards", [])
        if p:
            first_card_rank = p[0].get("rank", "")
            if first_card_rank:
                if first_card_rank == predicted_rank:
                    confidence = min(100.0, confidence + 5.0)
                    print(f"✅ Первая карта игрока {first_card_rank} совпадает с прогнозом! Уверенность +5%", flush=True)
                else:
                    confidence = max(0.0, confidence - 3.0)
                    print(f"⚠️ Первая карта игрока {first_card_rank} не совпадает с прогнозом {predicted_rank}. Уверенность -3%", flush=True)

        if confidence < MIN_CONFIDENCE:
            print(f"⏭️ После уточнения уверенность {confidence:.1f}% < {MIN_CONFIDENCE:.0f}% — НЕ ДАЁМ", flush=True)
            entry["status"] = "skipped"
            entry["selected_prediction"] = None
            entry["confidence"] = confidence
            entry["latency"] = latency
            save_json(STATE_FILE, state)
            continue

        entry["selected_prediction"] = predicted_rank
        entry["latency"] = latency
        entry["confidence"] = confidence
        entry["status"] = "pending"

        msg = prediction_text(entry, predicted_rank, source, confidence)
        mid = send_message(msg)

        if mid:
            entry["message_id"] = mid
            entry["message_text"] = msg
            save_json(STATE_FILE, state)

            print(
                f"✅ ПРОГНОЗ: #{target} → {predicted_rank} | "
                f"уверенность={confidence:.1f}%",
                flush=True,
            )
        else:
            entry["status"] = "scheduled"
            save_json(STATE_FILE, state)

def prediction_text(entry, prediction, source_game, confidence):
    msg = "🔮 <b>RANK BOT</b>\n"
    msg += f"🃏 Ранг: <b>{prediction}</b>\n"
    msg += f"🎯 Уверенность: {confidence:.1f}%\n"
    msg += f"🎯 Целевая игра: #N{entry['target']}\n"
    msg += f"📈 Догон: {DOGON_GAMES - 1} игры\n"

    p = source_game.get("player_cards", [])
    d = source_game.get("dealer_cards", [])
    seq = []
    for prefix, cards in [("P", p[:3]), ("D", d[:3])]:
        for i, c in enumerate(cards, 1):
            rank = c.get("rank", "")
            if rank:
                seq.append(f"{prefix}{i}:{rank}")
    if seq:
        msg += "📌 " + " ".join(seq) + "\n"

    msg += "⏰ " + datetime.now(MOSCOW_TZ).strftime("%H:%M:%S")
    return msg

# =====================================================================
# СТАТИСТИКА
# =====================================================================
def stats_text():
    total = state.get("total_predictions", 0)
    wins = state.get("wins", 0)
    losses = state.get("losses", 0)
    acc = f"{wins / total * 100:.1f}%" if total else "—"

    return (
        "📊 <b>СТАТИСТИКА (RANK BOT)</b>\n"
        f"⏰ {datetime.now(MOSCOW_TZ).strftime('%d.%m.%Y %H:%M:%S')}\n"
        "==============================\n"
        f"📈 Всего прогнозов: {total}\n"
        f"✅ Зашло: {wins}\n"
        f"❌ Не зашло: {losses}\n"
        f"🎯 Точность: {acc}\n"
        f"🎯 Минимальный порог: {MIN_CONFIDENCE:.0f}%"
    )

# =====================================================================
# OFFSET
# =====================================================================
def load_offset():
    try:
        return int(OFFSET_FILE.read_text().strip())
    except Exception:
        return 0

def save_offset(v):
    OFFSET_FILE.write_text(str(v), encoding="utf-8")

# =====================================================================
# MAIN
# =====================================================================
def main():
    global all_messages, state

    print("=" * 70, flush=True)
    print("🃏 RANK BOT (LATENCY + RANK)", flush=True)
    print("=" * 70, flush=True)

    send_message(stats_text())

    offset = load_offset()
    last_stats = time.time()

    print("🚀 БОТ ГОТОВ.", flush=True)
    print(f"🎯 OFFSET: +{OFFSET}", flush=True)
    print(f"🎯 Минимальная уверенность: {MIN_CONFIDENCE:.0f}%", flush=True)

    while True:
        try:
            now = time.time()

            check_results()
            process_scheduled()

            if now - last_stats > 3600:
                send_message(stats_text())
                last_stats = now

            updates = telegram_get_updates(offset)

            for upd in updates.get("result", []):
                uid = upd.get("update_id")
                if uid is not None:
                    offset = uid + 1
                    save_offset(offset)

                post = upd.get("channel_post") or upd.get("edited_channel_post")
                if not post:
                    continue

                chat_id = str(post.get("chat", {}).get("id", ""))
                if chat_id != str(CHANNEL_STATS):
                    continue

                text = post.get("text", "")
                if "#N" not in text:
                    continue

                if text not in all_messages:
                    all_messages.append(text)
                    if len(all_messages) > 500:
                        all_messages = all_messages[-500:]

                game = add_game(text)
                if not game:
                    continue

                n = game["number"]
                print(
                    f"📥 Игра #{n} | {'завершена' if is_finished_game(text) else 'не завершена'}",
                    flush=True,
                )

                if not is_finished_game(text):
                    schedule_for_game(n)

                cleanup_games()

            check_results()
            process_scheduled()

            time.sleep(1)

        except KeyboardInterrupt:
            print("🛑 Остановка", flush=True)
            break
        except Exception as e:
            print(f"❌ MAIN ERROR: {e}", flush=True)
            traceback.print_exc()
            try:
                save_json(STATE_FILE, state)
            except Exception:
                pass
            time.sleep(10)

if __name__ == "__main__":
    main()