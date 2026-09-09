import os
import sys
import json
import time
import requests
import pytz
import re

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
    print("❌ BOT_TOKEN не задан!", flush=True)
    sys.exit(1)

if not CHANNEL_STATS:
    print("❌ CHANNEL_STATS не задан!", flush=True)
    sys.exit(1)

if not CHANNEL_PROGNOZ:
    print("❌ CHANNEL_PROGNOZ не задан!", flush=True)
    sys.exit(1)


# =====================================================================
# CONFIG
# =====================================================================

MOSCOW_TZ = pytz.timezone("Europe/Moscow")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

OFFSET_FILE = "new_method_offset.txt"
RESULTS_FILE = "new_method_results.json"

POLL_INTERVAL = 2.0

# После первого появления ✅ ждём 30 секунд
GAME_RECHECK_SECONDS = 30

# Две игры между триггером и целью:
# #N492 -> #N495
TARGET_OFFSET = 3

# Целевая игра + 4 догона
DOGON_GAMES = 4


# =====================================================================
# SESSION
# =====================================================================

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/150 Safari/537.36"
    )
})


# =====================================================================
# GLOBALS
# =====================================================================

telegram_offset = 0

# Игры, которые впервые увидели как завершённые.
#
# Формат:
# {
#     game_number: {
#         "game": {...},
#         "first_seen": timestamp
#     }
# }
#
pending_games = {}

# Окончательно зафиксированные игры
games_cache = {}

# Прогнозы
predictions = []

# Игры, которые уже были окончательно обработаны
processed_games = set()

# Статистика
stats = {
    "triggers": 0,
    "predictions": 0,
    "wins": 0,
    "losses": 0,

    "J_total": 0,
    "J_win": 0,
    "J_loss": 0,

    "Q_total": 0,
    "Q_win": 0,
    "Q_loss": 0,

    "K_total": 0,
    "K_win": 0,
    "K_loss": 0,
}


# =====================================================================
# OFFSET
# =====================================================================

def load_offset():
    global telegram_offset

    try:
        if os.path.exists(OFFSET_FILE):
            with open(
                OFFSET_FILE,
                "r",
                encoding="utf-8"
            ) as f:
                telegram_offset = int(
                    f.read().strip()
                )

    except Exception as e:
        print(
            f"⚠️ Ошибка offset: {e}",
            flush=True
        )

        telegram_offset = 0


def save_offset():
    try:
        with open(
            OFFSET_FILE,
            "w",
            encoding="utf-8"
        ) as f:
            f.write(
                str(telegram_offset)
            )

    except Exception:
        pass


# =====================================================================
# SAVE / LOAD
# =====================================================================

def load_results():

    global predictions
    global games_cache
    global processed_games
    global stats

    if not os.path.exists(RESULTS_FILE):
        return

    try:

        with open(
            RESULTS_FILE,
            "r",
            encoding="utf-8"
        ) as f:
            data = json.load(f)

        if not isinstance(data, dict):
            return

        saved_stats = data.get("stats")

        if isinstance(saved_stats, dict):
            stats.update(saved_stats)

        saved_predictions = data.get(
            "predictions"
        )

        if isinstance(saved_predictions, list):
            predictions = saved_predictions

        saved_games = data.get(
            "games_cache"
        )

        if isinstance(saved_games, dict):

            games_cache = {}

            for key, value in saved_games.items():

                try:
                    games_cache[int(key)] = value

                except Exception:
                    pass

        saved_processed = data.get(
            "processed_games"
        )

        if isinstance(saved_processed, list):

            processed_games = set()

            for value in saved_processed:

                try:
                    processed_games.add(
                        int(value)
                    )

                except Exception:
                    pass

        print(
            f"💾 Загружено игр: {len(games_cache)}",
            flush=True
        )

        print(
            f"🔮 Загружено прогнозов: {len(predictions)}",
            flush=True
        )

    except Exception as e:

        print(
            f"⚠️ Ошибка загрузки результатов: {e}",
            flush=True
        )


