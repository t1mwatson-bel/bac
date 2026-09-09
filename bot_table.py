import os
import sys
import time
import re
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

if not BOT_TOKEN or not CHANNEL_STATS:
    print(
        "❌ Ошибка: BOT_TOKEN или CHANNEL_STATS не заданы!",
        flush=True
    )
    sys.exit(1)


# =====================================================================
# CONFIG
# =====================================================================

MOSCOW_TZ = pytz.timezone("Europe/Moscow")

# Сколько секунд ждём после первого появления ✅
GAME_RECHECK_SECONDS = 30

# Между триггером и целевой игрой 2 игры:
# trigger #N492 -> target #N495
TARGET_OFFSET = 3

# 4 догона после целевой:
# +0, +1, +2, +3, +4
DOGON_GAMES = 4

# Как часто опрашиваем Telegram
POLL_INTERVAL = 2.0

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

OFFSET_FILE = "channel_stats_offset.txt"
RESULTS_FILE = "new_method_results.json"


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

# Игры, которые увидели впервые, но ещё не прошло 30 секунд
pending_games = {}

# Уже окончательно обработанные игры
processed_games = set()

# Все окончательно подтверждённые игры,
# которые понадобятся для проверки прогнозов
games_cache = {}

# Активные прогнозы
predictions = []

