import os
import sys
import requests
import json
import time
from datetime import datetime
import pytz

# =====================================================================
# ПРИНУДИТЕЛЬНЫЙ ВЫВОД ЛОГОВ
# =====================================================================
sys.stdout.flush()
print("=" * 60, flush=True)
print("🕐 АНАЛИЗАТОР ВРЕМЕНИ И КАРТ (ЧАСТЫЙ ОПРОС)", flush=True)
print("=" * 60, flush=True)

# =====================================================================
# ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ
# =====================================================================
BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    BOT_TOKEN = os.getenv('BOT_TOKEN_PROGNOZ')

print("🔍 ДИАГНОСТИКА ПЕРЕМЕННЫХ:", flush=True)
print(f"BOT_TOKEN: {BOT_TOKEN[:5] if BOT_TOKEN else 'НЕ ЗАДАН'}", flush=True)
print("=" * 60, flush=True)

if not BOT_TOKEN:
    print("❌ ОШИБКА: BOT_TOKEN не задан!", flush=True)
    exit(1)

print("✅ BOT_TOKEN задан!", flush=True)

# =====================================================================
# НАСТРОЙКИ
# =====================================================================
MOSCOW_TZ = pytz.timezone('Europe/Moscow')
DATA_FILE = "timing_data.json"
MAX_RECORDS = 10000
CHECK_INTERVAL = 10  # Проверяем каждые 10 секунд

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://1xlite-36553.pro/ru/live/twentyone/2092323-21-classics",
    "Cookie": "platform_type=desktop; SESSION=34219176f69eace1b636911e2de9a15e; lng=ru; cookies_agree_type=3; tzo=3; is12h=0; auid=uaJbk2qIgo2M+6ofAxNqAg==; _ym_isad=2; mdd=1; _ga_7JGWL9SV66=GS2.1.s1787337341$o4$g1$t1787337359$j42$l0$h1608459194; window_width=150; referral_values=%7B%22type%22%3A%22reflinkid%22%2C%22val%22%3A%22s_50970m_355c_%22%2C%22additional%22%3A%7B%22name_tag%22%3A%22tag%22%7D%7D; fatman_uuid=45f69ff0-ecb1-67d4-3ff2-3a45baafc739; che_g=777dc1b9-efbf-4728-947a-4a2992ef6da5; sh.session.id=684214c4-f09e-42da-9c1a-ea61b9aca91b; _ym_uid=1786989905737338437; _ym_d=1786989905; _ga=GA1.1.547872848.1786989906"
}

SUITS_NAMES = {0: "♠️", 1: "♣️", 2: "♦️", 3: "♥️"}
RANKS = {1: "A", 2: "2", 3: "3", 4: "4", 5: "5", 6: "6", 7: "7", 8: "8", 9: "9", 10: "10", 11: "J", 12: "Q", 13: "K"}

finished_games = set()  # Завершённые игры (уже сохранены)
active_games_cache = {}  # {game_id: {"state": state, "cards_count": count, "last_update": time}}

# =====================================================================
# ФУНКЦИИ ДЛЯ РАБОТЫ С ФАЙЛОМ
# =====================================================================
def load_timing_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return []
    return []

def save_timing_data(game_id, timestamp_msk, timestamp_utc, latency, cards, player_score, dealer_score, state):
    data = load_timing_data()
    
    cards_list = []
    for card in cards:
        cs = card.get("CS", 0)
        cv = card.get("CV", 0)
        suit = SUITS_NAMES.get(cs, "?")
        rank = RANKS.get(cv, "?")
        cards_list.append(f"{rank}{suit}")
    
    record = {
        "game_id": game_id,
        "timestamp_msk": timestamp_msk,
        "timestamp_utc": timestamp_utc,
        "latency_ms": round(latency, 2),
        "cards": cards_list,
        "player_score": player_score,
        "dealer_score": dealer_score,
        "state": state
    }
    
    # Проверяем, есть ли уже запись для этой игры
    existing_index = None
    for i, r in enumerate(data):
        if r.get("game_id") == game_id:
            existing_index = i
            break
    
    if existing_index is not None:
        # Обновляем существующую запись (если карт больше)
        if len(cards_list) > len(data[existing_index].get("cards", [])):
            data[existing_index] = record
            print(f"🔄 Обновлена запись для игры {game_id} ({len(cards_list)} карт)", flush=True)
        else:
            return
    else:
        data.append(record)
        print(f"💾 Новая запись для игры {game_id} ({len(cards_list)} карт)", flush=True)
    
    if len(data) > MAX_RECORDS:
        data = data[-MAX_RECORDS:]
    
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

# =====================================================================
# ФУНКЦИИ ДЛЯ РАБОТЫ С API
# =====================================================================
def get_active_games():
    try:
        url = "https://1xlite-36553.pro/service-api/main-live-feed/v3/games1x2?cf