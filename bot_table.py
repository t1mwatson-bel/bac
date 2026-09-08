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

# Храним максимум 100 последних игр
MAX_HISTORY_GAMES = 100

# Проверяем 2 игры ДО и 2 игры ПОСЛЕ триггера
CHECK_BEFORE = 2
CHECK_AFTER = 2

# API
BASE_URL = "https://1xlite-36553.pro"

# Лига из рабочего кода
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
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/120.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": BASE_URL + "/",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


# ==================================================
# GLOBALS
# ==================================================

history_data = []
predictions = []

last_prediction_time = 0


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
# NORMALIZE
# ==================================================

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


def normalize_suit(suit):

    if suit is None:
        return None

    suit = str(suit)

    suit = suit.replace("\ufe0f", "")

    if suit in {"♠", "♣", "♦", "♥"}:
        return suit

    return None


def card_to_string(card):

    if isinstance(card, str):

        match = re.match(
            r"(10|[2-9AJQK])([♠♣♦♥])",
            card.replace("\ufe0f", ""),
            flags=re.IGNORECASE
        )

        if not match:
            return None

        rank = normalize_rank(match.group(1))
        suit = normalize_suit(match.group(2))

        if rank and suit:
            return f"{rank}{suit}\ufe0f"

        return None

    if not isinstance(card, dict):
        return None

    rank = normalize_rank(card.get("rank"))
    suit = normalize_suit(card.get("suit"))

    if not rank or not suit:
        return None

    return f"{rank}{suit}\ufe0f"


# ==================================================
# CARD VALUE
# ==================================================

def get_card_value(rank):

    rank = normalize_rank(rank)

    if rank is None:
        return None

    if rank == "A":
        return 11

    if rank in {"J", "Q", "K"}:
        return 10

    try:
        return int(rank)
    except Exception:
        return None


# ==================================================
# CALCULATE SCORE
#
# Туз считается как 11, при переборе
# автоматически уменьшается до 1
# ==================================================

def calculate_score(cards):

    if not cards:
        return None

    total = 0
    aces = 0

    for card in cards:

        if isinstance(card, dict):
            rank = card.get("rank")
        else:

            text = str(card).replace("\ufe0f", "")

            match = re.match(
                r"(10|[2-9AJQK])",
                text,
                flags=re.IGNORECASE
            )

            if not match:
                return None

            rank = match.group(1)

        rank = normalize_rank(rank)

        if not rank:
            return None

        value = get_card_value(rank)

        if value is None:
            return None

        total += value

        if rank == "A":
            aces += 1

    while total > 21 and aces > 0:

        total -= 10
        aces -= 1

    return total


# ==================================================
# UNKNOWN CARD CHECK
# ==================================================

def has_unknown_card(cards):

    if not cards:
        return True

    for card in cards:

        if isinstance(card, dict):

            rank = str(
                card.get("rank", "")
            ).strip()

        else:

            text = str(card).replace(
                "\ufe0f",
                ""
            )

            match = re.match(
                r"(10|[2-9AJQK?])",
                text,
                flags=re.IGNORECASE
            )

            rank = match.group(1) if match else "?"

        if rank == "?":
            return True

        if normalize_rank(rank) is None:
            return True

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

    # Если старые записи уже имеют номера
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
# GET NEXT GAME NUMBER
# ==================================================

def get_next_game_number():

    if not history_data:
        return 1

    numbers = []

    for game in history_data:

        try:

            num = int(
                game.get("game_number")
            )

            if num > 0:
                numbers.append(num)

        except Exception:
            pass

    if not numbers:
        return 1

    return max(numbers) + 1


# ==================================================
# GET ACTIVE GAMES
# ==================================================

