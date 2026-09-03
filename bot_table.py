import os
import sys
import json
import time
import requests
import pytz
import re

from datetime import datetime, timedelta

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    BOT_TOKEN = os.getenv("BOT_TOKEN_PROGNOZ")

CHANNEL_PROGNOZ = os.getenv("CHAT_ID_21")
if not CHANNEL_PROGNOZ:
    CHANNEL_PROGNOZ = os.getenv("CHANNEL_PROGNOZ")

CHANNEL_STATS = os.getenv("CHANNEL_STATS")

if not BOT_TOKEN or not CHANNEL_PROGNOZ:
    print("❌ Ошибка: BOT_TOKEN или CHANNEL_PROGNOZ не заданы!", flush=True)
    sys.exit(1)

print(f"✅ BOT_TOKEN: {BOT_TOKEN[:5]}...", flush=True)
print(f"✅ CHANNEL_PROGNOZ: {CHANNEL_PROGNOZ}", flush=True)
if CHANNEL_STATS:
    print(f"✅ CHANNEL_STATS: {CHANNEL_STATS}", flush=True)

MOSCOW_TZ = pytz.timezone("Europe/Moscow")
BASE_URL = "https://1xlite-36553.pro"
LEAGUE_ID = 1643503
DATA_FILE = "twentyone_data_full.json"
PREDICTIONS_FILE = "twentyone_predictions.json"
MAX_HISTORY_GAMES = 1000
DOGON_GAMES = 4
TARGET_RANKS = {"J", "Q", "K", "A"}
SUIT_PAIRS = {"♥": "♠", "♠": "♥", "♣": "♦", "♦": "♣"}
POLL_INTERVAL = 2.0

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": f"{BASE_URL}/ru/live/twentyone/1643503-twentyone-game",
    "Cookie": "platform_type=desktop; lng=ru; cookies_agree_type=3; tzo=3; is12h=0"
}
SESSION = requests.Session()
SESSION.headers.update(HEADERS)

SUITS_NAMES = {0: "♠️", 1: "♣️", 2: "♦️", 3: "♥️"}
RANKS = {2: "2", 3: "3", 4: "4", 5: "5", 6: "6", 7: "7", 8: "8", 9: "9", 10: "10", 11: "J", 12: "Q", 13: "K", 14: "A"}

def normalize_suit(v):
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, int):
        return SUITS_NAMES.get(v)
    t = str(v).strip().replace("\ufe0f", "")
    if t in ["0", "♠", "spade", "spades", "s"]:
        return "♠"
    if t in ["1", "♣", "club", "clubs", "c"]:
        return "♣"
    if t in ["2", "♦", "diamond", "diamonds", "d"]:
        return "♦"
    if t in ["3", "♥", "heart", "hearts", "h"]:
        return "♥"
    return None

def normalize_rank(v):
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, int):
        return RANKS.get(v)
    t = str(v).strip().upper().replace("А", "A")
    if t in {"2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"}:
        return t
    try:
        return RANKS.get(int(t))
    except:
        return None

def card_to_text(c):
    if not c:
        return ""
    r = normalize_rank(c.get("rank"))
    s = normalize_suit(c.get("suit"))
    return f"{r}{s}\ufe0f" if r and s else ""

def deep_find_value(obj, keys):
    wanted = {str(x).lower() for x in keys}
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).lower() in wanted:
                return v
        for v in obj.values():
            r = deep_find_value(v, keys)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for i in obj:
            r = deep_find_value(i, keys)
            if r is not None:
                return r
    return None

def extract_card_from_dict(obj):
    if not isinstance(obj, dict):
        return None
    rank = None
    suit = None
    rank_keys = {"rank", "value", "v", "cardvalue", "card_value", "nominal", "denomination"}
    suit_keys = {"suit", "s", "card_suit", "cardsuit", "mast", "color"}
    for k, v in obj.items():
        kl = str(k).strip().lower()
        if kl in rank_keys:
            c = normalize_rank(v)
            if c:
                rank = c
        if kl in suit_keys:
            c = normalize_suit(v)
            if c:
                suit = c
    if rank and suit:
        return {"rank": rank, "suit": f"{suit}\ufe0f"}
    return None

