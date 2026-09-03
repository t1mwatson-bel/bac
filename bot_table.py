import os
import sys
import json
import time
import requests
import pytz

from datetime import datetime, timedelta


# =====================================================================
# ENV
# =====================================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    BOT_TOKEN = os.getenv("BOT_TOKEN_PROGNOZ")

CHAT_ID = os.getenv("CHAT_ID_21")

if not CHAT_ID:
    CHAT_ID = os.getenv("CHAT_ID")

if not BOT_TOKEN or not CHAT_ID:
    print(
        "❌ Ошибка: BOT_TOKEN или CHAT_ID не заданы!",
        flush=True
    )
    sys.exit(1)


print(
    f"✅ BOT_TOKEN: {BOT_TOKEN[:5]}...",
    flush=True
)

print(
    f"✅ CHAT_ID: {CHAT_ID}",
    flush=True
)


# =====================================================================
# ОСНОВНЫЕ НАСТРОЙКИ
# =====================================================================

MOSCOW_TZ = pytz.timezone("Europe/Moscow")

BASE_URL = "https://1xlite-36553.pro"

LEAGUE_ID = 1643503

DATA_FILE = "twentyone_data_full.json"

PREDICTIONS_FILE = "twentyone_predictions.json"

# Храним последние 1000 игр
MAX_HISTORY_GAMES = 1000

# Проверяем target + ещё 3 игры
DOGON_GAMES = 4

# Прогноз только на эти ранги
TARGET_RANKS = {
    "J",
    "Q",
    "K",
    "A",
}

# Пары мастей:
#
# ♠ <-> ♥
# ♣ <-> ♦
#
SUIT_PAIRS = {
    "♥": "♠",
    "♠": "♥",
    "♣": "♦",
    "♦": "♣",
}

# Интервал опроса API
POLL_INTERVAL = 2.0


# =====================================================================
# TELEGRAM
# =====================================================================

TELEGRAM_API = (
    f"https://api.telegram.org/bot{BOT_TOKEN}"
)


# =====================================================================
# HTTP
# =====================================================================

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/120.0 Safari/537.36"
    ),

    "Accept": (
        "application/json, "
        "text/plain, */*"
    ),

    "Referer": (
        f"{BASE_URL}/ru/live/twentyone/"
        "1643503-twentyone-game"
    ),

    "Cookie": (
        "platform_type=desktop; "
        "lng=ru; "
        "cookies_agree_type=3; "
        "tzo=3; "
        "is12h=0; "
        "cookies_agree_type=3"
    ),
}


SESSION = requests.Session()

SESSION.headers.update(
    HEADERS
)


# =====================================================================
# КАРТЫ
# =====================================================================

SUITS_NAMES = {
    0: "♠️",
    1: "♣️",
    2: "♦️",
    3: "♥️",
}


RANKS = {
    2: "2",
    3: "3",
    4: "4",
    5: "5",
    6: "6",
    7: "7",
    8: "8",
    9: "9",
    10: "10",
    11: "J",
    12: "Q",
    13: "K",
    14: "A",
}


# =====================================================================
# НОРМАЛИЗАЦИЯ МАСТИ
# =====================================================================

def normalize_suit(value):

    if value is None:
        return None

    if isinstance(value, bool):
        return None

    if isinstance(value, int):
        return SUITS_NAMES.get(value)

    text = str(value).strip()

    text = text.replace(
        "\ufe0f",
        ""
    )

    if text == "0":
        return "♠"

    if text == "1":
        return "♣"

    if text == "2":
        return "♦"

    if text == "3":
        return "♥"

    aliases = {

        "♠": "♠",
        "spade": "♠",
        "spades": "♠",
        "s": "♠",

        "♣": "♣",
        "club": "♣",
        "clubs": "♣",
        "c": "♣",

        "♦": "♦",
        "diamond": "♦",
        "diamonds": "♦",
        "d": "♦",

        "♥": "♥",
        "heart": "♥",
        "hearts": "♥",
        "h": "♥",
    }

    return aliases.get(
        text.lower()
    )


# =====================================================================
# НОРМАЛИЗАЦИЯ РАНГА
# =====================================================================

def normalize_rank(value):

    if value is None:
        return None

    if isinstance(value, bool):
        return None

    if isinstance(value, int):
        return RANKS.get(value)

    text = str(value).strip().upper()

    text = text.replace(
        "А",
        "A"
    )

    if text in {
        "2",
        "3",
        "4",
        "5",
        "6",
        "7",
        "8",
        "9",
        "10",
        "J",
        "Q",
        "K",
        "A",
    }:
        return text

    try:

        number = int(text)

        return RANKS.get(
            number
        )

    except Exception:
        pass

    return None


# =====================================================================
# СОЗДАНИЕ КАРТЫ
# =====================================================================

def make_card(
    rank,
    suit
):

    rank = normalize_rank(
        rank
    )

    suit = normalize_suit(
        suit
    )

    if not rank or not suit:
        return None

    return {
        "rank": rank,
        "suit": f"{suit}\ufe0f",
    }


# =====================================================================
# КАРТА В ТЕКСТ
# =====================================================================

def card_to_text(card):

    if not card:
        return ""

    rank = normalize_rank(
        card.get("rank")
    )

    suit = normalize_suit(
        card.get("suit")
    )

    if not rank or not suit:
        return ""

    return (
        f"{rank}"
        f"{suit}\ufe0f"
    )


# =====================================================================
# ГЛУБОКИЙ ПОИСК
# =====================================================================

def deep_find_value(
    obj,
    wanted_keys
):

    wanted = {
        str(x).lower()
        for x in wanted_keys
    }

    if isinstance(obj, dict):

        for key, value in obj.items():

            if (
                str(key).lower()
                in wanted
            ):
                return value

        for value in obj.values():

            result = deep_find_value(
                value,
                wanted
            )

            if result is not None:
                return result

    elif isinstance(obj, list):

        for item in obj:

            result = deep_find_value(
                item,
                wanted
            )

            if result is not None:
                return result

    return None