def get_active_games():

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

    try:

        response = SESSION.get(
            url,
            timeout=10
        )

        if response.status_code != 200:

            print(
                f"❌ ActiveGames HTTP "
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
                data.get(
                    "value",
                    []
                )
            )

        else:
            return []

        result = []

        for game in games:

            if not isinstance(game, dict):
                continue

            game_id = (
                game.get("id")
                or game.get("I")
                or game.get("ID")
            )

            if not game_id:
                continue

            league_id = None

            liga = game.get("liga")

            if isinstance(liga, dict):

                league_id = liga.get("id")

            if league_id is None:

                league_id = (
                    game.get("LI")
                    or game.get("leagueId")
                )

            try:
                league_id = int(league_id)
            except Exception:
                continue

            if league_id != LEAGUE_ID:
                continue

            result.append(str(game_id))

        return result

    except Exception as e:

        print(
            f"❌ Ошибка ActiveGames: {e}",
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
            timeout=10
        )

        if response.status_code != 200:

            print(
                f"❌ GetGameZip {game_id} "
                f"HTTP {response.status_code}",
                flush=True
            )

            return None

        content_type = response.headers.get(
            "Content-Type",
            ""
        )

        if "json" not in content_type.lower():

            print(
                f"❌ GetGameZip {game_id}: "
                f"не JSON",
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
# FIND P1 / P2 RECURSIVELY
# ==================================================

def find_p1_p2(obj):

    if isinstance(obj, dict):

        if (
            "P1" in obj
            and "P2" in obj
        ):
            return (
                obj.get("P1"),
                obj.get("P2")
            )

        for value in obj.values():

            result = find_p1_p2(value)

            if result:
                return result

    elif isinstance(obj, list):

        for item in obj:

            result = find_p1_p2(item)

            if result:
                return result

    return None


# ==================================================
# EXTRACT CARDS FROM ANY OBJECT
# ==================================================

def extract_cards(obj):

    result = []

    if obj is None:
        return result

    if isinstance(obj, str):

        matches = re.findall(
            r"(10|[2-9AJQK?])([♠♣♦♥])",
            obj.replace("\ufe0f", ""),
            flags=re.IGNORECASE
        )

        for rank, suit in matches:

            result.append({
                "rank": rank.upper(),
                "suit": suit + "\ufe0f"
            })

        return result

    if isinstance(obj, list):

        for item in obj:
            result.extend(
                extract_cards(item)
            )

        return result

    if isinstance(obj, dict):

        rank = (
            obj.get("rank")
            or obj.get("Rank")
            or obj.get("R")
        )

        suit = (
            obj.get("suit")
            or obj.get("Suit")
            or obj.get("S")
        )

        if rank is not None and suit is not None:

            result.append({
                "rank": str(rank),
                "suit": str(suit)
            })

            return result

        preferred_keys = [
            "cards",
            "Cards",
            "C",
            "SC",
            "S"
        ]

        found_preferred = False

        for key in preferred_keys:

            if key in obj:

                found_preferred = True

                result.extend(
                    extract_cards(obj[key])
                )

        if found_preferred:
            return result

        for value in obj.values():
            result.extend(
                extract_cards(value)
            )

    return result


# ==================================================
# PARSE GAME
#
# Пытаемся получить P1/P2.
# Сохраняем карты именно в формате:
#
# {
#   "rank": "Q",
#   "suit": "♠️"
# }
# ==================================================

def parse_api_game(game_id, raw_data):

    if not raw_data:
        return None

    data = raw_data

    if isinstance(data, dict):

        for key in ["Value", "value"]:

            if isinstance(data.get(key), dict):

                data = data[key]
                break

    found = find_p1_p2(data)

    if not found:

        return None

    p1, p2 = found

    player_cards = extract_cards(p1)
    dealer_cards = extract_cards(p2)

    if not player_cards:
        return None

    if not dealer_cards:
        return None

    # Убираем дубликаты,
    # которые иногда появляются при рекурсивном обходе
    def unique_cards(cards):

        result = []

        seen = set()

        for card in cards:

            rank = str(
                card.get("rank", "")
            ).upper()

            suit = str(
                card.get("suit", "")
            )

            key = (rank, suit)

            if key in seen:
                continue

            seen.add(key)

            result.append(card)

        return result

    player_cards = unique_cards(
        player_cards
    )

    dealer_cards = unique_cards(
        dealer_cards
    )

    player_score = None
    dealer_score = None

    if not has_unknown_card(player_cards):

        player_score = calculate_score(
            player_cards
        )

    if not has_unknown_card(dealer_cards):

        dealer_score = calculate_score(
            dealer_cards
        )

    return {
        "game_id": str(game_id),

        "timestamp_msk":
            datetime.now(
                MOSCOW_TZ
            ).strftime("%H:%M:%S.%f")[:-3],

        "state": "3",

        "player_cards": player_cards,

        "dealer_cards": dealer_cards,

        "player_score": player_score,

        "dealer_score": dealer_score
    }


# ==================================================
# GAME EXISTS
# ==================================================

def game_exists(game_id):

    for game in history_data:

        if str(
            game.get("game_id")
        ) == str(game_id):

            return True

    return False


# ==================================================
# SAVE GAME
# ==================================================

def save_new_game(game):

    global history_data

    game_id = str(
        game.get("game_id")
    )

    if game_exists(game_id):
        return None

    game_number = get_next_game_number()

    game["game_number"] = game_number

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
    print("══════════════════════════════")
    print("💾 НОВАЯ ИГРА")
    print(
        f"🎮 #N{game_number}"
    )
    print(
        f"🆔 {game_id}"
    )
    print(
        f"👤 Игрок: "
        f"{game.get('player_score')} | "
        f"{game.get('player_cards')}"
    )
    print(
        f"🎩 Дилер: "
        f"{game.get('dealer_score')} | "
        f"{game.get('dealer_cards')}"
    )
    print("══════════════════════════════")

    return game


# ==================================================
# INVALID GAME
#
# Нельзя:
# - неизвестные карты
# - нет очков
# - игрок = 21
# - дилер = 21
# - ничья
# ==================================================

def is_invalid_game(game):

    if not game:
        return True

    player_cards = game.get(
        "player_cards",
        []
    )

    dealer_cards = game.get(
        "dealer_cards",
        []
    )

    if has_unknown_card(player_cards):
        return True

    if has_unknown_card(dealer_cards):
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

    if player_score == 21:
        return True

    if dealer_score == 21:
        return True

    if player_score == dealer_score:
        return True

    return False


# ==================================================
# GET GAME BY NUMBER
# ==================================================

def get_game_by_number(number):

    for game in history_data:

        try:

            if int(
                game.get("game_number")
            ) == int(number):

                return game

        except Exception:
            pass

    return None


# ==================================================
# CHECK NEIGHBOR GAMES
#
# 2 игры до и 2 игры после
# не должны быть:
# - ничья
# - 21
# ==================================================

def check_neighbors(trigger_number):

    # ----------------------------------------------
    # ДО ТРИГГЕРА
    # ----------------------------------------------

    for offset in range(
        1,
        CHECK_BEFORE + 1
    ):

        number = trigger_number - offset

        game = get_game_by_number(
            number
        )

        if not game:

            return False, (
                f"нет игры #N{number} "
                f"до триггера"
            )

        if is_invalid_game(game):

            return False, (
                f"#N{number} запрещённая "
                f"игра перед триггером"
            )

    # ----------------------------------------------
    # ПОСЛЕ ТРИГГЕРА
    # ----------------------------------------------

    for offset in range(
        1,
        CHECK_AFTER + 1
    ):

        number = trigger_number + offset

        game = get_game_by_number(
            number
        )

        if not game:

            return False, (
                f"ждём #N{number} "
                f"после триггера"
            )

        if is_invalid_game(game):

            return False, (
                f"#N{number} запрещённая "
                f"игра после триггера"
            )

    return True, "OK"


# ==================================================
# GET CARD PARTS
# ==================================================

def get_card_parts(card):

    if not card:
        return None, None

    if isinstance(card, dict):

        rank = normalize_rank(
            card.get("rank")
        )

        suit = normalize_suit(
            card.get("suit")
        )

        return rank, suit

    text = str(card).replace(
        "\ufe0f",
        ""
    )

    match = re.match(
        r"(10|[2-9AJQK])([♠♣♦♥])",
        text,
        flags=re.IGNORECASE
    )

    if not match:
        return None, None

    return (
        normalize_rank(match.group(1)),
        normalize_suit(match.group(2))
    )


# ==================================================
# BUILD EXACT PREDICTED CARD
#
# РАНГ:
# первая карта дилера
#
# МАСТЬ:
# первая карта игрока
# ==================================================

def build_predicted_card(game):

    player_cards = game.get(
        "player_cards",
        []
    )

    dealer_cards = game.get(
        "dealer_cards",
        []
    )

    if not player_cards:
        return None

    if not dealer_cards:
        return None

    dealer_rank, _ = get_card_parts(
        dealer_cards[0]
    )

    _, player_suit = get_card_parts(
        player_cards[0]
    )

    if not dealer_rank:
        return None

    if not player_suit:
        return None

    return f"{dealer_rank}{player_suit}\ufe0f"


# ==================================================
# SECOND DIGIT OFFSET
#
# 27 и 12
# 7 - 2 = 5
# ==================================================

def get_digit_offset(
    player_score,
    dealer_score
):

    try:

        p = abs(
            int(player_score)
        ) % 10

        d = abs(
            int(dealer_score)
        ) % 10

        return abs(p - d)

    except Exception:
        return None


# ==================================================
# SCORE DIFFERENCE OFFSET
#
# 27 - 12 = 15
# ==================================================

def get_score_offset(
    player_score,
    dealer_score
):

    try:

        return abs(
            int(player_score)
            - int(dealer_score)
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
            )
            == str(source_game_id)
            and
            int(
                entry.get(
                    "target_number",
                    0
                )
            )
            == int(target_number)
        ):

            return True

    return False


