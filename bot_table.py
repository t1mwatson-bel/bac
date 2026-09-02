import os
import time
import json
import sqlite3
import logging
import requests
from datetime import datetime, timezone


# =====================================================================
# SETTINGS
# =====================================================================

DB_FILE = "candles.db"

# ---------------------------------------------------------------------
# API
# ---------------------------------------------------------------------
# ВСТАВЬ СЮДА URL API СВЕЧЕЙ
#
# Старый сборщик работал с 5-секундными свечами и получал ~120 свечей.
#
CANDLES_URL = os.getenv(
    "CANDLES_URL",
    ""
)

REQUEST_TIMEOUT = 15
UPDATE_INTERVAL = 5


# ---------------------------------------------------------------------
# ANALYSIS
# ---------------------------------------------------------------------

# Последних свечей для формирования паттерна
PATTERN_LENGTH = 8

# Минимум совпадений паттерна в истории
MIN_MATCHES = 5

# Минимальная уверенность для сигнала
MIN_CONFIDENCE = 60.0

# Минимальное количество свечей перед началом анализа
MIN_CANDLES_FOR_ANALYSIS = 500


# ---------------------------------------------------------------------
# EXPIRATION
# ---------------------------------------------------------------------

# 5 секунд одна свеча
# 30 секунд = 6 свечей
EXPIRATION_SECONDS = 30
EXPIRATION_CANDLES = EXPIRATION_SECONDS // 5


# ---------------------------------------------------------------------
# SEARCH LIMITS
# ---------------------------------------------------------------------

# Не искать текущий паттерн среди последних свечей
EXCLUDE_LAST = PATTERN_LENGTH + EXPIRATION_CANDLES + 2

# Максимальное количество истории для поиска
# None = вся база
MAX_HISTORY_SEARCH = None


# =====================================================================
# LOGGING
# =====================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger(__name__)


# =====================================================================
# DATABASE
# =====================================================================

def create_database():

    conn = sqlite3.connect(
        DB_FILE
    )

    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS candles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT UNIQUE,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            created_at TEXT
        )
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_candles_timestamp
        ON candles(timestamp)
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            created_at TEXT,

            signal_time TEXT,

            prediction TEXT,

            confidence REAL,

            pattern TEXT,

            matches INTEGER,

            up_count INTEGER,

            down_count INTEGER,

            entry_price REAL,

            target_time TEXT,

            target_index INTEGER,

            status TEXT,

            exit_price REAL,

            result TEXT,

            checked_at TEXT
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS statistics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            total INTEGER DEFAULT 0,

            wins INTEGER DEFAULT 0,

            losses INTEGER DEFAULT 0,

            accuracy REAL DEFAULT 0,

            updated_at TEXT
        )
        """
    )

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM statistics
        """
    )

    exists = cursor.fetchone()[0]

    if exists == 0:

        cursor.execute(
            """
            INSERT INTO statistics (
                total,
                wins,
                losses,
                accuracy,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                0,
                0,
                0,
                0.0,
                datetime.now(
                    timezone.utc
                ).isoformat()
            )
        )

    conn.commit()

    return conn


# =====================================================================
# SAVE CANDLES
# =====================================================================

def save_candles(
    conn,
    candles
):

    cursor = conn.cursor()

    saved = 0

    for candle in candles:

        try:

            timestamp = str(
                candle["timestamp"]
            )

            open_price = float(
                candle["open"]
            )

            high_price = float(
                candle["high"]
            )

            low_price = float(
                candle["low"]
            )

            close_price = float(
                candle["close"]
            )

            cursor.execute(
                """
                INSERT OR REPLACE INTO candles (
                    timestamp,
                    open,
                    high,
                    low,
                    close,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    timestamp,
                    open_price,
                    high_price,
                    low_price,
                    close_price,
                    datetime.now(
                        timezone.utc
                    ).isoformat()
                )
            )

            saved += 1

        except Exception as e:

            logger.debug(
                f"Ошибка сохранения свечи: {e}"
            )

    conn.commit()

    return saved


# =====================================================================
# LOAD CANDLES
# =====================================================================