def classify_key(k):
    k = str(k).strip().lower()
    if k in ["p", "pcards", "playercards"] or k.startswith("player") or "playercard" in k:
        return "player"
    if k in ["d", "dcards", "dealercards"] or k.startswith("dealer") or "dealercard" in k:
        return "dealer"
    return None

def find_cards_recursive(obj, context=None, player=None, dealer=None):
    if player is None:
        player = []
    if dealer is None:
        dealer = []
    if isinstance(obj, dict):
        own = extract_card_from_dict(obj)
        if own:
            if context == "player":
                player.append(own)
            elif context == "dealer":
                dealer.append(own)
        for k, v in obj.items():
            ctx = context
            c = classify_key(k)
            if c:
                ctx = c
            kl = str(k).strip().lower()
            if kl in {"p", "p1", "p2", "p3", "p4", "p5", "p6", "p7", "p8", "p9"}:
                ctx = "player"
            if kl in {"d", "d1", "d2", "d3", "d4", "d5", "d6", "d7", "d8", "d9"}:
                ctx = "dealer"
            find_cards_recursive(v, ctx, player, dealer)
    elif isinstance(obj, list):
        for i in obj:
            find_cards_recursive(i, context, player, dealer)
    return player, dealer

def find_card_lists_fallback(obj):
    pc, dc = [], []
    def walk(v, ctx=None):
        if isinstance(v, dict):
            for k, item in v.items():
                kl = str(k).strip().lower()
                nctx = ctx
                if "player" in kl or kl in {"p", "p1", "p2", "p3", "p4", "p5", "p6"}:
                    nctx = "player"
                if "dealer" in kl or kl in {"d", "d1", "d2", "d3", "d4", "d5", "d6"}:
                    nctx = "dealer"
                if isinstance(item, list):
                    cards = []
                    for x in item:
                        c = extract_card_from_dict(x)
                        if c:
                            cards.append(c)
                    if cards:
                        if nctx == "player":
                            pc.append(cards)
                        elif nctx == "dealer":
                            dc.append(cards)
                walk(item, nctx)
        elif isinstance(v, list):
            for i in v:
                walk(i, ctx)
    walk(obj)
    player = max(pc, key=len) if pc else []
    dealer = max(dc, key=len) if dc else []
    return player, dealer

def clean_cards(cards):
    res = []
    for c in cards:
        if not c:
            continue
        r = normalize_rank(c.get("rank"))
        s = normalize_suit(c.get("suit"))
        if r and s:
            res.append({"rank": r, "suit": f"{s}\ufe0f"})
    return res

def parse_game_data(game_id, raw):
    if not raw:
        return None
    player, dealer = find_cards_recursive(raw)
    player = clean_cards(player)
    dealer = clean_cards(dealer)
    if not player:
        p2, d2 = find_card_lists_fallback(raw)
        if p2:
            player = clean_cards(p2)
        if d2:
            dealer = clean_cards(d2)
    if not player:
        return None
    now = datetime.now(MOSCOW_TZ)
    state = deep_find_value(raw, ["state", "STATE", "status", "STATUS"])
    if state is not None:
        state = str(state)
    ps = deep_find_value(raw, ["player_score", "playerscore", "p_score", "pscore", "scorep", "PScore"])
    ds = deep_find_value(raw, ["dealer_score", "dealerscore", "d_score", "dscore", "scored", "DScore"])
    try:
        ps = int(ps) if ps is not None else None
    except:
        ps = None
    try:
        ds = int(ds) if ds is not None else None
    except:
        ds = None
    all_cards = []
    seq = []
    pos = 1
    for i in range(max(len(player), len(dealer))):
        if i < len(player):
            c = player[i]
            all_cards.append(c)
            seq.append({"position": pos, "who": "P", "rank": c["rank"], "suit": c["suit"]})
            pos += 1
        if i < len(dealer):
            c = dealer[i]
            all_cards.append(c)
            seq.append({"position": pos, "who": "D", "rank": c["rank"], "suit": c["suit"]})
            pos += 1
    return {
        "game_id": str(game_id),
        "timestamp_msk": now.strftime("%H:%M:%S.%f")[:-3],
        "state": state,
        "player_score": ps,
        "dealer_score": ds,
        "player_cards": player,
        "dealer_cards": dealer,
        "player_suits": [c["suit"] for c in player],
        "player_ranks": [c["rank"] for c in player],
        "dealer_suits": [c["suit"] for c in dealer],
        "dealer_ranks": [c["rank"] for c in dealer],
        "all_suits": [c["suit"] for c in all_cards],
        "all_ranks": [c["rank"] for c in all_cards],
        "sequence": seq,
        "total_cards": len(all_cards),
        "first_player_card": player[0],
        "id_last_digit": str(game_id)[-1]
    }

