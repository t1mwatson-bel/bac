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

if not CHANNEL_PROGNOZ:
    CHANNEL_PROGNOZ = os.getenv("CHAT_ID")

if not BOT_TOKEN:
    print("❌ BOT_TOKEN не задан!", flush=True)
    sys.exit(1)

if not CHANNEL_PROGNOZ:
    print("❌ CHANNEL_PROGNOZ / CHAT_ID_21 не задан!", flush=True)
    sys.exit(1)


# ==================================================
# CONFIG
# ==================================================

MOSCOW_TZ = pytz.timezone("Europe/Moscow")

BASE_URL = "https://1xlite-36553.pro"

DATA_FILE = "twentyone_data_full.json"
PREDICTIONS_FILE = "twentyone_predictions.json"

# Храним максимум игр
MAX_HISTORY_GAMES = 100

# Сколько игр проверяем до и после триггера
CHECK_BEFORE_GAMES = 2
CHECK_AFTER_GAMES = 2

# Догоны отключены
DOGON_GAMES = 0

# Интервал мониторинга
POLL_INTERVAL = 5

# Лига обычной 21
TWENTYONE_LEAGUE_ID = 1643503


# ==================================================
# API
# ==================================================

API = f"https://api.telegram.org/bot{BOT_TOKEN}"

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

messages = {}
game_numbers = {}

player_cards_history = {}
dealer_cards_history = {}
game_state_history = {}

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
            f"⚠️ Ошибка сохранения {filename}: {e}",
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

    for item in data:

        if not isinstance(item, dict):
            continue

        if not item.get("game_id"):
            continue

        if item.get("game_number") is None:
            continue

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

    rank = str(rank).upper().strip()

    rank = rank.replace(
        "А",
        "A"
    )

    allowed = {
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
        r"(10|[2-9AJQK])([♠♣♦♥])",
        card.replace("\ufe0f", "")
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
        start = start - timedelta(days=1)

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
                f"❌ games1x2 HTTP "
                f"{response.status_code}",
                flush=True
            )

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
            return []

        result = []

        for game in games:

            if not isinstance(game, dict):
                continue

            league = game.get(
                "liga",
                {}
            )

            league_id = league.get("id")

            if league_id != TWENTYONE_LEAGUE_ID:
                continue

            if not game.get("id"):
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
            timeout=10
        )

        if response.status_code != 200:

            print(
                f"❌ GetGameZip {game_id} "
                f"HTTP {response.status_code}",
                flush=True
            )

            return None

        return response.json()

    except Exception as e:

        print(
            f"❌ Ошибка игры {game_id}: {e}",
            flush=True
        )

        return None


# ==================================================
# PARSE CARDS
#
# ВЗЯТО ИЗ РАБОЧЕГО СКАНЕРА
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

            cs = card.get(
                "CS",
                "?"
            )

            cv = card.get(
                "CV",
                "?"
            )

            try:
                cv_num = int(cv)
            except Exception:
                cv_num = cv

            rank = rank_map.get(
                str(cv_num),
                str(cv_num)
            )

            suit = suit_map.get(
                cs,
                ""
            )

            if rank and suit:

                card_str = (
                    f"{rank}{suit}"
                )

                if card_str not in result:
                    result.append(
                        card_str
                    )

        return result

    except Exception:

        return []


# ==================================================
# SCORE
# ==================================================

def calculate_score(cards):

    if not cards:
        return 0

    # Два туза = 21
    if (
        len(cards) == 2
        and all(
            c and c.startswith("A")
            for c in cards
        )
    ):
        return 21

    score = 0

    for card in cards:

        if not card:
            continue

        if card.startswith("10"):
            score += 10

        elif card.startswith("6"):
            score += 6

        elif card.startswith("7"):
            score += 7

        elif card.startswith("8"):
            score += 8

        elif card.startswith("9"):
            score += 9

        elif card.startswith("J"):
            score += 10

        elif card.startswith("Q"):
            score += 10

        elif card.startswith("K"):
            score += 10

        elif card.startswith("A"):
            score += 11

    return score


# ==================================================
# GAME FINISHED
#
# Та же логика, что в рабочем сканере
# ==================================================

