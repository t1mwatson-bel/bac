import os
import sys
import time
import sqlite3
import logging
import traceback
from datetime import datetime, timedelta, timezone

import requests


# =====================================================================
# SETTINGS
# =====================================================================

BASE_URL = "https://api.binarium.com"

# Твой Asset ID
ASSET_ID = 43

# Интервал свечей
DETAILIZATION = "5s"

# База данных
DB_FILE = "binarium_history.db"


# =====================================================================
# СБОР ИСТОРИИ
# =====================================================================

# При первом запуске загрузить столько часов истории
HISTORY_HOURS = 12

# Размер одного запроса истории
CHUNK_MINUTES = 60

# Каждые сколько секунд обновлять данные
UPDATE_INTERVAL = 5

# Сколько последних минут запрашивать при live-обновлении
LIVE_WINDOW_MINUTES = 10

REQUEST_TIMEOUT = 30
MAX_RETRIES = 3


# =====================================================================
# АНАЛИЗ ПАТТЕРНОВ
# =====================================================================

# Сколько последних свечей составляют паттерн
PATTERN_LENGTH = 8

# Минимальное количество похожих паттернов
MIN_MATCHES = 5

# Сколько свечей минимум нужно накопить перед анализом
MIN_CANDLES_FOR_ANALYSIS = 500

# Минимальная вероятность для сигнала
MIN_CONFIDENCE = 60.0

# Не искать паттерн слишком близко к текущему
EXCLUDE_LAST = PATTERN_LENGTH + 2


# =====================================================================
# ЭКСПИРАЦИЯ
# =====================================================================

# Свеча = 5 секунд
CANDLE_SECONDS = 5

# Экспирация сигнала = 30 секунд
EXPIRATION_SECONDS = 30

# Через сколько свечей проверять результат
EXPIRATION_CANDLES = (
    EXPIRATION_SECONDS // CANDLE_SECONDS
)


# =====================================================================
# ЛОГИРОВАНИЕ
# =====================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("BINARIUM")


# =====================================================================
# HTTP SESSION
# =====================================================================

session = requests.Session()

session.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/150.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://binarium.com/",
        "Origin": "https://binarium.com",
        "Connection": "keep-alive",
    }
)


# =====================================================================
# DATABASE
# =====================================================================

def init_database():

    conn = sqlite3.connect(DB_FILE)

    cursor = conn.cursor()

    # -------------------------------------------------------------
    # СВЕЧИ
    # -------------------------------------------------------------

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS candles (
            asset_id INTEGER NOT NULL,
            time TEXT NOT NULL,
            timestamp REAL NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            PRIMARY KEY (asset_id, time)
        )
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_candles_timestamp
        ON candles(asset_id, timestamp)
        """
    )

    # -------------------------------------------------------------
    # СИГНАЛЫ
    # -------------------------------------------------------------

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            signal_time TEXT NOT NULL,
            signal_timestamp REAL NOT NULL,

            entry_price REAL NOT NULL,

            prediction TEXT NOT NULL,

            confidence REAL NOT NULL,

            matches INTEGER NOT NULL,

            up_count INTEGER NOT NULL,
            down_count INTEGER NOT NULL,

            pattern TEXT NOT NULL,

            expiration_seconds INTEGER NOT NULL,

            checked INTEGER DEFAULT 0,

            result TEXT,

            exit_price REAL,

            checked_time TEXT
        )
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_signals_checked
        ON signals(checked)
        """
    )

    conn.commit()
    conn.close()

    logger.info(
        f"✅ База данных готова: {DB_FILE}"
    )


# =====================================================================
# TIME
# =====================================================================

def utc_now():

    return datetime.now(
        timezone.utc
    )


def format_api_time(dt):

    dt = dt.astimezone(
        timezone.utc
    )

    return dt.strftime(
        "%Y-%m-%dT%H:%M:%S.000Z"
    )


