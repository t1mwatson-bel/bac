import os
import sys
import json
import time
import requests
import pytz
import re

from datetime import datetime


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

# Храним только последние 100 игр
MAX_HISTORY_GAMES = 100

# Сколько игр проверяем ДО триггера
PRE_TRIGGER_GAMES = 2

# Сколько игр проверяем ПОСЛЕ триггера
POST_TRIGGER_GAMES = 2

# API polling
POLL_INTERVAL = 2.0

# ==================================================
# ЗЕРКАЛА
#
# Добавляй сюда известные рабочие зеркала.
# Бот НЕ генерирует номера случайно.
# Он проверяет только реально указанные адреса.
# ==================================================

MIRRORS = [
    "https://1xlite-36553.pro",
    "https://1xlite-0687.pro",
]

CURRENT_MIRROR_INDEX = 0
BASE_URL = MIRRORS[CURRENT_MIRROR_INDEX]

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"


# ==================================================
# HTTP
# ==================================================

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Connection": "keep-alive"
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


# ==================================================
# GLOBALS
# ==================================================

history_data = []
predictions = []

last_prediction_time = 0

# Чтобы не пытаться по 100 раз создать
# один и тот же триггер
processed_trigger_ids = set()


# ==================================================
# JSON
# ==================================================

def load_json_file(filename, default):

    try:

        if not os.path.exists(filename):
            return default

        with open(filename, "r", encoding="utf-8") as f:
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

        with open(tmp, "w", encoding="utf-8") as f:

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

    cleaned = []

    for item in data:

        if not isinstance(item, dict):
            continue

        if not item.get("game_id"):
            continue

        cleaned.append(item)

    cleaned.sort(
        key=lambda x: int(
            x.get("game_number", 0)
        )
    )

    if len(cleaned) > MAX_HISTORY_GAMES:
        cleaned = cleaned[-MAX_HISTORY_GAMES:]

    return cleaned


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
# NORMALIZE
# ==================================================

def normalize_suit(suit):

    if not suit:
        return None

    suit = str(suit).replace("\ufe0f", "")

    allowed = {
        "♠": "♠",
        "♣": "♣",
        "♦": "♦",
        "♥": "♥"
    }

    return allowed.get(suit)


def normalize_rank(rank):

    if not rank:
        return None

    rank = str(rank).upper().strip()

    # Русская А -> латинская A
    rank = rank.replace("А", "A")

    allowed = {
        "2", "3", "4", "5",
        "6", "7", "8", "9",
        "10", "J", "Q", "K", "A"
    }

    if rank in allowed:
        return rank

    return None


def normalize_card(rank, suit):

    rank = normalize_rank(rank)
    suit = normalize_suit(suit)

    if not rank or not suit:
        return None

    return f"{rank}{suit}\ufe0f"


# ==================================================
# GAME NUMBER
# ==================================================

def get_next_game_number():

    global history_data

    if not history_data:
        return 1

    numbers = []

    for item in history_data:

        try:
            numbers.append(
                int(item.get("game_number", 0))
            )
        except Exception:
            pass

    if not numbers:
        return 1

    return max(numbers) + 1


# ==================================================
# ADD OFFSET
# ==================================================

def add_game_offset(number, offset):

    try:
        return int(number) + int(offset)
    except Exception:
        return None


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

        if not data.get("ok"):

            print(
                f"⚠️ Telegram edit: {data}",
                flush=True
            )

        return bool(data.get("ok"))

    except Exception as e:

        print(
            f"❌ Telegram edit error: {e}",
            flush=True
        )

        return False


# ==================================================
# MIRROR HELPERS
# ==================================================

def get_current_base_url():

    global BASE_URL

    return BASE_URL


def switch_mirror():

    global CURRENT_MIRROR_INDEX
    global BASE_URL

    if not MIRRORS:
        return False

    old_url = BASE_URL

    CURRENT_MIRROR_INDEX += 1

    if CURRENT_MIRROR_INDEX >= len(MIRRORS):
        CURRENT_MIRROR_INDEX = 0

    BASE_URL = MIRRORS[
        CURRENT_MIRROR_INDEX
    ]

    if BASE_URL == old_url and len(MIRRORS) <= 1:
        return False

    print(
        f"🔄 Переключение зеркала:",
        flush=True
    )

    print(
        f"   Было: {old_url}",
        flush=True
    )

    print(
        f"   Стало: {BASE_URL}",
        flush=True
    )

    return True


