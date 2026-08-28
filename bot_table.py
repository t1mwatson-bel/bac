code = r'''import os
import sys
import requests
import json
import re
import time
from datetime import datetime, timedelta
import pytz
from collections import Counter

# ================================================================
# ГИБРИД: ТВОИ ПРАВИЛА + ML
#
# Логика:
# 1) Твои правила работают СРАЗУ, без ожидания 200 игр.
# 2) История одновременно накапливается для ML.
# 3) После 200 игр ML начинает делать собственные прогнозы.
# 4) Правила и ML сначала сравниваются на ОДНИХ и тех же играх.
# 5) После минимальной выборки автоматически выбирается лучший метод.
# 6) ML продолжает обучаться даже если активны правила.
#
# ВАЖНО:
# - ML не обещает 98% точности.
# - Переключение происходит только при статистически заметном
#   преимуществе и достаточном количестве прогнозов.
# ================================================================

sys.stdout.flush()
print("=" * 70, flush=True)
print("🧠 ГИБРИД: ПРАВИЛА + ML", flush=True)
print("📌 Правила работают сразу | ML обучается параллельно", flush=True)
print("=" * 70, flush=True)

# ================================================================
# ОКРУЖЕНИЕ
# ================================================================
BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("BOT_TOKEN_PROGNOZ")
CHANNEL_STATS = os.getenv("CHANNEL_STATS")
CHANNEL_PROGNOZ = os.getenv("CHANNEL_PROGNOZ")

print("🔍 ДИАГНОСТИКА:", flush=True)
print(f"BOT_TOKEN: {BOT_TOKEN[:5] if BOT_TOKEN else 'НЕ ЗАДАН'}", flush=True)
print(f"CHANNEL_STATS: {CHANNEL_STATS if CHANNEL_STATS else 'НЕ ЗАДАН'}", flush=True)
print(f"CHANNEL_PROGNOZ: {CHANNEL_PROGNOZ if CHANNEL_PROGNOZ else 'НЕ ЗАДАН'}", flush=True)

if not BOT_TOKEN or not CHANNEL_STATS or not CHANNEL_PROGNOZ:
    print("❌ Не заданы BOT_TOKEN / CHANNEL_STATS / CHANNEL_PROGNOZ", flush=True)
    sys.exit(1)

# ================================================================
# НАСТРОЙКИ
# ================================================================
MOSCOW_TZ = pytz.timezone("Europe/Moscow")
BASE_URL = "https://1xlite-36553.pro"

OFFSET = 10
MAX_HISTORY = 5000
ML_MIN_GAMES = 200

# После какого количества совместных прогнозов разрешаем переключение.
COMPARE_MIN_PREDICTIONS = 50

# ML должен опередить текущий метод хотя бы на столько процентных пунктов.
SWITCH_ADVANTAGE_PP = 3.0

# Чтобы не прыгать туда-сюда из-за одного-двух прогнозов.
SWITCH_COOLDOWN_PREDICTIONS = 20

# Максимум игр для проверки одного прогноза:
MAX_DOGON = 3

OFFSET_FILE = "hybrid_offset.txt"
HISTORY_FILE = "hybrid_history.json"
MODEL_FILE = "hybrid_model.json"

PROCESSED_GAMES = set()
LAST_REPORT = time.time()

SUITS = ["♠️", "♣️", "♦️", "♥️"]
SUIT_TO_ID = {"♠️": 0, "♣️": 1, "♦️": 2, "♥️": 3}
RANKS = ["6", "7", "8", "9", "10", "J", "Q", "K", "A"]
RANK_TO_ID = {r: i for i, r in enumerate(RANKS)}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": f"{BASE_URL}/ru/live/twentyone/2092323-21-classics",
    "Cookie": os.getenv("ONE_X_COOKIE", "")
}

# ================================================================
# СТАТИСТИКА
# ================================================================
stats = {
    "rules": {"total": 0, "win": 0, "lose": 0,
              "by_dogon": {"0": 0, "1": 0, "2": 0, "3": 0}},
    "ml": {"total": 0, "win": 0, "lose": 0,
           "by_dogon": {"0": 0, "1": 0, "2": 0, "3": 0}},
    "active_method": "rules",
    "switch_count": 0,
    "last_switch": None
}

# ================================================================
# TELEGRAM
# ================================================================
def get_updates(offset):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    params = {"offset": offset, "timeout": 30}
    try:
        r = requests.get(url, params=params, timeout=35)
        return r.json()
    except Exception as e:
        print(f"❌ getUpdates: {e}", flush=True)
        return {}

def send_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHANNEL_PROGNOZ,
        "text": text,
        "parse_mode": "HTML"
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code == 200:
            return r.json()["result"]["message_id"]
        print(f"❌ sendMessage: {r.status_code} {r.text}", flush=True)
    except Exception as e:
        print(f"❌ sendMessage: {e}", flush=True)
    return None

def edit_message(message_id, text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText"
    payload = {
        "chat_id": CHANNEL_PROGNOZ,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML"
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        return r.status_code == 200
    except Exception as e:
        print(f"❌ editMessageText: {e}", flush=True)
        return False

# ================================================================
# ПАРСИНГ ИГР
# ================================================================
def parse_game_from_text(text):
    try:
        m = re.search(r"#N(\d+)", text)
        if not m:
            return None

        number = int(m.group(1))

        parts = None
        for sep in ("◀️", "▶️", "—", "-"):
            if sep in text:
                parts = text.split(sep, 1)
                break

        if not parts or len(parts) < 2:
            return None

        def parse_cards(part):
            m2 = re.search(r"\(([^)]*)\)", part)
            if not m2:
                return []

            s = m2.group(1).strip()
            cards = []
            i = 0

            while i < len(s):
                if s[i].isspace():
                    i += 1
                    continue

                if s[i:i+2] == "10":
                    rank = "10"
                    i += 2
                elif i < len(s) and s[i] in "AKQJ":
                    rank = s[i]
                    i += 1
                elif i < len(s) and s[i].isdigit():
                    rank = s[i]
                    i += 1
                else:
                    i += 1
                    continue

                suit = None

                if s[i:i+2] in ("♠️", "♣️", "♦️", "♥️"):
                    suit = s[i:i+2]
                    i += 2
                elif i < len(s) and s[i] in "♠♣♦♥":
                    suit = {
                        "♠": "♠️",
                        "♣": "♣️",
                        "♦": "♦️",
                        "♥": "♥️"
                    }[s[i]]
                    i += 1
                else:
                    # Не теряем позицию навсегда при необычном emoji.
                    i += 1

                if rank in RANK_TO_ID and suit in SUIT_TO_ID:
                    cards.append({"rank": rank, "suit": suit})

            return cards

        player_cards = parse_cards(parts[0])
        dealer_cards = parse_cards(parts[1])

        return {
            "number": number,
            "player_cards": player_cards,
            "dealer_cards": dealer_cards,
            "text": text
        }

    except Exception as e:
        print(f"❌ parse_game: {e}", flush=True)
        return None

def game_is_finished(text):
    return "✅" in text or "🔰" in text

def index_messages(messages):
    result = {}
    for text in messages:
        if not text:
            continue
        m = re.search(r"#N(\d+)", text)
        if not m:
            continue
        n = int(m.group(1))
        # Если одно и то же #N встретилось несколько раз, оставляем
        # последнее сообщение.
        result[n] = text
    return result

# ================================================================
# API ИГРЫ / ЗАДЕРЖКА
# ================================================================
def get_active_games():
    try:
        url = (
            f"{BASE_URL}/service-api/main-live-feed/v3/games1x2"
            f"?cfView=3&count=40&fcountry=1&gr=415&grMode=4&lng=ru"
            f"&ref=7&selectedMs=1.146.2092323,2.146.2092323,10.146.2092323"
        )
        r = requests.get(url, headers=HEADERS, timeout=10)

        if r.status_code != 200:
            return []

        data = r.json()

        if isinstance(data, dict):
            games = data.get("Value", [])
        elif isinstance(data, list):
            games = data
        else:
            games = []

        return [
            g for g in games
            if g.get("liga", {}).get("id") == 2092323 and g.get("id")
        ]

    except Exception as e:
        print(f"❌ get_active_games: {e}", flush=True)
        return []

def get_game_data(game_id):
    url = (
        f"{BASE_URL}/service-api/LiveFeed/GetGameZip"
        f"?id={game_id}&isSubGames=true&GroupEvents=true"
        f"&countevents=250&grMode=4&partner=7&topGroups="
        f"&country=190&marketType=1&isNewBuilder=true"
    )

    try:
        start = time.time()
        r = requests.get(url, headers=HEADERS, timeout=5)
        latency = (time.time() - start) * 1000

        if r.status_code == 200:
            return r.json(), latency

    except Exception as e:
        print(f"❌ get_game_data: {e}", flush=True)

    return None, None

def get_fresh_latency():
    games = get_active_games()
    if not games:
        return None

    for game in games:
        data, latency = get_game_data(str(game.get("id")))
        if data is not None and latency is not None:
            return latency

    return None

def get_game_number():
    now = datetime.now(MOSCOW_TZ)
    start = now.replace(hour=3, minute=0, second=0, microsecond=0)

    if now < start:
        start -= timedelta(days=1)

    diff_minutes = (now - start).total_seconds() / 60
    return int(diff_minutes) // 2 % 720 + 1

# ================================================================
# ТВОИ ПРАВИЛА — СОХРАНЕНЫ
# ================================================================
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

def rules_predict(game_data, latency):
    if latency is None:
        return None

    base = predict_suit_by_latency(latency)
    if base is None:
        return None

    if not game_data:
        return base

    pc = game_data.get("player_cards", [])
    dc = game_data.get("dealer_cards", [])

    p1 = pc[0] if len(pc) > 0 else None
    p2 = dc[0] if len(dc) > 0 else None
    p3 = pc[1] if len(pc) > 1 else None

    return refine_by_sequence(p1, p2, p3, base, latency)

# ================================================================
# ML БЕЗ ТРЕБОВАНИЯ ВНЕШНЕЙ БИБЛИОТЕКИ
#
# Используется простой online Naive Bayes по дискретным признакам.
# Это специально сделано так, чтобы бот не падал, если sklearn
# отсутствует на сервере.
# ================================================================
class SimpleML:
    def __init__(self):
        self.samples = 0
        self.class_count = Counter()
        self.feature_count = {}
        self.feature_total = Counter()

    def fit(self, X, y):
        self.samples = 0
        self.class_count = Counter()
        self.feature_count = {}
        self.feature_total = Counter()

        for features, label in zip(X, y):
            self.update(features, label)

    def update(self, features, label):
        label = int(label)
        self.class_count[label] += 1
        self.samples += 1

        for key, value in features.items():
            k = f"{key}={value}"
            self.feature_count.setdefault(k, Counter())
            self.feature_count[k][label] += 1
            self.feature_total[k] += 1

    def predict_proba(self, features):
        if self.samples < 20:
            return None

        labels = list(range(4))
        log_probs = {}

        for label in labels:
            prior = (self.class_count[label] + 1) / (
                self.samples + len(labels)
            )
            score = prior

            for key, value in features.items():
                k = f"{key}={value}"
                total = self.feature_total.get(k, 0)
                count = self.feature_count.get(k, {}).get(label, 0)

                # Лапласовское сглаживание.
                likelihood = (count + 1) / (total + len(labels))
                score *= likelihood

            log_probs[label] = score

        total = sum(log_probs.values())
        if total <= 0:
            return None

        return {
            label: log_probs[label] / total
            for label in labels
        }

    def predict(self, features):
        probs = self.predict_proba(features)
        if not probs:
            return None

        return max(probs, key=probs.get)

# ================================================================
# ПРИЗНАКИ ДЛЯ ML
# ================================================================
def card_feature(prefix, card):
    if not card:
        return {
            f"{prefix}_rank": -1,
            f"{prefix}_suit": -1
        }

    return {
        f"{prefix}_rank": RANK_TO_ID.get(card.get("rank"), -1),
        f"{prefix}_suit": SUIT_TO_ID.get(card.get("suit"), -1)
    }

def make_features(game_data):
    features = {}

    pc = game_data.get("player_cards", []) if game_data else []
    dc = game_data.get("dealer_cards", []) if game_data else []

    p1 = pc[0] if len(pc) > 0 else None
    p2 = pc[1] if len(pc) > 1 else None
    p3 = pc[2] if len(pc) > 2 else None

    d1 = dc[0] if len(dc) > 0 else None
    d2 = dc[1] if len(dc) > 1 else None
    d3 = dc[2] if len(dc) > 2 else None

    for prefix, card in (
        ("p1", p1), ("p2", p2), ("p3", p3),
        ("d1", d1), ("d2", d2), ("d3", d3)
    ):
        features.update(card_feature(prefix, card))

    features["player_count"] = len(pc)
    features["dealer_count"] = len(dc)

    # Дополнительные простые признаки.
    player_suits = [c.get("suit") for c in pc if c.get("suit") in SUIT_TO_ID]
    dealer_suits = [c.get("suit") for c in dc if c.get("suit") in SUIT_TO_ID]

    for suit in SUITS:
        features[f"player_{SUIT_TO_ID[suit]}_count"] = player_suits.count(suit)
        features[f"dealer_{SUIT_TO_ID[suit]}_count"] = dealer_suits.count(suit)

    features["p1_d1_same_suit"] = int(
        p1 is not None and d1 is not None and
        p1.get("suit") == d1.get("suit")
    )

    return features

# ================================================================
# ХРАНИЛИЩЕ
# ================================================================
def save_history(history):
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"❌ save_history: {e}", flush=True)

def load_history():
    if not os.path.exists(HISTORY_FILE):
        return []

    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception as e:
        print(f"⚠️ load_history: {e}", flush=True)
        return []

def get_offset():
    if not os.path.exists(OFFSET_FILE):
        return 0

    try:
        with open(OFFSET_FILE, "r", encoding="utf-8") as f:
            return int(f.read().strip())
    except:
        return 0

def save_offset(offset):
    with open(OFFSET_FILE, "w", encoding="utf-8") as f:
        f.write(str(offset))

# ================================================================
# ML MODEL SAVE/LOAD
# ================================================================
def save_model(model):
    data = {
        "samples": model.samples,
        "class_count": dict(model.class_count),
        "feature_count": {
            k: dict(v) for k, v in model.feature_count.items()
        },
        "feature_total": dict(model.feature_total)
    }

    try:
        with open(MODEL_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ Не удалось сохранить ML: {e}", flush=True)

def load_model():
    model = SimpleML()

    if not os.path.exists(MODEL_FILE):
        return model

    try:
        with open(MODEL_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        model.samples = int(data.get("samples", 0))
        model.class_count = Counter({
            int(k): int(v)
            for k, v in data.get("class_count", {}).items()
        })

        model.feature_count = {
            k: Counter({
                int(label): int(count)
                for label, count in v.items()
            })
            for k, v in data.get("feature_count", {}).items()
        }

        model.feature_total = Counter({
            k: int(v)
            for k, v in data.get("feature_total", {}).items()
        })

    except Exception as e:
        print(f"⚠️ Не удалось загрузить ML: {e}", flush=True)

    return model

# ================================================================
# ЗАГРУЗКА СООБЩЕНИЙ
# ================================================================
def load_recent_messages():
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    params = {"limit": 100}

    try:
        r = requests.get(url, params=params, timeout=10)

        if r.status_code != 200:
            return []

        messages = []

        for update in r.json().get("result", []):
            post = update.get("channel_post") or update.get("edited_channel_post")

            if not post or not post.get("text"):
                continue

            chat_id = post.get("chat", {}).get("id")
            if str(chat_id) != str(CHANNEL_STATS):
                continue

            messages.append(post["text"])

        return messages

    except Exception as e:
        print(f"❌ load_recent_messages: {e}", flush=True)
        return []

# ================================================================
# СБОР ИСТОРИИ ДЛЯ ML
# ================================================================
def get_finished_game_map(all_messages):
    game_map = {}

    for text in all_messages:
        if not game_is_finished(text):
            continue

        game = parse_game_from_text(text)

        if game:
            game_map[game["number"]] = game

    return game_map

def target_suit(game):
    for card in game.get("player_cards", []):
        suit = card.get("suit")
        if suit in SUIT_TO_ID:
            return suit
    return None

def build_training_samples(all_messages):
    """
    Для игры N используем карты игры N как признаки,
    а результат игры N+10 как label.

    Это соответствует логике твоего OFFSET=10.
    """
    game_map = get_finished_game_map(all_messages)

    X = []
    y = []

    numbers = sorted(game_map)

    for n in numbers:
        source = game_map.get(n)
        target = game_map.get(n + OFFSET)

        if not source or not target:
            continue

        label_suit = target_suit(target)

        if label_suit not in SUIT_TO_ID:
            continue

        X.append(make_features(source))
        y.append(SUIT_TO_ID[label_suit])

    return X, y

def train_ml_if_needed(model, all_messages):
    X, y = build_training_samples(all_messages)

    if len(X) < ML_MIN_GAMES:
        print(
            f"🤖 ML: ОЖИДАЕТ ({len(X)}/{ML_MIN_GAMES})",
            flush=True
        )
        return False

    # Используем все доступные исторические samples.
    model.fit(X, y)
    save_model(model)

    print(
        f"🤖 ML: ОБУЧЕН | samples={len(X)}",
        flush=True
    )
    return True

# ================================================================
# СТАТИСТИКА МЕТОДОВ
# ================================================================
def update_method_stats(method, dogon, result):
    if method not in stats:
        return

    s = stats[method]
    s["total"] += 1

    if result == "win":
        s["win"] += 1
        key = str(dogon)
        s["by_dogon"][key] = s["by_dogon"].get(key, 0) + 1
    else:
        s["lose"] += 1

def accuracy(method):
    s = stats[method]

    if s["total"] == 0:
        return 0.0

    return s["win"] / s["total"] * 100.0

def active_method_name():
    return "🤖 ML" if stats["active_method"] == "ml" else "📌 ПРАВИЛА"

def send_stats_report(ml_samples=0):
    now = datetime.now(MOSCOW_TZ)

    r = stats["rules"]
    m = stats["ml"]

    msg = "📊 <b>СТАТИСТИКА (ГИБРИД)</b>\n"
    msg += f"⏰ {now.strftime('%d.%m.%Y %H:%M:%S')}\n"
    msg += "=" * 30 + "\n"

    # Собранные games считаем отдельно снаружи, поэтому здесь
    # выводим состояние ML.
    msg += f"🏆 <b>Сейчас работает: {active_method_name()}</b>\n\n"

    msg += "📌 <b>Правила:</b>\n"
    msg += f"  Прогнозов: {r['total']}\n"
    msg += f"  ✅ {r['win']}\n"
    msg += f"  ❌ {r['lose']}\n"
    msg += f"  🎯 Точность: {accuracy('rules'):.1f}%\n\n"

    msg += "🤖 <b>ML:</b>\n"
    msg += f"  Прогнозов: {m['total']}\n"
    msg += f"  ✅ {m['win']}\n"
    msg += f"  ❌ {m['lose']}\n"
    msg += f"  🎯 Точность: {accuracy('ml'):.1f}%\n"
    msg += f"  📚 Обучающих игр: {ml_samples}/{ML_MIN_GAMES}\n\n"

    if r["total"] > 0:
        msg += "📌 Догоны правил: "
        msg += " | ".join(
            f"{i}: {r['by_dogon'].get(str(i), 0)}"
            for i in range(4)
        ) + "\n"

    if m["total"] > 0:
        msg += "🤖 Догоны ML: "
        msg += " | ".join(
            f"{i}: {m['by_dogon'].get(str(i), 0)}"
            for i in range(4)
        ) + "\n"

    msg += "\n"
    msg += f"🔄 Переключений: {stats['switch_count']}\n"
    send_message(msg)

# ================================================================
# ВЫБОР МЕТОДА
# ================================================================
def maybe_switch_method():
    r_total = stats["rules"]["total"]
    m_total = stats["ml"]["total"]

    # Нельзя сравнивать до достаточной выборки.
    if r_total < COMPARE_MIN_PREDICTIONS or m_total < COMPARE_MIN_PREDICTIONS:
        return

    r_acc = accuracy("rules")
    m_acc = accuracy("ml")

    current = stats["active_method"]

    # Если ML лучше правил минимум на SWITCH_ADVANTAGE_PP.
    if m_acc >= r_acc + SWITCH_ADVANTAGE_PP:
        candidate = "ml"
    elif r_acc >= m_acc + SWITCH_ADVANTAGE_PP:
        candidate = "rules"
    else:
        # Разница небольшая — ничего не меняем.
        return

    if candidate == current:
        return

    # Защита от частого переключения.
    last_switch_total = stats.get("last_switch_total")

    current_total = r_total + m_total

    if last_switch_total is not None:
        if current_total - last_switch_total < SWITCH_COOLDOWN_PREDICTIONS * 2:
            return

    stats["active_method"] = candidate
    stats["switch_count"] += 1
    stats["last_switch"] = datetime.now(MOSCOW_TZ).isoformat()
    stats["last_switch_total"] = current_total

    print(
        f"🏆 ПЕРЕКЛЮЧЕНИЕ: {current.upper()} → {candidate.upper()} | "
        f"Правила {r_acc:.1f}% | ML {m_acc:.1f}%",
        flush=True
    )

    msg = (
        "🔄 <b>АВТОПЕРЕКЛЮЧЕНИЕ</b>\n\n"
        f"📌 Правила: {r_acc:.1f}%\n"
        f"🤖 ML: {m_acc:.1f}%\n\n"
        f"🏆 Новый активный метод: "
        f"{'🤖 ML' if candidate == 'ml' else '📌 ПРАВИЛА'}"
    )

    send_message(msg)

# ================================================================
# ПРОВЕРКА РЕЗУЛЬТАТОВ
#
# ВАЖНО: один прогноз создаёт две независимые оценки:
# - rules_prediction
# - ml_prediction
#
# Это позволяет честно сравнить методы на одной и той же цели.
# ================================================================
def prediction_hit(target_game_number, predicted_suit, game_map):
    if not predicted_suit:
        return None

    for dogon in range(MAX_DOGON + 1):
        n = target_game_number + dogon
        game = game_map.get(n)

        if not game:
            return None

        if target_suit(game) == predicted_suit:
            return dogon

    return -1

def finish_prediction_entry(entry, history, game_map):
    if entry.get("status") != "pending":
        return False

    target = entry.get("target")

    rules_suit = entry.get("rules_suit")
    ml_suit = entry.get("ml_suit")

    # Проверяем независимо.
    rules_dogon = prediction_hit(target, rules_suit, game_map)
    ml_dogon = prediction_hit(target, ml_suit, game_map)

    # Если ещё не появилась последняя нужная игра — ждём.
    if rules_dogon is None or ml_dogon is None:
        return False

    if rules_dogon >= 0:
        update_method_stats("rules", rules_dogon, "win")
    else:
        update_method_stats("rules", MAX_DOGON, "lose")

    if ml_dogon >= 0:
        update_method_stats("ml", ml_dogon, "win")
    else:
        update_method_stats("ml", MAX_DOGON, "lose")

    entry["status"] = "evaluated"
    entry["rules_result"] = "win" if rules_dogon >= 0 else "lose"
    entry["rules_dogon"] = rules_dogon if rules_dogon >= 0 else MAX_DOGON

    entry["ml_result"] = "win" if ml_dogon >= 0 else "lose"
    entry["ml_dogon"] = ml_dogon if ml_dogon >= 0 else MAX_DOGON

    save_history(history)

    print(
        f"📊 РЕЗУЛЬТАТ #{target}: "
        f"Правила={'✅' if rules_dogon >= 0 else '❌'} "
        f"ML={'✅' if ml_dogon >= 0 else '❌'}",
        flush=True
    )

    # После каждого сравнения проверяем, не пора ли переключаться.
    maybe_switch_method()

    # Обновляем сообщение прогноза.
    message_id = entry.get("message_id")

    if message_id:
        active_suit = (
            ml_suit
            if stats["active_method"] == "ml" and ml_suit
            else rules_suit
        )

        text = "🔮 <b>ГИБРИД: ПРАВИЛА + ML</b>\n"
        text += f"🃏 Активный метод: {active_method_name()}\n"
        text += f"🃏 Прогноз правил: {rules_suit or '—'}\n"
        text += f"🤖 Прогноз ML: {ml_suit or 'ОЖИДАЕТ'}\n"
        text += f"🎯 Целевая игра: #N{target}\n"

        if rules_dogon >= 0:
            text += f"📌 Правила: ✅ догон {rules_dogon}\n"
        else:
            text += "📌 Правила: ❌\n"

        if ml_dogon >= 0:
            text += f"🤖 ML: ✅ догон {ml_dogon}\n"
        else:
            text += "🤖 ML: ❌\n"

        text += f"⏰ {entry.get('time', '')[:16]}"

        edit_message(message_id, text)

    return True

def check_results(history, all_messages):
    game_map = get_finished_game_map(all_messages)

    changed = False

    for entry in history:
        if finish_prediction_entry(entry, history, game_map):
            changed = True

    if changed:
        save_history(history)

# ================================================================
# СОЗДАНИЕ ПРЕДСКАЗАНИЯ
# ================================================================
def create_prediction(history, all_messages, current_num, target):
    # Не создаём дубликат на ту же цель.
    for h in history:
        if (
            h.get("target") == target and
            h.get("status") in ("scheduled", "pending")
        ):
            return False

    game_map = get_finished_game_map(all_messages)
    current_game = game_map.get(current_num)

    latency = get_fresh_latency()

    if latency is None:
        print("⏳ Нет свежей latency — ждём", flush=True)
        return False

    # ============================================================
    # 1. ТВОИ ПРАВИЛА — РАБОТАЮТ СРАЗУ
    # ============================================================
    rules_suit = rules_predict(current_game, latency)

    if rules_suit is None:
        print(
            f"⏭️ Правила не дали прогноз при latency={latency:.2f}",
            flush=True
        )
        return False

    # ============================================================
    # 2. ML — только после 200 обучающих samples
    # ============================================================
    ml_suit = None
    ml_probs = None

    model = create_prediction.model

    if model.samples >= ML_MIN_GAMES and current_game:
        probs = model.predict_proba(make_features(current_game))

        if probs:
            ml_label = max(probs, key=probs.get)
            ml_suit = SUITS[ml_label]
            ml_probs = probs

    # ============================================================
    # Если ML ещё не готов, активным остаются правила.
    # ============================================================
    if stats["active_method"] == "ml" and ml_suit:
        active_suit = ml_suit
        active_name = "🤖 ML"
    else:
        active_suit = rules_suit
        active_name = "📌 ПРАВИЛА"

    now = datetime.now(MOSCOW_TZ)

    msg = "🔮 <b>ГИБРИД: ПРАВИЛА + ML</b>\n"
    msg += f"🏆 Активный метод: {active_name}\n"
    msg += f"🃏 Прогноз: {active_suit}\n"
    msg += f"📌 Правила: {rules_suit}\n"

    if ml_suit:
        confidence = max(ml_probs.values()) * 100 if ml_probs else 0
        msg += f"🤖 ML: {ml_suit} ({confidence:.1f}%)\n"
    else:
        msg += f"🤖 ML: ОЖИДАЕТ ({model.samples}/{ML_MIN_GAMES})\n"

    msg += f"🎯 Целевая игра: #N{target}\n"
    msg += "📈 3 игры догон\n"
    msg += f"⏱️ Latency: {latency:.2f} мс\n"

    if current_game:
        pc = current_game.get("player_cards", [])
        dc = current_game.get("dealer_cards", [])

        seq = []

        if len(pc) > 0:
            seq.append(f"P1:{pc[0]['rank']}{pc[0]['suit']}")
        if len(dc) > 0:
            seq.append(f"D1:{dc[0]['rank']}{dc[0]['suit']}")
        if len(pc) > 1:
            seq.append(f"P2:{pc[1]['rank']}{pc[1]['suit']}")

        if seq:
            msg += "📌 " + " ".join(seq) + "\n"

    msg += f"⏰ {now.strftime('%H:%M:%S')}"

    message_id = send_message(msg)

    if not message_id:
        return False

    history.append({
        "from_game": current_num,
        "target": target,
        "offset": OFFSET,
        "rules_suit": rules_suit,
        "ml_suit": ml_suit,
        "ml_probs": ml_probs,
        "latency": latency,
        "status": "pending",
        "time": now.isoformat(),
        "message_id": message_id
    })

    save_history(history)

    print(
        f"✅ ПРОГНОЗ: #{target} | "
        f"Правила={rules_suit} | ML={ml_suit or 'нет'} | "
        f"активен={active_name}",
        flush=True
    )

    return True

# Хак для передачи model без глобального аргумента.
create_prediction.model = None

# ================================================================
# СИНХРОНИЗАЦИЯ ML
# ================================================================
def rebuild_ml(model, all_messages):
    X, y = build_training_samples(all_messages)

    if len(X) < ML_MIN_GAMES:
        print(
            f"🤖 ML: ОЖИДАЕТ ({len(X)}/{ML_MIN_GAMES})",
            flush=True
        )
        return len(X)

    model.fit(X, y)
    save_model(model)

    print(
        f"🤖 ML: ОБУЧЕН | samples={len(X)}",
        flush=True
    )

    return len(X)

# ================================================================
# ОЧИСТКА
# ================================================================
def clean_history(history):
    if len(history) > MAX_HISTORY:
        # Сначала сохраняем pending, затем самые свежие записи.
        pending = [
            x for x in history
            if x.get("status") == "pending"
        ]

        done = [
            x for x in history
            if x.get("status") != "pending"
        ]

        keep_done = max(0, MAX_HISTORY - len(pending))
        history = done[-keep_done:] + pending

        print(
            f"🧹 История очищена: {len(history)} записей",
            flush=True
        )

    return history

# ================================================================
# STARTUP
# ================================================================
def send_startup_message(ml_samples):
    msg = "🚀 <b>ГИБРИД ЗАПУЩЕН</b>\n\n"
    msg += "📌 Правила: <b>РАБОТАЮТ СРАЗУ</b>\n"
    msg += f"🤖 ML: {ml_samples}/{ML_MIN_GAMES}\n"
    msg += f"🏆 Активный метод: {active_method_name()}\n"
    msg += "🔄 ML будет сравниваться с правилами\n"
    msg += "🏆 Переключение — только если ML реально лучше"

    send_message(msg)

# ================================================================
# MAIN
# ================================================================
def main():
    global LAST_REPORT

    print("🚀 Запуск гибрида...", flush=True)

    history = load_history()
    model = load_model()
    create_prediction.model = model

    offset = get_offset()

    print("📥 Загружаем историю Telegram...", flush=True)
    all_messages = load_recent_messages()

    print(
        f"📥 Сообщений загружено: {len(all_messages)}",
        flush=True
    )

    ml_samples = rebuild_ml(model, all_messages)

    # Если ML уже обучен, но active_method потерялся после перезапуска,
    # сохраняем правила как безопасный старт.
    if ml_samples < ML_MIN_GAMES:
        stats["active_method"] = "rules"

    send_startup_message(ml_samples)

    check_results(history, all_messages)

    last_train_time = time.time()
    last_stats_time = time.time()
    last_check_time = time.time()
    last_rebuild_messages = len(all_messages)

    print("=" * 70, flush=True)
    print("🟢 БОТ ГОТОВ", flush=True)
    print(
        f"📌 Правила активны | ML={ml_samples}/{ML_MIN_GAMES}",
        flush=True
    )
    print("=" * 70, flush=True)

    while True:
        try:
            now_ts = time.time()

            # --------------------------------------------------------
            # Telegram updates
            # --------------------------------------------------------
            updates = get_updates(offset)

            for update in updates.get("result", []):
                offset = update["update_id"] + 1
                save_offset(offset)

                post = (
                    update.get("channel_post")
                    or update.get("edited_channel_post")
                )

                if not post:
                    continue

                chat_id = post.get("chat", {}).get("id")

                if str(chat_id) != str(CHANNEL_STATS):
                    continue

                text = post.get("text", "")

                if not text or "#N" not in text:
                    continue

                m = re.search(r"#N(\d+)", text)

                if not m:
                    continue

                game_number = int(m.group(1))

                all_messages.append(text)

                if len(all_messages) > 5000:
                    all_messages = all_messages[-5000:]

                print(
                    f"📥 Получена игра #{game_number}",
                    flush=True
                )

                # Завершённая игра — проверяем результаты.
                if game_is_finished(text):
                    check_results(history, all_messages)
                    continue

                # ----------------------------------------------------
                # Для прогнозов ждём текущую игру.
                # ----------------------------------------------------
                if game_number in PROCESSED_GAMES:
                    continue

                PROCESSED_GAMES.add(game_number)

                current_num = get_game_number()
                target = current_num + OFFSET

                # Планируем одну цель за раз.
                already = any(
                    h.get("target") == target and
                    h.get("status") in ("pending", "scheduled")
                    for h in history
                )

                if already:
                    continue

                history.append({
                    "from_game": current_num,
                    "target": target,
                    "status": "scheduled",
                    "created_time":
                        datetime.now(MOSCOW_TZ).isoformat()
                })

                save_history(history)

                print(
                    f"📅 Запланирован прогноз "
                    f"#{current_num} → #{target} (+{OFFSET})",
                    flush=True
                )

            # --------------------------------------------------------
            # ПЕРЕОБУЧЕНИЕ ML
            #
            # Каждые 60 секунд перестраиваем модель на свежей истории.
            # --------------------------------------------------------
            if now_ts - last_train_time >= 60:
                ml_samples = rebuild_ml(model, all_messages)
                last_train_time = now_ts

            # --------------------------------------------------------
            # ПЛАНИРОВЩИК
            #
            # Как в твоём рабочем боте: прогноз примерно за 1 игру
            # до цели.
            # --------------------------------------------------------
            current_num = get_game_number()

            for entry in history:
                if entry.get("status") != "scheduled":
                    continue

                target = entry.get("target")

                if not isinstance(target, int):
                    continue

                games_left = target - current_num

                if games_left != 1:
                    continue

                print(
                    f"🔥 Время прогноза: #{current_num} → #{target}",
                    flush=True
                )

                if create_prediction(
                    history,
                    all_messages,
                    current_num,
                    target
                ):
                    entry["status"] = "pending"
                    save_history(history)

            # --------------------------------------------------------
            # ПРОВЕРКА РЕЗУЛЬТАТОВ
            # --------------------------------------------------------
            if now_ts - last_check_time >= 5:
                check_results(history, all_messages)
                last_check_time = now_ts

            # --------------------------------------------------------
            # ОТЧЁТ
            # --------------------------------------------------------
            if now_ts - last_stats_time >= 3600:
                send_stats_report(ml_samples)
                last_stats_time = now_ts

            # --------------------------------------------------------
            # Очистка памяти.
            # --------------------------------------------------------
            history = clean_history(history)

            if len(PROCESSED_GAMES) > 1000:
                # Оставляем последние номера.
                PROCESSED_GAMES.clear()

            save_history(history)

            time.sleep(1)

        except KeyboardInterrupt:
            print("🛑 Остановлено", flush=True)
            break

        except Exception as e:
            print(f"❌ MAIN ERROR: {e}", flush=True)
            import traceback
            traceback.print_exc()
            time.sleep(20)

if __name__ == "__main__":
    main()
'''
path = "/mnt/data/hybrid_rules_ml_bot.py"
with open(path, "w", encoding="utf-8") as f:
    f.write(code)

print(f"Готово: {path}")
