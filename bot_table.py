import os
import sys
import re
import json
import time
import requests
import pytz

from datetime import datetime


# =====================================================================
# ENV
# =====================================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    BOT_TOKEN = os.getenv("BOT_TOKEN_PROGNOZ")

CHANNEL_PROGNOZ = os.getenv("CHAT_ID_21")
if not CHANNEL_PROGNOZ:
    CHANNEL_PROGNOZ = os.getenv("CHANNEL_PROGNOZ")

CHANNEL_STATS = os.getenv("CHANNEL_STATS")


if not BOT_TOKEN:
    print("❌ BOT_TOKEN не задан", flush=True)
    sys.exit(1)

if not CHANNEL_PROGNOZ:
    print("❌ CHANNEL_PROGNOZ не задан", flush=True)
    sys.exit(1)

if not CHANNEL_STATS:
    print("❌ CHANNEL_STATS не задан", flush=True)
    sys.exit(1)


# =====================================================================
# CONFIG
# =====================================================================

MOSCOW_TZ = pytz.timezone("Europe/Moscow")

PREDICTIONS_FILE = "twentyone_predictions.json"
OFFSET_FILE = "telegram_offset.txt"

POLL_INTERVAL = 2.0

# После первого появления игры ждём 30 секунд,
# чтобы Telegram успел дописать все карты.
FINALIZE_WAIT_SECONDS = 30

# Догоны: 0, 1, 2, 3 — то есть целевая + 3 следующих.
DOGON_GAMES = 3


# =====================================================================
# TELEGRAM
# =====================================================================

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/150.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
})


# =====================================================================
# GLOBALS
# =====================================================================

# Только новые игры, пришедшие из CHANNEL_STATS.
games_cache = {}

# Игры, которые увидели, но ещё ждём 30 секунд.
pending_games = {}

# Триггеры, которые уже обработаны.
processed_triggers = set()

# Прогнозы.
predictions = []

# Telegram update offset.
telegram_offset = 0


# =====================================================================
# CARD NORMALIZATION
# =====================================================================

SUITS = {
    "♠": "♠️",
    "♣": "♣️",
    "♦": "♦️",
    "♥": "♥️",
}


def normalize_suit(suit):
    if suit is None:
        return None

    suit = str(suit).strip()
    suit = suit.replace("\ufe0f", "")

    return SUITS.get(suit)


def normalize_rank(rank):
    if rank is None:
        return None

    rank = str(rank).strip().upper()

    if rank == "А":
        rank = "A"

    if rank in {
        "6",
        "7",
        "8",
        "9",
        "10",
        "J",
        "Q",
        "K",
        "A",
    }:
        return rank

    return None


def card_to_text(card):
    if not card:
        return ""

    rank = normalize_rank(card.get("rank"))
    suit = normalize_suit(card.get("suit"))

    if not rank or not suit:
        return ""

    return f"{rank}{suit}"


def cards_to_text(cards):
    result = []

    for card in cards:
        value = card_to_text(card)

        if value:
            result.append(value)

    return " ".join(result)


# =====================================================================
# CYBER 21 SCORE
# =====================================================================

CARD_VALUES = {
    "6": 6,
    "7": 7,
    "8": 8,
    "9": 9,
    "10": 10,
    "J": 2,
    "Q": 3,
    "K": 4,
    "A": 11,
}


def cyber21_score(cards):
    total = 0

    for card in cards:
        rank = normalize_rank(card.get("rank"))

        if rank in CARD_VALUES:
            total += CARD_VALUES[rank]

    return total


# =====================================================================
# PARSE CARDS
# =====================================================================

CARD_RE = re.compile(
    r"(10|[6-9AJQK])\s*"
    r"(♠|♣|♦|♥)"
    r"\ufe0f?"
)


