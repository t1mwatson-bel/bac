import os
import sys
import json
import time
import requests
import pytz
import re

from datetime import datetime
from collections import deque


# ==================================================
# ENV
# ==================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    BOT_TOKEN = os.getenv("BOT_TOKEN_PROGNOZ")

CHANNEL_PROGNOZ = os.getenv("CHAT_ID_21")
if not CHANNEL_PROGNOZ:
    CHANNEL_PROGNOZ = os.getenv("CHANNEL_PROGNOZ")

CHANNEL_STATS = os.getenv("CHANNEL_STATS")

if not BOT_TOKEN:
    print("❌ BOT_TOKEN не задан!", flush=True)
    sys.exit(1)

if not CHANNEL_PROGNOZ:
    print("❌ CHANNEL_PROGNOZ не задан!", flush=True)
    sys.exit(1)

if not CHANNEL_STATS:
    print("❌ CHANNEL_STATS не задан!", flush=True)
    sys.exit(1)


# ==================================================
# CONFIG
# ==================================================

MOSCOW_TZ = pytz.timezone("Europe/Moscow")

PREDICTIONS_FILE = "twentyone_predictions.json"
OFFSET_FILE = "hybrid_offset.txt"
PROCESSED_SOURCES_FILE = "processed_pattern_sources.json"

DOGON_GAMES = 4
PREDICTION_OFFSET = 11

POLL_INTERVAL = 2.0

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"


# ==================================================
# ИСКЛЮЧАЕМЫЕ ХЭШТЕГИ
# ==================================================

EXCLUDED_TAGS = {"G", "O", "R", "X"}


# ==================================================
# ЦЕЛЕВЫЕ РАНГИ
# ==================================================

SOURCE_RANKS = {"J", "Q", "K", "A"}


# ==================================================
# ЗЕРКАЛЬНЫЕ РАНГИ
#
# J -> K
# K -> J
# Q -> A
# A -> Q
# ==================================================

RANK_MIRROR = {
    "J": "K",
    "K": "J",
    "Q": "A",
    "A": "Q"
}


# ==================================================
# ЗЕРКАЛЬНЫЕ МАСТИ
#
# ♣ -> ♥
# ♥ -> ♣
# ♠ -> ♦
# ♦ -> ♠
# ==================================================

SUIT_MIRROR = {
    "♣": "♥",
    "♥": "♣",
    "♠": "♦",
    "♦": "♠"
}


# ==================================================
# HTTP
# ==================================================

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36"
    ),
    "Accept": "application/json, text/plain, */*"
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


# ==================================================
# GLOBALS
# ==================================================

predictions = []

# Номера игр и ID источников,
# которые уже обработаны
processed_sources = set()

# Кэш результатов игр из канала
games_cache = {}

# Ограниченный кэш последних сообщений
recent_messages = deque(maxlen=500)

last_prediction_time = 0


# ==================================================
# JSON
# ==================================================

def load_json_file(filename, default):
    try:
        if not os.path.exists(filename):
            return default

        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)

    except Exception as e:
        print(
            f"⚠️ Ошибка чтения {filename}: {e}",
            flush=True
        )
        return default


def atomic_save_json(filename, data):
    tmp = filename + ".tmp"

    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(
                data,
                f,
                ensure_ascii=False,
                indent=2
            )

        os.replace(tmp, filename)
        return True

    except Exception as e:
        print(
            f"⚠️ Ошибка сохранения {filename}: {e}",
            flush=True
        )

        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass

        return False


# ==================================================
# LOAD / SAVE
# ==================================================

def load_predictions():
    data = load_json_file(
        PREDICTIONS_FILE,
        []
    )

    if not isinstance(data, list):
        return []

    return data


def load_processed_sources():
    data = load_json_file(
        PROCESSED_SOURCES_FILE,
        []
    )

    if not isinstance(data, list):
        return set()

    return set(str(x) for x in data)


def save_processed_sources():
    global processed_sources

    # Чтобы файл не рос бесконечно
    data = list(processed_sources)

    if len(data) > 10000:
        data = data[-10000:]
        processed_sources = set(data)

    atomic_save_json(
        PROCESSED_SOURCES_FILE,
        data
    )