def parse_api_time(value):

    if not value:
        return None

    value = str(value).strip()

    if value.endswith("Z"):
        value = value[:-1] + "+00:00"

    try:

        dt = datetime.fromisoformat(
            value
        )

        if dt.tzinfo is None:

            dt = dt.replace(
                tzinfo=timezone.utc
            )

        return dt.astimezone(
            timezone.utc
        )

    except Exception:

        return None


def timestamp_from_api_time(value):

    dt = parse_api_time(
        value
    )

    if dt is None:
        return 0.0

    return dt.timestamp()


# =====================================================================
# REQUEST CANDLES
# =====================================================================

def request_candles(
    start_dt,
    end_dt
):

    url = (
        f"{BASE_URL}"
        f"/api/v1/assets/"
        f"{ASSET_ID}/candles"
    )

    params = {
        "from": format_api_time(
            start_dt
        ),
        "to": format_api_time(
            end_dt
        ),
        "detalization": DETAILIZATION,
    }

    for attempt in range(
        1,
        MAX_RETRIES + 1
    ):

        try:

            logger.info(
                "Запрос свечей: %s -> %s",
                format_api_time(start_dt),
                format_api_time(end_dt),
            )

            response = session.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT,
            )

            logger.info(
                "HTTP %s | %.2f KB",
                response.status_code,
                len(response.content) / 1024,
            )

            if response.status_code != 200:

                logger.warning(
                    f"⚠️ HTTP {response.status_code}"
                )

                if attempt < MAX_RETRIES:

                    time.sleep(
                        2 * attempt
                    )

                    continue

                return []

            try:

                result = response.json()

            except Exception:

                logger.error(
                    "❌ Не удалось разобрать JSON"
                )

                return []

            if "errors" in result:

                logger.error(
                    f"❌ API ошибка: "
                    f"{result['errors']}"
                )

                return []

            data = result.get(
                "data"
            )

            if not isinstance(
                data,
                list
            ):

                logger.warning(
                    "⚠️ API не вернул data"
                )

                return []

            logger.info(
                f"Получено свечей: "
                f"{len(data)}"
            )

            return data

        except requests.RequestException as e:

            logger.warning(
                f"⚠️ HTTP ошибка: {e}"
            )

            if attempt < MAX_RETRIES:

                time.sleep(
                    2 * attempt
                )

                continue

            return []

        except Exception:

            logger.exception(
                "❌ Ошибка запроса"
            )

            return []

    return []


# =====================================================================
# NORMALIZE CANDLES
# =====================================================================

def normalize_candles(raw_candles):

    result = []

    for item in raw_candles:

        if not isinstance(
            item,
            dict
        ):
            continue

        candle_time = item.get(
            "time"
        )

        if not candle_time:
            continue

        try:

            open_price = float(
                item["open"]
            )

            high_price = float(
                item["high"]
            )

            low_price = float(
                item["low"]
            )

            close_price = float(
                item["close"]
            )

        except (
            KeyError,
            TypeError,
            ValueError
        ):

            continue

        timestamp = timestamp_from_api_time(
            candle_time
        )

        if timestamp <= 0:
            continue

        result.append(
            {
                "asset_id": ASSET_ID,
                "time": candle_time,
                "timestamp": timestamp,
                "open": open_price,
                "high": high_price,
                "low": low_price,
                "close": close_price,
            }
        )

    return result


# =====================================================================
# SAVE CANDLES
# =====================================================================

def save_candles(candles):

    if not candles:
        return 0

    conn = sqlite3.connect(
        DB_FILE
    )

    cursor = conn.cursor()

    saved = 0

    try:

        for candle in candles:

            cursor.execute(
                """
                INSERT OR REPLACE INTO candles (
                    asset_id,
                    time,
                    timestamp,
                    open,
                    high,
                    low,
                    close
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candle["asset_id"],
                    candle["time"],
                    candle["timestamp"],
                    candle["open"],
                    candle["high"],
                    candle["low"],
                    candle["close"],
                ),
            )

            saved += 1

        conn.commit()

    finally:

        conn.close()

    return saved


# =====================================================================
# DATABASE STATS
# =====================================================================

def get_database_stats():

    conn = sqlite3.connect(
        DB_FILE
    )

    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT
            COUNT(*),
            MIN(time),
            MAX(time)
        FROM candles
        WHERE asset_id = ?
        """,
        (
            ASSET_ID,
        ),
    )

    row = cursor.fetchone()

    conn.close()

    if not row:

        return (
            0,
            None,
            None,
        )

    return (
        row[0],
        row[1],
        row[2],
    )