def parse_cards(text):
    """
    Разбор карт из текста Telegram.

    Например:

    6♥️A♥️8♠️

    ->

    [
        {"rank": "6", "suit": "♥️"},
        {"rank": "A", "suit": "♥️"},
        {"rank": "8", "suit": "♠️"}
    ]
    """

    result = []

    if not text:
        return result

    for match in CARD_RE.finditer(text):

        rank = normalize_rank(
            match.group(1)
        )

        suit = normalize_suit(
            match.group(2)
        )

        if not rank or not suit:
            continue

        result.append({
            "rank": rank,
            "suit": suit,
        })

    return result


# =====================================================================
# PARSE GAME MESSAGE
# =====================================================================

def parse_game_message(text):
    """
    Разбираем:

    #N492. 25(6♥️A♥️8♠️) - 20(10♦️K♥️) #T45

    Первая скобка = Player.
    Вторая скобка = Dealer.

    Напечатанные очки перед скобками НЕ используем.
    Считаем Cyber 21 самостоятельно.

    У дилера может быть 0 карт — тогда вторая скобка пустая.
    """

    if not text:
        return None

    number_match = re.search(
        r"#N(\d+)",
        text
    )

    if not number_match:
        return None

    game_number = int(
        number_match.group(1)
    )

    groups = re.findall(
        r"\(([^()]*)\)",
        text
    )

    if len(groups) < 2:
        return None

    player_text = groups[0]
    dealer_text = groups[1]

    player_cards = parse_cards(
        player_text
    )

    dealer_cards = parse_cards(
        dealer_text
    )

    # У игрока карты обязательны.
    # У дилера может быть 0 карт — это нормально.
    if not player_cards:
        return None

    player_score = cyber21_score(
        player_cards
    )

    dealer_score = cyber21_score(
        dealer_cards
    )

    id_match = re.search(
        r"ID:\s*(\d+)",
        text
    )

    game_id = (
        id_match.group(1)
        if id_match
        else None
    )

    # #X = ничья.
    is_draw = bool(
        re.search(
            r"#X\b",
            text
        )
    )

    return {
        "game_number": game_number,
        "game_id": game_id,

        "player_cards": player_cards,
        "dealer_cards": dealer_cards,

        "player_score": player_score,
        "dealer_score": dealer_score,

        "is_draw": is_draw,

        "raw_text": text,
    }


# =====================================================================
# LOG GAME
# =====================================================================

def log_game(game):

    player = game.get(
        "player_cards",
        []
    )

    dealer = game.get(
        "dealer_cards",
        []
    )

    print(
        "",
        flush=True
    )

    print(
        "────────────────────────────────────",
        flush=True
    )

    print(
        f"🔒 ИГРА ЗАФИКСИРОВАНА "
        f"#N{game['game_number']}",
        flush=True
    )

    print(
        f"👤 P: {game['player_score']} "
        f"({cards_to_text(player)})",
        flush=True
    )

    print(
        f"🎰 D: {game['dealer_score']} "
        f"({cards_to_text(dealer)})",
        flush=True
    )

    if game.get("is_draw"):
        print(
            "🔰 #X — НИЧЬЯ",
            flush=True
        )

    print(
        "────────────────────────────────────",
        flush=True
    )


# =====================================================================
# OFFSET
# =====================================================================

def load_offset():

    try:

        if os.path.exists(
            OFFSET_FILE
        ):

            with open(
                OFFSET_FILE,
                "r",
                encoding="utf-8"
            ) as f:

                return int(
                    f.read().strip()
                )

    except Exception:
        pass

    return 0


def save_offset(offset):

    try:

        with open(
            OFFSET_FILE,
            "w",
            encoding="utf-8"
        ) as f:

            f.write(
                str(offset)
            )

    except Exception as e:

        print(
            f"⚠️ Ошибка сохранения offset: {e}",
            flush=True
        )


# =====================================================================
# PREDICTIONS JSON
# =====================================================================