def save_results():

    try:

        data = {
            "stats": stats,
            "predictions": predictions,
            "games_cache": games_cache,
            "processed_games": sorted(
                processed_games
            ),
        }

        tmp_file = RESULTS_FILE + ".tmp"

        with open(
            tmp_file,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                data,
                f,
                ensure_ascii=False,
                indent=2
            )

        os.replace(
            tmp_file,
            RESULTS_FILE
        )

    except Exception as e:

        print(
            f"⚠️ Ошибка сохранения: {e}",
            flush=True
        )


# =====================================================================
# CARD PARSER
# =====================================================================

CARD_RE = re.compile(
    r"(10|[2-9AJQK])([♠♣♦♥])(?:\ufe0f)?"
)


def normalize_rank(rank):

    if not rank:
        return None

    rank = str(rank).strip().upper()

    if rank == "А":
        rank = "A"

    if rank in {
        "2",
        "3",
        "4",
        "5",
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


def normalize_suit(suit):

    if not suit:
        return None

    suit = str(suit).strip()

    return {
        "♠": "♠️",
        "♣": "♣️",
        "♦": "♦️",
        "♥": "♥️",
    }.get(suit)


def card_to_text(card):

    if not card:
        return ""

    rank = normalize_rank(
        card.get("rank")
    )

    suit = normalize_suit(
        card.get("suit")
    )

    if not rank or not suit:
        return ""

    return f"{rank}{suit}"


def parse_cards(text):

    cards = []

    if not text:
        return cards

    for rank, suit in CARD_RE.findall(text):

        cards.append({
            "rank": normalize_rank(rank),
            "suit": normalize_suit(suit),
        })

    return cards


# =====================================================================
# CYBER 21 SCORE
# =====================================================================

def calculate_cyber21_score(cards):
    """
    Cyber 21:

    6-10 = номинал
    J = 2
    Q = 3
    K = 4
    A = 11
    """

    values = {
        "J": 2,
        "Q": 3,
        "K": 4,
        "A": 11,
    }

    total = 0

    for card in cards:

        rank = card.get("rank")

        if rank in values:

            total += values[rank]

        elif rank and rank.isdigit():

            total += int(rank)

    return total


# =====================================================================
# PARSE CHANNEL GAME
# =====================================================================

def parse_game_message(text):

    if not text:
        return None

    # -------------------------------------------------------------
    # Номер игры
    # -------------------------------------------------------------

    number_match = re.search(
        r"#N(\d+)",
        text
    )

    if not number_match:
        return None

    game_number = int(
        number_match.group(1)
    )

    # -------------------------------------------------------------
    # Игра должна быть завершённой
    #
    # ✅ = завершённая
    # 🔰 = ничья
    # -------------------------------------------------------------

    finished = (
        "✅" in text
        or "🔰" in text
    )

    if not finished:
        return None

    # -------------------------------------------------------------
    # Ищем две стороны:
    #
    # 25(6♥A♥8♠) - ✅20(10♦K♥)
    # -------------------------------------------------------------

    result_match = re.search(
        r"#N\d+\.\s*"
        r"(\d+)\(([^)]*)\)"
        r"\s*-\s*"
        r"(?:✅|🔰)?\s*"
        r"(\d+)\(([^)]*)\)",
        text
    )

    if not result_match:
        return None

    player_cards_text = (
        result_match.group(2)
    )

    dealer_cards_text = (
        result_match.group(4)
    )

    player_cards = parse_cards(
        player_cards_text
    )

    dealer_cards = parse_cards(
        dealer_cards_text
    )

    if not player_cards:
        return None

    if not dealer_cards:
        return None

    # -------------------------------------------------------------
    # Считаем очки САМИ
    #
    # Не используем цифры перед скобками.
    # -------------------------------------------------------------

    player_score = calculate_cyber21_score(
        player_cards
    )

    dealer_score = calculate_cyber21_score(
        dealer_cards
    )

    # -------------------------------------------------------------
    # Ничья
    # -------------------------------------------------------------

    draw = (
        "🔰" in text
        or "#X" in text
    )

    # -------------------------------------------------------------
    # ID из сообщения, если есть
    # -------------------------------------------------------------

    id_match = re.search(
        r"\(ID:\s*(\d+)\)",
        text
    )

    game_id = None

    if id_match:
        game_id = id_match.group(1)

    return {
        "game_number": game_number,

        "game_id": game_id,

        "player_cards": player_cards,
        "dealer_cards": dealer_cards,

        "player_score": player_score,
        "dealer_score": dealer_score,

        "draw": draw,
        "finished": finished,

        "raw_text": text,

        "updated_at": time.time(),
    }


# =====================================================================
# TARGET RANK
# =====================================================================

def get_target_rank(game):

    player_cards = game.get(
        "player_cards",
        []
    )

    if not player_cards:
        return None

    first_rank = player_cards[0].get(
        "rank"
    )

    # -------------------------------------------------------------
    # 6 или J -> J
    # 7 или Q -> Q
    # 8 или K -> K
    # -------------------------------------------------------------

    mapping = {
        "6": "J",
        "J": "J",

        "7": "Q",
        "Q": "Q",

        "8": "K",
        "K": "K",
    }

    return mapping.get(
        first_rank
    )


# =====================================================================
# SUIT PAIRS
# =====================================================================

def get_target_suits(game):

    player_cards = game.get(
        "player_cards",
        []
    )

    move = len(player_cards) - 1

    # -------------------------------------------------------------
    # Та же схема:
    #
    # 1 ход = 2 карты -> ♠ / ♦
    # 2 ход = 3 карты -> ♣ / ♥
    # 3 ход = 4 карты -> ♥ / ♣
    # 4 ход = 5 карт -> ♦ / ♠
    # -------------------------------------------------------------

    mapping = {
        1: ["♠️", "♦️"],
        2: ["♣️", "♥️"],
        3: ["♥️", "♣️"],
        4: ["♦️", "♠️"],
    }

    return mapping.get(
        move,
        []
    )


# =====================================================================
# TRIGGER
# =====================================================================

def is_trigger(game):

    if not game:
        return False

    if not game.get("finished"):
        return False

    player = game.get(
        "player_cards",
        []
    )

    dealer = game.get(
        "dealer_cards",
        []
    )

    # -------------------------------------------------------------
    # Нужно минимум 3 карты игрока
    # -------------------------------------------------------------

    if len(player) < 3:
        return False

    if not dealer:
        return False

    # -------------------------------------------------------------
    # ТРИГГЕР С 21 — НЕ ИСПОЛЬЗУЕМ
    # -------------------------------------------------------------

    if game.get("player_score") == 21:
        return False

    if game.get("dealer_score") == 21:
        return False

    # -------------------------------------------------------------
    # НИЧЬЯ — НЕ ИСПОЛЬЗУЕМ
    # -------------------------------------------------------------

    if game.get("draw"):
        return False

    # -------------------------------------------------------------
    # ПЕРВАЯ КАРТА ИГРОКА
    #
    # 6 / J
    # 7 / Q
    # 8 / K
    # -------------------------------------------------------------

    first_player_rank = player[0].get(
        "rank"
    )

    if first_player_rank not in {
        "6",
        "J",
        "7",
        "Q",
        "8",
        "K",
    }:
        return False

    # -------------------------------------------------------------
    # ПЕРВАЯ КАРТА ДИЛЕРА = 10
    # -------------------------------------------------------------

    first_dealer_rank = dealer[0].get(
        "rank"
    )

    if first_dealer_rank != "10":
        return False

    # -------------------------------------------------------------
    # ТРЕТЬЯ КАРТА ИГРОКА
    #
    # Только:
    # 6 / 7 / 8 / 9
    #
    # 10 исключена.
    # J/Q/K/A исключены.
    # -------------------------------------------------------------

    third_rank = player[2].get(
        "rank"
    )

    if third_rank not in {
        "6",
        "7",
        "8",
        "9",
    }:
        return False

    # -------------------------------------------------------------
    # Должна существовать раскладка мастей
    # -------------------------------------------------------------

    target_suits = get_target_suits(
        game
    )

    if not target_suits:
        return False

    return True


# =====================================================================
# TELEGRAM SEND
# =====================================================================

def telegram_send(
    text,
    chat_id=None
):

    if not chat_id:
        chat_id = CHANNEL_PROGNOZ

    try:

        response = SESSION.post(
            f"{TELEGRAM_API}/sendMessage",

            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },

            timeout=10,
        )

        data = response.json()

        if data.get("ok"):

            return data["result"].get(
                "message_id"
            )

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


