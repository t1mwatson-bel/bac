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
print("🃏 АНАЛИЗАТОР ПОСЛЕДОВАТЕЛЬНОСТИ КАРТ", flush=True)
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
DATA_FILE = "timing_data_full.json"
MAX_RECORDS = 10000
CHECK_INTERVAL = 10

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://1xlite-36553.pro/ru/live/twentyone/2092323-21-classics",
    "Cookie": "platform_type=desktop; SESSION=34219176f69eace1b636911e2de9a15e; lng=ru; cookies_agree_type=3; tzo=3; is12h=0; auid=uaJbk2qIgo2M+6ofAxNqAg==; _ym_isad=2; mdd=1; _ga_7JGWL9SV66=GS2.1.s1787337341$o4$g1$t1787337359$j42$l0$h1608459194; window_width=150; referral_values=%7B%22type%22%3A%22reflinkid%22%2C%22val%22%3A%22s_50970m_355c_%22%2C%22additional%22%3A%7B%22name_tag%22%3A%22tag%22%7D%7D; fatman_uuid=45f69ff0-ecb1-67d4-3ff2-3a45baafc739; che_g=777dc1b9-efbf-4728-947a-4a2992ef6da5; sh.session.id=684214c4-f09e-42da-9c1a-ea61b9aca91b; _ym_uid=1786989905737338437; _ym_d=1786989905; _ga=GA1.1.547872848.1786989906"
}

SUITS_NAMES = {0: "♠️", 1: "♣️", 2: "♦️", 3: "♥️"}
RANKS = {1: "A", 2: "2", 3: "3", 4: "4", 5: "5", 6: "6", 7: "7", 8: "8", 9: "9", 10: "10", 11: "J", 12: "Q", 13: "K"}

finished_games = set()

# =====================================================================
# ФУНКЦИИ ДЛЯ РАБОТЫ С ФАЙЛОМ
# =====================================================================
def load_full_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return []
    return []

def save_full_data(record):
    data = load_full_data()
    
    existing_index = None
    for i, r in enumerate(data):
        if r.get("game_id") == record["game_id"]:
            existing_index = i
            break
    
    if existing_index is not None:
        data[existing_index] = record
        print(f"🔄 Обновлена запись для игры {record['game_id']}", flush=True)
    else:
        data.append(record)
        print(f"💾 Новая запись для игры {record['game_id']}", flush=True)
    
    if len(data) > MAX_RECORDS:
        data = data[-MAX_RECORDS:]
    
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

# =====================================================================
# ФУНКЦИИ ДЛЯ РАБОТЫ С API
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
                    if game_id:
                        active_games.append(game)
            return active_games
        else:
            print(f"⚠️ Статус API: {response.status_code}", flush=True)
            return []
    except requests.exceptions.ConnectionError:
        print(f"❌ Ошибка подключения к API! Проверь зеркало.", flush=True)
        return []
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
            print(f"⚠️ Статус игры {game_id}: {response.status_code}", flush=True)
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

def analyze_full(game_id, player_cards, dealer_cards, latency, start_time, end_time, state):
    timestamp = datetime.fromtimestamp(start_time, MOSCOW_TZ)
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
        "latency_ms": round(latency, 2),
        "state": state,
        "player_score": player_score,
        "dealer_score": dealer_score,
        "player_cards": [{"rank": RANKS.get(c.get("CV", 0), "?"), "suit": SUITS_NAMES.get(c.get("CS", 0), "?")} for c in player_cards],
        "dealer_cards": [{"rank": RANKS.get(c.get("CV", 0), "?"), "suit": SUITS_NAMES.get(c.get("CS", 0), "?")} for c in dealer_cards],
        "sequence": sequence
    }
    
    # Исправленная строка с выводом последовательности
    seq_str = ', '.join([f"{c['who']}{c['position']}:{c['rank']}{c['suit']}" for c in sequence])
    print(f"🃏 Игра {game_id}: {len(player_cards)} карт игрока, {len(dealer_cards)} карт дилера, задержка={latency:.2f}мс", flush=True)
    print(f"   Последовательность: {seq_str}", flush=True)
    
    save_full_data(record)

# =====================================================================
# ОСНОВНОЙ ЦИКЛ
# =====================================================================
def main():
    print("🔄 АНАЛИЗАТОР ПОСЛЕДОВАТЕЛЬНОСТИ ЗАПУЩЕН", flush=True)
    print(f"📁 Данные сохраняются в {DATA_FILE}", flush=True)
    print(f"⏱️ Интервал опроса: {CHECK_INTERVAL} сек", flush=True)
    print("📌 Для выхода нажми Ctrl+C", flush=True)
    print("=" * 60, flush=True)
    
    existing_data = load_full_data()
    print(f"📊 Уже собрано записей: {len(existing_data)}", flush=True)
    print("=" * 60, flush=True)
    
    while True:
        try:
            active_games = get_active_games()
            
            if not active_games:
                print("💤 Нет активных игр", flush=True)
                time.sleep(CHECK_INTERVAL)
                continue
            
            for game in active_games:
                game_id = str(game.get("id"))
                
                if game_id in finished_games:
                    continue
                
                data, latency, start_time, end_time = get_game_data(game_id)
                if not data:
                    continue
                
                player_cards, dealer_cards, state = parse_cards_and_state(data)
                
                if player_cards or dealer_cards:
                    analyze_full(game_id, player_cards, dealer_cards, latency, start_time, end_time, state)
                    
                    if state in ["4", "5"]:
                        finished_games.add(game_id)
                        print(f"🏁 Игра {game_id} завершена (state={state}), сохранена финальная запись", flush=True)
                else:
                    print(f"⏳ Игра {game_id}: карт ещё нет", flush=True)
                
                time.sleep(0.5)
            
            if len(finished_games) > 500:
                finished_games.clear()
                print("🗑️ Кэш завершённых игр очищен", flush=True)
            
            time.sleep(CHECK_INTERVAL)
            
        except KeyboardInterrupt:
            print("🛑 Анализатор остановлен", flush=True)
            data_count = len(load_full_data())
            print(f"📊 Всего собрано записей: {data_count}", flush=True)
            break
        except Exception as e:
            print(f"❌ Ошибка: {e}", flush=True)
            time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()