def load_predictions():

    global predictions

    try:

        if not os.path.exists(
            PREDICTIONS_FILE
        ):

            predictions = []
            return

        with open(
            PREDICTIONS_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(f)

        if isinstance(
            data,
            list
        ):

            predictions = data

        else:

            predictions = []

    except Exception as e:

        print(
            f"⚠️ Ошибка чтения "
            f"{PREDICTIONS_FILE}: {e}",
            flush=True
        )

        predictions = []


def save_predictions():

    try:

        tmp = (
            PREDICTIONS_FILE
            + ".tmp"
        )

        with open(
            tmp,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                predictions,
                f,
                ensure_ascii=False,
                indent=2
            )

        os.replace(
            tmp,
            PREDICTIONS_FILE
        )

    except Exception as e:

        print(
            f"⚠️ Ошибка сохранения "
            f"прогнозов: {e}",
            flush=True
        )


# =====================================================================
# TELEGRAM SEND
# =====================================================================

def telegram_send(text):

    try:

        response = SESSION.post(
            f"{TELEGRAM_API}/sendMessage",

            json={
                "chat_id": CHANNEL_PROGNOZ,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },

            timeout=10,
        )

        data = response.json()

        if data.get("ok"):

            return data[
                "result"
            ][
                "message_id"
            ]

        print(
            f"❌ Telegram sendMessage: {data}",
            flush=True
        )

    except Exception as e:

        print(
            f"❌ Ошибка отправки Telegram: {e}",
            flush=True
        )

    return None


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
                "chat_id": CHANNEL_PROGNOZ,
                "message_id": message_id,
                "text": text,
                "parse_mode": "HTML",
            },

            timeout=10,
        )

        return bool(
            response.json().get(
                "ok"
            )
        )

    except Exception as e:

        print(
            f"⚠️ Ошибка редактирования Telegram: {e}",
            flush=True
        )

    return False


# =====================================================================
# GAME NUMBER
# =====================================================================

def add_game_offset(
    number,
    offset
):

    return (
        (
            int(number)
            - 1
            + int(offset)
        )
        % 1440
    ) + 1


# =====================================================================
# ALGORITHM: ПОСЛЕДНЯЯ 10
# =====================================================================

def get_last_card_prediction(game):
    """
    Алгоритм "Последняя 10".

    Триггер:
        - карты только у игрока
        - у дилера 0 карт ()
        - последняя карта игрока — 10
        - есть знак ✅

    Целевая игра (догон 0):
        game_number + количество карт игрока

    Масть:
        масть десятки из триггера

    Догоны: 0, 1, 2, 3
    """

    player = game.get("player_cards", [])
    dealer = game.get("dealer_cards", [])

    # У игрока должны быть карты
    if not player:
        return None

    # У дилера должно быть 0 карт
    if dealer:
        return None

    # Последняя карта игрока должна быть 10
    last_card = player[-1]
    last_rank = normalize_rank(last_card.get("rank"))

    if last_rank != "10":
        return None

    # В игре должен быть знак ✅
    if "✅" not in game.get("raw_text", ""):
        return None

    # Масть десятки
    suit = normalize_suit(last_card.get("suit"))

    if not suit:
        return None

    # Целевая игра = номер триггера + количество карт игрока
    target_offset = len(player)
    target_number = add_game_offset(
        game["game_number"],
        target_offset
    )

    return {
        "algorithm": "последняя 10",
        "trigger_number": game["game_number"],
        "trigger_game_id": game.get("game_id"),
        "target_number": target_number,
        "predicted_rank": "10",
        "predicted_suits": [suit],
        "predicted_cards": [f"10{suit}"],
        "trigger_player": [card_to_text(c) for c in player],
        "trigger_dealer": [card_to_text(c) for c in dealer],
        "trigger_player_score": game["player_score"],
        "trigger_dealer_score": game["dealer_score"],
        "status": "pending",
        "created_at": datetime.now(MOSCOW_TZ).isoformat(),
        "result_game": None,
        "found_card": None,
        "dogon": None,
        "message_id": None,
        "target_offset": target_offset,
    }