# =====================================================================
# PREDICTION MESSAGE
# =====================================================================

def make_prediction_message(
    prediction
):

    target_rank = prediction[
        "target_rank"
    ]

    suits = prediction[
        "target_suits"
    ]

    suit_text = " / ".join(
        suits
    )

    text = (
        "🔮 <b>НОВЫЙ ПРОГНОЗ</b>\n\n"
    )

    text += (
        f"🎯 Целевая игра: "
        f"<b>#N{prediction['target_number']}</b>\n"
    )

    text += (
        f"🃏 Прогноз: "
        f"<b>{target_rank} {suit_text}</b>\n\n"
    )

    text += (
        f"📌 Триггер: "
        f"<b>#N{prediction['trigger_number']}</b>\n"
    )

    text += (
        f"1️⃣ Игрок: "
        f"<b>{prediction['first_player_card']}</b>\n"
    )

    text += (
        f"1️⃣ Дилер: "
        f"<b>{prediction['first_dealer_card']}</b>\n"
    )

    text += (
        f"3️⃣ Игрок: "
        f"<b>{prediction['third_player_card']}</b>\n"
    )

    text += (
        f"📊 Очки триггера: "
        f"<b>{prediction['player_score']}</b>"
        f" — "
        f"<b>{prediction['dealer_score']}</b>\n\n"
    )

    text += (
        "📈 Проверка: "
        f"цель + {DOGON_GAMES} догона"
    )

    return text


