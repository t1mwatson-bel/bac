import time
import json
import requests
from datetime import datetime, timedelta
import pytz
import os

MOSCOW_TZ = pytz.timezone('Europe/Moscow')

# =====================================================================
# ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ
# =====================================================================
BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    BOT_TOKEN = os.getenv('BOT_TOKEN_PROGNOZ')

CHANNEL_STATS = os.getenv('CHANNEL_STATS_ID')
CHANNEL_PROGNOZ = os.getenv('CHANNEL_PROGNOZ_ID')

# =====================================================================
# ФУНКЦИИ ДЛЯ СБОРА ДАННЫХ
# =====================================================================
def get_game_data(game_id):
    """Получает данные игры и засекает время"""
    url = f"https://1xlite-36553.pro/service-api/LiveFeed/GetGameZip?id={game_id}&isSubGames=true&GroupEvents=true&countevents=250&grMode=4&partner=7&topGroups=&country=190&marketType=1&isNewBuilder=true"
    try:
        start_time = time.time()  # Засекаем время ДО запроса
        response = requests.get(url, headers=HEADERS, timeout=5)
        end_time = time.time()    # Засекаем время ПОСЛЕ ответа
        latency = (end_time - start_time) * 1000  # миллисекунды

        if response.status_code == 200:
            data = response.json()
            return data, latency, start_time, end_time
        else:
            return None, None, None, None
    except Exception as e:
        print(f"❌ Ошибка: {e}", flush=True)
        return None, None, None, None

def parse_cards(data):
    """Извлекает карты игрока и дилера из JSON"""
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

def analyze_timing(cards, latency, start_time, end_time):
    """Анализирует время и карты"""
    if not cards:
        return

    # Формируем данные для записи
    timestamp = datetime.fromtimestamp(start_time, MOSCOW_TZ)
    utc_timestamp = datetime.fromtimestamp(start_time, pytz.UTC)
    ms = int((start_time % 1) * 1000)

    print(f"🕐 Время (МСК): {timestamp.strftime('%H:%M:%S.%f')[:-3]}")
    print(f"🕐 Время (UTC): {utc_timestamp.strftime('%H:%M:%S.%f')[:-3]}")
    print(f"⏱️ Задержка API: {latency:.2f} мс")
    print(f"🃏 Карты: {len(cards)}")

    for card in cards:
        cs = card.get("CS", 0)
        cv = card.get("CV", 0)
        suit = {0: "♠️", 1: "♣️", 2: "♦️", 3: "♥️"}.get(cs, "?")
        rank = {1: "A", 2: "2", 3: "3", 4: "4", 5: "5", 6: "6", 7: "7", 8: "8", 9: "9", 10: "10", 11: "J", 12: "Q", 13: "K"}.get(cv, "?")
        print(f"   {rank}{suit}")

# =====================================================================
# ОСНОВНОЙ ЦИКЛ АНАЛИЗА
# =====================================================================
def main():
    print("🔍 АНАЛИЗАТОР ВРЕМЕНИ И КАРТ ЗАПУЩЕН", flush=True)

    game_id = "746153541"  # ID игры для анализа

    while True:
        try:
            data, latency, start_time, end_time = get_game_data(game_id)
            if not data:
                print("⏳ Ждем данные...", flush=True)
                time.sleep(2)
                continue

            player_cards, dealer_cards, state = parse_cards(data)

            if player_cards:
                print("=" * 60)
                analyze_timing(player_cards, latency, start_time, end_time)
                print("=" * 60)

            time.sleep(1)

        except Exception as e:
            print(f"❌ Ошибка: {e}", flush=True)
            time.sleep(5)

if __name__ == "__main__":
    main()