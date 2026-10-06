import re
import json
import time
import threading

from datetime import datetime

from config import (
    validate_env,
    MOSCOW_TZ,
    PREDICTIONS_FILE,
    POLL_INTERVAL,
    FINALIZE_WAIT_SECONDS,
    GAME_CYCLE,
    DOGON_GAMES,
    DOGON_MULT,
    START_BALANCE,
    WIN_COEF,
    SLEEP_HOUR,
    SLEEP_MINUTE,
    WAKE_HOUR,
    WAKE_MINUTE,
    CLEANUP_HOUR,
    CLEANUP_MINUTE,
    CHANNEL_STATS,
)

import bank as bank_module
import parsers
import telegram_api
import stats as stats_module
import web_server


validate_env()


# =====================================================================
# GLOBALS
# =====================================================================

games_cache = {}
pending_games = {}
processed_triggers = set()
predictions = []
telegram_offset = 0

sleeping = False

last_cleanup_date = None


# =====================================================================
# PREDICTIONS JSON
# =====================================================================

def load_predictions():
    global predictions
    import os

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
    import os
    try:
        tmp = PREDICTIONS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(predictions, f, ensure_ascii=False, indent=2)
        os.replace(tmp, PREDICTIONS_FILE)
    except Exception as e:
        print(f"⚠️ Ошибка сохранения прогнозов: {e}", flush=True)


# =====================================================================
# ГЕНЕРАЦИЯ СТАТИСТИКИ
# =====================================================================

def generate_stats():
    stats_module.generate_stats(predictions)


# =====================================================================
# НОЧНАЯ ОЧИСТКА (03:00)
# =====================================================================

def should_cleanup_now(now=None):
    global last_cleanup_date

    if now is None:
        now = datetime.now(MOSCOW_TZ)

    if last_cleanup_date == now.date():
        return False

    from datetime import time as dtime
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

def is_sleep_time(now=None):
    from datetime import time as dtime

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


def update_sleep_state():
    global sleeping

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
            print("☀️ Бот проснулся.", flush=True)


# =====================================================================
# PREDICTION MESSAGES
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


def make_result_message(prediction, result):
    suit = prediction["predicted_suit"]
    target = prediction["target_number"]
    mark = "✅" if result == "win" else "❌"

    state = bank_module.load_bank()
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
# CREATE PREDICTIONS
# =====================================================================

def create_predictions(game):
    game_number = game["game_number"]
    game_id = game.get("game_id")

    for prediction in parsers.get_algorithm_predictions(game, GAME_CYCLE):
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
        bet, step, _ = bank_module.get_current_bet()
        prediction["bets"] = [
            round(bet, 2),
            round(bet * DOGON_MULT, 2),
            round(bet * DOGON_MULT ** 2, 2),
            round(bet * DOGON_MULT ** 3, 2),
        ]
        prediction["bet_amount"] = prediction["bets"][0]
        prediction["bet_step"] = step

        # НЕ отправляем сразу — отправим за 1 игру до цели
        prediction["message_id"] = None
        prediction["sent"] = False

        predictions.append(prediction)
        processed_triggers.add(trigger_key)
        save_predictions()

        print("", flush=True)
        print("🔮 ПРОГНОЗ ПОДГОТОВЛЕН (ждём игру за 1 до цели)", flush=True)
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
# TRY SEND PREDICTION (за 1 игру до цели)
# =====================================================================

