# -*- coding: utf-8 -*-
import logging
import re
import random
import asyncio
from datetime import datetime
from collections import defaultdict
from telegram import Update
from telegram.ext import (
    Application,
    MessageHandler,
    filters,
    ContextTypes,
    CommandHandler
)

# === НАСТРОЙКИ ===
TOKEN = "1163348874:AAFgZEXveILvD4MbhQ8jiLTwIxs4puYhmq0"
INPUT_CHANNEL_ID = -1003469691743
OUTPUT_CHANNEL_ID = -1003842401391
ADMIN_ID = 683219603

MAX_GAME_NUMBER = 1440

FUNNY_PHRASES = [
    "🎰 ВА-БАНК! ОБНАРУЖЕН СУПЕР ПАТТЕРН! 🎰",
    "🚀 РАКЕТА ЗАПУЩЕНА! ЛЕТИМ ЗА ПОБЕДОЙ! 🚀",
    "💎 АЛМАЗНЫЙ СИГНАЛ ПРИЛЕТЕЛ! 💎",
    "🎯 СНАЙПЕР В ЦЕЛИ! ТОЧНЫЙ РАСЧЕТ! 🎯",
    "🔥 ГОРИМ ЖЕЛАНИЕМ ПОБЕДИТЬ! 🔥"
]

WIN_PHRASES = [
    "🎉 УРА! СТРАТЕГИЯ СРАБОТАЛА! 🎉",
    "💰 КАЗИНО В ШОКЕ! МЫ ВЫИГРАЛИ! 💰",
    "🥇 ЗОЛОТАЯ ПОБЕДА! ТОЧНО В ЦЕЛЬ! 🥇",
    "🏅 ОЛИМПИЙСКАЯ ТОЧНОСТЬ! ПОБЕДА! 🏅",
    "🎯 БИНГО! ПОПАДАНИЕ В ЯБЛОЧКО! 🎯"
]

LOSS_PHRASES = [
    "😔 УВЫ, НЕ СЕГОДНЯ...",
    "🌧️ НЕБО ПЛАЧЕТ, И МЫ ТОЖЕ...",
    "🍀 НЕ ПОВЕЗЛО В ЭТОТ РАЗ...",
    "🎭 ДРАМА... НО МЫ НЕ СДАЕМСЯ!",
    "🤡 ЦИРК ВЕРНУЛСЯ... ШУТКА НЕ УДАЛАСЬ"
]

SUITS = ["♥️", "♠️", "♣️", "♦️"]

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

pending_games = {}
prediction_messages = {}

# Статистика замен
card_stats = defaultdict(lambda: defaultdict(int))

