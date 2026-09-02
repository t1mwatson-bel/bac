import os
import sqlite3
import logging
from collections import defaultdict
from datetime import datetime


# =====================================================================
# SETTINGS
# =====================================================================

DB_FILE = "candles.db"

# Сколько последних свечей использовать как текущий паттерн
PATTERN_LENGTH = 8

# Минимальное количество найденных похожих паттернов
MIN_MATCHES = 5

# Сколько последних свечей пропускать при поиске истории
# чтобы текущий паттерн не находил сам себя
EXCLUDE_LAST = PATTERN_LENGTH + 2


# =====================================================================
# LOGGING
# =====================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger(__name__)


# =====================================================================
# DATABASE SEARCH
# =====================================================================

def find_database():

    if os.path.exists(DB_FILE):
        return DB_FILE

    candidates = []

    for filename in os.listdir("."):

        if filename.endswith(".db"):
            candidates.append(filename)

        elif filename.endswith(".sqlite"):
            candidates.append(filename)

        elif filename.endswith(".sqlite3"):
            candidates.append(filename)

    if not candidates:

        logger.error("❌ SQLite база не найдена!")

        return None

    logger.info(
        f"📂 Найдены базы: {candidates}"
    )

    return candidates[0]


# =====================================================================
# GET TABLES
# =====================================================================