# =====================================================================
# RESULT MESSAGE
# =====================================================================

def make_result_message(
    prediction,
    result,
):

    target_rank = prediction[
        "target_rank"
    ]

    target_number = prediction[
        "target_number"
    ]

    trigger_number = prediction[
        "trigger_number"
    ]

    if result["status"] == "win":

        text = (
            "✅ <b>ПРОГНОЗ ЗАШЁЛ</b>\n\n"
        )

        text += (
            f"🎯 Прогноз: "
            f"<b>{target_rank}</b>\n"
        )

        text += (
            f"📌 Триггер: "
            f"<b>#N{trigger_number}</b>\n"
        )

        text += (
            f"🎯 Цель: "
            f"<b>#N{target_number}</b>\n"
        )

        text += (
            f"🃏 Карта: "
            f"<b>{result['card']}</b>\n"
        )

        text += (
            f"📍 Попадание: "
            f"<b>#N{result['game_number']}</b>\n"
        )

        text += (
            f"📈 Догон: "
            f"<b>{result['dogon']}</b>"
        )

        return text

    text = (
        "❌ <b>ПРОГНОЗ НЕ ЗАШЁЛ</b>\n\n"
    )

    text += (
        f"🎯 Прогноз: "
        f"<b>{target_rank}</b>\n"
    )

    text += (
        f"📌 Триггер: "
        f"<b>#N{trigger_number}</b>\n"
    )

    text += (
        f"🎯 Цель: "
        f"<b>#N{target_number}</b>\n"
    )

    text += (
        f"📈 Проверены цель + "
        f"{DOGON_GAMES} догона"
    )

    return text


# =====================================================================
# CREATE PREDICTION
# =====================================================================