# ==================================================
# OFFSET
# ==================================================

def get_offset():
    try:
        if os.path.exists(OFFSET_FILE):
            with open(
                OFFSET_FILE,
                "r",
                encoding="utf-8"
            ) as f:
                value = f.read().strip()

                if value:
                    return int(value)

    except Exception as e:
        print(
            f"⚠️ Ошибка offset: {e}",
            flush=True
        )

    return 0


def save_offset(offset):
    try:
        with open(
            OFFSET_FILE,
            "w",
            encoding="utf-8"
        ) as f:
            f.write(str(offset))

    except Exception as e:
        print(
            f"⚠️ Ошибка сохранения offset: {e}",
            flush=True
        )


# ==================================================
# GAME NUMBER OFFSET
# ==================================================

def add_game_offset(number, offset):
    """
    Игра идёт по кругу 1..1440
    """

    try:
        number = int(number)
        offset = int(offset)
    except Exception:
        return None

    return (
        (number - 1 + offset) % 1440
    ) + 1


# ==================================================
# NORMALIZE
# ==================================================

def normalize_suit(suit):
    if not suit:
        return None

    suit = str(suit)

    suit = suit.replace(
        "\ufe0f",
        ""
    )

    mapping = {
        "♠": "♠",
        "♣": "♣",
        "♦": "♦",
        "♥": "♥"
    }

    return mapping.get(suit)


def normalize_rank(rank):
    if not rank:
        return None

    rank = str(rank).upper().strip()

    rank = rank.replace(
        "А",
        "A"
    )

    if rank in {
        "2", "3", "4", "5",
        "6", "7", "8", "9",
        "10", "J", "Q", "K", "A"
    }:
        return rank

    return None


def normalize_card(rank, suit):
    rank = normalize_rank(rank)
    suit = normalize_suit(suit)

    if not rank or not suit:
        return None

    return f"{rank}{suit}\ufe0f"


# ==================================================
# PARSE GAME FROM CHANNEL
# ==================================================

def parse_game_message(text):
    """
    Разбирает сообщение статистики.

    Пример:

    #N1001. ✅20(Q♣J♥Q♥J♠10♥) - 27(7♣7♥K♦9♥)
    #T47 (ID: 750854415)

    Возвращает:
    - номер игры
    - ID игры
    - карты игрока
    - карты дилера
    - теги
    """

    if not text:
        return None

    text = str(text)

    # ------------------------------------------------
    # GAME NUMBER
    # ------------------------------------------------

    game_match = re.search(
        r"#N(\d+)",
        text,
        flags=re.IGNORECASE
    )

    if not game_match:
        return None

    game_number = int(
        game_match.group(1)
    )

    # ------------------------------------------------
    # GAME ID
    # ------------------------------------------------

    id_match = re.search(
        r"\(ID:\s*(\d+)\)",
        text,
        flags=re.IGNORECASE
    )

    if not id_match:
        id_match = re.search(
            r"\bID:\s*(\d+)",
            text,
            flags=re.IGNORECASE
        )

    game_id = (
        id_match.group(1)
        if id_match
        else None
    )

    # ------------------------------------------------
    # TAGS
    # ------------------------------------------------

    found_tags = re.findall(
        r"#([A-Za-zА-Яа-яЁё]+)",
        text
    )

    tags = set()

    for tag in found_tags:
        tag = tag.upper().strip()

        # N1001 и T47 не считаем спец-тегами
        if re.fullmatch(r"N\d+", tag):
            continue

        if re.fullmatch(r"T\d+", tag):
            continue

        tags.add(tag)

    # ------------------------------------------------
    # ИЩЕМ ДВЕ ГРУППЫ КАРТ В СКОБКАХ
    # ------------------------------------------------

    groups = re.findall(
        r"\(([^()]*)\)",
        text
    )

    card_groups = []

    for group in groups:

        # ID не содержит мастей
        if not re.search(
            r"[♠♣♦♥]",
            group
        ):
            continue

        cards = re.findall(
            r"(10|[2-9AJQK])([♠♣♦♥])\ufe0f?",
            group,
            flags=re.IGNORECASE
        )

        if cards:
            parsed_cards = []

            for rank, suit in cards:
                card = normalize_card(
                    rank,
                    suit
                )

                if card:
                    parsed_cards.append(card)

            if parsed_cards:
                card_groups.append(
                    parsed_cards
                )

    if not card_groups:
        return None

    # Обычно первая группа = игрок
    player_cards = (
        card_groups[0]
        if len(card_groups) >= 1
        else []
    )

    dealer_cards = (
        card_groups[1]
        if len(card_groups) >= 2
        else []
    )

    return {
        "game_number": game_number,
        "game_id": game_id,
        "tags": tags,
        "player_cards": player_cards,
        "dealer_cards": dealer_cards,
        "text": text
    }