# ==================================================
# SAFE JSON RESPONSE
# ==================================================

def safe_json_response(response):

    if response is None:
        return None

    if response.status_code != 200:
        return None

    content_type = response.headers.get(
        "content-type",
        ""
    ).lower()

    text = response.text.strip()

    # Явный HTML = зеркало не подходит
    if (
        "<!doctype html" in text.lower()
        or "<html" in text.lower()
    ):
        return None

    try:
        return response.json()

    except Exception:
        return None


# ==================================================
# API REQUEST WITH MIRROR FAILOVER
# ==================================================

def api_get(path, params=None, timeout=10):

    global BASE_URL

    if not MIRRORS:
        return None

    attempts = len(MIRRORS)

    for attempt in range(attempts):

        base_url = get_current_base_url()

        url = f"{base_url}{path}"

        try:

            response = SESSION.get(
                url,
                params=params,
                timeout=timeout
            )

            data = safe_json_response(
                response
            )

            if data is not None:

                return data

            preview = response.text[:120].replace(
                "\n",
                " "
            )

            print(
                f"⚠️ Зеркало не дало JSON "
                f"[HTTP {response.status_code}] "
                f"{base_url}",
                flush=True
            )

            if preview:
                print(
                    f"   Ответ: {preview}",
                    flush=True
                )

        except Exception as e:

            print(
                f"⚠️ Ошибка зеркала {base_url}: {e}",
                flush=True
            )

        # Пробуем следующее зеркало
        if attempt < attempts - 1:
            switch_mirror()

    return None


# ==================================================
# GET LIVE GAMES
# ==================================================

def get_live_games():

    path = (
        "/service-api/main-live-feed/v3/games1x2"
    )

    params = {
        "cfView": 3,
        "count": 40,
        "fcountry": 190,
        "gr": 415,
        "grMode": 4,
        "lng": "ru",
        "ref": 7,
        "selectedMs": "10.146.1643503"
    }

    data = api_get(
        path,
        params=params,
        timeout=10
    )

    if data is None:

        print(
            "❌ Не удалось получить список игр "
            "ни с одного зеркала",
            flush=True
        )

        return []

    games = []

    if isinstance(data, list):

        games = data

    elif isinstance(data, dict):

        for key in [
            "Value",
            "value",
            "Games",
            "games",
            "Items",
            "items"
        ]:

            value = data.get(key)

            if isinstance(value, list):
                games = value
                break

    return games


# ==================================================
# EXTRACT GAME ID
# ==================================================

def extract_game_id(game):

    possible_keys = [
        "I",
        "id",
        "ID",
        "gameId",
        "GameId"
    ]

    for key in possible_keys:

        value = game.get(key)

        if value is not None:
            return str(value)

    return None


# ==================================================
# GET GAME DETAILS
# ==================================================

def get_game_details(game_id):

    path = "/service-api/LiveFeed/GetGameZip"

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

    data = api_get(
        path,
        params=params,
        timeout=10
    )

    if data is None:

        print(
            f"❌ Не удалось получить игру "
            f"{game_id}",
            flush=True
        )

    return data


# ==================================================
# FIND CARDS RECURSIVELY
# ==================================================

def find_cards_recursive(obj, found=None):

    if found is None:
        found = []

    if isinstance(obj, dict):

        rank = None
        suit = None

        for key in [
            "rank",
            "Rank",
            "R"
        ]:

            if key in obj:
                rank = obj[key]
                break

        for key in [
            "suit",
            "Suit",
            "S"
        ]:

            if key in obj:
                suit = obj[key]
                break

        if rank and suit:

            card = normalize_card(
                rank,
                suit
            )

            if card:
                found.append(card)

        for value in obj.values():

            find_cards_recursive(
                value,
                found
            )

    elif isinstance(obj, list):

        for item in obj:

            find_cards_recursive(
                item,
                found
            )

    return found


# ==================================================
# PARSE CARD STRING
# ==================================================

def parse_cards_from_string(text):

    if not text:
        return []

    text = str(text)

    cards = re.findall(
        r"(10|[2-9AJQK])([♠♣♦♥])",
        text,
        flags=re.IGNORECASE
    )

    result = []

    for rank, suit in cards:

        card = normalize_card(
            rank,
            suit
        )

        if card:
            result.append(card)

    return result


