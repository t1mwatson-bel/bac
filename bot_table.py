import os
import sys
import requests
import json
import re
import time
import pickle
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta
import pytz
from collections import deque, defaultdict
import warnings
import gc
import traceback
warnings.filterwarnings('ignore')

# =====================================================================
# RANK BOT (САМООБУЧАЮЩИЙСЯ, ТОЛЬКО РАНГИ, БЕЗ МАСТЕЙ)
# =====================================================================
sys.stdout.flush()
print("=" * 70, flush=True)
print("🃏 RANK BOT (ML SELF-LEARNING)", flush=True)
print("📌 Прогноз точного ранга (6,7,8,9,10,J,Q,K,A)", flush=True)
print("🧠 Самообучение на ВСЕХ играх | Анализ ошибок", flush=True)
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
DOGON_GAMES = 4
ML_CONFIDENCE_THRESHOLD = 0.60
MAX_RECORDS = 10000
MIN_TRAIN_SAMPLES = 100
CHECK_INTERVAL = 5
MAX_HISTORY = 2000

STATE_DIR = Path(os.getenv("STATE_DIR", ".")).resolve()
STATE_DIR.mkdir(parents=True, exist_ok=True)

STATE_FILE = STATE_DIR / "rank_state.json"
OFFSET_FILE = STATE_DIR / "rank_offset.txt"
ML_MODEL_FILE = STATE_DIR / "rank_model.pkl"
DATA_FILE = STATE_DIR / "rank_data.json"
HISTORY_FILE = STATE_DIR / "rank_history.json"

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
RANKS = ["6", "7", "8", "9", "10", "J", "Q", "K", "A"]

# =====================================================================
# ML-НАСТРОЙКИ (РАНГИ ВМЕСТО КАРТ)
# =====================================================================
TARGET_RANKS = ["6", "7", "8", "9", "10", "J", "Q", "K", "A"]
RANK_VALUES = {'6': 6, '7': 7, '8': 8, '9': 9, '10': 10, 'J': 11, 'Q': 12, 'K': 13, 'A': 14}
SUITS_NAMES = {0: "♠️", 1: "♣️", 2: "♦️", 3: "♥️"}

# =====================================================================
# ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ
# =====================================================================
ml_model = None
ml_initialized = False
all_messages = []
game_history = deque(maxlen=50)
stats = {
    "total": 0,
    "win": 0,
    "lose": 0,
    "ml_wins": 0,
    "ml_losses": 0,
    "games_collected": 0,
    "by_dogon": {0: 0, 1: 0, 2: 0, 3: 0}
}
games_by_number = {}

# =====================================================================
# ФУНКЦИИ ДЛЯ РАБОТЫ С ДАННЫМИ
# =====================================================================
def load_data():
    if DATA_FILE.exists():
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return []
    return []

def save_data(record):
    data = load_data()
    
    # Проверяем дубликаты по номеру игры
    existing = None
    for i, r in enumerate(data):
        if r.get("game_num") == record.get("game_num"):
            existing = i
            break
    
    if existing is not None:
        data[existing] = record
    else:
        data.append(record)
        stats["games_collected"] += 1
    
    # Ограничиваем размер
    if len(data) > MAX_RECORDS:
        data = data[-MAX_RECORDS:]
    
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return data

def load_history():
    if HISTORY_FILE.exists():
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return []
    return []

def save_history(history):
    if len(history) > MAX_HISTORY:
        history = history[-MAX_HISTORY:]
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)