# ==================================================
# ПРОВЕРКА ИСКЛЮЧЕНИЙ
# ==================================================

def has_excluded_tags(parsed):
    if not parsed:
        return True

    tags = parsed.get(
        "tags",
        set()
    )

    excluded_found = (
        tags & EXCLUDED_TAGS
    )

    return bool(excluded_found)


# ==================================================
# ПОСТРОЕНИЕ ПРОГНОЗА
# ==================================================

def build_pattern_prediction(parsed):
    """
    ЛОГИКА:

    Берём первую карту игрока.

    Только J/Q/K/A.

    Ранг:
        J <-> K
        Q <-> A

    Масть:
        ♣ <-> ♥
        ♠ <-> ♦

    Прогнозируются ДВЕ МАСТИ:
        исходная
        зеркальная

    Пример:
        Q♣

    Ранг:
        Q -> A

    Масти:
        ♣
        ♥ (зеркальная)

    Итог:
        A♣
        A♥
    """

    if not parsed:
        return None

    if has_excluded_tags(parsed):
        return None

    player_cards = parsed.get(
        "player_cards",
        []
    )

    if not player_cards:
        return None

    first_card = player_cards[0]

    match = re.match(
        r"(10|[2-9AJQK])([♠♣♦♥])",
        first_card
    )

    if not match:
        return None

    source_rank = normalize_rank(
        match.group(1)
    )

    source_suit = normalize_suit(
        match.group(2)
    )

    if not source_rank:
        return None

    if source_rank not in SOURCE_RANKS:
        return None

    target_rank = RANK_MIRROR.get(
        source_rank
    )

    if not target_rank:
        return None

    mirror_suit = SUIT_MIRROR.get(
        source_suit
    )

    if not mirror_suit:
        return None

    # Две карты:
    # исходная масть
    # зеркальная масть

    card_original = normalize_card(
        target_rank,
        source_suit
    )

    card_mirror = normalize_card(
        target_rank,
        mirror_suit
    )

    predicted_cards = []

    if card_original:
        predicted_cards.append(
            card_original
        )

    if (
        card_mirror
        and card_mirror not in predicted_cards
    ):
        predicted_cards.append(
            card_mirror
        )

    if not predicted_cards:
        return None

    target_number = add_game_offset(
        parsed["game_number"],
        PREDICTION_OFFSET
    )

    return {
        "source_number": parsed["game_number"],
        "source_game_id": parsed.get(
            "game_id"
        ),
        "source_card": first_card,
        "source_rank": source_rank,
        "source_suit": source_suit,
        "target_rank": target_rank,
        "target_number": target_number,
        "predicted_cards": predicted_cards
    }


# ==================================================
# TELEGRAM SEND
# ==================================================

def telegram_send(text, chat_id=None):
    if not chat_id:
        chat_id = CHANNEL_PROGNOZ

    try:
        response = SESSION.post(
            f"{TELEGRAM_API}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            },
            timeout=10
        )

        data = response.json()

        if data.get("ok"):
            return data[
                "result"
            ].get("message_id")

        print(
            f"❌ Telegram send: {data}",
            flush=True
        )

    except Exception as e:
        print(
            f"❌ Telegram send error: {e}",
            flush=True
        )

    return None


# ==================================================
# TELEGRAM EDIT
# ==================================================