# ==================================================
# EXTRACT SCORE
# ==================================================

def extract_score(obj):

    if isinstance(obj, (int, float)):
        return int(obj)

    if isinstance(obj, str):

        match = re.search(
            r"\b(\d{1,2})\b",
            obj
        )

        if match:
            return int(match.group(1))

    return None


# ==================================================
# PARSE GAME DATA
# ==================================================

def parse_api_game(game_id, raw_data):

    if not raw_data:
        return None

    data = raw_data

    # Иногда данные лежат в Value
    if isinstance(data, dict):

        for key in ["Value", "value"]:

            if isinstance(data.get(key), dict):
                data = data[key]
                break

    if not isinstance(data, dict):
        return None

    player_cards = []
    dealer_cards = []

    player_score = None
    dealer_score = None

    p1 = None
    p2 = None

    # ----------------------------------------------
    # ИЩЕМ P1 / P2
    # ----------------------------------------------

    def find_p1_p2(obj):

        nonlocal p1
        nonlocal p2

        if isinstance(obj, dict):

            if p1 is None and "P1" in obj:
                p1 = obj.get("P1")

            if p2 is None and "P2" in obj:
                p2 = obj.get("P2")

            for value in obj.values():
                find_p1_p2(value)

        elif isinstance(obj, list):

            for item in obj:
                find_p1_p2(item)

    find_p1_p2(data)

    # ----------------------------------------------
    # PLAYER P1
    # ----------------------------------------------

    if p1 is not None:

        if isinstance(p1, str):

            player_cards = parse_cards_from_string(
                p1
            )

        elif isinstance(p1, dict):

            for key in [
                "cards",
                "Cards",
                "C",
                "SC",
                "S"
            ]:

                value = p1.get(key)

                if value:

                    if isinstance(value, str):

                        player_cards.extend(
                            parse_cards_from_string(
                                value
                            )
                        )

                    else:

                        player_cards.extend(
                            find_cards_recursive(
                                value
                            )
                        )

            for key in [
                "score",
                "Score",
                "points",
                "Points",
                "value",
                "V"
            ]:

                if key in p1:

                    score = extract_score(
                        p1[key]
                    )

                    if score is not None:
                        player_score = score
                        break

    # ----------------------------------------------
    # DEALER P2
    # ----------------------------------------------

    if p2 is not None:

        if isinstance(p2, str):

            dealer_cards = parse_cards_from_string(
                p2
            )

        elif isinstance(p2, dict):

            for key in [
                "cards",
                "Cards",
                "C",
                "SC",
                "S"
            ]:

                value = p2.get(key)

                if value:

                    if isinstance(value, str):

                        dealer_cards.extend(
                            parse_cards_from_string(
                                value
                            )
                        )

                    else:

                        dealer_cards.extend(
                            find_cards_recursive(
                                value
                            )
                        )

            for key in [
                "score",
                "Score",
                "points",
                "Points",
                "value",
                "V"
            ]:

                if key in p2:

                    score = extract_score(
                        p2[key]
                    )

                    if score is not None:
                        dealer_score = score
                        break

    # ----------------------------------------------
    # FALLBACK SCORES
    # ----------------------------------------------

    if player_score is None:

        for key in [
            "P1Score",
            "p1Score",
            "playerScore"
        ]:

            if key in data:

                player_score = extract_score(
                    data[key]
                )

                if player_score is not None:
                    break

    if dealer_score is None:

        for key in [
            "P2Score",
            "p2Score",
            "dealerScore"
        ]:

            if key in data:

                dealer_score = extract_score(
                    data[key]
                )

                if dealer_score is not None:
                    break

    # ----------------------------------------------
    # ПРОВЕРКА
    # ----------------------------------------------

    if not player_cards:
        return None

    if not dealer_cards:
        return None

    if player_score is None:
        return None

    if dealer_score is None:
        return None

    return {
        "game_id": str(game_id),

        "player_score": int(
            player_score
        ),

        "dealer_score": int(
            dealer_score
        ),

        "player_cards": player_cards,

        "dealer_cards": dealer_cards,

        "raw_received_at":
            datetime.now(
                MOSCOW_TZ
            ).isoformat()
    }


# ==================================================
# SAVE NEW GAME
# ==================================================

