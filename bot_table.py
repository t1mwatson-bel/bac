import os
import sys
import json
import time
import requests
import pytz
import re

from datetime import datetime, timedelta


# =====================================================================
# ENV
# =====================================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    BOT_TOKEN = os.getenv("BOT_TOKEN_PROGNOZ")

CHANNEL_PROGNOZ = os.getenv("CHAT_ID_21")

if not CHANNEL_PROGNOZ:
    CHANNEL_PROGNOZ = os.getenv("CHANNEL_PROGNOZ")

CHANNEL_STATS = os.getenv("CHANNEL_STATS")  # Канал статистики

if not BOT_TOKEN or not CHANNEL_PROGNOZ:
    print(
        "❌ Ошибка: BOT_TOKEN или CHANNEL_PROGNOZ не заданы!",
        flush=True
    )
    sys.exit(1)

print(
    f"✅ BOT_TOKEN: {BOT_TOKEN[:5]}...",
    flush=True
)

print(
    f"✅ CHANNEL_PROGNOZ: {CHANNEL_PROGNOZ}",
    flush=True
)

if CHANNEL_STATS:
    print(
        f"✅ CHANNEL_STATS: {CHANNEL_STATS}",
        flush=True
    )


# =====================================================================
# ОСНОВНЫЕ НАСТРОЙКИ
# =====================================================================

MOSCOW_TZ = pytz.timezone("Europe/Moscow")

BASE_URL = "https://1xlite-36553.pro"

LEAGUE_ID = 1643503

DATA_FILE = "twentyone_data_full.json"

PREDICTIONS_FILE = "twentyone_predictions.json"

MAX_HISTORY_GAMES = 1000

DOGON_GAMES = 4

TARGET_RANKS = {"J", "Q", "K", "A"}

SUIT_PAIRS = {"♥": "♠", "♠": "♥", "♣": "♦", "♦": "♣"}

POLL_INTERVAL = 2.0


# =====================================================================
# TELEGRAM
# =====================================================================

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"


# =====================================================================
# HTTP
# =====================================================================

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/120.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": f"{BASE_URL}/ru/live/twentyone/1643503-twentyone-game",
    "Cookie": (
        "platform_type=desktop; "
        "lng=ru; "
        "cookies_agree_type=3; "
        "tzo=3; "
        "is12h=0; "
        "cookies_agree_type=3"
    ),
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


# =====================================================================
# КАРТЫ
# =====================================================================

SUITS_NAMES = {0: "♠️", 1: "♣️", 2: "♦️", 3: "♥️"}

RANKS = {
    2: "2", 3: "3", 4: "4", 5: "5", 6: "6", 7: "7", 8: "8", 9: "9",
    10: "10", 11: "J", 12: "Q", 13: "K", 14: "A"
}


# =====================================================================
# НОРМАЛИЗАЦИЯ
# =====================================================================

