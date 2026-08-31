import os
import sys
import requests
import json
import re
import time
import pickle
import numpy as np
from datetime import datetime, timedelta
import pytz
from collections import deque, defaultdict
import warnings
import gc
import math
import traceback

warnings.filterwarnings("ignore")


# =====================================================================
# АВТОУСТАНОВКА ЗАВИСИМОСТЕЙ
# =====================================================================

try:
    import subprocess
    import importlib

    REQUIRED_PACKAGES = [
        "numpy",
        "requests",
        "pytz"
    ]

    def install_package(package):
        print(f"📦 Устанавливаю: {package}...", flush=True)
        try:
            subprocess.check_call([
                sys.executable,
                "-m",
                "pip",
                "install",
                package,
                "--quiet"
            ])
            print(f"✅ {package} установлен!", flush=True)
            return True
        except Exception as e:
            print(f"❌ Ошибка установки {package}: {e}", flush=True)
            return False

    def check_and_install_dependencies():
        print("=" * 60, flush=True)
        print("🔍 ПРОВЕРКА ЗАВИСИМОСТЕЙ...", flush=True)
        print("=" * 60, flush=True)

        missing = []
        for package in REQUIRED_PACKAGES:
            try:
                importlib.import_module(package.replace("-", "_"))
                print(f"✅ {package} - уже установлен", flush=True)
            except ImportError:
                print(f"⚠️ {package} - НЕ НАЙДЕН", flush=True)
                missing.append(package)

        if missing:
            print(f"\n📦 Нужно установить: {', '.join(missing)}", flush=True)
            for package in missing:
                if not install_package(package):
                    return False

        print("\n✅ ВСЕ ЗАВИСИМОСТИ УСТАНОВЛЕНЫ!", flush=True)
        print("=" * 60, flush=True)
        return True

    if not check_and_install_dependencies():
        sys.exit(1)

except Exception as e:
    print(f"⚠️ Ошибка проверки зависимостей: {e}", flush=True)


# =====================================================================
# ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ
# =====================================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    BOT_TOKEN = os.getenv("BOT_TOKEN_PROGNOZ")

CHANNEL_STATS = os.getenv("CHANNEL_STATS")
CHANNEL_PROGNOZ = os.getenv("CHANNEL_PROGNOZ")

if not BOT_TOKEN or not CHANNEL_STATS or not CHANNEL_PROGNOZ:
    print("❌ ОШИБКА: переменные окружения не заданы!", flush=True)
    print("Нужны: BOT_TOKEN, CHANNEL_STATS, CHANNEL_PROGNOZ", flush=True)
    sys.exit(1)


# =====================================================================
# НОРМАЛИЗАЦИЯ
# =====================================================================

CHANNEL_STATS = str(CHANNEL_STATS).strip()
CHANNEL_PROGNOZ = str(CHANNEL_PROGNOZ).strip()


# =====================================================================
# НАСТРОЙКИ
# =====================================================================

MOSCOW_TZ = pytz.timezone("Europe/Moscow")
BASE_URL = "https://1xlite-0687.pro"

DATA_FILE = "cards_data.json"
HISTORY_FILE = "cards_history.json"
OFFSET_FILE = "cards_offset.txt"
HYBRID_DATA_FILE = "hybrid_state.json"

MAX_RECORDS = 10000
CHECK_INTERVAL = 5
MAX_HISTORY = 2000
DOGON_GAMES = 4

FORECAST_OFFSET = 0


# =====================================================================
# КАРТЫ
# =====================================================================

TARGET_CARDS = [
    "J♠️", "J♣️", "J♦️", "J♥️",
    "Q♠️", "Q♣️", "Q♦️", "Q♥️",
    "K♠️", "K♣️", "K♦️", "K♥️",
    "A♠️", "A♣️", "A♦️", "A♥️"
]


# =====================================================================
# HTTP HEADERS
# =====================================================================

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": f"{BASE_URL}/ru/live/twentyone/1643503-twentyone-game",
    "Cookie": "platform_type=desktop; lng=ru; cookies_agree_type=3; tzo=3; is12h=0"
}


# =====================================================================
# ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ
# =====================================================================

