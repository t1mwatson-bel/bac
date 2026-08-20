import os
import re
import time
import requests
from datetime import datetime

BOT_TOKEN = os.getenv('BOT_TOKEN')
CHANNEL_STATS = os.getenv('CHANNEL_STATS_ID')
CHANNEL_PROGNOZ = os.getenv('CHANNEL_PROGNOZ_ID')

if not BOT_TOKEN or not CHANNEL_STATS or not CHANNEL_PROGNOZ:
    print("❌ Ошибка: переменные не заданы!")
    exit(1)

offset = 0
PROCESSED = set()

def get_updates():
    global offset
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    params = {"offset": offset, "timeout": 30}
    try:
        r = requests.get(url, params=params, timeout=35)
        return r.json()
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        return {}

def send_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": CHANNEL_PROGNOZ, "text": text})
        return r.status_code == 200
    except Exception as e:
        print(f"❌ Ошибка отправки: {e}")
        return False

def parse_game(text):
    try:
        game_num = int(re.search(r'#N(\d+)', text).group(1))
        parts = text.split('-')
        player = re.search(r'(\d+)\(([^)]+)\)', parts[0])
        dealer = re.search(r'(\d+)\(([^)]+)\)', parts[1])
        if not player or not dealer:
            return None
        player_cards = re.findall(r'([AKQJ]|10|\d)([♠♣♦♥])', player.group(2))
        dealer_cards = re.findall(r'([AKQJ]|10|\d)([♠♣♦♥])', dealer.group(2))
        return {
            "number": game_num,
            "player": [{"rank": c[0], "suit": c[1]} for c in player_cards],
            "dealer": [{"rank": c[0], "suit": c[1]} for c in dealer_cards]
        }
    except:
        return None

def get_highest(cards):
    order = {"6":1, "7":2, "8":3, "9":4, "10":0, "J":5, "Q":6, "K":7, "A":8}
    filtered = [c for c in cards if c["rank"] != "10"]
    if not filtered:
        return None
    return max(filtered, key=lambda c: order.get(c["rank"], 0))

def predict(game):
    if game["number"] % 2 != 0:
        return None
    result = {}
    for who, cards in [("player", game["player"]), ("dealer", game["dealer"])]:
        h = get_highest(cards)
        if h:
            pos = cards.index(h) + 1
            rank = {"6":"J","7":"Q","8":"K","9":"A","J":"J","Q":"Q","K":"K","A":"A"}.get(h["rank"], h["rank"])
            suit = {1:"♣",2:"♦",3:"♥",4:"♠"}.get(pos, "?")
            result[who] = f"{rank}{suit}"
    target = game["number"] + 10
    while target % 2 != 0:
        target += 1
    result["target"] = target
    return result

while True:
    data = get_updates()
    for update in data.get("result", []):
        offset = update["update_id"] + 1
        post = update.get("channel_post") or update.get("edited_channel_post")
        if not post or str(post.get("chat", {}).get("id")) != str(CHANNEL_STATS):
            continue
        text = post.get("text", "")
        if "✅" not in text and "🔰" not in text:
            continue
        if "#N" not in text:
            continue
        game = parse_game(text)
        if not game or game["number"] in PROCESSED:
            continue
        if game["number"] % 2 != 0:
            continue
        p = predict(game)
        if p:
            msg = f"🔮 ПРОГНОЗ\n#N{game['number']} → #N{p['target']}\n"
            if p.get("player"):
                msg += f"🃏 Игрок: {p['player']}\n"
            if p.get("dealer"):
                msg += f"🃏 Дилер: {p['dealer']}"
            if send_message(msg):
                PROCESSED.add(game["number"])
                print(f"✅ Прогноз отправлен: #N{p['target']}")
    time.sleep(5)