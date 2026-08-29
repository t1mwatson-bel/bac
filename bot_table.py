import os
import sys
import requests
import json
import re
import time
from pathlib import Path
from datetime import datetime, timedelta
import pytz
import joblib
from catboost import CatBoostClassifier

# =====================================================================
# ПРИНУДИТЕЛЬНЫЙ ВЫВОД ЛОГОВ
# =====================================================================
sys.stdout.flush()
print("=" * 70, flush=True)
print("🃏 ML BOT (LATENCY + RANK MATCHING + SELF-LEARNING)", flush=True)
print("📌 Прогноз только при совпадении масти по задержке и рангу", flush=True)
print("🎯 Минимальная уверенность: 28%", flush=True)
print("🧠 Самообучение: каждые 10 прогнозов", flush=True)
print("=" * 70, flush=True)

# =====================================================================
# ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ
# =====================================================================
BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("BOT_TOKEN_PROGNOZ")
CHANNEL_STATS = os.getenv("CHANNEL_STATS")
CHANNEL_PROGNOZ = os.getenv("CHANNEL_PROGNOZ")
LIVE_COOKIE = os.getenv("LIVE_COOKIE", "")

if not BOT_TOKEN or not CHANNEL_STATS or not CHANNEL_PROGNOZ:
    print("❌ Ошибка: BOT_TOKEN, CHANNEL_STATS или CHANNEL_PROGNOZ не заданы!", flush=True)
    sys.exit(1)

print("✅ Все переменные заданы!", flush=True)

# =====================================================================
# НАСТРОЙКИ
# =====================================================================
MOSCOW_TZ = pytz.timezone("Europe/Moscow")
BASE_URL = os.getenv("BASE_URL", "https://1xlite-36553.pro")

OFFSET = 1
DOGON_GAMES = int(os.getenv("DOGON_GAMES", "4"))
MIN_CONFIDENCE = 28.0

STATE_DIR = Path(os.getenv("STATE_DIR", ".")).resolve()
STATE_DIR.mkdir(parents=True, exist_ok=True)

STATE_FILE = STATE_DIR / "hybrid_state.json"
OFFSET_FILE = STATE_DIR / "telegram_offset.txt"
MODEL_FILE = STATE_DIR / "catboost_model.joblib"

MAX_MESSAGES = 3000
MAX_STATE_PREDICTIONS = 3000

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": f"{BASE_URL}/ru/live/twentyone/2092323-21-classics",
}
if LIVE_COOKIE:
    HEADERS["Cookie"] = LIVE_COOKIE

SUITS = ["♠️", "♣️", "♦️", "♥️"]

# =====================================================================
# ТАБЛИЦА ЗАДЕРЖЕК
# =====================================================================
LATENCY_PROBS = {
    (93, 95): {"♣️": 28.3, "♥️": 24.5, "♠️": 23.5, "♦️": 23.7},
    (95, 97): {"♠️": 29.1, "♥️": 28.7, "♣️": 21.5, "♦️": 20.7},
    (97, 99): {"♦️": 26.7, "♠️": 25.9, "♣️": 24.5, "♥️": 22.9},
    (99, 101): {"♥️": 27.4, "♦️": 25.3, "♠️": 24.2, "♣️": 23.1},
    (101, 103): {"♣️": 26.5, "♠️": 25.1, "♥️": 24.8, "♦️": 23.6},
    (103, 105): {"♥️": 27.8, "♦️": 24.6, "♣️": 24.2, "♠️": 23.4},
    (105, 200): {"♠️": 27.6, "♣️": 25.9, "♥️": 24.3, "♦️": 22.2},
}

# =====================================================================
# ФУНКЦИИ ДЛЯ РАБОТЫ С ФАЙЛАМИ
# =====================================================================
def default_state():
    return {
        "predictions": [],
        "training_samples": [],
        "training_history": [],
        "training_data": [],
        "learned_probs": {},
        "total_predictions": 0,
        "wins": 0,
        "losses": 0,
        "ml_model_trained": False,
        "ml_samples": 0,
    }