predictions = []
seen_upcoming_games = set()

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
            print(f"❌ Telegram {method}: HTTP {response.status_code}", flush=True)
            return None
        data = response.json()
        if not data.get("ok"):
            print(f"❌ Telegram {method}: {data}", flush=True)
            return None
        return data
    except Exception as e:
        print(f"❌ Telegram {method}: {e}", flush=True)
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
# РАБОТА С ДАННЫМИ
# =====================================================================

def load_data():
    """Загружает данные из hybrid_state.json"""
    
    data = []
    
    if os.path.exists(HYBRID_DATA_FILE):
        try:
            with open(HYBRID_DATA_FILE, "r", encoding="utf-8") as f:
                hybrid_data = json.load(f)
                
                if isinstance(hybrid_data, list):
                    print(f"📊 Загружено из hybrid_state.json: {len(hybrid_data)} записей", flush=True)
                    data = hybrid_data
                elif isinstance(hybrid_data, dict):
                    for key in ["data", "games", "records", "items", "history", "cards"]:
                        if key in hybrid_data and isinstance(hybrid_data[key], list):
                            data = hybrid_data[key]
                            print(f"📊 Загружено из hybrid_state.json ({key}): {len(data)} записей", flush=True)
                            break
        except Exception as e:
            print(f"⚠️ Ошибка загрузки hybrid_state.json: {e}", flush=True)
    
    if not data:
        print(f"⚠️ Данные не найдены!", flush=True)
        return []
    
    print(f"📊 ИТОГО загружено данных: {len(data)} записей", flush=True)
    
    return data


# =====================================================================
# ИСТОРИЯ ПРОГНОЗОВ
# =====================================================================

def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
        except Exception as e:
            print(f"⚠️ Ошибка истории прогнозов: {e}", flush=True)
    return []


def save_history(history):
    if len(history) > MAX_HISTORY:
        history = history[-MAX_HISTORY:]
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"⚠️ Ошибка сохранения истории: {e}", flush=True)


# =====================================================================
# OFFSET
# =====================================================================

def get_offset():
    if os.path.exists(OFFSET_FILE):
        try:
            with open(OFFSET_FILE, "r") as f:
                return int(f.read().strip())
        except:
            return 0
    return 0


def save_offset(offset):
    try:
        with open(OFFSET_FILE, "w") as f:
            f.write(str(offset))
    except Exception as e:
        print(f"⚠️ Ошибка сохранения offset: {e}", flush=True)


# =====================================================================
# НОМЕР ИГРЫ
# =====================================================================

def get_game_number_from_timestamp(timestamp):
    if not timestamp:
        return None
    try:
        if isinstance(timestamp, (int, float)):
            start_time = datetime.fromtimestamp(timestamp, MOSCOW_TZ)
        else:
            start_time = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00")).astimezone(MOSCOW_TZ)
    except:
        return None

    start_day = start_time.replace(hour=3, minute=0, second=0, microsecond=0)
    if start_time < start_day:
        start_day -= timedelta(days=1)
    diff_minutes = (start_time - start_day).total_seconds() / 60
    return (int(diff_minutes) % 1440) + 1


def add_game_offset(game_num, offset):
    return ((int(game_num) - 1 + int(offset)) % 1440) + 1


def circular_game_distance(a, b):
    diff = abs(int(a) - int(b))
    return min(diff, 1440 - diff)


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
        upcoming_games = []
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
                        game_num = get_game_number_from_timestamp(start_ts)
                        if not game_num:
                            continue
                        start_time = datetime.fromtimestamp(start_ts, MOSCOW_TZ)
                        minutes_until = (start_time - now).total_seconds() / 60

                        if 0 < minutes_until <= 20:
                            upcoming_games.append({
                                "game_id": str(game.get("id")),
                                "game_num": game_num,
                                "start_time": start_time,
                                "minutes_until": minutes_until,
                                "start_ts": start_ts
                            })

        return upcoming_games
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
# ПОИСК ПО МИЛЛИСЕКУНДАМ (ЕДИНСТВЕННАЯ ЛОГИКА)
# =====================================================================