# =====================================================================
# ИЗВЛЕЧЕНИЕ КАРТЫ ИЗ DICT
# =====================================================================

def extract_card_from_dict(obj):

    if not isinstance(
        obj,
        dict
    ):
        return None

    rank = None
    suit = None

    rank_keys = {
        "rank",
        "value",
        "v",
        "cardvalue",
        "card_value",
        "nominal",
        "denomination",
    }

    suit_keys = {
        "suit",
        "s",
        "card_suit",
        "cardsuit",
        "mast",
        "color",
    }

    for key, value in obj.items():

        key_lower = str(
            key
        ).strip().lower()

        if key_lower in rank_keys:

            candidate = normalize_rank(
                value
            )

            if candidate:
                rank = candidate

        if key_lower in suit_keys:

            candidate = normalize_suit(
                value
            )

            if candidate:
                suit = candidate

    if rank and suit:

        return {
            "rank": rank,
            "suit": f"{suit}\ufe0f",
        }

    return None


# =====================================================================
# ОПРЕДЕЛЕНИЕ PLAYER / DEALER
# =====================================================================

def classify_key(key):

    k = str(
        key
    ).strip().lower()

    if (
        k == "p"
        or k.startswith("player")
        or k.startswith("p1")
        or k.startswith("p2")
        or k.startswith("p3")
        or k.startswith("p4")
        or "playercard" in k
        or "player_card" in k
        or k == "pcards"
        or k == "playercards"
    ):
        return "player"

    if (
        k == "d"
        or k.startswith("dealer")
        or k.startswith("d1")
        or k.startswith("d2")
        or k.startswith("d3")
        or k.startswith("d4")
        or "dealercard" in k
        or "dealer_card" in k
        or k == "dcards"
        or k == "dealercards"
    ):
        return "dealer"

    return None


# =====================================================================
# РЕКУРСИВНЫЙ ПОИСК КАРТ
# =====================================================================

def find_cards_recursive(
    obj,
    context=None,
    player=None,
    dealer=None
):

    if player is None:
        player = []

    if dealer is None:
        dealer = []

    if isinstance(
        obj,
        dict
    ):

        own_card = extract_card_from_dict(
            obj
        )

        if own_card:

            if context == "player":

                player.append(
                    own_card
                )

            elif context == "dealer":

                dealer.append(
                    own_card
                )

        for key, value in obj.items():

            local_context = context

            classified = classify_key(
                key
            )

            if classified:
                local_context = classified

            key_lower = str(
                key
            ).strip().lower()

            player_keys = {
                "p",
                "p1",
                "p2",
                "p3",
                "p4",
                "p5",
                "p6",
                "p7",
                "p8",
                "p9",
            }

            dealer_keys = {
                "d",
                "d1",
                "d2",
                "d3",
                "d4",
                "d5",
                "d6",
                "d7",
                "d8",
                "d9",
            }

            if key_lower in player_keys:
                local_context = "player"

            if key_lower in dealer_keys:
                local_context = "dealer"

            find_cards_recursive(
                value,
                local_context,
                player,
                dealer
            )

    elif isinstance(
        obj,
        list
    ):

        for item in obj:

            find_cards_recursive(
                item,
                context,
                player,
                dealer
            )

    return (
        player,
        dealer
    )


# =====================================================================
# FALLBACK ПАРСЕР
# =====================================================================

def find_card_lists_fallback(obj):

    player_candidates = []
    dealer_candidates = []

    def walk(
        value,
        context=None
    ):

        if isinstance(
            value,
            dict
        ):

            for key, item in value.items():

                key_lower = str(
                    key
                ).strip().lower()

                new_context = context

                if (
                    "player" in key_lower
                    or key_lower in {
                        "p",
                        "p1",
                        "p2",
                        "p3",
                        "p4",
                        "p5",
                        "p6",
                    }
                ):
                    new_context = "player"

                if (
                    "dealer" in key_lower
                    or key_lower in {
                        "d",
                        "d1",
                        "d2",
                        "d3",
                        "d4",
                        "d5",
                        "d6",
                    }
                ):
                    new_context = "dealer"

                if isinstance(
                    item,
                    list
                ):

                    cards = []

                    for x in item:

                        card = extract_card_from_dict(
                            x
                        )

                        if card:
                            cards.append(
                                card
                            )

                    if cards:

                        if new_context == "player":
                            player_candidates.append(
                                cards
                            )

                        elif new_context == "dealer":
                            dealer_candidates.append(
                                cards
                            )

                walk(
                    item,
                    new_context
                )

        elif isinstance(
            value,
            list
        ):

            for item in value:

                walk(
                    item,
                    context
                )

    walk(obj)

    player = []
    dealer = []

    if player_candidates:

        player = max(
            player_candidates,
            key=len
        )

    if dealer_candidates:

        dealer = max(
            dealer_candidates,
            key=len
        )

    return (
        player,
        dealer
    )


# =====================================================================
# УДАЛЕНИЕ НЕКОРРЕКТНЫХ КАРТ
# =====================================================================

def clean_cards(cards):

    result = []

    for card in cards:

        if not card:
            continue

        rank = normalize_rank(
            card.get("rank")
        )

        suit = normalize_suit(
            card.get("suit")
        )

        if not rank or not suit:
            continue

        result.append({
            "rank": rank,
            "suit": f"{suit}\ufe0f",
        })

    return result


# =====================================================================
# ПАРСИНГ ИГРЫ
# =====================================================================