class UniversalGameParser:
    @staticmethod
    def extract_game_data(text: str):
        logger.info(f"🔍 Парсим: {text[:150]}...")

        match = re.search(r'#N(\d+)', text)
        if not match:
            return None

        game_num = int(match.group(1))
        has_r_tag = '#R' in text
        has_x_tag = '#X' in text or '#X🟡' in text
        has_check = '✅' in text
        has_t = re.search(r'#T\d+', text) is not None

        is_completed = has_r_tag or has_x_tag or has_check or has_t

        left_part = UniversalGameParser._extract_left_part(text)

        left_result, cards_text, left_suits = UniversalGameParser._parse_all_cards(left_part)

        if left_result is None:
            left_result, cards_text, left_suits = UniversalGameParser._parse_whole_text(text)
        if left_result is not None and left_suits:
            card_value_match = re.search(r'(\d+)$', str(left_result))
            card_value = card_value_match.group(1) if card_value_match else None

            # Собираем все карты, а не только первые 2 и 3-ю
            all_cards = left_suits  # Все найденные карты
            initial_cards = []
            drawn_cards = []

            if len(left_suits) >= 2:
                initial_cards = left_suits[:2]  # Первые 2 — стартовые
                if len(left_suits) > 2:
                    drawn_cards = left_suits[2:]  # Остальные — добранные

            logger.info(f"✅ Игра #{game_num} ЗАВЕРШЕНА, всего карт: {len(all_cards)}, стартовые: {initial_cards}, добранные: {drawn_cards}")

            game_data = {
                'game_num': game_num,
                'has_r_tag': has_r_tag,
                'has_x_tag': has_x_tag,
                'has_check': has_check,
                'has_t': has_t,
                'is_deal': has_r_tag,
                'left_result': left_result,
                'left_cards_count': len(left_suits),
                'left_suits': left_suits,
                'initial_cards': initial_cards,
                'drawn_cards': drawn_cards,  # Список добранных карт
                'all_cards': all_cards,  # Все карты
                'total_cards_count': len(all_cards),  # Общее количество карт
                'original_text': text,
                'is_completed': True,
                'card_value': card_value
            }

            return game_data

        return None

    @staticmethod
    def _extract_left_part(self, text: str) -> str:
        separators = [
            ' 🔰 ', '🔰',
            ' - ', ' – ', ' — ',
            ' 👉👈 ', ' 👈👉 ', '👉👈', '👈👉',
            ' | ', ' |', '| ',
            ' : ', ' :', ': ',
            ';', ' ;', '; '
        ]

        for sep in separators:
            if sep in text:
                parts = text.split(sep, 1)
                if len(parts) > 1:
                    return parts[0].strip()

        return text.strip()

    @staticmethod
    def _parse_all_cards(self, left_text: str):
        left_result = None
        cards_text = ""
        suits = []

        bracket_pattern = r'(\d+)\(([^)]+)\)'
        bracket_match = re.search(bracket_pattern, left_text)

        if bracket_match:
            left_result = int(bracket_match.group(1))
            cards_text = bracket_match.group(2)
            suits = self._extract_all_suits(cards_text)
        else:
            num_match = re.search(r'\b(\d+)\b', left_text)
            if num_match:
                left_result = int(num_match.group(1))
                after_num = left_text[num_match.end():]
                suits = self._extract_all_suits(after_num)

        return left_result, cards_text, suits

    @staticmethod
    def _parse_whole_text(self, text: str):
        left_result = None
        cards_text = ""
        suits = []

        clean_text = text.replace('🔰', ' ').replace('✅', ' ').replace('🟡', ' ')

        num_match = re.search(r'\b(\d+)\b', clean_text)
        if num_match:
            left_result = int(num_match.group(1))

            card_search = re.search(r'\(([^)]+)\)', text)
            if card_search:
                cards_text = card_search.group(1)
                suits = self._extract_all_suits(cards_text)
            else:
                suits = self._extract_all_suits(text)

        return left_result, cards_text, suits

        @staticmethod
    def _extract_all_suits(self, text: str):
        suits = []

        suit_patterns = {
            '♥️': r'[♥❤♡\u2665]',
            '♠️': r'[♠♤\u2660]',
            '♣️': r'[♣♧\u2663]',
            '♦️': r'[♦♢\u2666]'
        }

        for suit_emoji, pattern in suit_patterns.items():
            matches = re.findall(pattern, text, re.IGNORECASE)
            for _ in matches:
                suits.append(suit_emoji)

        logger.debug(f"🔎 Найдено мастей в тексте: {suits}")
        return suits

def get_next_game_number(current_game, increment=1):
    next_game = current_game + increment
    while next_game > MAX_GAME_NUMBER:
        next_game -= MAX_GAME_NUMBER
    while next_game < 1:
        next_game += MAX_GAME_NUMBER
    return next_game

def get_funny_phrase():
    return random.choice(FUNNY_PHRASES)

def get_win_phrase():
    return random.choice(WIN_PHRASES)

def get_loss_phrase():
    return random.choice(LOSS_PHRASES)

def compare_suits(predicted_suit, found_suit):
    suit_map = {
        '♥️': '♥', '♥': '♥', '❤': '♥', '♡': '♥',
        '♠️': '♠', '♠': '♠', '♤': '♠',
        '♣️': '♣', '♣': '♣', '♧': '♣',
        '♦️': '♦', '♦': '♦', '♢': '♦'
    }

    predicted = suit_map.get(predicted_suit, predicted_suit)
    found = suit_map.get(found_suit, found_suit)

    predicted = predicted.replace('\ufe0f', '').replace('️', '').strip()
    found = found.replace('\ufe0f', '').replace('️', '').strip()

    return predicted == found

