import os
import sys
import json
import time
import requests
import re
from datetime import datetime, timedelta

import pytz


# ==================================================
# ENV
# ==================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    BOT_TOKEN = os.getenv("BOT_TOKEN_PROGNOZ")

CHANNEL_PROGNOZ = os.getenv("CHAT_ID_21")
if not CHANNEL_PROGNOZ:
    CHANNEL_PROGNOZ = os.getenv("CHANNEL_PROGNOZ")

if not BOT_TOKEN:
    print("❌ BOT_TOKEN не задан!", flush=True)
    sys.exit(1)

if not CHANNEL_PROGNOZ:
    print("❌ CHANNEL_PROGNOZ не задан!", flush=True)
    sys.exit(1)


# ==================================================
# CONFIG
# ==================================================

MOSCOW_TZ = pytz.timezone("Europe/Moscow")

DATA_FILE = "twentyone_data_full.json"
PREDICTIONS_FILE = "twentyone_predictions.json"

MAX_HISTORY_GAMES = 300
MAX_PREDICTIONS = 1000

DOGON_GAMES = 4

BASE_URL = "https://1xlite-36553.pro"

LEAGUE_ID = 1643503

POLL_INTERVAL = 2.0

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"


# ==================================================
# HTTP
# ==================================================

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": (
        f"{BASE_URL}/ru/live/twentyone/"
        "1643503-twentyone-game"
    )
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


# ==================================================
# GLOBALS
# ==================================================

history_data = []
predictions = []

processed_games = set()

# Активные игры.
# Именно этот кэш не даёт потерять игру,
# пока она меняется в API.
active_games_cache = {}

last_prediction_time = 0


# ==================================================
# JSON
# ==================================================

def load_json_file(filename, default):

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


def atomic_save_json(filename, data):

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
            f"⚠️ Ошибка сохранения "
            f"{filename}: {e}",
            flush=True
        )

        try:

            if os.path.exists(tmp):
                os.remove(tmp)

        except Exception:
            pass

        return False


# ==================================================
# LOAD HISTORY
# ==================================================

def load_history():

    data = load_json_file(
        DATA_FILE,
        []
    )

    if not isinstance(data, list):
        return []

    result = []
    seen_ids = set()

    for item in data:

        if not isinstance(item, dict):
            continue

        game_id = item.get("game_id")

        if not game_id:
            continue

        game_id = str(game_id)

        if game_id in seen_ids:
            continue

        seen_ids.add(game_id)

        result.append(item)

    result.sort(
        key=lambda x: int(
            x.get("game_number", 0)
        )
    )

    if len(result) > MAX_HISTORY_GAMES:
        result = result[-MAX_HISTORY_GAMES:]

    return result


# ==================================================
# LOAD PREDICTIONS
# ==================================================

def load_predictions():

    data = load_json_file(
        PREDICTIONS_FILE,
        []
    )

    if not isinstance(data, list):
        return []

    return data


# ==================================================
# NORMALIZE CARD
# ==================================================

def normalize_suit(suit):

    if suit is None:
        return None

    suit = str(suit)

    suit = suit.replace(
        "\ufe0f",
        ""
    )

    mapping = {
        "♠": "♠",
        "♣": "♣",
        "♦": "♦",
        "♥": "♥"
    }

    return mapping.get(suit)


def normalize_rank(rank):

    if rank is None:
        return None

    rank = str(rank).strip().upper()

    rank = rank.replace("А", "A")

    allowed = {
        "2", "3", "4", "5",
        "6", "7", "8", "9",
        "10", "J", "Q", "K", "A"
    }

    if rank in allowed:
        return rank

    return None


def normalize_card_string(card):

    if not card:
        return None

    card = str(card)

    match = re.match(
        r"(10|[2-9AJQKА])([♠♣♦♥])",
        card
    )

    if not match:
        return None

    rank = normalize_rank(
        match.group(1)
    )

    suit = normalize_suit(
        match.group(2)
    )

    if not rank or not suit:
        return None

    return f"{rank}{suit}"


# ==================================================
# GAME NUMBER FALLBACK
# ==================================================

def get_game_number_fallback():

    now = datetime.now(MOSCOW_TZ)

    start = now.replace(
        hour=3,
        minute=0,
        second=0,
        microsecond=0
    )

    if now < start:
        start -= timedelta(days=1)

    diff_minutes = (
        now - start
    ).total_seconds() / 60

    return int(diff_minutes) % 1440 + 1


