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

# Отсеивать ли прогноз, если туз встречается ПОСЛЕ десятки.
# Открытый вопрос — решается на статистике.
# False = не отсеиваем (по умолчанию)
# True  = отсеиваем
REJECT_ACE_AFTER_TEN = False

# Отсеивать ли прогноз, если триггерная десятка стоит ВТОРОЙ картой.
# Такие сигналы слабые: уходят в задержку на 4-м догоне
# или вообще не заходят.
# False = не отсеиваем
# True  = отсеиваем (по умолчанию)
REJECT_TEN_SECOND_CARD = True


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

games_cache = {}
pending_games = {}
processed_triggers = set()
predictions = []
telegram_offset = 0


# =====================================================================
# CARD NORMALIZATION
# =====================================================================

SUITS = {
    "\u2660": "\u2660\ufe0f",
    "\u2663": "\u2663\ufe0f",
    "\u2666": "\u2666\ufe0f",
    "\u2665": "\u2665\ufe0f",
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
        "6", "7", "8", "9", "10",
        "J", "Q", "K", "A",
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
    "6": 6, "7": 7, "8": 8, "9": 9, "10": 10,
    "J": 2, "Q": 3, "K": 4, "A": 11,
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
    r"(\u2660|\u2663|\u2666|\u2665)"
    r"\ufe0f?"
)


def parse_cards(text):
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
# PARSE GAME MESSAGE
# =====================================================================