class SuitAnalyzer:
    def __init__(self):
        self.suit_history = []
        self.frequency = defaultdict(int)

    def add_suit(self, suit):
        if suit:
            if '♥' in suit or '❤' in suit or '♡' in suit:
                normalized = '♥️'
            elif '♠' in suit or '♤' in suit:
                normalized = '♠️'
            elif '♣' in suit or '♧' in suit:
                normalized = '♣️'
            elif '♦' in suit or '♢' in suit:
                normalized = '♦️'
            else:
                return

            self.suit_history.append(normalized)
            self.frequency[normalized] += 1

            if len(self.suit_history) > 20:
                removed_suit = self.suit_history.pop(0)
                self.frequency[removed_suit] -= 1
                if self.frequency[removed_suit] == 0:
                    del self.frequency[removed_suit]

    def predict_next_suit(self):
        if not self.suit_history:
            suit = random.choice(SUITS)
            confidence = 0.5
        else:
            total = sum(self.frequency.values())
            weights = [self.frequency[s] / total if total > 0 else 0.25 for s in SUITS]
            suit = random.choices(SUITS, weights=weights, k=1)[0]
            confidence = 0.6

        logger.info(f"🤖 AI выбрал: {suit} ({confidence*100:.1f}%)")
        return suit, confidence

class Storage:
    def __init__(self):
        self.analyzer = SuitAnalyzer()
        self.game_history = {}
        self.strategy2_predictions = {}
        self.strategy2_counter = 0
        self.strategy2_stats = {'total': 0, 'wins': 0, 'losses': 0}
        self.active_games = {}  # отслеживание активных игр с добором карт

    def add_to_history(self, game_data):
        game_num = game_data['game_num']
        self.game_history[game_num] = game_data

        # Добавляем все карты в анализатор
        if game_data['all_cards']:
            for suit in game_data['all_cards']:
                self.analyzer.add_suit(suit)

        if len(self.game_history) > 100:
            oldest_key = min(self.game_history.keys())
            del self.game_history[oldest_key]

        # Обновляем активные игры
        if game_num in self.active_games:
            # Дополняем историю карт
            existing = self.active_games[game_num]
            existing['drawn_cards'].extend(game_data['drawn_cards'])
            existing['all_cards'] = existing['initial_cards'] + existing['drawn_cards']
        else:
            # Новая игра
            self.active_games[game_num] = {
                'initial_cards': game_data['initial_cards'],
                'drawn_cards': game_data['drawn_cards'],
                'all_cards': game_data['all_cards'],
                'status': 'active',
                'created_at': datetime.now()
            }

    def is_game_already_in_predictions(self, game_num):
        for pred in self.strategy2_predictions.values():
            if pred['status'] == 'pending' and game_num in pred['check_games']:
                return True
        return False

    def was_game_in_finished_predictions(self, game_num):
        for pred in self.strategy2_predictions.values():
            if pred['status'] in ['win', 'loss'] and game_num in pred['check_games']:
                return True
        return False

    def check_deal_before_game(self, game_num):
        prev_game_num = get_next_game_number(game_num, -1)
        if prev_game_num in self.game_history:
            prev_game = self.game_history[prev_game_num]
            if prev_game.get('has_r_tag', False):
                return True
        return False

    def predict_suit_for_card(self, card_value):
        if card_value not in card_stats or not card_stats[card_value]:
            return random.choice(SUITS), 0.5

        total = sum(card_stats[card_value].values())
        if total == 0:
            return random.choice(SUITS), 0.5

        best_suit = max(card_stats[card_value].items(), key=lambda x: x[1])
        probability = best_suit[1] / total

        return best_suit[0], probability


    def create_strategy2_prediction(self, game_num, card_value=None):
        if card_value:
            predicted_suit, confidence = self.predict_suit_for_card(card_value)
        else:
            predicted_suit, confidence = self.analyzer.predict_next_suit()

        target_game = get_next_game_number(game_num, 10)

        if self.is_game_already_in_predictions(target_game):
            return None

        if self.was_game_in_finished_predictions(target_game):
            return None

        if self.check_deal_before_game(target_game):
            return None

        check_games = [
            target_game,
            get_next_game_number(target_game, 1),
            get_next_game_number(target_game, 2)
        ]

                prediction_id = f"str2_{self.strategy2_counter}"
        self.strategy2_counter += 1

        prediction = {
            'id': prediction_id,
            'predicted_suit': predicted_suit,
            'confidence': confidence,
            'target_game': target_game,
            'check_games': check_games,
            'status': 'pending',
            'created_at': datetime.now(),
            'wins_in_check': 0,
            'losses_in_check': 0
        }

        self.strategy2_predictions[prediction_id] = prediction
        logger.info(f"🤖 Создан прогноз #{prediction_id}: {predicted_suit} для игры #{target_game}")
        return prediction_id

    def check_all_predictions(self):
        """Проверяет все активные прогнозы на завершение"""
        completed_predictions = []

        for pred_id, prediction in list(self.strategy2_predictions.items()):
            if prediction['status'] != 'pending':
                continue

            all_found = True
            suit_found = False
            found_cards = []
            check_suit = prediction['predicted_suit']

            for check_game_num in prediction['check_games']:
                if check_game_num in self.active_games:
                    all_game_cards = self.active_games[check_game_num]['all_cards']
                    logger.info(f"🃏 Все карты игры #{check_game_num}: {all_game_cards}")

                    for idx, found_suit in enumerate(all_game_cards):
                card_num = idx + 1
                if compare_suits(check_suit, found_suit):
                    suit_found = True
            found_cards.append(card_num)
            logger.info(f"✅✅✅ НАШЛИ В КАРТЕ #{card_num}!")

            if not suit_found:
                all_found = False

            if all_found:
                prediction['status'] = 'win'
                prediction['wins_in_check'] += 1
                self.strategy2_stats['wins'] += 1
                logger.info(f"🏆 ПРОГНОЗ #{pred_id} ВЫИГРАЛ!")
                completed_predictions.append(pred_id)
            else:
                prediction['status'] = 'loss'
                prediction['losses_in_check'] += 1
                self.strategy2_stats['losses'] += 1
                logger.info(f"💥 ПРОГНОЗ #{pred_id} ПРОИГРАЛ")
                completed_predictions.append(pred_id)

        return completed_predictions

    def cleanup_finished_games(self):
        """Очищает завершённые игры из активных"""
        current_time = datetime.now()
        to_remove = []

        for game_num, game_data in self.active_games.items():
            # Убираем игры старше 1 часа
            if 'created_at' in game_data:
                if (current_time - game_data['created_at']).total_seconds() > 3600:
                    to_remove.append(game_num)

        for game_num in to_remove:
            if game_num in self.active_games:
                del self.active_games[game_num]
                logger.info(f"🗑️ Игра #{game_num} удалена из активных (устарела)")