def telegram_edit(
    message_id,
    text,
    chat_id=None
):
    if not message_id:
        return False

    if not chat_id:
        chat_id = CHANNEL_PROGNOZ

    try:
        response = SESSION.post(
            f"{TELEGRAM_API}/editMessageText",
            json={
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "parse_mode": "HTML"
            },
            timeout=10
        )

        data = response.json()

        if not data.get("ok"):
            print(
                f"⚠️ Telegram edit: {data}",
                flush=True
            )

        return bool(
            data.get("ok")
        )

    except Exception as e:
        print(
            f"❌ Telegram edit error: {e}",
            flush=True
        )

        return False


# ==================================================
# MESSAGE PREDICTION
# ==================================================

def make_prediction_message(entry):

    cards = entry.get(
        "predicted_cards",
        []
    )

    cards_text = " + ".join(cards)

    source_card = entry.get(
        "source_card",
        "?"
    )

    source_number = entry.get(
        "source_number",
        "?"
    )

    target_number = entry.get(
        "target_number",
        "?"
    )

    return (
        f"🎯 <b>Игра: #N{target_number}</b>\n\n"
        f"🃏 <b>{cards_text}</b>\n\n"
        
    )


# ==================================================
# SOURCE KEY
# ==================================================

def get_source_key(parsed):
    """
    Уникальный ключ источника.

    Приоритет:
    ID игры

    Если ID нет:
    номер игры + текст
    """

    gid = parsed.get("game_id")

    if gid:
        return f"id:{gid}"

    number = parsed.get(
        "game_number"
    )

    text = parsed.get(
        "text",
        ""
    )

    return f"n:{number}:{hash(text)}"


# ==================================================
# ПРОВЕРКА СУЩЕСТВУЮЩЕГО ПРОГНОЗА
# ==================================================

def prediction_exists_for_source(source_key):

    for entry in predictions:

        if (
            entry.get("source_key")
            == source_key
        ):
            return True

    return False


# ==================================================
# СОЗДАНИЕ ПРОГНОЗА
# ==================================================

def create_pattern_prediction(parsed):
    global predictions
    global last_prediction_time

    source_key = get_source_key(
        parsed
    )

    # Уже обработан
    if source_key in processed_sources:
        print(
            f"⏭️ Источник уже обработан: {source_key}",
            flush=True
        )
        return None

    # Уже есть прогноз
    if prediction_exists_for_source(
        source_key
    ):
        print(
            f"⏭️ Уже есть прогноз для источника: "
            f"{source_key}",
            flush=True
        )

        processed_sources.add(
            source_key
        )

        save_processed_sources()

        return None

    # Исключающие теги
    if has_excluded_tags(parsed):

        found = (
            parsed.get("tags", set())
            & EXCLUDED_TAGS
        )

        print(
            f"🚫 #N{parsed['game_number']} "
            f"пропуск — исключения: "
            f"{', '.join(sorted(found))}",
            flush=True
        )

        processed_sources.add(
            source_key
        )

        save_processed_sources()

        return None

    # Строим паттерн
    pattern = build_pattern_prediction(
        parsed
    )

    if not pattern:

        player_cards = parsed.get(
            "player_cards",
            []
        )

        first = (
            player_cards[0]
            if player_cards
            else "нет"
        )

        print(
            f"⏭️ #N{parsed['game_number']} "
            f"не подходит | "
            f"первая карта игрока: {first}",
            flush=True
        )

        processed_sources.add(
            source_key
        )

        save_processed_sources()

        return None

    # Защита от очень частых отправок
    now_ts = time.time()

    if (
        now_ts - last_prediction_time
        < 1.0
    ):
        print(
            "⏭️ Anti-spam cooldown",
            flush=True
        )

        return None

    # ------------------------------------------------
    # ENTRY
    # ------------------------------------------------

    entry = {
        "source_key": source_key,

        "source_number":
            pattern["source_number"],

        "source_game_id":
            pattern["source_game_id"],

        "source_card":
            pattern["source_card"],

        "source_rank":
            pattern["source_rank"],

        "source_suit":
            pattern["source_suit"],

        "target_rank":
            pattern["target_rank"],

        "target_number":
            pattern["target_number"],

        "predicted_cards":
            pattern["predicted_cards"],

        "status": "pending",

        "created_at":
            datetime.now(
                MOSCOW_TZ
            ).isoformat(),

        "message_id": None,

        "original_text": "",

        "result_game": None,

        "found_card": None,

        "current_dogon": 0
    }

    # Сразу добавляем в память
    predictions.append(
        entry
    )

    # Сохраняем
    atomic_save_json(
        PREDICTIONS_FILE,
        predictions
    )

    # Отправляем
    message = make_prediction_message(
        entry
    )

    entry["original_text"] = message

    message_id = telegram_send(
        message
    )

    if message_id:

        entry["message_id"] = (
            message_id
        )

        atomic_save_json(
            PREDICTIONS_FILE,
            predictions
        )

    # Источник обработан
    processed_sources.add(
        source_key
    )

    save_processed_sources()

    last_prediction_time = now_ts

    print(
        "\n"
        "══════════════════════════════════════",
        flush=True
    )

    print(
        f"🔮 НОВЫЙ ПРОГНОЗ",
        flush=True
    )

    print(
        f"📥 Источник: "
        f"#N{pattern['source_number']}",
        flush=True
    )

    print(
        f"🃏 Первая карта: "
        f"{pattern['source_card']}",
        flush=True
    )

    print(
        f"🎯 Цель: "
        f"#N{pattern['target_number']}",
        flush=True
    )

    print(
        f"🔥 Прогноз: "
        f"{' + '.join(pattern['predicted_cards'])}",
        flush=True
    )

    print(
        "══════════════════════════════════════\n",
        flush=True
    )

    return entry