# =====================================================================
# LOAD CANDLES FROM DATABASE
# =====================================================================

def load_candles_from_database():

    conn = sqlite3.connect(
        DB_FILE
    )

    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT
            time,
            timestamp,
            open,
            high,
            low,
            close
        FROM candles
        WHERE asset_id = ?
        ORDER BY timestamp ASC
        """,
        (
            ASSET_ID,
        ),
    )

    rows = cursor.fetchall()

    conn.close()

    candles = []

    for row in rows:

        candles.append(
            {
                "time": row[0],
                "timestamp": row[1],
                "open": row[2],
                "high": row[3],
                "low": row[4],
                "close": row[5],
            }
        )

    return candles


# =====================================================================
# DOWNLOAD HISTORY
# =====================================================================

def download_history():

    count, _, _ = get_database_stats()

    if count > 0:

        logger.info(
            f"📚 База уже содержит "
            f"{count} свечей"
        )

        return

    logger.info("")
    logger.info("=" * 70)
    logger.info("📥 ПЕРВАЯ ЗАГРУЗКА ИСТОРИИ")
    logger.info("=" * 70)

    end_dt = utc_now()

    start_dt = (
        end_dt
        - timedelta(
            hours=HISTORY_HOURS
        )
    )

    current = start_dt

    total_saved = 0
    chunk_number = 0

    while current < end_dt:

        chunk_number += 1

        chunk_end = (
            current
            + timedelta(
                minutes=CHUNK_MINUTES
            )
        )

        if chunk_end > end_dt:

            chunk_end = end_dt

        logger.info("")
        logger.info(
            f"📦 ЧАНК #{chunk_number}"
        )

        raw = request_candles(
            current,
            chunk_end
        )

        candles = normalize_candles(
            raw
        )

        saved = save_candles(
            candles
        )

        total_saved += saved

        logger.info(
            f"💾 Сохранено: {saved}"
        )

        current = chunk_end

        time.sleep(0.3)

    logger.info("")
    logger.info("=" * 70)
    logger.info(
        f"✅ ИСТОРИЯ ЗАГРУЖЕНА"
    )
    logger.info(
        f"💾 Всего обработано: "
        f"{total_saved}"
    )
    logger.info("=" * 70)


# =====================================================================
# UPDATE RECENT CANDLES
# =====================================================================

def update_recent_candles():

    end_dt = utc_now()

    start_dt = (
        end_dt
        - timedelta(
            minutes=LIVE_WINDOW_MINUTES
        )
    )

    raw = request_candles(
        start_dt,
        end_dt
    )

    if not raw:

        logger.warning(
            "⚠️ Новые свечи не получены"
        )

        return 0

    candles = normalize_candles(
        raw
    )

    saved = save_candles(
        candles
    )

    return saved


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
# FIND PATTERN MATCHES
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

    if len(candles) < MIN_CANDLES_FOR_ANALYSIS:

        return None

    directions = build_directions(
        candles
    )

    current_pattern = directions[
        -PATTERN_LENGTH:
    ]

    if len(current_pattern) < PATTERN_LENGTH:

        return None

    if None in current_pattern:

        return None

    if "FLAT" in current_pattern:

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

    if total < MIN_MATCHES:

        return {
            "pattern": current_pattern,
            "matches": total,
            "up": up_count,
            "down": down_count,
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
    confidence = 0.0

    if up_probability > down_probability:

        prediction = "UP"
        confidence = up_probability

    elif down_probability > up_probability:

        prediction = "DOWN"
        confidence = down_probability

    if confidence < MIN_CONFIDENCE:

        prediction = None

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
# CHECK DUPLICATE SIGNAL
# =====================================================================

def has_active_signal():

    conn = sqlite3.connect(
        DB_FILE
    )

    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM signals
        WHERE checked = 0
        """
    )

    count = cursor.fetchone()[0]

    conn.close()

    return count > 0


