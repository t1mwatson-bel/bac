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
# HYBRID BOT: ТВОИ ПРАВИЛА + ML
# ================================================================

print("=" * 70, flush=True)
print("🃏 HYBRID RULES + ML BOT", flush=True)
print("📌 Сначала: ТВОЯ МЕТОДИКА | Потом: ML, если он лучше", flush=True)
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

MIN_TRAIN_SAMPLES = int(os.getenv("ML_MIN_SAMPLES", "200"))
RECENT_COMPARE = int(os.getenv("ML_RECENT_COMPARE", "50"))
SWITCH_MARGIN = float(os.getenv("ML_SWITCH_MARGIN", "0.02"))
MODEL_REFRESH_EVERY = int(os.getenv("ML_REFRESH_EVERY", "20"))

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
print(f"🤖 sklearn: {'OK' if SKLEARN_OK else 'НЕ УСТАНОВЛЕН — работают правила'}", flush=True)
print(f"🎯 OFFSET: +{OFFSET}", flush=True)
print(f"🧠 ML стартует после: {MIN_TRAIN_SAMPLES} размеченных прогнозов", flush=True)

# -------------------- STATE --------------------
def default_state():
    return {
        "predictions": [],
        "training_samples": [],
        "mode": "RULES",
        "model_samples": 0,
        "last_model_train": 0,
        "rule_wins": 0,
        "rule_losses": 0,
        "ml_wins": 0,
        "ml_losses": 0,
        "total_predictions": 0,
        "switches": 0,
    }

def load_json(path, default):
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
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

# -------------------- TELEGRAM --------------------
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
        print(f"❌ sendMessage: {r.status_code} {r.text[:300]}", flush=True)
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
        if r.status_code == 200:
            return True
        print(f"❌ editMessageText: {r.status_code} {r.text[:300]}", flush=True)
    except Exception as e:
        print(f"❌ editMessageText: {e}", flush=True)
    return False

def send_startup_message():
    text = (
        "🚀 <b>HYBRID RULES + ML</b>\n"
        "🃏 В начале работает твоя методика\n"
        f"🎯 Цель: +{OFFSET} игр\n"
        f"🤖 ML обучается параллельно, старт после {MIN_TRAIN_SAMPLES} результатов\n"
        "🔄 ML станет основным только если будет лучше правил\n"
        "🛡️ Если ML ухудшится — автоматический возврат к правилам"
    )
    send_message(text)

def send_ml_status():
    """Отправляет в канал статус ML при запуске"""
    samples = len(state.get("training_samples", []))
    total = state.get("total_predictions", 0)
    mode = state.get("mode", "RULES")
    model_samples = state.get("model_samples", 0)
    
    msg = "🧠 <b>ML СТАТУС</b>\n"
    msg += f"📊 Обучающих примеров: {samples}/{MIN_TRAIN_SAMPLES}\n"
    msg += f"📈 Всего прогнозов: {total}\n"
    msg += f"🔀 Текущий метод: {'🤖 ML' if mode == 'ML' else '📌 Правила'}\n"
    
    if MODELS is not None:
        msg += f"✅ ML модель: ОБУЧЕНА ({model_samples} примеров)\n"
    elif samples >= MIN_TRAIN_SAMPLES:
        msg += "⚠️ ML модель: НЕ ОБУЧЕНА (попытка обучения...)\n"
    else:
        msg += f"⏳ ML модель: ОЖИДАЕТ ({samples}/{MIN_TRAIN_SAMPLES})\n"
    
    # Проверяем sklearn
    if not SKLEARN_OK:
        msg += "\n❌ <b>ПРОБЛЕМА:</b> sklearn не установлен!\n"
        msg += "💡 Установи: pip install scikit-learn joblib\n"
    elif samples >= MIN_TRAIN_SAMPLES and MODELS is None:
        msg += "\n⚠️ <b>ВНИМАНИЕ:</b> данных достаточно, но модель не обучена.\n"
        msg += "💡 Возможные причины:\n"
        msg += "  - Все прогнозы одной масти (нужно разнообразие)\n"
        msg += "  - Ошибка в обучении (смотри логи)\n"
        msg += "  - Не хватает памяти\n"
    elif MODELS is not None:
        rw = state.get("rule_wins", 0)
        rl = state.get("rule_losses", 0)
        mw = state.get("ml_wins", 0)
        ml = state.get("ml_losses", 0)
        rtot = rw + rl
        mtot = mw + ml
        r_acc = f"{rw / rtot * 100:.1f}%" if rtot else "—"
        m_acc = f"{mw / mtot * 100:.1f}%" if mtot else "—"
        msg += f"\n📊 Правила: {rw}✅ / {rl}❌ ({r_acc})\n"
        msg += f"🤖 ML: {mw}✅ / {ml}❌ ({m_acc})\n"
    
    send_message(msg)

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
    except Exception as e:
        print(f"❌ parse_game: {e}", flush=True)
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

