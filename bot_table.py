import os
import sys
import json
import re
import time
import traceback
from pathlib import Path
from datetime import datetime, timedelta
import requests
import pytz

# ================================================================
# ML BOT (DATASET FROM GITHUB)
# ================================================================

print("=" * 70, flush=True)
print("🃏 ML BOT (DATASET FROM GITHUB)", flush=True)
print("📌 Загрузка датасета с GitHub для обучения ML", flush=True)
print("=" * 70, flush=True)

# -------------------- ENV --------------------
BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("BOT_TOKEN_PROGNOZ")
CHANNEL_STATS = os.getenv("CHANNEL_STATS")
CHANNEL_PROGNOZ = os.getenv("CHANNEL_PROGNOZ")
LIVE_COOKIE = os.getenv("LIVE_COOKIE", "")

if not BOT_TOKEN or not CHANNEL_STATS or not CHANNEL_PROGNOZ:
    raise RuntimeError("Нужны BOT_TOKEN, CHANNEL_STATS и CHANNEL_PROGNOZ")

# -------------------- SETTINGS --------------------
MOSCOW_TZ = pytz.timezone("Europe/Moscow")
BASE_URL = os.getenv("BASE_URL", "https://1xlite-36553.pro")

OFFSET = int(os.getenv("PREDICT_OFFSET", "10"))
DOGON_GAMES = int(os.getenv("DOGON_GAMES", "4"))

MIN_CONFIDENCE = 0.60
MIN_TRAIN_SAMPLES = 50

STATE_DIR = Path(os.getenv("STATE_DIR", ".")).resolve()
STATE_DIR.mkdir(parents=True, exist_ok=True)

STATE_FILE = STATE_DIR / "hybrid_state.json"
OFFSET_FILE = STATE_DIR / "telegram_offset.txt"
MODEL_FILE = STATE_DIR / "hybrid_ml_models.joblib"

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
SUIT_TO_ID = {s: i for i, s in enumerate(SUITS)}
RANKS = ["6", "7", "8", "9", "10", "J", "Q", "K", "A"]
RANK_TO_ID = {r: i + 1 for i, r in enumerate(RANKS)}

# -------------------- SKLEARN --------------------
try:
    from sklearn.ensemble import RandomForestClassifier
    import joblib
    SKLEARN_OK = True
except Exception:
    SKLEARN_OK = False

print(f"📁 STATE_DIR: {STATE_DIR}", flush=True)
print(f"🤖 sklearn: {'OK' if SKLEARN_OK else 'НЕ УСТАНОВЛЕН'}", flush=True)
print(f"🎯 OFFSET: +{OFFSET}", flush=True)
print(f"🎯 Минимальная уверенность ML: {MIN_CONFIDENCE*100:.0f}%", flush=True)