# ==================================================
# CACHE GAME RESULT
# ==================================================

def cache_finished_game(parsed):

    if not parsed:
        return

    number = parsed.get(
        "game_number"
    )

    if not number:
        return

    all_cards = []

    for card in parsed.get(
        "player_cards",
        []
    ):
        if card not in all_cards:
            all_cards.append(card)

    for card in parsed.get(
        "dealer_cards",
        []
    ):
        if card not in all_cards:
            all_cards.append(card)

    games_cache[number] = {
        "cards": all_cards,
        "parsed": parsed,
        "text": parsed.get(
            "text",
            ""
        ),
        "timestamp": datetime.now(
            MOSCOW_TZ
        ).isoformat()
    }

    print(
        f"💾 РЕЗУЛЬТАТ В КЭШ: "
        f"#N{number} -> {all_cards}",
        flush=True
    )


# ==================================================
# UPDATE PREDICTION MESSAGE
# ==================================================

def update_prediction_status(
    entry,
    success,
    found=None
):

    message_id = entry.get(
        "message_id"
    )

    original_text = entry.get(
        "original_text",
        ""
    )

    if not message_id:
        return

    if not original_text:
        return

    lines = original_text.split(
        "\n"
    )

    if not lines:
        return

    target = entry.get(
        "target_number"
    )

    if success:

        result_game = found.get(
            "num"
        )

        dogon = found.get(
            "dogon"
        )

        card = found.get(
            "card"
        )

        lines[0] = (
            f"🎯 <b>Игра: #N{target} ✅</b>"
        )

        lines.append("")

        lines.append(
            f"✅ ЗАШЛО: #N{result_game}"
        )

        lines.append(
            f"🃏 Выпало: {card}"
        )

        lines.append(
            f"🔁 Догон: {dogon}"
        )

    else:

        lines[0] = (
            f"🎯 <b>Игра: #N{target} ❌</b>"
        )

        lines.append("")

        lines.append(
            f"❌ Не зашло за "
            f"{DOGON_GAMES + 1} игр"
        )

    new_text = "\n".join(
        lines
    )

    telegram_edit(
        message_id,
        new_text
    )


# ==================================================
# CHECK PREDICTIONS
# ==================================================

