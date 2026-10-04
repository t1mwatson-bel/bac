import os
import sys
import re
import json
import time
import threading
import http.server
import socketserver
import requests
import pytz

from datetime import datetime, time as dtime
from collections import Counter, defaultdict


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
STATS_HTML_FILE = "stats.html"
BANK_FILE = "bank_state.json"

POLL_INTERVAL = 2.0

FINALIZE_WAIT_SECONDS = 30

DOGON_GAMES = 3

GAME_CYCLE = 720


# =====================================================================
# БАНК / СТАВКИ
# =====================================================================

START_BALANCE = 20000

DOGON_MULT = 2.7

WIN_COEF = 1.6


def get_base(balance):
    if balance < 50000:
        return 20000
    if balance < 100000:
        return 50000
    if balance < 200000:
        return 100000
    if balance < 400000:
        return 200000
    return 400000


def get_first_bet(balance):
    base = get_base(balance)
    return round(base * 0.0025, 2)


LOW_BALANCE_THRESHOLD = 5000


DEFAULT_BANK = {
    "balance": START_BALANCE,
    "start_balance": START_BALANCE,
    "current_bet": None,
    "step": 0,
    "history": [],
    "last_updated": None,
}


def load_bank():
    if not os.path.exists(BANK_FILE):
        state = dict(DEFAULT_BANK)
        state["current_bet"] = get_first_bet(state["balance"])
        save_bank(state)
        return state

    try:
        with open(BANK_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
    except Exception as e:
        print(f"⚠️ Ошибка чтения bank_state: {e}", flush=True)
        state = dict(DEFAULT_BANK)

    if not state.get("current_bet"):
        state["current_bet"] = get_first_bet(state["balance"])

    return state


def save_bank(state):
    state["last_updated"] = datetime.now(MOSCOW_TZ).isoformat()
    tmp = BANK_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, BANK_FILE)
    except Exception as e:
        print(f"⚠️ Ошибка сохранения bank_state: {e}", flush=True)


def get_current_bet():
    state = load_bank()
    return state.get("current_bet", 0), state.get("step", 0), state.get("balance", 0)


def apply_result(status, bet_amount, dogon=None):
    """Обновляет банк после закрытия прогноза."""
    state = load_bank()
    bet = bet_amount if bet_amount else state.get("current_bet", 0)
    profit = 0.0

    if status == "win":
        profit = bet * (WIN_COEF - 1)
        state["balance"] += profit
        state["current_bet"] = get_first_bet(state["balance"])
        state["step"] = 0

    elif status == "lose":
        profit = -bet
        state["balance"] += profit
        state["current_bet"] = round(bet * DOGON_MULT, 2)
        state["step"] = (dogon or 0) + 1

    state["history"].append({
        "time": datetime.now(MOSCOW_TZ).isoformat(),
        "status": status,
        "bet": bet,
        "profit": profit,
        "balance_after": state["balance"],
    })
    state["history"] = state["history"][-500:]
    save_bank(state)

    if state["balance"] < LOW_BALANCE_THRESHOLD:
        print(
            f"⚠️⚠️⚠️ ВНИМАНИЕ: банк ниже {LOW_BALANCE_THRESHOLD} ₽ "
            f"(сейчас {state['balance']:.0f} ₽)",
            flush=True,
        )
        try:
            telegram_send(
                f"⚠️ <b>ВНИМАНИЕ</b>\n"
                f"Банк упал ниже {LOW_BALANCE_THRESHOLD} ₽\n"
                f"Текущий банк: <b>{state['balance']:.0f} ₽</b>"
            )
        except Exception:
            pass

    return state, profit


# =====================================================================
# НОЧНАЯ ОЧИСТКА (03:00)
# =====================================================================

CLEANUP_HOUR = 3
CLEANUP_MINUTE = 0

last_cleanup_date = None


def should_cleanup_now(now=None):
    global last_cleanup_date

    if now is None:
        now = datetime.now(MOSCOW_TZ)

    if last_cleanup_date == now.date():
        return False

    cleanup_time = dtime(CLEANUP_HOUR, CLEANUP_MINUTE)

    if now.time() >= cleanup_time:
        return True

    return False