def parse_game_message(text):
    if not text:
        return None

    number_match = re.search(r"#N(\d+)", text)
    if not number_match:
        return None

    game_number = int(number_match.group(1))

    groups = re.findall(r"\(([^()]*)\)", text)
    if len(groups) < 2:
        return None

    player_text = groups[0]
    dealer_text = groups[1]

    player_cards = parse_cards(player_text)
    dealer_cards = parse_cards(dealer_text)

    # У игрока карты обязательны.
    # У дилера может быть 0 карт — это нормально.
    if not player_cards:
        return None

    player_score = cyber21_score(player_cards)
    dealer_score = cyber21_score(dealer_cards)

    id_match = re.search(r"ID:\s*(\d+)", text)
    game_id = id_match.group(1) if id_match else None

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
    print(f"🔒 ИГРА ЗАФИКСИРОВАНА #N{game['game_number']}", flush=True)
    print(f"👤 P: {game['player_score']} ({cards_to_text(player)})", flush=True)
    print(f"🎰 D: {game['dealer_score']} ({cards_to_text(dealer)})", flush=True)

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

        with open(PREDICTIONS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        predictions = data if isinstance(data, list) else []

    except Exception as e:
        print(f"⚠️ Ошибка чтения {PREDICTIONS_FILE}: {e}", flush=True)
        predictions = []


def save_predictions():
    try:
        tmp = PREDICTIONS_FILE + ".tmp"

        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(predictions, f, ensure_ascii=False, indent=2)

        os.replace(tmp, PREDICTIONS_FILE)

    except Exception as e:
        print(f"⚠️ Ошибка сохранения прогнозов: {e}", flush=True)


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

        print(f"❌ Telegram sendMessage: {data}", flush=True)

    except Exception as e:
        print(f"❌ Ошибка отправки Telegram: {e}", flush=True)

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
        print(f"⚠️ Ошибка редактирования Telegram: {e}", flush=True)

    return False


# =====================================================================
# GAME NUMBER
# =====================================================================

def add_game_offset(number, offset):
    return ((int(number) - 1 + int(offset)) % 1440) + 1


# =====================================================================
# ALGORITHM: ТРИГГЕРНАЯ 10 → РАНГ ПЕРВОЙ КАРТЫ ИГРОКА
# =====================================================================

def get_rank_prediction(game):
    """
    Алгоритм "Триггерная 10 → ранг первой карты игрока".

    ЦЕЛЬ:
        - ранг первой карты игрока (любая: 6...A)

    ТРИГГЕР:
        - первая десятка в общем порядке карт,
          НО НЕ первая карта игрока.

    Почему так:
        - если первая карта игрока — 10,
          она является ЦЕЛЬЮ и НЕ считается триггером;
        - триггер — следующая десятка в игре;
        - если после первой карты игрока десяток нет — триггера нет.

    Если первая карта игрока — НЕ 10:
        - триггер — первая десятка в игре.

    Отсев по позиции триггера:
        - если триггерная десятка стоит ВТОРОЙ картой (ten_index == 1)
          и REJECT_TEN_SECOND_CARD = True → прогноз отсеивается.
        - такие сигналы слабые: уходят в задержку на 4-м догоне
          или вообще не заходят.

    Отсев по тузу:
        - смотрим промежуток между первой картой игрока
          и найденной триггерной десяткой (не включая их самих)
        - если в промежутке есть туз — прогноз отсеивается
        - туз первой картой игрока — это цель, не отсев
        - туз ПОСЛЕ триггерной десятки — регулируется флагом
          REJECT_ACE_AFTER_TEN

    Целевая игра (догон 0):
        game_number + (индекс триггерной десятки) + 1

    Проверка:
        и у игрока, И у дилера.

    Догоны: 0OND, 1, 2, 3_C
    """

    player = game.get("ARDplayer_cards", [])
    and dealer = game.get("dealer_c tenards", [])

    if not player_index:
        return None

    # Общий порядок карт: сначала игрок, потом дилер.
    all_cards = list(player) + list(dealer)

    # Целевой ранг = ранг первой карты игрока.
    target_rank = normalize_rank(player[0].get("rank"))

    if not target_rank:
        return None

    # Ищем первую десятку, НАЧИНАЯ СО ВТОРОЙ ПОЗИЦИИ.
    # Первая позиция — это цель, её не считаем триггером.
    ten_index = None
    for idx in range(1, len(all_cards)):
        if normalize_rank(all_cards[idx].get("rank")) == "10":
            ten_index = idx
            break

    # Нет триггерной десятки — нет прогноза.
    if ten_index is None:
        return None

    # Отсев: триггер второй картой (ten_index == 1).
    if REJECT_TEN_SEC == 1:
        print(
            f"🚫 #N{game['game_number']}: "
            f"триггер второй картой — отсев "
            f"(REJECT_TEN_SECOND_CARD=True)",
            flush=True,
        )
        return None

    # Промежуток между первой картой игрока и триггерной десяткой.
    # all_cards[0] — первая карта игрока (цель).
    # all_cards[ten_index] — триггерная десятка.
    # Промежуток: all_cards[1:ten_index].
    middle_cards = all_cards[1:ten_index]

    # Отсев: туз в промежутке.
    for card in middle_cards:
        if normalize_rank(card.get("rank")) == "A":
            print(
                f"🚫 #N{game['game_number']}: "
                f"туз в промежутке до десятки — отсев",
                flush=True,
            )
            return None

    # Отсев: туз после триггерной десятки (если включён флаг).
    if REJECT_ACE_AFTER_TEN:
        after_ten = all_cards[ten_index + 1:]

        for card in after_ten:
            if normalize_rank(card.get("rank")) == "A":
                print(
                    f"🚫 #N{game['game_number']}: "
                    f"туз после десятки — отсев "
                    f"(REJECT_ACE_AFTER_TEN=True)",
                    flush=True,
                )
                return None

    # Целевая игра: номер + индекс триггерной десятки + 1.
    target_offset = ten_index + 1
    target_number = add_game_offset(game["game_number"], target_offset)

    return {
        "algorithm": "триггерная 10 → ранг",
        "trigger_number": game["game_number"],
        "trigger_game_id": game.get("game_id"),
        "target_number": target_number,
        "predicted_rank": target_rank,
        "trigger_player": [card_to_text(c) for c in player],
        "trigger_dealer": [card_to_text(c) for c in dealer],
        "trigger_player_score": game["player_score"],
        "trigger_dealer_score": game["dealer_score"],
        "ten_index": ten_index,
        "target_offset": target_offset,
        "status": "pending",
        "created_at": datetime.now(MOSCOW_TZ).isoformat(),
        "result_game": None,
        "found_card": None,
        "dogon": None,
        "message_id": None,
    }


# =====================================================================
# TRIGGERS / CREATE PREDICTION
# =====================================================================

def get_algorithm_predictions(game):
    prediction = get_rank_prediction(game)
    return [prediction] if prediction else []


# =====================================================================
# PREDICTION MESSAGE
# =====================================================================

def make_prediction_message(prediction):
    rank = prediction["predicted_rank"]
    target = prediction["target_number"]

    return f"🎯 Игра: <b>#N{target}</b> {rank}"


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
        print(f"🃏 Ранг: {prediction['predicted_rank']}", flush=True)
        print(f"📌 Триггер: #N{game_number}", flush=True)


def create_prediction(game):
    create_predictions(game)


# =====================================================================
# CHECK PREDICTION RANK
# =====================================================================

def check_prediction_rank(game, predicted_rank):
    """
    Проверяем РАНГ у игрока И у дилера.

    Достаточно одного совпадения.
    Порядок: сначала игрок, потом дилер.
    """

    player_cards = game.get("player_cards", [])
    dealer_cards = game.get("dealer_cards", [])

    for card in player_cards:
        rank = normalize_rank(card.get("rank"))

        if rank == predicted_rank:
            return card_to_text(card)

    for card in dealer_cards:
        rank = normalize_rank(card.get("rank"))

        if rank == predicted_rank:
            return card_to_text(card)

    return None


# =====================================================================
# RESULT MESSAGE
# =====================================================================

def make_result_message(prediction, result):
    rank = prediction["predicted_rank"]
    target = prediction["target_number"]
    mark = "✅" if result == "win" else "❌"

    return f"🎯 Игра: <b>#N{target}</b> {rank}{mark}"


# =====================================================================
# CHECK PREDICTIONS
# =====================================================================

def check_predictions():
    """
    Прогноз проверяется строго последовательно:
    целевая игра, затем догоны 1, 2, 3.

    Минус — только если все 4 игры реально появились,
    и ни в одной (ни у игрока, ни у дилера)
    не было нужного ранга.
    """

    changed = False

    for prediction in predictions:

        if prediction.get("status") != "pending":
            continue

        target = prediction.get("target_number")
        if not target:
            continue

        predicted_rank = prediction.get("predicted_rank")
        if not predicted_rank:
            continue

        all_games_checked = True

        for dogon in range(0, DOGON_GAMES + 1):

            game_number = add_game_offset(target, dogon)
            game = games_cache.get(game_number)

            # Игра ещё не появилась — ждём.
            if not game:
                all_games_checked = False

                print(
                    f"⏳ #N{target}: ждём #N{game_number} (догон {dogon})",
                    flush=True,
                )
                break

            # Игра есть — проверяем ранг у игрока и дилера.
            found_card = check_prediction_rank(game, predicted_rank)

            if found_card:
                prediction["status"] = "win"
                prediction["result_game"] = game_number
                prediction["found_card"] = found_card
                prediction["dogon"] = dogon

                telegram_edit(
                    prediction.get("message_id"),
                    make_result_message(prediction, "win"),
                )

                print("", flush=True)
                print(f"✅ PLUS #N{target}", flush=True)
                print(
                    f"🎯 Ранг {predicted_rank} "
                    f"найден в #N{game_number} "
                    f"({found_card})",
                    flush=True,
                )
                print(f"🔄 Догон: {dogon}", flush=True)

                changed = True
                all_games_checked = False
                break

            # Ранга нет — переходим к следующей игре.
            if game.get("is_draw"):
                print(
                    f"🔰 #N{game_number} — #X, ранга нет → дальше",
                    flush=True,
                )
            else:
                print(
                    f"🔍 #N{game_number} — ранга нет → дальше",
                    flush=True,
                )

        # Не все игры ещё получены — ждём.
        if not all_games_checked:
            continue

        # Все игры проверены, ранга нигде не было — минус.
        prediction["status"] = "lose"
        prediction["result_game"] = add_game_offset(target, DOGON_GAMES)
        prediction["dogon"] = DOGON_GAMES

        telegram_edit(
            prediction.get("message_id"),
            make_result_message(prediction, "lose"),
        )

        print("", flush=True)
        print(f"❌ MINUS #N{target}", flush=True)
        print(
            f"🏁 Проверены все игры "
            f"#N{target} — #N{add_game_offset(target, DOGON_GAMES)}",
            flush=True,
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

    for game_number, info in list(pending_games.items()):
        first_seen = info.get("first_seen", now)

        if now - first_seen >= FINALIZE_WAIT_SECONDS:
            ready.append(game_number)

    for game_number in ready:
        info = pending_games.pop(game_number, None)
        if not info:
            continue

        text = info.get("text", "")
        game = parse_game_message(text)

        if not game:
            print(
                f"⚠️ #N{game_number} не удалось разобрать "
                f"после 30 секунд",
                flush=True,
            )
            continue

        games_cache[game_number] = game
        log_game(game)
        create_prediction(game)


# =====================================================================
# TELEGRAM UPDATES
# =====================================================================

def process_telegram_updates(offset):
    try:
        response = SESSION.get(
            f"{TELEGRAM_API}/getUpdates",
            params={
                "offset": offset,
                "timeout": 3,
                "limit": 50,
                "allowed_updates": json.dumps([
                    "channel_post",
                    "edited_channel_post",
                ]),
            },
            timeout=10,
        )

        data = response.json()

        if not data.get("ok"):
            print(f"❌ Telegram getUpdates: {data}", flush=True)
            return offset

        updates = data.get("result", [])

        for update in updates:
            update_id = update.get("update_id")

            if update_id is not None:
                offset = update_id + 1
                save_offset(offset)

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

            number_match = re.search(r"#N(\d+)", text)
            if not number_match:
                continue

            game_number = int(number_match.group(1))

            game = parse_game_message(text)

            if game_number in pending_games:
                pending_games[game_number]["text"] = text

                print(
                    f"🔄 Обновлена игра #N{game_number} "
                    f"до окончания {FINALIZE_WAIT_SECONDS} секунд",
                    flush=True,
                )
                continue

            if game_number in games_cache:
                if game:
                    games_cache[game_number] = game

                    print(
                        f"🔄 Обновлена завершённая игра #N{game_number}",
                        flush=True,
                    )
                continue

            if re.search(r"[✅🔰]", text):
                if game_number in processed_triggers:
                    continue

                pending_games[game_number] = {
                    "first_seen": time.time(),
                    "text": text,
                }

                print("", flush=True)
                print(f"👀 НОВАЯ ИГРА #N{game_number}", flush=True)
                print(
                    f"⏳ Увидели завершение — ждём "
                    f"{FINALIZE_WAIT_SECONDS} секунд",
                    flush=True,
                )

    except Exception as e:
        print(f"⚠️ Updates error: {e}", flush=True)

    return offset


# =====================================================================
# CLEANUP
# =====================================================================

def cleanup_games_cache():
    if len(games_cache) <= 100:
        return

    numbers = sorted(games_cache.keys())
    keep = set(numbers[-100:])

    for number in list(games_cache.keys()):
        if number not in keep:
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
    print("==================================================", flush=True)
    print("🚀 CYBER 21 — TELEGRAM STATS FORECAST", flush=True)
    print("==================================================", flush=True)
    print("📡 Игры: CHANNEL_STATS", flush=True)
    print(f"⏳ Финализация: {FINALIZE_WAIT_SECONDS} сек", flush=True)
    print(
        "🧠 Алгоритм: триггерная 10 → ранг первой карты игрока",
        flush=True,
    )
    print(
        "🚫 Отсев по тузу до десятки: ВКЛ",
        flush=True,
    )
    print(
        f"🚫 Отсев по тузу после десятки: "
        f"{'ВКЛ' if REJECT_ACE_AFTER_TEN else 'ВЫКЛ'}",
        flush=True,
    )
    print(
        f"🚫 Отсев триггера второй картой: "
        f"{'ВКЛ' if REJECT_TEN_SECOND_CARD else 'ВЫКЛ'}",
        flush=True,
    )
    print(
        f"🔄 Догонов: {DOGON_GAMES} (0, 1, 2, ..., {DOGON_GAMES})",
        flush=True,
    )
    print(
        "🎯 Прогноз: ранг, проверка у игрока + дилера",
        flush=True,
    )
    print("==================================================", flush=True)

    load_predictions()
    telegram_offset = load_offset()

    print(f"📌 Telegram offset: {telegram_offset}", flush=True)
    print(f"📊 Загружено прогнозов: {len(predictions)}", flush=True)
    print("==================================================", flush=True)

    while True:
        try:
            telegram_offset = process_telegram_updates(telegram_offset)
            finalize_pending_games()
            check_predictions()
            cleanup_games_cache()
            cleanup_predictions()

            time.sleep(POLL_INTERVAL)

        except KeyboardInterrupt:
            print("\n🛑 Бот остановлен", flush=True)
            break

        except Exception as e:
            print(f"❌ Критическая ошибка: {e}", flush=True)
            time.sleep(3)


# =====================================================================
# START
# =====================================================================

if __name__ == "__main__":
    main()