def load_json_file(fn, default):
    try:
        if not os.path.exists(fn):
            return default
        with open(fn, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return default

def atomic_save_json(fn, data):
    tmp = fn + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, fn)
        return True
    except:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except:
            pass
        return False

def load_history():
    h = load_json_file(DATA_FILE, [])
    if not isinstance(h, list):
        h = []
    if len(h) > MAX_HISTORY_GAMES:
        h = h[-MAX_HISTORY_GAMES:]
        atomic_save_json(DATA_FILE, h)
    return h

def load_predictions():
    d = load_json_file(PREDICTIONS_FILE, [])
    return d if isinstance(d, list) else []

history = load_history()
predictions = load_predictions()
last_update_id = 0

print(f"📚 Загружено игр: {len(history)}", flush=True)
print(f"🔮 Загружено прогнозов: {len(predictions)}", flush=True)

def find_game_index(h, gid):
    gid = str(gid)
    for i, g in enumerate(h):
        if str(g.get("game_id", "")) == gid:
            return i
    return -1

def game_exists(h, gid):
    return find_game_index(h, gid) != -1

def merge_game_records(old, new):
    m = dict(old)
    for k, v in new.items():
        if v is None:
            continue
        if k in {"player_cards", "dealer_cards", "player_suits", "player_ranks", "dealer_suits", "dealer_ranks", "all_suits", "all_ranks", "sequence"}:
            continue
        m[k] = v
    op = old.get("player_cards", [])
    np = new.get("player_cards", [])
    if len(np) >= len(op):
        m["player_cards"] = np
        m["player_suits"] = new.get("player_suits", [])
        m["player_ranks"] = new.get("player_ranks", [])
    od = old.get("dealer_cards", [])
    nd = new.get("dealer_cards", [])
    if len(nd) >= len(od):
        m["dealer_cards"] = nd
        m["dealer_suits"] = new.get("dealer_suits", [])
        m["dealer_ranks"] = new.get("dealer_ranks", [])
    pc = m.get("player_cards", [])
    dc = m.get("dealer_cards", [])
    all_c = []
    seq = []
    pos = 1
    for i in range(max(len(pc), len(dc))):
        if i < len(pc):
            c = pc[i]
            all_c.append(c)
            seq.append({"position": pos, "who": "P", "rank": c.get("rank"), "suit": c.get("suit")})
            pos += 1
        if i < len(dc):
            c = dc[i]
            all_c.append(c)
            seq.append({"position": pos, "who": "D", "rank": c.get("rank"), "suit": c.get("suit")})
            pos += 1
    m["all_suits"] = [c.get("suit") for c in all_c if c.get("suit")]
    m["all_ranks"] = [c.get("rank") for c in all_c if c.get("rank")]
    m["sequence"] = seq
    m["total_cards"] = len(all_c)
    if pc:
        m["first_player_card"] = pc[0]
    return m

