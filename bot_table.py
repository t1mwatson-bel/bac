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
print("🕐 АНАЛИЗАТОР ВРЕМЕНИ И КАРТ (ЧИСТЫЙ)", flush=True)
print("=" * 60, flush=True)

# =====================================================================
# ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ
# =====================================================================
BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    BOT_TOKEN = os.getenv('BOT_TOKEN_PROGNOZ')

CHANNEL_STATS = os.getenv('CHANNEL_STATS_ID')
CHANNEL_PROGNOZ = os.getenv('CHANNEL_PROGNOZ_ID')

print("🔍 ДИАГНОСТИКА ПЕРЕМЕННЫХ:", flush=True)
print(f"BOT_TOKEN: {BOT_TOKEN[:5] if BOT_TOKEN else 'НЕ ЗАДАН'}", flush=True)
print(f"CHANNEL_STATS_ID: {CHANNEL_STATS if CHANNEL_STATS else 'НЕ ЗАДАН'}", flush=True)
print(f"CHANNEL_PROGNOZ_ID: {CHANNEL_PROGNOZ if CHANNEL_PROGNOZ else 'НЕ ЗАДАН'}", flush=True)
print("=" * 60, flush=True)

if not BOT_TOKEN or not CHANNEL_STATS or not CHANNEL_PROGNOZ:
    print("❌ ОШИБКА: переменные окружения не заданы!", flush=True)
    exit(1)

print("✅ Все переменные заданы!", flush=True)

# =====================================================================
# НАСТРОЙКИ
# =====================================================================
MOSCOW_TZ = pytz.timezone('Europe/Moscow')

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://1xlite-36553.pro/ru/live/twentyone/2092323-21-classics",
    "Cookie": "platform_type=desktop; SESSION=34219176f69eace1b636911e2de9a15e; lng=ru; cookies_agree_type=3; tzo=3; is12h=0; auid=uaJbk2qIgo2M+6ofAxNqAg==; _ym_isad=2; mdd=1; _ga_7JGWL9SV66=GS2.1.s1787337341$o4$g1$t1787337359$j42$l0$h1608459194; window_width=150; referral_values=%7B%22type%22%3A%22reflinkid%22%2C%22val%22%3A%22s_50970m_355c_%22%2C%22additional%22%3A%7B%22name_tag%22%3A%22tag%22%7D%7D; fatman_uuid=45f69ff0-ecb1-67d4-3ff2-3a45baafc739; che_g=777dc1b9-efbf-4728-947a-4a2992ef6da5; sh.session.id=684214c4-f09e-42da-9c1a-ea61b9aca91b; _ym_uid=1786989905737338437; _ym_d=1786989905; _ga=GA1.1.547872848.1786989906"
}

SUITS_NAMES = {0: "♠️", 1: "♣️", 2: "♦️", 3: "♥️"}
RANKS = {1: "A", 2: "2", 3: "3", 4: "4", 5: "5", 6: "6", 7: "7", 8: "8", 9: "9", 10: "10", 11: "J", 12: "Q", 13: "K"}

processed_games = set()

# =====================================================================
# ФУНКЦИИ
# =====================================================================
def get_active_games():
    try:
        url = "https://1xlite-36553.pro/service-api/main-live-feed/v3/games1x2?cfView=3&count=40&fcountry=1&gr=415&grMode=4&lng=ru&ref=7&selectedMs=1.146.2092323,2.146.2092323,10.146.2092323"
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
                    if game_id and str(game_id) not in processed_games:
                        active_games.append(game)
            return active_games
        else:
            print(f"⚠️ Статус API: {response.status_code}", flush=True)
    except Exception as e:
        print(f"❌ Ошибка: {e}", flush=True)
    return []

def get_game_data(game_id):
    url = f"https://1xlite-36553.pro/service-api/LiveFeed/GetGameZip?id={game_id}&isSubGames=true&GroupEvents=true&countevents=250&grMode=4&partner=7&topGroups=&country=190&marketType=1&isNewBuilder=true"
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

def parse_cards(data):
    sc = data.get("Value", {}).get("SC", {})
    player_cards = []
    dealer_cards = []
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
    return player_cards, dealer_cards

def analyze_timing(game_id, player_cards, latency, start_time, end_time):
    if not player_cards:
        return
    timestamp = datetime.fromtimestamp(start_time, MOSCOW_TZ)
    utc_timestamp = datetime.fromtimestamp(start_time, pytz.UTC)
    ms = int((start_time % 1) * 1000)
    print("=" * 60)
    print(f"🎮 Игра: {game_id}")
    print(f"🕐 МСК: {timestamp.strftime('%H:%M:%S.%f')[:-3]}")
    print(f"🕐 UTC: {utc_timestamp.strftime('%H:%M:%S.%f')[:-3]}")
    print(f"⏱️ Задержка API: {latency:.2f} мс")
    print(f"🃏 Карт игрока: {len(player_cards)}")
    for card in player_cards:
        cs = card.get("CS", 0)
        cv = card.get("CV", 0)
        suit = SUITS_NAMES.get(cs, "?")
        rank = RANKS.get(cv, "?")
        print(f"   {rank}{suit}")
    print("=" * 60)

# =====================================================================
# ОСНОВНОЙ ЦИКЛ
# =====================================================================
def main():
    print("🔄 АНАЛИЗАТОР ЗАПУЩЕН (ТОЛЬКО СБОР ДАННЫХ)", flush=True)
    print("=" * 60, flush=True)
    while True:
        try:
            active_games = get_active_games()
            if not active_games:
                time.sleep(2)
                continue
            for game in active_games:
                game_id = str(game.get("id"))
                if game_id in processed_games:
                    continue
                data, latency, start_time, end_time = get_game_data(game_id)
                if not data:
                    continue
                player_cards, dealer_cards = parse_cards(data)
                if player_cards:
                    analyze_timing(game_id, player_cards, latency, start_time, end_time)
                    processed_games.add(game_id)
                time.sleep(0.3)
            if len(processed_games) > 200:
                processed_games.clear()
            time.sleep(1)
        except Exception as e:
            print(f"❌ Ошибка: {e}", flush=True)
            time.sleep(5)

if __name__ == "__main__":
    main()