# ==================================================
# TELEGRAM
# ==================================================

def telegram_send(text):

    try:

        response = SESSION.post(
            f"{TELEGRAM_API}/sendMessage",
            json={
                "chat_id":
                    CHANNEL_PROGNOZ,

                "text":
                    text,

                "parse_mode":
                    "HTML",

                "disable_web_page_preview":
                    True
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
            f"❌ Telegram send: {e}",
            flush=True
        )

    return None


# ==================================================
# EDIT TELEGRAM
# ==================================================

def telegram_edit(
    message_id,
    text
):

    if not message_id:
        return False

    try:

        response = SESSION.post(
            f"{TELEGRAM_API}/editMessageText",
            json={
                "chat_id":
                    CHANNEL_PROGNOZ,

                "message_id":
                    message_id,

                "text":
                    text,

                "parse_mode":
                    "HTML"
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
# CREATE PREDICTION ENTRY
# ==================================================

def create_prediction(
    trigger_game,
    target_number,
    offset,
    offset_type,
    predicted_card
):

    global predictions

    source_game_id = trigger_game.get(
        "game_id"
    )

    if prediction_exists(
        source_game_id,
        target_number
    ):
        return None

    entry = {

        "source_number":
            trigger_game[
                "game_number"
            ],

        "source_game_id":
            source_game_id,

        "target_number":
            target_number,

        "offset":
            offset,

        "offset_type":
            offset_type,

        "predicted_card":
            predicted_card,

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

    predictions.append(entry)

    message = make_prediction_message(
        entry
    )

    entry["original_text"] = message

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

    print()
    print("🔮 НОВЫЙ ПРОГНОЗ")
    print(
        f"🔥 Триггер: "
        f"#N{entry['source_number']}"
    )
    print(
        f"🎯 Цель: "
        f"#N{target_number}"
    )
    print(
        f"🃏 Карта: "
        f"{predicted_card}"
    )
    print(
        f"📏 Смещение: "
        f"+{offset}"
    )
    print(
        f"📊 Тип: "
        f"{offset_type}"
    )
    print()

    return entry


# ==================================================
# MESSAGE
# ==================================================

def make_prediction_message(entry):

    offset_type = entry.get(
        "offset_type"
    )

    if offset_type == "digits":

        offset_text = (
            "Разница последних цифр очков"
        )

    else:

        offset_text = (
            "Разница очков"
        )

    return (
        f"🔥 <b>ПРОГНОЗ</b>\n\n"

        f"🎯 <b>Игра: "
        f"#N{entry['target_number']}</b>\n\n"

        f"🃏 <b>Карта: "
        f"{entry['predicted_card']}</b>\n\n"

        f"🔗 Триггер: "
        f"#N{entry['source_number']}\n"

        f"⏩ Смещение: "
        f"+{entry['offset']}\n"

        f"📊 {offset_text}"
    )


# ==================================================
# TRY CREATE PREDICTIONS
#
# Проверяем ВСЕ игры истории.
#
# Это важно:
# триггер создаётся только когда
# появились +2 игры после него.
# ==================================================

def process_triggers():

    global last_prediction_time

    if len(history_data) < (
        CHECK_BEFORE
        + CHECK_AFTER
        + 1
    ):
        return

    for game in list(history_data):

        trigger_number = game.get(
            "game_number"
        )

        if trigger_number is None:
            continue

        # ------------------------------------------
        # Уже должны быть +2 игры после
        # ------------------------------------------

        latest_number = max(
            int(
                x.get(
                    "game_number",
                    0
                )
            )
            for x in history_data
        )

        if latest_number < (
            int(trigger_number)
            + CHECK_AFTER
        ):
            continue

        # ------------------------------------------
        # ТРИГГЕР:
        # У ИГРОКА РОВНО 3 КАРТЫ
        # ------------------------------------------

        player_cards = game.get(
            "player_cards",
            []
        )

        if len(player_cards) != 3:
            continue

        # ------------------------------------------
        # САМ ТРИГГЕР НЕ МОЖЕТ БЫТЬ
        # НИЧЬЯ / 21 / НЕИЗВЕСТНЫЕ КАРТЫ
        # ------------------------------------------

        if is_invalid_game(game):
            continue

        # ------------------------------------------
        # ПРОВЕРКА 2 ДО + 2 ПОСЛЕ
        # ------------------------------------------

        valid, reason = check_neighbors(
            int(trigger_number)
        )

        if not valid:
            continue

        # ------------------------------------------
        # ПРОГНОЗИРУЕМАЯ КАРТА
        # Ранг дилера + масть игрока
        # ------------------------------------------

        predicted_card = build_predicted_card(
            game
        )

        if not predicted_card:
            continue

        player_score = game.get(
            "player_score"
        )

        dealer_score = game.get(
            "dealer_score"
        )

        # ------------------------------------------
        # OFFSET 1
        # |последняя цифра - последняя цифра|
        # ------------------------------------------

        offset_digits = get_digit_offset(
            player_score,
            dealer_score
        )

        # ------------------------------------------
        # OFFSET 2
        # |очки игрока - очки дилера|
        # ------------------------------------------

        offset_scores = get_score_offset(
            player_score,
            dealer_score
        )

        offsets = []

        if offset_digits is not None:

            # Нулевое смещение бессмысленно
            if offset_digits > 0:

                offsets.append(
                    (
                        offset_digits,
                        "digits"
                    )
                )

        if offset_scores is not None:

            if offset_scores > 0:

                offsets.append(
                    (
                        offset_scores,
                        "scores"
                    )
                )

        # ------------------------------------------
        # СОЗДАЁМ ДВА ПРОГНОЗА
        # ------------------------------------------

        for offset, offset_type in offsets:

            target_number = (
                int(trigger_number)
                + int(offset)
            )

            if target_number <= int(
                trigger_number
            ):
                continue

            if prediction_exists(
                game.get("game_id"),
                target_number
            ):
                continue

            now = time.time()

            if (
                now
                - last_prediction_time
                < 0.5
            ):
                time.sleep(0.5)

            created = create_prediction(
                game,
                target_number,
                offset,
                offset_type,
                predicted_card
            )

            if created:
                last_prediction_time = time.time()


# ==================================================
# GET ALL CARDS
# ==================================================

def get_all_cards(game):

    result = []

    for side in [
        "player_cards",
        "dealer_cards"
    ]:

        cards = game.get(
            side,
            []
        )

        for card in cards:

            card_text = card_to_string(
                card
            )

            if card_text:

                result.append(
                    card_text
                )

    return result


# ==================================================
# UPDATE PREDICTION STATUS
# ==================================================

def update_prediction_status(
    entry,
    success,
    result_game=None,
    found_card=None
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

        lines[2] = (
            f"🎯 <b>Игра: "
            f"#N{target} ✅</b>"
        )

        lines.append("")

        lines.append(
            f"✅ ЗАШЛО в "
            f"#N{result_game}"
        )

        lines.append(
            f"🃏 Выпало: "
            f"{found_card}"
        )

    else:

        lines[2] = (
            f"🎯 <b>Игра: "
            f"#N{target} ❌</b>"
        )

        lines.append("")

        lines.append(
            "❌ Карта не выпала"
        )

    new_text = "\n".join(lines)

    telegram_edit(
        message_id,
        new_text
    )


# ==================================================
# CHECK PREDICTIONS
#
# Проверяем только ЦЕЛЕВУЮ игру.
# Догонов в новой логике нет.
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

        target_game = get_game_by_number(
            target_number
        )

        # Игра ещё не пришла
        if not target_game:
            continue

        actual_cards = get_all_cards(
            target_game
        )

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
                f"✅ ЗАШЁЛ "
                f"#N{target_number} | "
                f"{predicted_card}",
                flush=True
            )

            update_prediction_status(
                entry,
                True,
                target_number,
                predicted_card
            )

        else:

            entry["status"] = "lose"

            entry["result_game"] = (
                target_number
            )

            changed = True

            print(
                f"❌ НЕ ЗАШЁЛ "
                f"#N{target_number} | "
                f"{predicted_card}",
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

        predictions = predictions[-1000:]

        atomic_save_json(
            PREDICTIONS_FILE,
            predictions
        )


# ==================================================
# PROCESS API
# ==================================================

def process_api_games():

    game_ids = get_active_games()

    if not game_ids:
        return

    # Обрабатываем в обратном порядке,
    # чтобы старые игры шли раньше новых
    game_ids = list(
        reversed(game_ids)
    )

    for game_id in game_ids:

        if game_exists(game_id):
            continue

        raw_data = get_game_data(
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
                f"⚠️ Не удалось распарсить игру "
                f"{game_id}",
                flush=True
            )

            continue

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
    print("==================================================")
    print("🚀 OLD TRIGGER PATTERN BOT")
    print("==================================================")
    print(f"🌐 API: {BASE_URL}")
    print(f"🎮 League ID: {LEAGUE_ID}")
    print(
        f"💾 История: "
        f"максимум {MAX_HISTORY_GAMES} игр"
    )
    print()
    print("🔥 ЛОГИКА ТРИГГЕРА:")
    print("   • У игрока ровно 3 карты")
    print("   • Нет ничьи")
    print("   • Нет 21")
    print("   • 2 игры ДО без ничьи/21")
    print("   • 2 игры ПОСЛЕ без ничьи/21")
    print()
    print("🃏 ПРОГНОЗ:")
    print("   • Ранг = 1-я карта дилера")
    print("   • Масть = 1-я карта игрока")
    print()
    print("📏 ДВА СМЕЩЕНИЯ:")
    print("   • Разница последних цифр очков")
    print("   • Абсолютная разница очков")
    print()
    print("==================================================")
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
            f"#N{last.get('game_number')} | "
            f"ID {last.get('game_id')}",
            flush=True
        )

    print()
    print("🤖 Бот запущен...")
    print()

    # ----------------------------------------------
    # LOOP
    # ----------------------------------------------

    while True:

        started = time.time()

        try:

            # 1. Забираем новые игры
            process_api_games()

            # 2. Когда появились +2 игры,
            #    подтверждаем старые триггеры
            process_triggers()

            # 3. Проверяем результаты прогнозов
            check_predictions()

            # 4. Чистим старые прогнозы
            cleanup_predictions()

            elapsed = time.time() - started

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

            time.sleep(3)


# ==================================================
# START
# ==================================================

if __name__ == "__main__":
    main()