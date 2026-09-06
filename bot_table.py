r card in predicted_cards:
                if card in actual_cards:
                    found = {"num": num, "dogon": dogon, "card": card}
                    break

            if found:
                break

        if found:
            entry["status"] = "win"
            entry["result_game"] = found["num"]
            entry["found_card"] = found["card"]
            entry["current_dogon"] = found["dogon"]
            changed = True

            print(f"✅ ЗАШЛО на #{found['num']} | догон {found['dogon']} | {found['card']}")

            if msg_id and original_text:
                lines = original_text.split('\n')
                lines[0] = f"🎯 Игра: #N{target} ✅"
                new_text = '\n'.join(lines)
                telegram_edit(msg_id, new_text)

            atomic_save_json(PREDICTIONS_FILE, predictions)
            continue

        if not all_available:
            print(f"⏳ Ожидание #{target} (не все догоны в кэше)")
            continue

        entry["status"] = "lose"
        changed = True

        print(f"❌ НЕ ЗАШЛО: догоны 0-{DOGON_GAMES} для #{target}")

        if msg_id and original_text:
            lines = original_text.split('\n')
            lines[0] = f"🎯 Игра: #N{target} ❌"
            new_text = '\n'.join(lines)
            telegram_edit(msg_id, new_text)

        atomic_save_json(PREDICTIONS_FILE, predictions)

    if changed:
        print("💾 Прогнозы обновлены")


# =====================================================================
# CREATE PREDICTION
# =====================================================================

def create_hybrid_prediction(game_id, game_number):
    global last_prediction_time

    game_id = str(game_id)

    # Проверяем, есть ли уже прогноз на этот номер
    for entry in predictions:
        if entry.get("target_number") == game_number and entry.get("status") == "pending":
            print(f"⏭️ Прогноз на #N{game_number} уже существует")
            return None

    # Минимальная задержка
    now_ts = time.time()
    if now_ts - last_prediction_time < PREDICTION_COOLDOWN_SECONDS:
        print(f"⏭️ Cooldown {PREDICTION_COOLDOWN_SECONDS} сек")
        return None

    now = datetime.now(MOSCOW_TZ)
    timestamp_msk = now.strftime("%H:%M:%S.%f")[:-3]

    print("\n══════════════════════════════════")
    print(f"🧠 HYBRID АНАЛИЗ | ID={game_id} | #N{game_number}")
    print(f"⏱ Timestamp={timestamp_msk}")

    result = build_hybrid_prediction(game_id, timestamp_msk)

    if not result:
        print("⏭️ Гибрид не дал результата")
        return None

    print(f"🥇 {result['card']} {result['probability']:.1%}")
    print(f"🥈 {result['second_card']} {result['second_probability']:.1%}")
    print(f"📏 Gap: {result['gap']:.1%}")
    print(f"🤝 Поддержка: {result['supporters']}")

    if not prediction_passes_filter(result):
        print("🚫 ПРОГНОЗ ОТМЕНЁН ФИЛЬТРОМ")
        return None

    entry = {
        "target_game_id": game_id,
        "target_number": game_number,
        "timestamp_msk": timestamp_msk,
        "hybrid": result,
        "predicted_card": result["card"],
        "predicted_cards": [result["card"], result.get("second_card")],
        "status": "pending",
        "current_dogon": 0,
        "created_at": datetime.now(MOSCOW_TZ).isoformat(),
        "message_id": None,
        "original_text": "",
        "result_game": None,
        "found_card": None
    }

    predictions.append(entry)
    atomic_save_json(PREDICTIONS_FILE, predictions)

    last_prediction_time = now_ts

    print(f"🔮 ПРОГНОЗ СОЗДАН: {result['card']} для #N{game_number}")

    return entry


# =====================================================================
# API
# =====================================================================