# ==================================================
# GET ACTIVE GAMES
# ==================================================

def get_active_games():

    try:

        url = (
            f"{BASE_URL}"
            "/service-api/main-live-feed/v3/games1x2"
            "?cfView=3"
            "&count=40"
            "&fcountry=190"
            "&gr=415"
            "&grMode=4"
            "&lng=ru"
            "&ref=7"
            "&selectedMs=10.146.1643503"
        )

        response = SESSION.get(
            url,
            timeout=10
        )

        if response.status_code != 200:

            print(
                f"⚠️ games1x2 HTTP "
                f"{response.status_code}",
                flush=True
            )

            return []

        data = response.json()

        if isinstance(data, list):
            games = data

        elif (
            isinstance(data, dict)
            and isinstance(
                data.get("Value"),
                list
            )
        ):
            games = data.get("Value", [])

        else:
            return []

        result = []

        for game in games:

            if not isinstance(game, dict):
                continue

            league = (
                game.get("liga", {})
            )

            league_id = None

            if isinstance(league, dict):
                league_id = league.get("id")

            if league_id != LEAGUE_ID:
                continue

            game_id = game.get("id")

            if not game_id:
                continue

            result.append(game)

        return result

    except Exception as e:

        print(
            f"❌ Ошибка получения игр: {e}",
            flush=True
        )

        return []


# ==================================================
# GET GAME DATA
# ==================================================

def get_game_data(game_id):

    url = (
        f"{BASE_URL}"
        "/service-api/LiveFeed/GetGameZip"
    )

    params = {
        "id": game_id,
        "isSubGames": "true",
        "GroupEvents": "true",
        "countevents": 250,
        "grMode": 4,
        "partner": 7,
        "topGroups": "",
        "country": 190,
        "marketType": 1,
        "isNewBuilder": "true"
    }

    try:

        response = SESSION.get(
            url,
            params=params,
            timeout=8
        )

        if response.status_code != 200:

            print(
                f"⚠️ GetGameZip HTTP "
                f"{response.status_code} "
                f"для {game_id}",
                flush=True
            )

            return None

        return response.json()

    except Exception as e:

        print(
            f"❌ Ошибка игры "
            f"{game_id}: {e}",
            flush=True
        )

        return None


# ==================================================
# PARSE CARDS
# ==================================================

def get_cards(value_str):

    if not value_str:
        return []

    if value_str == "[]":
        return []

    try:

        if isinstance(value_str, str):

            cards = json.loads(
                value_str
            )

        elif isinstance(value_str, list):

            cards = value_str

        else:
            return []

        result = []

        suit_map = {
            0: "♠",
            1: "♣",
            2: "♦",
            3: "♥"
        }

        rank_map = {
            "1": "A",
            "2": "2",
            "3": "3",
            "4": "4",
            "5": "5",
            "6": "6",
            "7": "7",
            "8": "8",
            "9": "9",
            "10": "10",
            "11": "J",
            "12": "Q",
            "13": "K",
            "14": "A"
        }

        for card in cards:

            if not isinstance(card, dict):
                continue

            cs = card.get("CS")
            cv = card.get("CV")

            try:
                cv = int(cv)
            except Exception:
                pass

            rank = rank_map.get(
                str(cv),
                str(cv)
            )

            suit = suit_map.get(cs)

            if not rank or not suit:
                continue

            card_string = f"{rank}{suit}"

            # Не убираем одинаковые карты.
            # Колода может содержать одинаковый ранг/масть
            # в разных игровых данных.
            result.append(card_string)

        return result

    except Exception:

        return []


# ==================================================
# CALCULATE SCORE
#
# ТВОИ ПРАВИЛА:
# J = 2
# Q = 3
# K = 4
# A = 11
# 2 туза = 21
# ==================================================