def normalize_suit(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return SUITS_NAMES.get(value)
    text = str(value).strip().replace("\ufe0f", "")
    if text in ["0", "♠", "spade", "spades", "s"]:
        return "♠"
    if text in ["1", "♣", "club", "clubs", "c"]:
        return "♣"
    if text in ["2", "♦", "diamond", "diamonds", "d"]:
        return "♦"
    if text in ["3", "♥", "heart", "hearts", "h"]:
        return "♥"
    return None


def normalize_rank(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return RANKS.get(value)
    text = str(value).strip().upper().replace("А", "A")
    if text in {"2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"}:
        return text
    try:
        return RANKS.get(int(text))
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


# =====================================================================
# ГЛУБОКИЙ ПОИСК
# =====================================================================

def deep_find_value(obj, wanted_keys):
    wanted = {str(x).lower() for x in wanted_keys}
    if isinstance(obj, dict):
        for key, value in obj.items():
            if str(key).lower() in wanted:
                return value
        for value in obj.values():
            result = deep_find_value(value, wanted)
            if result is not None:
                return result
    elif isinstance(obj, list):
        for item in obj:
            result = deep_find_value(item, wanted)
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
    for key, value in obj.items():
        key_lower = str(key).strip().lower()
        if key_lower in rank_keys:
            candidate = normalize_rank(value)
            if candidate:
                rank = candidate
        if key_lower in suit_keys:
            candidate = normalize_suit(value)
            if candidate:
                suit = candidate
    if rank and suit:
        return {"rank": rank, "suit": f"{suit}\ufe0f"}
    return None


def classify_key(key):
    k = str(key).strip().lower()
    if (k == "p" or k.startswith("player") or k.startswith("p1") or k.startswith("p2") or
        k.startswith("p3") or k.startswith("p4") or "playercard" in k or "player_card" in k or
        k == "pcards" or k == "playercards"):
        return "player"
    if (k == "d" or k.startswith("dealer") or k.startswith("d1") or k.startswith("d2") or
        k.startswith("d3") or k.startswith("d4") or "dealercard" in k or "dealer_card" in k or
        k == "dcards" or k == "dealercards"):
        return "dealer"
    return None


def find_cards_recursive(obj, context=None, player=None, dealer=None):
    if player is None:
        player = []
    if dealer is None:
        dealer = []
    if isinstance(obj, dict):
        own_card = extract_card_from_dict(obj)
        if own_card:
            if context == "player":
                player.append(own_card)
            elif context == "dealer":
                dealer.append(own_card)
        for key, value in obj.items():
            local_context = context
            classified = classify_key(key)
            if classified:
                local_context = classified
            key_lower = str(key).strip().lower()
            player_keys = {"p", "p1", "p2", "p3", "p4", "p5", "p6", "p7", "p8", "p9"}
            dealer_keys = {"d", "d1", "d2", "d3", "d4", "d5", "d6", "d7", "d8", "d9"}
            if key_lower in player_keys:
                local_context = "player"
            if key_lower in dealer_keys:
                local_context = "dealer"
            find_cards_recursive(value, local_context, player, dealer)
    elif isinstance(obj, list):
        for item in obj:
            find_cards_recursive(item, context, player, dealer)
    return player, dealer


def find_card_lists_fallback(obj):
    player_candidates = []
    dealer_candidates = []

    def walk(value, context=None):
        if isinstance(value, dict):
            for key, item in value.items():
                key_lower = str(key).strip().lower()
                new_context = context
                if ("player" in key_lower or key_lower in {"p", "p1", "p2", "p3", "p4", "p5", "p6"}):
                    new_context = "player"
                if ("dealer" in key_lower or key_lower in {"d", "d1", "d2", "d3", "d4", "d5", "d6"}):
                    new_context = "dealer"
                if isinstance(item, list):
                    cards = []
                    for x in item:
                        card = extract_card_from_dict(x)
                        if card:
                            cards.append(card)
                    if cards:
                        if new_context == "player":
                            player_candidates.append(cards)
                        elif new_context == "dealer":
                            dealer_candidates.append(cards)
                walk(item, new_context)
        elif isinstance(value, list):
            for item in value:
                walk(item, context)
    walk(obj)
    player = []
    dealer = []
    if player_candidates:
        player = max(player_candidates, key=len)
    if dealer_candidates:
        dealer = max(dealer_candidates, key=len)
    return player, dealer


def clean_cards(cards):
    result = []
    for card in cards:
        if not card:
            continue
        rank = normalize_rank(card.get("rank"))
        suit = normalize_suit(card.get("suit"))
        if not rank or not suit:
            continue
        result.append({"rank": rank, "suit": f"{suit}\ufe0f"})
    return result


# =====================================================================
# ПАРСИНГ ИГРЫ
# =====================================================================

def parse_game_data(game_id, raw_data):
    if raw_data is None:
        return None
    player_cards, dealer_cards = find_cards_recursive(raw_data)
    player_cards = clean_cards(player_cards)
    dealer_cards = clean_cards(dealer_cards)
    if not player_cards:
        p2, d2 = find_card_lists_fallback(raw_data)
        if p2:
            player_cards = clean_cards(p2)
        if d2:
            dealer_cards = clean_cards(d2)
    if not player_cards:
        return None
    now = datetime.now(MOSCOW_TZ)
    state = deep_find_value(raw_data, {"state", "STATE", "status", "STATUS"})
    if state is not None:
        state = str(state)
    player_score = deep_find_value(raw_data, {"player_score", "playerscore", "p_score", "pscore", "scorep", "PScore"})
    dealer_score = deep_find_value(raw_data, {"dealer_score", "dealerscore", "d_score", "dscore", "scored", "DScore"})
    try:
        if player_score is not None:
            player_score = int(player_score)
    except:
        player_score = None
    try:
        if dealer_score is not None:
            dealer_score = int(dealer_score)
    except:
        dealer_score = None
    player_suits = [card["suit"] for card in player_cards]
    player_ranks = [card["rank"] for card in player_cards]
    dealer_suits = [card["suit"] for card in dealer_cards]
    dealer_ranks = [card["rank"] for card in dealer_cards]
    all_cards = []
    sequence = []
    position = 1
    max_len = max(len(player_cards), len(dealer_cards))
    for i in range(max_len):
        if i < len(player_cards):
            card = player_cards[i]
            all_cards.append(card)
            sequence.append({"position": position, "who": "P", "rank": card["rank"], "suit": card["suit"]})
            position += 1
        if i < len(dealer_cards):
            card = dealer_cards[i]
            all_cards.append(card)
            sequence.append({"position": position, "who": "D", "rank": card["rank"], "suit": card["suit"]})
            position += 1
    all_suits = [card["suit"] for card in all_cards]
    all_ranks = [card["rank"] for card in all_cards]
    return {
        "game_id": str(game_id),
        "timestamp_msk": now.strftime("%H:%M:%S.%f")[:-3],
        "state": state,
        "player_score": player_score,
        "dealer_score": dealer_score,
        "player_cards": player_cards,
        "dealer_cards": dealer_cards,
        "player_suits": player_suits,
        "player_ranks": player_ranks,
        "dealer_suits": dealer_suits,
        "dealer_ranks": dealer_ranks,
        "all_suits": all_suits,
        "all_ranks": all_ranks,
        "sequence": sequence,
        "total_cards": len(all_cards),
        "first_player_card": player_cards[0],
        "id_last_digit": str(game_id)[-1],
    }
    # =====================================================================
# JSON
# =====================================================================

def load_json_file(filename, default):
    try:
        if not os.path.exists(filename):
            return default
        with open(filename, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data
    except Exception as e:
        print(f"⚠️ Ошибка чтения {filename}: {e}", flush=True)
        return default


def atomic_save_json(filename, data):
    temp_file = filename + ".tmp"
    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(temp_file, filename)
        return True
    except Exception as e:
        print(f"❌ Ошибка сохранения {filename}: {e}", flush=True)
        try:
            if os.path.exists(temp_file):
                os.remove(temp_file)
        except:
            pass
        return False


def load_history():
    history = load_json_file(DATA_FILE, [])
    if not isinstance(history, list):
        history = []
    if len(history) > MAX_HISTORY_GAMES:
        history = history[-MAX_HISTORY_GAMES:]
        atomic_save_json(DATA_FILE, history)
    return history


def load_predictions():
    data = load_json_file(PREDICTIONS_FILE, [])
    if not isinstance(data, list):
        data = []
    return data


history = load_history()
predictions = load_predictions()

print(f"📚 Загружено игр: {len(history)}", flush=True)
print(f"🔮 Загружено прогнозов: {len(predictions)}", flush=True)


# =====================================================================
# ПОИСК ИГРЫ ПО ID
# =====================================================================

def find_game_index(history_data, game_id):
    game_id = str(game_id)
    for i, game in enumerate(history_data):
        if str(game.get("game_id", "")) == game_id:
            return i
    return -1


def game_exists(history_data, game_id):
    return find_game_index(history_data, game_id) != -1


# =====================================================================
# ОБНОВЛЕНИЕ ЗАПИСИ ИГРЫ
# =====================================================================

def merge_game_records(old, new):
    merged = dict(old)
    for key, value in new.items():
        if value is None:
            continue
        if key in {"player_cards", "dealer_cards", "player_suits", "player_ranks",
                   "dealer_suits", "dealer_ranks", "all_suits", "all_ranks", "sequence"}:
            continue
        merged[key] = value
    old_player = old.get("player_cards", [])
    new_player = new.get("player_cards", [])
    if len(new_player) >= len(old_player):
        merged["player_cards"] = new_player
        merged["player_suits"] = new.get("player_suits", [])
        merged["player_ranks"] = new.get("player_ranks", [])
    old_dealer = old.get("dealer_cards", [])
    new_dealer = new.get("dealer_cards", [])
    if len(new_dealer) >= len(old_dealer):
        merged["dealer_cards"] = new_dealer
        merged["dealer_suits"] = new.get("dealer_suits", [])
        merged["dealer_ranks"] = new.get("dealer_ranks", [])
    player_cards = merged.get("player_cards", [])
    dealer_cards = merged.get("dealer_cards", [])
    all_cards = []
    sequence = []
    position = 1
    max_len = max(len(player_cards), len(dealer_cards))
    for i in range(max_len):
        if i < len(player_cards):
            card = player_cards[i]
            all_cards.append(card)
            sequence.append({"position": position, "who": "P", "rank": card.get("rank"), "suit": card.get("suit")})
            position += 1
        if i < len(dealer_cards):
            card = dealer_cards[i]
            all_cards.append(card)
            sequence.append({"position": position, "who": "D", "rank": card.get("rank"), "suit": card.get("suit")})
            position += 1
    merged["all_suits"] = [c.get("suit") for c in all_cards if c.get("suit")]
    merged["all_ranks"] = [c.get("rank") for c in all_cards if c.get("rank")]
    merged["sequence"] = sequence
    merged["total_cards"] = len(all_cards)
    if player_cards:
        merged["first_player_card"] = player_cards[0]
    return merged


def add_or_update_game(history_data, game):
    game_id = str(game["game_id"])
    index = find_game_index(history_data, game_id)
    if index != -1:
        old_game = history_data[index]
        merged = merge_game_records(old_game, game)
        old_json = json.dumps(old_game, ensure_ascii=False, sort_keys=True)
        new_json = json.dumps(merged, ensure_ascii=False, sort_keys=True)
        if old_json != new_json:
            history_data[index] = merged
            atomic_save_json(DATA_FILE, history_data)
            print(f"🔄 Игра обновлена | ID={game_id} | P={len(merged.get('player_cards', []))} | D={len(merged.get('dealer_cards', []))}", flush=True)
        return False
    history_data.append(game)
    if len(history_data) > MAX_HISTORY_GAMES:
        remove_count = len(history_data) - MAX_HISTORY_GAMES
        del history_data[:remove_count]
        print(f"♻️ Удалены самые старые игры: {remove_count}", flush=True)
    atomic_save_json(DATA_FILE, history_data)
    first_card = game.get("first_player_card")
    print(f"💾 Новая игра сохранена | ID={game_id} | последняя цифра={game_id[-1]} | P1={card_to_text(first_card)} | история={len(history_data)}/{MAX_HISTORY_GAMES}", flush=True)
    return True


# =====================================================================
# ПОИСК ПРЕДЫДУЩЕЙ ИГРЫ ПО ПОСЛЕДНИМ ЦИФРАМ ID
# =====================================================================

def find_last_same_digit_game(history, current_game_id, digits=1):
    current_game_id = str(current_game_id)
    target_digit = current_game_id[-digits:]
    
    print(f"🔍 Ищу игру с цифрой {target_digit} ({digits} цифр) в истории", flush=True)
    
    for game in history:
        old_game_id = str(game.get("game_id", "")).strip()
        if not old_game_id or old_game_id == current_game_id:
            continue
        if old_game_id[-digits:] != target_digit:
            continue
        p1 = None
        if not p1:
            cards = game.get("player_cards", [])
            if cards:
                p1 = cards[0]
        if not p1:
            p1 = game.get("first_player_card")
        if not p1:
            for card in game.get("sequence", []):
                if card.get("position") == 1 and card.get("who") == "P":
                    p1 = card
                    break
        if p1:
            rank = normalize_rank(p1.get("rank"))
            suit = normalize_suit(p1.get("suit"))
            if rank and suit:
                print(f"✅ Найдена игра {old_game_id} с P1={rank}{suit}", flush=True)
                return {
                    "game_id": old_game_id,
                    "first_player_card": {"rank": rank, "suit": f"{suit}\ufe0f"}
                }
    
    print(f"❌ Игра с цифрой {target_digit} не найдена", flush=True)
    return None


# =====================================================================
# ПРОВЕРКА АКТИВНОГО ПРОГНОЗА
# =====================================================================

def has_pending_prediction():
    for p in predictions:
        if p.get("status") == "pending":
            return True
    return False


# =====================================================================
# ПАРСИНГ КАРТ ИЗ КАНАЛА СТАТИСТИКИ
# =====================================================================

def parse_cards_from_message(text):
    game_match = re.search(r'#N(\d+)', text)
    if not game_match:
        return None
    game_number = game_match.group(1)
    cards_raw = re.findall(r'([AJQK2-10]+)([♠♣♦♥])', text)
    if not cards_raw:
        return None
    cards = []
    for rank, suit in cards_raw:
        rank = normalize_rank(rank)
        suit = normalize_suit(suit)
        if rank and suit:
            cards.append({"rank": rank, "suit": f"{suit}\ufe0f"})
    return {"game_number": game_number, "cards": cards}


# =====================================================================
# ПОЛУЧЕНИЕ СООБЩЕНИЙ ИЗ КАНАЛА СТАТИСТИКИ
# =====================================================================

def get_channel_messages(chat_id, limit=20):
    if not chat_id:
        return []
    url = f"{TELEGRAM_API}/getUpdates"
    try:
        response = SESSION.get(url, params={"chat_id": chat_id, "limit": limit}, timeout=10)
        data = response.json()
        if data.get("ok"):
            return data.get("result", [])
        print(f"❌ Ошибка получения сообщений: {data}", flush=True)
    except Exception as e:
        print(f"❌ Ошибка: {e}", flush=True)
    return []
    # =====================================================================
# СОЗДАНИЕ ПАРНОГО ПРОГНОЗА
# =====================================================================

def get_paired_prediction(card):
    if not card:
        return None
    rank = normalize_rank(card.get("rank"))
    suit = normalize_suit(card.get("suit"))
    if not rank or not suit:
        return None
    if rank not in TARGET_RANKS:
        return None
    pair = SUIT_PAIRS.get(suit)
    if not pair:
        return None
    return {
        "rank": rank,
        "suit": f"{suit}\ufe0f",
        "pair_suit": f"{pair}\ufe0f",
        "display": f"{rank}{suit}\ufe0f{pair}\ufe0f"
    }


# =====================================================================
# ЕСТЬ ЛИ УЖЕ ПРОГНОЗ НА ИГРУ
# =====================================================================

def prediction_already_exists(predictions_data, target_game_id):
    target_game_id = str(target_game_id)
    for prediction in predictions_data:
        if str(prediction.get("target_game_id", "")) == target_game_id:
            return True
    return False


# =====================================================================
# СОЗДАНИЕ ПРОГНОЗА (ОСНОВНОЙ + ТЕСТОВЫЙ)
# =====================================================================

def create_prediction_for_game(current_game_id, current_game_number):
    current_game_id = str(current_game_id)
    
    if prediction_already_exists(predictions, current_game_id):
        return None
    
    # ОСНОВНОЙ (1 цифра)
    main_game = find_last_same_digit_game(history, current_game_id, digits=1)
    main_prediction = None
    main_card_text = None
    if main_game:
        first_card = main_game.get("first_player_card")
        if first_card:
            main_prediction = get_paired_prediction(first_card)
            main_card_text = card_to_text(first_card)
    
    # ТЕСТОВЫЙ (2 цифры)
    test_game = find_last_same_digit_game(history, current_game_id, digits=2)
    test_prediction = None
    test_card_text = None
    if test_game:
        first_card = test_game.get("first_player_card")
        if first_card:
            test_prediction = get_paired_prediction(first_card)
            test_card_text = card_to_text(first_card)
    
    if not main_prediction:
        print(f"⏭️ Нет данных для основного прогноза ID={current_game_id}", flush=True)
        return None
    
    now = datetime.now(MOSCOW_TZ)
    
    entry = {
        "target_game_id": current_game_id,
        "target_number": current_game_number,
        "main_source_id": main_game.get("game_id") if main_game else None,
        "main_source_card": main_card_text,
        "main_rank": main_prediction["rank"],
        "main_suit": main_prediction["suit"],
        "main_pair_suit": main_prediction["pair_suit"],
        "main_predicted": main_prediction["display"],
        "test_source_id": test_game.get("game_id") if test_game else None,
        "test_source_card": test_card_text,
        "test_rank": test_prediction["rank"] if test_prediction else None,
        "test_suit": test_prediction["suit"] if test_prediction else None,
        "test_pair_suit": test_prediction["pair_suit"] if test_prediction else None,
        "test_predicted": test_prediction["display"] if test_prediction else "Нет данных",
        "status": "pending",
        "created_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "message_id": None,
        "result": None,
        "result_game_id": None,
    }
    
    predictions.append(entry)
    atomic_save_json(PREDICTIONS_FILE, predictions)
    
    print(f"🔮 ПРОГНОЗ СОЗДАН | #N{current_game_number} | ID={current_game_id}", flush=True)
    print(f"   Основной: {main_prediction['display']} (источник {main_game.get('game_id')})", flush=True)
    if test_prediction:
        print(f"   Тестовый: {test_prediction['display']} (источник {test_game.get('game_id')})", flush=True)
    
    return entry


# =====================================================================
# НОМЕР ИГРЫ
# =====================================================================

def get_game_number():
    now = datetime.now(MOSCOW_TZ)
    start = now.replace(hour=3, minute=0, second=0, microsecond=0)
    if now < start:
        start -= timedelta(days=1)
    minutes = int((now - start).total_seconds() // 60)
    return (minutes % 1440) + 1


# =====================================================================
# TELEGRAM SEND
# =====================================================================

def telegram_send(text, chat_id=None):
    if not chat_id:
        chat_id = CHANNEL_PROGNOZ  
    url = f"{TELEGRAM_API}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        response = SESSION.post(url, json=payload, timeout=10)
        data = response.json()
        if data.get("ok"):
            return data["result"]["message_id"]
        print(f"❌ Telegram send error: {data}", flush=True)
    except Exception as e:
        print(f"❌ Telegram send exception: {e}", flush=True)
    return None


def telegram_edit(message_id, text, chat_id=None):
    if not message_id:
        return False
    if not chat_id:
        chat_id = CHANNEL_PROGNOZ
    url = f"{TELEGRAM_API}/editMessageText"
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        response = SESSION.post(url, json=payload, timeout=10)
        data = response.json()
        if data.get("ok"):
            return True
        print(f"❌ Telegram edit error: {data}", flush=True)
    except Exception as e:
        print(f"❌ Telegram edit exception: {e}", flush=True)
    return False


# =====================================================================
# ТЕКСТ ПРОГНОЗА
# =====================================================================

def make_prediction_message(entry):
    target = entry.get("target_number")
    main_pred = entry.get("main_predicted", "")
    test_pred = entry.get("test_predicted", "")
    
    text = f"🔮 <b>ПРОГНОЗ</b>\n\n"
    text += f"🎯 Игра: <b>#N{target}</b>\n"
    text += f"🃏 Основной: <b>{main_pred}</b>\n"
    if test_pred and test_pred != "Нет данных":
        text += f"🧪 Тестовый: <b>{test_pred}</b>"
    
    return text


# =====================================================================
# ТЕКСТ РЕЗУЛЬТАТА
# =====================================================================

def make_result_message(entry, main_found, test_found=None):
    target = entry.get("target_number")
    main_pred = entry.get("main_predicted", "")
    test_pred = entry.get("test_predicted", "")
    
    main_status = "✅" if main_found else "❌"
    test_status = ""
    if test_pred and test_pred != "Нет данных":
        if test_found is True:
            test_status = "✅"
        elif test_found is False:
            test_status = "❌"
        else:
            test_status = "❓"
    
    text = f"🔮 <b>ПРОГНОЗ</b>\n\n"
    text += f"🎯 Игра: <b>#N{target}</b>\n"
    text += f"🃏 Основной: <b>{main_pred}</b> {main_status}\n"
    if test_pred and test_pred != "Нет данных" and test_status:
        text += f"🧪 Тестовый: <b>{test_pred}</b> {test_status}"
    
    return text
    # =====================================================================
# ПРОВЕРКА КАРТЫ НА СОВПАДЕНИЕ
# =====================================================================

def card_matches_prediction(card, prediction):
    if not card or not prediction:
        return False
    rank = normalize_rank(card.get("rank"))
    suit = normalize_suit(card.get("suit"))
    predicted_rank = normalize_rank(prediction.get("rank"))
    suit1 = normalize_suit(prediction.get("suit"))
    suit2 = normalize_suit(prediction.get("pair_suit"))
    if not rank or not suit:
        return False
    if rank != predicted_rank:
        return False
    if suit == suit1 or suit == suit2:
        return True
    return False


def game_matches_prediction(game, prediction):
    if not game:
        return False
    for card in game.get("player_cards", []):
        if card_matches_prediction(card, prediction):
            return True
    for card in game.get("dealer_cards", []):
        if card_matches_prediction(card, prediction):
            return True
    return False


def check_prediction_with_cards(entry, cards):
    if not cards:
        return None
    main_rank = entry.get("main_rank")
    main_suit = entry.get("main_suit")
    main_pair = entry.get("main_pair_suit")
    main_found = False
    for card in cards:
        rank = card.get("rank")
        suit = card.get("suit")
        if rank == main_rank and (suit == main_suit or suit == main_pair):
            main_found = True
            break
    test_found = None
    test_rank = entry.get("test_rank")
    test_suit = entry.get("test_suit")
    test_pair = entry.get("test_pair_suit")
    if test_rank and test_suit:
        test_found = False
        for card in cards:
            rank = card.get("rank")
            suit = card.get("suit")
            if rank == test_rank and (suit == test_suit or suit == test_pair):
                test_found = True
                break
    return {"main_found": main_found, "test_found": test_found}


# =====================================================================
# ПРОВЕРКА РЕЗУЛЬТАТОВ ИЗ КАНАЛА СТАТИСТИКИ
# =====================================================================

def check_results_from_channel():
    if not CHANNEL_STATS:
        print("⚠️ CHANNEL_STATS не задан, проверка по каналу статистики отключена", flush=True)
        return
    
    messages = get_channel_messages(CHANNEL_STATS, limit=30)
    changed = False
    
    for entry in predictions:
        if entry.get("status") != "pending":
            continue
        
        target_number = str(entry.get("target_number", ""))
        if not target_number:
            continue
        
        # Ищем сообщение с этим номером игры
        found_message = None
        for msg in messages:
            text = msg.get("message", {}).get("text", "")
            if f"#N{target_number}" in text:
                found_message = text
                break
        
        if not found_message:
            continue
        
        parsed = parse_cards_from_message(found_message)
        if not parsed or not parsed.get("cards"):
            continue
        
        result = check_prediction_with_cards(entry, parsed["cards"])
        if not result:
            continue
        
        main_found = result.get("main_found", False)
        test_found = result.get("test_found")
        
        if main_found:
            entry["status"] = "win"
            entry["result"] = "win"
            changed = True
            print(f"✅ ОСНОВНОЙ ЗАШЕЛ! #N{target_number} | {entry.get('main_predicted')}", flush=True)
        else:
            entry["status"] = "lose"
            entry["result"] = "lose"
            changed = True
            print(f"❌ ОСНОВНОЙ НЕ ЗАШЕЛ! #N{target_number} | {entry.get('main_predicted')}", flush=True)
        
        message_id = entry.get("message_id")
        if message_id:
            text = make_result_message(entry, main_found, test_found)
            telegram_edit(message_id, text)
    
    if changed:
        atomic_save_json(PREDICTIONS_FILE, predictions)


# =====================================================================
# API — АКТИВНЫЕ ИГРЫ
# =====================================================================

def get_active_games():
    url = (
        f"{BASE_URL}/service-api/main-live-feed/v3/games1x2"
        "?cfView=3&count=40&fcountry=190&gr=415&grMode=4&lng=ru&ref=7&selectedMs=10.146.1643503"
    )
    try:
        response = SESSION.get(url, timeout=10)
        print(f"🌐 API games1x2 status: {response.status_code}", flush=True)
        if response.status_code != 200:
            return []
        data = response.json()
        if isinstance(data, list):
            games = data
        elif isinstance(data, dict) and "Value" in data:
            games = data.get("Value", [])
        else:
            return []
        result = []
        for game in games:
            if not isinstance(game, dict):
                continue
            liga = game.get("liga", {})
            if not isinstance(liga, dict):
                liga = {}
            liga_id = liga.get("id")
            game_id = game.get("id")
            if not game_id:
                continue
            if str(liga_id) != str(LEAGUE_ID):
                continue
            result.append(game)
        return result
    except Exception as e:
        print(f"❌ Ошибка get_active_games: {e}", flush=True)
        return []


# =====================================================================
# API — ДАННЫЕ КОНКРЕТНОЙ ИГРЫ
# =====================================================================

def get_game_data(game_id):
    url = (
        f"{BASE_URL}/service-api/LiveFeed/GetGameZip"
        f"?id={game_id}&isSubGames=true&GroupEvents=true&countevents=250"
        "&grMode=4&partner=7&topGroups=&country=190&marketType=1&isNewBuilder=true"
    )
    try:
        response = SESSION.get(url, timeout=7)
        if response.status_code == 200:
            return response.json()
        print(f"⚠️ GetGameZip ID={game_id} status={response.status_code}", flush=True)
    except Exception as e:
        print(f"❌ Ошибка игры {game_id}: {e}", flush=True)
    return None


# =====================================================================
# ОБРАБОТКА ОДНОЙ ИГРЫ
# =====================================================================

def process_game(active_game, seen_this_cycle, skip_prediction=False):
    game_id = active_game.get("id")
    if not game_id:
        return
    game_id = str(game_id)
    if game_id in seen_this_cycle:
        return
    seen_this_cycle.add(game_id)
    last_digit = game_id[-1]
    print(f"🔍 API игра | ID={game_id} | последняя цифра={last_digit}", flush=True)
    is_new = not game_exists(history, game_id)
    game_number = get_game_number()
    
    if is_new:
        print(f"🆕 НОВАЯ ИГРА В ЛОББИ! ID={game_id}", flush=True)
        
        if skip_prediction:
            print(f"🚫 ПРОГНОЗ ПРОПУЩЕН | #N{game_number} | ID={game_id} | дубликат цифры {last_digit}", flush=True)
        else:
            if has_pending_prediction():
                print(f"⏳ Есть активный прогноз, пропускаем ID={game_id}", flush=True)
            else:
                prediction = create_prediction_for_game(game_id, game_number)
                if prediction:
                    message = make_prediction_message(prediction)
                    message_id = telegram_send(message)
                    if message_id:
                        prediction["message_id"] = message_id
                        atomic_save_json(PREDICTIONS_FILE, predictions)
                    print(f"🔮 ПРОГНОЗ ОТПРАВЛЕН СРАЗУ! #N{game_number} | ID={game_id} | {prediction.get('main_predicted')}", flush=True)
                else:
                    print(f"⏭️ Нет данных для прогноза ID={game_id} (цифра {last_digit})", flush=True)
    
    raw_data = get_game_data(game_id)
    if raw_data is None:
        print(f"⏳ ID={game_id} | данные игры пока недоступны", flush=True)
        return
    parsed = parse_game_data(game_id, raw_data)
    if parsed is None:
        print(f"⏳ ID={game_id} | игра ещё не началась", flush=True)
        return
    add_or_update_game(history, parsed)


# =====================================================================
# ФИЛЬТР БУДУЩИХ ИГР
# =====================================================================

def get_duplicate_last_digits(active_games):
    digit_counts = {}
    for game in active_games:
        game_id = game.get("id")
        if not game_id:
            continue
        game_id = str(game_id)
        if not game_id:
            continue
        last_digit = game_id[-1]
        digit_counts[last_digit] = digit_counts.get(last_digit, 0) + 1
    return {digit for digit, count in digit_counts.items() if count >= 2}


# =====================================================================
# ОЧИСТКА СТАРЫХ ПРОГНОЗОВ
# =====================================================================

def cleanup_predictions():
    global predictions
    if len(predictions) <= 1000:
        return
    predictions = predictions[-1000:]
    atomic_save_json(PREDICTIONS_FILE, predictions)


# =====================================================================
# MAIN
# =====================================================================

def main():
    print("", flush=True)
    print("==================================================", flush=True)
    print("🚀 OLD BOT — 21 CLASSIC", flush=True)
    print("🔢 Логика: последняя цифра API ID (основной)", flush=True)
    print("🔢 Логика: две последние цифры API ID (тестовый)", flush=True)
    print("🃏 Источник: первая карта игрока", flush=True)
    print(f"📚 История: последние {MAX_HISTORY_GAMES} игр", flush=True)
    print(f"🎯 Проверка: {DOGON_GAMES} игры (догон)", flush=True)
    if CHANNEL_STATS:  # <--- БЫЛО STATS_CHAT_ID, ИСПРАВИЛ НА CHANNEL_STATS
        print(f"📊 Проверка результатов из канала статистики", flush=True)
    print("==================================================", flush=True)
    
    while True:
        cycle_start = time.time()
        try:
            active_games = get_active_games()
            print(f"📡 API получено игр: {len(active_games)}", flush=True)
            
            duplicate_digits = get_duplicate_last_digits(active_games)
            if duplicate_digits:
                print(f"🚫 Запрещённые цифры будущих игр: {sorted(duplicate_digits)}", flush=True)
            else:
                print("✅ Одинаковых последних цифр среди будущих игр нет", flush=True)
            
            seen_this_cycle = set()
            for active_game in active_games:
                try:
                    game_id = active_game.get("id")
                    if not game_id:
                        continue
                    game_id = str(game_id)
                    last_digit = game_id[-1]
                    skip_prediction = last_digit in duplicate_digits
                    process_game(active_game, seen_this_cycle, skip_prediction)
                except Exception as e:
                    game_id = active_game.get("id", "?")
                    print(f"❌ Ошибка обработки ID={game_id}: {e}", flush=True)
            
            check_results_from_channel()
            cleanup_predictions()
            
            elapsed = time.time() - cycle_start
            sleep_time = max(0.1, POLL_INTERVAL - elapsed)
            time.sleep(sleep_time)
            
        except KeyboardInterrupt:
            print("\n🛑 Бот остановлен", flush=True)
            break
        except Exception as e:
            print(f"❌ Критическая ошибка: {e}", flush=True)
            time.sleep(3)


if __name__ == "__main__":
    main()