# =====================================================================
# TRIGGERS / CREATE PREDICTION
# =====================================================================

def get_algorithm_predictions(game):
    prediction = get_last_card_prediction(game)
    return [prediction] if prediction else []

# =====================================================================
# PREDICTION MESSAGE
# =====================================================================

def make_prediction_message(prediction):
    cards = prediction["predicted_cards"]
    algorithm = prediction.get("algorithm", "последняя 10")
    target_offset = prediction.get("target_offset", 0)

    return (
        f"🔮 <b>ТОЧНАЯ КАРТА</b>\n\n"
        f"🧠 Алгоритм: <b>{algorithm}</b>\n"
        f"🎯 Игра: <b>#N{prediction['target_number']}</b>\n"
        f"🃏 <b>{cards[0]}</b>\n\n"
        f"⏩ Прогноз: <b>+{target_offset}</b>\n"
        f"🔄 Догон: <b>{DOGON_GAMES}</b>"
    )


# =====================================================================
# CREATE PREDICTIONS
# =====================================================================

def create_predictions(game):
    game_number = game["game_number"]

    for prediction in get_algorithm_predictions(game):
        algorithm = prediction["algorithm"]
        trigger_key = (algorithm, game_number)

        if trigger_key in processed_triggers:
            continue

        target_number = prediction["target_number"]

        # Не создаём второй активный прогноз на ту же целевую игру
        # внутри ОДНОГО алгоритма. Разные алгоритмы могут иметь одну цель.
        already_exists = any(
            entry.get("algorithm") == algorithm
            and entry.get("target_number") == target_number
            and entry.get("status") == "pending"
            for entry in predictions
        )

        if already_exists:
            processed_triggers.add(trigger_key)
            continue

        message = make_prediction_message(prediction)
        message_id = telegram_send(message)

        if not message_id:
            print(
                f"❌ Прогноз не отправлен — алгоритм {algorithm}, "
                f"триггер #N{game_number}",
                flush=True,
            )
            continue

        prediction["message_id"] = message_id
        predictions.append(prediction)
        processed_triggers.add(trigger_key)
        save_predictions()

        print("", flush=True)
        print("🔮 ПРОГНОЗ СОЗДАН", flush=True)
        print(f"🧠 Алгоритм: {algorithm}", flush=True)
        print(f"🎯 Цель: #N{target_number}", flush=True)
        print(f"🃏 {prediction['predicted_cards'][0]}", flush=True)
        print(f"📌 Триггер: #N{game_number}", flush=True)


def create_prediction(game):
    """Совместимый вызов: создаёт прогнозы обоих алгоритмов."""
    create_predictions(game)


# =====================================================================
# CHECK PLAYER CARD
# =====================================================================

def check_prediction_cards(
    game,
    predicted_cards
):
    """
    Проверяем Player И Dealer.

    Если прогнозируемая карта найдена
    у Player ИЛИ у Dealer — сразу PLUS.
    """

    player_cards = game.get(
        "player_cards",
        []
    )

    dealer_cards = game.get(
        "dealer_cards",
        []
    )

    actual_cards = []

    for card in player_cards + dealer_cards:

        text = card_to_text(
            card
        )

        if text:
            actual_cards.append(
                text
            )

    for predicted in predicted_cards:

        if predicted in actual_cards:

            return predicted

    return None


# =====================================================================
# RESULT MESSAGE
# =====================================================================