# -------------------- ТВОИ ПРАВИЛА (С ПРОЦЕНТАМИ) --------------------
LATENCY_PROBS = {
    (93, 95): {"♣️": 28.3, "♥️": 24.5, "♠️": 23.5, "♦️": 23.7},
    (95, 97): {"♠️": 29.1, "♥️": 28.7, "♣️": 21.5, "♦️": 20.7},
    (97, 99): {"♦️": 26.7, "♠️": 25.9, "♣️": 24.5, "♥️": 22.9},
    (99, 101): {"♥️": 27.4, "♦️": 25.3, "♠️": 24.2, "♣️": 23.1},
    (101, 103): {"♣️": 26.5, "♠️": 25.1, "♥️": 24.8, "♦️": 23.6},
    (103, 105): {"♥️": 27.8, "♦️": 24.6, "♣️": 24.2, "♠️": 23.4},
    (105, 200): {"♠️": 27.6, "♣️": 25.9, "♥️": 24.3, "♦️": 22.2},
}

def get_suit_probs(latency):
    for (low, high), probs in LATENCY_PROBS.items():
        if low <= latency < high:
            return probs
    return None

def predict_suit_by_latency(latency):
    probs = get_suit_probs(latency)
    if not probs:
        return None
    sorted_probs = sorted(probs.items(), key=lambda x: x[1], reverse=True)
    return sorted_probs[0][0]

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

def rule_prediction(source_game, latency):
    base = predict_suit_by_latency(latency)
    if base is None:
        return None

    p = source_game.get("player_cards", [])
    d = source_game.get("dealer_cards", [])

    p1 = p[0] if len(p) > 0 else None
    p3 = p[1] if len(p) > 1 else None
    p2 = d[0] if len(d) > 0 else None

    return refine_by_sequence(p1, p2, p3, base, latency)

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

def train_models(force=False):
    global MODELS
    if not SKLEARN_OK:
        return False

    samples = state.get("training_samples", [])
    if len(samples) < MIN_TRAIN_SAMPLES:
        return False

    last = int(state.get("last_model_train", 0))
    if not force and len(samples) - last < MODEL_REFRESH_EVERY:
        return MODELS is not None

    try:
        X = [s["x"] for s in samples]
        models = []
        for suit_idx in range(4):
            y = [s["y"][suit_idx] for s in samples]
            if len(set(y)) < 2:
                models.append(None)
                continue
            model = RandomForestClassifier(
                n_estimators=300,
                max_depth=10,
                min_samples_leaf=3,
                random_state=42 + suit_idx,
                n_jobs=-1,
                class_weight="balanced_subsample",
            )
            model.fit(X, y)
            models.append(model)

        MODELS = models
        state["last_model_train"] = len(samples)
        state["model_samples"] = len(samples)

        try:
            joblib.dump({"models": MODELS, "samples": len(samples)}, MODEL_FILE)
        except Exception as e:
            print(f"⚠️ Не удалось сохранить ML-модель: {e}", flush=True)

        print(f"🤖 ML ОБУЧЕН. Образцов: {len(samples)}", flush=True)
        return True
    except Exception as e:
        print(f"❌ Ошибка обучения ML: {e}", flush=True)
        traceback.print_exc()
        return False

def load_models():
    global MODELS
    if not SKLEARN_OK or not MODEL_FILE.exists():
        return
    try:
        obj = joblib.load(MODEL_FILE)
        MODELS = obj.get("models")
        print(f"🤖 ML-модель загружена из {MODEL_FILE}", flush=True)
    except Exception as e:
        print(f"⚠️ Не удалось загрузить ML-модель: {e}", flush=True)

