import os
import sys
import subprocess
import importlib

# =====================================================================
# ЗАВИСИМОСТИ
# =====================================================================

REQUIRED_PACKAGES = [
    "requests",
    "numpy",
    "pytz",
    "catboost"
]


def install_package(package):
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", package, "--quiet"],
            check=True
        )
        print(f"✅ {package} установлен!", flush=True)
        return True
    except Exception as e:
        print(f"❌ Ошибка установки {package}: {e}", flush=True)
        return False


def check_and_install_dependencies():
    print("=" * 60, flush=True)
    print("🔍 ПРОВЕРКА ЗАВИСИМОСТЕЙ...", flush=True)
    print("=" * 60, flush=True)

    missing = []

    for package in REQUIRED_PACKAGES:
        try:
            importlib.import_module(package.replace("-", "_"))
            print(f"✅ {package} - уже установлен", flush=True)
        except ImportError:
            print(f"⚠️ {package} - НЕ НАЙДЕН", flush=True)
            missing.append(package)

    if missing:
        print(
            f"\n📦 Нужно установить: {', '.join(missing)}",
            flush=True
        )

        for package in missing:
            if not install_package(package):
                print(
                    f"❌ Не удалось установить {package}",
                    flush=True
                )
                return False

        print(
            "\n✅ ВСЕ ЗАВИСИМОСТИ УСТАНОВЛЕНЫ!",
            flush=True
        )
    else:
        print(
            "\n✅ ВСЕ ЗАВИСИМОСТИ УСТАНОВЛЕНЫ!",
            flush=True
        )

    print("=" * 60, flush=True)
    return True


if not check_and_install_dependencies():
    print(
        "❌ ОШИБКА: Невозможно продолжить работу",
        flush=True
    )
    sys.exit(1)


# =====================================================================
# ИМПОРТЫ
# =====================================================================

import requests
import json
import re
import time
import pickle
import numpy as np
from datetime import datetime, timedelta
import pytz
from collections import defaultdict, deque
import warnings

warnings.filterwarnings("ignore")

# =====================================================================
# ML-БИБЛИОТЕКА
# =====================================================================
ML_AVAILABLE = False
ML_LIB = None

try:
    from catboost import CatBoostClassifier
    ML_AVAILABLE = True
    ML_LIB = "catboost"
    print("✅ CatBoost загружен!", flush=True)
except ImportError:
    try:
        from xgboost import XGBClassifier
        ML_AVAILABLE = True
        ML_LIB = "xgboost"
        print("✅ XGBoost загружен!", flush=True)
    except ImportError:
        print("⚠️ ML-библиотеки не установлены. Работаем без ML.", flush=True)

# =====================================================================
# ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ
# =====================================================================
BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    BOT_TOKEN = os.getenv('BOT_TOKEN_PROGNOZ')

CHANNEL_STATS = os.getenv('CHANNEL_STATS')
CHANNEL_PROGNOZ = os.getenv('CHANNEL_PROGNOZ')

print("=" * 60, flush=True)
print("🔍 ДИАГНОСТИКА ПЕРЕМЕННЫХ:", flush=True)
print(f"BOT_TOKEN: {BOT_TOKEN[:5] if BOT_TOKEN else 'НЕ ЗАДАН'}...", flush=True)
print(f"CHANNEL_STATS: {CHANNEL_STATS if CHANNEL_STATS else 'НЕ ЗАДАН'}", flush=True)
print(f"CHANNEL_PROGNOZ: {CHANNEL_PROGNOZ if CHANNEL_PROGNOZ else 'НЕ ЗАДАН'}", flush=True)
print("=" * 60, flush=True)

if not BOT_TOKEN or not CHANNEL_STATS or not CHANNEL_PROGNOZ:
    print("❌ ОШИБКА: переменные окружения не заданы!", flush=True)
    sys.exit(1)

# =====================================================================
# НАСТРОЙКИ
# =====================================================================
MOSCOW_TZ = pytz.timezone('Europe/Moscow')
BASE_URL = "https://1xlite-36553.pro"

# Файлы
DATA_FILE = "hybrid_data.json"
HISTORY_FILE = "hybrid_history.json"
ML_MODEL_FILE = "hybrid_model.pkl"
OFFSET_FILE = "hybrid_offset.txt"
GAME_HISTORY_FILE = "game_history.json"

# Настройки
MAX_RECORDS = 10000
CHECK_INTERVAL = 3
OFFSET = 10
TRAIN_EVERY = 60
MIN_TRAIN_SAMPLES = 200
MAX_HISTORY = 3000
MAX_GAME_HISTORY = 20  # Храним последние 20 игр для истории

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": f"{BASE_URL}/ru/live/twentyone/2092323-21-classics",
    "Cookie": "platform_type=desktop; SESSION=34219176f69eace1b636911e2de9a15e; lng=ru; cookies_agree_type=3; tzo=3; is12h=0; auid=uaJbk2qIgo2M+6ofAxNqAg==; _ym_isad=2; mdd=1; _ga_7JGWL9SV66=GS2.1.s1787337341$o4$g1$t1787337359$j42$l0$h1608459194; window_width=150; referral_values=%7B%22type%22%3A%22reflinkid%22%2C%22val%22%3A%22s_50970m_355c_%22%2C%22additional%22%3A%7B%22name_tag%22%3A%22tag%22%7D%7D; fatman_uuid=45f69ff0-ecb1-67d4-3ff2-3a45baafc739; che_g=777dc1b9-efbf-4728-947a-4a2992ef6da5; sh.session.id=684214c4-f09e-42da-9c1a-ea61b9aca91b; _ym_uid=1786989905737338437; _ym_d=1786989905; _ga=GA1.1.547872848.1786989906"
}