def make_result_message(
    prediction,
    result,
    result_game,
    dogon
):

    target = prediction[
        "target_number"
    ]

    lines = [

        (
            f"🎯 Игра: "
            f"<b>#N{target}</b> "
            f"{'✅' if result == 'win' else '❌'}"
        ),
        (
            f"🧠 Алгоритм: "
            f"<b>{prediction.get('algorithm', 'последняя 10')}</b>"
        ),

        "",

        (
            f"🃏 Прогноз: "
            f"<b>{prediction['predicted_cards'][0]}</b>"
        ),
    ]

    if result == "win":

        lines.extend([

            "",

            "✅ <b>PLUS</b>",

            (
                f"🎯 Результат: "
                f"<b>#N{result_game}</b>"
            ),

            (
                f"🃏 Карта: "
                f"<b>{prediction['found_card']}</b>"
            ),

            (
                f"🔄 Догон: "
                f"<b>{dogon}</b>"
            ),
        ])

    else:

        lines.extend([

            "",

            "❌ <b>МИНУС</b>",

            (
                f"🏁 Проверены игры: "
                f"#N{target} + "
                f"{DOGON_GAMES} догонов"
            ),
        ])

    return "\n".join(
        lines
    )


# =====================================================================
# CHECK PREDICTIONS
# =====================================================================

def check_predictions():
    """
    Прогноз проверяется строго последовательно:
    целевая игра, затем догоны 1, 2, 3.

    Минус — только если все 4 игры реально появились,
    и ни в одной не было нужной карты.
    """

    changed = False

    for prediction in predictions:

        if prediction.get(
            "status"
        ) != "pending":

            continue

        target = prediction.get(
            "target_number"
        )

        if not target:
            continue

        predicted_cards = prediction.get(
            "predicted_cards",
            []
        )

        if not predicted_cards:
            continue

        # =============================================================
        # ПРОВЕРЯЕМ СТРОГО ПО ПОРЯДКУ
        # =============================================================

        all_games_checked = True

        for dogon in range(
            0,
            DOGON_GAMES + 1
        ):

            game_number = add_game_offset(
                target,
                dogon
            )

            game = games_cache.get(
                game_number
            )

            # =========================================================
            # ИГРА ЕЩЁ НЕ ПОЯВИЛАСЬ
            # =========================================================

            if not game:

                all_games_checked = False

                print(
                    f"⏳ #N{target}: "
                    f"ждём #N{game_number} "
                    f"(догон {dogon})",
                    flush=True
                )

                break

            # =========================================================
            # ИГРА ЕСТЬ — ПРОВЕРЯЕМ PLAYER И DEALER
            # =========================================================

            found_card = check_prediction_cards(
                game,
                predicted_cards
            )

            if found_card:

                prediction[
                    "status"
                ] = "win"

                prediction[
                    "result_game"
                ] = game_number

                prediction[
                    "found_card"
                ] = found_card

                prediction[
                    "dogon"
                ] = dogon

                new_message = make_result_message(
                    prediction,
                    "win",
                    game_number,
                    dogon
                )

                telegram_edit(
                    prediction.get(
                        "message_id"
                    ),
                    new_message
                )

                print(
                    "",
                    flush=True
                )

                print(
                    f"✅ PLUS #N{target}",
                    flush=True
                )

                print(
                    f"🎯 Карта "
                    f"{found_card} "
                    f"найдена в #N{game_number}",
                    flush=True
                )

                print(
                    f"🔄 Догон: {dogon}",
                    flush=True
                )

                changed = True

                all_games_checked = False

                break

            # =========================================================
            # КАРТЫ НЕТ
            # =========================================================

            if game.get(
                "is_draw"
            ):

                print(
                    f"🔰 #N{game_number} — "
                    f"#X, нужной карты нет → "
                    f"переходим к следующему",
                    flush=True
                )

            else:

                print(
                    f"🔍 #N{game_number} — "
                    f"нужной карты нет → "
                    f"переходим к следующему",
                    flush=True
                )

        # =============================================================
        # НЕ ВСЕ ИГРЫ ЕЩЁ ПОЛУЧЕНЫ
        # =============================================================

        if not all_games_checked:

            continue

        # =============================================================
        # СЮДА ПОПАДАЕМ ТОЛЬКО ЕСЛИ:
        #
        # целевая + все догоны реально появились,
        # и ни в одной нет нужной карты.
        # =============================================================

        prediction[
            "status"
        ] = "lose"

        prediction[
            "result_game"
        ] = add_game_offset(
            target,
            DOGON_GAMES
        )

        prediction[
            "dogon"
        ] = DOGON_GAMES

        new_message = make_result_message(
            prediction,
            "lose",
            target,
            DOGON_GAMES
        )

        telegram_edit(
            prediction.get(
                "message_id"
            ),
            new_message
        )

        print(
            "",
            flush=True
        )

        print(
            f"❌ MINUS #N{target}",
            flush=True
        )

        print(
            f"🏁 Реально проверены "
            f"все игры "
            f"#N{target} — "
            f"#N{add_game_offset(target, DOGON_GAMES)}",
            flush=True
        )

        changed = True

    if changed:
        save_predictions()