def ml_prediction(source_game, latency):
    if MODELS is None:
        return None

    x = [make_features(source_game, latency)]
    probs = []
    for model in MODELS:
        if model is None:
            probs.append(0.0)
            continue
        try:
            p = model.predict_proba(x)[0]
            if 1 in list(model.classes_):
                probs.append(float(p[list(model.classes_).index(1)]))
            else:
                probs.append(0.0)
        except Exception:
            probs.append(0.0)

    if not probs or max(probs) <= 0:
        return None

    idx = max(range(4), key=lambda i: probs[i])
    print(
        "🤖 ML вероятности: " +
        " ".join(f"{SUITS[i]}={probs[i]*100:.1f}%" for i in range(4)),
        flush=True,
    )
    return SUITS[idx]

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

# -------------------- UPDATE MODE --------------------
def update_mode():
    preds = state.get("predictions", [])
    if len(preds) < RECENT_COMPARE:
        return

    recent = preds[-RECENT_COMPARE:]

    rule_wins = sum(1 for p in recent if p.get("rule_result") is True)
    rule_losses = sum(1 for p in recent if p.get("rule_result") is False)
    ml_wins = sum(1 for p in recent if p.get("ml_result") is True)
    ml_losses = sum(1 for p in recent if p.get("ml_result") is False)

    rule_total = rule_wins + rule_losses
    ml_total = ml_wins + ml_losses

    if rule_total == 0 or ml_total == 0:
        return

    rule_acc = rule_wins / rule_total
    ml_acc = ml_wins / ml_total

    current_mode = state.get("mode", "RULES")

    if ml_acc > rule_acc + SWITCH_MARGIN and current_mode != "ML":
        state["mode"] = "ML"
        state["switches"] = state.get("switches", 0) + 1
        print(f"🔄 ПЕРЕКЛЮЧЕНИЕ НА ML: {ml_acc*100:.1f}% > {rule_acc*100:.1f}%", flush=True)
        send_message(
            f"🔄 <b>ПЕРЕКЛЮЧЕНИЕ НА ML</b>\n"
            f"ML: {ml_acc*100:.1f}% vs Правила: {rule_acc*100:.1f}%"
        )
    elif rule_acc > ml_acc + SWITCH_MARGIN and current_mode != "RULES":
        state["mode"] = "RULES"
        state["switches"] = state.get("switches", 0) + 1
        print(f"🔄 ВОЗВРАТ К ПРАВИЛАМ: {rule_acc*100:.1f}% > {ml_acc*100:.1f}%", flush=True)
        send_message(
            f"🔄 <b>ВОЗВРАТ К ПРАВИЛАМ</b>\n"
            f"Правила: {rule_acc*100:.1f}% vs ML: {ml_acc*100:.1f}%"
        )

    save_json(STATE_FILE, state)