def is_game_finished(
    state,
    player_cards,
    dealer_cards,
    p_score,
    d_score
):

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

    if state in ("2", "3"):

        if (
            dealer_cards
            and d_score <= 21
        ):
            return False

        return True

    if (
        dealer_cards
        and d_score > 21
    ):
        return True

    if len(player_cards) >= 5:
        return True

    if (
        dealer_cards
        and len(dealer_cards) >= 5
    ):
        return True

    return False


# ==================================================
# PARSE GAME
#
# ПАРСИНГ ИМЕННО КАК В РАБОЧЕМ СКАНЕРЕ
# ==================================================

def parse_finished_game(
    game_id,
    data
):

    if not data:
        return None

    value = data.get("Value")

    if not isinstance(value, dict):
        return None

    sc = value.get(
        "SC",
        {}
    )

    if not sc:
        return None

    # ----------------------------------------------
    # НОМЕР ИГРЫ
    # ----------------------------------------------

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

    # ----------------------------------------------
    # КАРТЫ
    # ----------------------------------------------

    player_cards = []
    dealer_cards = []
    state = None

    for item in sc.get("S", []):

        if not isinstance(item, dict):
            continue

        key = item.get("Key")
        item_value = item.get("Value")

        if key == "P1":

            player_cards = get_cards(
                item_value
            )

        elif key == "P2":

            dealer_cards = get_cards(
                item_value
            )

        elif key == "STATE":

            state = str(
                item_value
            )

    if not player_cards:
        return None

    p_score = calculate_score(
        player_cards
    )

    d_score = calculate_score(
        dealer_cards
    ) if dealer_cards else 0

    return {
        "game_number": game_number,
        "game_id": str(game_id),
        "state": state,
        "player_score": p_score,
        "dealer_score": d_score,
        "player_cards": player_cards,
        "dealer_cards": dealer_cards,
        "saved_at": datetime.now(
            MOSCOW_TZ
        ).isoformat()
    }


# ==================================================
# SAVE FINISHED GAME
# ==================================================

def save_finished_game(game):

    global history_data

    if not game:
        return False

    game_id = str(
        game.get("game_id")
    )

    # Проверка дубля по ID
    for item in history_data:

        if str(
            item.get("game_id")
        ) == game_id:

            return False

    history_data.append(game)

    history_data.sort(
        key=lambda x: int(
            x.get("game_number", 0)
        )
    )

    # Только последние 100
    if len(history_data) > MAX_HISTORY_GAMES:

        history_data = (
            history_data[
                -MAX_HISTORY_GAMES:
            ]
        )

    atomic_save_json(
        DATA_FILE,
        history_data
    )

    print(
        "\n"
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
        "══════════════════════════════════\n",
        flush=True
    )

    return True


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
                game.get("game_number")
            ) == number:

                return game

        except Exception:
            continue

    return None


# ==================================================
# INVALID GAME
#
# Нельзя:
# - ничья
# - 21 у игрока
# - 21 у дилера
# ==================================================

def is_invalid_game(game):

    if not game:
        return True

    p_score = game.get(
        "player_score"
    )

    d_score = game.get(
        "dealer_score"
    )

    if p_score is None:
        return True

    if d_score is None:
        return True

    # 21 у игрока
    if p_score == 21:
        return True

    # 21 у дилера
    if d_score == 21:
        return True

    # Ничья
    if p_score == d_score:
        return True

    return False


# ==================================================
# CHECK TRIGGER
#
# УСЛОВИЯ:
#
# 1. Игрок ровно 3 карты
# 2. Игрок < 21
# 3. Не ничья
# 4. Нет 21
# 5. -2 игры валидные
# 6. +2 игры валидные
# ==================================================