def load_json(path, default):
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            for key in default:
                if key not in data:
                    data[key] = default[key]
            return data
    except Exception as e:
        print(f"⚠️ Не удалось загрузить {path}: {e}", flush=True)
        return default

def save_json(path, data):
    tmp = Path(str(path) + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)

state = load_json(STATE_FILE, default_state())
all_messages = []

# =====================================================================
# ML ОБУЧЕНИЕ
# =====================================================================
def collect_training_data(latency, p1, predicted_suit, actual_suit, result):
    """
    Сохраняет данные для обучения ML.
    """
    if "training_data" not in state:
        state["training_data"] = []
    
    features = {
        "latency": latency,
        "p1_rank": p1["rank"] if p1 else "unknown",
        "p1_suit": p1["suit"] if p1 else "unknown",
    }
    
    state["training_data"].append({
        "features": features,
        "predicted_suit": predicted_suit,
        "actual_suit": actual_suit,
        "result": result,
        "timestamp": datetime.now(MOSCOW_TZ).isoformat()
    })
    
    if len(state["training_data"]) > 5000:
        state["training_data"] = state["training_data"][-5000:]
    
    save_json(STATE_FILE, state)
    
    # Обучение каждые 10 новых примеров
    if len(state["training_data"]) >= 30 and len(state["training_data"]) % 10 == 0:
        train_ml_model()

def train_ml_model():
    """
    Обучает CatBoost на собранных данных.
    """
    data = state.get("training_data", [])
    if len(data) < 30:
        return
    
    print(f"🧠 ML: начинаю обучение на {len(data)} примерах...", flush=True)
    
    rank_map = {r: i for i, r in enumerate(["6", "7", "8", "9", "10", "J", "Q", "K", "A"])}
    suit_map = {s: i for i, s in enumerate(SUITS)}
    
    X = []
    y = []
    
    for entry in data:
        features = entry["features"]
        vector = [
            features["latency"],
            rank_map.get(features["p1_rank"], -1),
            suit_map.get(features["p1_suit"], -1),
        ]
        X.append(vector)
        y.append(SUITS.index(entry["actual_suit"]))
    
    if len(X) < 30:
        return
    
    try:
        model = CatBoostClassifier(
            iterations=150,
            depth=6,
            learning_rate=0.1,
            loss_function='MultiClass',
            auto_class_weights='Balanced',
            verbose=False,
            random_seed=42,
        )
        model.fit(X, y)
        
        joblib.dump(model, MODEL_FILE)
        state["ml_model_trained"] = True
        state["ml_samples"] = len(X)
        save_json(STATE_FILE, state)
        
        print(f"✅ ML модель обучена на {len(X)} примерах.", flush=True)
    except Exception as e:
        print(f"❌ Ошибка обучения ML: {e}", flush=True)

def ml_predict_probs(latency, p1):
    """
    Возвращает вероятности по всем мастям от ML модели.
    """
    if not MODEL_FILE.exists() or not state.get("ml_model_trained", False):
        return None
    
    try:
        model = joblib.load(MODEL_FILE)
        
        rank_map = {r: i for i, r in enumerate(["6", "7", "8", "9", "10", "J", "Q", "K", "A"])}
        suit_map = {s: i for i, s in enumerate(SUITS)}
        
        p1_rank = p1["rank"] if p1 else "unknown"
        p1_suit = p1["suit"] if p1 else "unknown"
        
        vector = [
            latency,
            rank_map.get(p1_rank, -1),
            suit_map.get(p1_suit, -1),
        ]
        
        probs = model.predict_proba([vector])[0]
        
        result = {}
        for i, suit in enumerate(SUITS):
            result[suit] = round(probs[i] * 100, 1)
        
        return result
    except Exception as e:
        print(f"⚠️ Ошибка ML прогноза: {e}", flush=True)
        return None

