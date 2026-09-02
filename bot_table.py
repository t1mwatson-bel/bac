import os
import sys
import json
import time
import re
import requests
import pytz

from datetime import datetime, timedelta


# =====================================================================
# ENV / TELEGRAM
# =====================================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    BOT_TOKEN = os.getenv("BOT_TOKEN_PROGNOZ")


CHAT_ID = os.getenv("CHAT_ID_21")

if not CHAT_ID:
    CHAT_ID = os.getenv("CHAT_ID")


if not BOT_TOKEN or not CHAT_ID:

    print(
        "❌ Ошибка: BOT_TOKEN или CHAT_ID не заданы!",
        flush=True
    )

    sys.exit(1)


print(
    f"✅ BOT_TOKEN: {BOT_TOKEN[:5]}...",
    flush=True
)

print(
    f"✅ CHAT_ID: {CHAT_ID}",
    flush=True
)


# =====================================================================
# SETTINGS
# =====================================================================

MOSCOW_TZ = pytz.timezone(
    "Europe/Moscow"
)

BASE_URL = (
    "https://1xlite-36553.pro"
)

API = (
    f"https://api.telegram.org/bot{BOT_TOKEN}"
)


# =====================================================================
# FILES
# =====================================================================

DATA_FILE = (
    "twentyone_data_full.json"
)

PREDICTIONS_FILE = (
    "twentyone_predictions.json"
)


# =====================================================================
# HISTORY SETTINGS
# =====================================================================

# Храним последние 1000 игр.
MAX_HISTORY_GAMES = 1000


# =====================================================================
# PREDICTION SETTINGS
# =====================================================================

# Прогнозируем только эти ранги.
TARGET_RANKS = {
    "J",
    "Q",
    "K",
    "A",
}


# Сколько игр проверяем.
#
# 1 = только целевая игра
# 2 = целевая + 1 догон
# 3 = целевая + 2 догона
# и т.д.
DOGON_GAMES = 4


# =====================================================================
# GAME SETTINGS
# =====================================================================

LEAGUE_ID = 1643503


# =====================================================================
# MEMORY
# =====================================================================

messages = {}

processed_games = set()

game_numbers = {}

player_cards_history = {}

dealer_cards_history = {}

game_state_history = {}


# =====================================================================
# CARDS
# =====================================================================

SUITS_NAMES = {
    0: "♠️",
    1: "♣️",
    2: "♦️",
    3: "♥️",
}


RANKS = {
    2: "2",
    3: "3",
    4: "4",
    5: "5",
    6: "6",
    7: "7",
    8: "8",
    9: "9",
    10: "10",
    11: "J",
    12: "Q",
    13: "K",
    14: "A",
}


# =====================================================================
# PAIR SUITS
# =====================================================================

SUIT_PAIRS = {
    "♥": "♠",
    "♣": "♦",
    "♦": "♣",
    "♠": "♥",
}


# =====================================================================
# HEADERS
# =====================================================================

HEADERS = {
    "User-Agent":
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36",

    "Accept":
        "application/json, text/plain, */*",

    "Referer":
        f"{BASE_URL}/ru/live/twentyone/1643503-twentyone-game",

    "Cookie":
        "platform_type=desktop; "
        "lng=ru; "
        "cookies_agree_type=3; "
        "tzo=3; "
        "is12h=0; "
        "referral_values=%7B%22type%22%3A%22reflinkid%22%2C%22val%22%3A%22s_50970m_355c_%22%2C%22additional%22%3A%7B%22name_tag%22%3A%22tag%22%7D%7D; "
        "reflinkid=s_50970m_355c_; "
        "auid=uaJb+WqQFLEHP+WbAwdUAg==; "
        "fatman_uuid=6dac517c-7199-1491-828a-723ace371af0; "
        "che_g=3741ad9b-2648-4e11-b16e-55cbdda04b42; "
        "SESSION=ae9f1b4deac37d41be6873b1acf03cf4; "
        "sh.session.id=1e645679-820b-4250-86f5-bf39161d311d; "
        "_ga=GA1.1.103981619.1787827389; "
        "_ym_uid=1787827389562709649; "
        "_ym_d=1787827389; "
        "_ym_isad=2; "
        "_ym_visorc=b; "
        "mdd=1; "
        "_ga_7JGWL9SV66=GS2.1.s1787827388$o1$g1$t1787827414$j34$l0$h1219464045; "
        "window_width=150"
}