# -------------------- STATE --------------------
def default_state():
    return {
        "predictions": [],
        "training_samples": [],
        "mode": "ML",
        "model_samples": 0,
        "last_model_train": 0,
        "ml_wins": 0,
        "ml_losses": 0,
        "total_predictions": 0,
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
MODELS = None
all_messages = []

# ================================================================
# СИНХРОНИЗАЦИЯ С GITHUB (АДАПТАЦИЯ МАССИВА)
# ================================================================
def sync_state_with_github():
    """
    Скачивает dataset с GitHub и преобразует массив игр в формат training_samples.
    """
    GITHUB_RAW_URL = "https://raw.githubusercontent.com/t1mwatson-bel/bac/main/hybrid_state.json"
    
    try:
        print("🔄 Загружаю dataset с GitHub...", flush=True)
        r = requests.get(GITHUB_RAW_URL, timeout=10)
        if r.status_code != 200:
            print(f"⚠️ GitHub вернул {r.status_code}, dataset не загружен", flush=True)
            return False
        
        github_data = r.json()
        
        if not isinstance(github_data, list):
            print("⚠️ Неожиданный формат данных с GitHub (ожидался массив)", flush=True)
            return False
        
        print(f"📊 Загружено {len(github_data)} записей с GitHub", flush=True)
        
        new_samples = []
        for entry in github_data:
            if any("?" in str(card) for card in entry.get("cards", [])):
                continue
            
            source_game = {
                "player_cards": [],
                "dealer_cards": []
            }
            
            cards = entry.get("cards", [])
            for card in cards[:2]:
                if card and len(card) >= 2:
                    source_game["player_cards"].append({
                        "rank": card[:-1],
                        "suit": card[-1]
                    })
            for card in cards[2:]:
                if card and len(card) >= 2:
                    source_game["dealer_cards"].append({
                        "rank": card[:-1],
                        "suit": card[-1]
                    })
            
            target_labels = [0, 0, 0, 0]
            for card in cards:
                if card and len(card) >= 2:
                    suit = card[-1]
                    if suit in SUITS:
                        target_labels[SUITS.index(suit)] = 1
            
            if any(target_labels):
                new_samples.append({
                    "x": make_features(source_game, entry.get("latency_ms", 100)),
                    "y": target_labels
                })
        
        if new_samples:
            existing_count = len(state.get("training_samples", []))
            state["training_samples"].extend(new_samples)
            added = len(state["training_samples"]) - existing_count
            print(f"✅ Добавлено {added} новых обучающих примеров из GitHub", flush=True)
            save_json(STATE_FILE, state)
        
        return True
        
    except Exception as e:
        print(f"⚠️ Ошибка загрузки dataset с GitHub: {e}", flush=True)
        traceback.print_exc()
        return False

# -------------------- TELEGRAM --------------------
def telegram_get_updates(offset):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    try:
        r = requests.get(url, params={"offset": offset, "timeout": 30}, timeout=35)
        if r.status_code != 200:
            return {}
        return r.json()
    except Exception:
        return {}

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
    except Exception:
        pass
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
    except Exception:
        return False

# -------------------- PARSING --------------------
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
    except Exception:
        return None

def is_finished_game(text):
    return "✅" in text or "🔰" in text

# -------------------- LIVE API --------------------
def get_active_games():
    url = (
        f"{BASE_URL}/service-api/main-live-feed/v3/games1x2"
        "?cfView=3&count=40&fcountry=1&gr=415&grMode=4&lng=ru&ref=7"
        "&selectedMs=1.146.2092323,2.146.2092323,10.146.2092323"
    )
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.status_code != 200:
            return []
        data = r.json()
        games = data.get("Value", []) if isinstance(data, dict) else data
        return [
            g for g in games
            if g.get("liga", {}).get("id") == 2092323 and g.get("id")
        ]
    except Exception:
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
    except Exception:
        pass
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

# -------------------- FEATURES / ML --------------------
def card_features(card):
    if not card:
        return [0, 0]
    return [RANK_TO_ID.get(card.get("rank"), 0), SUIT_TO_ID.get(card.get("suit"), -1) + 1]

def make_features(source_game, latency):
    p = source_game.get("player_cards", [])
    d = source_game.get("dealer_cards", [])

    cards = []
    for i in range(3):
        cards.extend(card_features(p[i] if i < len(p) else None))
    for i in range(3):
        cards.extend(card_features(d[i] if i < len(d) else None))

    suit_counts_p = [0] * 4
    suit_counts_d = [0] * 4
    for c in p:
        if c.get("suit") in SUIT_TO_ID:
            suit_counts_p[SUIT_TO_ID[c["suit"]]] += 1
    for c in d:
        if c.get("suit") in SUIT_TO_ID:
            suit_counts_d[SUIT_TO_ID[c["suit"]]] += 1

    rank_counts_p = [0] * 9
    for c in p:
        if c.get("rank") in RANK_TO_ID:
            rank_counts_p[RANK_TO_ID[c["rank"]] - 1] += 1

    latency_bin = int(max(0, min(300, latency)) // 2)

    return cards + suit_counts_p + suit_counts_d + rank_counts_p + [latency_bin, round(latency, 1)]

def target_labels(target_game):
    suits_present = {c.get("suit") for c in target_game.get("player_cards", [])}
    return [1 if s in suits_present else 0 for s in SUITS]

# -------------------- ОБУЧЕНИЕ ML --------------------
def train_models(force=False):
    global MODELS
    if not SKLEARN_OK:
        print("❌ sklearn не установлен", flush=True)
        return False

    samples = state.get("training_samples", [])
    sample_count = len(samples)
    
    print(f"📊 Образцов для обучения: {sample_count}", flush=True)
    
    if sample_count < MIN_TRAIN_SAMPLES:
        print(f"⏳ Нужно минимум {MIN_TRAIN_SAMPLES} образцов (есть {sample_count})", flush=True)
        return False

    try:
        X = []
        y_all = []
        
        for s in samples:
            x = s.get("x")
            y = s.get("y")
            if x is None or y is None:
                continue
            X.append(x)
            y_all.append(y)
        
        if len(X) < MIN_TRAIN_SAMPLES:
            print(f"⚠️ После фильтрации осталось {len(X)} образцов", flush=True)
            return False
        
        print(f"🧠 Обучаю ML на {len(X)} образцах...", flush=True)
        
        models = []
        for suit_idx in range(4):
            y = [yy[suit_idx] for yy in y_all]
            if len(set(y)) < 2:
                print(f"⚠️ Масть {SUITS[suit_idx]}: недостаточно классов", flush=True)
                models.append(None)
                continue
            
            model = RandomForestClassifier(
                n_estimators=100,
                max_depth=8,
                min_samples_leaf=5,
                random_state=42 + suit_idx,
                n_jobs=-1,
            )
            model.fit(X, y)
            models.append(model)
            print(f"✅ Масть {SUITS[suit_idx]}: обучена", flush=True)

        MODELS = models
        state["last_model_train"] = sample_count
        state["model_samples"] = sample_count

        try:
            joblib.dump({"models": MODELS, "samples": sample_count}, MODEL_FILE)
            print(f"💾 Модель сохранена: {MODEL_FILE}", flush=True)
        except Exception as e:
            print(f"⚠️ Не удалось сохранить ML-модель: {e}", flush=True)

        print(f"🤖 ML ОБУЧЕН. Образцов: {sample_count}", flush=True)
        return True
        
    except Exception as e:
        print(f"❌ Ошибка обучения ML: {e}", flush=True)
        traceback.print_exc()
        return False

def load_models():
    global MODELS
    if not SKLEARN_OK or not MODEL_FILE.exists():
        print("📁 Файл модели не найден, будет создан при обучении", flush=True)
        return
    
    try:
        obj = joblib.load(MODEL_FILE)
        MODELS = obj.get("models")
        samples = obj.get("samples", 0)
        print(f"🤖 ML-модель загружена из {MODEL_FILE} ({samples} образцов)", flush=True)
    except Exception as e:
        print(f"⚠️ Не удалось загрузить ML-модель: {e}", flush=True)

def ensure_model_trained():
    if not SKLEARN_OK:
        return False
    
    samples = len(state.get("training_samples", []))
    if samples < MIN_TRAIN_SAMPLES:
        print(f"⏳ Нужно минимум {MIN_TRAIN_SAMPLES} образцов (есть {samples})", flush=True)
        return False
    
    if MODELS is not None:
        print("✅ Модель уже загружена", flush=True)
        return True
    
    print("🧠 Обучаю модель...", flush=True)
    return train_models(force=True)

def ml_prediction_with_confidence(source_game, latency):
    if MODELS is None:
        print("🤖 ML: модель не загружена", flush=True)
        return None, 0.0

    try:
        x = [make_features(source_game, latency)]
        
        probs = []
        for idx, model in enumerate(MODELS):
            if model is None:
                probs.append(0.0)
                continue
            try:
                p = model.predict_proba(x)[0]
                classes = list(model.classes_)
                if 1 in classes:
                    prob = float(p[classes.index(1)])
                else:
                    prob = 0.0
                probs.append(prob)
            except Exception:
                probs.append(0.0)

        if not probs or max(probs) <= 0:
            print("🤖 ML: все вероятности = 0", flush=True)
            return None, 0.0

        max_prob = max(probs)
        idx = max(range(4), key=lambda i: probs[i])
        result = SUITS[idx]
        
        print(
            f"🤖 ML: {result} с уверенностью {max_prob*100:.1f}% | " +
            " ".join(f"{SUITS[i]}={probs[i]*100:.1f}%" for i in range(4)),
            flush=True,
        )
        
        if max_prob < MIN_CONFIDENCE:
            print(f"⏭️ Уверенность {max_prob*100:.1f}% < {MIN_CONFIDENCE*100:.0f}% — прогноз НЕ ДАЁМ", flush=True)
            return None, max_prob
        
        return result, max_prob
        
    except Exception as e:
        print(f"❌ Ошибка ML прогноза: {e}", flush=True)
        return None, 0.0

# -------------------- GAME STORAGE --------------------
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

# -------------------- ОЧИСТКА ЗАВИСШИХ ПРОГНОЗОВ --------------------
def cleanup_stuck_predictions():
    """Удаляет прогнозы, которые висят в scheduled больше 5 минут"""
    now = datetime.now(MOSCOW_TZ)
    to_remove = []
    
    for i, entry in enumerate(state.get("predictions", [])):
        if entry.get("status") == "scheduled":
            created = entry.get("created")
            if created:
                try:
                    created_time = datetime.fromisoformat(created)
                    if (now - created_time).total_seconds() > 300:  # 5 минут
                        to_remove.append(i)
                        print(f"⏰ Удаляю зависший прогноз #{entry.get('target')} (старше 5 минут)", flush=True)
                except:
                    pass
    
    if to_remove:
        predictions = state.get("predictions", [])
        for i in sorted(to_remove, reverse=True):
            if i < len(predictions):
                predictions.pop(i)
        state["predictions"] = predictions
        save_json(STATE_FILE, state)

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
                continue

            game_data = parse_game_from_text(game_msg)
            if not game_data:
                continue

            suit_found = False
            player_cards = game_data.get("player_cards", [])
            for card in player_cards:
                if card.get("suit") == predicted_suit:
                    suit_found = True
                    break

            if suit_found:
                print(f"🎯 МАСТЬ {predicted_suit} НАЙДЕНА в игре #N{game_to_check}!", flush=True)

                entry["selected_result"] = True
                entry["ml_result"] = True
                entry["evaluated"] = True
                entry["result_game"] = game_to_check
                entry["dogon"] = i
                entry["status"] = "win"

                state["ml_wins"] = state.get("ml_wins", 0) + 1
                state["total_predictions"] = state.get("total_predictions", 0) + 1

                suffix = f"\n\n✅ <b>ЗАШЛО</b> в игре #N{game_to_check}" if i == 0 else f"\n\n✅ <b>ЗАШЛО</b> на догоне {i}: #N{game_to_check}"
                edit_message(message_id, original_text + suffix)
                entry["message_text"] = original_text + suffix

                source = find_game(entry.get("source"))
                latency = entry.get("latency")
                if source and latency is not None:
                    target_game_data = parse_game_from_text(game_msg)
                    if target_game_data:
                        state["training_samples"].append({
                            "x": make_features(source, float(latency)),
                            "y": target_labels(target_game_data),
                        })

                if len(state["training_samples"]) > 5000:
                    state["training_samples"] = state["training_samples"][-5000:]

                save_json(STATE_FILE, state)
                return

            if i == max_games_to_check - 1:
                print(f"❌ Масть {predicted_suit} НЕ НАЙДЕНА за {max_games_to_check} игр", flush=True)

                entry["selected_result"] = False
                entry["ml_result"] = False
                entry["evaluated"] = True
                entry["status"] = "lose"

                state["ml_losses"] = state.get("ml_losses", 0) + 1
                state["total_predictions"] = state.get("total_predictions", 0) + 1

                suffix = f"\n\n❌ <b>НЕ ЗАШЛО</b> (проверено {max_games_to_check} игр)"
                edit_message(message_id, original_text + suffix)
                entry["message_text"] = original_text + suffix

                save_json(STATE_FILE, state)
                return

# -------------------- PREDICTION SCHEDULER --------------------
def schedule_for_game(game_number):
    target = game_number + OFFSET

    for e in state.get("predictions", []):
        if e.get("target") == target and e.get("status") in ("scheduled", "pending"):
            return

    source = target - 1

    state["predictions"].append({
        "source": source,
        "target": target,
        "ml_prediction": None,
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

        if MODELS is None:
            print("⏳ ML модель не загружена, ждём...", flush=True)
            continue

        prediction, confidence = ml_prediction_with_confidence(source, latency)
        
        if prediction is None:
            print(f"⏭️ Прогноз НЕ ДАН (уверенность {confidence*100:.1f}% < {MIN_CONFIDENCE*100:.0f}%)", flush=True)
            entry["status"] = "skipped"
            entry["ml_prediction"] = None
            entry["selected_prediction"] = None
            entry["confidence"] = confidence
            entry["latency"] = latency
            save_json(STATE_FILE, state)
            continue

        entry["ml_prediction"] = prediction
        entry["selected_prediction"] = prediction
        entry["latency"] = latency
        entry["confidence"] = confidence
        entry["status"] = "pending"

        msg = prediction_text(entry, prediction, source, confidence)
        mid = send_message(msg)

        if mid:
            entry["message_id"] = mid
            entry["message_text"] = msg
            save_json(STATE_FILE, state)

            print(
                f"✅ ПРОГНОЗ: #{target} → {prediction} | "
                f"уверенность={confidence*100:.1f}%",
                flush=True,
            )
        else:
            entry["status"] = "scheduled"
            save_json(STATE_FILE, state)

def prediction_text(entry, prediction, source_game, confidence):
    msg = "🔮 <b>ML 21 CLASSIC</b>\n"
    msg += f"🃏 Масть: {prediction}\n"
    msg += f"🎯 Уверенность: {confidence*100:.1f}%\n"
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

# -------------------- STATS --------------------
def stats_text():
    samples = len(state.get("training_samples", []))
    total = state.get("total_predictions", 0)
    mw = state.get("ml_wins", 0)
    ml = state.get("ml_losses", 0)

    mtot = mw + ml
    m_acc = f"{mw / mtot * 100:.1f}%" if mtot else "—"

    return (
        "📊 <b>СТАТИСТИКА (ML ONLY)</b>\n"
        f"⏰ {datetime.now(MOSCOW_TZ).strftime('%d.%m.%Y %H:%M:%S')}\n"
        "==============================\n"
        f"📈 Всего прогнозов: {total}\n"
        f"🧠 Обучающих примеров: {samples}\n"
        f"🤖 ML: {mw}✅ / {ml}❌ ({m_acc})\n"
        f"🎯 Минимальная уверенность: {MIN_CONFIDENCE*100:.0f}%"
    )

# -------------------- OFFSET --------------------
def load_offset():
    try:
        return int(OFFSET_FILE.read_text().strip())
    except Exception:
        return 0

def save_offset(v):
    OFFSET_FILE.write_text(str(v), encoding="utf-8")

# -------------------- MAIN --------------------
def main():
    global all_messages, state

    print("=" * 70, flush=True)
    print("🃏 ML BOT (DATASET FROM GITHUB)", flush=True)
    print("=" * 70, flush=True)

    # Синхронизация с GitHub
    sync_state_with_github()

    load_models()
    
    if SKLEARN_OK:
        ensure_model_trained()

    # Очистка зависших прогнозов при старте
    cleanup_stuck_predictions()

    send_message(stats_text())

    offset = load_offset()
    last_stats = time.time()
    last_cleanup = time.time()

    print("🚀 БОТ ГОТОВ.", flush=True)

    while True:
        try:
            now = time.time()

            # Очистка зависших прогнозов каждые 60 секунд
            if now - last_cleanup > 60:
                cleanup_stuck_predictions()
                last_cleanup = now

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