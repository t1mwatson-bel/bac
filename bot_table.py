import os
import sys
import requests
import json
import re
import time
from datetime import datetime, timedelta
import pytz
from collections import defaultdict
import traceback

# =====================================================================
# ПЕРЕМЕННЫЕ
# =====================================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    BOT_TOKEN = os.getenv("BOT_TOKEN_PROGNOZ")

CHANNEL_STATS = os.getenv("CHANNEL_STATS")
CHANNEL_PROGNOZ = os.getenv("CHANNEL_PROGNOZ")

if not BOT_TOKEN or not CHANNEL_STATS or not CHANNEL_PROGNOZ:
    print("❌ ОШИБКА: переменные окружения не заданы!", flush=True)
    sys.exit(1)

CHANNEL_STATS = str(CHANNEL_STATS).strip()
CHANNEL_PROGNOZ = str(CHANNEL_PROGNOZ).strip()

MOSCOW_TZ = pytz.timezone("Europe/Moscow")
BASE_URL = "https://1xlite-0687.pro"
HYBRID_DATA_FILE = "hybrid_state.json"
HISTORY_FILE = "cards_history.json"
OFFSET_FILE = "cards_offset.txt"
DOGON_GAMES = 4
CHECK_INTERVAL = 5

TARGET_CARDS = [
    "J♠️", "J♣️", "J♦️", "J♥️",
    "Q♠️", "Q♣️", "Q♦️", "Q♥️",
    "K♠️", "K♣️", "K♦️", "K♥️",
    "A♠️", "A♣️", "A♦️", "A♥️"
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": f"{BASE_URL}/ru/live/twentyone/1643503-twentyone-game",
    "Cookie": "platform_type=desktop; lng=ru; cookies_agree_type=3; tzo=3; is12h=0"
}

predictions = []
seen_upcoming_games = set()
games_cache = {}

stats = {
    "total": 0,
    "win": 0,
    "lose": 0,
    "by_dogon": {0: 0, 1: 0, 2: 0, 3: 0},
    "card_hits": defaultdict(int)
}


# =====================================================================
# TELEGRAM
# =====================================================================

def telegram_request(method, payload=None, timeout=20):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    try:
        response = requests.post(url, json=payload or {}, timeout=timeout)
        if response.status_code != 200:
            return None
        data = response.json()
        if not data.get("ok"):
            return None
        return data
    except:
        return None


def send_message(chat_id, text):
    result = telegram_request("sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML"
    }, timeout=15)
    if not result:
        return None
    try:
        return result["result"]["message_id"]
    except:
        return None


def edit_message(message_id, text):
    result = telegram_request("editMessageText", {
        "chat_id": CHANNEL_PROGNOZ,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML"
    }, timeout=15)
    return bool(result)


# =====================================================================
# ДАННЫЕ (ТОЛЬКО ЧТЕНИЕ)
# =====================================================================