def find_by_milliseconds(timestamp_msk):
    """Ищет игры с такой же миллисекундой"""
    if not timestamp_msk:
        return []
    
    parts = timestamp_msk.split(".")
    if len(parts) < 2:
        return []
    
    target_ms = parts[1]
    print(f"🔍 Ищу миллисекунду: {target_ms}", flush=True)
    
    data = load_data()
    if not data:
        print("⚠️ Нет данных для поиска", flush=True)
        return []
    
    matches = []
    
    for record in data:
        record_time = record.get("timestamp_msk", "")
        if not record_time:
            continue
        
        record_parts = record_time.split(".")
        if len(record_parts) < 2:
            continue
        
        record_ms = record_parts[1]
        
        if record_ms == target_ms:
            cards = get_target_cards_from_record(record)
            if cards:
                matches.append({
                    "record": record,
                    "cards": cards,
                    "timestamp": record_time
                })
    
    return matches


def predict_by_milliseconds(timestamp_msk):
    """Прогноз ТОЛЬКО по миллисекундам"""
    matches = find_by_milliseconds(timestamp_msk)
    
    if not matches:
        print("⚠️ Совпадений по миллисекундам не найдено", flush=True)
        return None, 0
    
    # Считаем частоту карт
    card_counter = defaultdict(int)
    for match in matches:
        for card in match["cards"]:
            card_counter[card] += 1
    
    if not card_counter:
        print("⚠️ В найденных играх нет целевых карт", flush=True)
        return None, 0
    
    # Берем САМУЮ ЧАСТУЮ КАРТУ
    most_common = max(card_counter.items(), key=lambda x: x[1])
    
    print(f"\n🔍 ПОИСК ПО МИЛЛИСЕКУНДАМ", flush=True)
    print(f"   Найдено совпадений: {len(matches)}", flush=True)
    print(f"   Миллисекунда: {timestamp_msk.split('.')[1] if '.' in timestamp_msk else '?'}", flush=True)
    print(f"   🎯 КАРТА: {most_common[0]} (выпала {most_common[1]} раз)", flush=True)
    
    # Выводим топ карт для информации
    sorted_cards = sorted(card_counter.items(), key=lambda x: x[1], reverse=True)
    for i, (card, count) in enumerate(sorted_cards[:3], 1):
        print(f"   {i}. {card} — {count} раз", flush=True)
    
    # Возвращаем ТОЛЬКО ОДНУ КАРТУ (самую частую)
    return most_common, len(matches)


# =====================================================================
# ПРОВЕРКА ПРОГНОЗОВ
# =====================================================================

def has_prediction_for_target(target_game_num):
    for entry in predictions:
        if entry.get("target") == target_game_num:
            if entry.get("status") in ["pending", "win", "lose"]:
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
        scheduled_game_num = game.get("game_num")
        game_id = game.get("game_id")

        if not scheduled_game_num or not game_id:
            continue

        target_game_num = add_game_offset(scheduled_game_num, FORECAST_OFFSET)

        if game_id not in seen_upcoming_games:
            seen_upcoming_games.add(game_id)

            print(f"\n🆕 НОВАЯ ИГРА #{scheduled_game_num} появилась в лобби!", flush=True)
            print(f"⏰ До старта: {game.get('minutes_until', 0):.1f} минут", flush=True)
            print(f"🔮 Прогноз на +{FORECAST_OFFSET}: #{target_game_num}", flush=True)

            if has_prediction_for_target(target_game_num):
                print(f"⏭️ На #{target_game_num} прогноз уже существует", flush=True)
                continue

            # =========================================================
            # ЕДИНСТВЕННАЯ ЛОГИКА: МИЛЛИСЕКУНДЫ
            # =========================================================
            
            # Получаем текущее время с миллисекундами
            now = datetime.now(MOSCOW_TZ)
            timestamp_msk = now.strftime("%H:%M:%S.%f")[:-3]
            
            print(f"⏰ Текущее время: {timestamp_msk}", flush=True)

            # Прогноз по миллисекундам
            predicted_card, matches_count = predict_by_milliseconds(timestamp_msk)

            if not predicted_card:
                print(f"⏭️ Нет прогноза для #{target_game_num}", flush=True)
                continue

            card, count = predicted_card

            # =========================================================
            # ФОРМИРУЕМ ПРОГНОЗ
            # =========================================================
            
            msg = (
                "🔮 ТОЧНАЯ КАРТА (МИЛЛИСЕКУНДЫ)\n\n"
                f"🎯 Целевая игра: #N{target_game_num}\n"
                f"⏰ Время замера: {timestamp_msk}\n"
                f"📊 Найдено совпадений: {matches_count}\n\n"
                f"1️⃣ {card}\n"
                f"\n📈 Догон: {DOGON_GAMES - 1} игр"
            )

            message_id = send_message(CHANNEL_PROGNOZ, msg)

            if not message_id:
                print("❌ Не удалось отправить прогноз", flush=True)
                continue

            entry = {
                "source": scheduled_game_num,
                "target": target_game_num,
                "cards": [card],
                "card": card,
                "count": count,
                "message_id": message_id,
                "channel_id": CHANNEL_PROGNOZ,
                "original_text": msg,
                "status": "pending",
                "timestamp_msk": timestamp_msk,
                "matches_count": matches_count,
                "forecast_offset": FORECAST_OFFSET,
                "dogon_games": DOGON_GAMES,
                "created": datetime.now(MOSCOW_TZ).isoformat()
            }

            predictions.append(entry)
            if len(predictions) > 200:
                predictions = predictions[-200:]

            save_history(predictions)

            print("\n✅ ПРОГНОЗ ОТПРАВЛЕН", flush=True)
            print(f"📌 Источник: #{scheduled_game_num}", flush=True)
            print(f"🎯 Цель: #{target_game_num}", flush=True)
            print(f"🃏 Прогноз: {card}", flush=True)
            print(f"📊 Совпадений: {matches_count}", flush=True)
            print(f"📢 Канал прогноза: {CHANNEL_PROGNOZ}", flush=True)