def get_game_data(game_id):
    url = (
        f"{BASE_URL}/service-api/"
        "LiveFeed/GetGameZip"
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
        response = SESSION.get(url, timeout=7)

        if response.status_code == 200:
            return response.json()

    except:
        pass

    return None


# =====================================================================
# API GET ACTIVE GAMES (ДЛЯ ИСТОРИИ)
# =====================================================================

def get_active_games():
    url = (
        f"{BASE_URL}/service-api/"
        "main-live-feed/v3/games1x2"
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
        response = SESSION.get(url, timeout=10)

        if response.status_code != 200:
            return []

        data = response.json()

        if isinstance(data, list):
            games = data
        elif isinstance(data, dict):
            games = data.get("Value", [])
        else:
            games = []

        result = []

        for game in games:
            if not isinstance(game, dict):
                continue

            liga = game.get("liga", {})

            if str(liga.get("id", "")) != str(LEAGUE_ID):
                continue

            if not game.get("id"):
                continue

            result.append(game)

        return result

    except Exception as e:
        print(f"❌ API games: {e}", flush=True)
        return []


# =====================================================================
# PROCESS GAME (ТОЛЬКО ДЛЯ ИСТОРИИ, БЕЗ ПРОГНОЗОВ)
# =====================================================================

def process_game(active_game):
    """Получает игру из API и сохраняет только финальную версию STATE=5."""
    gid = str(active_game.get("id", ""))
    if not gid:
        return

    raw = get_game_data(gid)
    if not raw:
        return

    parsed = parse_game_data(gid, raw)
    if not parsed:
        return

    game_number = active_game.get("gameNumber", active_game.get("number"))
    try:
        parsed["game_number"] = int(game_number) if game_number is not None else get_game_number()
    except (TypeError, ValueError):
        parsed["game_number"] = get_game_number()

    add_or_update_game(parsed)


# =====================================================================
# CLEANUP
# =====================================================================

def cleanup_predictions():
    global predictions

    if len(predictions) > 1000:
        predictions = predictions[-1000:]
        atomic_save_json(PREDICTIONS_FILE, predictions)


# =====================================================================
# MAIN
# =====================================================================

def main():
    global history, predictions

    print("\n==================================================")
    print("🚀 БОТ — ПРОГНОЗЫ ПО ID ИЗ КАНАЛА + ИСТОРИЯ ИЗ API")
    print("==================================================")

    history = load_history()
    predictions = load_predictions()

    print(f"📚 История: {len(history)} игр")
    print(f"📊 Прогнозов: {len(predictions)}")
    print("📡 Источник прогнозов: ТВОЙ КАНАЛ СТАТИСТИКИ")
    print(f"📡 Источник истории: API | окно базы: {HISTORY_HOURS} часов")
    print("==================================================\n")

    offset = get_offset()
    print(f"📌 Telegram offset: {offset}")

    while True:
        start = time.time()

        try:
            # ✅ 1. ОПРАШИВАЕМ API ДЛЯ ИСТОРИИ
            games = get_active_games()
            if games:
                print(f"📡 API: {len(games)} игр")

            for game in games:
                try:
                    process_game(game)  # ТОЛЬКО ДЛЯ ИСТОРИИ!
                except Exception as e:
                    print(f"❌ Ошибка API: {e}")

            # ✅ 2. ЧИТАЕМ КАНАЛ ДЛЯ ПРОГНОЗОВ
            offset = process_telegram_updates(offset)

            # ✅ 3. ПРОВЕРЯЕМ ПРОГНОЗЫ
            check_predictions()

            # ✅ 4. ЧИСТИМ СТАРЫЕ ПРОГНОЗЫ И ИСТОРИЮ > 48 ЧАСОВ
            cleanup_predictions()
            cleanup_history_by_time()

            elapsed = time.time() - start
            time.sleep(max(0.1, POLL_INTERVAL - elapsed))

        except KeyboardInterrupt:
            print("\n🛑 Бот остановлен")
            break

        except Exception as e:
            print(f"❌ Критическая ошибка: {e}")
            time.sleep(3)


# =====================================================================
# START
# =====================================================================

if __name__ == "__main__":
    main()