def try_send_prediction(current_game_number):
    changed = False

    for prediction in predictions:
        if prediction.get("status") != "pending":
            continue
        if prediction.get("sent"):
            continue

        target = prediction.get("target_number")
        if not target:
            continue

        if current_game_number == target - 1 or current_game_number == target:
            message = make_prediction_message(prediction)
            message_id = telegram_api.telegram_send(message)

            if message_id:
                prediction["message_id"] = message_id
                prediction["sent"] = True
                changed = True

                print("", flush=True)
                print("📤 ПРОГНОЗ ОТПРАВЛЕН", flush=True)
                print(f"🎯 Цель: #N{target}", flush=True)
                print(f"📌 Отправлен при игре #N{current_game_number}", flush=True)
            else:
                print(
                    f"❌ Не удалось отправить прогноз на #N{target}",
                    flush=True,
                )

    if changed:
        save_predictions()


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

            game_number = parsers.add_game_offset(target, dogon, GAME_CYCLE)
            game = games_cache.get(game_number)

            if not game:
                all_games_checked = False
                print(
                    f"⏳ #N{target}: ждём #N{game_number} (догон {dogon})",
                    flush=True,
                )
                break

            found_card = parsers.check_prediction_suit(game, predicted_suit)

            if found_card:
                prediction["status"] = "win"
                prediction["result_game"] = game_number
                prediction["found_card"] = found_card
                prediction["dogon"] = dogon

                bets = prediction.get("bets") or [prediction.get("bet_amount", 0)]
                actual_bet = bets[dogon] if dogon < len(bets) else bets[-1]
                bank_module.apply_result(
                    "win", actual_bet, dogon,
                    telegram_send_func=telegram_api.telegram_send,
                )

                telegram_api.telegram_edit(
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
        prediction["result_game"] = parsers.add_game_offset(
            target, DOGON_GAMES, GAME_CYCLE
        )
        prediction["dogon"] = DOGON_GAMES

        bets = prediction.get("bets") or [prediction.get("bet_amount", 0)]
        actual_bet = bets[DOGON_GAMES] if DOGON_GAMES < len(bets) else bets[-1]
        bank_module.apply_result(
            "lose", actual_bet, DOGON_GAMES,
            telegram_send_func=telegram_api.telegram_send,
        )

        telegram_api.telegram_edit(
            prediction.get("message_id"),
            make_result_message(prediction, "lose"),
        )

        print("", flush=True)
        print(f"❌ MINUS #N{target}", flush=True)
        print(
            f"🏁 Проверены все игры "
            f"#N{target} — #N{parsers.add_game_offset(target, DOGON_GAMES, GAME_CYCLE)}",
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
        game = parsers.parse_game_message(text)

        if not game:
            print(
                f"⚠️ #N{game_number} не удалось разобрать "
                f"после {FINALIZE_WAIT_SECONDS} секунд",
                flush=True,
            )
            continue

        games_cache[game_number] = game
        parsers.log_game(game)

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
    updates = telegram_api.get_updates(offset)

    for update in updates:
        update_id = update.get("update_id")

        if update_id is not None:
            offset = update_id + 1
            telegram_api.save_offset(offset)

        chat_id, text = telegram_api.extract_channel_post(update)

        if not chat_id or not text:
            continue

        if not telegram_api.is_stats_channel(chat_id):
            continue

        number_match = re.search(r"#N(\d+)", text)
        if not number_match:
            continue

        game_number = int(number_match.group(1))

        game = parsers.parse_game_message(text)

        # проверяем, не пора ли отправить прогноз
        try_send_prediction(game_number)

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
    print("🚀 OLD_GROUP — TELEGRAM STATS FORECAST", flush=True)
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
    print(f"🔄 Догонов: {DOGON_GAMES} (0, 1, 2, ..., {DOGON_GAMES})", flush=True)
    print(f"🔁 Цикл нумерации игр: {GAME_CYCLE}", flush=True)
    print(f"💰 Стартовый банк: {START_BALANCE} ₽", flush=True)
    print(f"📈 Коэффициент: {WIN_COEF}, множитель догона: {DOGON_MULT}", flush=True)
    print("📤 Отправка прогноза: за 1 игру до цели", flush=True)
    print("==================================================", flush=True)

    load_predictions()

    telegram_offset = telegram_api.load_offset()

    print(f"📌 Telegram offset: {telegram_offset}", flush=True)
    print(f"📊 Загружено прогнозов: {len(predictions)}", flush=True)
    print("==================================================", flush=True)

    generate_stats()

    threading.Thread(
        target=web_server.start_web_server,
        daemon=True,
    ).start()

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