def calculate_score(cards):

    if not cards:
        return 0

    # Два туза = 21
    if (
        len(cards) == 2
        and all(
            card.startswith("A")
            for card in cards
        )
    ):
        return 21

    score = 0

    for card in cards:

        if not card:
            continue

        card = str(card)

        if card.startswith("10"):
            score += 10

        elif card.startswith("2"):
            score += 2

        elif card.startswith("3"):
            score += 3

        elif card.startswith("4"):
            score += 4

        elif card.startswith("5"):
            score += 5

        elif card.startswith("6"):
            score += 6

        elif card.startswith("7"):
            score += 7

        elif card.startswith("8"):
            score += 8

        elif card.startswith("9"):
            score += 9

        elif card.startswith("J"):
            score += 2

        elif card.startswith("Q"):
            score += 3

        elif card.startswith("K"):
            score += 4

        elif card.startswith("A"):
            score += 11

    return score


# ==================================================
# PARSE API GAME
#
# ТОЧНО ПО ЛОГИКЕ РАБОЧЕГО СКАНЕРА:
#
# Value
#   └── SC
#        └── S
#             ├── P1
#             ├── P2
#             └── STATE
# ==================================================

def parse_api_game(game_id, data):

    if not isinstance(data, dict):
        return None

    value = data.get("Value")

    if not isinstance(value, dict):
        return None

    sc = value.get("SC", {})

    if not isinstance(sc, dict):
        return None

    player_cards = []
    dealer_cards = []
    state = None

    for item in sc.get("S", []):

        if not isinstance(item, dict):
            continue

        key = item.get("Key")
        item_value = item.get(
            "Value",
            "[]"
        )

        if key == "P1":

            player_cards = get_cards(
                item_value
            )

        elif key == "P2":

            dealer_cards = get_cards(
                item_value
            )

        elif key == "STATE":

            state = str(item_value)

    if not player_cards:
        return None

    # Номер игры из API
    raw_game_num = (
        value.get("DI")
        or value.get("TN")
    )

    if raw_game_num:

        match = re.search(
            r"\d+",
            str(raw_game_num)
        )

        if match:
            game_number = int(
                match.group()
            )
        else:
            game_number = (
                get_game_number_fallback()
            )

    else:
        game_number = (
            get_game_number_fallback()
        )

    player_score = calculate_score(
        player_cards
    )

    dealer_score = calculate_score(
        dealer_cards
    )

    return {
        "game_id": str(game_id),

        "game_number": game_number,

        "player_cards": player_cards,

        "dealer_cards": dealer_cards,

        "player_score": player_score,

        "dealer_score": dealer_score,

        "state": state,

        "updated_at": datetime.now(
            MOSCOW_TZ
        ).isoformat()
    }


# ==================================================
# GAME FINISHED
#
# ЛОГИКА ИЗ ТВОЕГО РАБОЧЕГО СКАНЕРА
# ==================================================

def is_game_finished(
    state,
    player_cards,
    dealer_cards,
    p_score,
    d_score
):

    # Два туза
    if (
        len(player_cards) == 2
        and p_score == 21
    ):
        return True

    if (
        dealer_cards
        and len(dealer_cards) == 2
        and d_score == 21
    ):
        return True

    # Финальное состояние
    if state == "5":
        return True

    if state == "4":

        if p_score == 21:
            return True

        if (
            dealer_cards
            and d_score in (20, 21)
        ):
            return True

        return False

    # Перебор
    if p_score > 21:
        return True

    if dealer_cards and d_score > 21:
        return True

    # 5 карт
    if len(player_cards) >= 5:
        return True

    if (
        dealer_cards
        and len(dealer_cards) >= 5
    ):
        return True

    return False


# ==================================================
# GAME EXISTS
# ==================================================

def game_exists(game_id):

    game_id = str(game_id)

    for game in history_data:

        if str(
            game.get("game_id")
        ) == game_id:

            return True

    return False


# ==================================================
# SAVE GAME
# ==================================================

def save_new_game(game):

    global history_data

    if not game:
        return None

    game_id = str(
        game.get("game_id")
    )

    if game_exists(game_id):
        return None

    history_data.append(game)

    history_data.sort(
        key=lambda x: int(
            x.get("game_number", 0)
        )
    )

    if len(history_data) > MAX_HISTORY_GAMES:

        history_data = history_data[
            -MAX_HISTORY_GAMES:
        ]

    atomic_save_json(
        DATA_FILE,
        history_data
    )

    print()
    print(
        "══════════════════════════════════",
        flush=True
    )
    print(
        "💾 ИГРА СОХРАНЕНА",
        flush=True
    )
    print(
        f"🎮 #N{game['game_number']}",
        flush=True
    )
    print(
        f"🆔 {game_id}",
        flush=True
    )
    print(
        f"👤 Игрок: "
        f"{game['player_score']} "
        f"{game['player_cards']}",
        flush=True
    )
    print(
        f"🎩 Дилер: "
        f"{game['dealer_score']} "
        f"{game['dealer_cards']}",
        flush=True
    )
    print(
        "══════════════════════════════════",
        flush=True
    )

    return game