def parse_game_data(
    game_id,
    raw_data
):

    if raw_data is None:
        return None

    player_cards, dealer_cards = (
        find_cards_recursive(
            raw_data
        )
    )

    player_cards = clean_cards(
        player_cards
    )

    dealer_cards = clean_cards(
        dealer_cards
    )

    # Если ничего не нашли — fallback
    if not player_cards:

        p2, d2 = find_card_lists_fallback(
            raw_data
        )

        if p2:
            player_cards = clean_cards(
                p2
            )

        if d2:
            dealer_cards = clean_cards(
                d2
            )

    # Игра ещё не началась.
    # Это НОРМАЛЬНО.
    #
    # Здесь просто возвращаем None.
    # Прогноз уже был создан раньше
    # в process_game().
    if not player_cards:

        return None

    now = datetime.now(
        MOSCOW_TZ
    )

    state = deep_find_value(
        raw_data,
        {
            "state",
            "STATE",
            "status",
            "STATUS",
        }
    )

    if state is not None:
        state = str(state)

    player_score = deep_find_value(
        raw_data,
        {
            "player_score",
            "playerscore",
            "p_score",
            "pscore",
            "scorep",
            "PScore",
        }
    )

    dealer_score = deep_find_value(
        raw_data,
        {
            "dealer_score",
            "dealerscore",
            "d_score",
            "dscore",
            "scored",
            "DScore",
        }
    )

    try:

        if player_score is not None:
            player_score = int(
                player_score
            )

    except Exception:

        player_score = None

    try:

        if dealer_score is not None:
            dealer_score = int(
                dealer_score
            )

    except Exception:

        dealer_score = None

    player_suits = [
        card["suit"]
        for card in player_cards
    ]

    player_ranks = [
        card["rank"]
        for card in player_cards
    ]

    dealer_suits = [
        card["suit"]
        for card in dealer_cards
    ]

    dealer_ranks = [
        card["rank"]
        for card in dealer_cards
    ]

    # ================================================================
    # ОБЩАЯ ПОСЛЕДОВАТЕЛЬНОСТЬ
    # ================================================================

    all_cards = []

    sequence = []

    position = 1

    max_len = max(
        len(player_cards),
        len(dealer_cards)
    )

    for i in range(max_len):

        if i < len(player_cards):

            card = player_cards[i]

            all_cards.append(
                card
            )

            sequence.append({
                "position": position,
                "who": "P",
                "rank": card["rank"],
                "suit": card["suit"],
            })

            position += 1

        if i < len(dealer_cards):

            card = dealer_cards[i]

            all_cards.append(
                card
            )

            sequence.append({
                "position": position,
                "who": "D",
                "rank": card["rank"],
                "suit": card["suit"],
            })

            position += 1

    all_suits = [
        card["suit"]
        for card in all_cards
    ]

    all_ranks = [
        card["rank"]
        for card in all_cards
    ]

    return {

        "game_id": str(
            game_id
        ),

        "timestamp_msk": (
            now.strftime(
                "%H:%M:%S.%f"
            )[:-3]
        ),

        "state": state,

        "player_score": player_score,

        "dealer_score": dealer_score,

        "player_cards": player_cards,

        "dealer_cards": dealer_cards,

        "player_suits": player_suits,

        "player_ranks": player_ranks,

        "dealer_suits": dealer_suits,

        "dealer_ranks": dealer_ranks,

        "all_suits": all_suits,

        "all_ranks": all_ranks,

        "sequence": sequence,

        "total_cards": len(
            all_cards
        ),

        "first_player_card": (
            player_cards[0]
        ),

        "id_last_digit": (
            str(game_id)[-1]
        ),
    }


# =====================================================================
# JSON
# =====================================================================