# =====================================================================
# ФУНКЦИИ ТЕЛЕГРАМ
# =====================================================================
def send_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(
            url,
            json={"chat_id": CHANNEL_PROGNOZ, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
        if r.status_code == 200:
            return r.json()["result"]["message_id"]
    except Exception as e:
        print(f"❌ sendMessage: {e}", flush=True)
    return None

def edit_message(message_id, text):
    if not message_id:
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText"
    try:
        r = requests.post(
            url,
            json={
                "chat_id": CHANNEL_PROGNOZ,
                "message_id": message_id,
                "text": text,
                "parse_mode": "HTML",
            },
            timeout=10,
        )
        return r.status_code == 200
    except Exception as e:
        print(f"❌ editMessageText: {e}", flush=True)
        return False

def telegram_get_updates(offset):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    try:
        r = requests.get(url, params={"offset": offset, "timeout": 30}, timeout=35)
        if r.status_code != 200:
            print(f"❌ Telegram getUpdates: {r.status_code} {r.text[:300]}", flush=True)
            return {}
        return r.json()
    except Exception as e:
        print(f"❌ getUpdates: {e}", flush=True)
        return {}

# =====================================================================
# ПАРСИНГ
# =====================================================================
def parse_game_from_text(text):
    try:
        m = re.search(r"#N(\d+)", text)
        if not m:
            return None
        number = int(m.group(1))

        if "◀️" in text:
            parts = text.split("◀️", 1)
        elif "▶️" in text:
            parts = text.split("▶️", 1)
        elif "—" in text:
            parts = text.split("—", 1)
        elif " - " in text:
            parts = text.split(" - ", 1)
        elif "-" in text:
            parts = text.split("-", 1)
        else:
            return None

        if len(parts) < 2:
            return None

        def parse_cards(part):
            m2 = re.search(r"\(([^)]*)\)", part)
            if not m2:
                return []
            s = m2.group(1)
            cards = []
            i = 0
            while i < len(s):
                if s[i].isspace():
                    i += 1
                    continue
                if s.startswith("10", i):
                    rank = "10"
                    i += 2
                elif s[i] in "AKQJ":
                    rank = s[i]
                    i += 1
                elif s[i].isdigit():
                    rank = s[i]
                    i += 1
                else:
                    i += 1
                    continue
                suit = None
                for sym in SUITS:
                    if s.startswith(sym, i):
                        suit = sym
                        i += len(sym)
                        break
                if suit is None and i < len(s) and s[i] in "♠♣♦♥":
                    suit = s[i] + "️"
                    i += 1
                if suit:
                    cards.append({"rank": rank, "suit": suit})
            return cards

        return {
            "number": number,
            "player_cards": parse_cards(parts[0]),
            "dealer_cards": parse_cards(parts[1]),
            "text": text,
        }
    except Exception as e:
        print(f"❌ parse_game: {e}", flush=True)
        return None

def is_finished_game(text):
    return "✅" in text or "🔰" in text

# =====================================================================
# LIVE API
# =====================================================================
def get_active_games():
    url = (
        f"{BASE_URL}/service-api/main-live-feed/v3/games1x2"
        "?cfView=3&count=40&fcountry=1&gr=415&grMode=4&lng=ru&ref=7"
        "&selectedMs=1.146.2092323,2.146.2092323,10.146.2092323"
    )
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.status_code != 200:
            print(f"⚠️ active games HTTP {r.status_code}", flush=True)
            return []
        data = r.json()
        games = data.get("Value", []) if isinstance(data, dict) else data
        return [
            g for g in games
            if g.get("liga", {}).get("id") == 2092323 and g.get("id")
        ]
    except Exception as e:
        print(f"❌ get_active_games: {e}", flush=True)
        return []

def get_game_data(game_id):
    url = (
        f"{BASE_URL}/service-api/LiveFeed/GetGameZip?id={game_id}"
        "&isSubGames=true&GroupEvents=true&countevents=250&grMode=4"
        "&partner=7&topGroups=&country=190&marketType=1&isNewBuilder=true"
    )
    try:
        t0 = time.perf_counter()
        r = requests.get(url, headers=HEADERS, timeout=5)
        latency = (time.perf_counter() - t0) * 1000
        if r.status_code == 200:
            return r.json(), latency
    except Exception as e:
        print(f"⚠️ get_game_data {game_id}: {e}", flush=True)
    return None, None

def get_fresh_latency():
    games = get_active_games()
    if not games:
        return None
    for g in games:
        data, latency = get_game_data(str(g.get("id")))
        if data is not None and latency is not None:
            return latency
    return None

def get_game_number():
    now = datetime.now(MOSCOW_TZ)
    start = now.replace(hour=3, minute=0, second=0, microsecond=0)
    if now < start:
        start = start - timedelta(days=1)
    diff_minutes = (now - start).total_seconds() / 60
    return int(diff_minutes) // 2 % 720 + 1

# =====================================================================
# ПРОГНОЗ
# =====================================================================
def predict_suit_by_latency(latency):
    if 93 <= latency < 95:
        return "♣️"
    elif 95 <= latency < 97:
        return "♠️"
    elif 97 <= latency < 99:
        return "♦️"
    elif 99 <= latency < 101:
        return "♥️"
    elif 101 <= latency < 103:
        return "♣️"
    elif 103 <= latency < 105:
        return "♥️"
    elif latency >= 105:
        return "♠️"
    else:
        return None

def refine_by_sequence(p1, p2, p3, base_suit, latency):
    if 93 <= latency < 95:
        if p1 and p1.get("rank") == "7" and p1.get("suit") == "♣️":
            return "♥️"
        elif p1 and p1.get("rank") == "8" and p1.get("suit") == "♠️":
            return "♣️"
        elif p1 and p1.get("rank") == "9" and p1.get("suit") == "♥️":
            return "♦️"
        elif p1 and p1.get("rank") in ["J", "Q", "K"] and p1.get("suit") == "♣️":
            return "♥️"
        elif p1 and p1.get("rank") in ["J", "Q", "K"] and p1.get("suit") == "♠️":
            return "♣️"
    
    if 95 <= latency < 97:
        if p1 and p1.get("rank") == "7" and p1.get("suit") == "♠️":
            return "♣️"
        elif p1 and p1.get("rank") == "8" and p1.get("suit") == "♣️":
            return "♥️"
        elif p1 and p1.get("rank") == "9" and p1.get("suit") == "♦️":
            return "♠️"
    
    if 97 <= latency < 99:
        if p1 and p1.get("rank") == "9" and p1.get("suit") == "♦️":
            return "♠️"
        elif p1 and p1.get("rank") == "8" and p1.get("suit") == "♠️":
            return "♦️"
        elif p1 and p1.get("rank") == "7" and p1.get("suit") == "♥️":
            return "♣️"
    
    if p1 and p2 and p1.get("suit") == p2.get("suit"):
        if p1.get("suit") == "♣️":
            return "♥️"
        elif p1.get("suit") == "♠️":
            return "♦️"
        elif p1.get("suit") == "♦️":
            return "♣️"
        elif p1.get("suit") == "♥️":
            return "♠️"
    
    return base_suit

# =====================================================================
# GAME STORAGE
# =====================================================================
games_by_number = {}

def add_game(text):
    game = parse_game_from_text(text)
    if not game:
        return None
    n = game["number"]
    games_by_number[n] = game
    return game

def find_game(n):
    return games_by_number.get(n)

def cleanup_games():
    if len(games_by_number) > MAX_MESSAGES:
        keys = sorted(games_by_number.keys())
        for k in keys[:-MAX_MESSAGES]:
            games_by_number.pop(k, None)

# =====================================================================
# ПРОВЕРКА РЕЗУЛЬТАТА
# =====================================================================
def check_results():
    global all_messages, state

    for entry in state.get("predictions", []):
        if entry.get("status") != "pending":
            continue

        target = entry.get("target")
        predicted_suit = entry.get("selected_prediction")
        message_id = entry.get("message_id")
        original_text = entry.get("message_text", "")
        latency = entry.get("latency", 100)
        p1 = entry.get("p1", None)

        if not predicted_suit or not message_id:
            continue

        max_games_to_check = DOGON_GAMES

        for i in range(max_games_to_check):
            game_to_check = target + i

            game_msg = None
            for msg in all_messages:
                if f"#N{game_to_check}" in msg and ('✅' in msg or '🔰' in msg):
                    game_msg = msg
                    break

            if not game_msg:
                print(f"⏳ Ждём игру #N{game_to_check} для проверки масти {predicted_suit}", flush=True)
                continue

            game_data = parse_game_from_text(game_msg)
            if not game_data:
                continue

            suit_found = False
            actual_suit = None
            player_cards = game_data.get("player_cards", [])
            
            for card in player_cards:
                if card.get("suit") == predicted_suit:
                    suit_found = True
                    actual_suit = predicted_suit
                    break
            
            if not actual_suit and player_cards:
                actual_suit = player_cards[0].get("suit", "♠️")

            if suit_found:
                print(f"🎯 МАСТЬ {predicted_suit} НАЙДЕНА в игре #N{game_to_check}!", flush=True)
                dogon_number = i

                entry["selected_result"] = True
                entry["evaluated"] = True
                entry["result_game"] = game_to_check
                entry["dogon"] = dogon_number
                entry["status"] = "win"

                state["wins"] = state.get("wins", 0) + 1
                state["total_predictions"] = state.get("total_predictions", 0) + 1

                # Сохраняем для обучения
                collect_training_data(latency, p1, predicted_suit, actual_suit, True)

                suffix = f"\n\n✅ <b>ЗАШЛО</b> в игре #N{game_to_check}" if i == 0 else f"\n\n✅ <b>ЗАШЛО</b> на догоне {i}: #N{game_to_check}"
                edit_message(message_id, original_text + suffix)
                entry["message_text"] = original_text + suffix

                save_json(STATE_FILE, state)
                return

            if i == max_games_to_check - 1:
                print(f"❌ Масть {predicted_suit} НЕ НАЙДЕНА за {max_games_to_check} игр", flush=True)

                entry["selected_result"] = False
                entry["evaluated"] = True
                entry["status"] = "lose"

                state["losses"] = state.get("losses", 0) + 1
                state["total_predictions"] = state.get("total_predictions", 0) + 1

                collect_training_data(latency, p1, predicted_suit, actual_suit, False)

                suffix = f"\n\n❌ <b>НЕ ЗАШЛО</b> (проверено {max_games_to_check} игр)"
                edit_message(message_id, original_text + suffix)
                entry["message_text"] = original_text + suffix

                save_json(STATE_FILE, state)
                return

# =====================================================================
# PREDICTION SCHEDULER
# =====================================================================
def schedule_for_game(game_number):
    target = game_number + OFFSET

    for e in state.get("predictions", []):
        if e.get("target") == target and e.get("status") in ("scheduled", "pending"):
            return

    source = target - 1

    state["predictions"].append({
        "source": source,
        "target": target,
        "selected_prediction": None,
        "latency": None,
        "confidence": 0.0,
        "status": "scheduled",
        "evaluated": False,
        "created": datetime.now(MOSCOW_TZ).isoformat(),
        "message_id": None,
        "message_text": "",
        "p1": None,
    })
    if len(state["predictions"]) > MAX_STATE_PREDICTIONS:
        state["predictions"] = state["predictions"][-MAX_STATE_PREDICTIONS:]
    save_json(STATE_FILE, state)

    print(f"📅 Запланировано: #{game_number} → #{target} (+{OFFSET})", flush=True)

# =====================================================================
# ОСНОВНАЯ ЛОГИКА ПРОГНОЗА
# =====================================================================
def process_scheduled():
    for entry in state.get("predictions", []):
        if entry.get("status") != "scheduled":
            continue

        target = entry.get("target")
        source_num = entry.get("source")
        source = find_game(source_num)

        if not source:
            continue

        latency = get_fresh_latency()
        if latency is None:
            print("⏳ Нет свежей задержки — прогноз остаётся в очереди", flush=True)
            continue

        # 1. Определяем масть по задержке
        base_suit = predict_suit_by_latency(latency)
        if base_suit is None:
            print(f"⏭️ Задержка {latency:.1f} мс — нет базовой масти", flush=True)
            continue

        # 2. Получаем уверенность по задержке из таблицы
        confidence = None
        for (low, high), probs in LATENCY_PROBS.items():
            if low <= latency < high:
                confidence = probs.get(base_suit, 0)
                break
        
        if confidence is None or confidence < MIN_CONFIDENCE:
            print(f"⏭️ Уверенность {confidence:.1f}% < {MIN_CONFIDENCE:.0f}% — НЕ ДАЁМ", flush=True)
            entry["status"] = "skipped"
            entry["selected_prediction"] = None
            entry["confidence"] = confidence or 0.0
            entry["latency"] = latency
            save_json(STATE_FILE, state)
            continue

        # 3. Определяем масть по рангу (уточнение)
        p = source.get("player_cards", [])
        d = source.get("dealer_cards", [])
        p1 = p[0] if len(p) > 0 else None
        p3 = p[1] if len(p) > 1 else None
        p2 = d[0] if len(d) > 0 else None

        refined_suit = refine_by_sequence(p1, p2, p3, base_suit, latency)

        # 4. Сравниваем масти
        if refined_suit != base_suit:
            print(f"⏭️ Масть по рангу ({refined_suit}) не совпадает с мастью по задержке ({base_suit}) — НЕ ДАЁМ", flush=True)
            entry["status"] = "skipped"
            entry["selected_prediction"] = None
            entry["confidence"] = confidence
            entry["latency"] = latency
            save_json(STATE_FILE, state)
            continue

        # 5. ВСЁ СОВПАЛО — даём прогноз
        print(f"✅ Совпадение! Задержка {latency:.1f} мс → {base_suit} ({confidence:.1f}%), ранг подтверждает", flush=True)

        # ⭐ Получаем вероятности от ML (для вывода)
        ml_probs = ml_predict_probs(latency, p1)
        
        # Если ML не дал проценты — используем табличные
        if ml_probs is None:
            ml_probs = {}
            for suit in SUITS:
                if suit == base_suit:
                    ml_probs[suit] = confidence
                else:
                    # Ищем процент для других мастей в таблице
                    for (low, high), probs in LATENCY_PROBS.items():
                        if low <= latency < high:
                            ml_probs[suit] = probs.get(suit, 0.0)
                            break

        entry["selected_prediction"] = base_suit
        entry["latency"] = latency
        entry["confidence"] = confidence
        entry["status"] = "pending"
        entry["p1"] = p1

        msg = prediction_text(entry, base_suit, source, confidence, ml_probs)
        mid = send_message(msg)

        if mid:
            entry["message_id"] = mid
            entry["message_text"] = msg
            save_json(STATE_FILE, state)

            print(
                f"✅ ПРОГНОЗ: #{target} → {base_suit} | "
                f"уверенность={confidence:.1f}%",
                flush=True,
            )
        else:
            entry["status"] = "scheduled"
            save_json(STATE_FILE, state)

# =====================================================================
# ФОРМИРОВАНИЕ СООБЩЕНИЯ С РАСКЛАДКОЙ ПО МАСТЯМ
# =====================================================================
def prediction_text(entry, prediction, source_game, confidence, all_probs):
    msg = "🔮 <b>ML 21 CLASSIC</b>\n"
    msg += f"🃏 Масть: {prediction}\n"
    msg += f"🎯 Уверенность: {confidence:.1f}%\n"
    
    # ⭐ ВЫВОД ВСЕХ МАСТЕЙ
    if all_probs:
        msg += "📊 <b>Раскладка:</b>\n"
        sorted_probs = sorted(all_probs.items(), key=lambda x: x[1], reverse=True)
        for suit, prob in sorted_probs:
            bar = "█" * int(prob / 2) if prob > 0 else "░"
            msg += f"  {suit}: {prob:.1f}% {bar}\n"
    
    msg += f"🎯 Целевая игра: #N{entry['target']}\n"
    msg += f"📈 Догон: {DOGON_GAMES - 1} игры\n"

    p = source_game.get("player_cards", [])
    d = source_game.get("dealer_cards", [])
    seq = []
    for prefix, cards in [("P", p[:3]), ("D", d[:3])]:
        for i, c in enumerate(cards, 1):
            seq.append(f"{prefix}{i}:{c['rank']}{c['suit']}")
    if seq:
        msg += "📌 " + " ".join(seq) + "\n"

    msg += "⏰ " + datetime.now(MOSCOW_TZ).strftime("%H:%M:%S")
    return msg

# =====================================================================
# STATS
# =====================================================================
def stats_text():
    total = state.get("total_predictions", 0)
    wins = state.get("wins", 0)
    losses = state.get("losses", 0)
    acc = f"{wins / total * 100:.1f}%" if total else "—"
    ml_samples = state.get("ml_samples", 0)
    ml_trained = state.get("ml_model_trained", False)

    return (
        "📊 <b>СТАТИСТИКА (LATENCY + RANK MATCHING + ML)</b>\n"
        f"⏰ {datetime.now(MOSCOW_TZ).strftime('%d.%m.%Y %H:%M:%S')}\n"
        "==============================\n"
        f"📈 Всего прогнозов: {total}\n"
        f"✅ Зашло: {wins}\n"
        f"❌ Не зашло: {losses}\n"
        f"🎯 Точность: {acc}\n"
        f"🧠 ML: {'ОБУЧЕНА' if ml_trained else 'ОБУЧАЕТСЯ'} ({ml_samples} примеров)\n"
        f"🎯 Минимальный порог: {MIN_CONFIDENCE:.0f}%"
    )

# =====================================================================
# OFFSET
# =====================================================================
def load_offset():
    try:
        return int(OFFSET_FILE.read_text().strip())
    except Exception:
        return 0

def save_offset(v):
    OFFSET_FILE.write_text(str(v), encoding="utf-8")

# =====================================================================
# MAIN
# =====================================================================
def main():
    global all_messages, state

    print("=" * 70, flush=True)
    print("🃏 ML BOT (LATENCY + RANK MATCHING + SELF-LEARNING)", flush=True)
    print("=" * 70, flush=True)

    send_message(stats_text())

    offset = load_offset()
    last_stats = time.time()

    print("🚀 БОТ ГОТОВ.", flush=True)
    print(f"🎯 OFFSET: +{OFFSET}", flush=True)
    print(f"🎯 Минимальная уверенность: {MIN_CONFIDENCE:.0f}%", flush=True)

    while True:
        try:
            now = time.time()

            check_results()
            process_scheduled()

            if now - last_stats > 3600:
                send_message(stats_text())
                last_stats = now

            updates = telegram_get_updates(offset)

            for upd in updates.get("result", []):
                uid = upd.get("update_id")
                if uid is not None:
                    offset = uid + 1
                    save_offset(offset)

                post = upd.get("channel_post") or upd.get("edited_channel_post")
                if not post:
                    continue

                chat_id = str(post.get("chat", {}).get("id", ""))
                if chat_id != str(CHANNEL_STATS):
                    continue

                text = post.get("text", "")
                if "#N" not in text:
                    continue

                if text not in all_messages:
                    all_messages.append(text)
                    if len(all_messages) > 500:
                        all_messages = all_messages[-500:]

                game = add_game(text)
                if not game:
                    continue

                n = game["number"]
                print(
                    f"📥 Игра #{n} | {'завершена' if is_finished_game(text) else 'не завершена'}",
                    flush=True,
                )

                if not is_finished_game(text):
                    schedule_for_game(n)

                cleanup_games()

            check_results()
            process_scheduled()

            time.sleep(1)

        except KeyboardInterrupt:
            print("🛑 Остановка", flush=True)
            break
        except Exception as e:
            print(f"❌ MAIN ERROR: {e}", flush=True)
            traceback.print_exc()
            try:
                save_json(STATE_FILE, state)
            except Exception:
                pass
            time.sleep(10)

if __name__ == "__main__":
    main()