# ==================================================
# BAD GAME
#
# Нельзя:
# - ничья
# - у любой стороны 21
# ==================================================

def is_bad_game(game):

    if not game:
        return True

    p_score = game.get(
        "player_score"
    )

    d_score = game.get(
        "dealer_score"
    )

    if p_score is None or d_score is None:
        return True

    if p_score == d_score:
        return True

    if p_score == 21:
        return True

    if d_score == 21:
        return True

    return False


# ==================================================
# GET GAME BY NUMBER
# ==================================================

def get_game_by_number(number):

    try:
        number = int(number)
    except Exception:
        return None

    for game in history_data:

        try:

            if int(
                game.get(
                    "game_number",
                    0
                )
            ) == number:

                return game

        except Exception:
            continue

    return None


# ==================================================
# CHECK SURROUNDING GAMES
#
# Перед триггером:
# -2
# -1
#
# После триггера:
# +1
# +2
#
# Нигде не должно быть:
# - ничьи
# - 21
#
# Если будущих игр ещё нет —
# ждём, не создаём прогноз раньше времени.
# ==================================================

def surrounding_games_ready(
    trigger_number
):

    required_numbers = [
        trigger_number - 2,
        trigger_number - 1,
        trigger_number + 1,
        trigger_number + 2
    ]

    for number in required_numbers:

        game = get_game_by_number(
            number
        )

        if not game:

            return (
                False,
                False
            )

        if is_bad_game(game):

            return (
                True,
                False
            )

    return (
        True,
        True
    )


# ==================================================
# SECOND DIGIT DIFFERENCE
#
# 27 и 12:
# 7 - 2 = 5
#
# Берём модуль.
# ==================================================

def second_digit_difference(a, b):

    try:

        a = int(a)
        b = int(b)

    except Exception:
        return None

    a_digit = abs(a) % 10
    b_digit = abs(b) % 10

    return abs(
        a_digit - b_digit
    )


# ==================================================
# BUILD TRIGGER PREDICTIONS
#
# УСЛОВИЯ:
#
# 1. У игрока РОВНО 3 карты
# 2. Значение берём с ПЕРВОЙ карты дилера
# 3. Масть берём с ПЕРВОЙ карты игрока
#
# Пример:
#
# Игрок:
# 7♠ 9♣ A♦
#
# Дилер:
# Q♠ 9♦
#
# Значение = Q (первая дилера)
# Масть = ♠ (первая игрока)
#
# Прогноз = Q♠
#
# Смещение 1:
# |последняя цифра игрока -
#  последняя цифра дилера|
#
# Смещение 2:
# |очки игрока - очки дилера|
# ==================================================

def build_trigger_predictions(game):

    if not game:
        return []

    player_cards = game.get("player_cards", [])
    dealer_cards = game.get("dealer_cards", [])

    # 1. У игрока РОВНО 3 карты
    if len(player_cards) != 3:
        return []

    if not dealer_cards:
        return []

    player_score = game.get("player_score")
    dealer_score = game.get("dealer_score")

    if player_score is None or dealer_score is None:
        return []

    # 2. Первая карта дилера — J/Q/K/A (ОБЯЗАТЕЛЬНОЕ УСЛОВИЕ!)
    dealer_first = normalize_card_string(dealer_cards[0])
    if not dealer_first:
        return []

    dealer_match = re.match(r"(10|[2-9AJQK])([♠♣♦♥])", dealer_first)
    if not dealer_match:
        return []

    dealer_rank = dealer_match.group(1)

    # Ранг должен быть J, Q, K или A
    if dealer_rank not in ["J", "Q", "K", "A"]:
        return []  # ❌ НЕ ТРИГГЕР

    # 3. Первая карта игрока -> МАСТЬ
    player_first = normalize_card_string(player_cards[0])
    if not player_first:
        return []

    player_match = re.match(r"(10|[2-9AJQK])([♠♣♦♥])", player_first)
    if not player_match:
        return []

    source_suit = player_match.group(2)      # масть с первой карты игрока
    source_rank = dealer_rank                # ранг с первой карты дилера (уже J/Q/K/A)

    predicted_card = f"{source_rank}{source_suit}"

    # 4. Смещения
    offset_1 = second_digit_difference(player_score, dealer_score)
    offset_2 = abs(int(player_score) - int(dealer_score))

    if offset_1 is None or offset_1 <= 0:
        return []

    if offset_2 <= 0:
        return []

    source_number = int(game.get("game_number"))
    results = []

    target_1 = source_number + offset_1
    results.append({
        "prediction_type": "digits",
        "offset": offset_1,
        "target_number": target_1,
        "predicted_card": predicted_card
    })

    target_2 = source_number + offset_2
    if target_2 != target_1:
        results.append({
            "prediction_type": "score",
            "offset": offset_2,
            "target_number": target_2,
            "predicted_card": predicted_card
        })

    return results