# =====================================================================
# ПРОВЕРКА РЕЗУЛЬТАТА (ИЗ ТВОЕГО РАБОЧЕГО КОДА)
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
                print(f"⏳ Ждём игру #N{game_to_check} для проверки масти {predicted_suit}", flush=True)
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
                dogon_number = i

                entry["selected_result"] = True
                entry["rule_result"] = True if entry.get("rule_prediction") == predicted_suit else False
                entry["ml_result"] = True if entry.get("ml_prediction") == predicted_suit else False
                entry["evaluated"] = True
                entry["result_game"] = game_to_check
                entry["dogon"] = dogon_number
                entry["status"] = "win"

                if entry.get("rule_prediction"):
                    state["rule_wins" if entry["rule_result"] else "rule_losses"] += 1
                if entry.get("ml_prediction"):
                    state["ml_wins" if entry["ml_result"] else "ml_losses"] += 1
                state["total_predictions"] += 1

                if dogon_number == 0:
                    suffix = f"\n\n✅ <b>ЗАШЛО</b> в целевой игре: #N{game_to_check}"
                else:
                    suffix = f"\n\n✅ <b>ЗАШЛО</b> на догоне {dogon_number}: #N{game_to_check}"

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

                update_mode()
                save_json(STATE_FILE, state)
                return

            if i == max_games_to_check - 1:
                print(f"❌ Масть {predicted_suit} НЕ НАЙДЕНА за {max_games_to_check} игр", flush=True)

                entry["selected_result"] = False
                entry["rule_result"] = False if entry.get("rule_prediction") else None
                entry["ml_result"] = False if entry.get("ml_prediction") else None
                entry["evaluated"] = True
                entry["status"] = "lose"

                if entry.get("rule_prediction"):
                    state["rule_losses"] += 1
                if entry.get("ml_prediction"):
                    state["ml_losses"] += 1
                state["total_predictions"] += 1

                suffix = f"\n\n❌ <b>НЕ ЗАШЛО</b> (проверено {max_games_to_check} игр)"
                edit_message(message_id, original_text + suffix)
                entry["message_text"] = original_text + suffix

                save_json(STATE_FILE, state)
                return

        print(f"⏳ Ни одна из игр #{target}-#{target + DOGON_GAMES - 1} ещё не завершена, ждём...", flush=True)

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
        "rule_prediction": None,
        "ml_prediction": None,
        "selected_prediction": None,
        "latency": None,
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

        rule_pred = rule_prediction(source, latency)
        if rule_pred is None:
            print(f"⏭️ Правила: latency {latency:.2f} — нет подходящего прогноза", flush=True)
            continue

        ml_pred = ml_prediction(source, latency)

        mode = state.get("mode", "RULES")
        selected = ml_pred if mode == "ML" and ml_pred else rule_pred
        selected_mode = "ML" if mode == "ML" and ml_pred else "RULES"

        entry["rule_prediction"] = rule_pred
        entry["ml_prediction"] = ml_pred
        entry["selected_prediction"] = selected
        entry["latency"] = latency
        entry["status"] = "pending"
        entry["mode_used"] = selected_mode

        msg = prediction_text(entry, selected, source, selected_mode)
        mid = send_message(msg)

        if mid:
            entry["message_id"] = mid
            entry["message_text"] = msg
            save_json(STATE_FILE, state)

            print(
                f"✅ ПРОГНОЗ: #{target} → {selected} | "
                f"метод={selected_mode} | "
                f"правила={rule_pred} | ML={ml_pred or '—'}",
                flush=True,
            )
        else:
            entry["status"] = "scheduled"
            save_json(STATE_FILE, state)

def prediction_text(entry, prediction, source_game, mode):
    msg = "🔮 <b>HYBRID 21 CLASSIC</b>\n"
    msg += f"🃏 Масть: {prediction}\n"
    msg += f"🎯 Целевая игра: #N{entry['target']}\n"
    msg += f"📌 Метод: {'🤖 ML' if mode == 'ML' else '📌 Правила'}\n"
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
    rw = state.get("rule_wins", 0)
    rl = state.get("rule_losses", 0)
    mw = state.get("ml_wins", 0)
    ml = state.get("ml_losses", 0)

    rtot = rw + rl
    mtot = mw + ml

    r_acc = f"{rw / rtot * 100:.1f}%" if rtot else "—"
    m_acc = f"{mw / mtot * 100:.1f}%" if mtot else "—"

    return (
        "📊 <b>СТАТИСТИКА (HYBRID)</b>\n"
        f"⏰ {datetime.now(MOSCOW_TZ).strftime('%d.%m.%Y %H:%M:%S')}\n"
        "==============================\n"
        f"📈 Всего прогнозов: {total}\n"
        f"🧠 Обучающих результатов: {samples}/{MIN_TRAIN_SAMPLES}\n"
        f"🤖 ML статус: {'ОБУЧЕН' if MODELS is not None else 'ОЖИДАЕТ'}\n"
        f"🔀 Текущий метод: {'🤖 ML' if state.get('mode') == 'ML' else '📌 Правила'}\n"
        "==============================\n"
        f"📌 Правила: {rw}✅ / {rl}❌ ({r_acc})\n"
        f"🤖 ML: {mw}✅ / {ml}❌ ({m_acc})\n"
        "==============================\n"
        f"🔄 Переключений: {state.get('switches', 0)}"
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
    global all_messages

    load_models()

    # Если есть данные — пытаемся обучить или дообучить
    if SKLEARN_OK and len(state.get("training_samples", [])) >= MIN_TRAIN_SAMPLES:
        train_models(force=True)

    send_startup_message()
    send_message(stats_text())
    send_ml_status()  # ← ОТПРАВЛЯЕМ СТАТУС ML

    offset = load_offset()
    last_stats = time.time()

    print("🚀 БОТ ГОТОВ. ПРАВИЛА РАБОТАЮТ СРАЗУ.", flush=True)

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