# === ОБРАБОТЧИКИ СООБЩЕНИЙ ===
async def handle_new_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.channel_post and update.channel_post.chat.id == INPUT_CHANNEL_ID:
        text = update.channel_post.text or update.channel_post.caption or ""
        if not text:
            return

        game_data = UniversalGameParser.extract_game_data(text)
        if game_data:
            storage.add_to_history(game_data)
            # Запускаем проверку прогнозов
            completed = storage.check_all_predictions()
            for pred_id in completed:
                await send_prediction_result(update, context, pred_id)

async def send_prediction_result(update: Update, context: ContextTypes.DEFAULT_TYPE, pred_id: str):
    prediction = storage.strategy2_predictions.get(pred_id)
    if not prediction:
        return

    # ОЧИСТКА АКТИВНЫХ ИГР
    if prediction['target_game'] in storage.active_games:
        del storage.active_games[prediction['target_game']]
        logger.info(f"🗑️ Игра #{prediction['target_game']} удалена из активных после завершения прогноза")

    result_text = ""
    if prediction['status'] == 'win':
        result_text = f"{get_win_phrase()}\n\n🎯 Прогноз #{pred_id}\n🃏 Масть: {prediction['predicted_suit']}\n🏆 ВЫИГРЫШ!\n📊 Статистика: {storage.strategy2_stats['wins']} побед, {storage.strategy2_stats['losses']} поражений"
    else:
        result_text = f"{get_loss_phrase()}\n\n🎯 Прогноз #{pred_id}\n🃏 Масть: {prediction['predicted_suit']}\n💥 ПРОИГРЫШ\n📊 Статистика: {storage.strategy2_stats['wins']} побед, {storage.strategy2_stats['losses']} поражений"

    await context.bot.send_message(
        chat_id=OUTPUT_CHANNEL_ID,
        text=result_text
    )

# ИНИЦИАЛИЗАЦИЯ ХРАНИЛИЩА
storage = Storage()

# === ЗАПУСК БОТА ===
def main():
    application = Application.builder().token(TOKEN).build()
    application.add_handler(MessageHandler(filters.Chat(INPUT_CHANNEL_ID), handle_new_game))
    application.run_polling()

if __name__ == '__main__':
    main()