# ==================================================
# PREDICTION EXISTS
# ==================================================

def prediction_exists(
    source_game_id,
    target_number
):

    for entry in predictions:

        if (
            str(
                entry.get("source_game_id")
            ) == str(source_game_id)
            and int(
                entry.get(
                    "target_number",
                    0
                )
            ) == int(target_number)
        ):
            return True

    return False


# ==================================================
# TELEGRAM SEND
# ==================================================

def telegram_send(text):

    try:

        response = SESSION.post(
            f"{TELEGRAM_API}/sendMessage",
            json={
                "chat_id": CHANNEL_PROGNOZ,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            },
            timeout=10
        )

        data = response.json()

        if data.get("ok"):

            return data[
                "result"
            ].get("message_id")

        print(
            f"❌ Telegram send: {data}",
            flush=True
        )

    except Exception as e:

        print(
            f"❌ Telegram send error: {e}",
            flush=True
        )

    return None


# ==================================================
# TELEGRAM EDIT
# ==================================================

def telegram_edit(message_id, text):

    if not message_id:
        return False

    try:

        response = SESSION.post(
            f"{TELEGRAM_API}/editMessageText",
            json={
                "chat_id": CHANNEL_PROGNOZ,
                "message_id": message_id,
                "text": text,
                "parse_mode": "HTML"
            },
            timeout=10
        )

        data = response.json()

        return bool(
            data.get("ok")
        )

    except Exception as e:

        print(
            f"❌ Telegram edit error: {e}",
            flush=True
        )

        return False


# ==================================================
# MAKE PREDICTION MESSAGE
# ==================================================

def make_prediction_message(entry):

    return (
        f"🎯 <b>Игра: "
        f"#N{entry['target_number']}</b>\n\n"

        f"🃏 <b>{entry['predicted_card']}</b>\n\n"

        f"🔥 Триггер: "
        f"#N{entry['source_number']}\n"

        f"👤 Игрок: "
        f"{entry['player_score']} "
        f"({entry['player_cards_text']})\n"

        f"🎩 Дилер: "
        f"{entry['dealer_score']} "
        f"({entry['dealer_cards_text']})\n\n"

        f"⏩ Смещение: "
        f"+{entry['offset']} игр"
    )


# ==================================================
# CREATE PREDICTIONS
# ==================================================