def save_new_game(game):

    global history_data

    game_id = str(
        game.get("game_id")
    )

    # Проверяем дубликат
    for item in history_data:

        if str(
            item.get("game_id")
        ) == game_id:

            return None

    game_number = get_next_game_number()

    game["game_number"] = game_number

    history_data.append(game)

    history_data.sort(
        key=lambda x: int(
            x.get("game_number", 0)
        )
    )

    # Только последние 100
    if len(history_data) > MAX_HISTORY_GAMES:

        history_data = history_data[
            -MAX_HISTORY_GAMES:
        ]

    atomic_save_json(
        DATA_FILE,
        history_data
    )

    print(
        "\n💾 НОВАЯ ИГРА",
        flush=True
    )

    print(
        f"🎮 #N{game_number}",
        flush=True
    )

    print(
        f"🆔 ID: {game_id}",
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

    return game


# ==================================================
# FIND GAME BY NUMBER
# ==================================================

def get_game_by_number(number):

    for game in history_data:

        try:

            if int(
                game.get(
                    "game_number",
                    0
                )
            ) == int(number):

                return game

        except Exception:
            pass

    return None


# ==================================================
# INVALID GAME
#
# Нельзя:
# - игрок 21
# - дилер 21
# - ничья
#
# Перебор разрешен.
# ==================================================

def is_invalid_game(game):

    if not game:
        return True

    player_score = game.get(
        "player_score"
    )

    dealer_score = game.get(
        "dealer_score"
    )

    if player_score is None:
        return True

    if dealer_score is None:
        return True

    try:

        player_score = int(player_score)
        dealer_score = int(dealer_score)

    except Exception:
        return True

    # Любой 21
    if player_score == 21:
        return True

    if dealer_score == 21:
        return True

    # Ничья
    if player_score == dealer_score:
        return True

    return False


# ==================================================
# CHECK PRE-TRIGGER GAMES
#
# 2 игры ДО триггера
# ==================================================

def check_pre_trigger_games(trigger_game):

    trigger_number = int(
        trigger_game.get(
            "game_number"
        )
    )

    for offset in range(
        1,
        PRE_TRIGGER_GAMES + 1
    ):

        check_number = (
            trigger_number - offset
        )

        game = get_game_by_number(
            check_number
        )

        if not game:

            print(
                f"⏳ #N{trigger_number}: "
                f"нет игры #N{check_number} "
                f"для проверки до триггера",
                flush=True
            )

            return False

        if is_invalid_game(game):

            print(
                f"🚫 #N{trigger_number}: "
                f"до триггера запрещённая игра "
                f"#N{check_number}",
                flush=True
            )

            return False

    return True


# ==================================================
# CHECK POST-TRIGGER GAMES
#
# 2 игры ПОСЛЕ триггера
# ==================================================

def check_post_trigger_games(trigger_game):

    trigger_number = int(
        trigger_game.get(
            "game_number"
        )
    )

    for offset in range(
        1,
        POST_TRIGGER_GAMES + 1
    ):

        check_number = (
            trigger_number + offset
        )

        game = get_game_by_number(
            check_number
        )

        # Игра ещё не появилась
        if not game:
            return None

        if is_invalid_game(game):

            print(
                f"🚫 #N{trigger_number}: "
                f"после триггера запрещённая игра "
                f"#N{check_number}",
                flush=True
            )

            return False

    return True


# ==================================================
# CHECK TRIGGER
#
# Главное условие:
# У ИГРОКА РОВНО 3 КАРТЫ
#
# У дилера количество карт не важно
# ==================================================

def is_trigger_game(game):

    if not game:
        return False

    player_cards = game.get(
        "player_cards",
        []
    )

    if not isinstance(player_cards, list):
        return False

    return len(player_cards) == 3


# ==================================================
# CALCULATE SECOND DIGIT OFFSET
#
# Пример:
# 27 и 12
# 7 - 2 = 5
# ==================================================

def get_second_digit_offset(
    player_score,
    dealer_score
):

    try:

        player_score = abs(
            int(player_score)
        )

        dealer_score = abs(
            int(dealer_score)
        )

        player_digit = int(
            str(player_score)[-1]
        )

        dealer_digit = int(
            str(dealer_score)[-1]
        )

        return abs(
            player_digit - dealer_digit
        )

    except Exception:
        return None


# ==================================================
# CALCULATE SCORE DIFFERENCE
#
# Пример:
# 27 - 12 = 15
# ==================================================

def get_score_difference(
    player_score,
    dealer_score
):

    try:

        return abs(
            int(player_score)
            -
            int(dealer_score)
        )

    except Exception:
        return None


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
                entry.get(
                    "source_game_id"
                )
            ) == str(source_game_id)
            and
            int(
                entry.get(
                    "target_number",
                    -1
                )
            ) == int(target_number)
        ):

            return True

    return False