def check_trigger_conditions(game):

    if not game:
        return False

    number = game.get(
        "game_number"
    )

    if number is None:
        return False

    player_cards = game.get(
        "player_cards",
        []
    )

    dealer_cards = game.get(
        "dealer_cards",
        []
    )

    p_score = game.get(
        "player_score"
    )

    d_score = game.get(
        "dealer_score"
    )

    # ----------------------------------------------
    # У ИГРОКА РОВНО 3 КАРТЫ
    # ----------------------------------------------

    if len(player_cards) != 3:

        return False

    # ----------------------------------------------
    # ОБЯЗАТЕЛЬНО <21
    # ----------------------------------------------

    if p_score >= 21:

        return False

    # ----------------------------------------------
    # ДИЛЕР 21
    # ----------------------------------------------

    if d_score == 21:

        return False

    # ----------------------------------------------
    # НИЧЬЯ
    # ----------------------------------------------

    if p_score == d_score:

        return False

    # ----------------------------------------------
    # ПЕРВАЯ КАРТА ИГРОКА НУЖНА ДЛЯ МАСТИ
    # ----------------------------------------------

    if not player_cards:
        return False

    # ----------------------------------------------
    # ПЕРВАЯ КАРТА ДИЛЕРА НУЖНА ДЛЯ РАНГА
    # ----------------------------------------------

    if not dealer_cards:
        return False

    # ----------------------------------------------
    # ПРОВЕРЯЕМ 2 ИГРЫ ДО
    # ----------------------------------------------

    for offset in range(
        1,
        CHECK_BEFORE_GAMES + 1
    ):

        check_number = (
            int(number) - offset
        )

        previous_game = (
            get_game_by_number(
                check_number
            )
        )

        # Если игры ещё нет в истории —
        # триггер пока не рассматриваем
        if not previous_game:
            return False

        if is_invalid_game(
            previous_game
        ):

            return False

    # ----------------------------------------------
    # ПРОВЕРЯЕМ 2 ИГРЫ ПОСЛЕ
    # ----------------------------------------------

    for offset in range(
        1,
        CHECK_AFTER_GAMES + 1
    ):

        check_number = (
            int(number) + offset
        )

        next_game = (
            get_game_by_number(
                check_number
            )
        )

        # Пока следующие игры не появились
        if not next_game:
            return False

        if is_invalid_game(
            next_game
        ):

            return False

    return True


# ==================================================
# GET CARD PARTS
# ==================================================

def get_card_parts(card):

    if not card:
        return None, None

    card = str(card).replace(
        "\ufe0f",
        ""
    )

    match = re.match(
        r"^(10|[2-9AJQK])([♠♣♦♥])$",
        card
    )

    if not match:
        return None, None

    rank = normalize_rank(
        match.group(1)
    )

    suit = normalize_suit(
        match.group(2)
    )

    return rank, suit


# ==================================================
# BUILD PREDICTIONS
#
# РАНГ = ПЕРВАЯ КАРТА ДИЛЕРА
# МАСТЬ = ПЕРВАЯ КАРТА ИГРОКА
#
# OFFSET 1:
# разница последних цифр очков
#
# OFFSET 2:
# абсолютная разница очков
# ==================================================

def build_predictions_from_trigger(game):

    if not game:
        return []

    player_cards = game.get(
        "player_cards",
        []
    )

    dealer_cards = game.get(
        "dealer_cards",
        []
    )

    if not player_cards:
        return []

    if not dealer_cards:
        return []

    p_score = int(
        game.get("player_score", 0)
    )

    d_score = int(
        game.get("dealer_score", 0)
    )

    # ----------------------------------------------
    # ПЕРВАЯ КАРТА ИГРОКА = МАСТЬ
    # ----------------------------------------------

    player_first_card = (
        player_cards[0]
    )

    player_rank, player_suit = (
        get_card_parts(
            player_first_card
        )
    )

    if not player_suit:
        return []

    # ----------------------------------------------
    # ПЕРВАЯ КАРТА ДИЛЕРА = РАНГ
    # ----------------------------------------------

    dealer_first_card = (
        dealer_cards[0]
    )

    dealer_rank, dealer_suit = (
        get_card_parts(
            dealer_first_card
        )
    )

    if not dealer_rank:
        return []

    # ----------------------------------------------
    # ЦЕЛЕВАЯ КАРТА
    # РАНГ ДИЛЕРА + МАСТЬ ИГРОКА
    # ----------------------------------------------

    predicted_card = (
        f"{dealer_rank}{player_suit}"
    )

    # ----------------------------------------------
    # OFFSET 1
    #
    # 27 и 12
    # 7 - 2 = 5
    # ----------------------------------------------

    p_last = abs(p_score) % 10
    d_last = abs(d_score) % 10

    offset_digits = abs(
        p_last - d_last
    )

    # ----------------------------------------------
    # OFFSET 2
    #
    # |27 - 12| = 15
    # ----------------------------------------------

    offset_difference = abs(
        p_score - d_score
    )

    source_number = int(
        game["game_number"]
    )

    result = []

    # ----------------------------------------------
    # Первый прогноз
    # ----------------------------------------------

    if offset_digits > 0:

        result.append({
            "type": "digits",
            "offset": offset_digits,
            "target_number": (
                source_number
                + offset_digits
            ),
            "predicted_card": predicted_card
        })

    # ----------------------------------------------
    # Второй прогноз
    # ----------------------------------------------

    if offset_difference > 0:

        # Не создаём дубль,
        # если оба смещения одинаковые
        exists = False

        for item in result:

            if (
                item["target_number"]
                == source_number
                + offset_difference
            ):
                exists = True
                break

        if not exists:

            result.append({
                "type": "difference",
                "offset": offset_difference,
                "target_number": (
                    source_number
                    + offset_difference
                ),
                "predicted_card": predicted_card
            })

    return result


