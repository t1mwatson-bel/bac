import os
import sys
import re
import json
import time
import requests
import pytz

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

# После появления завершённой игры ждём 30 секунд,
# чтобы Telegram-статистика успела дописать все карты.
FINALIZE_WAIT_SECONDS = 30

# N492 -> N495
FORECAST_OFFSET = 3

# Целевая игра + 4 следующих догона.
DOGON_GAMES = 4


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

# Новые завершённые игры из CHANNEL_STATS.
games_cache = {}

# Игры, которые впервые увидели завершёнными,
# но ещё не прошло 30 секунд.
pending_games = {}

# Номера игр, которые уже использовались как триггер.
processed_triggers = set()

# Прогнозы.
predictions = []

# Последний update Telegram.
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

    # Убираем variation selector.
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
    return " ".join(
        card_to_text(card)
        for card in cards
        if card_to_text(card)
    )


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
    Рабочий разбор карт из Telegram-текста.

    Возвращает:
    [
        {"rank": "6", "suit": "♥️"},
        ...
    ]
    """

    result = []

    if not text:
        return result

    for match in CARD_RE.finditer(text):
        rank = normalize_rank(match.group(1))
        suit = normalize_suit(match.group(2))

        if not rank or not suit:
            continue

        result.append({
            "rank": rank,
            "suit": suit,
        })

    return result


# =====================================================================
# PARSE PLAYER / DEALER
# =====================================================================

def parse_game_message(text):
    """
    Разбирает именно структуру:

    #N492. 25(6♥A♥8♠) - 20(10♦K♥) #T45 (ID: ...)

    Первые скобки = Player.
    Вторые скобки = Dealer.

    Числа перед скобками НЕ используются для расчёта.
    Очки считаются самостоятельно по Cyber 21.
    """

    if not text:
        return None

    number_match = re.search(r"#N(\d+)", text)

    if not number_match:
        return None

    game_number = int(number_match.group(1))

    # Находим содержимое скобок.
    groups = re.findall(r"\(([^()]*)\)", text)

    if len(groups) < 2:
        return None

    player_text = groups[0]
    dealer_text = groups[1]

    player_cards = parse_cards(player_text)
    dealer_cards = parse_cards(dealer_text)

    if not player_cards or not dealer_cards:
        return None

    player_score = cyber21_score(player_cards)
    dealer_score = cyber21_score(dealer_cards)

    # ID игры из сообщения, если есть.
    id_match = re.search(r"ID:\s*(\d+)", text)
    game_id = id_match.group(1) if id_match else None

    # #X = ничья.
    is_draw = bool(re.search(r"#X\b", text))

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
    player = game.get("player_cards", [])
    dealer = game.get("dealer_cards", [])

    print("", flush=True)
    print("────────────────────────────────────", flush=True)
    print(
        f"🔒 ИГРА ЗАФИКСИРОВАНА #N{game['game_number']}",
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
        print("🔰 #X — НИЧЬЯ", flush=True)

    print("────────────────────────────────────", flush=True)


# =====================================================================
# OFFSET
# =====================================================================

def load_offset():
    try:
        if os.path.exists(OFFSET_FILE):
            with open(OFFSET_FILE, "r", encoding="utf-8") as f:
                return int(f.read().strip())
    except Exception:
        pass

    return 0


def save_offset(offset):
    try:
        with open(OFFSET_FILE, "w", encoding="utf-8") as f:
            f.write(str(offset))
    except Exception as e:
        print(f"⚠️ Ошибка сохранения offset: {e}", flush=True)


# =====================================================================
# PREDICTIONS JSON
# =====================================================================

def load_predictions():
    global predictions

    try:
        if not os.path.exists(PREDICTIONS_FILE):
            predictions = []
            return

        with open(
            PREDICTIONS_FILE,
            "r",
            encoding="utf-8"
        ) as f:
            data = json.load(f)

        if isinstance(data, list):
            predictions = data
        else:
            predictions = []

    except Exception as e:
        print(
            f"⚠️ Ошибка чтения {PREDICTIONS_FILE}: {e}",
            flush=True
        )
        predictions = []


def save_predictions():
    try:
        tmp = PREDICTIONS_FILE + ".tmp"

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

        os.replace(tmp, PREDICTIONS_FILE)

    except Exception as e:
        print(
            f"⚠️ Ошибка сохранения прогнозов: {e}",
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
            return data["result"]["message_id"]

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
                "parse_mode": "HTML",
            },
            timeout=10,
        )

        return bool(response.json().get("ok"))

    except Exception as e:
        print(
            f"⚠️ Ошибка редактирования Telegram: {e}",
            flush=True
        )

    return False


# =====================================================================
# GAME NUMBER
# =====================================================================

def add_game_offset(number, offset):
    """
    Номер игры + offset.
    Диапазон 1..1440.
    """

    return ((int(number) - 1 + int(offset)) % 1440) + 1


# =====================================================================
# TRIGGER
# =====================================================================

def get_trigger_prediction(game):
    """
    Триггер:

    1. Первая карта Player:
       6 или J -> прогноз J
       7 или Q -> прогноз Q
       8 или K -> прогноз K

    2. Первая карта Dealer = 10.

    3. Третья карта Player строго:
       6 / 7 / 8 / 9

    4. Игра-триггер не должна быть:
       Player = 21
       Dealer = 21
       Draw (#X)

    5. Прогноз идёт на +3 игры.
    """

    player = game.get("player_cards", [])
    dealer = game.get("dealer_cards", [])

    if len(player) < 3:
        return None

    if len(dealer) < 1:
        return None

    # Ничья.
    if game.get("is_draw"):
        return None

    # 21 по Cyber 21.
    if game.get("player_score") == 21:
        return None

    if game.get("dealer_score") == 21:
        return None

    first_player_rank = normalize_rank(
        player[0].get("rank")
    )

    first_dealer_rank = normalize_rank(
        dealer[0].get("rank")
    )

    third_player_rank = normalize_rank(
        player[2].get("rank")
    )

    # Dealer должен начинать с 10.
    if first_dealer_rank != "10":
        return None

    # Третья карта Player только 6/7/8/9.
    if third_player_rank not in {
        "6",
        "7",
        "8",
        "9",
    }:
        return None

    # Первая карта Player определяет ранг прогноза.
    rank_mapping = {
        "6": "J",
        "J": "J",

        "7": "Q",
        "Q": "Q",

        "8": "K",
        "K": "K",
    }

    predicted_rank = rank_mapping.get(
        first_player_rank
    )

    if not predicted_rank:
        return None

    # Масть определяется количеством карт Player.
    player_count = len(player)

    suit_pairs = {
        2: ["♠️", "♦️"],
        3: ["♣️", "♥️"],
        4: ["♥️", "♣️"],
        5: ["♦️", "♠️"],
    }

    predicted_suits = suit_pairs.get(player_count)

    if not predicted_suits:
        print(
            f"⏭️ #N{game['game_number']} — "
            f"для {player_count} карт Player нет пары мастей",
            flush=True
        )
        return None

    predicted_cards = [
        f"{predicted_rank}{predicted_suits[0]}",
        f"{predicted_rank}{predicted_suits[1]}",
    ]

    return {
        "trigger_number": game["game_number"],
        "trigger_game_id": game.get("game_id"),

        "target_number": add_game_offset(
            game["game_number"],
            FORECAST_OFFSET
        ),

        "predicted_rank": predicted_rank,
        "predicted_suits": predicted_suits,
        "predicted_cards": predicted_cards,

        "trigger_player": [
            card_to_text(c)
            for c in player
        ],

        "trigger_dealer": [
            card_to_text(c)
            for c in dealer
        ],

        "trigger_player_score": game["player_score"],
        "trigger_dealer_score": game["dealer_score"],

        "status": "pending",

        "created_at": datetime.now(
            MOSCOW_TZ
        ).isoformat(),

        "result_game": None,
        "found_card": None,
        "dogon": None,

        "message_id": None,
    }


# =====================================================================
# PREDICTION MESSAGE
# =====================================================================

def make_prediction_message(prediction):
    cards = prediction["predicted_cards"]

    return (
        f"🔮 <b>ТОЧНАЯ КАРТА</b>\n\n"
        f"🎯 Игра: <b>#N{prediction['target_number']}</b>\n"
        f"📌 Триггер: #N{prediction['trigger_number']}\n\n"
        f"🃏 <b>{cards[0]}</b>\n"
        f"🃏 <b>{cards[1]}</b>\n\n"
        f"⏩ Прогноз: <b>+{FORECAST_OFFSET}</b>\n"
        f"🔄 Догон: <b>{DOGON_GAMES}</b>"
    )


# =====================================================================
# CREATE PREDICTION
# =====================================================================

def create_prediction(game):
    game_number = game["game_number"]

    if game_number in processed_triggers:
        return

    prediction = get_trigger_prediction(game)

    if not prediction:
        processed_triggers.add(game_number)

        print(
            f"⏭️ #{game_number} — не триггер",
            flush=True
        )

        return

    target_number = prediction["target_number"]

    # Не создаём второй активный прогноз на тот же target.
    for entry in predictions:
        if (
            entry.get("target_number") == target_number
            and entry.get("status") == "pending"
        ):
            print(
                f"⏭️ Прогноз на #N{target_number} уже существует",
                flush=True
            )

            processed_triggers.add(game_number)
            return

    message = make_prediction_message(prediction)

    message_id = telegram_send(message)

    if not message_id:
        print(
            "❌ Прогноз не отправлен — "
            "не помечаем триггер обработанным",
            flush=True
        )
        return

    prediction["message_id"] = message_id

    predictions.append(prediction)

    save_predictions()

    processed_triggers.add(game_number)

    print("", flush=True)
    print("🔮 ПРОГНОЗ СОЗДАН", flush=True)
    print(
        f"🎯 Цель: #N{target_number}",
        flush=True
    )
    print(
        f"🃏 {prediction['predicted_cards'][0]}",
        flush=True
    )
    print(
        f"🃏 {prediction['predicted_cards'][1]}",
        flush=True
    )
    print(
        f"📌 Триггер: #N{game_number}",
        flush=True
    )


# =====================================================================
# CHECK PLAYER CARD
# =====================================================================

def check_prediction_cards(game, predicted_cards):
    """
    Проверяем ТОЛЬКО карты Player.

    Если нужная карта появилась у Player —
    это PLUS сразу.

    Никаких проверок Dealer.
    """

    player_cards = game.get("player_cards", [])

    actual_player_cards = [
        card_to_text(card)
        for card in player_cards
    ]

    for predicted in predicted_cards:
        if predicted in actual_player_cards:
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
    target = prediction["target_number"]

    lines = [
        (
            f"🎯 Игра: <b>#N{target}</b> "
            f"{'✅' if result == 'win' else '❌'}"
        ),
        "",
        (
            f"🃏 Прогноз: "
            f"<b>{prediction['predicted_cards'][0]}</b> / "
            f"<b>{prediction['predicted_cards'][1]}</b>"
        ),
    ]

    if result == "win":
        lines.extend([
            "",
            f"✅ <b>PLUS</b>",
            f"🎯 Результат: <b>#N{result_game}</b>",
            f"🃏 Карта: <b>{prediction['found_card']}</b>",
            f"🔄 Догон: <b>{dogon}</b>",
        ])

    else:
        lines.extend([
            "",
            "❌ <b>МИНУС</b>",
            f"🏁 Проверены игры: #N{target} + {DOGON_GAMES} догонов",
        ])

    return "\n".join(lines)


# =====================================================================
# CHECK PREDICTIONS
# =====================================================================

def check_predictions():
    """
    Проверяем:
    target + 4 dogons.

    ВАЖНО:

    Даже если конкретная игра = 21
    или #X,
    если нужная карта есть у Player,
    прогноз сразу PLUS.

    Если 21/#X и нужной карты нет —
    продолжаем следующий догон.
    """

    changed = False

    for prediction in predictions:

        if prediction.get("status") != "pending":
            continue

        target = prediction.get("target_number")

        if not target:
            continue

        predicted_cards = prediction.get(
            "predicted_cards",
            []
        )

        if not predicted_cards:
            continue

        # Проверяем target + DOGON_GAMES.
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

            if not game:
                # Игра ещё не пришла из канала.
                continue

            found_card = check_prediction_cards(
                game,
                predicted_cards
            )

            # =========================================================
            # PLUS
            # =========================================================

            if found_card:

                prediction["status"] = "win"
                prediction["result_game"] = game_number
                prediction["found_card"] = found_card
                prediction["dogon"] = dogon

                new_message = make_result_message(
                    prediction,
                    "win",
                    game_number,
                    dogon
                )

                telegram_edit(
                    prediction.get("message_id"),
                    new_message
                )

                print("", flush=True)
                print(
                    f"✅ PLUS #N{target}",
                    flush=True
                )
                print(
                    f"🎯 Карта {found_card} "
                    f"найдена у Player в #N{game_number}",
                    flush=True
                )
                print(
                    f"🔄 Догон: {dogon}",
                    flush=True
                )

                changed = True
                break

            # =========================================================
            # НЕТ КАРТЫ
            # =========================================================

            if game.get("is_draw"):
                print(
                    f"🔰 #N{game_number} — #X, "
                    f"карты нет → продолжаем",
                    flush=True
                )

            elif (
                game.get("player_score") == 21
                or game.get("dealer_score") == 21
            ):
                print(
                    f"21 в #N{game_number}, "
                    f"нужной карты нет → продолжаем",
                    flush=True
                )

            else:
                print(
                    f"🔍 #N{game_number} — "
                    f"нужной карты нет → продолжаем",
                    flush=True
                )

        else:
            # Цикл завершился без break.
            # Значит проверены все target + 4 dogons.
            prediction["status"] = "lose"

            new_message = make_result_message(
                prediction,
                "lose",
                target,
                DOGON_GAMES
            )

            telegram_edit(
                prediction.get("message_id"),
                new_message
            )

            print("", flush=True)
            print(
                f"❌ MINUS #N{target}",
                flush=True
            )
            print(
                f"🔄 Проверены target + "
                f"{DOGON_GAMES} догонов",
                flush=True
            )

            changed = True

    if changed:
        save_predictions()


# =====================================================================
# FINALIZE PENDING GAME
# =====================================================================

def finalize_pending_games():
    """
    После первого появления завершённой игры
    ждём 30 секунд.

    Затем повторно используем последнее полученное
    сообщение этой игры.
    """

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
            ready.append(game_number)

    for game_number in ready:

        info = pending_games.pop(
            game_number,
            None
        )

        if not info:
            continue

        text = info.get("text", "")

        # Если за 30 секунд пришла новая версия
        # того же сообщения — берём последнюю.
        game = parse_game_message(text)

        if not game:
            print(
                f"⚠️ #N{game_number} "
                f"не удалось разобрать после 30 секунд",
                flush=True
            )
            continue

        # Сохраняем последнюю финальную версию.
        games_cache[game_number] = game

        log_game(game)

        create_prediction(game)


# =====================================================================
# PROCESS TELEGRAM UPDATE
# =====================================================================

def process_telegram_updates(offset):
    """
    Получаем только новые updates Telegram.

    Источник игр:
    CHANNEL_STATS.

    Никакого API для получения игр здесь нет.
    """

    try:

        response = SESSION.get(
            f"{TELEGRAM_API}/getUpdates",
            params={
                "offset": offset,
                "timeout": 3,
                "limit": 50,
                "allowed_updates": json.dumps([
                    "channel_post",
                    "edited_channel_post"
                ]),
            },
            timeout=10,
        )

        data = response.json()

        if not data.get("ok"):
            print(
                f"❌ Telegram getUpdates: {data}",
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
                offset = update_id + 1
                save_offset(offset)

            # Обычный пост или отредактированный пост.
            post = (
                update.get("channel_post")
                or update.get("edited_channel_post")
            )

            if not post:
                continue

            chat = post.get(
                "chat",
                {}
            )

            chat_id = str(
                chat.get("id", "")
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
            # ОБНОВЛЯЕМ ПОСЛЕДНЮЮ ВЕРСИЮ ИГРЫ
            # =========================================================

            game = parse_game_message(
                text
            )

            if game:

                # Если игра уже находится в ожидании,
                # просто обновляем её текст.
                if game_number in pending_games:

                    pending_games[
                        game_number
                    ]["text"] = text

                    print(
                        f"🔄 Обновлена игра "
                        f"#N{game_number} "
                        f"до окончания 30 секунд",
                        flush=True
                    )

                    continue

                # Если игра уже зафиксирована,
                # обновляем кэш для проверки прогнозов.
                if game_number in games_cache:

                    games_cache[
                        game_number
                    ] = game

                    print(
                        f"🔄 Обновлена завершённая "
                        f"игра #N{game_number}",
                        flush=True
                    )

                    continue

            # =========================================================
            # ПЕРВОЕ ПОЯВЛЕНИЕ ЗАВЕРШЁННОЙ ИГРЫ
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
                    "first_seen": time.time(),
                    "text": text,
                }

                print("", flush=True)
                print(
                    f"👀 НОВАЯ ИГРА #N{game_number}",
                    flush=True
                )
                print(
                    "⏳ Увидели завершение — "
                    "ждём 30 секунд",
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
    """
    Не даём кэшу расти бесконечно.
    Для проверки прогнозов достаточно хранить
    последние несколько десятков игр.
    """

    if len(games_cache) <= 100:
        return

    numbers = sorted(
        games_cache.keys()
    )

    keep = numbers[-100:]

    keep_set = set(keep)

    for number in list(
        games_cache.keys()
    ):

        if number not in keep_set:
            del games_cache[number]


def cleanup_predictions():
    global predictions

    if len(predictions) > 1000:
        predictions = predictions[-1000:]
        save_predictions()


# =====================================================================
# MAIN
# =====================================================================

def main():

    global telegram_offset

    print("", flush=True)
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
        f"⏳ Ожидание финализации: "
        f"{FINALIZE_WAIT_SECONDS} сек",
        flush=True
    )

    print(
        f"🎯 Смещение прогноза: +{FORECAST_OFFSET}",
        flush=True
    )

    print(
        f"🔄 Догонов: {DOGON_GAMES}",
        flush=True
    )

    print(
        "🧮 Cyber 21: "
        "6-10 = номинал, J=2, Q=3, K=4, A=11",
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
            # 1. Получаем НОВЫЕ игры из CHANNEL_STATS.
            # ---------------------------------------------------------

            telegram_offset = (
                process_telegram_updates(
                    telegram_offset
                )
            )

            # ---------------------------------------------------------
            # 2. Ждём 30 секунд и фиксируем игры.
            # ---------------------------------------------------------

            finalize_pending_games()

            # ---------------------------------------------------------
            # 3. Проверяем уже созданные прогнозы.
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

            time.sleep(3)


# =====================================================================
# START
# =====================================================================

if __name__ == "__main__":
    main()