# =====================================================================
# SAVE SIGNAL
# =====================================================================

def save_signal(
    result,
    candle
):

    prediction = result.get(
        "prediction"
    )

    if prediction not in (
        "UP",
        "DOWN",
    ):

        return False

    if has_active_signal():

        logger.info(
            "⏳ Уже есть активный сигнал. "
            "Ждём проверки."
        )

        return False

    signal_time = candle.get(
        "time"
    )

    signal_timestamp = candle.get(
        "timestamp"
    )

    entry_price = candle.get(
        "close"
    )

    pattern_text = ",".join(
        result.get(
            "pattern",
            []
        )
    )

    conn = sqlite3.connect(
        DB_FILE
    )

    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO signals (
            signal_time,
            signal_timestamp,
            entry_price,
            prediction,
            confidence,
            matches,
            up_count,
            down_count,
            pattern,
            expiration_seconds,
            checked
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
        """,
        (
            signal_time,
            signal_timestamp,
            entry_price,
            prediction,
            result.get("confidence", 0.0),
            result.get("matches", 0),
            result.get("up", 0),
            result.get("down", 0),
            pattern_text,
            EXPIRATION_SECONDS,
        ),
    )

    conn.commit()
    conn.close()

    return True


# =====================================================================
# SHOW SIGNAL
# =====================================================================

def print_signal(
    result,
    candle
):

    prediction = result.get(
        "prediction"
    )

    if prediction not in (
        "UP",
        "DOWN",
    ):

        return

    logger.info("")
    logger.info("=" * 65)
    logger.info("🚨 НОВЫЙ СИГНАЛ")
    logger.info("=" * 65)

    logger.info(
        f"🕯 Время: "
        f"{candle.get('time')}"
    )

    logger.info(
        f"💰 Цена входа: "
        f"{candle.get('close')}"
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
        f"🟢 Вверх: "
        f"{result['up']} "
        f"({result['up_probability']:.1f}%)"
    )

    logger.info(
        f"🔴 Вниз: "
        f"{result['down']} "
        f"({result['down_probability']:.1f}%)"
    )

    logger.info("-" * 65)

    if prediction == "UP":

        logger.info(
            "🚀 СИГНАЛ: ВЫШЕ 🟢"
        )

    else:

        logger.info(
            "📉 СИГНАЛ: НИЖЕ 🔴"
        )

    logger.info(
        f"🎯 Вероятность: "
        f"{result['confidence']:.1f}%"
    )

    logger.info(
        f"⏱ Экспирация: "
        f"{EXPIRATION_SECONDS} секунд"
    )

    logger.info("=" * 65)


# =====================================================================
# CHECK SIGNALS
# =====================================================================

def check_pending_signals(candles):

    if not candles:

        return

    latest_candle = candles[-1]

    latest_timestamp = latest_candle.get(
        "timestamp"
    )

    if latest_timestamp is None:

        return

    conn = sqlite3.connect(
        DB_FILE
    )

    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT
            id,
            signal_time,
            signal_timestamp,
            entry_price,
            prediction,
            confidence,
            expiration_seconds
        FROM signals
        WHERE checked = 0
        ORDER BY id ASC
        """
    )

    signals = cursor.fetchall()

    if not signals:

        conn.close()
        return

    for signal in signals:

        signal_id = signal[0]
        signal_time = signal[1]
        signal_timestamp = signal[2]
        entry_price = signal[3]
        prediction = signal[4]
        confidence = signal[5]
        expiration_seconds = signal[6]

        expiration_timestamp = (
            signal_timestamp
            + expiration_seconds
        )

        if latest_timestamp < expiration_timestamp:

            continue

        cursor.execute(
            """
            SELECT
                time,
                close
            FROM candles
            WHERE asset_id = ?
            AND timestamp >= ?
            ORDER BY timestamp ASC
            LIMIT 1
            """,
            (
                ASSET_ID,
                expiration_timestamp,
            ),
        )

        exit_row = cursor.fetchone()

        if not exit_row:

            continue

        exit_time = exit_row[0]
        exit_price = exit_row[1]

        result = "LOSE"

        if prediction == "UP":

            if exit_price > entry_price:
                result = "WIN"

        elif prediction == "DOWN":

            if exit_price < entry_price:
                result = "WIN"

        cursor.execute(
            """
            UPDATE signals
            SET
                checked = 1,
                result = ?,
                exit_price = ?,
                checked_time = ?
            WHERE id = ?
            """,
            (
                result,
                exit_price,
                exit_time,
                signal_id,
            ),
        )

        conn.commit()

        logger.info("")
        logger.info("=" * 65)
        logger.info("🔍 ПРОВЕРКА СИГНАЛА")
        logger.info("=" * 65)

        logger.info(
            f"🕯 Сигнал был: "
            f"{signal_time}"
        )

        logger.info(
            f"💰 Вход: "
            f"{entry_price}"
        )

        logger.info(
            f"💰 Цена через "
            f"{expiration_seconds} сек: "
            f"{exit_price}"
        )

        logger.info(
            f"🎯 Прогноз: "
            f"{prediction}"
        )

        logger.info(
            f"📊 Уверенность: "
            f"{confidence:.1f}%"
        )

        if result == "WIN":

            logger.info("")
            logger.info(
                "✅ ЗАШЛО!"
            )

        else:

            logger.info("")
            logger.info(
                "❌ НЕ ЗАШЛО!"
            )

        logger.info("=" * 65)

    conn.close()


