import os
import sys
import requests
import json
import re
import time
from datetime import datetime, timedelta
import pytz

# =====================================================================
# ПРИНУДИТЕЛЬНЫЙ ВЫВОД ЛОГОВ
# =====================================================================
sys.stdout.flush()
print("=" * 60, flush=True)
print("🃏 ПРОГНОЗИСТ ПО ЗАДЕРЖКЕ (ПРОВЕРКА ИЗ КАНАЛА)", flush=True)
print("=" * 60, flush=True)

# =====================================================================
# ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ
# =====================================================================
BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    BOT_TOKEN = os.getenv('BOT_TOKEN_PROGNOZ')

CHANNEL_STATS = os.getenv('CHANNEL_STATS')
CHANNEL_PROGNOZ = os.getenv('CHANNEL_PROGNOZ')

print("🔍 ДИАГНОСТИКА ПЕРЕМЕННЫХ:", flush=True)
print(f"BOT_TOKEN: {BOT_TOKEN[:5] if BOT_TOKEN else 'НЕ ЗАДАН'}", flush=True)
print(f"CHANNEL_STATS: {CHANNEL_STATS if CHANNEL_STATS else 'НЕ ЗАДАН'}", flush=True)
print(f"CHANNEL_PROGNOZ: {CHANNEL_PROGNOZ if CHANNEL_PROGNOZ else 'НЕ ЗАДАН'}", flush=True)
print("=" * 60, flush=True)

if not BOT_TOKEN or not CHANNEL_STATS or not CHANNEL_PROGNOZ:
    print("❌ ОШИБКА: переменные окружения не заданы!", flush=True)
    exit(1)

print("✅ Все переменные заданы!", flush=True)

# =====================================================================
# НАСТРОЙКИ
# =====================================================================
MOSCOW_TZ = pytz.timezone('Europe/Moscow')
BASE_URL = "https://1xlite-36553.pro"
OFFSET_FILE = "offset_latency.txt"
HISTORY_FILE = "history_latency.json"
MAX_HISTORY = 200
PROCESSED_GAMES = set()
LAST_PREDICT_TIME = 0
PREDICT_INTERVAL = 3
TIMEOUT_SECONDS = 600  # 10 минут

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": f"{BASE_URL}/ru/live/twentyone/2092323-21-classics",
    "Cookie": "platform_type=desktop; SESSION=34219176f69eace1b636911e2de9a15e; lng=ru; cookies_agree_type=3; tzo=3; is12h=0; auid=uaJbk2qIgo2M+6ofAxNqAg==; _ym_isad=2; mdd=1; _ga_7JGWL9SV66=GS2.1.s1787337341$o4$g1$t1787337359$j42$l0$h1608459194; window_width=150; referral_values=%7B%22type%22%3A%22reflinkid%22%2C%22val%22%3A%22s_50970m_355c_%22%2C%22additional%22%3A%7B%22name_tag%22%3A%22tag%22%7D%7D; fatman_uuid=45f69ff0-ecb1-67d4-3ff2-3a45baafc739; che_g=777dc1b9-efbf-4728-947a-4a2992ef6da5; sh.session.id=684214c4-f09e-42da-9c1a-ea61b9aca91b; _ym_uid=1786989905737338437; _ym_d=1786989905; _ga=GA1.1.547872848.1786989906"
}

SUITS_NAMES = {0: "♠️", 1: "♣️", 2: "♦️", 3: "♥️"}

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

def send_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHANNEL_PROGNOZ, "text": text, "parse_mode": "HTML"}
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            return response.json()["result"]["message_id"]
        else:
            print(f"❌ Ошибка отправки: {response.status_code} - {response.text}", flush=True)
            return None
    except Exception as e:
        print(f"❌ Ошибка отправки: {e}", flush=True)
        return None