# ==================================================
# BUILD PREDICTIONS
#
# ЛОГИКА:
#
# Триггер:
# игрок ровно 3 карты
#
# Карта прогноза:
# первая карта дилера
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

    if not is_trigger_game(game):

        return []

    game_number = int(
        game.get(
            "game_number"
        )
    )

    player_score = game.get(
        "player_score"
    )

    dealer_score = game.get(
        "dealer_score"
    )

    player_cards = game.get(
        "player_cards",
        []
    )

    dealer_cards = game.get(
        "dealer_cards",
        []
    )

    if player_score is None:
        return []

    if dealer_score is None:
        return []

    # Первая карта дилера обязательна
    if not dealer_cards:
        return []

    first_dealer_card = dealer_cards[0]

    if not first_dealer_card:
        return []

    # ----------------------------------------------
    # ПРОВЕРКА ДО ТРИГГЕРА
    # ----------------------------------------------

    if not check_pre_trigger_games(
        game
    ):

        return []

    # ----------------------------------------------
    # ПРОВЕРКА ПОСЛЕ ТРИГГЕРА
    #
    # None = ещё ждём игры
    # False = запрещённая игра
    # True = всё хорошо
    # ----------------------------------------------

    post_check = check_post_trigger_games(
        game
    )

    if post_check is None:

        return None

    if post_check is False:

        return []

    # ----------------------------------------------
    # СМЕЩЕНИЕ 1
    # ----------------------------------------------

    offset_digits = get_second_digit_offset(
        player_score,
        dealer_score
    )

    # ----------------------------------------------
    # СМЕЩЕНИЕ 2
    # ----------------------------------------------

    offset_scores = get_score_difference(
        player_score,
        dealer_score
    )

    offsets = []

    if offset_digits is not None:

        if offset_digits > 0:

            offsets.append({
                "offset": offset_digits,
                "type": "digits"
            })

    if offset_scores is not None:

        if offset_scores > 0:

            # Если оба смещения одинаковые,
            # второй одинаковый прогноз не создаём
            if offset_scores not in [
                x["offset"]
                for x in offsets
            ]:

                offsets.append({
                    "offset": offset_scores,
                    "type": "scores"
                })

    if not offsets:
        return []

    result = []

    for item in offsets:

        offset = item["offset"]

        target_number = add_game_offset(
            game_number,
            offset
        )

        result.append({

            "source_number":
                game_number,

            "source_game_id":
                game.get("game_id"),

            "player_score":
                player_score,

            "dealer_score":
                dealer_score,

            "player_cards":
                player_cards,

            "dealer_cards":
                dealer_cards,

            "predicted_card":
                first_dealer_card,

            "offset":
                offset,

            "offset_type":
                item["type"],

            "target_number":
                target_number
        })

    return result


# ==================================================
# MESSAGE
# ==================================================

def make_prediction_message(entry):

    source_number = entry.get(
        "source_number"
    )

    target_number = entry.get(
        "target_number"
    )

    player_score = entry.get(
        "player_score"
    )

    dealer_score = entry.get(
        "dealer_score"
    )

    predicted_card = entry.get(
        "predicted_card"
    )

    offset = entry.get(
        "offset"
    )

    offset_type = entry.get(
        "offset_type"
    )

    if offset_type == "digits":

        formula = (
            f"|{str(player_score)[-1]} - "
            f"{str(dealer_score)[-1]}| = "
            f"{offset}"
        )

        formula_name = (
            "Разница последних цифр"
        )

    else:

        formula = (
            f"|{player_score} - "
            f"{dealer_score}| = "
            f"{offset}"
        )

        formula_name = (
            "Разница очков"
        )

    return (
        f"🎯 <b>Игра: "
        f"#N{target_number}</b>\n\n"

        f"🃏 <b>Прогноз: "
        f"{predicted_card}</b>\n\n"

        f"🔥 <b>Триггер: "
        f"#N{source_number}</b>\n"

        f"👤 Игрок: "
        f"{player_score} очков "
        f"({len(entry.get('player_cards', []))} карты)\n"

        f"🎩 Дилер: "
        f"{dealer_score} очков\n\n"

        f"📊 {formula_name}:\n"
        f"<b>{formula}</b>\n\n"

        f"⏩ Смещение: "
        f"<b>+{offset} игр</b>"
    )