def add_or_update_game(h, game):
    gid = str(game["game_id"])
    idx = find_game_index(h, gid)
    if idx != -1:
        old = h[idx]
        merged = merge_game_records(old, game)
        if json.dumps(old, ensure_ascii=False, sort_keys=True) != json.dumps(merged, ensure_ascii=False, sort_keys=True):
            h[idx] = merged
            atomic_save_json(DATA_FILE, h)
            print(f"🔄 Игра обновлена | ID={gid}", flush=True)
        return False
    h.append(game)
    if len(h) > MAX_HISTORY_GAMES:
        del h[:len(h) - MAX_HISTORY_GAMES]
        print(f"♻️ Удалены старые игры", flush=True)
    atomic_save_json(DATA_FILE, h)
    print(f"💾 Новая игра сохранена | ID={gid} | P1={card_to_text(game.get('first_player_card'))}", flush=True)
    return True

def find_last_same_digit_game(history, current_game_id, digits=1):
    current_game_id = str(current_game_id)
    target = current_game_id[-digits:]
    for game in history:
        old_id = str(game.get("game_id", "")).strip()
        if not old_id or old_id == current_game_id:
            continue
        if old_id[-digits:] != target:
            continue
        p1 = None
        if not p1:
            cards = game.get("player_cards", [])
            if cards:
                p1 = cards[0]
        if not p1:
            p1 = game.get("first_player_card")
        if not p1:
            for c in game.get("sequence", []):
                if c.get("position") == 1 and c.get("who") == "P":
                    p1 = c
                    break
        if p1:
            r = normalize_rank(p1.get("rank"))
            s = normalize_suit(p1.get("suit"))
            if r and s:
                return {"game_id": old_id, "first_player_card": {"rank": r, "suit": f"{s}\ufe0f"}}
    return None

def has_pending_prediction():
    for p in predictions:
        if p.get("status") == "pending":
            return True
    return False

def get_paired_prediction(card):
    if not card:
        return None
    r = normalize_rank(card.get("rank"))
    s = normalize_suit(card.get("suit"))
    if not r or not s or r not in TARGET_RANKS:
        return None
    pair = SUIT_PAIRS.get(s)
    if not pair:
        return None
    return {
        "rank": r,
        "suit": f"{s}\ufe0f",
        "pair_suit": f"{pair}\ufe0f",
        "display": f"{r}{s}\ufe0f{pair}\ufe0f"
    }

def prediction_already_exists(preds, target_id):
    target_id = str(target_id)
    for p in preds:
        if str(p.get("target_game_id", "")) == target_id:
            return True
    return False

def create_prediction_for_game(game_id, game_number):
    game_id = str(game_id)
    if prediction_already_exists(predictions, game_id):
        return None
    main_game = find_last_same_digit_game(history, game_id, 1)
    main_pred = None
    if main_game:
        fc = main_game.get("first_player_card")
        if fc:
            main_pred = get_paired_prediction(fc)
    test_game = find_last_same_digit_game(history, game_id, 2)
    test_pred = None
    if test_game:
        fc = test_game.get("first_player_card")
        if fc:
            test_pred = get_paired_prediction(fc)
    if not main_pred:
        return None
    entry = {
        "target_game_id": game_id,
        "target_number": game_number,
        "main_source_id": main_game.get("game_id") if main_game else None,
        "main_predicted": main_pred["display"],
        "main_rank": main_pred["rank"],
        "main_suit": main_pred["suit"],
        "main_pair_suit": main_pred["pair_suit"],
        "test_predicted": test_pred["display"] if test_pred else "Нет данных",
        "test_rank": test_pred["rank"] if test_pred else None,
        "test_suit": test_pred["suit"] if test_pred else None,
        "test_pair_suit": test_pred["pair_suit"] if test_pred else None,
        "status": "pending",
        "created_at": datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "message_id": None,
        "result": None,
        "result_game_id": None
    }
    predictions.append(entry)
    atomic_save_json(PREDICTIONS_FILE, predictions)
    print(f"🔮 ПРОГНОЗ СОЗДАН | #N{game_number} | ID={game_id}", flush=True)
    print(f"   Основной: {main_pred['display']}", flush=True)
    if test_pred:
        print(f"   Тестовый: {test_pred['display']}", flush=True)
    return entry