# =====================================================================
# ПРОВЕРКА РЕЗУЛЬТАТОВ
# =====================================================================

def find_stats_game_text(game_number):
    if not hasattr(find_stats_game_text, "games_cache"):
        find_stats_game_text.games_cache = {}
    return find_stats_game_text.games_cache.get(game_number)


def cache_stats_result(game_number, text):
    if not hasattr(find_stats_game_text, "games_cache"):
        find_stats_game_text.games_cache = {}
    find_stats_game_text.games_cache[game_number] = text
    
    if len(find_stats_game_text.games_cache) > 1000:
        current = get_game_number_from_timestamp(time.time())
        sorted_items = sorted(
            find_stats_game_text.games_cache.items(),
            key=lambda x: circular_game_distance(x[0], current)
        )
        find_stats_game_text.games_cache = dict(sorted_items[:500])


def parse_game_from_text(text):
    try:
        game_match = re.search(r"#N(\d+)", text)
        if not game_match:
            return None
        game_number = int(game_match.group(1))

        if "◀️" in text:
            parts = text.split("◀️", 1)
        elif "▶️" in text:
            parts = text.split("▶️", 1)
        elif " - " in text:
            parts = text.split(" - ", 1)
        elif "—" in text:
            parts = text.split("—", 1)
        else:
            return None

        if len(parts) < 2:
            return None

        def parse_cards(part):
            match = re.search(r"\(([^)]*)\)", part)
            if not match:
                return []
            cards_str = match.group(1)
            cards = []
            pattern = r"(10|[2-9AJQK])([♠♣♦♥])"
            matches = re.findall(pattern, cards_str)
            for rank, suit in matches:
                suit_map = {"♠": "♠️", "♣": "♣️", "♦": "♦️", "♥": "♥️"}
                cards.append({"rank": rank, "suit": suit_map.get(suit, suit)})
            return cards

        return {
            "number": game_number,
            "player_cards": parse_cards(parts[0]),
            "dealer_cards": parse_cards(parts[1]),
            "text": text
        }
    except Exception as e:
        print(f"❌ Ошибка парсинга игры: {e}", flush=True)
        return None


def get_actual_cards_from_game(game_text):
    game_data = parse_game_from_text(game_text)
    if not game_data:
        return []

    all_cards = game_data.get("player_cards", []) + game_data.get("dealer_cards", [])
    actual_cards = []
    for card in all_cards:
        rank = card.get("rank", "")
        suit = card.get("suit", "")
        card_str = rank + suit
        if card_str:
            actual_cards.append(card_str)
    return actual_cards