def default_state():
    return {
        "predictions": [],
        "errors": [],
        "total_games": 0,
        "total_predictions": 0,
        "wins": 0,
        "losses": 0,
        "learning_active": True,
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

# =====================================================================
# ML-ФУНКЦИИ
# =====================================================================
def extract_features_from_game(game_data, latency, game_num):
    """Извлекает признаки из игры для ML"""
    if not game_data:
        return None
    
    player_cards = game_data.get("player_cards", [])
    dealer_cards = game_data.get("dealer_cards", [])
    
    features = {
        "latency": latency if latency else 0,
        "game_num": game_num % 100,
        "p1_rank_val": 0,
        "p2_rank_val": 0,
        "p3_rank_val": 0,
        "d1_rank_val": 0,
        "d2_rank_val": 0,
        "player_total": 0,
        "dealer_total": 0,
        "player_count": len(player_cards),
        "dealer_count": len(dealer_cards),
        "hour": 0,
        "minute": 0,
        "day_of_week": 0,
        "is_weekend": 0,
    }
    
    for i, card in enumerate(player_cards[:3]):
        rank = card.get("rank", "")
        if rank in RANK_VALUES:
            features[f"p{i+1}_rank_val"] = RANK_VALUES[rank]
    
    for i, card in enumerate(dealer_cards[:2]):
        rank = card.get("rank", "")
        if rank in RANK_VALUES:
            features[f"d{i+1}_rank_val"] = RANK_VALUES[rank]
    
    player_total = 0
    for card in player_cards:
        rank = card.get("rank", "")
        if rank in RANK_VALUES:
            val = RANK_VALUES[rank]
            if val >= 11:
                player_total += 10
            else:
                player_total += val
    features["player_total"] = player_total
    
    dealer_total = 0
    for card in dealer_cards:
        rank = card.get("rank", "")
        if rank in RANK_VALUES:
            val = RANK_VALUES[rank]
            if val >= 11:
                dealer_total += 10
            else:
                dealer_total += val
    features["dealer_total"] = dealer_total
    
    now = datetime.now(MOSCOW_TZ)
    features["hour"] = now.hour
    features["minute"] = now.minute
    features["day_of_week"] = now.weekday()
    features["is_weekend"] = 1 if now.weekday() >= 5 else 0
    
    return features

def train_ml_model():
    global ml_model, ml_initialized
    
    try:
        from catboost import CatBoostClassifier
    except ImportError:
        print("⚠️ CatBoost не установлен. Установите: pip install catboost", flush=True)
        return False
    
    data = load_data()
    if len(data) < MIN_TRAIN_SAMPLES:
        print(f"⚠️ ML: недостаточно данных ({len(data)}/{MIN_TRAIN_SAMPLES})", flush=True)
        return False
    
    X = []
    y = []
    feature_names = None
    
    print(f"🧠 ML: начинаю обучение на {len(data)} играх...", flush=True)
    
    for game in data:
        player_cards = game.get("player_cards", [])
        if not player_cards:
            continue
        
        features = extract_features_from_game(game, game.get("latency", 0), game.get("game_num", 0))
        if not features:
            continue
        
        feature_vector = []
        sorted_keys = sorted(features.keys())
        if not feature_names:
            feature_names = sorted_keys
        for key in sorted_keys:
            feature_vector.append(features[key])
        
        # Берем ПЕРВУЮ карту игрока как целевой ранг
        first_card = player_cards[0] if player_cards else None
        if first_card:
            rank = first_card.get("rank", "")
            if rank in TARGET_RANKS:
                X.append(feature_vector)
                y.append(TARGET_RANKS.index(rank))
    
    if len(X) < MIN_TRAIN_SAMPLES:
        print(f"⚠️ ML: недостаточно примеров ({len(X)}/{MIN_TRAIN_SAMPLES})", flush=True)
        return False
    
    print(f"🧠 ML: обучение на {len(X)} примерах из {len(data)} игр...", flush=True)
    print(f"📊 Признаков: {len(feature_names)}", flush=True)
    
    X = np.array(X)
    y = np.array(y)
    
    model = CatBoostClassifier(
        iterations=200,
        depth=6,
        learning_rate=0.08,
        random_seed=42,
        verbose=False,
        loss_function='MultiClass',
        early_stopping_rounds=30,
        l2_leaf_reg=5,
        thread_count=1
    )
    
    model.fit(X, y)
    ml_model = model
    ml_initialized = True
    
    try:
        with open(ML_MODEL_FILE, 'wb') as f:
            pickle.dump({
                'model': model,
                'feature_count': len(X[0]),
                'train_samples': len(X),
                'total_games': len(data),
                'feature_names': feature_names
            }, f)
        print(f"✅ Модель сохранена! Обучено на {len(X)} примерах из {len(data)} игр", flush=True)
        return True
    except Exception as e:
        print(f"⚠️ Ошибка сохранения: {e}", flush=True)
        return False

def load_ml_model():
    global ml_model, ml_initialized
    
    if not ML_MODEL_FILE.exists():
        return False
    
    try:
        with open(ML_MODEL_FILE, 'rb') as f:
            data = pickle.load(f)
            ml_model = data['model']
            ml_initialized = True
            print(f"✅ ML модель загружена ({data.get('train_samples', 0)} примеров)", flush=True)
            return True
    except Exception as e:
        print(f"⚠️ Не удалось загрузить ML модель: {e}", flush=True)
        return False

def predict_ml(features):
    global ml_model, ml_initialized
    
    if not ml_initialized or not ml_model:
        return None, None
    
    try:
        feature_vector = []
        for key in sorted(features.keys()):
            feature_vector.append(features[key])
        
        feature_vector = np.array([feature_vector])
        probs = ml_model.predict_proba(feature_vector)[0]
        
        top_idx = np.argmax(probs)
        confidence = probs[top_idx]
        predicted_rank = TARGET_RANKS[top_idx]
        
        return predicted_rank, confidence
    except Exception as e:
        print(f"⚠️ Ошибка ML-прогноза: {e}", flush=True)
        return None, None

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
# GAME STORAGE
# =====================================================================
def add_game(text):
    game = parse_game_from_text(text)
    if not game:
        return None
    n = game["number"]
    games_by_number[n] = game
    
    # Сохраняем в данные для обучения
    record = {
        "game_num": n,
        "latency": 0,  # Будет обновлено при прогнозе
        "player_cards": game.get("player_cards", []),
        "dealer_cards": game.get("dealer_cards", []),
        "timestamp": datetime.now(MOSCOW_TZ).isoformat()
    }
    save_data(record)
    
    return game

def find_game(n):
    return games_by_number.get(n)

def cleanup_games():
    if len(games_by_number) > MAX_MESSAGES:
        keys = sorted(games_by_number.keys())
        for k in keys[:-MAX_MESSAGES]:
            games_by_number.pop(k, None)

# =====================================================================
# ПРОВЕРКА РЕЗУЛЬТАТА (С ОБУЧЕНИЕМ НА ОШИБКАХ)
# =====================================================================
def check_results():
    global state, all_messages

    for entry in state.get("predictions", []):
        if entry.get("status") != "pending":
            continue

        target = entry.get("target")
        predicted_rank = entry.get("selected_prediction")
        message_id = entry.get("message_id")
        original_text = entry.get("message_text", "")
        latency = entry.get("latency", 100)

        if not predicted_rank or not message_id:
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
                continue

            game_data = parse_game_from_text(game_msg)
            if not game_data:
                continue

            rank_found = False
            actual_rank = None
            player_cards = game_data.get("player_cards", [])
            
            for card in player_cards:
                if card.get("rank") == predicted_rank:
                    rank_found = True
                    actual_rank = predicted_rank
                    break
            
            if not actual_rank and player_cards:
                actual_rank = player_cards[0].get("rank", "10")

            # ===========================================
            # СЛУЧАЙ 1: ПРОГНОЗ ЗАШЁЛ
            # ===========================================
            if rank_found:
                print(f"🎯 РАНГ {predicted_rank} НАЙДЕН в игре #N{game_to_check}!", flush=True)

                entry["status"] = "win"
                state["wins"] = state.get("wins", 0) + 1
                state["total_predictions"] = state.get("total_predictions", 0) + 1
                stats["total"] += 1
                stats["win"] += 1
                stats["ml_wins"] += 1
                stats["by_dogon"][i] = stats["by_dogon"].get(i, 0) + 1

                suffix = f"\n\n✅ <b>ЗАШЛО</b> на догоне {i}: #N{game_to_check}" if i > 0 else f"\n\n✅ <b>ЗАШЛО</b> в целевой игре: #N{game_to_check}"
                edit_message(message_id, original_text + suffix)
                entry["message_text"] = original_text + suffix

                save_json(STATE_FILE, state)
                save_history(state.get("predictions", []))
                return

            # ===========================================
            # СЛУЧАЙ 2: ПРОГНОЗ НЕ ЗАШЁЛ → УЧИМСЯ
            # ===========================================
            if i == max_games_to_check - 1:
                print(f"❌ Ранг {predicted_rank} НЕ НАЙДЕН за {max_games_to_check} игр", flush=True)

                entry["status"] = "lose"
                state["losses"] = state.get("losses", 0) + 1
                state["total_predictions"] = state.get("total_predictions", 0) + 1
                stats["total"] += 1
                stats["lose"] += 1
                stats["ml_losses"] += 1

                # 🔥 ОБУЧЕНИЕ НА ОШИБКЕ
                try:
                    features = extract_features_from_game(game_data, latency, target)
                    if features and ml_initialized:
                        feature_vector = []
                        for key in sorted(features.keys()):
                            feature_vector.append(features[key])
                        
                        X_new = np.array([feature_vector])
                        y_new = TARGET_RANKS.index(actual_rank if actual_rank else "10")
                        
                        if hasattr(ml_model, 'partial_fit'):
                            ml_model.partial_fit(X_new, [y_new])
                            print(f"✅ Мгновенное обучение: запомнил {actual_rank}")
                        
                        # Сохраняем игру в данные
                        game_record = {
                            "game_num": target,
                            "latency": latency,
                            "player_cards": game_data.get("player_cards", []),
                            "dealer_cards": game_data.get("dealer_cards", []),
                            "actual_rank": actual_rank,
                            "predicted_rank": predicted_rank,
                            "is_error": True
                        }
                        save_data(game_record)
                except Exception as e:
                    print(f"⚠️ Ошибка при дообучении: {e}", flush=True)

                suffix = f"\n\n❌ <b>НЕ ЗАШЛО</b> (проверено {max_games_to_check} игр)\n   Выпал: {actual_rank if actual_rank else 'неизвестно'}"
                edit_message(message_id, original_text + suffix)
                entry["message_text"] = original_text + suffix

                save_json(STATE_FILE, state)
                save_history(state.get("predictions", []))
                return

# =====================================================================
# ПРОГНОЗ (ML)
# =====================================================================
def get_prediction(latency, current_game_data):
    if not ml_initialized:
        print(f"⏳ ML модель не инициализирована", flush=True)
        return None, None, None
    
    if not current_game_data:
        print(f"⏳ Нет данных о текущей игре", flush=True)
        return None, None, None
    
    features = extract_features_from_game(current_game_data, latency, 0)
    if not features:
        print(f"⏳ Не удалось извлечь признаки", flush=True)
        return None, None, None
    
    predicted_rank, confidence = predict_ml(features)
    
    if predicted_rank and confidence is not None:
        print(f"📊 ML: предсказан ранг {predicted_rank} с уверенностью {confidence*100:.1f}%", flush=True)
        print(f"   Порог: {ML_CONFIDENCE_THRESHOLD*100:.0f}%", flush=True)
        
        if confidence >= ML_CONFIDENCE_THRESHOLD:
            print(f"✅ Уверенность {confidence*100:.1f}% >= {ML_CONFIDENCE_THRESHOLD*100:.0f}% → ДАЮ ПРОГНОЗ!", flush=True)
            return predicted_rank, confidence
        else:
            print(f"⏭️ Уверенность {confidence*100:.1f}% < {ML_CONFIDENCE_THRESHOLD*100:.0f}% → ПРОПУСКАЮ", flush=True)
            return None, None
    else:
        print(f"⏭️ ML не выдал прогноз", flush=True)
        return None, None

# =====================================================================
# PREDICTION SCHEDULER
# =====================================================================
def schedule_for_game(game_number):
    target = game_number + OFFSET

    for e in state.get("predictions", []):
        if e.get("target") == target and e.get("status") in ("scheduled", "pending"):
            return

    state["predictions"].append({
        "source": target - 1,
        "target": target,
        "selected_prediction": None,
        "latency": None,
        "confidence": 0.0,
        "status": "scheduled",
        "evaluated": False,
        "created": datetime.now(MOSCOW_TZ).isoformat(),
        "message_id": None,
        "message_text": "",
    })
    if len(state["predictions"]) > MAX_STATE_PREDICTIONS:
        state["predictions"] = state["predictions"][-MAX_STATE_PREDICTIONS:]
    save_json(STATE_FILE, state)

    print(f"📅 Запланировано: #{game_number} → #{target} (+{OFFSET})", flush=True)

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

        # ============================================================
        # 🔥 ПРОГНОЗ РАНГА ПО ЗАДЕРЖКЕ (БЕЗ МАСТЕЙ)
        # ============================================================
        predicted_rank, confidence = predict_rank_by_latency(latency)

        if predicted_rank is None or confidence < MIN_CONFIDENCE:
            print(f"⏭️ Уверенность {confidence:.1f}% < {MIN_CONFIDENCE:.0f}% — НЕ ДАЁМ", flush=True)
            entry["status"] = "skipped"
            entry["selected_prediction"] = None
            entry["confidence"] = confidence
            entry["latency"] = latency
            save_json(STATE_FILE, state)
            continue

        entry["selected_prediction"] = predicted_rank
        entry["latency"] = latency
        entry["confidence"] = confidence
        entry["status"] = "pending"

        # ============================================================
        # 🔥 ФОРМИРУЕМ СООБЩЕНИЕ ТОЛЬКО С РАНГОМ (БЕЗ МАСТЕЙ)
        # ============================================================
        msg = f"🔮 <b>RANK BOT (ML)</b>\n"
        msg += f"🃏 Ранг: <b>{predicted_rank}</b>\n"
        msg += f"🎯 Уверенность: {confidence:.1f}%\n"
        msg += f"🎯 Целевая игра: #N{target} (+{OFFSET})\n"
        msg += f"📈 Догон: {DOGON_GAMES - 1} игры\n"

        # Показываем только ранги (БЕЗ МАСТЕЙ)
        p = source.get("player_cards", [])
        d = source.get("dealer_cards", [])
        seq = []
        for prefix, cards in [("P", p[:3]), ("D", d[:3])]:
            for i, c in enumerate(cards, 1):
                rank = c.get("rank", "")
                if rank:
                    seq.append(f"{prefix}{i}:{rank}")
        if seq:
            msg += "📌 " + " ".join(seq) + "\n"

        msg += "⏰ " + datetime.now(MOSCOW_TZ).strftime("%H:%M:%S")

        # ============================================================
        # 🔥 ЛОГИРУЕМ ТОЛЬКО РАНГ (БЕЗ МАСТЕЙ)
        # ============================================================
        print(f"✅ ПРОГНОЗ: #{target} → {predicted_rank} | уверенность={confidence:.1f}%", flush=True)

        mid = send_message(msg)
        if mid:
            entry["message_id"] = mid
            entry["message_text"] = msg
            save_json(STATE_FILE, state)
            print(
                f"✅ ПРОГНОЗ ОТПРАВЛЕН: #{target} → {predicted_rank} (уверенность {confidence:.1f}%)",
                flush=True,
            )
        else:
            entry["status"] = "scheduled"
            save_json(STATE_FILE, state)

        entry["selected_prediction"] = predicted_rank
        entry["latency"] = latency
        entry["confidence"] = confidence
        entry["status"] = "pending"

        # Формируем сообщение
        msg = f"🔮 <b>RANK BOT (ML)</b>\n"
        msg += f"🃏 Ранг: <b>{predicted_rank}</b>\n"
        msg += f"🎯 Уверенность: {confidence*100:.1f}%\n"
        msg += f"🎯 Целевая игра: #N{target} (+{OFFSET})\n"
        msg += f"📈 Догон: {DOGON_GAMES - 1} игры\n"
        
        p = source.get("player_cards", [])
        d = source.get("dealer_cards", [])
        seq = []
        for prefix, cards in [("P", p[:3]), ("D", d[:3])]:
            for i, c in enumerate(cards, 1):
                if c.get("rank") and c.get("suit"):
                    seq.append(f"{prefix}{i}:{c['rank']}{c['suit']}")
        if seq:
            msg += "📌 " + " ".join(seq) + "\n"
        
        msg += "⏰ " + datetime.now(MOSCOW_TZ).strftime("%H:%M:%S")
        
        mid = send_message(msg)
        if mid:
            entry["message_id"] = mid
            entry["message_text"] = msg
            save_json(STATE_FILE, state)
            print(f"✅ ПРОГНОЗ ОТПРАВЛЕН: #{target} → {predicted_rank} (уверенность {confidence*100:.1f}%)", flush=True)
        else:
            entry["status"] = "scheduled"
            save_json(STATE_FILE, state)

# =====================================================================
# СБОР ДАННЫХ
# =====================================================================
def collect_game_data():
    """Собирает данные из активных игр"""
    active_games = get_active_games()
    if not active_games:
        return
    
    for game in active_games:
        game_id = str(game.get("id"))
        game_data, latency = get_game_data(game_id)
        
        if not game_data:
            continue
        
        # Парсим карты из API
        sc = game_data.get("Value", {}).get("SC", {})
        player_cards = []
        dealer_cards = []
        
        for item in sc.get("S", []):
            if item.get("Key") == "P1":
                try:
                    cards = json.loads(item.get("Value", "[]"))
                    player_cards = [
                        {"rank": RANKS.get(c.get("CV", 0), "?"), "suit": SUITS_NAMES.get(c.get("CS", 0), "?")}
                        for c in cards
                    ]
                except:
                    pass
            if item.get("Key") == "P2":
                try:
                    cards = json.loads(item.get("Value", "[]"))
                    dealer_cards = [
                        {"rank": RANKS.get(c.get("CV", 0), "?"), "suit": SUITS_NAMES.get(c.get("CS", 0), "?")}
                        for c in cards
                    ]
                except:
                    pass
        
        if player_cards:
            record = {
                "game_num": get_game_number(),
                "latency": latency if latency else 0,
                "player_cards": player_cards,
                "dealer_cards": dealer_cards,
                "timestamp": datetime.now(MOSCOW_TZ).isoformat()
            }
            save_data(record)
            print(f"📊 Собрана игра: {record['game_num']}, карт: {len(player_cards)}", flush=True)

# =====================================================================
# СТАТИСТИКА
# =====================================================================
def stats_text():
    total = state.get("total_predictions", 0)
    wins = state.get("wins", 0)
    losses = state.get("losses", 0)
    acc = f"{wins / total * 100:.1f}%" if total else "—"
    data_count = len(load_data())
    
    msg = (
        "📊 <b>СТАТИСТИКА (RANK BOT ML)</b>\n"
        f"⏰ {datetime.now(MOSCOW_TZ).strftime('%d.%m.%Y %H:%M:%S')}\n"
        "==============================\n"
        f"📊 Собрано игр: {data_count}/{MAX_RECORDS}\n"
        f"📈 Всего прогнозов: {total}\n"
        f"✅ Зашло: {wins}\n"
        f"❌ Не зашло: {losses}\n"
        f"🎯 Точность: {acc}\n"
        f"🧠 ML: {'✅ АКТИВНА' if ml_initialized else '⏳ ОЖИДАЕТ'}\n"
        f"🎯 Минимальный порог: {ML_CONFIDENCE_THRESHOLD*100:.0f}%"
    )
    return msg

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
    global all_messages, state, ml_initialized

    print("=" * 70, flush=True)
    print("🃏 RANK BOT (ML SELF-LEARNING)", flush=True)
    print("=" * 70, flush=True)

    # Загружаем модель при старте
    load_ml_model()
    
    # Пытаемся создать модель, если есть данные
    if not ml_initialized:
        data_count = len(load_data())
        if data_count >= MIN_TRAIN_SAMPLES:
            print(f"📊 Начинаю обучение на {data_count} играх...", flush=True)
            train_ml_model()
        else:
            print(f"⏳ Нужно собрать {MIN_TRAIN_SAMPLES} игр. Сейчас: {data_count}", flush=True)
    
    send_message(stats_text())

    offset = load_offset()
    last_stats = time.time()
    last_train_time = time.time()

    print("🚀 БОТ ГОТОВ.", flush=True)
    print(f"🎯 OFFSET: +{OFFSET}", flush=True)
    print(f"🎯 Минимальная уверенность: {ML_CONFIDENCE_THRESHOLD*100:.0f}%", flush=True)

    while True:
        try:
            now = time.time()

            # Сбор данных
            collect_game_data()
            
            # Проверка результатов
            check_results()
            
            # Обработка запланированных прогнозов
            process_scheduled()
            
            # Переобучение каждые 3 минуты
            if now - last_train_time > 180:
                data_count = len(load_data())
                if data_count >= MIN_TRAIN_SAMPLES:
                    print(f"🔄 ЗАПУСК ПЕРЕОБУЧЕНИЯ (всего игр: {data_count})...", flush=True)
                    train_ml_model()
                    last_train_time = now
                    gc.collect()

            # Статистика раз в час
            if now - last_stats > 3600:
                send_message(stats_text())
                last_stats = now

            # Обработка сообщений из канала
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

            time.sleep(CHECK_INTERVAL)

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