print(
    "✅ Настройки для обычной 21 загружены",
    flush=True
)


# =====================================================================
# GAME NUMBER
# =====================================================================

def get_game_number():

    now = datetime.now(
        MOSCOW_TZ
    )

    start = now.replace(
        hour=3,
        minute=0,
        second=0,
        microsecond=0
    )

    if now < start:

        start = (
            start
            - timedelta(days=1)
        )

    diff_minutes = (
        now - start
    ).total_seconds() / 60

    return (
        int(diff_minutes)
        % 1440
        + 1
    )


# =====================================================================
# GET ACTIVE GAMES
# =====================================================================

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

        response = requests.get(
            url,
            headers=HEADERS,
            timeout=10
        )

        print(
            f"🌐 API status: {response.status_code}",
            flush=True
        )

        if response.status_code != 200:

            print(
                f"❌ API ответ: {response.text[:500]}",
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

            print(
                f"❌ Неизвестный формат API: {type(data)}",
                flush=True
            )

            return []

        print(
            f"📡 API получено игр: {len(games)}",
            flush=True
        )

        result = []

        for g in games:

            game_id = g.get("id")

            if not game_id:
                continue

            liga = g.get(
                "liga",
                {}
            )

            liga_id = liga.get(
                "id"
            )

            # Диагностика первых игр
            print(
                f"🔍 API игра | "
                f"ID={game_id} | "
                f"liga={liga_id}",
                flush=True
            )

            # Пока НЕ фильтруем жёстко,
            # чтобы увидеть реальные данные API
            result.append(
                g
            )

        return result

    except Exception as e:

        print(
            f"❌ Ошибка get_active_games: {e}",
            flush=True
        )

        return []


# =====================================================================
# GET GAME DATA
# =====================================================================

def get_game_data(
    game_id
):

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

        response = requests.get(
            url,
            headers=HEADERS,
            timeout=5
        )

        if response.status_code == 200:

            return response.json()

    except Exception as e:

        print(
            f"❌ Ошибка игры {game_id}: {e}",
            flush=True
        )

    return None


# =====================================================================
# JSON LOAD
# =====================================================================

def load_json_file(
    filename,
    default
):

    if not os.path.exists(
        filename
    ):

        return default

    try:

        with open(
            filename,
            "r",
            encoding="utf-8"
        ) as file:

            return json.load(
                file
            )

    except Exception as e:

        print(
            f"⚠️ Ошибка чтения {filename}: {e}",
            flush=True
        )

        return default


# =====================================================================
# JSON SAVE
# =====================================================================

def save_json_file(
    filename,
    data
):

    try:

        temp_file = (
            filename
            + ".tmp"
        )

        with open(
            temp_file,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                data,
                file,
                ensure_ascii=False,
                indent=2
            )

        os.replace(
            temp_file,
            filename
        )

    except Exception as e:

        print(
            f"❌ Ошибка сохранения {filename}: {e}",
            flush=True
        )


# =====================================================================
# LOAD HISTORY
# =====================================================================

def load_history():

    data = load_json_file(
        DATA_FILE,
        []
    )

    if not isinstance(
        data,
        list
    ):

        return []

    return data


# =====================================================================
# SAVE HISTORY
# =====================================================================

def save_history(
    history
):

    if len(history) > MAX_HISTORY_GAMES:

        history = history[
            -MAX_HISTORY_GAMES:
        ]

    save_json_file(
        DATA_FILE,
        history
    )


# =====================================================================
# LOAD PREDICTIONS
# =====================================================================

def load_predictions():

    data = load_json_file(
        PREDICTIONS_FILE,
        []
    )

    if not isinstance(
        data,
        list
    ):

        return []

    return data


# =====================================================================
# SAVE PREDICTIONS
# =====================================================================

def save_predictions():

    save_json_file(
        PREDICTIONS_FILE,
        predictions
    )


# =====================================================================
# TELEGRAM SEND MESSAGE
# =====================================================================

def send_message(
    text
):

    try:

        url = (
            f"{API}/sendMessage"
        )

        payload = {
            "chat_id":
                CHAT_ID,

            "text":
                text,

            "parse_mode":
                "HTML",
        }

        response = requests.post(
            url,
            json=payload,
            timeout=10
        )

        data = response.json()

        if (
            data.get("ok")
            and data.get("result")
        ):

            message_id = (
                data["result"].get(
                    "message_id"
                )
            )

            return message_id

        print(
            f"❌ Telegram send error: {data}",
            flush=True
        )

    except Exception as e:

        print(
            f"❌ Ошибка Telegram: {e}",
            flush=True
        )

    return None


# =====================================================================
# EDIT TELEGRAM MESSAGE
# =====================================================================

def edit_message(
    message_id,
    text
):

    try:

        url = (
            f"{API}/editMessageText"
        )

        payload = {
            "chat_id":
                CHAT_ID,

            "message_id":
                message_id,

            "text":
                text,

            "parse_mode":
                "HTML",
        }

        response = requests.post(
            url,
            json=payload,
            timeout=10
        )

        data = response.json()

        return data.get(
            "ok",
            False
        )

    except Exception as e:

        print(
            f"❌ Ошибка редактирования: {e}",
            flush=True
        )

        return False


# =====================================================================
# NORMALIZE SUIT
# =====================================================================

def normalize_suit(
    suit
):

    if not suit:

        return None

    return (
        str(suit)
        .replace(
            "\ufe0f",
            ""
        )
        .strip()
    )


# =====================================================================
# DISPLAY CARD
# =====================================================================

def make_card(
    rank,
    suit
):

    if not rank or not suit:

        return None

    clean_suit = normalize_suit(
        suit
    )

    if not clean_suit:

        return None

    return (
        f"{rank}"
        f"{clean_suit}"
        "\ufe0f"
    )


# =====================================================================
# GET DISPLAY PREDICTION
# =====================================================================

def get_paired_prediction(
    card
):

    if not card:

        return None

    clean = (
        str(card)
        .replace(
            "\ufe0f",
            ""
        )
        .strip()
    )

    if len(clean) < 2:

        return None

    rank = clean[:-1]

    suit = clean[-1]

    pair = SUIT_PAIRS.get(
        suit
    )

    if not pair:

        return (
            f"{rank}{suit}\ufe0f"
        )

    return (
        f"{rank}{suit}\ufe0f"
        f"{pair}\ufe0f"
    )


# =====================================================================
# EXTRACT CARD
# =====================================================================

def extract_card_from_obj(
    obj
):

    if not isinstance(
        obj,
        dict
    ):

        return None

    rank = obj.get(
        "rank"
    )

    suit = obj.get(
        "suit"
    )

    if rank is None:

        rank = obj.get(
            "v"
        )

    if suit is None:

        suit = obj.get(
            "s"
        )

    if isinstance(
        rank,
        int
    ):

        rank = RANKS.get(
            rank,
            str(rank)
        )

    if isinstance(
        suit,
        int
    ):

        suit = SUITS_NAMES.get(
            suit
        )

    if not rank or not suit:

        return None

    rank = str(
        rank
    ).strip()

    suit = normalize_suit(
        suit
    )

    return {
        "rank":
            rank,

        "suit":
            f"{suit}\ufe0f",
    }


# =====================================================================
# RECURSIVE SEARCH FOR CARDS
# =====================================================================

def find_card_lists(
    obj,
    found=None
):

    if found is None:

        found = []

    if isinstance(
        obj,
        dict
    ):

        for key, value in obj.items():

            key_lower = str(
                key
            ).lower()

            if (
                isinstance(value, list)
                and (
                    "player" in key_lower
                    or key_lower in [
                        "p",
                        "p1",
                        "cards1",
                    ]
                )
            ):

                cards = []

                for item in value:

                    card = extract_card_from_obj(
                        item
                    )

                    if card:

                        cards.append(
                            card
                        )

                if cards:

                    found.append(
                        (
                            "player",
                            cards
                        )
                    )

            elif (
                isinstance(value, list)
                and (
                    "dealer" in key_lower
                    or key_lower in [
                        "d",
                        "d1",
                        "cards2",
                    ]
                )
            ):

                cards = []

                for item in value:

                    card = extract_card_from_obj(
                        item
                    )

                    if card:

                        cards.append(
                            card
                        )

                if cards:

                    found.append(
                        (
                            "dealer",
                            cards
                        )
                    )

            else:

                find_card_lists(
                    value,
                    found
                )

    elif isinstance(
        obj,
        list
    ):

        for item in obj:

            find_card_lists(
                item,
                found
            )

    return found


# =====================================================================
# PARSE GAME DATA
#
# API у 1x может отдавать структуру по-разному.
# Сначала ищем готовые player_cards/dealer_cards,
# затем пытаемся найти их рекурсивно.
# =====================================================================

def parse_game_data(
    game_id,
    raw_data
):

    if not raw_data:

        return None

    player_cards = []

    dealer_cards = []


    # -------------------------------------------------------------
    # Если структура уже похожа на сохранённую.
    # -------------------------------------------------------------

    if isinstance(
        raw_data,
        dict
    ):

        raw_player = raw_data.get(
            "player_cards"
        )

        raw_dealer = raw_data.get(
            "dealer_cards"
        )

        if isinstance(
            raw_player,
            list
        ):

            for item in raw_player:

                card = extract_card_from_obj(
                    item
                )

                if card:

                    player_cards.append(
                        card
                    )

        if isinstance(
            raw_dealer,
            list
        ):

            for item in raw_dealer:

                card = extract_card_from_obj(
                    item
                )

                if card:

                    dealer_cards.append(
                        card
                    )


    # -------------------------------------------------------------
    # Если прямой структуры нет.
    # -------------------------------------------------------------

    if not player_cards:

        found = find_card_lists(
            raw_data
        )

        for who, cards in found:

            if (
                who == "player"
                and cards
                and not player_cards
            ):

                player_cards = cards

            elif (
                who == "dealer"
                and cards
                and not dealer_cards
            ):

                dealer_cards = cards


    # -------------------------------------------------------------
    # Без P1 игра нам не нужна.
    # -------------------------------------------------------------

    if not player_cards:

        return None


    # -------------------------------------------------------------
    # Первая карта игрока.
    # -------------------------------------------------------------

    first_card = (
        player_cards[0]
    )


    if not first_card:

        return None


    return {
        "game_id":
            str(game_id),

        "timestamp_msk":
            datetime.now(
                MOSCOW_TZ
            ).strftime(
                "%H:%M:%S.%f"
            )[:-3],

        "player_cards":
            player_cards,

        "dealer_cards":
            dealer_cards,

        "first_player_card":
            first_card,

        "id_last_digit":
            str(game_id)[-1],
    }


# =====================================================================
# GAME EXISTS
# =====================================================================

def game_exists(
    history,
    game_id
):

    game_id = str(
        game_id
    )

    for game in history:

        if str(
            game.get(
                "game_id"
            )
        ) == game_id:

            return True

    return False


# =====================================================================
# ADD GAME TO HISTORY
# =====================================================================

def add_game_to_history(
    history,
    game
):

    if not game:

        return history

    game_id = str(
        game.get(
            "game_id",
            ""
        )
    )

    if not game_id:

        return history


    # Не добавляем дубль.
    if game_exists(
        history,
        game_id
    ):

        return history


    history.append(
        game
    )


    # Оставляем последние 1000.
    if len(history) > MAX_HISTORY_GAMES:

        history = history[
            -MAX_HISTORY_GAMES:
        ]


    save_history(
        history
    )


    print(
        f"💾 Игра сохранена | "
        f"ID={game_id} | "
        f"последняя={game_id[-1]} | "
        f"P1={make_card(game['first_player_card']['rank'], game['first_player_card']['suit'])} | "
        f"история={len(history)}",
        flush=True
    )

    return history


# =====================================================================
# FIND LAST SAME ID DIGIT
#
# Ищем с конца истории.
# То есть находим самую последнюю предыдущую игру.
# =====================================================================

def find_last_same_digit_game(
    history,
    game_id
):

    if not history:

        return None

    game_id = str(
        game_id
    )

    if not game_id:

        return None

    target_digit = game_id[-1]


    for game in reversed(
        history
    ):

        old_game_id = str(
            game.get(
                "game_id",
                ""
            )
        )

        if not old_game_id:

            continue

        if old_game_id == game_id:

            continue

        if old_game_id[-1] == target_digit:

            return game


    return None


# =====================================================================
# CREATE PREDICTION FROM ID
#
# ГЛАВНАЯ ЛОГИКА:
#
# новая игра -> последняя цифра ID
# ↓
# ищем последнюю старую игру с такой цифрой
# ↓
# берём P1
# ↓
# только J/Q/K/A
# =====================================================================

def create_prediction_from_id(
    history,
    game_id,
    game_number
):

    previous_game = (
        find_last_same_digit_game(
            history,
            game_id
        )
    )


    if not previous_game:

        print(
            f"⏭️ #N{game_number} ПРОПУСК | "
            f"нет предыдущей игры "
            f"с ID на {str(game_id)[-1]}",
            flush=True
        )

        return None


    first_card = (
        previous_game.get(
            "first_player_card"
        )
    )


    if not first_card:

        player_cards = (
            previous_game.get(
                "player_cards",
                []
            )
        )

        if player_cards:

            first_card = (
                player_cards[0]
            )


    if not first_card:

        return None


    rank = str(
        first_card.get(
            "rank",
            ""
        )
    ).strip()


    suit = first_card.get(
        "suit"
    )


    # -------------------------------------------------------------
    # Только J/Q/K/A.
    # -------------------------------------------------------------

    if rank not in TARGET_RANKS:

        print(
            f"⏭️ #N{game_number} ПРОПУСК | "
            f"ID заканчивается на {str(game_id)[-1]} | "
            f"найдена игра ID={previous_game.get('game_id')} | "
            f"P1={rank}{suit} | "
            f"ранг не J/Q/K/A",
            flush=True
        )

        return None


    predicted_card = make_card(
        rank,
        suit
    )


    display_prediction = (
        get_paired_prediction(
            predicted_card
        )
    )


    print(
        f"🎯 ЛОГИКА ID СРАБОТАЛА\n"
        f"Новая игра: #N{game_number}\n"
        f"Новый ID: {game_id}\n"
        f"Последняя цифра: {str(game_id)[-1]}\n"
        f"Найдена предыдущая игра: "
        f"ID={previous_game.get('game_id')}\n"
        f"P1 предыдущей игры: "
        f"{predicted_card}\n"
        f"🔮 ПРОГНОЗ: "
        f"{display_prediction}",
        flush=True
    )


    return {
        "predicted_card":
            predicted_card,

        "display_prediction":
            display_prediction,

        "source_game_id":
            previous_game.get(
                "game_id"
            ),

        "source_digit":
            str(game_id)[-1],

        "source_first_card":
            predicted_card,
    }


# =====================================================================
# CHECK CARD AGAINST GAME
#
# Проверяем:
# 1. Основную масть
# 2. Парную масть
#
# Например прогноз J♠️♦️:
# J♠️ = WIN
# J♦️ = WIN
# =====================================================================

def check_prediction_against_game(
    predicted_card,
    parsed_game
):

    if not predicted_card:

        return False

    if not parsed_game:

        return False


    clean_card = (
        str(predicted_card)
        .replace(
            "\ufe0f",
            ""
        )
    )


    if len(clean_card) < 2:

        return False


    rank = clean_card[:-1]

    suit = clean_card[-1]

    paired_suit = SUIT_PAIRS.get(
        suit
    )


    allowed_suits = {
        suit
    }


    if paired_suit:

        allowed_suits.add(
            paired_suit
        )


    # -------------------------------------------------------------
    # Проверяем карты игрока.
    # -------------------------------------------------------------

    player_cards = parsed_game.get(
        "player_cards",
        []
    )


    for card in player_cards:

        card_rank = str(
            card.get(
                "rank",
                ""
            )
        )

        card_suit = normalize_suit(
            card.get(
                "suit"
            )
        )

        if (
            card_rank == rank
            and card_suit in allowed_suits
        ):

            return True


    # -------------------------------------------------------------
    # Проверяем карты дилера.
    # -------------------------------------------------------------

    dealer_cards = parsed_game.get(
        "dealer_cards",
        []
    )


    for card in dealer_cards:

        card_rank = str(
            card.get(
                "rank",
                ""
            )
        )

        card_suit = normalize_suit(
            card.get(
                "suit"
            )
        )

        if (
            card_rank == rank
            and card_suit in allowed_suits
        ):

            return True


    return False


# =====================================================================
# GET DOGON RESULT
# =====================================================================

def get_dogon_result(
    entry,
    history
):

    target_game_id = entry.get(
        "target_game_id"
    )


    if not target_game_id:

        return None


    predicted_card = entry.get(
        "predicted_card"
    )


    if not predicted_card:

        return None


    # Ищем целевую игру и следующие игры
    # по порядку в истории.

    target_index = None


    for index, game in enumerate(
        history
    ):

        if str(
            game.get(
                "game_id"
            )
        ) == str(target_game_id):

            target_index = index

            break


    if target_index is None:

        return None


    checked_games = []


    for dogon in range(
        DOGON_GAMES
    ):

        index = (
            target_index
            + dogon
        )


        if index >= len(history):

            return None


        game = history[index]

        checked_games.append(
            game.get(
                "game_id"
            )
        )


        if check_prediction_against_game(
            predicted_card,
            game
        ):

            return {
                "status":
                    "win",

                "game":
                    game.get(
                        "game_id"
                    ),

                "dogon":
                    dogon,

                "checked_games":
                    checked_games.copy(),
            }


    return {
        "status":
            "lose",

        "game":
            None,

        "dogon":
            None,

        "checked_games":
            checked_games.copy(),
    }


# =====================================================================
# CHECK RESULTS
# =====================================================================

def check_results(
    history
):

    global predictions

    changed = False


    for entry in predictions:

        if entry.get(
            "status"
        ) != "pending":

            continue


        result = get_dogon_result(
            entry,
            history
        )


        if result is None:

            continue


        message_id = entry.get(
            "message_id"
        )


        target_number = entry.get(
            "target_number"
        )


        display_prediction = entry.get(
            "display_prediction"
        )


        # =========================================================
        # WIN
        # =========================================================

        if result["status"] == "win":

            text = (
                f"🎯 Игра: "
                f"<b>#N{target_number}</b>: "
                f"<b>{display_prediction}✅</b>"
            )


            if message_id:

                edit_message(
                    message_id,
                    text
                )


            entry.update(
                {
                    "status":
                        "win",

                    "result_game":
                        result.get(
                            "game"
                        ),

                    "dogon":
                        result.get(
                            "dogon"
                        ),

                    "checked_games":
                        result.get(
                            "checked_games"
                        ),
                }
            )


            print(
                f"✅ ЗАШЛО | "
                f"#N{target_number} | "
                f"{display_prediction} | "
                f"ID={result.get('game')} | "
                f"догон={result.get('dogon')}",
                flush=True
            )


            changed = True


        # =========================================================
        # LOSE
        # =========================================================

        elif result["status"] == "lose":

            text = (
                f"🎯 Игра: "
                f"<b>#N{target_number}</b>: "
                f"<b>{display_prediction}❌</b>"
            )


            if message_id:

                edit_message(
                    message_id,
                    text
                )


            entry.update(
                {
                    "status":
                        "lose",

                    "result_game":
                        None,

                    "dogon":
                        None,

                    "checked_games":
                        result.get(
                            "checked_games"
                        ),
                }
            )


            print(
                f"❌ НЕ ЗАШЛО | "
                f"#N{target_number} | "
                f"{display_prediction}",
                flush=True
            )


            changed = True


    if changed:

        save_predictions()


# =====================================================================
# PREDICTION EXISTS
# =====================================================================

def has_prediction_for_game(
    game_id
):

    for entry in predictions:

        if str(
            entry.get(
                "target_game_id"
            )
        ) == str(game_id):

            return True

    return False


# =====================================================================
# PROCESS NEW GAME
# =====================================================================

def process_game(
    history,
    game_id,
    game_number
):

    game_id = str(
        game_id
    )


    # -------------------------------------------------------------
    # Уже есть прогноз.
    # -------------------------------------------------------------

    if has_prediction_for_game(
        game_id
    ):

        return


    # -------------------------------------------------------------
    # Сначала создаём прогноз
    # НА ОСНОВЕ СТАРОЙ ИСТОРИИ.
    #
    # Текущую игру ещё НЕ добавляем,
    # чтобы она не нашла саму себя.
    # -------------------------------------------------------------

    prediction = (
        create_prediction_from_id(
            history,
            game_id,
            game_number
        )
    )


    if not prediction:

        return


    predicted_card = prediction.get(
        "predicted_card"
    )


    display_prediction = prediction.get(
        "display_prediction"
    )


    # -------------------------------------------------------------
    # Telegram.
    # -------------------------------------------------------------

    text = (
        f"🎯 Игра: "
        f"<b>#N{game_number}</b>: "
        f"<b>{display_prediction}</b>"
    )


    message_id = send_message(
        text
    )


    if not message_id:

        print(
            f"❌ Не удалось отправить прогноз "
            f"#N{game_number}",
            flush=True
        )

        return


    # -------------------------------------------------------------
    # Сохраняем прогноз.
    # -------------------------------------------------------------

    entry = {
        "target_number":
            game_number,

        "target_game_id":
            game_id,

        "predicted_card":
            predicted_card,

        "display_prediction":
            display_prediction,

        "source_game_id":
            prediction.get(
                "source_game_id"
            ),

        "source_digit":
            prediction.get(
                "source_digit"
            ),

        "source_first_card":
            prediction.get(
                "source_first_card"
            ),

        "message_id":
            message_id,

        "status":
            "pending",

        "created":
            datetime.now(
                MOSCOW_TZ
            ).isoformat(),

        "checked_games":
            [],
    }


    predictions.append(
        entry
    )


    save_predictions()


    print(
        f"🔥 ПРОГНОЗ СОХРАНЁН | "
        f"#N{game_number} | "
        f"{display_prediction} | "
        f"ID={game_id} | "
        f"источник={prediction.get('source_game_id')}",
        flush=True
    )


# =====================================================================
# MAIN LOOP
#
# ВАЖНО:
#
# Активные игры приходят раньше результата.
#
# Для полноценного наполнения истории бот постоянно
# опрашивает API и пытается получить данные игры.
# =====================================================================

def main_loop():

    global history


    print(
        "=" * 70,
        flush=True
    )

    print(
        "🚀 BOT STARTED",
        flush=True
    )

    print(
        f"📚 История: {len(history)} игр",
        flush=True
    )

    print(
        f"📦 Максимум истории: "
        f"{MAX_HISTORY_GAMES}",
        flush=True
    )

    print(
        f"🎯 Ранги: "
        f"{', '.join(sorted(TARGET_RANKS))}",
        flush=True
    )

    print(
        f"🔁 DOGON_GAMES: "
        f"{DOGON_GAMES}",
        flush=True
    )

    print(
        "=" * 70,
        flush=True
    )


    while True:

        try:

            games = get_active_games()


            if not games:

                time.sleep(
                    2
                )

                continue


            current_game_number = (
                get_game_number()
            )


            # ---------------------------------------------------------
            # Обрабатываем игры.
            # ---------------------------------------------------------

            for game in games:

                game_id = game.get(
                    "id"
                )


                if not game_id:

                    continue


                game_id = str(
                    game_id
                )


                # -----------------------------------------------------
                # Получаем данные.
                # -----------------------------------------------------

                raw_data = get_game_data(
                    game_id
                )


                if not raw_data:

                    continue


                parsed = parse_game_data(
                    game_id,
                    raw_data
                )


                if not parsed:

                    continue


                # -----------------------------------------------------
                # Если игра новая для истории.
                # -----------------------------------------------------

                if not game_exists(
                    history,
                    game_id
                ):

                    # -------------------------------------------------
                    # ВАЖНО:
                    #
                    # Прогноз делаем ДО добавления игры в историю.
                    # -------------------------------------------------

                    process_game(
                        history,
                        game_id,
                        current_game_number
                    )


                    # -------------------------------------------------
                    # Теперь добавляем игру.
                    # -------------------------------------------------

                    history = add_game_to_history(
                        history,
                        parsed
                    )


            # ---------------------------------------------------------
            # Проверяем результаты прогнозов.
            # ---------------------------------------------------------

            check_results(
                history
            )


            time.sleep(
                2
            )


        except KeyboardInterrupt:

            print(
                "🛑 Бот остановлен",
                flush=True
            )

            break


        except Exception as e:

            print(
                f"❌ MAIN ERROR: {e}",
                flush=True
            )

            time.sleep(
                3
            )


# =====================================================================
# START
# =====================================================================

history = load_history()

predictions = load_predictions()


if __name__ == "__main__":

    main_loop()