def load_data():
    data = []
    if os.path.exists(HYBRID_DATA_FILE):
        try:
            with open(HYBRID_DATA_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
                if isinstance(raw, list):
                    data = raw
                elif isinstance(raw, dict):
                    for key in ["data", "games", "records", "items", "history", "cards"]:
                        if key in raw and isinstance(raw[key], list):
                            data = raw[key]
                            break
        except Exception as e:
            print(f"⚠️ Ошибка загрузки: {e}", flush=True)
    return data


def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
        except:
            pass
    return []


def save_history(history):
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
    except:
        pass


def get_offset():
    try:
        with open(OFFSET_FILE, "r") as f:
            return int(f.read().strip())
    except:
        return 0


def save_offset(offset):
    try:
        with open(OFFSET_FILE, "w") as f:
            f.write(str(offset))
    except:
        pass


# =====================================================================
# НОМЕР ИГРЫ
# =====================================================================

def get_game_number_from_timestamp(ts):
    if not ts:
        return None
    try:
        if isinstance(ts, (int, float)):
            dt = datetime.fromtimestamp(ts, MOSCOW_TZ)
        else:
            dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone(MOSCOW_TZ)
    except:
        return None
    start = dt.replace(hour=3, minute=0, second=0, microsecond=0)
    if dt < start:
        start -= timedelta(days=1)
    minutes = (dt - start).total_seconds() / 60
    return (int(minutes) % 1440) + 1


def add_game_offset(num, offset):
    return ((int(num) - 1 + int(offset)) % 1440) + 1


# =====================================================================
# API
# =====================================================================

def get_upcoming_games():
    try:
        url = f"{BASE_URL}/service-api/main-live-feed/v3/leftMenuSports?fcountry=1&gr=415&lng=ru&ref=7&selectedMs=10.146.1643503"
        response = requests.get(url, headers=HEADERS, timeout=10)
        if response.status_code != 200:
            return []
        data = response.json()
        games = []
        now = datetime.now(MOSCOW_TZ)
        if not isinstance(data, list):
            return []
        for section in data:
            if section.get("menuSectionId") != 10:
                continue
            for sport in section.get("sports", []):
                if sport.get("id") != 146:
                    continue
                for liga in sport.get("ligas", []):
                    if liga.get("id") != 1643503:
                        continue
                    for game in liga.get("games", []):
                        if game.get("nonStarted") != True:
                            continue
                        start_ts = game.get("startTs")
                        if not start_ts:
                            continue
                        num = get_game_number_from_timestamp(start_ts)
                        if not num:
                            continue
                        start_time = datetime.fromtimestamp(start_ts, MOSCOW_TZ)
                        minutes = (start_time - now).total_seconds() / 60
                        if 0 < minutes <= 20:
                            games.append({
                                "game_id": str(game.get("id")),
                                "game_num": num,
                                "minutes_until": minutes
                            })
        return games
    except Exception as e:
        print(f"❌ Ошибка будущих игр: {e}", flush=True)
        return []


# =====================================================================
# КАРТЫ ИЗ ЗАПИСИ
# =====================================================================

def get_target_cards_from_record(record):
    cards = []
    all_cards = record.get("player_cards", []) + record.get("dealer_cards", [])
    for card in all_cards:
        if isinstance(card, dict):
            rank = card.get("rank", "")
            suit = card.get("suit", "")
            card_str = rank + suit
        else:
            card_str = str(card)
        if card_str in TARGET_CARDS:
            cards.append(card_str)
    return list(set(cards))


# =====================================================================
# ЕДИНСТВЕННАЯ ЛОГИКА: МИЛЛИСЕКУНДЫ
# =====================================================================

def get_prediction_by_milliseconds(timestamp_msk):
    if not timestamp_msk:
        return None, 0
    
    parts = timestamp_msk.split(".")
    if len(parts) < 2:
        return None, 0
    
    target_ms = int(parts[1])
    
    data = load_data()
    if not data:
        return None, 0
    
    matches = []
    for record in data:
        record_time = record.get("timestamp_msk", "")
        if not record_time:
            continue
        r_parts = record_time.split(".")
        if len(r_parts) < 2:
            continue
        if int(r_parts[1]) == target_ms:
            cards = get_target_cards_from_record(record)
            if cards:
                matches.extend(cards)
    
    if not matches:
        return None, 0
    
    counter = defaultdict(int)
    for card in matches:
        counter[card] += 1
    
    max_count = max(counter.values())
    
    # Если максимальная частота == 1 → нет повторений → нет прогноза
    if max_count == 1:
        return None, 0
    
    # Собираем все карты с максимальной частотой
    leaders = [card for card, count in counter.items() if count == max_count]
    
    # Если больше одной карты имеют одинаковую максимальную частоту → нет прогноза
    if len(leaders) > 1:
        return None, 0
    
    # Остался только один лидер
    return leaders[0], len(matches)


# =====================================================================
# ПРОВЕРКА ПРОГНОЗОВ
# =====================================================================

def has_prediction_for_target(target):
    for entry in predictions:
        if entry.get("target") == target and entry.get("status") in ["pending", "win", "lose"]:
            return True
    return False


# =====================================================================
# СОЗДАНИЕ ПРОГНОЗА
# =====================================================================

def check_upcoming_games():
    global predictions, seen_upcoming_games
    
    upcoming = get_upcoming_games()
    if not upcoming:
        return
    
    for game in upcoming:
        scheduled_num = game.get("game_num")
        game_id = game.get("game_id")
        
        if not scheduled_num or not game_id:
            continue
        
        target_num = add_game_offset(scheduled_num, 0)
        
        if game_id in seen_upcoming_games:
            continue
        
        seen_upcoming_games.add(game_id)
        
        print(f"\n🆕 НОВАЯ ИГРА #{scheduled_num} в лобби!", flush=True)
        print(f"⏰ До старта: {game.get('minutes_until', 0):.1f} мин", flush=True)
        
        if has_prediction_for_target(target_num):
            print(f"⏭️ Прогноз на #{target_num} уже есть", flush=True)
            continue
        
        now = datetime.now(MOSCOW_TZ)
        timestamp_msk = now.strftime("%H:%M:%S.%f")[:-3]
        
        print(f"⏰ Время: {timestamp_msk}", flush=True)
        
        card, matches_count = get_prediction_by_milliseconds(timestamp_msk)
        
        if not card:
            print(f"⏭️ Нет прогноза для #{target_num}", flush=True)
            continue
        
        msg = (
            f"🔮 ТОЧНАЯ КАРТА (МИЛЛИСЕКУНДЫ)\n\n"
            f"🎯 Игра: #N{target_num}\n"
            f"⏰ Время: {timestamp_msk}\n"
            f"📊 Совпадений: {matches_count}\n\n"
            f"1️⃣ {card}\n\n"
            f"📈 Догон: {DOGON_GAMES - 1} игр"
        )
        
        msg_id = send_message(CHANNEL_PROGNOZ, msg)
        
        if not msg_id:
            print("❌ Не удалось отправить", flush=True)
            continue
        
        entry = {
            "target": target_num,
            "source": scheduled_num,
            "cards": [card],
            "card": card,
            "message_id": msg_id,
            "original_text": msg,
            "status": "pending",
            "timestamp_msk": timestamp_msk,
            "matches_count": matches_count,
            "created": datetime.now(MOSCOW_TZ).isoformat()
        }
        
        predictions.append(entry)
        save_history(predictions)
        
        print(f"✅ ПРОГНОЗ: {card} на #{target_num}", flush=True)


# =====================================================================
# ПРОВЕРКА РЕЗУЛЬТАТОВ
# =====================================================================

def cache_result(num, text):
    global games_cache
    games_cache[num] = text
    if len(games_cache) > 1000:
        current = get_game_number_from_timestamp(time.time())
        sorted_items = sorted(games_cache.items(), key=lambda x: abs(x[0] - current))
        games_cache = dict(sorted_items[:500])


def parse_cards_from_text(text):
    try:
        match = re.search(r"#N\d+\.\s*(\d+)\(([^)]*)\)\s*-\s*(?:✅|❌)?\s*(\d+)\(([^)]*)\)", text)
        if not match:
            return []
        all_cards_str = match.group(2) + match.group(4)
        cards = []
        pattern = r"(10|[2-9AJQK])([♠♣♦♥])"
        suit_map = {"♠": "♠️", "♣": "♣️", "♦": "♦️", "♥": "♥️"}
        for rank, suit in re.findall(pattern, all_cards_str):
            cards.append(rank + suit_map.get(suit, suit))
        return cards
    except:
        return []


def check_results():
    global predictions, stats
    
    if not predictions:
        return
    
    for entry in predictions:
        if entry.get("status") != "pending":
            continue
        
        target = entry.get("target")
        predicted_cards = entry.get("cards", [])
        msg_id = entry.get("message_id")
        original = entry.get("original_text", "")
        
        if not target or not predicted_cards or not msg_id:
            continue
        
        print(f"\n🔍 ПРОВЕРКА #{target}: ищем {predicted_cards}", flush=True)
        
        found = False
        found_card = None
        found_num = None
        dogon = 0
        
        for i in range(DOGON_GAMES):
            num = add_game_offset(target, i)
            text = games_cache.get(num)
            
            if not text:
                print(f"⏳ #{num} нет", flush=True)
                continue
            
            actual = parse_cards_from_text(text)
            print(f"🔎 #{num}: {actual}", flush=True)
            
            for card in predicted_cards:
                if card in actual:
                    found = True
                    found_card = card
                    found_num = num
                    dogon = i
                    break
            
            if found:
                break
        
        if found:
            print(f"✅ ЗАШЛО на #{found_num}! ({found_card})", flush=True)
            stats["total"] += 1
            stats["win"] += 1
            stats["by_dogon"][dogon] = stats["by_dogon"].get(dogon, 0) + 1
            stats["card_hits"][found_card] += 1
            
            result_text = (
                f"\n\n════════════════════\n"
                f"✅ <b>ЗАШЛО</b>\n"
                f"════════════════════\n"
                f"🎯 Игра: #{found_num}\n"
                f"🃏 Выпала: <b>{found_card}</b>\n"
                f"📈 Догон: <b>{dogon}</b>"
            )
            
            edit_message(msg_id, original + result_text)
            entry["status"] = "win"
            entry["result_game"] = found_num
            entry["dogon"] = dogon
            entry["found_card"] = found_card
            save_history(predictions)
            continue
        
        checked = 0
        for i in range(DOGON_GAMES):
            if games_cache.get(add_game_offset(target, i)):
                checked += 1
        
        if checked < DOGON_GAMES:
            print(f"⏳ Ждем результаты ({checked}/{DOGON_GAMES})", flush=True)
            continue
        
        print(f"❌ НЕ ЗАШЛО", flush=True)
        stats["total"] += 1
        stats["lose"] += 1
        
        result_text = (
            f"\n\n════════════════════\n"
            f"❌ <b>НЕ ЗАШЛО</b>\n"
            f"════════════════════\n"
            f"🎯 Цель: #{target}\n"
            f"🃏 Искали: {', '.join(predicted_cards)}"
        )
        
        edit_message(msg_id, original + result_text)
        entry["status"] = "lose"
        save_history(predictions)


# =====================================================================
# TELEGRAM UPDATES
# =====================================================================

def process_updates(updates, offset):
    if not updates:
        return offset
    
    for update in updates.get("result", []):
        uid = update.get("update_id")
        if uid is None:
            continue
        offset = uid + 1
        save_offset(offset)
        
        post = update.get("channel_post") or update.get("edited_channel_post")
        if not post:
            continue
        
        chat = post.get("chat", {})
        chat_id = str(chat.get("id", ""))
        text = post.get("text", "")
        
        if chat_id == CHANNEL_STATS:
            match = re.search(r"#N(\d+)", text)
            if match and ("✅" in text or "🔰" in text):
                num = int(match.group(1))
                cache_result(num, text)
                print(f"💾 Результат #{num}", flush=True)
    
    return offset


# =====================================================================
# MAIN
# =====================================================================

def main():
    global predictions
    
    print("=" * 60, flush=True)
    print("🔮 ТОЧНАЯ КАРТА (МИЛЛИСЕКУНДЫ)", flush=True)
    print("=" * 60, flush=True)
    
    data = load_data()
    print(f"📊 Загружено игр: {len(data)}", flush=True)
    
    predictions = load_history()
    if not isinstance(predictions, list):
        predictions = []
    print(f"🔮 Прогнозов: {len(predictions)}", flush=True)
    
    offset = get_offset()
    print(f"📌 Offset: {offset}", flush=True)
    
    print("=" * 60, flush=True)
    print("🚀 БОТ ГОТОВ!", flush=True)
    print("=" * 60, flush=True)
    
    last_check = 0
    last_result = 0
    
    while True:
        try:
            now = time.time()
            
            if now - last_check >= 10:
                check_upcoming_games()
                last_check = now
            
            if now - last_result >= 5:
                check_results()
                last_result = now
            
            updates = telegram_request("getUpdates", {"offset": offset, "timeout": 5})
            if updates:
                offset = process_updates(updates, offset)
            
            time.sleep(CHECK_INTERVAL)
            
        except KeyboardInterrupt:
            print("\n🛑 ОСТАНОВЛЕН", flush=True)
            break
        except Exception as e:
            print(f"❌ ОШИБКА: {e}", flush=True)
            traceback.print_exc()
            time.sleep(30)


if __name__ == "__main__":
    main()