# =====================================================================
# FINALIZE PENDING GAMES
# =====================================================================

def finalize_pending_games():

    now = time.time()

    ready = []

    for game_number, info in list(
        pending_games.items()
    ):

        first_seen = info.get(
            "first_seen",
            now
        )

        if (
            now - first_seen
            >= FINALIZE_WAIT_SECONDS
        ):

            ready.append(
                game_number
            )

    for game_number in ready:

        info = pending_games.pop(
            game_number,
            None
        )

        if not info:
            continue

        text = info.get(
            "text",
            ""
        )

        game = parse_game_message(
            text
        )

        if not game:

            print(
                f"⚠️ #N{game_number} "
                f"не удалось разобрать "
                f"после 30 секунд",
                flush=True
            )

            continue

        games_cache[
            game_number
        ] = game

        log_game(
            game
        )

        create_prediction(
            game
        )


# =====================================================================
# TELEGRAM UPDATES
# =====================================================================

def process_telegram_updates(
    offset
):

    try:

        response = SESSION.get(

            f"{TELEGRAM_API}/getUpdates",

            params={
                "offset": offset,
                "timeout": 3,
                "limit": 50,

                "allowed_updates":
                    json.dumps([
                        "channel_post",
                        "edited_channel_post"
                    ]),
            },

            timeout=10,
        )

        data = response.json()

        if not data.get("ok"):

            print(
                f"❌ Telegram getUpdates: "
                f"{data}",
                flush=True
            )

            return offset

        updates = data.get(
            "result",
            []
        )

        for update in updates:

            update_id = update.get(
                "update_id"
            )

            if update_id is not None:

                offset = (
                    update_id + 1
                )

                save_offset(
                    offset
                )

            post = (
                update.get(
                    "channel_post"
                )
                or
                update.get(
                    "edited_channel_post"
                )
            )

            if not post:
                continue

            chat = post.get(
                "chat",
                {}
            )

            chat_id = str(
                chat.get(
                    "id",
                    ""
                )
            )

            if chat_id != str(
                CHANNEL_STATS
            ):

                continue

            text = post.get(
                "text",
                ""
            )

            if not text:
                continue

            number_match = re.search(
                r"#N(\d+)",
                text
            )

            if not number_match:
                continue

            game_number = int(
                number_match.group(1)
            )

            # =========================================================
            # Сначала пробуем разобрать игру.
            # =========================================================

            game = parse_game_message(
                text
            )

            # =========================================================
            # Если игра уже ожидает финализации,
            # обновляем её последней версией.
            # =========================================================

            if game_number in pending_games:

                pending_games[
                    game_number
                ]["text"] = text

                print(
                    f"🔄 Обновлена игра "
                    f"#N{game_number} "
                    f"до окончания "
                    f"{FINALIZE_WAIT_SECONDS} секунд",
                    flush=True
                )

                continue

            # =========================================================
            # Если игра уже зафиксирована,
            # обновляем её в кэше.
            # =========================================================

            if game_number in games_cache:

                if game:

                    games_cache[
                        game_number
                    ] = game

                    print(
                        f"🔄 Обновлена "
                        f"завершённая "
                        f"игра #N{game_number}",
                        flush=True
                    )

                continue

            # =========================================================
            # Новая завершённая игра.
            # =========================================================

            if re.search(
                r"[✅🔰]",
                text
            ):

                if game_number in processed_triggers:
                    continue

                pending_games[
                    game_number
                ] = {

                    "first_seen":
                        time.time(),

                    "text":
                        text,
                }

                print(
                    "",
                    flush=True
                )

                print(
                    f"👀 НОВАЯ ИГРА "
                    f"#N{game_number}",
                    flush=True
                )

                print(
                    "⏳ Увидели завершение — "
                    f"ждём "
                    f"{FINALIZE_WAIT_SECONDS} секунд",
                    flush=True
                )

    except Exception as e:

        print(
            f"⚠️ Updates error: {e}",
            flush=True
        )

    return offset