# ==================================================
# CREATE PREDICTION
# ==================================================

def create_prediction_from_pattern(pattern):

    global predictions
    global last_prediction_time

    if prediction_exists(
        pattern.get("source_game_id"),
        pattern.get("target_number")
    ):
        return None

    now = time.time()

    entry = {

        "source_number":
            pattern["source_number"],

        "source_game_id":
            pattern["source_game_id"],

        "player_score":
            pattern["player_score"],

        "dealer_score":
            pattern["dealer_score"],

        "player_cards":
            pattern["player_cards"],

        "dealer_cards":
            pattern["dealer_cards"],

        "predicted_card":
            pattern["predicted_card"],

        "offset":
            pattern["offset"],

        "offset_type":
            pattern["offset_type"],

        "target_number":
            pattern["target_number"],

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

    message = make_prediction_message(
        entry
    )

    entry["original_text"] = message

    predictions.append(entry)

    atomic_save_json(
        PREDICTIONS_FILE,
        predictions
    )

    message_id = telegram_send(
        message
    )

    if message_id:

        entry["message_id"] = message_id

        atomic_save_json(
            PREDICTIONS_FILE,
            predictions
        )

    last_prediction_time = now

    print()
    print(
        "════════════════════════════════",
        flush=True
    )

    print(
        "🔮 НОВЫЙ ПРОГНОЗ",
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
        "════════════════════════════════",
        flush=True
    )

    print()

    return entry


# ==================================================
# PROCESS READY TRIGGERS
#
# Проверяем игры из истории.
#
# Важно:
# прогноз создаётся только когда появились
# две игры ПОСЛЕ триггера.
# ==================================================

def process_ready_triggers():

    global processed_trigger_ids

    if len(history_data) < (
        PRE_TRIGGER_GAMES
        +
        POST_TRIGGER_GAMES
        +
        1
    ):
        return

    for game in history_data:

        game_id = str(
            game.get("game_id")
        )

        if not game_id:
            continue

        # Не триггер — не интересует
        if not is_trigger_game(game):
            continue

        # Проверяем, есть ли уже прогнозы
        # от этой игры
        source_has_prediction = False

        for entry in predictions:

            if str(
                entry.get(
                    "source_game_id"
                )
            ) == game_id:

                source_has_prediction = True
                break

        if source_has_prediction:
            continue

        # Строим паттерн
        result = build_trigger_predictions(
            game
        )

        # None = ждём две игры после
        if result is None:
            continue

        # [] = паттерн не прошёл условия
        if not result:
            processed_trigger_ids.add(
                game_id
            )
            continue

        # Создаём два прогноза
        created_count = 0

        for pattern in result:

            created = create_prediction_from_pattern(
                pattern
            )

            if created:
                created_count += 1

        if created_count > 0:

            processed_trigger_ids.add(
                game_id
            )


# ==================================================
# UPDATE MESSAGE
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
            f"✅ ЗАШЛО"
        )

        lines.append(
            f"🎮 Игра: "
            f"#N{found['num']}"
        )

        lines.append(
            f"🃏 Выпало: "
            f"{found['card']}"
        )

    else:

        lines[0] = (
            f"🎯 <b>Игра: "
            f"#N{target} ❌</b>"
        )

        lines.append("")

        lines.append(
            f"❌ НЕ ЗАШЛО"
        )

    new_text = "\n".join(lines)

    telegram_edit(
        message_id,
        new_text
    )


# ==================================================
# CHECK PREDICTIONS
#
# Проверяем прогнозируемую карту
# во ВСЕХ картах игры.
# ==================================================