def get_game_number():
    now = datetime.now(MOSCOW_TZ)
    start = now.replace(hour=3, minute=0, second=0, microsecond=0)
    if now < start:
        start -= timedelta(days=1)
    return int((now - start).total_seconds() // 60) % 1440 + 1

def telegram_send(text, chat_id=None):
    if not chat_id:
        chat_id = CHANNEL_PROGNOZ
    try:
        r = SESSION.post(f"{TELEGRAM_API}/sendMessage", json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }, timeout=10)
        data = r.json()
        if data.get("ok"):
            return data["result"]["message_id"]
        print(f"❌ Telegram send error: {data}", flush=True)
    except Exception as e:
        print(f"❌ Telegram send exception: {e}", flush=True)
    return None

def telegram_edit(message_id, text, chat_id=None):
    if not message_id:
        return False
    if not chat_id:
        chat_id = CHANNEL_PROGNOZ
    try:
        r = SESSION.post(f"{TELEGRAM_API}/editMessageText", json={
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }, timeout=10)
        if r.json().get("ok"):
            return True
    except:
        pass
    return False

def make_prediction_message(entry):
    text = f"🔮 <b>ПРОГНОЗ</b>\n\n"
    text += f"🎯 Игра: <b>#N{entry.get('target_number')}</b>\n"
    text += f"🃏 Основной: <b>{entry.get('main_predicted')}</b>\n"
    if entry.get("test_predicted") and entry.get("test_predicted") != "Нет данных":
        text += f"🧪 Тестовый: <b>{entry.get('test_predicted')}</b>"
    return text

def make_result_message(entry, main_found, test_found=None):
    text = f"🔮 <b>ПРОГНОЗ</b>\n\n"
    text += f"🎯 Игра: <b>#N{entry.get('target_number')}</b>\n"
    text += f"🃏 Основной: <b>{entry.get('main_predicted')}</b> {'✅' if main_found else '❌'}\n"
    if entry.get("test_predicted") and entry.get("test_predicted") != "Нет данных":
        if test_found is True:
            text += f"🧪 Тестовый: <b>{entry.get('test_predicted')}</b> ✅"
        elif test_found is False:
            text += f"🧪 Тестовый: <b>{entry.get('test_predicted')}</b> ❌"
        else:
            text += f"🧪 Тестовый: <b>{entry.get('test_predicted')}</b> ❓"
    return text

def card_matches_prediction(card, pred):
    if not card or not pred:
        return False
    r = normalize_rank(card.get("rank"))
    s = normalize_suit(card.get("suit"))
    pr = normalize_rank(pred.get("rank"))
    s1 = normalize_suit(pred.get("suit"))
    s2 = normalize_suit(pred.get("pair_suit"))
    if not r or not s:
        return False
    return r == pr and (s == s1 or s == s2)

def game_matches_prediction(game, pred):
    if not game:
        return False
    for c in game.get("player_cards", []):
        if card_matches_prediction(c, pred):
            return True
    for c in game.get("dealer_cards", []):
        if card_matches_prediction(c, pred):
            return True
    return False

def check_prediction_with_cards(entry, cards):
    if not cards:
        return None
    main_found = False
    for c in cards:
        r = c.get("rank")
        s = c.get("suit")
        if r == entry.get("main_rank") and (s == entry.get("main_suit") or s == entry.get("main_pair_suit")):
            main_found = True
            break
    test_found = None
    if entry.get("test_rank") and entry.get("test_suit"):
        test_found = False
        for c in cards:
            r = c.get("rank")
            s = c.get("suit")
            if r == entry.get("test_rank") and (s == entry.get("test_suit") or s == entry.get("test_pair_suit")):
                test_found = True
                break
    return {"main_found": main_found, "test_found": test_found}