def create_trigger_predictions(game):

    global predictions
    global last_prediction_time

    patterns = build_trigger_predictions(
        game
    )

    if not patterns:
        return

    source_number = int(
        game.get("game_number")
    )

    source_game_id = str(
        game.get("game_id")
    )

    print()
    print(
        "🔥 НАЙДЕН ТРИГГЕР",
        flush=True
    )
    print(
        f"🎮 #N{source_number}",
        flush=True
    )
    print(
        f"👤 Игрок: "
        f"{game['player_score']} "
        f"{game['player_cards']}",
        flush=True
    )
    print(
        f"🎩 Дилер: "
        f"{game['dealer_score']} "
        f"{game['dealer_cards']}",
        flush=True
    )
    print(
        "⏳ Ждём проверку "
        "2 игр после триггера...",
        flush=True
    )

    # Здесь прогнозы НЕ создаём мгновенно.
    # Сначала surrounding_games_ready()
    # подтвердит +1 и +2.
    for pattern in patterns:

        entry = {
            "source_number": source_number,
            "source_game_id": source_game_id,

            "player_score": (
                game["player_score"]
            ),

            "dealer_score": (
                game["dealer_score"]
            ),

            "player_cards": (
                game["player_cards"]
            ),

            "dealer_cards": (
                game["dealer_cards"]
            ),

            "prediction_type": (
                pattern["prediction_type"]
            ),

            "offset": pattern["offset"],

            "target_number": (
                pattern["target_number"]
            ),

            "predicted_card": (
                pattern["predicted_card"]
            ),

            "status": "waiting_validation",

            "message_id": None,

            "original_text": "",

            "result_game": None,

            "found_card": None,

            "current_dogon": 0,

            "created_at": datetime.now(
                MOSCOW_TZ
            ).isoformat()
        }

        # Не создаём дубль
        duplicate = False

        for old in predictions:

            if (
                str(
                    old.get(
                        "source_game_id"
                    )
                ) == source_game_id
                and int(
                    old.get(
                        "target_number",
                        0
                    )
                ) == int(
                    entry["target_number"]
                )
            ):
                duplicate = True
                break

        if not duplicate:

            predictions.append(entry)

    atomic_save_json(
        PREDICTIONS_FILE,
        predictions
    )


# ==================================================
# VALIDATE WAITING PREDICTIONS
#
# Ждём +1/+2 после триггера.
# Потом проверяем:
#
# -2
# -1
# +1
# +2
#
# Нет ничьи / 21
# ==================================================

def validate_waiting_predictions():

    global predictions

    changed = False

    source_ids = set()

    for entry in predictions:

        if (
            entry.get("status")
            == "waiting_validation"
        ):

            source_ids.add(
                str(
                    entry.get(
                        "source_game_id"
                    )
                )
            )

    for source_game_id in source_ids:

        source_game = None

        for game in history_data:

            if (
                str(
                    game.get("game_id")
                ) == source_game_id
            ):
                source_game = game
                break

        if not source_game:
            continue

        source_number = int(
            source_game.get(
                "game_number"
            )
        )

        ready, valid = (
            surrounding_games_ready(
                source_number
            )
        )

        if not ready:
            continue

        for entry in predictions:

            if (
                str(
                    entry.get(
                        "source_game_id"
                    )
                ) != source_game_id
            ):
                continue

            if (
                entry.get("status")
                != "waiting_validation"
            ):
                continue

            if not valid:

                entry["status"] = "cancelled"

                changed = True

                print(
                    f"🚫 ТРИГГЕР ОТМЕНЁН "
                    f"#N{source_number} "
                    f"| рядом ничья или 21",
                    flush=True
                )

                continue

            # ПРОГНОЗ ПОДТВЕРЖДЁН
            entry["status"] = "pending"

            entry["player_cards_text"] = (
                " ".join(
                    entry.get(
                        "player_cards",
                        []
                    )
                )
            )

            entry["dealer_cards_text"] = (
                " ".join(
                    entry.get(
                        "dealer_cards",
                        []
                    )
                )
            )

            message = (
                make_prediction_message(
                    entry
                )
            )

            entry["original_text"] = message

            message_id = telegram_send(
                message
            )

            if message_id:

                entry["message_id"] = (
                    message_id
                )

            changed = True

            print()
            print(
                "══════════════════════════════",
                flush=True
            )
            print(
                "🔮 ПРОГНОЗ ПОДТВЕРЖДЁН",
                flush=True
            )
            print(
                f"🔥 Триггер: "
                f"#N{source_number}",
                flush=True
            )
            print(
                f"🎯 Цель: "
                f"#N{entry['target_number']}",
                flush=True
            )
            print(
                f"🃏 Карта: "
                f"{entry['predicted_card']}",
                flush=True
            )
            print(
                f"⏩ +{entry['offset']} игр",
                flush=True
            )
            print(
                "══════════════════════════════",
                flush=True
            )

    if changed:

        atomic_save_json(
            PREDICTIONS_FILE,
            predictions
        )


# ==================================================
# UPDATE PREDICTION STATUS
# ==================================================