# ==================================================
# CHECK DUPLICATE
# ==================================================

def prediction_exists(
    source_game_id,
    target_number
):

    for entry in predictions:

        if (
            str(
                entry.get(
                    "source_game_id"
                )
            )
            == str(source_game_id)
            and
            int(
                entry.get(
                    "target_number",
                    -1
                )
            )
            == int(target_number)
        ):

            return True

    return False


# ==================================================
# TELEGRAM SEND
# ==================================================

def telegram_send(text):

    try:

        response = SESSION.post(
            f"{API}/sendMessage",
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
            f"❌ Telegram: {data}",
            flush=True
        )

    except Exception as e:

        print(
            f"❌ Ошибка Telegram: {e}",
            flush=True
        )

    return None


# ==================================================
# TELEGRAM EDIT
# ==================================================

def telegram_edit(
    message_id,
    text
):

    if not message_id:
        return False

    try:

        response = SESSION.post(
            f"{API}/editMessageText",
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
            f"❌ Telegram edit: {e}",
            flush=True
        )

        return False


# ==================================================
# MESSAGE
# ==================================================

def make_prediction_message(entry):

    return (
        f"🔮 <b>ПРОГНОЗ КАРТЫ</b>\n\n"

        f"🎯 <b>Игра: "
        f"#N{entry['target_number']}</b>\n\n"

        f"🃏 <b>{entry['predicted_card']}</b>\n\n"

        f"🔥 Триггер: "
        f"#N{entry['source_number']}\n"

        f"👤 Игрок: "
        f"{entry['player_score']} "
        f"({' '.join(entry['player_cards'])})\n"

        f"🎩 Дилер: "
        f"{entry['dealer_score']} "
        f"({' '.join(entry['dealer_cards'])})\n\n"

        f"📊 Смещение: "
        f"+{entry['offset']} игр"
    )


# ==================================================
# CREATE PREDICTIONS
# ==================================================

def create_predictions_for_trigger(game):

    global predictions
    global last_prediction_time

    if not check_trigger_conditions(
        game
    ):
        return

    built_predictions = (
        build_predictions_from_trigger(
            game
        )
    )

    if not built_predictions:
        return

    for pattern in built_predictions:

        if prediction_exists(
            game["game_id"],
            pattern["target_number"]
        ):
            continue

        entry = {

            "source_number":
                game["game_number"],

            "source_game_id":
                game["game_id"],

            "target_number":
                pattern["target_number"],

            "offset":
                pattern["offset"],

            "prediction_type":
                pattern["type"],

            "predicted_card":
                pattern["predicted_card"],

            "player_score":
                game["player_score"],

            "dealer_score":
                game["dealer_score"],

            "player_cards":
                game["player_cards"],

            "dealer_cards":
                game["dealer_cards"],

            "status":
                "pending",

            "created_at":
                datetime.now(
                    MOSCOW_TZ
                ).isoformat(),

            "message_id":
                None,

            "original_text":
                "",

            "result_game":
                None,

            "found_card":
                None
        }

        message = (
            make_prediction_message(
                entry
            )
        )

        entry["original_text"] = message

        predictions.append(
            entry
        )

        atomic_save_json(
            PREDICTIONS_FILE,
            predictions
        )

        message_id = telegram_send(
            message
        )

        if message_id:

            entry["message_id"] = (
                message_id
            )

            atomic_save_json(
                PREDICTIONS_FILE,
                predictions
            )

        print(
            "\n"
            "══════════════════════════════════",
            flush=True
        )

        print(
            "🔮 СОЗДАН НОВЫЙ ПРОГНОЗ",
            flush=True
        )

        print(
            f"🔥 Триггер: "
            f"#N{entry['source_number']}",
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
            f"⏩ Смещение: "
            f"+{entry['offset']}",
            flush=True
        )

        print(
            "══════════════════════════════════\n",
            flush=True
        )