SUITS = ["♠️", "♣️", "♦️", "♥️"]
SUITS_NAMES = {0: "♠️", 1: "♣️", 2: "♦️", 3: "♥️"}
RANK_VALUES = {'6': 6, '7': 7, '8': 8, '9': 9, '10': 10, 'J': 11, 'Q': 12, 'K': 13, 'A': 14}
RANKS = {1: "A", 2: "2", 3: "3", 4: "4", 5: "5", 6: "6", 7: "7", 8: "8", 9: "9", 10: "10", 11: "J", 12: "Q", 13: "K"}

# =====================================================================
# ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ
# =====================================================================
ml_model = None
ml_initialized = False
collection_active = True
game_history = deque(maxlen=MAX_GAME_HISTORY)  # История последних игр

stats = {
    "total": 0,
    "win": 0,
    "lose": 0,
    "by_dogon": {0: 0, 1: 0, 2: 0, 3: 0},
    "ml_wins": 0,
    "ml_losses": 0,
    "rules_wins": 0,
    "rules_losses": 0,
    "games_collected": 0,
    "last_report": time.time()
}
processed_games = set()
finished_games = set()
all_messages = []

# =====================================================================
# ФУНКЦИИ ТЕЛЕГРАМ
# =====================================================================
def get_updates(offset):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    params = {"offset": offset, "timeout": 30}
    try:
        response = requests.get(url, params=params, timeout=35)
        return response.json()
    except Exception as e:
        print(f"❌ Ошибка getUpdates: {e}", flush=True)
        return {}