def edit_message(message_id, text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText"
    payload = {"chat_id": CHANNEL_PROGNOZ, "message_id": message_id, "text": text, "parse_mode": "HTML"}
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            return True
        else:
            print(f"❌ Ошибка редактирования: {response.status_code} - {response.text}", flush=True)
            return False
    except Exception as e:
        print(f"❌ Ошибка редактирования: {e}", flush=True)
        return False

def send_startup_message():
    now = datetime.now(MOSCOW_TZ)
    msg = f"🚀 <b>БОТ ПРОГНОЗИСТ ЗАПУЩЕН</b>\n"
    msg += f"⏰ Время: {now.strftime('%d.%m.%Y %H:%M:%S')} (МСК)\n"
    msg += f"🤖 Статус: Активен\n"
    msg += f"📌 Режим: Прогноз по задержке → игрок\n"
    msg += f"🔄 Версия: 5.1 (таймаут 10 мин)"
    send_message(msg)
    print(f"📤 Приветствие отправлено в канал", flush=True)

# =====================================================================
# ПАРСИНГ ИЗ КАНАЛА
# =====================================================================
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
        
        player_cards_match = re.search(r'\(([^)]+)\)', player_part)
        if not player_cards_match:
            return None
        
        player_cards_str = player_cards_match.group(1).strip()
        
        def parse_cards(cards_str):
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
        
        player_cards = parse_cards(player_cards_str)
        
        return {
            "number": game_number,
            "player_cards": player_cards,
            "text": text
        }
    except Exception as e:
        print(f"❌ Ошибка парсинга: {e}", flush=True)
        return None

# =====================================================================
# ФУНКЦИИ API ДЛЯ ПРОГНОЗА
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
        print(f"❌ Ошибка: {e}", flush=True)
        return []

def get_game_data(game_id):
    url = f"{BASE_URL}/service-api/LiveFeed/GetGameZip?id={game_id}&isSubGames=true&GroupEvents=true&countevents=250&grMode=4&partner=7&topGroups=&country=190&marketType=1&isNewBuilder=true"
    try:
        start_time = time.time()
        response = requests.get(url, headers=HEADERS, timeout=5)
        end_time = time.time()
        latency = (end_time - start_time) * 1000
        if response.status_code == 200:
            return response.json(), latency
        else:
            return None, None
    except Exception as e:
        print(f"❌ Ошибка игры {game_id}: {e}", flush=True)
        return None, None

def predict_suit_by_latency(latency):
    if 93 <= latency < 96:
        return "♥️"
    elif 96 <= latency < 99:
        return "♠️"
    elif 99 <= latency < 102:
        return "♦️"
    elif 102 <= latency < 105:
        return "♣️"
    else:
        return None

def get_game_number():
    now = datetime.now(MOSCOW_TZ)
    start = now.replace(hour=3, minute=0, second=0, microsecond=0)
    if now < start:
        start = start - timedelta(days=1)
    diff_minutes = (now - start).total_seconds() / 60
    game_number = int(diff_minutes) // 2 % 720 + 1
    return game_number

# =====================================================================
# ОСНОВНЫЕ ФУНКЦИИ
# =====================================================================
def save_history(history):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)

def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return []
    return []

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

def load_recent_messages():
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    params = {"chat_id": CHANNEL_STATS, "limit": 100}
    try:
        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            messages = []
            for update in data.get("result", []):
                post = update.get("channel_post")
                if post and post.get("text"):
                    messages.append(post.get("text"))
            return messages
    except Exception as e:
        print(f"❌ Ошибка загрузки истории: {e}", flush=True)
    return []

def clean_memory(history):
    if len(history) > MAX_HISTORY:
        history = history[-MAX_HISTORY:]
        print(f"🧹 Очистка кэша: оставлено {len(history)} записей", flush=True)
    return history