def update_prediction_status(
    entry,
    success,
    found=None
):

    message_id = entry.get(
        "message_id"
    )

    original_text = entry.get(
        "original_text",
        ""
    )

    if not message_id:
        return

    if not original_text:
        return

    lines = original_text.split("\n")

    target = entry.get(
        "target_number"
    )

    if success:

        lines[0] = (
            f"🎯 <b>Игра: "
            f"#N{target} ✅</b>"
        )

        lines.append("")

        lines.append(
            f"✅ ЗАШЛО: "
            f"#N{found['num']}"
        )

        lines.append(
            f"🃏 Выпало: "
            f"{found['card']}"
        )

        lines.append(
            f"🔁 Догон: "
            f"{found['dogon']}"
        )

    else:

        lines[0] = (
            f"🎯 <b>Игра: "
            f"#N{target} ❌</b>"
        )

        lines.append("")

        lines.append(
            f"❌ Не зашло за "
            f"{DOGON_GAMES + 1} игр"
        )

    telegram_edit(
        message_id,
        "\n".join(lines)
    )


# ==================================================
# CHECK PREDICTIONS
# ==================================================

def check_predictions():

    global predictions

    changed = False

    for entry in predictions:

        if entry.get("status") != "pending":
            continue

        target = entry.get(
            "target_number"
        )

        predicted_card = entry.get(
            "predicted_card"
        )

        if not target:
            continue

        if not predicted_card:
            continue

        found = None
        all_available = True

        for dogon in range(
            DOGON_GAMES + 1
        ):

            check_number = (
                int(target) + dogon
            )

            game = get_game_by_number(
                check_number
            )

            if not game:

                all_available = False
                break

            actual_cards = []

            actual_cards.extend(
                game.get(
                    "player_cards",
                    []
                )
            )

            actual_cards.extend(
                game.get(
                    "dealer_cards",
                    []
                )
            )

            if predicted_card in actual_cards:

                found = {
                    "num": check_number,
                    "dogon": dogon,
                    "card": predicted_card
                }

                break

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
                f"✅ ПРОГНОЗ ЗАШЁЛ | "
                f"#N{target} → "
                f"#N{found['num']} | "
                f"{found['card']}",
                flush=True
            )

            update_prediction_status(
                entry,
                True,
                found
            )

            continue

        if not all_available:
            continue

        entry["status"] = "lose"

        entry["current_dogon"] = (
            DOGON_GAMES
        )

        changed = True

        print(
            f"❌ ПРОГНОЗ НЕ ЗАШЁЛ | "
            f"#N{target}",
            flush=True
        )

        update_prediction_status(
            entry,
            False
        )

    if changed:

        atomic_save_json(
            PREDICTIONS_FILE,
            predictions
        )


# ==================================================
# UPDATE ACTIVE GAME
#
# ВАЖНЕЙШАЯ ЧАСТЬ:
# Игра НЕ сохраняется сразу.
# Она хранится и обновляется,
# как в твоём рабочем сканере.
# ==================================================

def update_active_game(game_id):

    data = get_game_data(
        game_id
    )

    if not data:
        return None

    parsed = parse_api_game(
        game_id,
        data
    )

    if not parsed:
        return None

    active_games_cache[
        str(game_id)
    ] = parsed

    return parsed


# ==================================================
# PROCESS ACTIVE GAMES
# ==================================================

def process_active_games():

    active_games = get_active_games()

    if not active_games:
        return

    current_ids = set()

    # ----------------------------------------------
    # 1. Обновляем все игры из API
    # ----------------------------------------------

    for game_info in active_games:

        game_id = str(
            game_info.get("id")
        )

        if not game_id:
            continue

        if game_id in processed_games:
            continue

        current_ids.add(game_id)

        parsed = update_active_game(
            game_id
        )

        if not parsed:
            continue

        # ------------------------------------------
        # Проверяем завершение
        # ------------------------------------------

        if is_game_finished(
            parsed.get("state"),
            parsed.get(
                "player_cards",
                []
            ),
            parsed.get(
                "dealer_cards",
                []
            ),
            parsed.get(
                "player_score",
                0
            ),
            parsed.get(
                "dealer_score",
                0
            )
        ):

            saved = save_new_game(
                parsed
            )

            if saved:

                create_trigger_predictions(
                    saved
                )

            processed_games.add(
                game_id
            )

            active_games_cache.pop(
                game_id,
                None
            )

    # ----------------------------------------------
    # 2. ВАЖНО:
    # Проверяем игры, которые уже были замечены.
    #
    # Даже если они временно исчезли
    # из текущего списка active_games.
    #
    # Это защита от пропусков #N737.
    # ----------------------------------------------

    cached_ids = list(
        active_games_cache.keys()
    )

    for game_id in cached_ids:

        if game_id in processed_games:
            continue

        # Если уже был проверен выше
        if game_id in current_ids:
            continue

        parsed = update_active_game(
            game_id
        )

        if not parsed:
            continue

        if is_game_finished(
            parsed.get("state"),
            parsed.get(
                "player_cards",
                []
            ),
            parsed.get(
                "dealer_cards",
                []
            ),
            parsed.get(
                "player_score",
                0
            ),
            parsed.get(
                "dealer_score",
                0
            )
        ):

            saved = save_new_game(
                parsed
            )

            if saved:

                create_trigger_predictions(
                    saved
                )

            processed_games.add(
                game_id
            )

            active_games_cache.pop(
                game_id,
                None
            )


