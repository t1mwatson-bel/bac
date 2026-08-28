
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
# HYBRID BOT: сначала ТВОЯ МЕТОДИКА -> затем ML, если ML реально лучше
# 21 Classic / прогноз МАСТИ игроку
#
# ВАЖНО:
# - правила работают СРАЗУ, ML не блокирует прогнозы;
# - ML сначала работает в "тени", собирая честную статистику;
# - после MIN_TRAIN_SAMPLES размеченных прогнозов ML может стать основным
#   только если на последних RECENT_COMPARE прогнозах он лучше правил;
# - если ML потом становится хуже, бот автоматически возвращается к правилам.
# ================================================================

print("=" * 70, flush=True)
print("🃏 HYBRID RULES + ML BOT", flush=True)
print("📌 Сначала: ТВОЯ МЕТОДИКА | Потом: ML, если он лучше", flush=True)
print("=" * 70, flush=True)

# -------------------- ENV --------------------
BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("BOT_TOKEN_PROGNOZ")
CHANNEL_STATS = os.getenv("CHANNEL_STATS")
CHANNEL_PROGNOZ = os.getenv("CHANNEL_PROGNOZ")

# Cookie из старого рабочего бота НЕ хранится в коде.
# Если API без него не отвечает, задай переменную LIVE_COOKIE.
LIVE_COOKIE = os.getenv("LIVE_COOKIE", "")

if not BOT_TOKEN or not CHANNEL_STATS or not CHANNEL_PROGNOZ:
    raise RuntimeError("Нужны BOT_TOKEN, CHANNEL_STATS и CHANNEL_PROGNOZ")

# -------------------- SETTINGS --------------------
MOSCOW_TZ = pytz.timezone("Europe/Moscow")
BASE_URL = os.getenv("BASE_URL", "https://1xlite-36553.pro")

OFFSET = int(os.getenv("PREDICT_OFFSET", "10"))
DOGON_GAMES = int(os.getenv("DOGON_GAMES", "4"))

# ML не мешает правилам.
MIN_TRAIN_SAMPLES = int(os.getenv("ML_MIN_SAMPLES", "200"))
RECENT_COMPARE = int(os.getenv("ML_RECENT_COMPARE", "50"))
SWITCH_MARGIN = float(os.getenv("ML_SWITCH_MARGIN", "0.02"))  # 2 п.п.
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

# sklearn необязателен для запуска: правила будут работать даже если ML
# библиотека недоступна. При наличии sklearn ML включается автоматически.
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

def startup_message():
    text = (
        "🚀 <b>HYBRID RULES + ML</b>\n"
        "🃏 В начале работает твоя методика\n"
        f"🎯 Цель: +{OFFSET} игр\n"
        f"🤖 ML обучается параллельно, старт после {MIN_TRAIN_SAMPLES} результатов\n"
        "🔄 ML станет основным только если будет лучше правил\n"
        "🛡️ Если ML ухудшится — автоматический возврат к правилам"
    )
    send_message(text)

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
                    # emoji may contain variation selector, therefore startswith
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

# -------------------- YOUR RULES: НЕ МЕНЯЕМ --------------------
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

    # counts of suits/ranks in the source hand
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

    # latency is used as bins + raw value
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
            # Если в истории пока только один класс, обучаемся позже.
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

        # Сохраняем модели локально. Никаких /mnt/data.
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
            # class 1 может отсутствовать у необычной модели
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
seen_update_ids = set()

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

# -------------------- PREDICTION / COMPARISON --------------------
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

def evaluate_prediction(entry, result_game, training_target_game=None):
    """Оценивает попадание с учётом догонов, а ML sample всегда берёт target-game."""
    if entry.get("evaluated"):
        return

    predicted = entry.get("selected_prediction")
    rule_pred = entry.get("rule_prediction")
    ml_pred = entry.get("ml_prediction")

    present_result = {c.get("suit") for c in result_game.get("player_cards", [])}
    rule_win = bool(rule_pred and rule_pred in present_result)
    ml_win = bool(ml_pred and ml_pred in present_result)
    selected_win = bool(predicted and predicted in present_result)

    entry["rule_result"] = rule_win if rule_pred else None
    entry["ml_result"] = ml_win if ml_pred else None
    entry["selected_result"] = selected_win
    entry["evaluated"] = True
    entry["result_game"] = result_game.get("number")
    entry["dogon"] = max(0, int(result_game.get("number", entry["target"])) - int(entry["target"]))

    if rule_pred:
        state["rule_wins" if rule_win else "rule_losses"] += 1
    if ml_pred:
        state["ml_wins" if ml_win else "ml_losses"] += 1

    # Обучающий пример всегда относится к target-игре.
    source = find_game(entry.get("source"))
    latency = entry.get("latency")
    if source and latency is not None:
        try:
            target_for_training = training_target_game or find_game(entry["target"])
            if target_for_training:
                state["training_samples"].append({
                    "x": make_features(source, float(latency)),
                    "y": target_labels(target_for_training),
                })
        except Exception as e:
            print(f"⚠️ ML sample error: {e}", flush=True)

    state["total_predictions"] += 1

    if entry.get("message_id"):
        old = entry.get("message_text", "")
        if selected_win:
            if entry["dogon"] == 0:
                suffix = f"\n\n✅ <b>ЗАШЛО</b> — #N{entry['target']}"
            else:
                suffix = (
                    f"\n\n✅ <b>ЗАШЛО НА ДОГОНЕ {entry['dogon']}</b> "
                    f"— #N{result_game['number']}"
                )
        else:
            suffix = (
                f"\n\n❌ <b>НЕ ЗАШЛО</b> — проверено "
                f"#{entry['target']}-#{entry['target'] + DOGON_GAMES - 1}"
            )
        edit_message(entry["message_id"], old + suffix)

    print(
        f"📊 #N{entry['target']}: "
        f"Правила={'✅' if rule_pred and rule_win else '❌' if rule_pred else '—'} | "
        f"ML={'✅' if ml_pred and ml_win else '❌' if ml_pred else '—'} | "
        f"Итог={'✅' if selected_win else '❌'} | "
        f"догон={entry['dogon']}",
        flush=True,
    )

    if len(state["training_samples"]) > 5000:
        state["training_samples"] = state["training_samples"][-5000:]

    update_mode()
    save_json(STATE_FILE, state)