# =====================================================================
# SIGNAL STATISTICS
# =====================================================================

def print_statistics():

    conn = sqlite3.connect(
        DB_FILE
    )

    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT
            COUNT(*),
            SUM(
                CASE
                    WHEN result = 'WIN'
                    THEN 1
                    ELSE 0
                END
            ),
            SUM(
                CASE
                    WHEN result = 'LOSE'
                    THEN 1
                    ELSE 0
                END
            )
        FROM signals
        WHERE checked = 1
        """
    )

    row = cursor.fetchone()

    conn.close()

    total = row[0] or 0
    wins = row[1] or 0
    losses = row[2] or 0

    accuracy = 0.0

    if total > 0:

        accuracy = (
            wins
            / total
            * 100
        )

    logger.info("")
    logger.info("📊 СТАТИСТИКА СИГНАЛОВ")

    logger.info(
        f"🎯 Всего: {total}"
    )

    logger.info(
        f"✅ Зашло: {wins}"
    )

    logger.info(
        f"❌ Не зашло: {losses}"
    )

    logger.info(
        f"📈 Точность: "
        f"{accuracy:.2f}%"
    )


# =====================================================================
# CURRENT ANALYSIS
# =====================================================================

def print_current_analysis(
    result,
    candles
):

    if not result:

        return

    logger.info("")
    logger.info("-" * 65)

    logger.info(
        f"🧩 Текущий паттерн: "
        f"{pattern_to_text(result['pattern'])}"
    )

    logger.info(
        f"🔎 Найдено совпадений: "
        f"{result['matches']}"
    )

    if result["matches"] > 0:

        logger.info(
            f"🟢 Вверх: "
            f"{result['up']} "
            f"({result['up_probability']:.1f}%)"
        )

        logger.info(
            f"🔴 Вниз: "
            f"{result['down']} "
            f"({result['down_probability']:.1f}%)"
        )

    if result.get("prediction"):

        logger.info(
            f"🎯 Возможный сигнал: "
            f"{result['prediction']}"
        )

    else:

        logger.info(
            "⏭️ Сигнал пока не подходит"
        )

    logger.info("-" * 65)


# =====================================================================
# MAIN
# =====================================================================

def main():

    logger.info("")
    logger.info("=" * 70)
    logger.info("🤖 BINARIUM AUTO ANALYZER")
    logger.info("=" * 70)

    logger.info("Режим:")

    logger.info(
        f"🕯 Интервал свечи: "
        f"{DETAILIZATION}"
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

    init_database()

    # -------------------------------------------------------------
    # HISTORY
    # -------------------------------------------------------------

    download_history()

    # -------------------------------------------------------------
    # STATS
    # -------------------------------------------------------------

    count, first_time, last_time = (
        get_database_stats()
    )

    logger.info("")
    logger.info(
        f"📚 Свечей в базе: {count}"
    )

    logger.info(
        f"🕐 Первая: {first_time}"
    )

    logger.info(
        f"🕐 Последняя: {last_time}"
    )

    cycle = 0

    # -------------------------------------------------------------
    # MAIN LOOP
    # -------------------------------------------------------------

    while True:

        cycle += 1

        try:

            now = utc_now()

            logger.info("")
            logger.info("=" * 70)

            logger.info(
                f"🔄 ЦИКЛ #{cycle} | "
                f"{format_api_time(now)}"
            )

            # -----------------------------------------------------
            # UPDATE
            # -----------------------------------------------------

            saved = update_recent_candles()

            count, _, last_time = (
                get_database_stats()
            )

            logger.info(
                f"💾 Сохранено/обновлено: "
                f"{saved}"
            )

            logger.info(
                f"📚 Всего свечей: "
                f"{count}"
            )

            logger.info(
                f"🕯 Последняя свеча: "
                f"{last_time}"
            )

            # -----------------------------------------------------
            # LOAD
            # -----------------------------------------------------

            candles = load_candles_from_database()

            # -----------------------------------------------------
            # CHECK OLD SIGNALS
            # -----------------------------------------------------

            check_pending_signals(
                candles
            )

            # -----------------------------------------------------
            # WAIT FOR DATA
            # -----------------------------------------------------

            if len(candles) < MIN_CANDLES_FOR_ANALYSIS:

                remaining = (
                    MIN_CANDLES_FOR_ANALYSIS
                    - len(candles)
                )

                logger.info(
                    f"⏳ Накопление данных..."
                )

                logger.info(
                    f"Ещё нужно минимум "
                    f"{remaining} свечей"
                )

                time.sleep(
                    UPDATE_INTERVAL
                )

                continue

            # -----------------------------------------------------
            # ANALYZE
            # -----------------------------------------------------

            result = analyze_pattern(
                candles
            )

            print_current_analysis(
                result,
                candles
            )

            # -----------------------------------------------------
            # NEW SIGNAL
            # -----------------------------------------------------

            if (
                result
                and result.get("prediction")
            ):

                if not has_active_signal():

                    latest_candle = candles[-1]

                    saved_signal = save_signal(
                        result,
                        latest_candle
                    )

                    if saved_signal:

                        print_signal(
                            result,
                            latest_candle
                        )

            # -----------------------------------------------------
            # STATS
            # -----------------------------------------------------

            if cycle % 12 == 0:

                print_statistics()

            # -----------------------------------------------------
            # WAIT
            # -----------------------------------------------------

            time.sleep(
                UPDATE_INTERVAL
            )

        except KeyboardInterrupt:

            logger.info(
                "🛑 Остановка по Ctrl+C"
            )

            break

        except Exception:

            logger.exception(
                "❌ Ошибка главного цикла"
            )

            time.sleep(5)


# =====================================================================
# START
# =====================================================================

if __name__ == "__main__":

    try:

        main()

    except KeyboardInterrupt:

        print()
        print(
            "🛑 Бот остановлен."
        )

    except Exception:

        print()
        print("❌ КРИТИЧЕСКАЯ ОШИБКА:")

        traceback.print_exc()

        sys.exit(1)