def check_predictions():
    global predictions

    pending = [
        x for x in predictions
        if x.get("status") == "pending"
    ]

    if not pending:
        return

    changed = False

    for entry in pending:

        target = entry.get(
            "target_number"
        )

        predicted_cards = [
            x for x in entry.get(
                "predicted_cards",
                []
            )
            if x
        ]

        if not target:
            continue

        if not predicted_cards:
            continue

        # --------------------------------------------
        # ПРОВЕРЯЕМ ОСНОВНУЮ + ДОГОНЫ
        # --------------------------------------------

        found = None

        all_available = True

        for dogon in range(
            DOGON_GAMES + 1
        ):

            check_number = (
                add_game_offset(
                    target,
                    dogon
                )
            )

            result = games_cache.get(
                check_number
            )

            if not result:

                all_available = False

                continue

            actual_cards = result.get(
                "cards",
                []
            )

            for predicted in predicted_cards:

                if predicted in actual_cards:

                    found = {
                        "num": check_number,
                        "dogon": dogon,
                        "card": predicted
                    }

                    break

            if found:
                break

        # --------------------------------------------
        # WIN
        # --------------------------------------------

        if found:

            entry["status"] = "win"

            entry["result_game"] = (
                found["num"]
            )

            entry["found_card"] = (
                found["card"]
            )

            entry["current_dogon"] = (
                found["dogon"]
            )

            changed = True

            print(
                f"✅ ПРОГНОЗ ЗАШЁЛ | "
                f"Цель #{target} | "
                f"Результат #{found['num']} | "
                f"Догон {found['dogon']} | "
                f"{found['card']}",
                flush=True
            )

            update_prediction_status(
                entry,
                True,
                found
            )

            continue

        # --------------------------------------------
        # ЕЩЁ НЕ ВСЕ ИГРЫ ПРИШЛИ
        # --------------------------------------------

        if not all_available:
            continue

        # --------------------------------------------
        # LOSE
        # --------------------------------------------

        entry["status"] = "lose"

        entry["current_dogon"] = (
            DOGON_GAMES
        )

        changed = True

        print(
            f"❌ ПРОГНОЗ НЕ ЗАШЁЛ | "
            f"Цель #{target} | "
            f"Догоны 0-{DOGON_GAMES}",
            flush=True
        )

        update_prediction_status(
            entry,
            False
        )

    if changed:

        atomic_save_json(
            PREDICTIONS_FILE,
            predictions
        )

        print(
            "💾 Результаты прогнозов обновлены",
            flush=True
        )


# ==================================================
# CLEANUP PREDICTIONS
# ==================================================

def cleanup_predictions():
    global predictions

    # Храним последние 3000 прогнозов
    if len(predictions) > 3000:

        predictions = predictions[-3000:]

        atomic_save_json(
            PREDICTIONS_FILE,
            predictions
        )


# ==================================================
# PROCESS TELEGRAM UPDATES
# ==================================================