def create_prediction(game):

    if not is_trigger(game):
        return None

    trigger_number = game[
        "game_number"
    ]

    target_rank = get_target_rank(
        game
    )

    if not target_rank:
        return None

    target_suits = get_target_suits(
        game
    )

    if not target_suits:
        return None

    target_number = (
        trigger_number
        + TARGET_OFFSET
    )

    # -------------------------------------------------------------
    # Защита от повторного прогноза
    # -------------------------------------------------------------

    for existing in predictions:

        if (
            existing.get(
                "trigger_number"
            )
            == trigger_number
        ):
            return None

    first_player = card_to_text(
        game["player_cards"][0]
    )

    first_dealer = card_to_text(
        game["dealer_cards"][0]
    )

    third_player = card_to_text(
        game["player_cards"][2]
    )

    prediction = {

        "trigger_number":
            trigger_number,

        "target_number":
            target_number,

        "target_rank":
            target_rank,

        "target_suits":
            target_suits,

        "first_player_card":
            first_player,

        "first_dealer_card":
            first_dealer,

        "third_player_card":
            third_player,

        "player_score":
            game["player_score"],

        "dealer_score":
            game["dealer_score"],

        "status":
            "pending",

        "message_id":
            None,

        "created_at":
            datetime.now(
                MOSCOW_TZ
            ).isoformat(),

        "result_game":
            None,

        "result_dogon":
            None,

        "found_card":
            None,
    }

    # -------------------------------------------------------------
    # Сначала отправляем в канал
    # -------------------------------------------------------------

    message = make_prediction_message(
        prediction
    )

    message_id = telegram_send(
        message,
        CHANNEL_PROGNOZ
    )

    if not message_id:

        print(
            "❌ Прогноз не отправлен — "
            "в predictions не записываем",
            flush=True
        )

        return None

    prediction[
        "message_id"
    ] = message_id

    predictions.append(
        prediction
    )

    stats["triggers"] += 1
    stats["predictions"] += 1

    if target_rank == "J":
        stats["J_total"] += 1

    elif target_rank == "Q":
        stats["Q_total"] += 1

    elif target_rank == "K":
        stats["K_total"] += 1

    save_results()

    print(
        "",
        flush=True
    )

    print(
        "════════════════════════════════════",
        flush=True
    )

    print(
        "📤 ПРОГНОЗ ОТПРАВЛЕН",
        flush=True
    )

    print(
        f"📌 Триггер: #{trigger_number}",
        flush=True
    )

    print(
        f"🎯 Цель: #{target_number}",
        flush=True
    )

    print(
        f"🃏 Прогноз: "
        f"{target_rank} "
        f"{' / '.join(target_suits)}",
        flush=True
    )

    print(
        "════════════════════════════════════",
        flush=True
    )

    return prediction


# =====================================================================
# CHECK ONE PREDICTION
# =====================================================================