def evaluate_prediction_against_game(predicted_cards, game_text):
    actual_cards = get_actual_cards_from_game(game_text)
    if not actual_cards:
        return {"matched": False, "found_card": None, "actual_cards": []}

    predicted_set = set(predicted_cards)
    for actual_card in actual_cards:
        if actual_card in predicted_set:
            return {"matched": True, "found_card": actual_card, "actual_cards": actual_cards}

    return {"matched": False, "found_card": None, "actual_cards": actual_cards}


def is_finished_game_text(text):
    if not text:
        return False
    return "✅" in text or "🔰" in text


def check_results():
    global predictions, stats

    if not predictions:
        return

    cache = getattr(find_stats_game_text, "games_cache", {})
    if not cache:
        return

    for entry in predictions:
        if entry.get("status") != "pending":
            continue

        target = entry.get("target")
        predicted_cards = entry.get("cards", [])
        message_id = entry.get("message_id")
        original_text = entry.get("original_text", "")

        if target is None or not predicted_cards or not message_id:
            continue

        print(f"\n🔍 ПРОВЕРКА ПРОГНОЗА", flush=True)
        print(f"🎯 Целевая игра: #{target}", flush=True)
        print(f"🃏 Ищем: {' + '.join(predicted_cards)}", flush=True)

        found = False
        found_card = None
        found_game = None
        found_dogon = None
        games_checked = []
        actual_cards_by_game = {}

        for i in range(DOGON_GAMES):
            game_to_check = add_game_offset(target, i)
            game_text = cache.get(game_to_check)

            if not game_text:
                print(f"⏳ #{game_to_check} ещё отсутствует", flush=True)
                continue

            games_checked.append(game_to_check)
            result = evaluate_prediction_against_game(predicted_cards, game_text)
            actual_cards_by_game[game_to_check] = result.get("actual_cards", [])
            print(f"🔎 #{game_to_check}: {result.get('actual_cards', [])}", flush=True)

            if result["matched"]:
                found = True
                found_card = result["found_card"]
                found_game = game_to_check
                found_dogon = i
                break

        if found:
            print(f"🎯 ПРОГНОЗ ЗАШЁЛ!", flush=True)
            stats["total"] += 1
            stats["win"] += 1
            stats["by_dogon"][found_dogon] = stats["by_dogon"].get(found_dogon, 0) + 1
            stats["card_hits"][found_card] += 1

            result_text = (
                "\n\n════════════════════\n"
                f"✅ <b>ЗАШЛО</b>\n"
                "════════════════════\n"
                f"🎯 Игра: #{found_game}\n"
                f"🃏 Выпала: <b>{found_card}</b>\n"
                f"📈 Догон: <b>{found_dogon}</b>"
            )

            edit_message(message_id, original_text + result_text)
            entry["status"] = "win"
            entry["result_game"] = found_game
            entry["dogon"] = found_dogon
            entry["found_card"] = found_card
            entry["checked_games"] = games_checked
            entry["actual_cards_by_game"] = actual_cards_by_game
            entry["checked_at"] = datetime.now(MOSCOW_TZ).isoformat()
            save_history(predictions)
            continue

        if len(games_checked) < DOGON_GAMES:
            print(f"⏳ Прогноз #{target} ждёт результаты ({len(games_checked)}/{DOGON_GAMES})", flush=True)
            continue

        print(f"❌ ПРОГНОЗ НЕ ЗАШЁЛ", flush=True)
        stats["total"] += 1
        stats["lose"] += 1

        result_text = (
            "\n\n════════════════════\n"
            f"❌ <b>НЕ ЗАШЛО</b>\n"
            "════════════════════\n"
            f"🎯 Цель: #{target}\n"
            f"🔍 Проверено игр: {DOGON_GAMES}\n"
            f"🃏 Искали: {' / '.join(predicted_cards)}"
        )

        edit_message(message_id, original_text + result_text)
        entry["status"] = "lose"
        entry["checked_games"] = games_checked
        entry["actual_cards_by_game"] = actual_cards_by_game
        entry["checked_at"] = datetime.now(MOSCOW_TZ).isoformat()
        save_history(predictions)