def load_candles(conn):

    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT
            timestamp,
            open,
            high,
            low,
            close
        FROM candles
        ORDER BY timestamp ASC
        """
    )

    rows = cursor.fetchall()

    candles = []

    for row in rows:

        candles.append(
            {
                "timestamp": row[0],
                "open": row[1],
                "high": row[2],
                "low": row[3],
                "close": row[4],
            }
        )

    return candles


# =====================================================================
# COUNT CANDLES
# =====================================================================

def get_candle_count(conn):

    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM candles
        """
    )

    return cursor.fetchone()[0]


# =====================================================================
# API PARSING
# =====================================================================

def normalize_timestamp(value):

    if value is None:
        return None

    try:

        if isinstance(
            value,
            (int, float)
        ):

            # milliseconds
            if value > 100000000000:

                value = value / 1000

            return datetime.fromtimestamp(
                value,
                tz=timezone.utc
            ).isoformat()

        return str(value)

    except Exception:

        return str(value)


def parse_candle(item):

    if not isinstance(
        item,
        dict
    ):
        return None

    timestamp = (
        item.get("timestamp")
        or item.get("time")
        or item.get("t")
        or item.get("date")
        or item.get("open_time")
    )

    open_price = (
        item.get("open")
        or item.get("o")
    )

    high_price = (
        item.get("high")
        or item.get("h")
    )

    low_price = (
        item.get("low")
        or item.get("l")
    )

    close_price = (
        item.get("close")
        or item.get("c")
        or item.get("price")
    )

    if timestamp is None:
        return None

    if open_price is None:
        return None

    if close_price is None:
        return None

    try:

        return {
            "timestamp": normalize_timestamp(
                timestamp
            ),

            "open": float(
                open_price
            ),

            "high": float(
                high_price
                if high_price is not None
                else max(
                    float(open_price),
                    float(close_price)
                )
            ),

            "low": float(
                low_price
                if low_price is not None
                else min(
                    float(open_price),
                    float(close_price)
                )
            ),

            "close": float(
                close_price
            ),
        }

    except Exception:

        return None


# =====================================================================
# EXTRACT CANDLES FROM JSON
# =====================================================================

def extract_candle_list(data):

    if isinstance(
        data,
        list
    ):
        return data

    if not isinstance(
        data,
        dict
    ):
        return []

    possible_keys = [

        "candles",
        "data",
        "result",
        "items",
        "history",
        "ticks",

    ]

    for key in possible_keys:

        value = data.get(
            key
        )

        if isinstance(
            value,
            list
        ):
            return value

        if isinstance(
            value,
            dict
        ):

            nested = extract_candle_list(
                value
            )

            if nested:
                return nested

    for value in data.values():

        if isinstance(
            value,
            dict
        ):

            nested = extract_candle_list(
                value
            )

            if nested:
                return nested

        elif isinstance(
            value,
            list
        ):

            if len(value) > 0:

                if isinstance(
                    value[0],
                    dict
                ):

                    return value

    return []


# =====================================================================
# FETCH CANDLES
# =====================================================================

def fetch_candles():

    if not CANDLES_URL:

        logger.warning(
            "⚠️ CANDLES_URL пока не задан. "
            "Бот создаёт базу и ожидает API."
        )

        return []

    try:

        now = datetime.now(
            timezone.utc
        )

        end_timestamp = now.isoformat(
            timespec="milliseconds"
        ).replace(
            "+00:00",
            "Z"
        )

        logger.info(
            f"Запрос свечей: "
            f"{end_timestamp}"
        )

        response = requests.get(
            CANDLES_URL,
            timeout=REQUEST_TIMEOUT,
            headers={
                "User-Agent":
                    "Mozilla/5.0"
            }
        )

        logger.info(
            f"HTTP {response.status_code} | "
            f"{len(response.content) / 1024:.2f} KB"
        )

        if response.status_code != 200:

            return []

        try:

            data = response.json()

        except Exception:

            logger.error(
                "❌ API вернул не JSON"
            )

            return []

        raw_candles = extract_candle_list(
            data
        )

        candles = []

        for item in raw_candles:

            candle = parse_candle(
                item
            )

            if candle:

                candles.append(
                    candle
                )

        candles.sort(
            key=lambda x: x["timestamp"]
        )

        logger.info(
            f"Получено свечей: "
            f"{len(candles)}"
        )

        return candles

    except Exception as e:

        logger.error(
            f"❌ Ошибка получения свечей: {e}"
        )

        return []


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

    if close_price < open_price:

        return "DOWN"

    return "FLAT"