def check_prediction(
    prediction
):

    if prediction.get(
        "status"
    ) != "pending":

        return False

    target_number = prediction[
        "target_number"
    ]

    target_rank = prediction[
        "target_rank"
    ]

    target_suits = prediction[
        "target_suits"
    ]

    # -------------------------------------------------------------
    # Цель + догоны
    # -------------------------------------------------------------

    for dogon in range(
        DOGON_GAMES + 1
    ):

        game_number = (
            target_number
            + dogon
        )

        game = games_cache.get(
            game_number
        )

        if not game:
            continue

        player_cards = game.get(
            "player_cards",
            []
        )

        # ---------------------------------------------------------
        # Ищем нужную карту ТОЛЬКО у игрока
        # ---------------------------------------------------------

        for card in player_cards:

            rank = card.get(
                "rank"
            )

            suit = card.get(
                "suit"
            )

            if (
                rank == target_rank
                and suit in target_suits
            ):

                # -------------------------------------------------
                # ВАЖНО:
                #
                # если карта есть,
                # то это PLUS независимо от:
                #
                # 21
                # ничьей
                # результата игры
                # -------------------------------------------------

                prediction[
                    "status"
                ] = "win"

                prediction[
                    "result_game"
                ] = game_number

                prediction[
                    "result_dogon"
                ] = dogon

                prediction[
                    "found_card"
                ] = card_to_text(
                    card
                )

                prediction[
                    "result_checked_at"
                ] = datetime.now(
                    MOSCOW_TZ
                ).isoformat()

                stats["wins"] += 1

                if target_rank == "J":
                    stats["J_win"] += 1

                elif target_rank == "Q":
                    stats["Q_win"] += 1

                elif target_rank == "K":
                    stats["K_win"] += 1

                telegram_send(
                    make_result_message(
                        prediction,
                        {
                            "status": "win",
                            "game_number":
                                game_number,
                            "dogon":
                                dogon,
                            "card":
                                card_to_text(
                                    card
                                ),
                        }
                    ),
                    CHANNEL_PROGNOZ
                )

                save_results()

                print(
                    f"✅ ЗАШЛО: "
                    f"#{target_number} "
                    f"догон={dogon} "
                    f"{card_to_text(card)}",
                    flush=True
                )

                return True

    # -------------------------------------------------------------
    # Если цель + все 4 догона ещё не получены,
    # прогноз пока остаётся pending.
    # -------------------------------------------------------------

    for dogon in range(
        DOGON_GAMES + 1
    ):

        number = (
            target_number
            + dogon
        )

        if number not in games_cache:
            return False

    # -------------------------------------------------------------
    # Все игры доступны и карты не было
    # -------------------------------------------------------------

    prediction[
        "status"
    ] = "loss"

    prediction[
        "result_checked_at"
    ] = datetime.now(
        MOSCOW_TZ
    ).isoformat()

    stats["losses"] += 1

    if target_rank == "J":
        stats["J_loss"] += 1

    elif target_rank == "Q":
        stats["Q_loss"] += 1

    elif target_rank == "K":
        stats["K_loss"] += 1

    telegram_send(
        make_result_message(
            prediction,
            {
                "status": "loss"
            }
        ),
        CHANNEL_PROGNOZ
    )

    save_results()

    print(
        f"❌ НЕ ЗАШЛО: "
        f"#{target_number}",
        flush=True
    )

    return True


# =====================================================================
# CHECK ALL PREDICTIONS
# =====================================================================

def check_predictions():

    changed = False

    for prediction in predictions:

        if prediction.get(
            "status"
        ) != "pending":

            continue

        before = prediction.get(
            "status"
        )

        result = check_prediction(
            prediction
        )

        after = prediction.get(
            "status"
        )

        if before != after:
            changed = True

    if changed:
        save_results()


# =====================================================================
# FINALIZE GAME
# =====================================================================

def finalize_pending_game(
    game_number
):

    pending = pending_games.get(
        game_number
    )

    if not pending:
        return

    # -------------------------------------------------------------
    # Сколько прошло с первого появления ✅
    # -------------------------------------------------------------

    elapsed = (
        time.time()
        - pending["first_seen"]
    )

    if elapsed < GAME_RECHECK_SECONDS:
        return

    game = pending["game"]

    del pending_games[
        game_number
    ]

    if not game.get("finished"):
        return

    # -------------------------------------------------------------
    # Если уже окончательно обработали —
    # повторно триггер не создаём.
    # -------------------------------------------------------------

    if game_number in processed_games:
        return

    # -------------------------------------------------------------
    # Сохраняем окончательную версию
    # -------------------------------------------------------------

    games_cache[
        game_number
    ] = game

    processed_games.add(
        game_number
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
        f"#N{game_number}",
        flush=True
    )

    print(
        f"👤 P: "
        f"{game['player_score']} "
        f"("
        f"{' '.join(card_to_text(c) for c in game['player_cards'])}"
        f")",
        flush=True
    )

    print(
        f"🎰 D: "
        f"{game['dealer_score']} "
        f"("
        f"{' '.join(card_to_text(c) for c in game['dealer_cards'])}"
        f")",
        flush=True
    )

    if game["draw"]:

        print(
            "🔰 НИЧЬЯ",
            flush=True
        )

    if game["player_score"] == 21:

        print(
            "⚠️ P = 21",
            flush=True
        )

    if game["dealer_score"] == 21:

        print(
            "⚠️ D = 21",
            flush=True
        )

    print(
        "────────────────────────────────────",
        flush=True
    )

    # -------------------------------------------------------------
    # Сначала проверяем старые прогнозы,
    # потому что эта игра может быть целью/догоном.
    # -------------------------------------------------------------

    check_predictions()

    # -------------------------------------------------------------
    # Потом проверяем эту игру как новый триггер.
    # -------------------------------------------------------------

    if is_trigger(game):

        create_prediction(
            game
        )

    else:

        print(
            f"⏭️ #{game_number} — "
            f"не триггер",
            flush=True
        )

    save_results()