def process_telegram_updates(offset):
    """
    Главный обработчик канала статистики.

    ВАЖНО:
    Больше НЕ ищем:

        "⏳ Ожидание игры"

    Теперь работаем напрямую
    с завершёнными играми #N....
    """

    global games_cache

    try:

        response = SESSION.get(
            f"{TELEGRAM_API}/getUpdates",
            params={
                "offset": offset,
                "timeout": 1,
                "limit": 100,
                "allowed_updates": json.dumps([
                    "channel_post",
                    "edited_channel_post"
                ])
            },
            timeout=10
        )

        data = response.json()

        if not data.get("ok"):

            print(
                f"❌ GETUPDATES ERROR: {data}",
                flush=True
            )

            return offset

        updates = data.get(
            "result",
            []
        )

        if updates:

            print(
                f"📨 Telegram updates: "
                f"{len(updates)}",
                flush=True
            )

        for update in updates:

            # ----------------------------------------
            # UPDATE ID
            # ----------------------------------------

            update_id = update.get(
                "update_id"
            )

            if update_id is not None:

                offset = (
                    int(update_id) + 1
                )

                save_offset(
                    offset
                )

            # ----------------------------------------
            # CHANNEL POST
            # ----------------------------------------

            post = (
                update.get("channel_post")
                or
                update.get("edited_channel_post")
            )

            if not post:

                print(
                    f"⚠️ Update без channel_post: "
                    f"{list(update.keys())}",
                    flush=True
                )

                continue

            # ----------------------------------------
            # CHAT ID
            # ----------------------------------------

            chat = post.get(
                "chat",
                {}
            )

            chat_id = str(
                chat.get("id", "")
            )

            expected_chat_id = str(
                CHANNEL_STATS
            )

            text = post.get(
                "text",
                ""
            )

            # Диагностика
            print(
                f"📬 POST ПОЛУЧЕН | "
                f"chat_id={chat_id} | "
                f"ожидается={expected_chat_id}",
                flush=True
            )

            # ----------------------------------------
            # FILTER CHANNEL
            # ----------------------------------------

            if chat_id != expected_chat_id:

                print(
                    "⏭️ Пропуск — сообщение "
                    "из другого канала",
                    flush=True
                )

                continue

            if not text:

                print(
                    "⏭️ Пустое сообщение",
                    flush=True
                )

                continue

            print(
                f"📩 CHANNEL_STATS: "
                f"{text[:300]}",
                flush=True
            )

            recent_messages.append(
                text
            )

            # ----------------------------------------
            # PARSE GAME
            # ----------------------------------------

            parsed = parse_game_message(
                text
            )

            if not parsed:

                print(
                    "⏭️ Не похоже на сообщение игры",
                    flush=True
                )

                continue

            # ----------------------------------------
            # CACHE RESULT
            #
            # Любая завершённая игра нужна
            # для проверки старых прогнозов
            # ----------------------------------------

            cache_finished_game(
                parsed
            )

            # ----------------------------------------
            # CREATE NEW PATTERN PREDICTION
            # ----------------------------------------

            create_pattern_prediction(
                parsed
            )

    except requests.exceptions.Timeout:
        pass

    except Exception as e:

        print(
            f"❌ Updates error: {e}",
            flush=True
        )

    return offset


# ==================================================
# MAIN
# ==================================================

def main():
    global predictions
    global processed_sources

    print()
    print("==================================================")
    print("🚀 OLD PATTERN BOT — НОВАЯ ЛОГИКА")
    print("==================================================")
    print("📡 Источник: CHANNEL_STATS")
    print("🧠 Паттерн: первая карта игрока J/Q/K/A")
    print("🔄 Ранги: J↔K | Q↔A")
    print("🔄 Масти: ♣↔♥ | ♠↔♦")
    print(
        f"🎯 Смещение прогноза: "
        f"+{PREDICTION_OFFSET} игр"
    )
    print("🚫 Исключения: #G, #O, #R, #X")
    print(
        f"🔁 Проверка: основная + "
        f"{DOGON_GAMES} догонов"
    )
    print("==================================================")
    print()

    # ----------------------------------------------
    # LOAD
    # ----------------------------------------------

    predictions = load_predictions()

    processed_sources = (
        load_processed_sources()
    )

    print(
        f"📊 Загружено прогнозов: "
        f"{len(predictions)}",
        flush=True
    )

    print(
        f"🛡️ Обработанных источников: "
        f"{len(processed_sources)}",
        flush=True
    )

    # ----------------------------------------------
    # OFFSET
    # ----------------------------------------------

    offset = get_offset()

    print(
        f"📌 Telegram offset: {offset}",
        flush=True
    )

    print()
    print("🤖 Бот запущен...")
    print()

    # ----------------------------------------------
    # LOOP
    # ----------------------------------------------

    while True:

        start = time.time()

        try:

            # 1. Читаем новые игры канала
            offset = (
                process_telegram_updates(
                    offset
                )
            )

            # 2. Проверяем прогнозы
            check_predictions()

            # 3. Ограничиваем файл прогнозов
            cleanup_predictions()

            elapsed = (
                time.time() - start
            )

            sleep_time = max(
                0.1,
                POLL_INTERVAL - elapsed
            )

            time.sleep(
                sleep_time
            )

        except KeyboardInterrupt:

            print(
                "\n🛑 Бот остановлен",
                flush=True
            )

            break

        except Exception as e:

            print(
                f"❌ Критическая ошибка: {e}",
                flush=True
            )

            time.sleep(3)


# ==================================================
# START
# ==================================================

if __name__ == "__main__":
    main()