def load_json_file(
    filename,
    default
):

    try:

        if not os.path.exists(
            filename
        ):
            return default

        with open(
            filename,
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(f)

        return data

    except Exception as e:

        print(
            f"⚠️ Ошибка чтения "
            f"{filename}: {e}",
            flush=True
        )

        return default


# =====================================================================
# БЕЗОПАСНОЕ СОХРАНЕНИЕ JSON
# =====================================================================

def atomic_save_json(
    filename,
    data
):

    temp_file = (
        filename
        + ".tmp"
    )

    try:

        with open(
            temp_file,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                data,
                f,
                ensure_ascii=False,
                indent=2
            )

        os.replace(
            temp_file,
            filename
        )

        return True

    except Exception as e:

        print(
            f"❌ Ошибка сохранения "
            f"{filename}: {e}",
            flush=True
        )

        try:

            if os.path.exists(
                temp_file
            ):
                os.remove(
                    temp_file
                )

        except Exception:
            pass

        return False


# =====================================================================
# ЗАГРУЗКА ИСТОРИИ
# =====================================================================

def load_history():

    history = load_json_file(
        DATA_FILE,
        []
    )

    if not isinstance(
        history,
        list
    ):
        history = []

    if len(history) > MAX_HISTORY_GAMES:

        history = history[
            -MAX_HISTORY_GAMES:
        ]

        atomic_save_json(
            DATA_FILE,
            history
        )

    return history


# =====================================================================
# ЗАГРУЗКА ПРОГНОЗОВ
# =====================================================================

def load_predictions():

    data = load_json_file(
        PREDICTIONS_FILE,
        []
    )

    if not isinstance(
        data,
        list
    ):
        data = []

    return data


# =====================================================================
# ГЛОБАЛЬНЫЕ ДАННЫЕ
# =====================================================================

history = load_history()

predictions = load_predictions()


print(
    f"📚 Загружено игр: "
    f"{len(history)}",
    flush=True
)

print(
    f"🔮 Загружено прогнозов: "
    f"{len(predictions)}",
    flush=True
)


# =====================================================================
# ПОИСК ИГРЫ ПО ID
# =====================================================================

def find_game_index(
    history_data,
    game_id
):

    game_id = str(
        game_id
    )

    for i, game in enumerate(
        history_data
    ):

        if str(
            game.get(
                "game_id",
                ""
            )
        ) == game_id:

            return i

    return -1


def game_exists(
    history_data,
    game_id
):

    return (
        find_game_index(
            history_data,
            game_id
        )
        != -1
    )


# =====================================================================
# ОБНОВЛЕНИЕ ЗАПИСИ ИГРЫ
# =====================================================================

def merge_game_records(
    old,
    new
):

    merged = dict(
        old
    )

    for key, value in new.items():

        if value is None:
            continue

        if key in {
            "player_cards",
            "dealer_cards",
            "player_suits",
            "player_ranks",
            "dealer_suits",
            "dealer_ranks",
            "all_suits",
            "all_ranks",
            "sequence",
        }:
            continue

        merged[key] = value

    # ================================================================
    # PLAYER
    # ================================================================

    old_player = old.get(
        "player_cards",
        []
    )

    new_player = new.get(
        "player_cards",
        []
    )

    if len(new_player) >= len(
        old_player
    ):

        merged["player_cards"] = (
            new_player
        )

        merged["player_suits"] = (
            new.get(
                "player_suits",
                []
            )
        )

        merged["player_ranks"] = (
            new.get(
                "player_ranks",
                []
            )
        )

    # ================================================================
    # DEALER
    # ================================================================

    old_dealer = old.get(
        "dealer_cards",
        []
    )

    new_dealer = new.get(
        "dealer_cards",
        []
    )

    if len(new_dealer) >= len(
        old_dealer
    ):

        merged["dealer_cards"] = (
            new_dealer
        )

        merged["dealer_suits"] = (
            new.get(
                "dealer_suits",
                []
            )
        )

        merged["dealer_ranks"] = (
            new.get(
                "dealer_ranks",
                []
            )
        )

    # ================================================================
    # ПЕРЕСБОРКА
    # ================================================================

    player_cards = merged.get(
        "player_cards",
        []
    )

    dealer_cards = merged.get(
        "dealer_cards",
        []
    )

    all_cards = []

    sequence = []

    position = 1

    max_len = max(
        len(player_cards),
        len(dealer_cards)
    )

    for i in range(max_len):

        if i < len(player_cards):

            card = player_cards[i]

            all_cards.append(
                card
            )

            sequence.append({
                "position": position,
                "who": "P",
                "rank": card.get(
                    "rank"
                ),
                "suit": card.get(
                    "suit"
                ),
            })

            position += 1

        if i < len(dealer_cards):

            card = dealer_cards[i]

            all_cards.append(
                card
            )

            sequence.append({
                "position": position,
                "who": "D",
                "rank": card.get(
                    "rank"
                ),
                "suit": card.get(
                    "suit"
                ),
            })

            position += 1

    merged["all_suits"] = [
        c.get("suit")
        for c in all_cards
        if c.get("suit")
    ]

    merged["all_ranks"] = [
        c.get("rank")
        for c in all_cards
        if c.get("rank")
    ]

    merged["sequence"] = sequence

    merged["total_cards"] = len(
        all_cards
    )

    if player_cards:

        merged["first_player_card"] = (
            player_cards[0]
        )

    return merged


# =====================================================================
# ДОБАВИТЬ / ОБНОВИТЬ ИГРУ
# =====================================================================

def add_or_update_game(
    history_data,
    game
):

    game_id = str(
        game["game_id"]
    )

    index = find_game_index(
        history_data,
        game_id
    )

    # ================================================================
    # ИГРА УЖЕ ЕСТЬ
    # ================================================================

    if index != -1:

        old_game = history_data[index]

        merged = merge_game_records(
            old_game,
            game
        )

        old_json = json.dumps(
            old_game,
            ensure_ascii=False,
            sort_keys=True
        )

        new_json = json.dumps(
            merged,
            ensure_ascii=False,
            sort_keys=True
        )

        if old_json != new_json:

            history_data[index] = (
                merged
            )

            atomic_save_json(
                DATA_FILE,
                history_data
            )

            print(
                f"🔄 Игра обновлена | "
                f"ID={game_id} | "
                f"P={len(merged.get('player_cards', []))} | "
                f"D={len(merged.get('dealer_cards', []))}",
                flush=True
            )

        return False

    # ================================================================
    # НОВАЯ ИГРА
    # ================================================================

    history_data.append(
        game
    )

    # Удаляем только самые старые,
    # если превысили 1000.
    if len(history_data) > MAX_HISTORY_GAMES:

        remove_count = (
            len(history_data)
            - MAX_HISTORY_GAMES
        )

        del history_data[
            :remove_count
        ]

        print(
            f"♻️ Удалены самые старые "
            f"игры: {remove_count}",
            flush=True
        )

    atomic_save_json(
        DATA_FILE,
        history_data
    )

    first_card = game.get(
        "first_player_card"
    )

    print(
        f"💾 Новая игра сохранена | "
        f"ID={game_id} | "
        f"последняя цифра={game_id[-1]} | "
        f"P1={card_to_text(first_card)} | "
        f"история={len(history_data)}/{MAX_HISTORY_GAMES}",
        flush=True
    )

    return True


# =====================================================================
# ПОИСК ПРЕДЫДУЩЕЙ ИГРЫ ПО ПОСЛЕДНЕЙ ЦИФРЕ ID
# =====================================================================

def find_last_same_digit_game(history, current_game_id):
    current_game_id = str(current_game_id)
    target_digit = current_game_id[-1]
    
    print(f"🔍 Ищу игру с цифрой {target_digit} в истории", flush=True)
    
    # Ищем ЛЮБУЮ игру с P1
    for game in history:  # Просто перебираем все
        old_game_id = str(game.get("game_id", "")).strip()
        
        if not old_game_id or old_game_id == current_game_id:
            continue
            
        if old_game_id[-1] != target_digit:
            continue
        
        # Пытаемся найти P1
        p1 = None
        
        # 1. player_cards
        if not p1:
            cards = game.get("player_cards", [])
            if cards:
                p1 = cards[0]
        
        # 2. first_player_card
        if not p1:
            p1 = game.get("first_player_card")
        
        # 3. sequence
        if not p1:
            for card in game.get("sequence", []):
                if card.get("position") == 1 and card.get("who") == "P":
                    p1 = card
                    break
        
        if p1:
            rank = normalize_rank(p1.get("rank"))
            suit = normalize_suit(p1.get("suit"))
            
            if rank and suit:
                print(f"✅ Найдена игра {old_game_id} с P1={rank}{suit}", flush=True)
                return {
                    "game_id": old_game_id,
                    "first_player_card": {
                        "rank": rank,
                        "suit": f"{suit}\ufe0f",
                    }
                }
    
    print(f"❌ Игра с цифрой {target_digit} не найдена", flush=True)
    return None


# =====================================================================
# СОЗДАНИЕ ПАРНОГО ПРОГНОЗА
# =====================================================================

def get_paired_prediction(
    card
):

    if not card:
        return None

    rank = normalize_rank(
        card.get("rank")
    )

    suit = normalize_suit(
        card.get("suit")
    )

    if not rank or not suit:
        return None

    if rank not in TARGET_RANKS:
        return None

    pair = SUIT_PAIRS.get(
        suit
    )

    if not pair:
        return None

    return {

        "rank": rank,

        "suit": (
            f"{suit}\ufe0f"
        ),

        "pair_suit": (
            f"{pair}\ufe0f"
        ),

        "display": (
            f"{rank}"
            f"{suit}\ufe0f"
            f"{pair}\ufe0f"
        ),
    }


# =====================================================================
# ЕСТЬ ЛИ УЖЕ ПРОГНОЗ НА ИГРУ
# =====================================================================

def prediction_already_exists(
    predictions_data,
    target_game_id
):

    target_game_id = str(
        target_game_id
    )

    for prediction in predictions_data:

        if str(
            prediction.get(
                "target_game_id",
                ""
            )
        ) == target_game_id:

            return True

    return False


# =====================================================================
# СОЗДАНИЕ ПРОГНОЗА
# =====================================================================

def create_prediction_for_game(
    current_game_id,
    current_game_number
):

    current_game_id = str(
        current_game_id
    )

    # Не повторяем прогноз.
    if prediction_already_exists(
        predictions,
        current_game_id
    ):

        return None

    target_digit = current_game_id[-1]

    print(
        f"🔎 Ищу в истории игру с "
        f"последней цифрой ID={target_digit}",
        flush=True
    )

    # ================================================================
    # ИЩЕМ ПРЕДЫДУЩУЮ ИГРУ
    # ================================================================

    previous_game = (
        find_last_same_digit_game(
            history,
            current_game_id
        )
    )

    if not previous_game:

        print(
            f"⏭️ ID={current_game_id} | "
            f"предыдущая игра с цифрой "
            f"{target_digit} не найдена",
            flush=True
        )

        return None

    source_id = str(
        previous_game.get(
            "game_id"
        )
    )

    # ================================================================
    # БЕРЁМ ПЕРВУЮ КАРТУ ИГРОКА
    #
    # В ПЕРВУЮ ОЧЕРЕДЬ:
    # player_cards[0]
    #
    # Именно это находится в
    # twentyone_data_full.json.
    # ================================================================

    first_card = None

    player_cards = previous_game.get(
        "player_cards",
        []
    )

    if player_cards:

        first_card = (
            player_cards[0]
        )

    # fallback
    if not first_card:

        first_card = previous_game.get(
            "first_player_card"
        )

    if not first_card:

        print(
            f"⏭️ ID={current_game_id} | "
            f"у предыдущей игры "
            f"{source_id} нет P1",
            flush=True
        )

        return None

    first_card_text = card_to_text(
        first_card
    )

    print(
        f"🃏 Источник найден | "
        f"ID={source_id} | "
        f"P1={first_card_text}",
        flush=True
    )

    # ================================================================
    # СОЗДАЁМ ПАРНЫЙ ПРОГНОЗ
    # ================================================================

    prediction = get_paired_prediction(
        first_card
    )

    # Если 2-10 — пропуск.
    if not prediction:

        print(
            f"⏭️ ID={current_game_id} | "
            f"P1={first_card_text} | "
            f"ранг не J/Q/K/A — пропуск",
            flush=True
        )

        return None

    now = datetime.now(
        MOSCOW_TZ
    )

    entry = {

        "target_game_id": (
            current_game_id
        ),

        "target_number": (
            current_game_number
        ),

        "source_game_id": (
            source_id
        ),

        "source_first_player_card": (
            first_card
        ),

        "predicted_rank": (
            prediction["rank"]
        ),

        "predicted_suit": (
            prediction["suit"]
        ),

        "predicted_pair_suit": (
            prediction["pair_suit"]
        ),

        "predicted_card": (
            prediction["display"]
        ),

        "status": "pending",

        "created_at": (
            now.strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        ),

        "message_id": None,

        "checked_games": [],
    }

    predictions.append(
        entry
    )

    atomic_save_json(
        PREDICTIONS_FILE,
        predictions
    )

    print(
        f"🔮 ПРОГНОЗ СОЗДАН | "
        f"#N{current_game_number} | "
        f"ID={current_game_id} | "
        f"последняя цифра={target_digit} | "
        f"источник={source_id} | "
        f"P1={first_card_text} | "
        f"прогноз={prediction['display']}",
        flush=True
    )

    return entry


# =====================================================================
# НОМЕР ИГРЫ
# =====================================================================

def get_game_number():

    now = datetime.now(
        MOSCOW_TZ
    )

    start = now.replace(
        hour=3,
        minute=0,
        second=0,
        microsecond=0
    )

    if now < start:

        start -= timedelta(
            days=1
        )

    minutes = int(
        (
            now - start
        ).total_seconds()
        // 60
    )

    return (
        minutes % 1440
    ) + 1


# =====================================================================
# TELEGRAM SEND
# =====================================================================

def telegram_send(
    text
):

    url = (
        f"{TELEGRAM_API}/sendMessage"
    )

    payload = {

        "chat_id": CHAT_ID,

        "text": text,

        "parse_mode": "HTML",

        "disable_web_page_preview": True,
    }

    try:

        response = SESSION.post(
            url,
            json=payload,
            timeout=10
        )

        data = response.json()

        if data.get("ok"):

            return data[
                "result"
            ][
                "message_id"
            ]

        print(
            f"❌ Telegram send error: "
            f"{data}",
            flush=True
        )

    except Exception as e:

        print(
            f"❌ Telegram send exception: "
            f"{e}",
            flush=True
        )

    return None


# =====================================================================
# TELEGRAM EDIT
# =====================================================================

def telegram_edit(
    message_id,
    text
):

    if not message_id:
        return False

    url = (
        f"{TELEGRAM_API}/editMessageText"
    )

    payload = {

        "chat_id": CHAT_ID,

        "message_id": message_id,

        "text": text,

        "parse_mode": "HTML",

        "disable_web_page_preview": True,
    }

    try:

        response = SESSION.post(
            url,
            json=payload,
            timeout=10
        )

        data = response.json()

        if data.get("ok"):
            return True

        print(
            f"❌ Telegram edit error: "
            f"{data}",
            flush=True
        )

    except Exception as e:

        print(
            f"❌ Telegram edit exception: "
            f"{e}",
            flush=True
        )

    return False


# =====================================================================
# ТЕКСТ ПРОГНОЗА
# =====================================================================

def make_prediction_message(
    entry
):

    target = entry.get(
        "target_number"
    )

    predicted = entry.get(
        "predicted_card",
        ""
    )

    return (
        f"🔮 <b>ПРОГНОЗ</b>\n\n"
        f"🎯 Игра: <b>#N{target}</b>\n"
        f"🃏 Прогноз: "
        f"<b>{predicted}</b>"
    )


# =====================================================================
# ПРОВЕРКА КАРТЫ
# =====================================================================

def card_matches_prediction(
    card,
    prediction
):

    if not card or not prediction:
        return False

    rank = normalize_rank(
        card.get("rank")
    )

    suit = normalize_suit(
        card.get("suit")
    )

    predicted_rank = normalize_rank(
        prediction.get("rank")
    )

    suit1 = normalize_suit(
        prediction.get("suit")
    )

    suit2 = normalize_suit(
        prediction.get("pair_suit")
    )

    if not rank or not suit:
        return False

    if rank != predicted_rank:
        return False

    # Подходит любая из двух мастей.
    #
    # Q♠️♥️:
    #
    # Q♠️ -> WIN
    # Q♥️ -> WIN
    #
    if suit == suit1:
        return True

    if suit == suit2:
        return True

    return False


# =====================================================================
# ПРОВЕРКА ИГРЫ
# =====================================================================

def game_matches_prediction(
    game,
    prediction
):

    if not game:
        return False

    # PLAYER
    for card in game.get(
        "player_cards",
        []
    ):

        if card_matches_prediction(
            card,
            prediction
        ):
            return True

    # DEALER
    for card in game.get(
        "dealer_cards",
        []
    ):

        if card_matches_prediction(
            card,
            prediction
        ):
            return True

    return False


# =====================================================================
# РЕЗУЛЬТАТ ПРОГНОЗА С ДОГОНОМ
# =====================================================================

def get_dogon_result(
    prediction_entry
):

    target_game_id = str(
        prediction_entry.get(
            "target_game_id",
            ""
        )
    )

    if not target_game_id:
        return None

    target_index = find_game_index(
        history,
        target_game_id
    )

    if target_index == -1:
        return None

    end_index = (
        target_index
        + DOGON_GAMES
    )

    # Пока 4 игры нет —
    # результат не ставим.
    if end_index > len(history):
        return None

    prediction = {

        "rank": (
            prediction_entry.get(
                "predicted_rank"
            )
        ),

        "suit": (
            prediction_entry.get(
                "predicted_suit"
            )
        ),

        "pair_suit": (
            prediction_entry.get(
                "predicted_pair_suit"
            )
        ),
    }

    checked_games = []

    # target + следующие 3
    for index in range(
        target_index,
        end_index
    ):

        game = history[index]

        game_id = str(
            game.get(
                "game_id",
                ""
            )
        )

        checked_games.append(
            game_id
        )

        if game_matches_prediction(
            game,
            prediction
        ):

            return {

                "status": "win",

                "game_id": game_id,

                "game_index": index,

                "checked_games": (
                    checked_games
                ),
            }

    return {

        "status": "lose",

        "game_id": (
            checked_games[-1]
            if checked_games
            else target_game_id
        ),

        "game_index": (
            end_index - 1
        ),

        "checked_games": (
            checked_games
        ),
    }


# =====================================================================
# ПРОВЕРКА ВСЕХ ПРОГНОЗОВ - ИСПРАВЛЕНА
# =====================================================================

def check_results():

    changed = False

    for entry in predictions:

        if entry.get(
            "status"
        ) != "pending":

            continue

        result = get_dogon_result(
            entry
        )

        if result is None:
            continue

        target = entry.get(
            "target_number"
        )

        predicted = entry.get(
            "predicted_card",
            ""
        )

        checked_games = result.get(
            "checked_games",
            []
        )

        entry["checked_games"] = (
            checked_games
        )

        # ============================================================
        # WIN
        # ============================================================

        if result["status"] == "win":

            entry["status"] = "win"

            entry["result_game_id"] = (
                result["game_id"]
            )

            changed = True

            print(
                f"✅ ЗАШЛО | "
                f"#N{target} | "
                f"{predicted} | "
                f"результат ID="
                f"{result['game_id']}",
                flush=True
            )

            message_id = entry.get(
                "message_id"
            )

            if message_id:

                text = (
                    f"🔮 <b>ПРОГНОЗ</b>\n\n"
                    f"🎯 Игра: "
                    f"<b>#N{target}</b>\n"
                    f"🃏 Прогноз: "
                    f"<b>{predicted}</b> "
                    f"✅"
                )

                telegram_edit(
                    message_id,
                    text
                )

        # ============================================================
        # LOSE
        # ============================================================

        else:

            entry["status"] = "lose"

            entry["result_game_id"] = (
                result["game_id"]
            )

            changed = True

            print(
                f"❌ НЕ ЗАШЛО | "
                f"#N{target} | "
                f"{predicted}",
                flush=True
            )

            message_id = entry.get(
                "message_id"
            )

            if message_id:

                text = (
                    f"🔮 <b>ПРОГНОЗ</b>\n\n"
                    f"🎯 Игра: "
                    f"<b>#N{target}</b>\n"
                    f"🃏 Прогноз: "
                    f"<b>{predicted}</b> "
                    f"❌"
                )

                telegram_edit(
                    message_id,
                    text
                )

    if changed:

        atomic_save_json(
            PREDICTIONS_FILE,
            predictions
        )


# =====================================================================
# ФИЛЬТР БУДУЩИХ ИГР
# =====================================================================

def get_duplicate_last_digits(
    active_games
):

    """
    Смотрим ВСЕ игры, которые одновременно
    пришли из API.

    Если последняя цифра API ID
    встречается 2 раза или больше,
    эта цифра запрещена для прогноза.

    Например:

        749469585 -> 5
        749469755 -> 5

    Значит обе игры с цифрой 5
    получают ПРОПУСК прогноза.
    """

    digit_counts = {}

    for game in active_games:

        game_id = game.get(
            "id"
        )

        if not game_id:
            continue

        game_id = str(
            game_id
        )

        if not game_id:
            continue

        last_digit = game_id[-1]

        digit_counts[
            last_digit
        ] = (
            digit_counts.get(
                last_digit,
                0
            )
            + 1
        )

    duplicate_digits = {

        digit

        for digit, count
        in digit_counts.items()

        if count >= 2
    }

    return duplicate_digits


# =====================================================================
# API — АКТИВНЫЕ ИГРЫ
# =====================================================================

def get_active_games():

    url = (
        f"{BASE_URL}"
        "/service-api/main-live-feed/v3/"
        "games1x2"
        "?cfView=3"
        "&count=40"
        "&fcountry=190"
        "&gr=415"
        "&grMode=4"
        "&lng=ru"
        "&ref=7"
        "&selectedMs=10.146.1643503"
    )

    try:

        response = SESSION.get(
            url,
            timeout=10
        )

        print(
            f"🌐 API games1x2 status: "
            f"{response.status_code}",
            flush=True
        )

        if response.status_code != 200:
            return []

        data = response.json()

        if isinstance(
            data,
            list
        ):

            games = data

        elif (
            isinstance(data, dict)
            and "Value" in data
        ):

            games = data.get(
                "Value",
                []
            )

        else:

            return []

        result = []

        for game in games:

            if not isinstance(
                game,
                dict
            ):
                continue

            liga = game.get(
                "liga",
                {}
            )

            if not isinstance(
                liga,
                dict
            ):

                liga = {}

            liga_id = liga.get(
                "id"
            )

            game_id = game.get(
                "id"
            )

            if not game_id:
                continue

            if str(liga_id) != str(
                LEAGUE_ID
            ):
                continue

            result.append(
                game
            )

        return result

    except Exception as e:

        print(
            f"❌ Ошибка get_active_games: "
            f"{e}",
            flush=True
        )

        return []


# =====================================================================
# API — ДАННЫЕ КОНКРЕТНОЙ ИГРЫ
# =====================================================================

def get_game_data(
    game_id
):

    url = (
        f"{BASE_URL}"
        "/service-api/LiveFeed/GetGameZip"
        f"?id={game_id}"
        "&isSubGames=true"
        "&GroupEvents=true"
        "&countevents=250"
        "&grMode=4"
        "&partner=7"
        "&topGroups="
        "&country=190"
        "&marketType=1"
        "&isNewBuilder=true"
    )

    try:

        response = SESSION.get(
            url,
            timeout=7
        )

        if response.status_code == 200:

            return response.json()

        print(
            f"⚠️ GetGameZip ID="
            f"{game_id} "
            f"status="
            f"{response.status_code}",
            flush=True
        )

    except Exception as e:

        print(
            f"❌ Ошибка игры "
            f"{game_id}: {e}",
            flush=True
        )

    return None


# =====================================================================
# ОБРАБОТКА ОДНОЙ ИГРЫ - ИСПРАВЛЕНА
# =====================================================================

def process_game(
    active_game,
    seen_this_cycle,
    skip_prediction=False
):

    game_id = active_game.get(
        "id"
    )

    if not game_id:
        return

    game_id = str(
        game_id
    )

    # Не обрабатываем дважды
    # в одном цикле.
    if game_id in seen_this_cycle:
        return

    seen_this_cycle.add(
        game_id
    )

    last_digit = game_id[-1]

    print(
        f"🔍 API игра | "
        f"ID={game_id} | "
        f"последняя цифра={last_digit}",
        flush=True
    )

    # ================================================================
    # ОПРЕДЕЛЯЕМ, НОВАЯ ЛИ ЭТО ИГРА
    # ================================================================

    is_new = not game_exists(
        history,
        game_id
    )

    game_number = get_game_number()

    # ================================================================
    # ЕСЛИ НОВАЯ - ДАЕМ ПРОГНОЗ СРАЗУ
    # ================================================================

    if is_new:

        print(f"🆕 НОВАЯ ИГРА В ЛОББИ! ID={game_id}", flush=True)

        if skip_prediction:

            print(
                f"🚫 ПРОГНОЗ ПРОПУЩЕН | "
                f"#N{game_number} | "
                f"ID={game_id} | "
                f"дубликат цифры {last_digit}",
                flush=True
            )

        else:

            prediction = create_prediction_for_game(
                game_id,
                game_number
            )

            if prediction:

                message = make_prediction_message(
                    prediction
                )

                message_id = telegram_send(
                    message
                )

                if message_id:

                    prediction["message_id"] = message_id
                    atomic_save_json(PREDICTIONS_FILE, predictions)

                print(
                    f"🔮 ПРОГНОЗ ОТПРАВЛЕН СРАЗУ! "
                    f"#N{game_number} | "
                    f"ID={game_id} | "
                    f"{prediction.get('predicted_card', '')}",
                    flush=True
                )

            else:

                print(
                    f"⏭️ Нет данных в истории для прогноза "
                    f"ID={game_id} (цифра {last_digit})",
                    flush=True
                )

    # ================================================================
    # ПОЛУЧАЕМ КАРТЫ (ТОЛЬКО ДЛЯ СОХРАНЕНИЯ)
    # ================================================================

    raw_data = get_game_data(
        game_id
    )

    if raw_data is None:

        print(
            f"⏳ ID={game_id} | "
            f"данные игры пока недоступны",
            flush=True
        )

        return

    parsed = parse_game_data(
        game_id,
        raw_data
    )

    if parsed is None:

        print(
            f"⏳ ID={game_id} | "
            f"игра ещё не началась",
            flush=True
        )

        return

    add_or_update_game(
        history,
        parsed
    )


# =====================================================================
# ОЧИСТКА СТАРЫХ ПРОГНОЗОВ
# =====================================================================

def cleanup_predictions():

    global predictions

    if len(predictions) <= 1000:
        return

    predictions = predictions[
        -1000:
    ]

    atomic_save_json(
        PREDICTIONS_FILE,
        predictions
    )


# =====================================================================
# MAIN
# =====================================================================

def main():

    print(
        "",
        flush=True
    )

    print(
        "==================================================",
        flush=True
    )

    print(
        "🚀 OLD BOT — 21 CLASSIC",
        flush=True
    )

    print(
        "🤖 ML: ОТКЛЮЧЕН",
        flush=True
    )

    print(
        "🔢 Логика: последняя цифра API ID",
        flush=True
    )

    print(
        "🃏 Источник: первая карта игрока",
        flush=True
    )

    print(
        f"📚 История: "
        f"последние {MAX_HISTORY_GAMES} игр",
        flush=True
    )

    print(
        f"🎯 Проверка: "
        f"{DOGON_GAMES} игры",
        flush=True
    )

    print(
        "🚫 Одинаковые последние цифры "
        "среди будущих игр: ПРОПУСК",
        flush=True
    )

    print(
        "⏳ Прогноз создаётся ДО начала "
        "текущей игры",
        flush=True
    )

    print(
        "==================================================",
        flush=True
    )

    while True:

        cycle_start = time.time()

        try:

            # ========================================================
            # 1. Получаем ВСЕ текущие игры из API
            # ========================================================

            active_games = (
                get_active_games()
            )

            print(
                f"📡 API получено игр: "
                f"{len(active_games)}",
                flush=True
            )

            # ========================================================
            # 2. Сначала определяем ДУБЛИ
            #
            # Это делается ДО обработки игр,
            # чтобы обе игры с одинаковой
            # последней цифрой получили
            # запрет на прогноз.
            # ========================================================

            duplicate_digits = (
                get_duplicate_last_digits(
                    active_games
                )
            )

            if duplicate_digits:

                print(
                    f"🚫 Запрещённые цифры "
                    f"будущих игр: "
                    f"{sorted(duplicate_digits)}",
                    flush=True
                )

            else:

                print(
                    "✅ Одинаковых последних "
                    "цифр среди будущих игр нет",
                    flush=True
                )

            # ========================================================
            # 3. Обрабатываем все игры
            # ========================================================

            seen_this_cycle = set()

            for active_game in active_games:

                try:

                    game_id = active_game.get(
                        "id"
                    )

                    if not game_id:
                        continue

                    game_id = str(
                        game_id
                    )

                    last_digit = (
                        game_id[-1]
                    )

                    skip_prediction = (
                        last_digit
                        in duplicate_digits
                    )

                    process_game(
                        active_game,
                        seen_this_cycle,
                        skip_prediction
                    )

                except Exception as e:

                    game_id = active_game.get(
                        "id",
                        "?"
                    )

                    print(
                        f"❌ Ошибка обработки "
                        f"ID={game_id}: "
                        f"{e}",
                        flush=True
                    )

            # ========================================================
            # 4. Проверяем результаты
            # ========================================================

            check_results()

            # ========================================================
            # 5. Чистим predictions.json
            # ========================================================

            cleanup_predictions()

            # ========================================================
            # 6. Пауза
            # ========================================================

            elapsed = (
                time.time()
                - cycle_start
            )

            sleep_time = max(
                0.1,
                POLL_INTERVAL
                - elapsed
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
                f"❌ Критическая ошибка: "
                f"{e}",
                flush=True
            )

            time.sleep(
                3
            )


# =====================================================================
# START
# =====================================================================

if __name__ == "__main__":

    main()