def cleanup_nightly():
    global games_cache, pending_games, processed_triggers, predictions
    global last_cleanup_date

    now = datetime.now(MOSCOW_TZ)

    print("", flush=True)
    print("🧹 НОЧНАЯ ОЧИСТКА (03:00)", flush=True)

    games_count = len(games_cache)
    games_cache.clear()
    print(f"   🗑️ games_cache: удалено {games_count} игр", flush=True)

    pending_count = len(pending_games)
    pending_games.clear()
    print(f"   🗑️ pending_games: удалено {pending_count}", flush=True)

    triggers_count = len(processed_triggers)
    processed_triggers.clear()
    print(f"   🗑️ processed_triggers: удалено {triggers_count}", flush=True)

    expired_count = 0

    for p in predictions:
        if p.get("status") == "pending":
            p["status"] = "expired"
            p["closed_at"] = now.isoformat()
            p["close_reason"] = "nightly_cleanup"
            expired_count += 1

    if expired_count:
        save_predictions()

    print(
        f"   🗑️ predictions: pending → expired — {expired_count}",
        flush=True,
    )

    last_cleanup_date = now.date()

    print("✅ Ночная очистка завершена", flush=True)
    print("", flush=True)


# =====================================================================
# РАСПИСАНИЕ СНА
# =====================================================================

SLEEP_HOUR = 23
SLEEP_MINUTE = 59

WAKE_HOUR = 9
WAKE_MINUTE = 0


def is_sleep_time(now=None):
    if now is None:
        now = datetime.now(MOSCOW_TZ)

    current = now.time()

    sleep_start = dtime(SLEEP_HOUR, SLEEP_MINUTE)
    wake_start = dtime(WAKE_HOUR, WAKE_MINUTE)

    if sleep_start <= current or current < wake_start:
        return True

    return False


def has_pending_predictions():
    for p in predictions:
        if p.get("status") == "pending":
            return True
    return False


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