def parse_cards_from_message(text):
    m = re.search(r'#N(\d+)', text)
    if not m:
        return None
    game_number = m.group(1)
    cards_raw = re.findall(r'([AJQK2-10]+)([♠♣♦♥])', text)
    if not cards_raw:
        return None
    cards = []
    for r, s in cards_raw:
        rn = normalize_rank(r)
        sn = normalize_suit(s)
        if rn and sn:
            cards.append({"rank": rn, "suit": f"{sn}\ufe0f"})
    return {"game_number": game_number, "cards": cards}

def get_channel_messages(chat_id, limit=50):
    if not chat_id:
        return []
    try:
        r = SESSION.get(f"{TELEGRAM_API}/getUpdates", params={"limit": limit}, timeout=10)
        data = r.json()
        if not data.get("ok"):
            return []
        updates = data.get("result", [])
        messages = []
        for u in updates:
            if "channel_post" in u:
                msg = u["channel_post"]
                if str(msg.get("chat", {}).get("id")) == str(chat_id):
                    messages.append(msg)
            if "message" in u:
                msg = u["message"]
                if str(msg.get("chat", {}).get("id")) == str(chat_id):
                    messages.append(msg)
        return messages
    except:
        return []

def check_results_from_channel():
    global last_update_id
    if not CHANNEL_STATS:
        return
    try:
        params = {"limit": 50}
        if last_update_id > 0:
            params["offset"] = last_update_id + 1
        r = SESSION.get(f"{TELEGRAM_API}/getUpdates", params=params, timeout=10)
        data = r.json()
        if not data.get("ok"):
            return
        updates = data.get("result", [])
        if not updates:
            return
        for u in updates:
            if u.get("update_id", 0) > last_update_id:
                last_update_id = u.get("update_id")
        messages = []
        for u in updates:
            if "channel_post" in u:
                msg = u["channel_post"]
                if str(msg.get("chat", {}).get("id")) == str(CHANNEL_STATS):
                    messages.append(msg)
        if not messages:
            return
        changed = False
        for entry in predictions:
            if entry.get("status") != "pending":
                continue
            target = int(entry.get("target_number", 0))
            if not target:
                continue
            found_win = False
            checked = 0
            win_game = None
            for offset in range(4):
                game_num = target + offset
                found_text = None
                for msg in messages:
                    text = msg.get("text", "")
                    if f"#N{game_num}" in text:
                        found_text = text
                        break
                if not found_text:
                    break
                checked += 1
                parsed = parse_cards_from_message(found_text)
                if not parsed or not parsed.get("cards"):
                    continue
                result = check_prediction_with_cards(entry, parsed["cards"])
                if result and result.get("main_found"):
                    found_win = True
                    win_game = game_num
                    break
            if checked < 4 and not found_win:
                continue
            if found_win:
                entry["status"] = "win"
                entry["result"] = "win"
                changed = True
                print(f"✅ ЗАШЕЛ! #N{target} на #N{win_game} | {entry.get('main_predicted')}", flush=True)
                if entry.get("message_id"):
                    telegram_edit(entry["message_id"], make_result_message(entry, True))
            elif checked == 4:
                entry["status"] = "lose"
                entry["result"] = "lose"
                changed = True
                print(f"❌ НЕ ЗАШЕЛ! #N{target} (4 игры) | {entry.get('main_predicted')}", flush=True)
                if entry.get("message_id"):
                    telegram_edit(entry["message_id"], make_result_message(entry, False))
        if changed:
            atomic_save_json(PREDICTIONS_FILE, predictions)
    except Exception as e:
        print(f"❌ Ошибка проверки: {e}", flush=True)