# =====================================================================
# FINALIZE ALL
# =====================================================================

def finalize_all_pending():

    for game_number in list(
        pending_games.keys()
    ):

        try:

            finalize_pending_game(
                game_number
            )

        except Exception as e:

            print(
                f"❌ Ошибка фиксации "
                f"#{game_number}: {e}",
                flush=True
            )


# =====================================================================
# TELEGRAM UPDATES
# =====================================================================

def process_telegram_updates():

    global telegram_offset

    try:

        response = SESSION.get(
            f"{TELEGRAM_API}/getUpdates",

            params={
                "offset":
                    telegram_offset,

                "timeout":
                    3,

                "limit":
                    100,

                "allowed_updates":
                    '["channel_post","edited_channel_post"]',
            },

            timeout=10,
        )

        data = response.json()

        if not data.get("ok"):

            print(
                f"⚠️ getUpdates: {data}",
                flush=True
            )

            return

        for update in data.get(
            "result",
            []
        ):

            update_id = update.get(
                "update_id"
            )

            if update_id is not None:

                telegram_offset = (
                    update_id + 1
                )

                save_offset()

            # ---------------------------------------------------------
            # Новая публикация
            # или отредактированная публикация
            # ---------------------------------------------------------

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

            chat_id = str(
                post.get(
                    "chat",
                    {}
                ).get(
                    "id",
                    ""
                )
            )

            # ---------------------------------------------------------
            # Только CHANNEL_STATS
            # ---------------------------------------------------------

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

            game = parse_game_message(
                text
            )

            if not game:
                continue

            game_number = game[
                "game_number"
            ]

            # ---------------------------------------------------------
            # Новая игра
            # ---------------------------------------------------------

            if game_number in processed_games:

                # Уже окончательно зафиксирована.
                #
                # После 30 секунд не перезапускаем её,
                # чтобы не создавать второй триггер.
                #
                # Саму игру оставляем в cache как есть.
                continue

            # ---------------------------------------------------------
            # Уже ждём эту игру
            #
            # Если пришёл edited_channel_post,
            # просто заменяем данные.
            #
            # first_seen НЕ меняем.
            # ---------------------------------------------------------

            if game_number in pending_games:

                pending_games[
                    game_number
                ]["game"] = game

                print(
                    f"🔄 Обновлена игра "
                    f"#{game_number} "
                    f"до окончания 30 секунд",
                    flush=True
                )

                continue

            # ---------------------------------------------------------
            # Первое появление завершённой игры
            # ---------------------------------------------------------

            pending_games[
                game_number
            ] = {
                "game": game,
                "first_seen":
                    time.time(),
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
                "ждём 30 секунд",
                flush=True
            )

    except Exception as e:

        print(
            f"⚠️ Ошибка Telegram updates: {e}",
            flush=True
        )


# =====================================================================
# STATISTICS
# =====================================================================

def percent(
    win,
    total
):

    if total <= 0:
        return 0.0

    return (
        win
        / total
        * 100.0
    )