def load_old_telegram_results():
    print("\n📥 ЗАГРУЗКА СТАРЫХ РЕЗУЛЬТАТОВ", flush=True)
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
        response = requests.get(url, params={
            "limit": 100,
            "allowed_updates": json.dumps(["channel_post", "edited_channel_post"])
        }, timeout=10)

        if response.status_code != 200:
            print("⚠️ Не удалось получить старые updates", flush=True)
            return

        data = response.json()
        result_count = 0
        ignored_count = 0

        for update in data.get("result", []):
            post = update.get("channel_post") or update.get("edited_channel_post")
            if not post:
                continue

            chat = post.get("chat", {})
            chat_id = str(chat.get("id", ""))
            text = post.get("text", "")

            if chat_id != CHANNEL_STATS:
                ignored_count += 1
                continue

            if "#N" not in text or not is_finished_game_text(text):
                continue

            match = re.search(r"#N(\d+)", text)
            if not match:
                continue

            game_number = int(match.group(1))
            cache_stats_result(game_number, text)
            result_count += 1

        print(f"📊 Загружено результатов: {result_count}, игнорировано: {ignored_count}", flush=True)
    except Exception as e:
        print(f"⚠️ Ошибка загрузки старых результатов: {e}", flush=True)


def rebuild_prediction_stats():
    global stats
    stats["total"] = 0
    stats["win"] = 0
    stats["lose"] = 0
    stats["by_dogon"] = {0: 0, 1: 0, 2: 0, 3: 0}
    stats["card_hits"] = defaultdict(int)

    for entry in predictions:
        status = entry.get("status")
        if status == "win":
            stats["total"] += 1
            stats["win"] += 1
            dogon = entry.get("dogon", 0)
            stats["by_dogon"][dogon] = stats["by_dogon"].get(dogon, 0) + 1
            found_card = entry.get("found_card")
            if found_card:
                stats["card_hits"][found_card] += 1
        elif status == "lose":
            stats["total"] += 1
            stats["lose"] += 1


def process_telegram_updates(updates, offset):
    if not updates:
        return offset

    for update in updates.get("result", []):
        update_id = update.get("update_id")
        if update_id is None:
            continue

        offset = update_id + 1
        save_offset(offset)

        post = update.get("channel_post") or update.get("edited_channel_post")
        if not post:
            continue

        chat = post.get("chat", {})
        chat_id = str(chat.get("id", ""))
        text = post.get("text", "")

        if chat_id == CHANNEL_STATS and "#N" in text and is_finished_game_text(text):
            match = re.search(r"#N(\d+)", text)
            if match:
                game_number = int(match.group(1))
                cache_stats_result(game_number, text)
                print(f"💾 Сохранил результат #{game_number}", flush=True)

    return offset


def main():
    global predictions, collection_active

    print("=" * 60, flush=True)
    print("🔮 ТОЧНАЯ КАРТА (МИЛЛИСЕКУНДЫ)", flush=True)
    print("📌 Логика: Миллисекунды → карта", flush=True)
    print("=" * 60, flush=True)

    # Загружаем данные
    existing_data = load_data()
    print(f"📊 Загружено игр: {len(existing_data)}", flush=True)

    # Загружаем прогнозы
    predictions = load_history()
    if not isinstance(predictions, list):
        predictions = []
    print(f"🔮 Загружено прогнозов: {len(predictions)}", flush=True)

    # Пересчет статистики
    rebuild_prediction_stats()

    # Загружаем старые результаты
    load_old_telegram_results()

    offset = get_offset()
    print(f"📌 Telegram offset: {offset}", flush=True)

    print("=" * 60, flush=True)
    print("🚀 БОТ ГОТОВ!", flush=True)
    print("=" * 60, flush=True)

    last_upcoming_check = 0
    last_result_check = 0

    while True:
        try:
            current_time = time.time()

            if current_time - last_upcoming_check >= 10:
                check_upcoming_games()
                last_upcoming_check = current_time

            if current_time - last_result_check >= 5:
                check_results()
                last_result_check = current_time

            time.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:
            print("\n🛑 БОТ ОСТАНОВЛЕН", flush=True)
            break
        except Exception as e:
            print(f"❌ КРИТИЧЕСКАЯ ОШИБКА: {e}", flush=True)
            traceback.print_exc()
            time.sleep(30)


if __name__ == "__main__":
    main()