sleeping = False


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

    if not player_cards:
        return None

    player_score = cyber21_score(player_cards)
    dealer_score = cyber21_score(dealer_cards)

    id_match = re.search(r"ID:\s*(\d+)", text)
    game_id = id_match.group(1) if id_match else None

    is_draw = bool(re.search(r"#X\b", text))
    is_ochko = bool(re.search(r"#O\b", text))

    return {
        "game_number": game_number,
        "game_id": game_id,
        "player_cards": player_cards,
        "dealer_cards": dealer_cards,
        "player_score": player_score,
        "dealer_score": dealer_score,
        "is_draw": is_draw,
        "is_ochko": is_ochko,
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

    if game.get("is_ochko"):
        print("⭕ #O — ОЧКО (21), пропускаем триггер", flush=True)

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
    return ((int(number) - 1 + int(offset)) % GAME_CYCLE) + 1


# =====================================================================
# ALGORITHM: ПОСЛЕДНЯЯ 10
# =====================================================================

def get_last_card_prediction(game):
    player = game.get("player_cards", [])
    dealer = game.get("dealer_cards", [])

    if not player:
        return None

    if dealer:
        return None

    if game.get("is_ochko"):
        print(
            f"⭕ #N{game['game_number']}: #O (очко) — пропуск триггера",
            flush=True,
        )
        return None

    last_card = player[-1]
    last_rank = normalize_rank(last_card.get("rank"))

    if last_rank != "10":
        return None

    if "✅" not in game.get("raw_text", ""):
        return None

    suit = normalize_suit(last_card.get("suit"))

    if not suit:
        return None

    target_offset = len(player)
    target_number = add_game_offset(game["game_number"], target_offset)

    return {
        "algorithm": "последняя 10",
        "trigger_number": game["game_number"],
        "trigger_game_id": game.get("game_id"),
        "target_number": target_number,
        "predicted_suit": suit,
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
    suit = prediction["predicted_suit"]
    target = prediction["target_number"]
    bet = prediction.get("bet_amount", 0)
    step = prediction.get("bet_step", 0)

    return (
        f"🎯 Игра: <b>#N{target}</b> {suit}\n"
        f"💰 Ставка: <b>{bet:.0f} ₽</b> (Д{step})"
    )


# =====================================================================
# CREATE PREDICTIONS
# =====================================================================

def create_predictions(game):
    game_number = game["game_number"]
    game_id = game.get("game_id")

    for prediction in get_algorithm_predictions(game):
        algorithm = prediction["algorithm"]

        trigger_key = (algorithm, game_id or game_number)

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

        # фиксируем все 4 ставки серии заранее
        bet, step, _ = get_current_bet()
        prediction["bets"] = [
            round(bet, 2),
            round(bet * DOGON_MULT, 2),
            round(bet * DOGON_MULT ** 2, 2),
            round(bet * DOGON_MULT ** 3, 2),
        ]
        prediction["bet_amount"] = prediction["bets"][0]
        prediction["bet_step"] = step

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
        print(f"🃏 Масть: {prediction['predicted_suit']}", flush=True)
        print(
            f"💰 Ставки серии: "
            f"Д0={prediction['bets'][0]:.0f} "
            f"Д1={prediction['bets'][1]:.0f} "
            f"Д2={prediction['bets'][2]:.0f} "
            f"Д3={prediction['bets'][3]:.0f}",
            flush=True,
        )
        print(f"📌 Триггер: #N{game_number} (ID: {game_id})", flush=True)


def create_prediction(game):
    create_predictions(game)


# =====================================================================
# CHECK PLAYER SUIT
# =====================================================================

def check_prediction_suit(game, predicted_suit):
    player_cards = game.get("player_cards", [])

    for card in player_cards:
        suit = normalize_suit(card.get("suit"))

        if suit == predicted_suit:
            return card_to_text(card)

    return None


# =====================================================================
# RESULT MESSAGE
# =====================================================================

def make_result_message(prediction, result):
    suit = prediction["predicted_suit"]
    target = prediction["target_number"]
    mark = "✅" if result == "win" else "❌"

    state = load_bank()
    balance = state["balance"]

    bets = prediction.get("bets") or [prediction.get("bet_amount", 0)]
    step = prediction.get("dogon", 0)
    bet = bets[step] if step < len(bets) else bets[-1]

    return (
        f"🎯 Игра: <b>#N{target}</b> {suit}{mark}\n"
        f"💰 Ставка: {bet:.0f} ₽ (Д{step})\n"
        f"🏦 Банк: <b>{balance:.0f} ₽</b>"
    )


# =====================================================================
# CHECK PREDICTIONS
# =====================================================================

def check_predictions():
    changed = False

    for prediction in predictions:

        if prediction.get("status") != "pending":
            continue

        target = prediction.get("target_number")
        if not target:
            continue

        predicted_suit = prediction.get("predicted_suit")
        if not predicted_suit:
            continue

        all_games_checked = True

        for dogon in range(0, DOGON_GAMES + 1):

            game_number = add_game_offset(target, dogon)
            game = games_cache.get(game_number)

            if not game:
                all_games_checked = False
                print(
                    f"⏳ #N{target}: ждём #N{game_number} (догон {dogon})",
                    flush=True,
                )
                break

            found_card = check_prediction_suit(game, predicted_suit)

            if found_card:
                prediction["status"] = "win"
                prediction["result_game"] = game_number
                prediction["found_card"] = found_card
                prediction["dogon"] = dogon

                bets = prediction.get("bets") or [prediction.get("bet_amount", 0)]
                actual_bet = bets[dogon] if dogon < len(bets) else bets[-1]
                apply_result("win", actual_bet, dogon)

                telegram_edit(
                    prediction.get("message_id"),
                    make_result_message(prediction, "win"),
                )

                print("", flush=True)
                print(f"✅ PLUS #N{target}", flush=True)
                print(
                    f"🎯 Масть {predicted_suit} "
                    f"найдена у игрока в #N{game_number} "
                    f"({found_card})",
                    flush=True,
                )
                print(f"🔄 Догон: {dogon}, ставка: {actual_bet:.0f} ₽", flush=True)

                changed = True
                all_games_checked = False
                break

            if game.get("is_draw"):
                print(
                    f"🔰 #N{game_number} — #X, масти нет → дальше",
                    flush=True,
                )
            else:
                print(
                    f"🔍 #N{game_number} — масти нет → дальше",
                    flush=True,
                )

        if not all_games_checked:
            continue

        prediction["status"] = "lose"
        prediction["result_game"] = add_game_offset(target, DOGON_GAMES)
        prediction["dogon"] = DOGON_GAMES

        bets = prediction.get("bets") or [prediction.get("bet_amount", 0)]
        actual_bet = bets[DOGON_GAMES] if DOGON_GAMES < len(bets) else bets[-1]
        apply_result("lose", actual_bet, DOGON_GAMES)

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
        print(f"💰 Списано: {actual_bet:.0f} ₽ (Д{DOGON_GAMES})", flush=True)

        changed = True

    if changed:
        save_predictions()
        generate_stats()


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

        if sleeping:
            print(
                f"😴 #N{game_number}: бот спит — прогноз не создаём",
                flush=True,
            )
            continue

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
# SLEEP / WAKE LOGIC
# =====================================================================

def update_sleep_state():
    global sleeping, telegram_offset

    now = datetime.now(MOSCOW_TZ)
    sleep_now = is_sleep_time(now)

    if sleep_now:
        if not sleeping:
            if has_pending_predictions():
                print(
                    "😴 Время сна. Ждём закрытия открытых прогнозов...",
                    flush=True,
                )
            else:
                sleeping = True
                print(
                    "😴 Время сна. Открытых прогнозов нет.",
                    flush=True,
                )
        elif has_pending_predictions():
            sleeping = False
    else:
        if sleeping:
            sleeping = False
            print("☀️ 09:00 — бот проснулся.", flush=True)


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
# STATS HTML
# =====================================================================

def generate_stats():
    bank = load_bank()

    total = len(predictions)
    win = sum(1 for p in predictions if p.get("status") == "win")
    lose = sum(1 for p in predictions if p.get("status") == "lose")
    pending = sum(1 for p in predictions if p.get("status") == "pending")
    expired = sum(1 for p in predictions if p.get("status") == "expired")

    decided = win + lose
    winrate = round(win / decided * 100, 1) if decided else 0.0

    balance = bank.get("balance", START_BALANCE)
    start_balance = bank.get("start_balance", START_BALANCE)
    profit = balance - start_balance

    total_staked = sum(
        p.get("bet_amount", 0) for p in predictions
        if p.get("status") in ("win", "lose")
    )
    roi = round(profit / total_staked * 100, 1) if total_staked else 0.0

    current_bet = bank.get("current_bet", 0)
    current_step = bank.get("step", 0)

    dogons = Counter()
    dogons_win = Counter()
    for p in predictions:
        if p.get("status") in ("win", "lose"):
            d = p.get("dogon")
            if d is not None:
                dogons[d] += 1
                if p.get("status") == "win":
                    dogons_win[d] += 1

    suits = Counter()
    suits_win = Counter()
    for p in predictions:
        s = p.get("predicted_suit")
        if s:
            suits[s] += 1
            if p.get("status") == "win":
                suits_win[s] += 1

    days = defaultdict(lambda: {"win": 0, "lose": 0})
    for p in predictions:
        if p.get("status") not in ("win", "lose"):
            continue
        created = p.get("created_at") or ""
        day = created[:10] if len(created) >= 10 else "?"
        days[day][p["status"]] += 1

    rows = []
    for p in predictions[-30:][::-1]:
        status = p.get("status", "?")
        mark = {"win": "✅", "lose": "❌", "pending": "⏳",
                "expired": "🗑️"}.get(status, "?")
        target = p.get("target_number", "?")
        suit = p.get("predicted_suit", "").replace("\ufe0f", "")
        dogon = p.get("dogon")
        dogon_str = f"Д{dogon}" if dogon is not None else ""

        bets = p.get("bets") or [p.get("bet_amount", 0)]
        dogon_idx = p.get("dogon") or 0
        bet = bets[dogon_idx] if dogon_idx < len(bets) else bets[-1]

        created = (p.get("created_at") or "")[:16].replace("T", " ")
        rows.append(
            f"<tr><td>{mark}</td><td>#N{target}</td><td>{suit}</td>"
            f"<td>{bet:.0f} ₽</td><td>{dogon_str}</td>"
            f"<td>{status}</td><td>{created}</td></tr>"
        )
    rows_html = "\n".join(rows) or '<tr><td colspan="7">Нет данных</td></tr>'

    dogon_rows = ""
    for d in sorted(dogons.keys()):
        played = dogons[d]
        won = dogons_win.get(d, 0)
        wr = round(won / played * 100, 1) if played else 0
        dogon_rows += (
            f"<tr><td>Д{d}</td><td>{played}</td>"
            f"<td>{won}</td><td>{wr}%</td></tr>"
        )

    suit_rows = ""
    for s, cnt in suits.items():
        won = suits_win.get(s, 0)
        wr = round(won / cnt * 100, 1) if cnt else 0
        s_clean = s.replace("\ufe0f", "")
        suit_rows += (
            f"<tr><td>{s_clean}</td><td>{cnt}</td>"
            f"<td>{won}</td><td>{wr}%</td></tr>"
        )

    day_rows = ""
    for day in sorted(days.keys(), reverse=True)[:30]:
        d = days[day]
        w, l = d["win"], d["lose"]
        total_d = w + l
        wr = round(w / total_d * 100, 1) if total_d else 0
        day_rows += (
            f"<tr><td>{day}</td><td>{total_d}</td>"
            f"<td>{w}</td><td>{l}</td><td>{wr}%</td></tr>"
        )

    updated = datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d %H:%M:%S")

    profit_class = "win" if profit >= 0 else "lose"

    html = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<title>Cyber 21 — Статистика</title>
<meta http-equiv="refresh" content="300">
<style>
body { font-family: Arial, sans-serif; background:#121212; color:#eaeaea; margin:0; padding:20px; }
h1,h2 { color:#fff; }
.cards { display:flex; flex-wrap:wrap; gap:12px; margin-bottom:24px; }
.card { background:#1e1e1e; padding:16px 20px; border-radius:10px; min-width:140px; }
.card .label { font-size:12px; color:#888; text-transform:uppercase; }
.card .value { font-size:26px; font-weight:bold; margin-top:4px; }
.win { color:#4caf50; }
.lose { color:#f44336; }
.pending { color:#ffc107; }
.expired { color:#888; }
table { width:100%; border-collapse:collapse; background:#1e1e1e; border-radius:10px; overflow:hidden; margin-bottom:24px; }
th,td { padding:8px 12px; text-align:left; border-bottom:1px solid #2a2a2a; font-size:14px; }
th { background:#262626; color:#aaa; font-weight:normal; }
.updated { color:#666; font-size:12px; margin-top:20px; }
</style>
</head>
<body>

<h1>📊 Cyber 21 — Статистика</h1>

<div class="cards">
<div class="card"><div class="label">Баланс</div><div class="value">__BALANCE__ ₽</div></div>
<div class="card"><div class="label">Профит</div><div class="value __PROFIT_CLASS__">__PROFIT__ ₽</div></div>
<div class="card"><div class="label">Winrate</div><div class="value">__WINRATE__%</div></div>
<div class="card"><div class="label">ROI</div><div class="value">__ROI__%</div></div>
<div class="card"><div class="label">Всего</div><div class="value">__TOTAL__</div></div>
<div class="card"><div class="label">Плюсы</div><div class="value win">__WIN__</div></div>
<div class="card"><div class="label">Минусы</div><div class="value lose">__LOSE__</div></div>
<div class="card"><div class="label">В ожидании</div><div class="value pending">__PENDING__</div></div>
<div class="card"><div class="label">Текущая ставка</div><div class="value">__CURRENT_BET__ ₽</div></div>
</div>

<h2>🎯 По догонам</h2>
<table>
<tr><th>Догон</th><th>Сыграно</th><th>Плюсов</th><th>Winrate</th></tr>
__DOGON_ROWS__
</table>

<h2>🃏 По мастям</h2>
<table>
<tr><th>Масть</th><th>Всего</th><th>Плюсов</th><th>Winrate</th></tr>
__SUIT_ROWS__
</table>

<h2>📅 По дням</h2>
<table>
<tr><th>Дата</th><th>Всего</th><th>Плюсов</th><th>Минусов</th><th>Winrate</th></tr>
__DAY_ROWS__
</table>

<h2>🕐 Последние 30 прогнозов</h2>
<table>
<tr><th></th><th>Игра</th><th>Масть</th><th>Ставка</th><th>Догон</th><th>Статус</th><th>Создан</th></tr>
__ROWS__
</table>

<div class="updated">Обновлено: __UPDATED__ (МСК)</div>

</body>
</html>"""

    html = html.replace("__BALANCE__", f"{balance:.0f}")
    html = html.replace("__PROFIT__", f"{profit:+.0f}")
    html = html.replace("__PROFIT_CLASS__", profit_class)
    html = html.replace("__WINRATE__", str(winrate))
    html = html.replace("__ROI__", str(roi))
    html = html.replace("__TOTAL__", str(total))
    html = html.replace("__WIN__", str(win))
    html = html.replace("__LOSE__", str(lose))
    html = html.replace("__PENDING__", str(pending))
    html = html.replace("__CURRENT_BET__", f"{current_bet:.0f}")
    html = html.replace("__DOGON_ROWS__", dogon_rows or '<tr><td colspan="4">Нет данных</td></tr>')
    html = html.replace("__SUIT_ROWS__", suit_rows or '<tr><td colspan="4">Нет данных</td></tr>')
    html = html.replace("__DAY_ROWS__", day_rows or '<tr><td colspan="5">Нет данных</td></tr>')
    html = html.replace("__ROWS__", rows_html)
    html = html.replace("__UPDATED__", updated)

    try:
        with open(STATS_HTML_FILE, "w", encoding="utf-8") as f:
            f.write(html)

        print(
            f"📊 Статистика: {total} прогнозов, "
            f"банк {balance:.0f} ₽, профит {profit:+.0f} ₽, "
            f"winrate {winrate}%, ROI {roi}%, "
            f"ставка {current_bet:.0f} ₽ (Д{current_step})",
            flush=True,
        )
    except Exception as e:
        print(f"⚠️ Ошибка генерации статистики: {e}", flush=True)


# =====================================================================
# ВЕБ-СЕРВЕР ДЛЯ СТАТИСТИКИ
# =====================================================================

def start_web_server():
    port = int(os.getenv("PORT", 8000))

    try:
        os.chdir(os.path.dirname(os.path.abspath(__file__)))
    except Exception:
        pass

    class StatsHandler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            if self.path in ("/", "/health"):
                if os.path.exists(STATS_HTML_FILE):
                    self.path = "/" + STATS_HTML_FILE
                    return super().do_GET()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write("OK".encode("utf-8"))
                return
            return super().do_GET()

    try:
        with socketserver.TCPServer(("0.0.0.0", port), StatsHandler) as httpd:
            print(f"🌐 Веб-сервер запущен: 0.0.0.0:{port}", flush=True)
            httpd.serve_forever()
    except Exception as e:
        print(f"⚠️ Ошибка веб-сервера на порту {port}: {e}", flush=True)


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
    print("🧠 Алгоритм: последняя 10", flush=True)
    print("⭕ Фильтр #O: пропуск триггера", flush=True)
    print(
        f"😴 Сон: с {SLEEP_HOUR:02d}:{SLEEP_MINUTE:02d} "
        f"до {WAKE_HOUR:02d}:{WAKE_MINUTE:02d}",
        flush=True,
    )
    print(
        f"🧹 Ночная очистка: {CLEANUP_HOUR:02d}:{CLEANUP_MINUTE:02d}",
        flush=True,
    )
    print(
        f"🔄 Догонов: {DOGON_GAMES} (0, 1, 2, ..., {DOGON_GAMES})",
        flush=True,
    )
    print(f"🔁 Цикл нумерации игр: {GAME_CYCLE}", flush=True)
    print(f"💰 Стартовый банк: {START_BALANCE} ₽", flush=True)
    print(f"📈 Коэффициент: {WIN_COEF}, множитель догона: {DOGON_MULT}", flush=True)
    print("==================================================", flush=True)

    load_predictions()

    telegram_offset = load_offset()

    print(f"📌 Telegram offset: {telegram_offset}", flush=True)
    print(f"📊 Загружено прогнозов: {len(predictions)}", flush=True)
    print("==================================================", flush=True)

    generate_stats()
    threading.Thread(target=start_web_server, daemon=True).start()

    last_stats_hour = datetime.now(MOSCOW_TZ).hour

    while True:
        try:
            update_sleep_state()

            if should_cleanup_now():
                cleanup_nightly()

            telegram_offset = process_telegram_updates(telegram_offset)
            finalize_pending_games()
            check_predictions()
            cleanup_games_cache()
            cleanup_predictions()

            now = datetime.now(MOSCOW_TZ)
            if last_stats_hour != now.hour:
                generate_stats()
                last_stats_hour = now.hour

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