# =====================================================================
# BUILD DIRECTIONS
# =====================================================================

def build_directions(candles):

    return [
        candle_direction(candle)
        for candle in candles
    ]


# =====================================================================
# PATTERN TEXT
# =====================================================================

def pattern_to_text(pattern):

    symbols = {

        "UP": "🟢",

        "DOWN": "🔴",

        "FLAT": "⚪",

    }

    return "".join(
        symbols.get(
            direction,
            "❓"
        )
        for direction in pattern
    )


# =====================================================================
# FIND HISTORICAL PATTERNS
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

        # -------------------------------------------------------------
        # Смотрим направление не следующей 5-секундной свечи,
        # а через 30 секунд
        # -------------------------------------------------------------

        future_index = (
            i
            + EXPIRATION_CANDLES
            - 1
        )

        if future_index >= len(
            directions
        ):
            continue

        future_direction = directions[
            future_index
        ]

        if future_direction not in (
            "UP",
            "DOWN"
        ):
            continue

        matches.append(
            future_direction
        )

    return matches


# =====================================================================
# ANALYZE PATTERN
# =====================================================================

def analyze_pattern(candles):

    if len(candles) < MIN_CANDLES_FOR_ANALYSIS:

        return None

    directions = build_directions(
        candles
    )

    current_pattern = directions[
        -PATTERN_LENGTH:
    ]

    if None in current_pattern:

        return None

    if "FLAT" in current_pattern:

        return None

    matches = find_pattern_matches(
        directions,
        current_pattern
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

    prediction = None

    confidence = max(
        up_probability,
        down_probability
    )

    if total >= MIN_MATCHES:

        if (
            up_probability > down_probability
            and up_probability >= MIN_CONFIDENCE
        ):

            prediction = "UP"

        elif (
            down_probability > up_probability
            and down_probability >= MIN_CONFIDENCE
        ):

            prediction = "DOWN"

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
# CHECK ACTIVE PREDICTION
# =====================================================================

def has_active_prediction(conn):

    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM predictions
        WHERE status = 'ACTIVE'
        """
    )

    return cursor.fetchone()[0] > 0


# =====================================================================
# CREATE PREDICTION
# =====================================================================

def create_prediction(
    conn,
    result,
    candles
):

    if not result:

        return False

    prediction = result.get(
        "prediction"
    )

    if prediction not in (
        "UP",
        "DOWN"
    ):

        return False

    if has_active_prediction(
        conn
    ):

        return False

    if len(candles) == 0:

        return False

    entry_candle = candles[-1]

    entry_price = float(
        entry_candle["close"]
    )

    signal_time = entry_candle[
        "timestamp"
    ]

    target_index = (
        len(candles)
        - 1
        + EXPIRATION_CANDLES
    )

    now = datetime.now(
        timezone.utc
    ).isoformat()

    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO predictions (

            created_at,
            signal_time,
            prediction,
            confidence,
            pattern,
            matches,
            up_count,
            down_count,
            entry_price,
            target_time,
            target_index,
            status,
            exit_price,
            result,
            checked_at

        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (

            now,

            signal_time,

            prediction,

            result["confidence"],

            json.dumps(
                result["pattern"]
            ),

            result["matches"],

            result["up"],

            result["down"],

            entry_price,

            None,

            target_index,

            "ACTIVE",

            None,

            None,

            None,

        )
    )

    conn.commit()

    logger.info("")
    logger.info("=" * 65)
    logger.info("🔮 НОВЫЙ СИГНАЛ")
    logger.info("=" * 65)

    logger.info(
        f"🕯 Время: "
        f"{signal_time}"
    )

    logger.info(
        f"💰 Цена входа: "
        f"{entry_price}"
    )

    logger.info(
        f"🧩 Паттерн: "
        f"{pattern_to_text(result['pattern'])}"
    )

    logger.info(
        f"🔎 Совпадений: "
        f"{result['matches']}"
    )

    logger.info(
        f"🟢 Исторически вверх: "
        f"{result['up']} "
        f"({result['up_probability']:.1f}%)"
    )

    logger.info(
        f"🔴 Исторически вниз: "
        f"{result['down']} "
        f"({result['down_probability']:.1f}%)"
    )

    logger.info("-" * 65)

    if prediction == "UP":

        logger.info(
            "🚀 СИГНАЛ: ВЫШЕ ⬆️"
        )

    else:

        logger.info(
            "📉 СИГНАЛ: НИЖЕ ⬇️"
        )

    logger.info(
        f"⏱ ЭКСПИРАЦИЯ: "
        f"{EXPIRATION_SECONDS} секунд"
    )

    logger.info(
        f"🎯 Уверенность: "
        f"{result['confidence']:.1f}%"
    )

    logger.info("=" * 65)

    return True


# =====================================================================
# CHECK PREDICTIONS
# =====================================================================

def check_predictions(
    conn,
    candles
):

    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT
            id,
            prediction,
            entry_price,
            target_index
        FROM predictions
        WHERE status = 'ACTIVE'
        """
    )

    predictions = cursor.fetchall()

    if not predictions:

        return

    for row in predictions:

        prediction_id = row[0]

        prediction = row[1]

        entry_price = float(
            row[2]
        )

        target_index = int(
            row[3]
        )

        if len(candles) <= target_index:

            continue

        exit_candle = candles[
            target_index
        ]

        exit_price = float(
            exit_candle["close"]
        )

        checked_at = datetime.now(
            timezone.utc
        ).isoformat()

        # -------------------------------------------------------------
        # RESULT
        # -------------------------------------------------------------

        if prediction == "UP":

            win = (
                exit_price > entry_price
            )

        else:

            win = (
                exit_price < entry_price
            )

        result_text = (
            "WIN"
            if win
            else "LOSS"
        )

        status = "FINISHED"

        cursor.execute(
            """
            UPDATE predictions
            SET
                status = ?,
                exit_price = ?,
                result = ?,
                checked_at = ?,
                target_time = ?
            WHERE id = ?
            """,
            (

                status,

                exit_price,

                result_text,

                checked_at,

                exit_candle["timestamp"],

                prediction_id,

            )
        )

        conn.commit()

        logger.info("")
        logger.info("=" * 65)
        logger.info("📊 ПРОВЕРКА СИГНАЛА")
        logger.info("=" * 65)

        logger.info(
            f"💰 Цена входа: "
            f"{entry_price}"
        )

        logger.info(
            f"💰 Цена через "
            f"{EXPIRATION_SECONDS} сек: "
            f"{exit_price}"
        )

        logger.info(
            f"🔮 Прогноз: "
            f"{prediction}"
        )

        if win:

            logger.info(
                "✅ ЗАШЛО"
            )

        else:

            logger.info(
                "❌ НЕ ЗАШЛО"
            )

        logger.info("=" * 65)

        update_statistics(
            conn
        )