# ==================================================
# CLEANUP
# ==================================================

def cleanup():

    global predictions
    global processed_games

    if len(predictions) > MAX_PREDICTIONS:

        predictions = predictions[
            -MAX_PREDICTIONS:
        ]

        atomic_save_json(
            PREDICTIONS_FILE,
            predictions
        )

    if len(processed_games) > 2000:

        processed_games = set(
            list(processed_games)[-1000:]
        )


# ==================================================
# MAIN
# ==================================================

def main():

    global history_data
    global predictions

    print()
    print(
        "=================================================="
    )
    print(
        "🚀 OLD TRIGGER PATTERN BOT"
    )
    print(
        "=================================================="
    )
    print(
        "📡 API: 1x LIVE"
    )
    print(
        "💾 Парсинг: Value → SC → P1/P2/STATE"
    )
    print(
        "🔥 Триггер: ровно 3 карты у игрока"
    )
    print()
    print(
        "🃏 Прогноз:"
    )
    print(
        "   • Ранг = первая карта дилера"
    )
    print(
        "   • Масть = первая карта игрока"
    )
    print()
    print(
        "📊 Смещение №1:"
    )
    print(
        "   |последняя цифра очков игрока -"
    )
    print(
        "    последняя цифра очков дилера|"
    )
    print()
    print(
        "📊 Смещение №2:"
    )
    print(
        "   |очки игрока - очки дилера|"
    )
    print()
    print(
        "🚫 Вокруг триггера ±2 игры:"
    )
    print(
        "   • нельзя ничья"
    )
    print(
        "   • нельзя 21"
    )
    print(
        "=================================================="
    )
    print()

    history_data = load_history()

    predictions = load_predictions()

    # Загружаем уже известные ID,
    # чтобы после рестарта не сохранять их повторно.
    for game in history_data:

        game_id = game.get("game_id")

        if game_id:
            processed_games.add(
                str(game_id)
            )

    print(
        f"💾 Загружено игр: "
        f"{len(history_data)}",
        flush=True
    )

    print(
        f"🔮 Загружено прогнозов: "
        f"{len(predictions)}",
        flush=True
    )

    if history_data:

        last = history_data[-1]

        print(
            f"📌 Последняя игра: "
            f"#N{last.get('game_number')} "
            f"| ID {last.get('game_id')}",
            flush=True
        )

    print()
    print(
        "🤖 Бот запущен...",
        flush=True
    )
    print()

    while True:

        started = time.time()

        try:

            # 1. Следим за активными играми
            process_active_games()

            # 2. Проверяем триггеры,
            #    ожидающие +1/+2 игры
            validate_waiting_predictions()

            # 3. Проверяем прогнозы
            check_predictions()

            # 4. Очистка
            cleanup()

            elapsed = (
                time.time() - started
            )

            sleep_time = max(
                0.2,
                POLL_INTERVAL - elapsed
            )

            time.sleep(
                sleep_time
            )

        except KeyboardInterrupt:

            print(
                "\n🛑 Бот остановлен",
                flush=True
            )

            break

        except Exception as e:

            print(
                f"❌ Критическая ошибка: "
                f"{e}",
                flush=True
            )

            import traceback

            traceback.print_exc()

            time.sleep(3)


# ==================================================
# START
# ==================================================

if __name__ == "__main__":
    main()