def print_statistics():

    total = stats[
        "predictions"
    ]

    wins = stats[
        "wins"
    ]

    losses = stats[
        "losses"
    ]

    print(
        "",
        flush=True
    )

    print(
        "╔══════════════════════════════════════╗",
        flush=True
    )

    print(
        "║       НОВАЯ МЕТОДИКА — СТАТА        ║",
        flush=True
    )

    print(
        "╠══════════════════════════════════════╣",
        flush=True
    )

    print(
        f"║ Триггеров:  {stats['triggers']:<24}║",
        flush=True
    )

    print(
        f"║ Прогнозов:  {total:<24}║",
        flush=True
    )

    print(
        f"║ Зашло:      {wins:<24}║",
        flush=True
    )

    print(
        f"║ Не зашло:   {losses:<24}║",
        flush=True
    )

    print(
        f"║ Процент:    {percent(wins, total):>7.2f}%                 ║",
        flush=True
    )

    print(
        "╠══════════════════════════════════════╣",
        flush=True
    )

    print(
        f"║ J: {stats['J_win']}/{stats['J_total']}"
        f" = {percent(stats['J_win'], stats['J_total']):.2f}%"
        f"                 ║",
        flush=True
    )

    print(
        f"║ Q: {stats['Q_win']}/{stats['Q_total']}"
        f" = {percent(stats['Q_win'], stats['Q_total']):.2f}%"
        f"                 ║",
        flush=True
    )

    print(
        f"║ K: {stats['K_win']}/{stats['K_total']}"
        f" = {percent(stats['K_win'], stats['K_total']):.2f}%"
        f"                 ║",
        flush=True
    )

    print(
        "╚══════════════════════════════════════╝",
        flush=True
    )


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
        "🚀 OLD BOT — НОВАЯ МЕТОДИКА",
        flush=True
    )

    print(
        "==================================================",
        flush=True
    )

    print(
        "📡 Источник игр: CHANNEL_STATS",
        flush=True
    )

    print(
        "⏳ Проверка через 30 секунд",
        flush=True
    )

    print(
        "🎯 6/J → J",
        flush=True
    )

    print(
        "🎯 7/Q → Q",
        flush=True
    )

    print(
        "🎯 8/K → K",
        flush=True
    )

    print(
        "🎰 Первая карта дилера: 10",
        flush=True
    )

    print(
        "🔢 Третья карта игрока: 6/7/8/9",
        flush=True
    )

    print(
        "🚫 Третья карта 10/J/Q/K/A — исключена",
        flush=True
    )

    print(
        "🚫 Триггер с 21/ничьёй — исключён",
        flush=True
    )

    print(
        "🎯 Цель: +3",
        flush=True
    )

    print(
        f"📈 Догоны: {DOGON_GAMES}",
        flush=True
    )

    print(
        "📤 Прогнозы → CHANNEL_PROGNOZ",
        flush=True
    )

    print(
        "==================================================",
        flush=True
    )

    load_offset()
    load_results()

    print(
        f"📌 Telegram offset: "
        f"{telegram_offset}",
        flush=True
    )

    print(
        f"📚 Кэш игр: "
        f"{len(games_cache)}",
        flush=True
    )

    print(
        f"🔮 Активных/старых прогнозов: "
        f"{len(predictions)}",
        flush=True
    )

    print(
        "==================================================",
        flush=True
    )

    last_stats_print = 0

    while True:

        loop_start = time.time()

        try:

            # ---------------------------------------------------------
            # 1. Получаем новые игры из CHANNEL_STATS
            # ---------------------------------------------------------

            process_telegram_updates()

            # ---------------------------------------------------------
            # 2. Фиксируем игры, которым исполнилось 30 секунд
            # ---------------------------------------------------------

            finalize_all_pending()

            # ---------------------------------------------------------
            # 3. Проверяем прогнозы
            # ---------------------------------------------------------

            check_predictions()

            # ---------------------------------------------------------
            # 4. Печать статистики раз в минуту
            # ---------------------------------------------------------

            now = time.time()

            if (
                now - last_stats_print
                >= 60
            ):

                print_statistics()

                last_stats_print = now

            # ---------------------------------------------------------
            # 5. Пауза
            # ---------------------------------------------------------

            elapsed = (
                time.time()
                - loop_start
            )

            time.sleep(
                max(
                    0.1,
                    POLL_INTERVAL
                    - elapsed
                )
            )

        except KeyboardInterrupt:

            print(
                "",
                flush=True
            )

            print(
                "🛑 Бот остановлен",
                flush=True
            )

            save_results()

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