# =====================================================================
# UPDATE STATISTICS
# =====================================================================

def update_statistics(conn):

    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM predictions
        WHERE result IN ('WIN', 'LOSS')
        """
    )

    total = cursor.fetchone()[0]

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM predictions
        WHERE result = 'WIN'
        """
    )

    wins = cursor.fetchone()[0]

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM predictions
        WHERE result = 'LOSS'
        """
    )

    losses = cursor.fetchone()[0]

    accuracy = (

        wins / total * 100

        if total > 0

        else 0.0

    )

    cursor.execute(
        """
        UPDATE statistics
        SET
            total = ?,
            wins = ?,
            losses = ?,
            accuracy = ?,
            updated_at = ?
        WHERE id = 1
        """,
        (

            total,

            wins,

            losses,

            accuracy,

            datetime.now(
                timezone.utc
            ).isoformat()

        )
    )

    conn.commit()

    logger.info("")
    logger.info("📊 ОБЩАЯ СТАТИСТИКА")

    logger.info(
        f"🎯 Всего сигналов: "
        f"{total}"
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
        f"📈 Точность: "
        f"{accuracy:.2f}%"
    )


# =====================================================================
# SHOW WAITING STATUS
# =====================================================================

def show_status(
    candles,
    result
):

    total = len(
        candles
    )

    logger.info(
        f"📚 Всего свечей в истории: "
        f"{total}"
    )

    if total < MIN_CANDLES_FOR_ANALYSIS:

        need = (
            MIN_CANDLES_FOR_ANALYSIS
            - total
        )

        logger.info(
            f"⏳ Накопление данных... "
            f"ещё нужно минимум {need} свечей"
        )

        return

    if not result:

        logger.info(
            "⏳ Недостаточно данных для паттерна"
        )

        return

    logger.info(
        f"🧩 Текущий паттерн: "
        f"{pattern_to_text(result['pattern'])}"
    )

    logger.info(
        f"🔎 Найдено совпадений: "
        f"{result['matches']}"
    )

    if result["prediction"] is None:

        logger.info(
            "⏭️ Сигнал не дан: "
            "недостаточно сильная закономерность"
        )


# =====================================================================
# MAIN LOOP
# =====================================================================

def main():

    logger.info("")
    logger.info("=" * 70)
    logger.info("🤖 BINARIUM AUTO ANALYZER")
    logger.info("=" * 70)

    logger.info(
        "Режим:"
    )

    logger.info(
        f"🕯 Интервал свечи: 5 секунд"
    )

    logger.info(
        f"🧩 Размер паттерна: "
        f"{PATTERN_LENGTH} свечей"
    )

    logger.info(
        f"⏱ Экспирация: "
        f"{EXPIRATION_SECONDS} секунд"
    )

    logger.info(
        f"🎯 Минимум совпадений: "
        f"{MIN_MATCHES}"
    )

    logger.info(
        f"📊 Минимальная уверенность: "
        f"{MIN_CONFIDENCE}%"
    )

    # -------------------------------------------------------------
    # DATABASE
    # -------------------------------------------------------------

    conn = create_database()

    logger.info(
        f"✅ База данных готова: "
        f"{DB_FILE}"
    )

    cycle = 0

    try:

        while True:

            cycle += 1

            logger.info("")
            logger.info(
                f"ЦИКЛ #{cycle} | "
                f"{datetime.now(timezone.utc).isoformat()}"
            )

            # -----------------------------------------------------
            # FETCH
            # -----------------------------------------------------

            new_candles = fetch_candles()

            if new_candles:

                saved = save_candles(
                    conn,
                    new_candles
                )

                logger.info(
                    f"Обновление завершено: "
                    f"сохранено/обновлено={saved}"
                )

            # -----------------------------------------------------
            # LOAD
            # -----------------------------------------------------

            candles = load_candles(
                conn
            )

            # -----------------------------------------------------
            # LAST CANDLE
            # -----------------------------------------------------

            if candles:

                logger.info(
                    f"Последняя свеча: "
                    f"{candles[-1]['timestamp']}"
                )

            # -----------------------------------------------------
            # CHECK OLD SIGNAL
            # -----------------------------------------------------

            check_predictions(
                conn,
                candles
            )

            # -----------------------------------------------------
            # ANALYZE
            # -----------------------------------------------------

            result = analyze_pattern(
                candles
            )

            show_status(
                candles,
                result
            )

            # -----------------------------------------------------
            # NEW SIGNAL
            # -----------------------------------------------------

            if result:

                create_prediction(
                    conn,
                    result,
                    candles
                )

            # -----------------------------------------------------
            # WAIT
            # -----------------------------------------------------

            time.sleep(
                UPDATE_INTERVAL
            )

    except KeyboardInterrupt:

        logger.info(
            "🛑 Бот остановлен пользователем"
        )

    except Exception as e:

        logger.exception(
            f"❌ Критическая ошибка: {e}"
        )

    finally:

        conn.close()

        logger.info(
            "🔒 База данных закрыта"
        )


# =====================================================================
# START
# =====================================================================

if __name__ == "__main__":
    main()