# ==================================================
# UPDATE STATUS
# ==================================================

def update_prediction_status(
    entry,
    success,
    result_game=None
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

    target = entry.get(
        "target_number"
    )

    if success:

        text = (
            original_text
            + "\n\n"
            + "━━━━━━━━━━━━━━━━━━\n"
            + f"🎯 <b>Игра #N{target} ✅</b>\n"
            + f"🃏 Выпало: "
            + f"<b>{entry['found_card']}</b>"
        )

    else:

        text = (
            original_text
            + "\n\n"
            + "━━━━━━━━━━━━━━━━━━\n"
            + f"🎯 <b>Игра #N{target} ❌</b>\n"
            + "❌ Прогноз не зашёл"
        )

    telegram_edit(
        message_id,
        text
    )


# ==================================================
# CHECK PREDICTIONS
#
# Проверяем карту во ВСЕХ картах
# игрока и дилера целевой игры
# ==================================================

def check_predictions():

    global predictions

    changed = False

    for entry in predictions:

        if entry.get("status") != "pending":
            continue

        target_number = entry.get(
            "target_number"
        )

        predicted_card = entry.get(
            "predicted_card"
        )

        if not target_number:
            continue

        game = get_game_by_number(
            target_number
        )

        # Целевая игра ещё не появилась
        if not game:
            continue

        all_cards = []

        for card in game.get(
            "player_cards",
            []
        ):

            normalized = (
                normalize_card_string(card)
            )

            if normalized:
                all_cards.append(
                    normalized
                )

        for card in game.get(
            "dealer_cards",
            []
        ):

            normalized = (
                normalize_card_string(card)
            )

            if normalized:
                all_cards.append(
                    normalized
                )

        # ------------------------------------------
        # WIN
        # ------------------------------------------

        if predicted_card in all_cards:

            entry["status"] = "win"

            entry["result_game"] = (
                target_number
            )

            entry["found_card"] = (
                predicted_card
            )

            changed = True

            print(
                f"✅ ПРОГНОЗ ЗАШЁЛ | "
                f"#N{target_number} | "
                f"{predicted_card}",
                flush=True
            )

            update_prediction_status(
                entry,
                True,
                game
            )

        # ------------------------------------------
        # LOSE
        # ------------------------------------------

        else:

            entry["status"] = "lose"

            entry["result_game"] = (
                target_number
            )

            changed = True

            print(
                f"❌ ПРОГНОЗ НЕ ЗАШЁЛ | "
                f"#N{target_number} | "
                f"{predicted_card}",
                flush=True
            )

            update_prediction_status(
                entry,
                False,
                game
            )

    if changed:

        atomic_save_json(
            PREDICTIONS_FILE,
            predictions
        )


# ==================================================
# SCAN HISTORY FOR TRIGGERS
#
# Это важно:
# когда появилась новая игра,
# проверяем предыдущие игры.
#
# Потому что триггеру нужны +2 игры после него.
# ==================================================

def scan_history_for_triggers():

    if not history_data:
        return

    for game in history_data:

        create_predictions_for_trigger(
            game
        )


# ==================================================
# PROCESS ACTIVE GAMES
#
# Основано на рабочем сканере
# ==================================================

def process_active_games():

    global processed_games

    active_games = get_active_games()

    if not active_games:
        return

    for game_info in active_games:

        game_id = str(
            game_info.get("id")
        )

        # Уже полностью обработана
        if game_id in processed_games:
            continue

        data = get_game_data(
            game_id
        )

        if not data:
            continue

        parsed = parse_finished_game(
            game_id,
            data
        )

        if not parsed:
            continue

        player_cards = parsed.get(
            "player_cards",
            []
        )

        dealer_cards = parsed.get(
            "dealer_cards",
            []
        )

        p_score = parsed.get(
            "player_score",
            0
        )

        d_score = parsed.get(
            "dealer_score",
            0
        )

        state = parsed.get(
            "state"
        )

        # ------------------------------------------
        # СЛЕЖЕНИЕ ЗА ИЗМЕНЕНИЯМИ
        # ------------------------------------------

        p1_str = json.dumps(
            player_cards,
            ensure_ascii=False
        )

        p2_str = json.dumps(
            dealer_cards,
            ensure_ascii=False
        )

        player_cards_history[game_id] = (
            p1_str
        )

        dealer_cards_history[game_id] = (
            p2_str
        )

        game_state_history[game_id] = (
            state
        )

        # ------------------------------------------
        # ПРОВЕРКА ЗАВЕРШЕНИЯ
        # ------------------------------------------

        finished = is_game_finished(
            state,
            player_cards,
            dealer_cards,
            p_score,
            d_score
        )

        if not finished:
            continue

        # ------------------------------------------
        # СОХРАНЯЕМ
        # ------------------------------------------

        saved = save_finished_game(
            parsed
        )

        processed_games.add(
            game_id
        )

        # ------------------------------------------
        # ПОСЛЕ НОВОЙ ИГРЫ
        # ПРОВЕРЯЕМ ТРИГГЕРЫ
        # ------------------------------------------

        if saved:

            scan_history_for_triggers()

            check_predictions()


# ==================================================
# CLEANUP
# ==================================================

def cleanup_cache():

    global processed_games
    global predictions

    # Ограничиваем кэш обработанных игр
    if len(processed_games) > 500:

        processed_games.clear()

        print(
            "🗑️ Кэш processed_games очищен",
            flush=True
        )

    # Оставляем максимум 1000 прогнозов
    if len(predictions) > 1000:

        predictions = predictions[-1000:]

        atomic_save_json(
            PREDICTIONS_FILE,
            predictions
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
        "🚀 OLD TRIGGER PREDICTION BOT"
    )

    print(
        "=================================================="
    )

    print(
        f"📡 API: {BASE_URL}"
    )

    print(
        f"🎮 Лига: {TWENTYONE_LEAGUE_ID}"
    )

    print(
        f"💾 История: максимум "
        f"{MAX_HISTORY_GAMES} игр"
    )

    print()

    print(
        "🔥 ТРИГГЕР:"
    )

    print(
        "   • У игрока ровно 3 карты"
    )

    print(
        "   • Игрок < 21"
    )

    print(
        "   • Не ничья"
    )

    print(
        "   • Нет 21"
    )

    print()

    print(
        "🛡️ ОКРУЖЕНИЕ:"
    )

    print(
        f"   • {CHECK_BEFORE_GAMES} игры до "
        "без ничьи/21"
    )

    print(
        f"   • {CHECK_AFTER_GAMES} игры после "
        "без ничьи/21"
    )

    print()

    print(
        "🃏 КАРТА ПРОГНОЗА:"
    )

    print(
        "   • Ранг = первая карта дилера"
    )

    print(
        "   • Масть = первая карта игрока"
    )

    print()

    print(
        "📊 СМЕЩЕНИЯ:"
    )

    print(
        "   • |последняя цифра игрока - "
        "последняя цифра дилера|"
    )

    print(
        "   • |очки игрока - очки дилера|"
    )

    print(
        "=================================================="
    )

    print()

    # ----------------------------------------------
    # LOAD
    # ----------------------------------------------

    history_data = load_history()

    predictions = load_predictions()

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
            f"#N{last.get('game_number')}",
            flush=True
        )

    print()
    print(
        "🤖 Бот запущен...",
        flush=True
    )

    print()

    # ----------------------------------------------
    # START
    # ----------------------------------------------

    while True:

        try:

            start = time.time()

            # Получаем и сохраняем завершённые игры
            process_active_games()

            # Проверяем ожидающие прогнозы
            check_predictions()

            # Чистим кэш
            cleanup_cache()

            elapsed = (
                time.time() - start
            )

            sleep_time = max(
                1,
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
                f"❌ Критическая ошибка: {e}",
                flush=True
            )

            import traceback

            traceback.print_exc()

            time.sleep(5)


# ==================================================
# START
# ==================================================

if __name__ == "__main__":
    main()