def get_active_games():
    url = f"{BASE_URL}/service-api/main-live-feed/v3/games1x2?cfView=3&count=40&fcountry=190&gr=415&grMode=4&lng=ru&ref=7&selectedMs=10.146.1643503"
    try:
        r = SESSION.get(url, timeout=10)
        if r.status_code != 200:
            return []
        data = r.json()
        games = data if isinstance(data, list) else data.get("Value", []) if isinstance(data, dict) else []
        result = []
        for g in games:
            if not isinstance(g, dict):
                continue
            liga = g.get("liga", {})
            if str(liga.get("id")) != str(LEAGUE_ID):
                continue
            if g.get("id"):
                result.append(g)
        return result
    except:
        return []

def get_game_data(game_id):
    url = f"{BASE_URL}/service-api/LiveFeed/GetGameZip?id={game_id}&isSubGames=true&GroupEvents=true&countevents=250&grMode=4&partner=7&topGroups=&country=190&marketType=1&isNewBuilder=true"
    try:
        r = SESSION.get(url, timeout=7)
        if r.status_code == 200:
            return r.json()
    except:
        pass
    return None

def get_duplicate_last_digits(games):
    counts = {}
    for g in games:
        gid = str(g.get("id", ""))
        if gid:
            d = gid[-1]
            counts[d] = counts.get(d, 0) + 1
    return {d for d, c in counts.items() if c >= 2}

def process_game(active_game, seen, skip=False):
    gid = str(active_game.get("id"))
    if not gid or gid in seen:
        return
    seen.add(gid)
    print(f"🔍 API игра | ID={gid} | последняя цифра={gid[-1]}", flush=True)
    is_new = not game_exists(history, gid)
    if is_new:
        print(f"🆕 НОВАЯ ИГРА В ЛОББИ! ID={gid}", flush=True)
        if not skip:
            if has_pending_prediction():
                print(f"⏳ Есть активный прогноз, пропускаем ID={gid}", flush=True)
            else:
                pred = create_prediction_for_game(gid, get_game_number())
                if pred:
                    msg = make_prediction_message(pred)
                    mid = telegram_send(msg)
                    if mid:
                        pred["message_id"] = mid
                        atomic_save_json(PREDICTIONS_FILE, predictions)
                    print(f"🔮 ПРОГНОЗ ОТПРАВЛЕН! #N{pred['target_number']} | {pred['main_predicted']}", flush=True)
        else:
            print(f"🚫 ПРОГНОЗ ПРОПУЩЕН (дубликат цифры)", flush=True)
    raw = get_game_data(gid)
    if raw:
        parsed = parse_game_data(gid, raw)
        if parsed:
            add_or_update_game(history, parsed)

def cleanup_predictions():
    global predictions
    if len(predictions) > 1000:
        predictions = predictions[-1000:]
        atomic_save_json(PREDICTIONS_FILE, predictions)

def main():
    print("\n==================================================", flush=True)
    print("🚀 OLD BOT — 21 CLASSIC", flush=True)
    print("🔢 Основной: 1 цифра | Тестовый: 2 цифры", flush=True)
    print(f"📚 История: {MAX_HISTORY_GAMES} игр", flush=True)
    print(f"🎯 Догон: {DOGON_GAMES} игры", flush=True)
    if CHANNEL_STATS:
        print(f"📊 Проверка по каналу статистики", flush=True)
    print("==================================================\n", flush=True)
    while True:
        start = time.time()
        try:
            games = get_active_games()
            print(f"📡 API получено игр: {len(games)}", flush=True)
            dup = get_duplicate_last_digits(games)
            if dup:
                print(f"🚫 Запрещённые цифры: {sorted(dup)}", flush=True)
            seen = set()
            for g in games:
                try:
                    gid = str(g.get("id", ""))
                    if gid:
                        process_game(g, seen, gid[-1] in dup)
                except Exception as e:
                    print(f"❌ Ошибка: {e}", flush=True)
            check_results_from_channel()
            cleanup_predictions()
            time.sleep(max(0.1, POLL_INTERVAL - (time.time() - start)))
        except KeyboardInterrupt:
            print("\n🛑 Бот остановлен", flush=True)
            break
        except Exception as e:
            print(f"❌ Критическая ошибка: {e}", flush=True)
            time.sleep(3)

if __name__ == "__main__":
    main()