# =====================================================================
# ПРОВЕРКА РЕЗУЛЬТАТА (С ТАЙМАУТОМ 10 МИНУТ)
# =====================================================================
def check_results(history, all_messages):
    current_time = time.time()
    
    for entry in history:
        if entry.get("status") != "pending":
            continue
        
        target = entry.get("target")
        predicted_suit = entry.get("suit")
        from_game = entry.get("from_game")
        message_id = entry.get("message_id")
        created_time = entry.get("time", "")
        
        if not predicted_suit or not message_id:
            continue
        
        # === ТАЙМАУТ 10 МИНУТ ===
        try:
            created_ts = datetime.fromisoformat(created_time).timestamp()
        except:
            created_ts = 0
        
        if current_time - created_ts > TIMEOUT_SECONDS:
            print(f"⏰ Таймаут! Прогноз #N{from_game} → #N{target} висит больше 10 минут", flush=True)
            
            original_text = f"🔮 <b>ПРОГНОЗ ПО ЗАДЕРЖКЕ</b>\n"
            original_text += f"📊 От игры: #N{from_game}\n"
            original_text += f"🃏 Масть игрока: {predicted_suit}\n"
            original_text += f"🎯 Целевая игра: #N{target}\n"
            original_text += f"📈 3 игры догон\n"
            original_text += f"⏰ {entry.get('time', '')[:16]}"
            result_text = f"\n\n⏰ <b>ТАЙМАУТ</b> (игра не завершена за 10 минут)"
            
            edit_message(message_id, original_text + result_text)
            entry["status"] = "lose"
            save_history(history)
            print(f"❌ Прогноз #N{from_game} → #N{target} НЕ ЗАШЕЛ (таймаут)", flush=True)
            continue
        
        max_games_to_check = 4
        
        for i in range(max_games_to_check):
            game_to_check = target + i
            
            # Ищем завершенную игру в канале
            game_msg = None
            for msg in all_messages:
                if f"#N{game_to_check}" in msg and ('✅' in msg or '🔰' in msg):
                    game_msg = msg
                    break
            
            if not game_msg:
                print(f"⏳ Ждем завершенную игру #N{game_to_check} для проверки масти {predicted_suit}", flush=True)
                break
            
            game_data = parse_game_from_text(game_msg)
            if not game_data:
                print(f"⚠️ Не удалось распарсить #N{game_to_check}", flush=True)
                continue
            
            # Проверяем масть у игрока
            suit_found = False
            player_cards = game_data.get("player_cards", [])
            
            if not player_cards:
                print(f"⚠️ Нет карт игрока в #N{game_to_check}", flush=True)
                continue
            
            print(f"   Проверка #N{game_to_check}: {len(player_cards)} карт игрока", flush=True)
            for card in player_cards:
                print(f"      Карта: {card['rank']}{card['suit']}", flush=True)
                if card.get("suit") == predicted_suit:
                    suit_found = True
                    print(f"   ✅ Найдена масть {predicted_suit} у игрока в карте {card['rank']}{card['suit']}", flush=True)
                    break
            
            if suit_found:
                print(f"🎯 МАСТЬ {predicted_suit} НАЙДЕНА в игре #N{game_to_check}!", flush=True)
                
                dogon_number = i
                
                original_text = f"🔮 <b>ПРОГНОЗ ПО ЗАДЕРЖКЕ</b>\n"
                original_text += f"📊 От игры: #N{from_game}\n"
                original_text += f"🃏 Масть игрока: {predicted_suit}\n"
                original_text += f"🎯 Целевая игра: #N{target}\n"
                original_text += f"📈 3 игры догон\n"
                original_text += f"⏰ {entry.get('time', '')[:16]}"
                
                if dogon_number == 0:
                    result_text = f"\n\n✅ <b>ЗАШЛО</b> в целевой игре: #N{game_to_check}"
                else:
                    result_text = f"\n\n✅ <b>ЗАШЛО</b> на догоне {dogon_number}: #N{game_to_check}"
                
                edit_message(message_id, original_text + result_text)
                entry["status"] = "win"
                entry["result_game"] = game_to_check
                entry["dogon"] = dogon_number
                save_history(history)
                
                print(f"✅ Прогноз #N{from_game} → #N{target} ЗАШЕЛ на игре #N{game_to_check}", flush=True)
                return
            
            if i == max_games_to_check - 1:
                print(f"❌ Масть {predicted_suit} НЕ НАЙДЕНА за {max_games_to_check} игр", flush=True)
                
                original_text = f"🔮 <b>ПРОГНОЗ ПО ЗАДЕРЖКЕ</b>\n"
                original_text += f"📊 От игры: #N{from_game}\n"
                original_text += f"🃏 Масть игрока: {predicted_suit}\n"
                original_text += f"🎯 Целевая игра: #N{target}\n"
                original_text += f"📈 3 игры догон\n"
                original_text += f"⏰ {entry.get('time', '')[:16]}"
                result_text = f"\n\n❌ <b>НЕ ЗАШЛО</b> (проверено {max_games_to_check} игр)"
                
                edit_message(message_id, original_text + result_text)
                entry["status"] = "lose"
                save_history(history)
                
                print(f"❌ Прогноз #N{from_game} → #N{target} НЕ ЗАШЕЛ", flush=True)
                return