def get_tables(conn):

    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type='table'
        ORDER BY name
        """
    )

    rows = cursor.fetchall()

    return [
        row[0]
        for row in rows
    ]


# =====================================================================
# GET TABLE COLUMNS
# =====================================================================

def get_columns(conn, table):

    cursor = conn.cursor()

    cursor.execute(
        f"PRAGMA table_info('{table}')"
    )

    rows = cursor.fetchall()

    return [
        row[1]
        for row in rows
    ]


# =====================================================================
# FIND CANDLE TABLE
# =====================================================================

def find_candle_table(conn):

    tables = get_tables(conn)

    logger.info(
        f"📊 Таблицы в базе: {tables}"
    )

    best_table = None
    best_score = -1

    for table in tables:

        columns = get_columns(
            conn,
            table
        )

        lower_columns = [
            str(col).lower()
            for col in columns
        ]

        score = 0

        keywords = [
            "open",
            "high",
            "low",
            "close",
            "timestamp",
            "time",
            "date",
        ]

        for keyword in keywords:

            if keyword in lower_columns:
                score += 1

        logger.info(
            f"🔍 Таблица {table}: "
            f"{columns} | score={score}"
        )

        if score > best_score:

            best_score = score
            best_table = table

    if not best_table:

        return None

    logger.info(
        f"✅ Выбрана таблица свечей: "
        f"{best_table}"
    )

    return best_table


# =====================================================================
# FIND COLUMN
# =====================================================================

def find_column(columns, variants):

    lower_map = {
        str(column).lower(): column
        for column in columns
    }

    for variant in variants:

        if variant in lower_map:
            return lower_map[variant]

    return None


# =====================================================================
# DETECT CANDLE STRUCTURE
# =====================================================================

def detect_columns(conn, table):

    columns = get_columns(
        conn,
        table
    )

    time_col = find_column(
        columns,
        [
            "timestamp",
            "time",
            "datetime",
            "date",
            "open_time",
            "ts",
        ]
    )

    open_col = find_column(
        columns,
        [
            "open",
            "o",
        ]
    )

    high_col = find_column(
        columns,
        [
            "high",
            "h",
        ]
    )

    low_col = find_column(
        columns,
        [
            "low",
            "l",
        ]
    )

    close_col = find_column(
        columns,
        [
            "close",
            "c",
            "price",
        ]
    )

    logger.info(
        "🧩 Определены колонки:"
    )

    logger.info(
        f"   TIME  = {time_col}"
    )

    logger.info(
        f"   OPEN  = {open_col}"
    )

    logger.info(
        f"   HIGH  = {high_col}"
    )

    logger.info(
        f"   LOW   = {low_col}"
    )

    logger.info(
        f"   CLOSE = {close_col}"
    )

    if not close_col:

        logger.error(
            "❌ Не найдена колонка CLOSE!"
        )

        return None

    return {
        "time": time_col,
        "open": open_col,
        "high": high_col,
        "low": low_col,
        "close": close_col,
    }


# =====================================================================
# LOAD CANDLES
# =====================================================================

def load_candles(conn, table, cols):

    cursor = conn.cursor()

    selected = []

    if cols["time"]:
        selected.append(
            f'"{cols["time"]}"'
        )
    else:
        selected.append(
            "rowid"
        )

    if cols["open"]:
        selected.append(
            f'"{cols["open"]}"'
        )
    else:
        selected.append(
            "NULL"
        )

    if cols["high"]:
        selected.append(
            f'"{cols["high"]}"'
        )
    else:
        selected.append(
            "NULL"
        )

    if cols["low"]:
        selected.append(
            f'"{cols["low"]}"'
        )
    else:
        selected.append(
            "NULL"
        )

    selected.append(
        f'"{cols["close"]}"'
    )

    query = f"""
        SELECT {", ".join(selected)}
        FROM "{table}"
        ORDER BY rowid ASC
    """

    cursor.execute(query)

    rows = cursor.fetchall()

    candles = []

    for row in rows:

        try:

            timestamp = row[0]

            open_price = (
                float(row[1])
                if row[1] is not None
                else None
            )

            high_price = (
                float(row[2])
                if row[2] is not None
                else None
            )

            low_price = (
                float(row[3])
                if row[3] is not None
                else None
            )

            close_price = float(
                row[4]
            )

            candles.append(
                {
                    "time": timestamp,
                    "open": open_price,
                    "high": high_price,
                    "low": low_price,
                    "close": close_price,
                }
            )

        except Exception:

            continue

    logger.info(
        f"🕯 Загружено свечей: "
        f"{len(candles)}"
    )

    return candles


# =====================================================================
# CANDLE DIRECTION
# =====================================================================

def candle_direction(candle):

    open_price = candle.get(
        "open"
    )

    close_price = candle.get(
        "close"
    )

    if (
        open_price is None
        or close_price is None
    ):
        return None

    if close_price > open_price:
        return "UP"

    elif close_price < open_price:
        return "DOWN"

    return "FLAT"


# =====================================================================
# BUILD DIRECTIONS
# =====================================================================

def build_directions(candles):

    directions = []

    for candle in candles:

        direction = candle_direction(
            candle
        )

        directions.append(
            direction
        )

    return directions


# =====================================================================
# PATTERN TO TEXT
# =====================================================================

def pattern_to_text(pattern):

    symbols = {
        "UP": "🟢",
        "DOWN": "🔴",
        "FLAT": "⚪",
    }

    return "".join(
        symbols.get(
            item,
            "?"
        )
        for item in pattern
    )


# =====================================================================
# SEARCH HISTORICAL PATTERNS
# =====================================================================

def find_pattern_matches(
    directions,
    pattern
):

    matches = []

    pattern_length = len(
        pattern
    )

    search_end = (
        len(directions)
        - EXCLUDE_LAST
    )

    if search_end <= pattern_length:

        return matches

    for i in range(
        pattern_length,
        search_end
    ):

        historical_pattern = directions[
            i - pattern_length:i
        ]

        if historical_pattern != pattern:
            continue

        next_direction = directions[i]

        if next_direction not in (
            "UP",
            "DOWN",
        ):
            continue

        matches.append(
            {
                "index": i,
                "next": next_direction,
            }
        )

    return matches


# =====================================================================
# ANALYZE PATTERN
# =====================================================================

def analyze_pattern(candles):

    if len(candles) < (
        PATTERN_LENGTH
        + MIN_MATCHES
        + 10
    ):

        logger.warning(
            "⚠️ Недостаточно свечей"
        )

        return None

    directions = build_directions(
        candles
    )

    current_pattern = directions[
        -PATTERN_LENGTH:
    ]

    if None in current_pattern:

        logger.warning(
            "⚠️ В текущем паттерне нет данных"
        )

        return None

    matches = find_pattern_matches(
        directions,
        current_pattern
    )

    up_count = sum(
        1
        for match in matches
        if match["next"] == "UP"
    )

    down_count = sum(
        1
        for match in matches
        if match["next"] == "DOWN"
    )

    total = (
        up_count
        + down_count
    )

    if total == 0:

        return {
            "pattern": current_pattern,
            "matches": 0,
            "up": 0,
            "down": 0,
            "up_probability": 0.0,
            "down_probability": 0.0,
            "prediction": None,
            "confidence": 0.0,
        }

    up_probability = (
        up_count
        / total
        * 100
    )

    down_probability = (
        down_count
        / total
        * 100
    )

    if total < MIN_MATCHES:

        prediction = None
        confidence = max(
            up_probability,
            down_probability
        )

    elif up_probability > down_probability:

        prediction = "UP"
        confidence = up_probability

    elif down_probability > up_probability:

        prediction = "DOWN"
        confidence = down_probability

    else:

        prediction = None
        confidence = 50.0

    return {
        "pattern": current_pattern,
        "matches": total,
        "up": up_count,
        "down": down_count,
        "up_probability": up_probability,
        "down_probability": down_probability,
        "prediction": prediction,
        "confidence": confidence,
    }


# =====================================================================
# SHOW ANALYSIS
# =====================================================================

def print_analysis(result, candles):

    if not result:

        return

    logger.info("")
    logger.info("=" * 60)
    logger.info("🔮 АНАЛИЗ СВЕЧНОГО ПАТТЕРНА")
    logger.info("=" * 60)

    logger.info(
        f"📊 Последняя свеча: "
        f"{candles[-1].get('time')}"
    )

    logger.info(
        f"🧩 Паттерн ({PATTERN_LENGTH}): "
        f"{pattern_to_text(result['pattern'])}"
    )

    logger.info(
        f"🔎 Найдено совпадений: "
        f"{result['matches']}"
    )

    logger.info(
        f"🟢 ВВЕРХ после паттерна: "
        f"{result['up']} "
        f"({result['up_probability']:.1f}%)"
    )

    logger.info(
        f"🔴 ВНИЗ после паттерна: "
        f"{result['down']} "
        f"({result['down_probability']:.1f}%)"
    )

    logger.info("-" * 60)

    prediction = result.get(
        "prediction"
    )

    if prediction == "UP":

        logger.info(
            f"🚀 ПРОГНОЗ: ВЫШЕ 🟢"
        )

        logger.info(
            f"🎯 Вероятность: "
            f"{result['confidence']:.1f}%"
        )

    elif prediction == "DOWN":

        logger.info(
            f"📉 ПРОГНОЗ: НИЖЕ 🔴"
        )

        logger.info(
            f"🎯 Вероятность: "
            f"{result['confidence']:.1f}%"
        )

    else:

        logger.info(
            "⏭️ ПРОГНОЗ НЕ ДАН"
        )

        logger.info(
            "Причина: мало совпадений "
            "или равные вероятности"
        )

    logger.info("=" * 60)


# =====================================================================
# BACKTEST
# =====================================================================

def run_backtest(candles):

    logger.info("")
    logger.info("=" * 60)
    logger.info("🧪 BACKTEST")
    logger.info("=" * 60)

    directions = build_directions(
        candles
    )

    start_index = max(
        500,
        PATTERN_LENGTH + 50
    )

    total_predictions = 0
    wins = 0
    losses = 0
    skipped = 0

    for end_index in range(
        start_index,
        len(candles) - 1
    ):

        history_directions = directions[
            :end_index
        ]

        current_pattern = history_directions[
            -PATTERN_LENGTH:
        ]

        if None in current_pattern:

            skipped += 1
            continue

        matches = []

        search_end = (
            len(history_directions)
            - PATTERN_LENGTH
        )

        for i in range(
            PATTERN_LENGTH,
            search_end
        ):

            historical_pattern = (
                history_directions[
                    i - PATTERN_LENGTH:i
                ]
            )

            if historical_pattern != current_pattern:
                continue

            next_direction = (
                history_directions[i]
            )

            if next_direction in (
                "UP",
                "DOWN",
            ):

                matches.append(
                    next_direction
                )

        up_count = matches.count(
            "UP"
        )

        down_count = matches.count(
            "DOWN"
        )

        total = (
            up_count
            + down_count
        )

        if total < MIN_MATCHES:

            skipped += 1
            continue

        up_probability = (
            up_count
            / total
            * 100
        )

        down_probability = (
            down_count
            / total
            * 100
        )

        if up_probability == down_probability:

            skipped += 1
            continue

        prediction = (
            "UP"
            if up_probability > down_probability
            else "DOWN"
        )

        actual = directions[
            end_index
        ]

        if actual not in (
            "UP",
            "DOWN",
        ):

            skipped += 1
            continue

        total_predictions += 1

        if prediction == actual:

            wins += 1

        else:

            losses += 1

    accuracy = (
        wins
        / total_predictions
        * 100
        if total_predictions > 0
        else 0.0
    )

    logger.info(
        f"📊 Всего прогнозов: "
        f"{total_predictions}"
    )

    logger.info(
        f"✅ Зашло: "
        f"{wins}"
    )

    logger.info(
        f"❌ Не зашло: "
        f"{losses}"
    )

    logger.info(
        f"⏭️ Пропущено: "
        f"{skipped}"
    )

    logger.info(
        f"🎯 ТОЧНОСТЬ: "
        f"{accuracy:.2f}%"
    )

    logger.info("=" * 60)

    return {
        "total": total_predictions,
        "win": wins,
        "lose": losses,
        "skipped": skipped,
        "accuracy": accuracy,
    }


# =====================================================================
# MAIN
# =====================================================================

def main():

    logger.info("")
    logger.info("=" * 65)
    logger.info("🤖 BINARIUM CANDLE PATTERN ANALYZER")
    logger.info("=" * 65)

    database = find_database()

    if not database:

        logger.error(
            "❌ Работа остановлена: "
            "нет базы данных"
        )

        return

    logger.info(
        f"📂 Используется база: "
        f"{database}"
    )

    try:

        conn = sqlite3.connect(
            database
        )

        table = find_candle_table(
            conn
        )

        if not table:

            logger.error(
                "❌ Не удалось найти таблицу свечей"
            )

            return

        columns = detect_columns(
            conn,
            table
        )

        if not columns:

            return

        candles = load_candles(
            conn,
            table,
            columns
        )

        conn.close()

        if len(candles) < 100:

            logger.error(
                f"❌ Слишком мало свечей: "
                f"{len(candles)}"
            )

            return

        # ---------------------------------------------------------
        # CURRENT ANALYSIS
        # ---------------------------------------------------------

        result = analyze_pattern(
            candles
        )

        print_analysis(
            result,
            candles
        )

        # ---------------------------------------------------------
        # BACKTEST
        # ---------------------------------------------------------

        run_backtest(
            candles
        )

    except Exception as e:

        logger.exception(
            f"❌ Критическая ошибка: {e}"
        )


# =====================================================================
# START
# =====================================================================

if __name__ == "__main__":
    main()