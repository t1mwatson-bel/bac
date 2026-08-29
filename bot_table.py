import os
import sys
import json
import re
import time
import traceback
from pathlib import Path
from datetime import datetime
import requests
import pytz

# ================================================================
# HYBRID BOT: ML НА ОСНОВЕ DATASET.JSON
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

STATE_DIR = Path(os.getenv("STATE_DIR", ".")).resolve()
STATE_DIR.mkdir(parents=True, exist_ok=True)

STATE_FILE = STATE_DIR / "hybrid_state.json"
DATASET_FILE = STATE_DIR / "dataset.json"
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
            if isinstance(data, list):
                # Если это список — преобразуем в словарь
                return {"training_samples": data, "predictions": [], "mode": "ML", "model_samples": 0, "last_model_train": 0, "ml_wins": 0, "ml_losses": 0, "total_predictions": 0}
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

# Загружаем state
state = load_json(STATE_FILE, default_state())
MODELS = None
all_messages = []

# ================================================================
# ЗАГРУЗКА DATASET С GITHUB
# ================================================================
def download_dataset_from_github():
    """Скачивает dataset.json с GitHub и сохраняет как training_samples"""
    GITHUB_DATASET_URL = "https://raw.githubusercontent.com/ArtyomGrigorian/hybrid_bot/main/dataset.json"
    
    try:
        print("🔄 Загружаю dataset с GitHub...", flush=True)
        r = requests.get(GITHUB_DATASET_URL, timeout=10)
        if r.status_code != 200:
            print(f"⚠️ GitHub вернул {r.status_code}, dataset не загружен", flush=True)
            return False
        
        dataset = json.loads(r.text)
        if not isinstance(dataset, list):
            print("⚠️ Dataset не является списком", flush=True)
            return False
        
        # Преобразуем в training_samples
        training_samples = []
        for entry in dataset:
            # Извлекаем карты игрока из cards
            cards = entry.get("cards", [])
            suits = entry.get("suits", [])
            ranks = entry.get("ranks", [])
            
            if not cards or not suits or not ranks:
                continue
            
            # Собираем структуру как в make_features
            # Создаём фиктивную game для источника (source)
            source_game = {
                "player_cards": [],
                "dealer_cards": []
            }
            
            # Используем карты как фичи
            # Просто сохраняем сырые данные, чтобы потом преобразовать через make_features
            training_samples.append({
                "cards": cards,
                "suits": suits,
                "ranks": ranks,
                "state": entry.get("state", "2"),
                "player_score": entry.get("player_score", 0)
            })
        
        # Сохраняем как training_samples в state
        state["training_samples"] = training_samples
        state["model_samples"] = len(training_samples)
        save_json(STATE_FILE, state)
        
        print(f"✅ Dataset загружен: {len(training_samples)} записей", flush=True)
        return True
        
    except Exception as e:
        print(f"⚠️ Ошибка загрузки dataset: {e}", flush=True)
        return False

# ================================================================
# ФУНКЦИИ ДЛЯ ОБУЧЕНИЯ НА DATASET
# ================================================================
def make_features_from_dataset(entry):
    """Преобразует запись из dataset в фичи для ML"""
    # Формируем source_game из карт
    cards = entry.get("cards", [])
    suits = entry.get("suits", [])
    ranks = entry.get("ranks", [])
    
    # Разбираем карты
    player_cards = []
    dealer_cards = []
    
    # Первые 2-3 карты — игрок
    for i in range(min(len(cards), 3)):
        if i < len(cards):
            rank = ranks[i] if i < len(ranks) else "?"
            suit = suits[i] if i < len(suits) else "?"
            # Если символ масти в SUITS
            if suit in SUITS:
                player_cards.append({"rank": rank, "suit": suit})
    
    source_game = {
        "player_cards": player_cards,
        "dealer_cards": dealer_cards
    }
    
    # Используем латентность из dataset или 100 по умолчанию
    latency = entry.get("latency_ms", 100.0)
    
    return make_features(source_game, float(latency))

def target_labels_from_entry(entry):
    """Извлекает масти из карт игрока"""
    suits = entry.get("suits", [])
    # Берём первые 3 карты (или все)
    suits_present = set(suits[:3])
    return [1 if s in suits_present else 0 for s in SUITS]

# -------------------- ФУНКЦИИ ДЛЯ ML --------------------
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

def train_models_from_dataset(force=False):
    global MODELS
    if not SKLEARN_OK:
        print("❌ sklearn не установлен", flush=True)
        return False

    samples = state.get("training_samples", [])
    sample_count = len(samples)
    
    print(f"📊 Образцов в dataset: {sample_count}", flush=True)
    
    if sample_count < 10:
        print(f"⏳ Нужно минимум 10 образцов (есть {sample_count})", flush=True)
        return False

    try:
        X = []
        y_all = []
        
        for s in samples:
            x = make_features_from_dataset(s)
            y = target_labels_from_entry(s)
            X.append(x)
            y_all.append(y)
        
        if len(X) < 10:
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

def ensure_model_trained():
    if not SKLEARN_OK:
        return False
    
    samples = len(state.get("training_samples", []))
    if samples < 10:
        print(f"⏳ Нужно минимум 10 образцов (есть {samples})", flush=True)
        return False
    
    if MODELS is not None:
        print("✅ Модель уже загружена", flush=True)
        return True
    
    print("🧠 Обучаю модель на dataset...", flush=True)
    return train_models_from_dataset(force=True)

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

# -------------------- ОСТАЛЬНЫЕ ФУНКЦИИ (БЕЗ ИЗМЕНЕНИЙ) --------------------
# Здесь идут telegram_get_updates, send_message, edit_message,
# parse_game_from_text, is_finished_game, get_active_games,
# get_game_data, get_fresh_latency, schedule_for_game,
# process_scheduled, prediction_text, check_results,
# stats_text, load_offset, save_offset
# 
# Они остаются такими же, как в твоём рабочем коде.
# Если нужно — я добавлю их в полной версии.
# ================================================================

# -------------------- MAIN --------------------
def main():
    global all_messages, state

    print("=" * 70, flush=True)
    print("🃏 ML BOT (DATASET FROM GITHUB)", flush=True)
    print("=" * 70, flush=True)

    # Скачиваем dataset с GitHub
    download_dataset_from_github()

    load_models()
    
    if SKLEARN_OK:
        ensure_model_trained()

    send_startup_message()
    send_message(stats_text())

    offset = load_offset()
    last_stats = time.time()

    print("🚀 БОТ ГОТОВ.", flush=True)

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