# =====================================================================
# ОСНОВНОЙ ЦИКЛ
# =====================================================================
def main():
    global LAST_PREDICT_TIME
    
    print("🔄 ЗАПУСК ПРОГНОЗИСТА (ПРОВЕРКА ИЗ КАНАЛА)...", flush=True)
    print("=" * 60, flush=True)
    
    send_startup_message()
    
    offset = get_offset()
    history = load_history()
    
    print("📥 Загрузка последних сообщений из канала...", flush=True)
    all_messages = load_recent_messages()
    print(f"📥 Загружено сообщений: {len(all_messages)}", flush=True)
    
    check_results(history, all_messages)
    
    last_cleanup_time = time.time()
    last_forced_check = time.time()
    
    print("🚀 БОТ ГОТОВ К РАБОТЕ!", flush=True)
    print("=" * 60, flush=True)
    print("", flush=True)
    
    while True:
        try:
            current_time = time.time()
            
            if current_time - last_cleanup_time > 3600:
                history = clean_memory(history)
                save_history(history)
                last_cleanup_time = current_time
            
            # ПРИНУДИТЕЛЬНАЯ ПРОВЕРКА КАЖДЫЕ 30 СЕКУНД
            if current_time - last_forced_check > 30:
                print("🔄 Принудительная проверка ожидающих прогнозов...", flush=True)
                check_results(history, all_messages)
                last_forced_check = current_time
            
            # Получаем обновления из канала
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
                
                game_id_match = re.search(r'#N(\d+)', text)
                if not game_id_match:
                    continue
                game_number = int(game_id_match.group(1))
                
                all_messages.append(text)
                if len(all_messages) > 500:
                    all_messages = all_messages[-500:]
                
                print(f"📥 Получена игра #N{game_number}", flush=True)
                print(f"📝 Текст: {text}", flush=True)
                
                # ЕСЛИ ИГРА ЗАВЕРШЕНА - ПРОВЕРЯЕМ РЕЗУЛЬТАТ
                if '✅' in text or '🔰' in text:
                    print(f"✅ #N{game_number} завершена - проверяем результаты", flush=True)
                    check_results(history, all_messages)
                    continue
                
                # НЕЗАВЕРШЕННАЯ ИГРА - ДЕЛАЕМ ПРОГНОЗ
                print(f"⏭️ #N{game_number} не завершена (нет ✅ или 🔰)", flush=True)
                
                # НЕ ДАЁМ НОВЫЙ ПРОГНОЗ, ПОКА ЕСТЬ PENDING
                pending_exists = any(h.get('status') == 'pending' for h in history)
                if pending_exists:
                    print(f"⏳ Есть ожидающий прогноз, новый не даём", flush=True)
                    continue
                
                if game_number in PROCESSED_GAMES:
                    print(f"⏭️ #N{game_number} уже обработана", flush=True)
                    continue
                
                # =====================================================
                # ДЕЛАЕМ ПРОГНОЗ ПО ЗАДЕРЖКЕ (API)
                # =====================================================
                games = get_active_games()
                if not games:
                    continue
                
                current_game_num = get_game_number()
                target_game = current_game_num + 1
                
                latency = None
                for game in games:
                    game_id = str(game.get("id"))
                    data, latency = get_game_data(game_id)
                    if data:
                        break
                
                if latency is None:
                    continue
                
                predicted_suit = predict_suit_by_latency(latency)
                if predicted_suit is None:
                    print(f"⏭️ Задержка {latency:.2f} мс — нет прогноза", flush=True)
                    continue
                
                if current_time - LAST_PREDICT_TIME < PREDICT_INTERVAL:
                    print(f"⏳ Интервал: {int(current_time - LAST_PREDICT_TIME)} сек < {PREDICT_INTERVAL} сек", flush=True)
                    continue
                
                msg = f"🔮 <b>ПРОГНОЗ ПО ЗАДЕРЖКЕ</b>\n"
                msg += f"📊 От игры: #N{current_game_num}\n"
                msg += f"🃏 Масть игрока: {predicted_suit}\n"
                msg += f"🎯 Целевая игра: #N{target_game}\n"
                msg += f"📈 3 игры догон\n"
                msg += f"⏰ {datetime.now(MOSCOW_TZ).strftime('%H:%M:%S')}"
                
                message_id = send_message(msg)
                if message_id:
                    print(f"✅ ПРОГНОЗ ОТПРАВЛЕН: #N{target_game} → масть {predicted_suit}", flush=True)
                    LAST_PREDICT_TIME = current_time
                    PROCESSED_GAMES.add(game_number)
                    
                    history.append({
                        "from_game": current_game_num,
                        "target": target_game,
                        "suit": predicted_suit,
                        "card": f"{predicted_suit}",
                        "time": datetime.now(MOSCOW_TZ).isoformat(),
                        "status": "pending",
                        "message_id": message_id
                    })
                    save_history(history)
                    
                    pending_count = len([h for h in history if h.get('status') == 'pending'])
                    print(f"📊 Ожидающих прогнозов: {pending_count}", flush=True)
            
            check_results(history, all_messages)
            history = clean_memory(history)
            save_history(history)
            
            if len(PROCESSED_GAMES) > 500:
                PROCESSED_GAMES.clear()
            
            time.sleep(1)
            
        except Exception as e:
            print(f"❌ Ошибка: {e}", flush=True)
            import traceback
            traceback.print_exc()
            time.sleep(30)

if __name__ == "__main__":
    main()