def check_pending():
    for entry in state.get("predictions", []):
        if entry.get("evaluated"):
            continue

        target = entry.get("target")
        if not isinstance(target, int):
            continue

        # Цель и ещё три догона.
        for i in range(DOGON_GAMES):
            game = find_game(target + i)
            if game and is_finished_game(game.get("text", "")):
                present = {c.get("suit") for c in game.get("player_cards", [])}
                selected = entry.get("selected_prediction")
                if selected and selected in present:
                    entry["result_game"] = target + i
                    # Для простоты результат фиксируем по фактической игре,
                    # а ML-обучение делаем по target, когда он появился.
                    # Если target не зашёл, shadow сравнение всё равно идёт.
                    if i == 0:
                        evaluate_prediction(entry, game)
                    else:
                        # Для обучения используем target, если он есть.
                        target_game = find_game(target)
                        evaluate_prediction(entry, target_game or game)
                    return
        # Если дошли до последнего догона и в нём тоже нет масти.
        last_game = find_game(target + DOGON_GAMES - 1)
        if last_game and is_finished_game(last_game.get("text", "")):
            target_game = find_game(target)
            evaluate_prediction(entry, target_game or last_game)

# -------------------- PREDICTION SCHEDULER --------------------
def schedule_for_game(game_number):
    target = game_number + OFFSET

    # Не создаём вторую запись на ту же цель.
    for e in state.get("predictions", []):
        if e.get("target") == target and not e.get("evaluated"):
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
    })
    if len(state["predictions"]) > MAX_STATE_PREDICTIONS:
        state["predictions"] = state["predictions"][-MAX_STATE_PREDICTIONS:]
    save_json(STATE_FILE, state)

    print(f"📅 Запланировано: #{game_number} → #{target} (+{OFFSET})", flush=True)

def process_scheduled():
    # Прогноз делаем, когда исходная игра source=target-1 уже пришла.
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
            print("⏳ Нет свежей задержки — правила остаются в очереди", flush=True)
            continue

        rule_pred = rule_prediction(source, latency)
        if rule_pred is None:
            print(f"⏭️ Правила: latency {latency:.2f} — нет подходящего прогноза", flush=True)
            continue

        ml_pred = ml_prediction(source, latency)

        # Главный метод определяется mode.
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
            # Не теряем задачу, попробуем снова.
            entry["status"] = "scheduled"
            save_json(STATE_FILE, state)

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

# -------------------- TELEGRAM OFFSET --------------------
def load_offset():
    try:
        return int(OFFSET_FILE.read_text().strip())
    except Exception:
        return 0

def save_offset(v):
    OFFSET_FILE.write_text(str(v), encoding="utf-8")

# -------------------- MAIN --------------------
def main():
    load_models()

    # Если уже есть достаточно samples, пытаемся дообучить.
    if SKLEARN_OK and len(state.get("training_samples", [])) >= MIN_TRAIN_SAMPLES:
        train_models(force=True)

    startup_message()
    send_message(stats_text())

    offset = load_offset()
    last_stats = time.time()

    print("🚀 БОТ ГОТОВ. ПРАВИЛА РАБОТАЮТ СРАЗУ.", flush=True)

    while True:
        try:
            now = time.time()

            # Сначала пытаемся завершить старые прогнозы.
            check_pending()

            # Затем ставим/отправляем новые.
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

                game = add_game(text)
                if not game:
                    continue

                n = game["number"]
                print(
                    f"📥 Игра #{n} | {'завершена' if is_finished_game(text) else 'не завершена'}",
                    flush=True,
                )

                # Каждая новая игра создаёт свою цель +10.
                # ML здесь НЕ требуется.
                if not is_finished_game(text):
                    schedule_for_game(n)

                cleanup_games()

            # После загрузки новых игр сразу проверяем расписание.
            check_pending()
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