def send_message(chat_id, text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            return response.json()["result"]["message_id"]
        else:
            print(f"❌ Ошибка отправки: {response.status_code}", flush=True)
            return None
    except Exception as e:
        print(f"❌ Ошибка отправки: {e}", flush=True)
        return None

def edit_message(message_id, text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText"
    payload = {"chat_id": CHANNEL_PROGNOZ, "message_id": message_id, "text": text, "parse_mode": "HTML"}
    try:
        response = requests.post(url, json=payload, timeout=10)
        return response.status_code == 200
    except Exception as e:
        print(f"❌ Ошибка редактирования: {e}", flush=True)
        return False

def send_startup_message():
    data_count = len(load_data())
    now = datetime.now(MOSCOW_TZ)
    
    msg = f"""
╔══════════════════════════════════════════╗
║     🃏 ГИБРИДНЫЙ БОТ v13.0 🃏           ║
╠══════════════════════════════════════════╣
║  📊 Собрано игр: {data_count}/{MAX_RECORDS}          ║
║  🧠 ML-модель: {'✅ АКТИВНА' if ml_initialized else '⏳ ОЖИДАЕТ'}     ║
║  🔄 Сбор данных: {'🔄 АКТИВЕН' if collection_active else '⏸️ ОСТАНОВЛЕН'} ║
║  🎯 Смещение: +{OFFSET} игр (~{OFFSET*2} мин)      ║
║  📈 История: {len(game_history)} игр в памяти      ║
║  ⏰ {now.strftime('%d.%m.%Y %H:%M:%S')}        ║
╚══════════════════════════════════════════╝
"""
    send_message(CHANNEL_PROGNOZ, msg)
    print("🚀 БОТ ЗАПУЩЕН!", flush=True)

# =====================================================================
# ФУНКЦИИ ДЛЯ РАБОТЫ С ДАННЫМИ
# =====================================================================
def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return []
    return []

def save_data(record):
    global collection_active, stats
    
    data = load_data()
    
    if len(data) >= MAX_RECORDS:
        collection_active = False
        return data
    
    existing_index = None
    for i, r in enumerate(data):
        if r.get("game_id") == record["game_id"]:
            existing_index = i
            break
    
    if existing_index is not None:
        data[existing_index] = record
    else:
        data.append(record)
        stats["games_collected"] += 1
    
    if len(data) >= MAX_RECORDS and collection_active:
        collection_active = False
        print(f"⏸️ СБОР ДАННЫХ ОСТАНОВЛЕН! Достигнут лимит {MAX_RECORDS}", flush=True)
        send_message(CHANNEL_STATS, f"⏸️ <b>СБОР ДАННЫХ ОСТАНОВЛЕН</b>\nДостигнут лимит {MAX_RECORDS} игр")
    
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    
    return data

def load_history():
    if os.path.exists(HISTORY_FILE):
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

def load_game_history():
    if os.path.exists(GAME_HISTORY_FILE):
        try:
            with open(GAME_HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return deque(data, maxlen=MAX_GAME_HISTORY)
        except:
            return deque(maxlen=MAX_GAME_HISTORY)
    return deque(maxlen=MAX_GAME_HISTORY)

def save_game_history():
    try:
        with open(GAME_HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(list(game_history), f, indent=2, ensure_ascii=False)
    except:
        pass

def get_offset():
    if os.path.exists(OFFSET_FILE):
        try:
            with open(OFFSET_FILE, "r") as f:
                return int(f.read().strip())
        except:
            return 0
    return 0

def save_offset(offset):
    with open(OFFSET_FILE, "w") as f:
        f.write(str(offset))

# =====================================================================
# ФУНКЦИИ API
# =====================================================================
def get_active_games():
    try:
        url = f"{BASE_URL}/service-api/main-live-feed/v3/games1x2?cfView=3&count=40&fcountry=1&gr=415&grMode=4&lng=ru&ref=7&selectedMs=1.146.2092323,2.146.2092323,10.146.2092323"
        response = requests.get(url, headers=HEADERS, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, dict) and "Value" in data:
                games = data.get("Value", [])
            elif isinstance(data, list):
                games = data
            else:
                return []
            
            active_games = []
            for game in games:
                if game.get("liga", {}).get("id") == 2092323:
                    game_id = game.get("id")
                    if game_id:
                        active_games.append(game)
            return active_games
        else:
            return []
    except Exception as e:
        print(f"❌ Ошибка API: {e}", flush=True)
        return []

def get_game_data(game_id):
    url = f"{BASE_URL}/service-api/LiveFeed/GetGameZip?id={game_id}&isSubGames=true&GroupEvents=true&countevents=250&grMode=4&partner=7&topGroups=&country=190&marketType=1&isNewBuilder=true"
    try:
        start_time = time.time()
        response = requests.get(url, headers=HEADERS, timeout=5)
        end_time = time.time()
        latency = (end_time - start_time) * 1000
        
        if response.status_code == 200:
            return response.json(), latency, start_time, end_time
        else:
            return None, None, None, None
    except Exception as e:
        print(f"❌ Ошибка игры {game_id}: {e}", flush=True)
        return None, None, None, None

def parse_cards_and_state(data):
    sc = data.get("Value", {}).get("SC", {})
    player_cards = []
    dealer_cards = []
    state = None
    
    for item in sc.get("S", []):
        if item.get("Key") == "P1":
            try:
                player_cards = json.loads(item.get("Value", "[]"))
            except:
                player_cards = []
        if item.get("Key") == "P2":
            try:
                dealer_cards = json.loads(item.get("Value", "[]"))
            except:
                dealer_cards = []
        if item.get("Key") == "STATE":
            state = item.get("Value")
    
    return player_cards, dealer_cards, state

def get_game_number_by_time():
    now = datetime.now(MOSCOW_TZ)
    start = now.replace(hour=3, minute=0, second=0, microsecond=0)
    if now < start:
        start = start - timedelta(days=1)
    diff_minutes = (now - start).total_seconds() / 60
    game_number = int(diff_minutes) // 2 % 720 + 1
    return game_number

def get_target_game():
    current = get_game_number_by_time()
    target = current + OFFSET
    if target > 720:
        target = target - 720
    return target

def parse_game_from_text(text):
    try:
        game_match = re.search(r'#N(\d+)', text)
        if not game_match:
            return None
        game_number = int(game_match.group(1))
        
        parts = None
        if '◀️' in text:
            parts = text.split('◀️')
        elif '▶️' in text:
            parts = text.split('▶️')
        elif '-' in text:
            parts = text.split('-')
        elif '—' in text:
            parts = text.split('—')
        else:
            return None
        
        if not parts or len(parts) < 2:
            return None
        
        player_part = parts[0].strip()
        dealer_part = parts[1].strip()
        
        def parse_cards_from_part(part):
            cards_match = re.search(r'\(([^)]+)\)', part)
            if not cards_match:
                return []
            cards_str = cards_match.group(1).strip()
            cards = []
            i = 0
            while i < len(cards_str):
                if cards_str[i] == ' ':
                    i += 1
                    continue
                rank = ''
                if i + 1 < len(cards_str) and cards_str[i:i+2] == '10':
                    rank = '10'
                    i += 2
                elif cards_str[i] in 'AKQJ':
                    rank = cards_str[i]
                    i += 1
                elif cards_str[i].isdigit():
                    rank = cards_str[i]
                    i += 1
                else:
                    i += 1
                    continue
                suit = ''
                if i < len(cards_str):
                    if cards_str[i:i+2] == '♠️':
                        suit = '♠️'
                        i += 2
                    elif cards_str[i:i+2] == '♣️':
                        suit = '♣️'
                        i += 2
                    elif cards_str[i:i+2] == '♦️':
                        suit = '♦️'
                        i += 2
                    elif cards_str[i:i+2] == '♥️':
                        suit = '♥️'
                        i += 2
                    elif cards_str[i] in '♠♣♦♥':
                        suit = cards_str[i].replace('♠', '♠️').replace('♣', '♣️').replace('♦', '♦️').replace('♥', '♥️')
                        i += 1
                    else:
                        i += 1
                        continue
                if rank and suit:
                    cards.append({"rank": rank, "suit": suit})
            return cards
        
        player_cards = parse_cards_from_part(player_part)
        dealer_cards = parse_cards_from_part(dealer_part)
        
        return {
            "number": game_number,
            "player_cards": player_cards,
            "dealer_cards": dealer_cards,
            "text": text
        }
    except Exception as e:
        print(f"❌ Ошибка парсинга: {e}", flush=True)
        return None

# =====================================================================
# ИСТОРИЯ ИГР (ДЛЯ ML)
# =====================================================================
def update_game_history(latency, suit, rank, game_num):
    """Обновляет историю последних игр"""
    global game_history
    
    game_history.append({
        "latency": latency,
        "suit": suit,
        "rank": rank,
        "game_num": game_num,
        "timestamp": datetime.now(MOSCOW_TZ).isoformat()
    })
    
    save_game_history()

def get_history_features(history_source=None):
    source = list(history_source) if history_source is not None else list(game_history)
    features = {}
    latencies = [g.get("latency", 0) for g in source if isinstance(g.get("latency", 0), (int, float))]
    if len(latencies) >= 2:
        features["prev_latency"] = latencies[-2]
        features["latency_delta"] = latencies[-1] - latencies[-2]
        if len(latencies) >= 5:
            recent = latencies[-5:]
            features["latency_trend"] = (recent[-1] - recent[0]) / 5
            features["latency_volatility"] = float(np.std(recent))
    suits = [g.get("suit") for g in source if g.get("suit") in SUITS]
    if len(suits) >= 2:
        features["prev_suit"] = SUITS.index(suits[-2])
        last = suits[-1]
        run = 0
        for s in reversed(suits):
            if s == last: run += 1
            else: break
        features["suit_run"] = run
    ranks = [g.get("rank") for g in source if g.get("rank") in RANK_VALUES]
    if len(ranks) >= 2:
        features["prev_rank"] = RANK_VALUES.get(ranks[-2], 0)
    now = datetime.now(MOSCOW_TZ)
    features.update({
        "hour": now.hour, "minute": now.minute,
        "day_of_week": now.weekday(),
        "is_weekend": 1 if now.weekday() >= 5 else 0
    })
    return features

# =====================================================================
# ML-ФУНКЦИИ
# =====================================================================
def extract_features_from_game(game_data, latency, game_num, history_source=None):
    if not game_data:
        return None
    player_cards = game_data.get("player_cards", [])
    dealer_cards = game_data.get("dealer_cards", [])
    features = {
        "latency": float(latency or 0), "game_num": int(game_num or 0) % 100,
        "p1_rank_val": 0, "p1_suit": -1, "p2_rank_val": 0, "p2_suit": -1,
        "p3_rank_val": 0, "p3_suit": -1, "d1_rank_val": 0, "d1_suit": -1,
        "d2_rank_val": 0, "d2_suit": -1, "player_total": 0, "dealer_total": 0,
        "player_count": len(player_cards), "dealer_count": len(dealer_cards),
        "prev_latency": 0, "latency_delta": 0, "latency_trend": 0,
        "latency_volatility": 0, "prev_suit": -1, "suit_run": 0, "prev_rank": 0,
        "hour": 0, "minute": 0, "day_of_week": 0, "is_weekend": 0
    }
    for i, card in enumerate(player_cards[:3]):
        r, s = card.get("rank", ""), card.get("suit", "")
        if r in RANK_VALUES: features[f"p{i+1}_rank_val"] = RANK_VALUES[r]
        if s in SUITS: features[f"p{i+1}_suit"] = SUITS.index(s)
    for i, card in enumerate(dealer_cards[:2]):
        r, s = card.get("rank", ""), card.get("suit", "")
        if r in RANK_VALUES: features[f"d{i+1}_rank_val"] = RANK_VALUES[r]
        if s in SUITS: features[f"d{i+1}_suit"] = SUITS.index(s)
    def score(cards):
        return sum(10 if RANK_VALUES.get(c.get("rank",""), 0) >= 11 else RANK_VALUES.get(c.get("rank",""), 0) for c in cards)
    features["player_total"] = score(player_cards)
    features["dealer_total"] = score(dealer_cards)
    for k, v in get_history_features(history_source).items():
        if k in features: features[k] = v
    return features

def train_ml_model():
    global ml_model, ml_initialized
    if not ML_AVAILABLE:
        return False
    data = load_data()
    if len(data) < MIN_TRAIN_SAMPLES + 1:
        print(f"⏳ ML: данных {len(data)}/{MIN_TRAIN_SAMPLES + 1}", flush=True)
        return False

    X, y, feature_names = [], [], None

    # Обучаемся честно: признаки берём из ПРЕДЫДУЩЕЙ игры,
    # а правильный ответ — масть первой карты СЛЕДУЮЩЕЙ игры.
    # Карты целевой игры в признаки не попадают.
    for i in range(1, len(data)):
        prev_game, target_game = data[i-1], data[i]
        target_cards = target_game.get("player_cards", [])
        if not target_cards or target_cards[0].get("suit") not in SUITS:
            continue

        history_source = []
        for old in data[:i]:
            cards = old.get("player_cards", [])
            if cards:
                history_source.append({
                    "latency": float(old.get("latency_ms", 0) or 0),
                    "suit": cards[0].get("suit"),
                    "rank": cards[0].get("rank"),
                    "game_num": int(old.get("game_id", 0) or 0)
                })

        features = extract_features_from_game(
            prev_game,
            float(prev_game.get("latency_ms", 0) or 0),
            int(prev_game.get("game_id", 0) or 0),
            history_source=history_source
        )
        keys = sorted(features.keys())
        if feature_names is None: feature_names = keys
        X.append([features[k] for k in feature_names])
        y.append(SUITS.index(target_cards[0]["suit"]))

    if len(X) < MIN_TRAIN_SAMPLES:
        print(f"⏳ ML: качественных примеров {len(X)}/{MIN_TRAIN_SAMPLES}", flush=True)
        return False
    if len(set(y)) < 2:
        print("⚠️ ML: нужен минимум 2 класса масти", flush=True)
        return False
    if ML_LIB != "catboost":
        print(f"⚠️ ML: библиотека {ML_LIB} не поддержана этим кодом", flush=True)
        return False

    print(f"🧠 ML: обучение на {len(X)} примерах...", flush=True)
    model = CatBoostClassifier(
        iterations=300, depth=6, learning_rate=0.05,
        random_seed=42, verbose=False,
        loss_function="MultiClass", allow_writing_files=False
    )
    try:
        X = np.asarray(X, dtype=float)
        y = np.asarray(y, dtype=int)
        model.fit(X, y)
        with open(ML_MODEL_FILE, "wb") as f:
            pickle.dump({
                "model": model, "feature_count": X.shape[1],
                "train_samples": len(X), "feature_names": feature_names,
                "version": 2
            }, f)
        ml_model = model
        ml_initialized = True
        print(f"✅ ML ОБУЧЕНА И АКТИВНА: {len(X)} примеров", flush=True)
        return True
    except Exception as e:
        print(f"❌ Ошибка обучения ML: {e}", flush=True)
        return False

def load_ml_model():
    global ml_model, ml_initialized
    if not ML_AVAILABLE or not os.path.exists(ML_MODEL_FILE):
        return False
    try:
        with open(ML_MODEL_FILE, "rb") as f:
            saved = pickle.load(f)
        ml_model = saved["model"]
        ml_initialized = True
        print(f"✅ ML модель загружена ({saved.get('train_samples', 0)} примеров)", flush=True)
        return True
    except Exception as e:
        ml_model = None
        ml_initialized = False
        print(f"⚠️ Не удалось загрузить ML модель: {e}", flush=True)
        return False

def predict_ml(features):
    if not ml_initialized or ml_model is None:
        return None, None
    try:
        keys = sorted(features.keys())
        vector = np.asarray([[features[k] for k in keys]], dtype=float)
        expected = getattr(ml_model, "n_features_in_", vector.shape[1])
        if vector.shape[1] != expected:
            print(f"⚠️ ML: несовпадение признаков {vector.shape[1]} != {expected}", flush=True)
            return None, None
        probs = ml_model.predict_proba(vector)[0]
        cls = int(np.argmax(probs))
        return SUITS[cls], float(probs[cls])
    except Exception as e:
        print(f"⚠️ Ошибка ML-прогноза: {e}", flush=True)
        return None, None

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

def get_prediction(latency, current_game_data):
    """Гибридный прогноз с учётом истории"""
    global game_history
    
    # 1. Базовый прогноз по задержке
    base_suit = predict_suit_by_latency(latency)
    
    # 2. Уточнение по последовательности
    if current_game_data:
        p1 = current_game_data.get("player_cards", [])[0] if current_game_data.get("player_cards") else None
        p2 = current_game_data.get("dealer_cards", [])[0] if current_game_data.get("dealer_cards") else None
        p3 = current_game_data.get("player_cards", [])[1] if len(current_game_data.get("player_cards", [])) > 1 else None
        rules_pred = refine_by_sequence(p1, p2, p3, base_suit, latency)
    else:
        rules_pred = base_suit
    
    # 3. ML-прогноз (с историей)
    ml_pred = None
    ml_conf = None
    ml_features = None
    
    if ml_initialized and current_game_data:
        features = extract_features_from_game(current_game_data, latency, 0)
        if features:
            ml_features = features
            ml_pred, ml_conf = predict_ml(features)
    
    # 4. Гибридное решение
    if ml_pred and ml_conf and ml_conf > 0.75:
        print(f"🤖 ML: {ml_pred} ({ml_conf:.2f}) vs RULES: {rules_pred}", flush=True)
        return ml_pred, "ml", ml_conf, ml_features
    elif rules_pred:
        return rules_pred, "rules", None, ml_features
    else:
        return None, None, None, ml_features

# =====================================================================
# ПРОВЕРКА РЕЗУЛЬТАТОВ
# =====================================================================
def check_results(history):
    global game_history
    
    for entry in history:
        if entry.get("status") != "pending":
            continue
        
        target = entry.get("target")
        predicted_suit = entry.get("suit")
        message_id = entry.get("message_id")
        method = entry.get("method", "rules")
        
        if not predicted_suit or not message_id:
            continue
        
        data = load_data()
        found = False
        result_game = None
        
        for game in data:
            if game.get("game_id") == str(target):
                player_cards = game.get("player_cards", [])
                for card in player_cards:
                    if card.get("suit") == predicted_suit:
                        found = True
                        result_game = target
                        break
                break
        
        if found:
            print(f"🎯 МАСТЬ {predicted_suit} НАЙДЕНА в игре #{target}!", flush=True)
            update_stats(0, "win", method)
            
            original_text = entry.get("original_text", "")
            result_text = f"\n\n✅ <b>ЗАШЛО</b> в целевой игре: #{target}"
            
            if message_id:
                edit_message(message_id, original_text + result_text)
            entry["status"] = "win"
            entry["result_game"] = result_game
            save_history(history)
            return
        
        # Догон (3 игры)
        for i in range(1, 4):
            check_game = target + i
            for game in data:
                if game.get("game_id") == str(check_game):
                    player_cards = game.get("player_cards", [])
                    for card in player_cards:
                        if card.get("suit") == predicted_suit:
                            found = True
                            result_game = check_game
                            break
                    break
            
            if found:
                print(f"🎯 МАСТЬ {predicted_suit} НАЙДЕНА на догоне {i} в игре #{check_game}!", flush=True)
                update_stats(i, "win", method)
                
                original_text = entry.get("original_text", "")
                result_text = f"\n\n✅ <b>ЗАШЛО</b> на догоне {i}: #{check_game}"
                
                if message_id:
                    edit_message(message_id, original_text + result_text)
                entry["status"] = "win"
                entry["result_game"] = result_game
                entry["dogon"] = i
                save_history(history)
                return
        
        print(f"❌ Масть {predicted_suit} НЕ НАЙДЕНА за 4 игры", flush=True)
        update_stats(0, "lose", method)
        
        original_text = entry.get("original_text", "")
        result_text = f"\n\n❌ <b>НЕ ЗАШЛО</b> (проверено 4 игры)"
        
        if message_id:
            edit_message(message_id, original_text + result_text)
        entry["status"] = "lose"
        save_history(history)

# =====================================================================
# ОБНОВЛЕНИЕ СТАТИСТИКИ
# =====================================================================
def update_stats(dogon_number, result, method="rules"):
    stats["total"] += 1
    if result == "win":
        stats["win"] += 1
        stats["by_dogon"][dogon_number] = stats["by_dogon"].get(dogon_number, 0) + 1
        if method == "ml":
            stats["ml_wins"] += 1
        else:
            stats["rules_wins"] += 1
    else:
        stats["lose"] += 1
        if method == "ml":
            stats["ml_losses"] += 1
        else:
            stats["rules_losses"] += 1

def send_stats_report():
    now = datetime.now(MOSCOW_TZ)
    msg = f"📊 <b>СТАТИСТИКА (ГИБРИД)</b>\n"
    msg += f"⏰ {now.strftime('%d.%m.%Y %H:%M:%S')}\n"
    msg += f"{'=' * 30}\n"
    msg += f"📊 Собрано игр: {stats['games_collected']}/{MAX_RECORDS}\n"
    msg += f"📈 Всего прогнозов: {stats['total']}\n"
    if stats['total'] > 0:
        msg += f"✅ Зашло: {stats['win']} ({stats['win']/stats['total']*100:.1f}%)\n"
    else:
        msg += f"✅ Зашло: 0\n"
    msg += f"❌ Не зашло: {stats['lose']}\n"
    msg += f"{'=' * 30}\n"
    msg += f"<b>По методам:</b>\n"
    total_rules = stats['rules_wins'] + stats['rules_losses']
    if total_rules > 0:
        msg += f"  📌 Правила: {stats['rules_wins']}✅ / {stats['rules_losses']}❌ ({stats['rules_wins']/total_rules*100:.1f}%)\n"
    else:
        msg += f"  📌 Правила: 0✅ / 0❌\n"
    total_ml = stats['ml_wins'] + stats['ml_losses']
    if total_ml > 0:
        msg += f"  🤖 ML: {stats['ml_wins']}✅ / {stats['ml_losses']}❌ ({stats['ml_wins']/total_ml*100:.1f}%)\n"
    else:
        msg += f"  🤖 ML: 0✅ / 0❌\n"
    msg += f"{'=' * 30}\n"
    msg += f"<b>По догонам:</b>\n"
    for i in range(4):
        msg += f"  Догон {i}: {stats['by_dogon'].get(i, 0)}\n"
    
    if ml_initialized:
        msg += f"\n🤖 ML: АКТИВНА"
    else:
        msg += f"\n🤖 ML: ОЖИДАЕТ ({stats['games_collected']}/{MIN_TRAIN_SAMPLES})"
    
    msg += f"\n📈 История в памяти: {len(game_history)} игр"
    
    send_message(CHANNEL_PROGNOZ, msg)

# =====================================================================
# СБОР ДАННЫХ
# =====================================================================
def collect_game_data():
    global collection_active, finished_games
    
    if not collection_active:
        return
    
    active_games = get_active_games()
    if not active_games:
        return
    
    data = load_data()
    if len(data) >= MAX_RECORDS:
        collection_active = False
        return
    
    for game in active_games:
        game_id = str(game.get("id"))
        
        if game_id in finished_games:
            continue
        
        game_data, latency, start_time, end_time = get_game_data(game_id)
        if not game_data:
            continue
        
        player_cards, dealer_cards, state = parse_cards_and_state(game_data)
        
        if player_cards or dealer_cards:
            timestamp = datetime.fromtimestamp(start_time, MOSCOW_TZ) if start_time else datetime.now(MOSCOW_TZ)
            timestamp_msk_str = timestamp.strftime('%H:%M:%S.%f')[:-3]
            
            sequence = []
            max_len = max(len(player_cards), len(dealer_cards))
            for i in range(max_len):
                if i < len(player_cards):
                    pc = player_cards[i]
                    rank = RANKS.get(pc.get("CV", 0), "?")
                    suit = SUITS_NAMES.get(pc.get("CS", 0), "?")
                    sequence.append({"position": i*2+1, "who": "P", "rank": rank, "suit": suit})
                if i < len(dealer_cards):
                    dc = dealer_cards[i]
                    rank = RANKS.get(dc.get("CV", 0), "?")
                    suit = SUITS_NAMES.get(dc.get("CS", 0), "?")
                    sequence.append({"position": i*2+2, "who": "D", "rank": rank, "suit": suit})
            
            def calc_score(cards):
                score = 0
                for card in cards:
                    cv = card.get("CV", 0)
                    if cv == 14:
                        score += 11
                    elif cv == 13:
                        score += 4
                    elif cv == 12:
                        score += 3
                    elif cv == 11:
                        score += 2
                    elif 6 <= cv <= 10:
                        score += cv
                return score
            
            player_score = calc_score(player_cards)
            dealer_score = calc_score(dealer_cards)
            
            record = {
                "game_id": game_id,
                "timestamp_msk": timestamp_msk_str,
                "latency_ms": round(latency, 2) if latency else 0,
                "state": state,
                "player_score": player_score,
                "dealer_score": dealer_score,
                "player_cards": [{"rank": RANKS.get(c.get("CV", 0), "?"), "suit": SUITS_NAMES.get(c.get("CS", 0), "?")} for c in player_cards],
                "dealer_cards": [{"rank": RANKS.get(c.get("CV", 0), "?"), "suit": SUITS_NAMES.get(c.get("CS", 0), "?")} for c in dealer_cards],
                "sequence": sequence
            }
            
            data = save_data(record)
            
            if state in ["4", "5"]:
                finished_games.add(game_id)
                print(f"🏁 Игра {game_id} завершена (state={state}), сохранена", flush=True)
            
            if len(data) >= MAX_RECORDS:
                collection_active = False
                return
        
        time.sleep(0.5)

# =====================================================================
# ПРОГНОЗ ПО ВРЕМЕНИ
# =====================================================================
def check_and_predict():
    global stats, all_messages, game_history
    
    current_num = get_game_number_by_time()
    target_num = get_target_game()
    games_left = target_num - current_num
    
    if games_left != 2 and games_left != 1:
        return
    
    print(f"🔥 До цели #{target_num} осталось {games_left} игр! Делаю прогноз...", flush=True)
    
    # Получаем задержку
    latency = None
    active_games = get_active_games()
    for game in active_games:
        game_id = str(game.get("id"))
        data, measured_latency, _, _ = get_game_data(game_id)
        if data:
            latency = measured_latency
            break
    
    if latency is None:
        print("⏳ Не удалось получить задержку", flush=True)
        return
    
    # Получаем данные текущей игры
    current_game_data = None
    for msg in all_messages:
        if f"#N{current_num}" in msg:
            current_game_data = parse_game_from_text(msg)
            break
    
    # Делаем прогноз
    predicted_suit, method, confidence, ml_features = get_prediction(latency, current_game_data)
    
    if not predicted_suit:
        print(f"⏭️ Нет прогноза для #{target_num}", flush=True)
        return
    
    # Обновляем историю (если есть данные)
    if current_game_data:
        player_cards = current_game_data.get("player_cards", [])
        if player_cards:
            first_card = player_cards[0]
            update_game_history(latency, first_card.get("suit", "?"), first_card.get("rank", "?"), current_num)
    
    # Формируем сообщение
    msg = (f"🔮 <b>ГИБРИД (+{OFFSET})</b>\n" if ml_initialized else f"🔮 <b>ТЕСТ: ГИБРИД (+{OFFSET})</b>\n")
    msg += f"🃏 Масть: {predicted_suit}\n"
    
    if method == "ml":
        msg += f"🤖 Метод: ML (увер. {confidence:.2f})\n"
    elif method == "rules":
        msg += f"📌 Метод: ПРАВИЛА\n"
    else:
        msg += f"📌 Метод: ПРАВИЛА\n"
    
    if current_game_data:
        p1 = current_game_data.get("player_cards", [])[0] if current_game_data.get("player_cards") else None
        p2 = current_game_data.get("dealer_cards", [])[0] if current_game_data.get("dealer_cards") else None
        p3 = current_game_data.get("player_cards", [])[1] if len(current_game_data.get("player_cards", [])) > 1 else None
        
        seq_str = ""
        if p1:
            seq_str += f"P1:{p1['rank']}{p1['suit']} "
        if p2:
            seq_str += f"D2:{p2['rank']}{p2['suit']} "
        if p3:
            seq_str += f"P3:{p3['rank']}{p3['suit']}"
        
        if seq_str:
            msg += f"📌 {seq_str}\n"
    
    msg += f"🎯 Целевая игра: #N{target_num}\n"
    msg += f"📈 3 игры догон\n"
    msg += f"⏰ {datetime.now(MOSCOW_TZ).strftime('%H:%M:%S')}"
    
    # Добавляем информацию об истории
    if len(game_history) >= 2:
        last_game = game_history[-1]
        msg += f"\n📊 Предыдущая: {last_game.get('suit', '?')} (задержка {last_game.get('latency', 0):.1f} мс)"
        if len(game_history) >= 3:
            prev_game = game_history[-2]
            delta = last_game.get('latency', 0) - prev_game.get('latency', 0)
            msg += f", тренд: {'↗️' if delta > 0 else '↘️'}{abs(delta):.1f} мс"
    
    message_id = send_message(CHANNEL_PROGNOZ, msg)
    
    if message_id:
        history = load_history()
        history.append({
            "from_game": current_num,
            "target": target_num,
            "offset": OFFSET,
            "suit": predicted_suit,
            "method": method,
            "time": datetime.now(MOSCOW_TZ).isoformat(),
            "message_id": message_id,
            "status": "pending",
            "original_text": msg,
            "features": ml_features
        })
        save_history(history)
        print(f"✅ ПРОГНОЗ ОТПРАВЛЕН: #{target_num} → {predicted_suit} ({method})", flush=True)

# =====================================================================
# ОСНОВНОЙ ЦИКЛ
# =====================================================================
def main():
    global all_messages, stats, game_history
    
    print("🔄 ГИБРИДНЫЙ БОТ ЗАПУЩЕН", flush=True)
    print(f"📁 Данные в {DATA_FILE}", flush=True)
    print(f"📊 Максимум записей: {MAX_RECORDS}", flush=True)
    print(f"⏱️ Интервал: {CHECK_INTERVAL} сек", flush=True)
    print(f"🎯 Смещение: +{OFFSET} игр (~{OFFSET*2} мин)", flush=True)
    print(f"📈 История: {MAX_GAME_HISTORY} игр в памяти", flush=True)
    print("=" * 60, flush=True)
    
    # Загружаем данные
    existing_data = load_data()
    print(f"📊 Уже собрано записей: {len(existing_data)}", flush=True)
    
    if len(existing_data) >= MAX_RECORDS:
        global collection_active
        collection_active = False
        print(f"⏸️ СБОР ДАННЫХ ОТКЛЮЧЁН (лимит {MAX_RECORDS} достигнут)", flush=True)
    
    # Загружаем историю игр
    game_history = load_game_history()
    print(f"📈 Загружено истории: {len(game_history)} игр", flush=True)
    
    # Загружаем ML модель
    load_ml_model()
    if not ml_initialized:
        print("🧠 ML: запускаю первичное обучение сразу после старта...", flush=True)
        train_ml_model()
    
    stats["games_collected"] = len(existing_data)
    
    send_startup_message()
    
    # Загружаем сообщения
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
        params = {"chat_id": CHANNEL_STATS, "limit": 100}
        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            for update in data.get("result", []):
                post = update.get("channel_post")
                if post and post.get("text"):
                    all_messages.append(post.get("text"))
    except:
        pass
    
    print(f"📥 Загружено сообщений: {len(all_messages)}", flush=True)
    
    last_stats_time = time.time()
    last_train_time = time.time()
    last_check_time = time.time()
    offset = get_offset()
    
    print("🚀 БОТ ГОТОВ К РАБОТЕ!", flush=True)
    print("=" * 60, flush=True)
    
    while True:
        try:
            current_time = time.time()
            
            # Сбор данных
            collect_game_data()
            
            # Обработка сообщений
            updates = get_updates(offset)
            for update in updates.get("result", []):
                offset = update["update_id"] + 1
                save_offset(offset)
                
                channel_post = update.get("channel_post")
                edited_post = update.get("edited_channel_post")
                post = channel_post if channel_post else edited_post
                if not post:
                    continue
                
                chat_id = post.get("chat", {}).get("id")
                if str(chat_id) != str(CHANNEL_STATS):
                    continue
                
                text = post.get("text", "")
                if not text or "#N" not in text:
                    continue
                
                if text not in all_messages:
                    all_messages.append(text)
                    if len(all_messages) > 500:
                        all_messages = all_messages[-500:]
                
                game_id_match = re.search(r'#N(\d+)', text)
                if game_id_match:
                    game_number = int(game_id_match.group(1))
                    print(f"📥 Получена игра #{game_number}", flush=True)
                    
                    # Проверяем результаты
                    history = load_history()
                    for entry in history:
                        if entry.get("status") == "pending":
                            target = entry.get("target")
                            if target == game_number:
                                check_results(history)
            
            # Прогноз по времени
            if current_time - last_check_time >= CHECK_INTERVAL:
                check_and_predict()
                last_check_time = current_time
            
            # Проверка результатов
            history = load_history()
            for entry in history:
                if entry.get("status") == "pending":
                    check_results(history)
            
            # Обучение / переобучение ML
            if current_time - last_train_time >= TRAIN_EVERY:
                data_count = len(load_data())
                if data_count >= MIN_TRAIN_SAMPLES + 1:
                    train_ml_model()
                else:
                    print(
                        f"⏳ ML: собираем данные "
                        f"{data_count}/{MIN_TRAIN_SAMPLES + 1}",
                        flush=True
                    )
                last_train_time = current_time
            
            # Статистика
            if current_time - last_stats_time > 3600:
                send_stats_report()
                last_stats_time = current_time
            
            # Очистка
            if len(processed_games) > 500:
                processed_games.clear()
            
            time.sleep(CHECK_INTERVAL)
            
        except KeyboardInterrupt:
            print("🛑 Бот остановлен", flush=True)
            data_count = len(load_data())
            print(f"📊 Всего собрано записей: {data_count}", flush=True)
            break
        except Exception as e:
            print(f"❌ Ошибка: {e}", flush=True)
            import traceback
            traceback.print_exc()
            time.sleep(30)

if __name__ == "__main__":
    main()