# Статистика
stats = {
    "triggers": 0,
    "predictions": 0,
    "wins": 0,
    "losses": 0,
    "pending": 0,

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
# JSON
# =====================================================================

def load_results():
    if not os.path.exists(RESULTS_FILE):
        return

    try:
        import json

        with open(RESULTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict):
            return

        saved_stats = data.get("stats")
        if isinstance(saved_stats, dict):
            stats.update(saved_stats)

        saved_predictions = data.get("predictions")
        if isinstance(saved_predictions, list):
            predictions.extend(saved_predictions)

        saved_processed = data.get("processed_games")
        if isinstance(saved_processed, list):
            processed_games.update(
                int(x) for x in saved_processed
                if str(x).isdigit()
            )

        saved_games = data.get("games_cache")
        if isinstance(saved_games, dict):
            for key, value in saved_games.items():
                try:
                    games_cache[int(key)] = value
                except Exception:
                    pass

        print(
            f"💾 Загружено: игр={len(games_cache)}, "
            f"прогнозов={len(predictions)}",
            flush=True
        )

    except Exception as e:
        print(f"⚠️ Ошибка загрузки {RESULTS_FILE}: {e}", flush=True)


def save_results():
    try:
        import json

        data = {
            "stats": stats,
            "predictions": predictions,
            "processed_games": sorted(processed_games),
            "games_cache": games_cache,
        }

        tmp = RESULTS_FILE + ".tmp"

        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(
                data,
                f,
                ensure_ascii=False,
                indent=2
            )

        os.replace(tmp, RESULTS_FILE)

    except Exception as e:
        print(f"⚠️ Ошибка сохранения результатов: {e}", flush=True)


# =====================================================================
# TELEGRAM OFFSET
# =====================================================================

def load_offset():
    global telegram_offset

    try:
        if os.path.exists(OFFSET_FILE):
            with open(OFFSET_FILE, "r", encoding="utf-8") as f:
                telegram_offset = int(f.read().strip())

    except Exception:
        telegram_offset = 0


def save_offset():
    try:
        with open(OFFSET_FILE, "w", encoding="utf-8") as f:
            f.write(str(telegram_offset))

    except Exception:
        pass


# =====================================================================
# CARD PARSER
# =====================================================================

CARD_RE = re.compile(
    r"(10|[2-9AJQK])([♠♣♦♥])(?:️)?"
)


def normalize_cards(text):
    if not text:
        return []

    result = []

    for rank, suit in CARD_RE.findall(text):
        result.append({
            "rank": rank,
            "suit": suit
        })

    return result


def card_text(card):
    if not card:
        return ""

    return f"{card['rank']}{card['suit']}️"


# =====================================================================
# PARSE GAME FROM CHANNEL MESSAGE
# =====================================================================

def parse_game_message(text):
    """
    Разбирает именно основную строку игры.

    Пример:

    #N492. 25(6♥A♥8♠) - ✅20(10♦K♥) #T45 (ID: 751288531)

    Возвращает:

    game_number
    player_cards
    dealer_cards
    player_score
    dealer_score
    finished
    draw
    raw_text
    """

    if not text:
        return None

    # Номер игры
    number_match = re.search(
        r"#N(\d+)",
        text
    )

    if not number_match:
        return None

    game_number = int(number_match.group(1))

    # Нам нужна именно строка результата.
    #
    # Пример:
    # 25(6♥A♥8♠) - ✅20(10♦K♥)
    #
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

    player_score = int(result_match.group(1))
    player_cards_text = result_match.group(2)

    dealer_score = int(result_match.group(3))
    dealer_cards_text = result_match.group(4)

    player_cards = normalize_cards(player_cards_text)
    dealer_cards = normalize_cards(dealer_cards_text)

    if not player_cards or not dealer_cards:
        return None

    # В статистике:
    # 🔰 = ничья
    # #X = ничья
    draw = (
        "🔰" in text
        or "#X" in text
    )

    # Игра считается завершённой только при наличии:
    # ✅ или 🔰
    finished = (
        "✅" in text
        or "🔰" in text
    )

    return {
        "game_number": game_number,
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
# TRIGGER
# =====================================================================

def is_trigger(game):
    """
    НОВАЯ МЕТОДИКА.

    6 -> J
    7 -> Q
    8 -> K

    При этом:

    первая карта игрока = 6/7/8
    первая карта дилера = 10
    третья карта игрока = цифра 6-10

    Если в самом триггере:
    - игрок 21
    - дилер 21
    - ничья

    -> триггер не используется.
    """

    if not game:
        return False

    if not game.get("finished"):
        return False

    player = game.get("player_cards", [])
    dealer = game.get("dealer_cards", [])

    if len(player) < 3:
        return False

    if not dealer:
        return False

    # -------------------------------------------------------------
    # 21 в триггере — полностью игнорируем
    # -------------------------------------------------------------

    if game.get("player_score") == 21:
        return False

    if game.get("dealer_score") == 21:
        return False

    if game.get("draw"):
        return False

    # -------------------------------------------------------------
    # Первая карта игрока
    # -------------------------------------------------------------

    first_player_rank = player[0]["rank"]

    if first_player_rank not in {"6", "7", "8"}:
        return False

    # -------------------------------------------------------------
    # Первая карта дилера = 10
    # -------------------------------------------------------------

    first_dealer_rank = dealer[0]["rank"]

    if first_dealer_rank != "10":
        return False

    # -------------------------------------------------------------
    # Третья карта игрока должна быть цифрой
    # -------------------------------------------------------------

    third_player_rank = player[2]["rank"]

    if third_player_rank not in {
        "6",
        "7",
        "8",
        "9",
        "10"
    }:
        return False

    return True


# =====================================================================
# TARGET RANK
# =====================================================================

def get_target_rank(game):
    """
    6 -> J
    7 -> Q
    8 -> K
    """

    first_rank = game["player_cards"][0]["rank"]

    mapping = {
        "6": "J",
        "7": "Q",
        "8": "K",
    }

    return mapping.get(first_rank)


# =====================================================================
# GAME NUMBER OFFSET
# =====================================================================

def add_game_offset(number, offset):
    return int(number) + int(offset)


# =====================================================================
# FIND TARGET / DOGON
# =====================================================================

def find_prediction_hit(prediction):
    """
    Проверяем:

    target
    +1
    +2
    +3
    +4

    Нужная карта должна быть у ИГРОКА.

    Если карта выпала в игре с 21 или ничьёй —
    всё равно WIN.

    21/ничья НЕ отменяют уже сделанную ставку.
    """

    target_number = prediction["target_number"]
    target_rank = prediction["target_rank"]

    for dogon in range(DOGON_GAMES + 1):

        number = add_game_offset(
            target_number,
            dogon
        )

        game = games_cache.get(number)

        if not game:
            continue

        player_cards = game.get(
            "player_cards",
            []
        )

        for card in player_cards:

            if card.get("rank") == target_rank:

                return {
                    "win": True,
                    "game_number": number,
                    "dogon": dogon,
                    "card": card_text(card),
                    "game": game,
                }

    return None


# =====================================================================
# CHECK PREDICTIONS
# =====================================================================

def check_predictions():
    changed = False

    for prediction in predictions:

        if prediction.get("status") != "pending":
            continue

        result = find_prediction_hit(prediction)

        # -------------------------------------------------------------
        # WIN
        # -------------------------------------------------------------

        if result:

            prediction["status"] = "win"
            prediction["result_game"] = result["game_number"]
            prediction["result_dogon"] = result["dogon"]
            prediction["found_card"] = result["card"]
            prediction["result_checked_at"] = datetime.now(
                MOSCOW_TZ
            ).isoformat()

            stats["wins"] += 1

            rank = prediction["target_rank"]

            if rank == "J":
                stats["J_win"] += 1

            elif rank == "Q":
                stats["Q_win"] += 1

            elif rank == "K":
                stats["K_win"] += 1

            changed = True

            print(
                "",
                flush=True
            )

            print(
                "════════════════════════════════════",
                flush=True
            )

            print(
                f"✅ ЗАШЛО | триггер #{prediction['trigger_number']} "
                f"→ цель #{prediction['target_number']}",
                flush=True
            )

            print(
                f"🎯 Ранг: {rank}",
                flush=True
            )

            print(
                f"🃏 Карта: {result['card']}",
                flush=True
            )

            print(
                f"📍 Игра попадания: #{result['game_number']}",
                flush=True
            )

            print(
                f"📈 Догон: {result['dogon']}",
                flush=True
            )

            print(
                "════════════════════════════════════",
                flush=True
            )

            continue

        # -------------------------------------------------------------
        # Проверяем, все ли игры до +4 уже доступны
        # -------------------------------------------------------------

        all_available = True

        for dogon in range(DOGON_GAMES + 1):

            number = add_game_offset(
                prediction["target_number"],
                dogon
            )

            if number not in games_cache:
                all_available = False
                break

        if not all_available:
            continue

        # -------------------------------------------------------------
        # LOSS
        # -------------------------------------------------------------

        prediction["status"] = "loss"

        prediction["result_checked_at"] = datetime.now(
            MOSCOW_TZ
        ).isoformat()

        stats["losses"] += 1

        rank = prediction["target_rank"]

        if rank == "J":
            stats["J_loss"] += 1

        elif rank == "Q":
            stats["Q_loss"] += 1

        elif rank == "K":
            stats["K_loss"] += 1

        changed = True

        print(
            f"❌ НЕ ЗАШЛО | "
            f"триггер #{prediction['trigger_number']} "
            f"→ цель #{prediction['target_number']}",
            flush=True
        )

    if changed:
        save_results()


# =====================================================================
# CREATE PREDICTION
# =====================================================================

def create_prediction(game):
    if not is_trigger(game):
        return None

    trigger_number = game["game_number"]

    target_rank = get_target_rank(game)

    if not target_rank:
        return None

    target_number = add_game_offset(
        trigger_number,
        TARGET_OFFSET
    )

    # -------------------------------------------------------------
    # Не создаём повторный прогноз на один триггер
    # -------------------------------------------------------------

    for prediction in predictions:

        if prediction.get("trigger_number") == trigger_number:
            return None

    prediction = {
        "trigger_number": trigger_number,
        "target_number": target_number,
        "target_rank": target_rank,

        "trigger_first_player": card_text(
            game["player_cards"][0]
        ),

        "trigger_first_dealer": card_text(
            game["dealer_cards"][0]
        ),

        "trigger_third_player": card_text(
            game["player_cards"][2]
        ),

        "trigger_player_score": game["player_score"],
        "trigger_dealer_score": game["dealer_score"],

        "status": "pending",

        "created_at": datetime.now(
            MOSCOW_TZ
        ).isoformat(),

        "result_game": None,
        "result_dogon": None,
        "found_card": None,
    }

    predictions.append(prediction)

    stats["predictions"] += 1
    stats["triggers"] += 1

    if target_rank == "J":
        stats["J_total"] += 1

    elif target_rank == "Q":
        stats["Q_total"] += 1

    elif target_rank == "K":
        stats["K_total"] += 1

    print(
        "",
        flush=True
    )

    print(
        "🔮 НОВЫЙ ПРОГНОЗ",
        flush=True
    )

    print(
        f"🎯 Триггер: #{trigger_number}",
        flush=True
    )

    print(
        f"🃏 Первая карта игрока: "
        f"{prediction['trigger_first_player']}",
        flush=True
    )

    print(
        f"🃏 Первая карта дилера: "
        f"{prediction['trigger_first_dealer']}",
        flush=True
    )

    print(
        f"🃏 Третья карта игрока: "
        f"{prediction['trigger_third_player']}",
        flush=True
    )

    print(
        f"🎯 Прогноз: {target_rank}",
        flush=True
    )

    print(
        f"🎯 Целевая игра: #{target_number}",
        flush=True
    )

    print(
        "📌 Правило: 6→J | 7→Q | 8→K",
        flush=True
    )

    print(
        "📌 Третья карта = цифра",
        flush=True
    )

    print(
        "════════════════════════════════════",
        flush=True
    )

    save_results()

    return prediction


# =====================================================================
# FINALIZE PENDING GAME
# =====================================================================

def finalize_pending_game(game_number):
    """
    После 30 секунд окончательно принимаем последнюю
    версию игры, которую получили из канала.

    Если за эти 30 секунд Telegram прислал
    edited_channel_post — pending_games уже содержит
    обновлённую версию.
    """

    pending = pending_games.get(game_number)

    if not pending:
        return

    game = pending["game"]

    age = time.time() - pending["first_seen"]

    if age < GAME_RECHECK_SECONDS:
        return

    # Убираем из pending
    del pending_games[game_number]

    # Уже обрабатывали
    if game_number in processed_games:
        return

    # Игра должна быть завершена
    if not game.get("finished"):
        return

    # Сохраняем окончательную версию
    games_cache[game_number] = game

    processed_games.add(game_number)

    print(
        "",
        flush=True
    )

    print(
        "────────────────────────────────────",
        flush=True
    )

    print(
        f"🔒 ИГРА ЗАФИКСИРОВАНА #{game_number}",
        flush=True
    )

    print(
        f"P: {game['player_score']} "
        f"({', '.join(card_text(c) for c in game['player_cards'])})",
        flush=True
    )

    print(
        f"D: {game['dealer_score']} "
        f"({', '.join(card_text(c) for c in game['dealer_cards'])})",
        flush=True
    )

    if game["draw"]:
        print(
            "🔰 НИЧЬЯ",
            flush=True
        )

    if game["player_score"] == 21:
        print(
            "⚠️ У игрока 21",
            flush=True
        )

    if game["dealer_score"] == 21:
        print(
            "⚠️ У дилера 21",
            flush=True
        )

    print(
        "────────────────────────────────────",
        flush=True
    )

    # -------------------------------------------------------------
    # Сначала эта игра может быть результатом старого прогноза
    # -------------------------------------------------------------

    check_predictions()

    # -------------------------------------------------------------
    # Потом проверяем, является ли она новым триггером
    # -------------------------------------------------------------

    if is_trigger(game):

        create_prediction(game)

    else:

        # Просто выводим причину для удобства
        player = game.get("player_cards", [])
        dealer = game.get("dealer_cards", [])

        if len(player) >= 3 and dealer:

            first_p = player[0]["rank"]
            first_d = dealer[0]["rank"]
            third_p = player[2]["rank"]

            if game["player_score"] == 21:
                reason = "игрок 21"

            elif game["dealer_score"] == 21:
                reason = "дилер 21"

            elif game["draw"]:
                reason = "ничья"

            elif first_p not in {"6", "7", "8"}:
                reason = "первая карта игрока не 6/7/8"

            elif first_d != "10":
                reason = "первая карта дилера не 10"

            elif third_p not in {
                "6",
                "7",
                "8",
                "9",
                "10"
            }:
                reason = "третья карта игрока не цифра"

            else:
                reason = "не триггер"

            print(
                f"⏭️ #{game_number}: {reason}",
                flush=True
            )

    save_results()


# =====================================================================
# RECEIVE CHANNEL UPDATES
# =====================================================================

def process_telegram_updates():
    """
    Читаем ТОЛЬКО новые обновления канала.

    Нас интересуют:

    channel_post
    edited_channel_post

    edited_channel_post особенно важен:
    если статистика позже дополнила игру,
    мы заменяем старую версию на новую.
    """

    global telegram_offset

    if not CHANNEL_STATS:
        return

    try:

        response = SESSION.get(
            f"{TELEGRAM_API}/getUpdates",
            params={
                "offset": telegram_offset,
                "timeout": 3,
                "limit": 100,
                "allowed_updates": (
                    '["channel_post","edited_channel_post"]'
                ),
            },
            timeout=10
        )

        data = response.json()

        if not data.get("ok"):
            print(
                f"⚠️ Telegram getUpdates: {data}",
                flush=True
            )
            return

        for update in data.get("result", []):

            update_id = update.get("update_id")

            if update_id is not None:

                telegram_offset = update_id + 1
                save_offset()

            # ---------------------------------------------------------
            # Новое сообщение или изменение сообщения
            # ---------------------------------------------------------

            post = (
                update.get("channel_post")
                or update.get("edited_channel_post")
            )

            if not post:
                continue

            chat = post.get("chat", {})
            chat_id = str(chat.get("id", ""))

            if chat_id != str(CHANNEL_STATS):
                continue

            text = post.get("text", "")

            if not text:
                continue

            game = parse_game_message(text)

            if not game:
                continue

            game_number = game["game_number"]

            # ---------------------------------------------------------
            # Если игру уже окончательно обработали,
            # но Telegram прислал её изменение —
            # обновляем её и снова даём 30 секунд.
            # ---------------------------------------------------------

            is_edited = "edited_channel_post" in update

            if is_edited:

                print(
                    f"🔄 ИЗМЕНЕНИЕ #{game_number} "
                    f"— получена новая версия",
                    flush=True
                )

                # Если уже обработана — разрешаем повторную
                # фиксацию именно этой игры.
                if game_number in processed_games:

                    processed_games.discard(game_number)

                    # Важно: если эта игра уже использовалась
                    # как триггер, старый прогноз не создаём повторно.
                    # Саму игру просто обновляем.

                pending_games[game_number] = {
                    "game": game,
                    "first_seen": time.time(),
                }

                continue

            # ---------------------------------------------------------
            # Новая игра
            # ---------------------------------------------------------

            if game_number in processed_games:
                continue

            # Если уже ждём эту игру 30 секунд —
            # просто обновляем её последней версией.
            if game_number in pending_games:

                pending_games[game_number]["game"] = game

                print(
                    f"🔄 Обновление #{game_number} "
                    f"до окончательной проверки",
                    flush=True
                )

                continue

            # ---------------------------------------------------------
            # Интересуют только игры с ✅ или 🔰
            # ---------------------------------------------------------

            if not game["finished"]:
                continue

            pending_games[game_number] = {
                "game": game,
                "first_seen": time.time(),
            }

            print(
                "",
                flush=True
            )

            print(
                f"👀 УВИДЕЛИ ЗАВЕРШЕНИЕ #{game_number}",
                flush=True
            )

            print(
                "⏳ Ждём 30 секунд перед окончательной проверкой",
                flush=True
            )

    except Exception as e:

        print(
            f"⚠️ Ошибка чтения канала: {e}",
            flush=True
        )


# =====================================================================
# FINALIZE ALL PENDING
# =====================================================================

def finalize_all_pending():
    for game_number in list(pending_games.keys()):

        try:
            finalize_pending_game(game_number)

        except Exception as e:

            print(
                f"❌ Ошибка обработки #{game_number}: {e}",
                flush=True
            )


# =====================================================================
# STATISTICS
# =====================================================================

def percent(win, total):
    if total <= 0:
        return 0.0

    return win / total * 100.0


def print_statistics():
    total = stats["predictions"]
    wins = stats["wins"]
    losses = stats["losses"]

    print(
        "",
        flush=True
    )

    print(
        "╔════════════════════════════════════╗",
        flush=True
    )

    print(
        "║        НОВАЯ МЕТОДИКА — СТАТА     ║",
        flush=True
    )

    print(
        "╠════════════════════════════════════╣",
        flush=True
    )

    print(
        f"║ Триггеров:      {stats['triggers']:<18}║",
        flush=True
    )

    print(
        f"║ Прогнозов:      {total:<18}║",
        flush=True
    )

    print(
        f"║ Зашло:          {wins:<18}║",
        flush=True
    )

    print(
        f"║ Не зашло:       {losses:<18}║",
        flush=True
    )

    print(
        f"║ Процент:        {percent(wins, total):>7.2f}%       ║",
        flush=True
    )

    print(
        "╠════════════════════════════════════╣",
        flush=True
    )

    print(
        f"║ J: {stats['J_win']}/{stats['J_total']}"
        f" = {percent(stats['J_win'], stats['J_total']):.2f}%"
        f"                    ║",
        flush=True
    )

    print(
        f"║ Q: {stats['Q_win']}/{stats['Q_total']}"
        f" = {percent(stats['Q_win'], stats['Q_total']):.2f}%"
        f"                    ║",
        flush=True
    )

    print(
        f"║ K: {stats['K_win']}/{stats['K_total']}"
        f" = {percent(stats['K_win'], stats['K_total']):.2f}%"
        f"                    ║",
        flush=True
    )

    print(
        "╚════════════════════════════════════╝",
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
        "📡 Источник: CHANNEL_STATS",
        flush=True
    )

    print(
        "⏳ Перепроверка игры: 30 секунд",
        flush=True
    )

    print(
        "🎯 6 → J",
        flush=True
    )

    print(
        "🎯 7 → Q",
        flush=True
    )

    print(
        "🎯 8 → K",
        flush=True
    )

    print(
        "🔢 Третья карта игрока: только цифра 6-10",
        flush=True
    )

    print(
        "🚫 Триггер с 21/ничьёй: игнор",
        flush=True
    )

    print(
        "🎯 Цель: +3 игры",
        flush=True
    )

    print(
        "📈 Проверка: цель + 4 догона",
        flush=True
    )

    print(
        "==================================================",
        flush=True
    )

    load_offset()
    load_results()

    print(
        f"📌 Telegram offset: {telegram_offset}",
        flush=True
    )

    print(
        f"📚 Кэш игр: {len(games_cache)}",
        flush=True
    )

    print(
        f"🔮 Прогнозов: {len(predictions)}",
        flush=True
    )

    print(
        "==================================================",
        flush=True
    )

    last_stats_print = 0

    while True:

        start = time.time()

        try:

            # ---------------------------------------------------------
            # 1. Получаем только новые сообщения/изменения
            # ---------------------------------------------------------

            process_telegram_updates()

            # ---------------------------------------------------------
            # 2. Ждём 30 секунд и фиксируем игры
            # ---------------------------------------------------------

            finalize_all_pending()

            # ---------------------------------------------------------
            # 3. Проверяем активные прогнозы
            # ---------------------------------------------------------

            check_predictions()

            # ---------------------------------------------------------
            # 4. Периодическая статистика
            # ---------------------------------------------------------

            if time.time() - last_stats_print >= 60:

                print_statistics()

                last_stats_print = time.time()

            elapsed = time.time() - start

            time.sleep(
                max(
                    0.1,
                    POLL_INTERVAL - elapsed
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