# =====================================================================
# CLEANUP
# =====================================================================

def cleanup_games_cache():

    if len(games_cache) <= 100:
        return

    numbers = sorted(
        games_cache.keys()
    )

    keep = numbers[-100:]

    keep_set = set(
        keep
    )

    for number in list(
        games_cache.keys()
    ):

        if number not in keep_set:

            del games_cache[
                number
            ]


def cleanup_predictions():

    global predictions

    if len(predictions) > 1000:

        predictions = predictions[
            -1000:
        ]

        save_predictions()


# =====================================================================
# MAIN
# =====================================================================

def main():

    global telegram_offset

    print(
        "",
        flush=True
    )

    print(
        "==================================================",
        flush=True
    )

    print(
        "🚀 CYBER 21 — TELEGRAM STATS FORECAST",
        flush=True
    )

    print(
        "==================================================",
        flush=True
    )

    print(
        "📡 Игры: CHANNEL_STATS",
        flush=True
    )

    print(
        f"⏳ Финализация: "
        f"{FINALIZE_WAIT_SECONDS} сек",
        flush=True
    )

    print(
        "🧠 Алгоритм: последняя 10 "
        "(карты только у игрока, "
        "у дилера 0 карт, есть ✅)",
        flush=True
    )

    print(
        f"🔄 Догонов: "
        f"{DOGON_GAMES} "
        f"(0, 1, 2, ..., {DOGON_GAMES})",
        flush=True
    )

    print(
        "🧮 Cyber 21: "
        "6-10 = номинал, "
        "J=2, Q=3, K=4, A=11",
        flush=True
    )

    print(
        "==================================================",
        flush=True
    )

    load_predictions()

    telegram_offset = load_offset()

    print(
        f"📌 Telegram offset: "
        f"{telegram_offset}",
        flush=True
    )

    print(
        f"📊 Загружено прогнозов: "
        f"{len(predictions)}",
        flush=True
    )

    print(
        "==================================================",
        flush=True
    )

    while True:

        try:

            # ---------------------------------------------------------
            # 1. Получаем новые игры.
            # ---------------------------------------------------------

            telegram_offset = (
                process_telegram_updates(
                    telegram_offset
                )
            )

            # ---------------------------------------------------------
            # 2. Финализируем игры после 30 секунд.
            # ---------------------------------------------------------

            finalize_pending_games()

            # ---------------------------------------------------------
            # 3. Проверяем прогнозы.
            # ---------------------------------------------------------

            check_predictions()

            # ---------------------------------------------------------
            # 4. Очистка.
            # ---------------------------------------------------------

            cleanup_games_cache()

            cleanup_predictions()

            time.sleep(
                POLL_INTERVAL
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

            time.sleep(
                3
            )


# =====================================================================
# START
# =====================================================================

if __name__ == "__main__":
    main()