def check_predictions():

    global predictions

    changed = False

    pending = [
        x for x in predictions
        if x.get("status") == "pending"
    ]

    for entry in pending:

        target_number = entry.get(
            "target_number"
        )

        predicted_card = entry.get(
            "predicted_card"
        )

        if not target_number:
            continue

        if not predicted_card:
            continue

        game = get_game_by_number(
            target_number
        )

        if not game:
            continue

        actual_cards = []

        # Карты игрока
        for card in game.get(
            "player_cards",
            []
        ):

            if card not in actual_cards:
                actual_cards.append(card)

        # Карты дилера
        for card in game.get(
            "dealer_cards",
            []
        ):

            if card not in actual_cards:
                actual_cards.append(card)

        # ----------------------------------------------
        # WIN
        # ----------------------------------------------

        if predicted_card in actual_cards:

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
                {
                    "num": target_number,
                    "card": predicted_card
                }
            )

        # ----------------------------------------------
        # LOSE
        # ----------------------------------------------

        else:

            entry["status"] = "lose"

            entry["result_game"] = (
                target_number
            )

            changed = True

            print(
                f"❌ ПРОГНОЗ НЕ ЗАШЁЛ | "
                f"#N{target_number} | "
                f"ожидали {predicted_card}",
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
# CLEANUP PREDICTIONS
# ==================================================

def cleanup_predictions():

    global predictions

    if len(predictions) > 1000:

        predictions = predictions[
            -1000:
        ]

        atomic_save_json(
            PREDICTIONS_FILE,
            predictions
        )


# ==================================================
# PROCESS API GAMES
# ==================================================

def process_api_games():

    games = get_live_games()

    if not games:
        return

    candidates = []

    for game in games:

        if not isinstance(game, dict):
            continue

        game_id = extract_game_id(
            game
        )

        if not game_id:
            continue

        candidates.append(
            game_id
        )

    # Убираем дубли
    unique_candidates = []

    for game_id in candidates:

        if game_id not in unique_candidates:
            unique_candidates.append(
                game_id
            )

    # ----------------------------------------------
    # ОБРАБАТЫВАЕМ
    # ----------------------------------------------

    for game_id in unique_candidates:

        # Уже есть?
        exists = False

        for item in history_data:

            if str(
                item.get("game_id")
            ) == str(game_id):

                exists = True
                break

        if exists:
            continue

        # Получаем детали
        raw_data = get_game_details(
            game_id
        )

        if not raw_data:
            continue

        parsed = parse_api_game(
            game_id,
            raw_data
        )

        if not parsed:

            print(
                f"⚠️ Не удалось распарсить "
                f"игру {game_id}",
                flush=True
            )

            continue

        # Сохраняем
        save_new_game(
            parsed
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
        "🚀 OLD PATTERN API BOT"
    )

    print(
        "=================================================="
    )

    print(
        f"📡 Активное зеркало: "
        f"{BASE_URL}"
    )

    print(
        f"🔄 Зеркал в списке: "
        f"{len(MIRRORS)}"
    )

    print(
        f"💾 История: "
        f"последние {MAX_HISTORY_GAMES} игр"
    )

    print()

    print(
        "🔥 ЛОГИКА ТРИГГЕРА:"
    )

    print(
        "   • У игрока ровно 3 карты"
    )

    print(
        "   • Карта прогноза = первая карта дилера"
    )

    print()

    print(
        "🚫 ДО триггера 2 игры:"
    )

    print(
        "   • нельзя ничья"
    )

    print(
        "   • нельзя 21"
    )

    print()

    print(
        "🚫 ПОСЛЕ триггера 2 игры:"
    )

    print(
        "   • нельзя ничья"
    )

    print(
        "   • нельзя 21"
    )

    print()

    print(
        "🎯 ДВА ПРОГНОЗА:"
    )

    print(
        "   1. Разница последних цифр"
    )

    print(
        "   2. Абсолютная разница очков"
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
        f"📊 Загружено прогнозов: "
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

    # ==================================================
    # LOOP
    # ==================================================

    while True:

        start = time.time()

        try:

            # 1. Получаем новые игры
            process_api_games()

            # 2. Проверяем триггеры,
            # когда уже появились 2 игры после них
            process_ready_triggers()

            # 3. Проверяем результаты прогнозов
            check_predictions()

            # 4. Чистим историю прогнозов
            cleanup_predictions()

            elapsed = time.time() - start

            sleep_time = max(
                0.1,
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

            time.sleep(3)


# ==================================================
# START
# ==================================================

if __name__ == "__main__":
    main()