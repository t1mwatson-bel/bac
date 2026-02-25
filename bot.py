# -*- coding: utf-8 -*-
import logging
import re
import os
import sys
import fcntl
import urllib.request
import urllib.error
import json
from datetime import datetime, time, timedelta
from collections import defaultdict, deque
from telegram import Update
from telegram.ext import (
    Application,
    MessageHandler,
    filters,
    ContextTypes
)
from telegram.error import Conflict
import random
import pytz
import numpy as np
from sklearn.ensemble import (
    RandomForestClassifier,
    GradientBoostingClassifier
)
import joblib

# ======== НАСТРОЙКА ЛОГИРОВАНИЯ ========
class JsonFormatter(logging.Formatter):
    def format(self, record):
        log_record = {
            "message": record.getMessage(),
            "level": record.levelname.lower(),
            "timestamp": self.formatTime(record, "%Y-%m-%d %H:%M:%S"),
            "name": record.name
        }
        if record.exc_info:
            log_record["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_record, ensure_ascii=False)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(JsonFormatter())
logger.addHandler(handler)
logging.getLogger().handlers.clear()

# ======== НАСТРОЙКИ ========
TOKEN = "1163348874:AAFgZEXveILvD4MbhQ8jiLTwIxs4puYhmq0"
INPUT_CHANNEL_ID = -1003469691743
OUTPUT_CHANNEL_ID = -1003842401391

LOCK_FILE = f'/tmp/intel_bot_{TOKEN[-10:]}.lock'

# ======== ИНТЕЛЛЕКТУАЛЬНЫЙ ML ПРЕДИКТОР ========
class IntelMLPredictor:
    def __init__(self, history_size=1000):
        self.history = deque(maxlen=history_size)
        self.history_2cards = deque(maxlen=history_size)
        self.history_player3 = deque(maxlen=history_size)
        self.history_banker3 = deque(maxlen=history_size)
        
        # МОДЕЛИ ДЛЯ МАСТЕЙ
        self.models = {
            '2cards': self._create_ensemble(),
            'player3': self._create_ensemble(),
            'banker3': self._create_ensemble()
        }
        
        # ИНТЕЛЛЕКТУАЛЬНЫЕ НАСТРОЙКИ
        self.confidence_threshold = 0.15
        self.dynamic_threshold = False
        self.min_games_for_training = 20
        
        # ТАЙМЕР между прогнозами (2-7 минут)
        self.last_prediction_time = None
        self.min_time_between = 120  # 2 минуты
        self.max_time_between = 420  # 7 минут
        
        # Статистика догонов
        self.dogon_stats = {
            'same_suit': {'attempts': 0, 'success': 0, 'failures': []},
            'changed_suit': {'attempts': 0, 'success': 0, 'failures': []},
            'skip_1': {'attempts': 0, 'success': 0},
            'skip_2': {'attempts': 0, 'success': 0},
            'skip_3': {'attempts': 0, 'success': 0},
            'skip_4': {'attempts': 0, 'success': 0}
        }
        
        # Статистика мастей
        self.suit_stats = {
            '♥️': {'total': 0, 'frequency': 0, 'dogon_success': 0, 'best_skip': 2},
            '♦️': {'total': 0, 'frequency': 0, 'dogon_success': 0, 'best_skip': 2},
            '♠️': {'total': 0, 'frequency': 0, 'dogon_success': 0, 'best_skip': 2},
            '♣️': {'total': 0, 'frequency': 0, 'dogon_success': 0, 'best_skip': 2}
        }
        
        self.predictions_stats = {
            'total': 0,
            'success': 0,
            'failures': [],
            'by_type': defaultdict(int)
        }
        
        self.active_predictions = []
        self.prediction_counter = 0
        self.recent_suits = deque(maxlen=20)
        
        self.anomalies_detected = []
        self.last_anomaly_time = None
        self.suit_streak = 0
        self.last_suit = None
        self.player_win_streak = 0
        self.banker_win_streak = 0
        self.tie_streak = 0
        
        self.load_models()
        self.load_history()
        self.load_dogon_stats()
        
    def _create_ensemble(self):
        return {
            'rf': RandomForestClassifier(n_estimators=50, max_depth=5, random_state=42),
            'gb': GradientBoostingClassifier(n_estimators=50, max_depth=3, random_state=42)
        }
    
    def _ensemble_predict(self, models_dict, X):
        predictions = []
        probabilities = []
        
        for name, model in models_dict.items():
            try:
                pred = model.predict(X)[0]
                if hasattr(model, 'predict_proba'):
                    proba = model.predict_proba(X)[0]
                    prob = max(proba)
                else:
                    prob = 0.5
                
                predictions.append(pred)
                probabilities.append(prob)
            except:
                continue
        
        if not predictions:
            return None, 0
        
        from collections import Counter
        counter = Counter(predictions)
        final_pred = counter.most_common(1)[0][0]
        confidence = np.mean(probabilities)
        
        return final_pred, confidence
    
    def _get_funny_comment(self, comment_type, **kwargs):
        jokes = {
            'high_confidence': [
                "🧠 Я прям чувствую!", 
                "🎯 Точняк будет!", 
                "👨‍🏫 Профессор сказал!"
            ],
            'low_confidence': [
                "🤔 Ща подумаю...", 
                "📊 Анализирую...", 
                "🔄 Мозги кипят!"
            ],
            'suit': {
                '♥️': ["❤️ Сердечко екнуло!", "💕 Любовь зла!"],
                '♦️': ["💎 Бриллиант!", "💰 К бабкам!"],
                '♠️': ["⚫ Пика!", "🌙 Ночная масть!"],
                '♣️': ["🍀 Клевер!", "🌿 К удаче!"]
            },
            'dogon_same': [
                "🔄 Продолжаем преследование!",
                "🎯 Не сдаемся!",
                "💪 Еще попытка!"
            ],
            'dogon_change': [
                "🔄 Меняем стратегию!",
                "🎯 Новая цель!",
                "🧠 Интеллект в деле!"
            ],
            'dogon_skip': [
                "⏸ Пауза на анализ...",
                "🧘 Наблюдаем...",
                "📈 Изучаем ситуацию..."
            ],
            'win': [
                "🏆 ГЕНИАЛЬНО!", 
                "⭐ В яблочко!", 
                "🎉 Я красавчик!"
            ],
            'loss': [
                "😢 Просчет...", 
                "📉 Анализирую ошибку...", 
                "🧠 Умный учится на ошибках!"
            ]
        }
        
        if comment_type == 'confidence':
            confidence = kwargs.get('confidence', 0.5)
            if confidence >= 0.6:
                return random.choice(jokes['high_confidence'])
            else:
                return random.choice(jokes['low_confidence'])
        elif comment_type == 'suit':
            suit = kwargs.get('suit', '♥️')
            return random.choice(jokes['suit'].get(suit, jokes['suit']['♥️']))
        elif comment_type == 'win':
            return random.choice(jokes['win'])
        elif comment_type == 'loss':
            return random.choice(jokes['loss'])
        return ""
    
    def _get_suit_frequency(self, suit, games=50):
        recent = list(self.history)[-games:]
        if not recent:
            return 0.25
        
        count = 0
        for game in recent:
            if suit in game.get('player_suits', []):
                count += 1
        
        frequency = count / len(recent)
        self.suit_stats[suit]['frequency'] = frequency
        return frequency
    
    def _analyze_dogon_strategy(self, suit):
        stats = self.dogon_stats
        suit_data = self.suit_stats[suit]
        
        same_suit_rate = 0
        if stats['same_suit']['attempts'] > 0:
            same_suit_rate = stats['same_suit']['success'] / stats['same_suit']['attempts']
        
        changed_suit_rate = 0
        if stats['changed_suit']['attempts'] > 0:
            changed_suit_rate = stats['changed_suit']['success'] / stats['changed_suit']['attempts']
        
        skip_rates = {}
        for skip in [1,2,3,4]:
            key = f'skip_{skip}'
            if stats[key]['attempts'] > 0:
                skip_rates[skip] = stats[key]['success'] / stats[key]['attempts']
        
        best_skip = 2
        if skip_rates:
            best_skip = max(skip_rates, key=skip_rates.get)
        
        suit_data['best_skip'] = best_skip
        
        return {
            'same_suit_rate': same_suit_rate,
            'changed_suit_rate': changed_suit_rate,
            'best_skip': best_skip
        }
    
    def _should_change_suit(self, original_suit, attempt):
        frequency = self._get_suit_frequency(original_suit)
        strategy = self._analyze_dogon_strategy(original_suit)
        
        if frequency < 0.15:
            return True, "🎯 Редкая масть, меняем стратегию"
        
        if strategy['same_suit_rate'] < 0.3 and strategy['same_suit_rate'] > 0:
            return True, "🔄 Та же масть не эффективна"
        
        if strategy['changed_suit_rate'] > strategy['same_suit_rate'] + 0.2:
            return True, "📊 Смена масти эффективнее"
        
        return False, "💪 Продолжаем преследование"
    
    def _calculate_skip_games(self, suit, attempt):
        frequency = self._get_suit_frequency(suit)
        strategy = self._analyze_dogon_strategy(suit)
        
        base_skip = strategy['best_skip']
        
        if attempt == 1:
            if frequency > 0.3:
                return max(1, base_skip - 1)
            else:
                return base_skip
        elif attempt == 2:
            if frequency < 0.2:
                return base_skip + 2
            else:
                return base_skip + 1
        
        return base_skip + 2
    
    def _predict_best_suit(self, context):
        scores = {'♥️': 0, '♦️': 0, '♠️': 0, '♣️': 0}
        
        total_games = len(self.history)
        if total_games > 0:
            for suit in scores:
                count = 0
                for game in self.history:
                    if suit in game.get('player_suits', []):
                        count += 1
                scores[suit] += (count / total_games) * 0.4
        
        recent = list(self.history)[-10:]
        if recent:
            for suit in scores:
                count = 0
                for game in recent:
                    if suit in game.get('player_suits', []):
                        count += 1
                scores[suit] += (count / len(recent)) * 0.3
        
        if context and context.get('player_draws'):
            for suit in scores:
                scores[suit] += 0.1
        
        for suit in scores:
            if self.suit_stats[suit]['dogon_success'] > 0:
                scores[suit] += self.suit_stats[suit]['dogon_success'] * 0.2
        
        return max(scores, key=scores.get)
    
    def _find_alternative_suit(self, exclude_suit=None):
        """Находит альтернативную масть"""
        scores = {'♥️': 0, '♦️': 0, '♠️': 0, '♣️': 0}
        
        recent = list(self.history)[-10:]
        if recent:
            for suit in scores:
                if suit == exclude_suit:
                    continue
                count = 0
                for game in recent:
                    if suit in game.get('player_suits', []):
                        count += 1
                scores[suit] = count
        
        if max(scores.values()) > 0:
            return max(scores, key=scores.get)
        return '♠️'
    
    def _get_intelligent_dogon_plan(self, original_pred, attempt, context):
        original_suit_num = original_pred['value']
        suit_map_rev = {0: '♥️', 1: '♦️', 2: '♠️', 3: '♣️'}
        original_suit = suit_map_rev.get(original_suit_num, '♥️')
        
        should_change, change_reason = self._should_change_suit(original_suit, attempt)
        skip_games = self._calculate_skip_games(original_suit, attempt)
        
        if should_change:
            new_suit = self._predict_best_suit(context)
            
            if self._check_suit_duplicate(new_suit, last_games=2):
                new_suit = self._find_alternative_suit(exclude_suit=original_suit)
            
            new_suit_num = {'♥️':0, '♦️':1, '♠️':2, '♣️':3}[new_suit]
            
            return {
                'action': 'change',
                'new_value': new_suit_num,
                'skip': skip_games,
                'reason': f"{change_reason} → выбираю {new_suit}",
                'comment': self._get_funny_comment('dogon_change')
            }
        elif skip_games > 3:
            return {
                'action': 'skip',
                'skip': skip_games,
                'reason': f"⏳ Оптимальный пропуск {skip_games} игр",
                'comment': self._get_funny_comment('dogon_skip')
            }
        else:
            return {
                'action': 'same',
                'new_value': original_suit_num,
                'skip': skip_games,
                'reason': f"🔄 Догон {attempt + 1}, пропуск {skip_games} игр",
                'comment': self._get_funny_comment('dogon_same')
            }
    
    def save_history(self):
        try:
            with open('ml_history.json', 'w', encoding='utf-8') as f:
                history_list = []
                for game in self.history:
                    game_copy = game.copy()
                    if 'timestamp' in game_copy and game_copy['timestamp']:
                        game_copy['timestamp'] = game_copy['timestamp'].isoformat()
                    history_list.append(game_copy)
                json.dump(history_list, f, ensure_ascii=False, indent=2)
            logger.info(f"ML: история сохранена ({len(self.history)} игр)")
        except Exception as e:
            logger.error(f"ML: ошибка сохранения истории: {e}")
    
    def load_history(self):
        try:
            if os.path.exists('ml_history.json'):
                with open('ml_history.json', 'r', encoding='utf-8') as f:
                    history_list = json.load(f)
                    for game in history_list:
                        if 'timestamp' in game and game['timestamp']:
                            try:
                                game['timestamp'] = datetime.fromisoformat(game['timestamp'])
                            except:
                                game['timestamp'] = datetime.now()
                    self.history = deque(history_list, maxlen=1000)
                    
                    for game in self.history:
                        self._classify_game_by_type(game)
                        if 'player_suits' in game:
                            for suit in game['player_suits']:
                                self.recent_suits.append(suit)
                                
                logger.info(f"ML: загружено {len(self.history)} игр из файла")
        except Exception as e:
            logger.error(f"ML: ошибка загрузки истории: {e}")
    
    def load_dogon_stats(self):
        try:
            if os.path.exists('dogon_stats.json'):
                with open('dogon_stats.json', 'r', encoding='utf-8') as f:
                    self.dogon_stats.update(json.load(f))
                logger.info(f"ML: загружена статистика догонов")
        except Exception as e:
            logger.error(f"ML: ошибка загрузки статистики догонов: {e}")
    
    def save_dogon_stats(self):
        try:
            with open('dogon_stats.json', 'w', encoding='utf-8') as f:
                json.dump(self.dogon_stats, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"ML: ошибка сохранения статистики догонов: {e}")
    
    def _classify_game_by_type(self, game_data):
        player_count = game_data.get('player_cards_count', 0)
        banker_count = game_data.get('banker_cards_count', 0)
        player_draws = game_data.get('player_draws', False)
        banker_draws = game_data.get('banker_draws', False)
        
        if player_draws or player_count == 3:
            self.history_player3.append(game_data)
            return 'player3'
        elif banker_draws or banker_count == 3:
            self.history_banker3.append(game_data)
            return 'banker3'
        else:
            self.history_2cards.append(game_data)
            return '2cards'
    
    def add_game(self, game_data):
        if not game_data:
            return []
        
        ml_data = self.prepare_ml_data(game_data)
        self.history.append(ml_data)
        game_type = self._classify_game_by_type(ml_data)
        
        if 'player_suits' in ml_data:
            for suit in ml_data['player_suits']:
                self.recent_suits.append(suit)
        
        anomalies = self._check_anomalies(ml_data)
        
        logger.info(f"ML: добавлена игра #{game_data['game_num']} (тип: {game_type}). Всего игр: {len(self.history)}")
        self.save_history()
        
        return anomalies
        
    def prepare_ml_data(self, game_data):
        features = {
            'game_num': game_data['game_num'],
            'player_score': game_data.get('player_score', 0),
            'banker_score': game_data.get('banker_score', 0),
            'player_cards_count': len(game_data.get('player_cards', [])),
            'banker_cards_count': len(game_data.get('banker_cards', [])),
            'winner': game_data.get('winner'),
            'total_sum': game_data.get('total_sum', 0),
            'timestamp': game_data.get('timestamp'),
            'has_r': game_data.get('has_r_tag', False),
            'has_x': game_data.get('has_x_tag', False),
            'player_draws': game_data.get('player_draws', False),
            'banker_draws': game_data.get('banker_draws', False),
        }
        
        player_suits = [c['suit'] for c in game_data.get('player_cards', [])]
        features['player_suits'] = player_suits
        
        if features['timestamp']:
            features['hour'] = features['timestamp'].hour
            features['minute'] = features['timestamp'].minute
            features['weekday'] = features['timestamp'].weekday()
        else:
            features['hour'] = 0
            features['minute'] = 0
            features['weekday'] = 0
        
        return features
    
    def extract_features_for_training(self, index):
        if index >= len(self.history) - 1:
            return None, None, None
        
        current = list(self.history)[index]
        next_game = list(self.history)[index + 1]
        
        game_type = '2cards'
        if next_game.get('player_cards_count', 0) == 3:
            game_type = 'player3'
        elif next_game.get('banker_cards_count', 0) == 3:
            game_type = 'banker3'
        
        features = []
        
        features.append(current['player_score'])
        features.append(current['banker_score'])
        features.append(current['player_score'] - current['banker_score'])
        
        features.append(current['player_cards_count'])
        features.append(current['banker_cards_count'])
        
        winner = current.get('winner', 'unknown')
        features.append(1 if winner == 'player' else 0)
        features.append(1 if winner == 'banker' else 0)
        features.append(1 if winner == 'tie' else 0)
        
        suit_map = {'♥️': 0, '♦️': 1, '♠️': 2, '♣️': 3}
        if current.get('player_suits'):
            features.append(suit_map.get(current['player_suits'][-1], -1))
        else:
            features.append(-1)
        
        features.append(1 if current.get('player_draws', False) else 0)
        features.append(1 if current.get('banker_draws', False) else 0)
        
        features.append(current.get('hour', 0))
        features.append(current.get('minute', 0))
        features.append(current.get('weekday', 0))
        
        for offset in range(1, 4):
            if index - offset >= 0:
                past = list(self.history)[index - offset]
                features.append(1 if past.get('winner') == 'player' else 0)
                features.append(1 if past.get('winner') == 'banker' else 0)
                features.append(1 if past.get('winner') == 'tie' else 0)
            else:
                features.append(0)
                features.append(0)
                features.append(0)
        
        targets = {
            'suit': suit_map.get(next_game['player_suits'][0] if next_game.get('player_suits') else None, -1)
        }
        
        return features, targets, game_type
    
    def train_models(self):
        if len(self.history) < self.min_games_for_training:
            logger.info(f"ML: обучение начнется после {self.min_games_for_training} игр (сейчас {len(self.history)})")
            return False
        
        data_by_type = {
            '2cards': {'X': [], 'y_suit': []},
            'player3': {'X': [], 'y_suit': []},
            'banker3': {'X': [], 'y_suit': []}
        }
        
        for i in range(len(self.history) - 1):
            features, targets, game_type = self.extract_features_for_training(i)
            if features and targets:
                data_by_type[game_type]['X'].append(features)
                if targets['suit'] != -1:
                    data_by_type[game_type]['y_suit'].append(targets['suit'])
        
        for game_type in ['2cards', 'player3', 'banker3']:
            X = np.array(data_by_type[game_type]['X'])
            
            if len(X) < 5:
                continue
            
            if len(data_by_type[game_type]['y_suit']) >= 5:
                y_suit = np.array(data_by_type[game_type]['y_suit'])
                X_suit = X[:len(y_suit)]
                
                for name, model in self.models[game_type].items():
                    try:
                        model.fit(X_suit, y_suit)
                        logger.info(f"ML: обучена модель {game_type}/{name}")
                    except Exception as e:
                        logger.error(f"Ошибка обучения {game_type}/{name}: {e}")
        
        self.save_models()
        return True
    
    def _check_suit_duplicate(self, suit, last_games=3):
        recent_games = list(self.history)[-last_games:]
        for game in recent_games:
            player_suits = game.get('player_suits', [])
            if suit in player_suits:
                return True
        return False
    
    def _check_duplicate_prediction(self, target_game):
        for pred in self.active_predictions:
            if pred['status'] == 'pending' and pred['target_game'] == target_game:
                return True
        return False
    
    def _get_max_predicted_game(self):
        """Возвращает максимальный номер игры среди активных прогнозов"""
        max_game = 0
        for pred in self.active_predictions:
            if pred['status'] == 'pending' and pred['target_game'] > max_game:
                max_game = pred['target_game']
        return max_game
    
    def _check_stuck_predictions(self):
        """Проверяет не зависли ли прогнозы"""
        current_time = datetime.now(pytz.timezone('Europe/Moscow'))
        stuck_count = 0
        
        for pred in self.active_predictions:
            if pred['status'] != 'pending':
                continue
            
            # Если прогноз висит больше 30 минут - считаем его зависшим
            if 'timestamp' in pred:
                delta = (current_time - pred['timestamp']).seconds
                if delta > 1800:  # 30 минут
                    logger.warning(f"⚠️ Зависший прогноз #{pred['id']} на игру #{pred['target_game']} (висит {delta}с)")
                    pred['status'] = 'stuck'
                    stuck_count += 1
        
        if stuck_count > 0:
            logger.info(f"🧹 Очищено {stuck_count} зависших прогнозов")
        
        return stuck_count
    
    def predict_next_game(self):
        logger.info("📢 ВХОД В predict_next_game")
        logger.info(f"len(history)={len(self.history)}")
        
        if len(self.history) < 5:
            logger.info("❌ Мало истории (<5)")
            return None, None
        
        last_game = list(self.history)[-1]
        current_game_num = last_game['game_num']
        
        # Находим максимальный номер среди активных прогнозов
        max_predicted = self._get_max_predicted_game()
        
        # Определяем минимальный допустимый номер для нового прогноза
        min_next = max(max_predicted + 1, current_game_num + 2)
        
        # Определяем тип игры
        if last_game.get('player_draws'):
            game_type = 'player3'
        elif last_game.get('banker_draws'):
            game_type = 'banker3'
        else:
            game_type = '2cards'
        
        features = []
        
        features.append(last_game['player_score'])
        features.append(last_game['banker_score'])
        features.append(last_game['player_score'] - last_game['banker_score'])
        
        features.append(last_game['player_cards_count'])
        features.append(last_game['banker_cards_count'])
        
        winner = last_game.get('winner', 'unknown')
        features.append(1 if winner == 'player' else 0)
        features.append(1 if winner == 'banker' else 0)
        features.append(1 if winner == 'tie' else 0)
        
        suit_map = {'♥️': 0, '♦️': 1, '♠️': 2, '♣️': 3}
        if last_game.get('player_suits'):
            features.append(suit_map.get(last_game['player_suits'][-1], -1))
        else:
            features.append(-1)
        
        features.append(1 if last_game.get('player_draws', False) else 0)
        features.append(1 if last_game.get('banker_draws', False) else 0)
        
        features.append(last_game.get('hour', 0))
        features.append(last_game.get('minute', 0))
        features.append(last_game.get('weekday', 0))
        
        history_len = len(self.history)
        for offset in range(1, 4):
            if history_len - 1 - offset >= 0:
                past = list(self.history)[history_len - 1 - offset]
                features.append(1 if past.get('winner') == 'player' else 0)
                features.append(1 if past.get('winner') == 'banker' else 0)
                features.append(1 if past.get('winner') == 'tie' else 0)
            else:
                features.append(0)
                features.append(0)
                features.append(0)
        
        X = np.array(features).reshape(1, -1)
        
        if game_type in self.models:
            logger.info(f"📊 Пробуем предсказать для типа {game_type}")
            pred, confidence = self._ensemble_predict(self.models[game_type], X)
            
            if pred is not None:
                suit_map_rev = {0: '♥️', 1: '♦️', 2: '♠️', 3: '♣️'}
                predicted_suit = suit_map_rev.get(pred, '?')
                
                logger.info(f"📈 Предсказание: {predicted_suit} с уверенностью {confidence:.2f}")
                
                if confidence >= self.confidence_threshold:
                    logger.info(f"✅ Прошел порог {self.confidence_threshold}")
                    
                    # Диапазон 2-7 игр
                    base_skip = random.randint(2, 7)
                    next_game_num = max(min_next, current_game_num + base_skip)
                    
                    predictions = {
                        'suit': {
                            'value': pred,
                            'confidence': float(confidence),
                            'game_type': game_type,
                            'suit': predicted_suit
                        }
                    }
                    logger.info(f"🔥 ИТОГОВЫЙ ПРОГНОЗ на игру #{next_game_num}")
                    return predictions, next_game_num
                else:
                    logger.info(f"❌ Не прошел порог {self.confidence_threshold} (уверенность {confidence:.2f})")
            else:
                logger.info("❌ pred is None")
        else:
            logger.info(f"❌ game_type {game_type} не в моделях")
        
        logger.info("❌ Прогноз не создан")
        return None, None
    
    def _check_anomalies(self, game_data):
        anomalies = []
        
        if 'player_suits' in game_data and game_data['player_suits']:
            current_suit = game_data['player_suits'][0]
            if current_suit == self.last_suit:
                self.suit_streak += 1
            else:
                self.suit_streak = 1
                self.last_suit = current_suit
            
            if self.suit_streak == 5:
                anomalies.append(f"⚠️ 5 ИГР ПОДРЯД МАСТЬ {current_suit}!")
                self.suit_streak = 0
        
        if game_data.get('winner') == 'player':
            self.player_win_streak += 1
            self.banker_win_streak = 0
            self.tie_streak = 0
            if self.player_win_streak == 8:
                anomalies.append(f"🔥 8 ПОБЕД ИГРОКА ПОДРЯД!")
        elif game_data.get('winner') == 'banker':
            self.banker_win_streak += 1
            self.player_win_streak = 0
            self.tie_streak = 0
            if self.banker_win_streak == 8:
                anomalies.append(f"🔥 8 ПОБЕД БАНКИРА ПОДРЯД!")
        elif game_data.get('winner') == 'tie':
            self.tie_streak += 1
            self.player_win_streak = 0
            self.banker_win_streak = 0
            if self.tie_streak == 3:
                anomalies.append(f"🤝 3 НИЧЬИ ПОДРЯД!")
        
        return anomalies
    
    def register_prediction_result(self, game_num, succeeded, situation, attempt=0, strategy='same_suit', skip=2):
        self.predictions_stats['total'] += 1
        self.predictions_stats['by_type'][f"attempt_{attempt}"] += 1
        
        if succeeded:
            self.predictions_stats['success'] += 1
            
            if strategy in self.dogon_stats:
                self.dogon_stats[strategy]['attempts'] += 1
                self.dogon_stats[strategy]['success'] += 1
            
            skip_key = f'skip_{skip}'
            if skip_key in self.dogon_stats:
                self.dogon_stats[skip_key]['attempts'] += 1
                self.dogon_stats[skip_key]['success'] += 1
            
            if 'suit' in situation:
                suit = situation.get('suit')
                if suit in self.suit_stats:
                    self.suit_stats[suit]['dogon_success'] += 1
            
        else:
            self.predictions_stats['failures'].append({
                'game': game_num,
                'situation': situation,
                'attempt': attempt,
                'timestamp': datetime.now(pytz.timezone('Europe/Moscow'))
            })
            
            if strategy in self.dogon_stats:
                self.dogon_stats[strategy]['attempts'] += 1
            
            skip_key = f'skip_{skip}'
            if skip_key in self.dogon_stats:
                self.dogon_stats[skip_key]['attempts'] += 1
            
            if len(self.predictions_stats['failures']) > 200:
                self.predictions_stats['failures'].pop(0)
        
        self.save_dogon_stats()
    
    def save_models(self):
        os.makedirs('ml_models', exist_ok=True)
        
        for game_type in ['2cards', 'player3', 'banker3']:
            for name, model in self.models[game_type].items():
                if model:
                    try:
                        joblib.dump(model, f'ml_models/{game_type}_{name}.pkl')
                    except:
                        pass
        logger.info("ML: модели сохранены")
    
    def load_models(self):
        if not os.path.exists('ml_models'):
            logger.info("ML: папка с моделями не найдена")
            return
        
        for game_type in ['2cards', 'player3', 'banker3']:
            for name in ['rf', 'gb']:
                model_path = f'ml_models/{game_type}_{name}.pkl'
                if os.path.exists(model_path) and name in self.models[game_type]:
                    try:
                        self.models[game_type][name] = joblib.load(model_path)
                        logger.info(f"ML: загружена модель {game_type}/{name}")
                    except Exception as e:
                        logger.error(f"Ошибка загрузки {game_type}/{name}: {e}")
    
    async def analyze_and_predict(self, game_data, context):
        predictions = None
        next_game_num = None
        
        # ПРОВЕРКА ЗАВИСШИХ ПРОГНОЗОВ
        self._check_stuck_predictions()
        
        # ПРОВЕРКА АКТИВНЫХ ПРОГНОЗОВ - если есть активные, новый не даем
        active = [p for p in self.active_predictions if p['status'] == 'pending']
        if active:
            logger.info(f"⏳ Есть активные прогнозы ({len(active)}), новый не даем")
            # Не делаем новый прогноз, но сохраняем игру и проверяем старые
            anomalies = self.add_game(game_data)
            if anomalies:
                await self._send_anomaly_alert(anomalies, game_data, context)
            await self.check_predictions(game_data['game_num'], game_data, context)
            return
        
        # ПРОВЕРКА ТАЙМЕРА
        current_time = datetime.now(pytz.timezone('Europe/Moscow'))
        
        if self.last_prediction_time:
            time_diff = (current_time - self.last_prediction_time).seconds
            if time_diff < self.min_time_between:
                logger.info(f"⏳ С момента последнего прогноза прошло {time_diff}с, нужно минимум {self.min_time_between}с")
                # Не делаем новый прогноз, но сохраняем игру и проверяем старые
                anomalies = self.add_game(game_data)
                if anomalies:
                    await self._send_anomaly_alert(anomalies, game_data, context)
                await self.check_predictions(game_data['game_num'], game_data, context)
                return
        
        # ДЕЛАЕМ ПРОГНОЗ
        if len(self.history) >= 5:
            logger.info(f"📊 Пытаемся сделать прогноз (история: {len(self.history)} игр)")
            try:
                predictions, next_game_num = self.predict_next_game()
                if predictions:
                    logger.info(f"🔥 ИНТЕЛЛЕКТУАЛЬНЫЙ ПРОГНОЗ на игру #{next_game_num}")
                else:
                    logger.info("❌ predictions is None")
            except Exception as e:
                logger.error(f"❌ Ошибка прогноза: {e}")
        else:
            logger.info(f"📚 Мало истории для прогноза: {len(self.history)}/5")
        
        # ПОТОМ СОХРАНЯЕМ ИГРУ
        anomalies = self.add_game(game_data)
        
        # ОБУЧАЕМСЯ
        if len(self.history) >= self.min_games_for_training:
            try:
                self.train_models()
            except Exception as e:
                logger.error(f"❌ Ошибка обучения: {e}")
        else:
            logger.info(f"📚 До обучения осталось: {self.min_games_for_training - len(self.history)} игр")
        
        # АНОМАЛИИ
        if anomalies:
            await self._send_anomaly_alert(anomalies, game_data, context)
        
        # ПРОВЕРЯЕМ ПРОГНОЗЫ
        await self.check_predictions(game_data['game_num'], game_data, context)
        
        # ОТПРАВЛЯЕМ НОВЫЙ ПРОГНОЗ (если есть)
        if predictions and next_game_num:
            self.last_prediction_time = current_time
            
            moscow_tz = pytz.timezone('Europe/Moscow')
            current_time_str = datetime.now(moscow_tz).strftime('%H:%M')
            next_time = (datetime.now(moscow_tz) + timedelta(minutes=1)).strftime('%H:%M')
            
            for target_type, pred in predictions.items():
                self.prediction_counter += 1
                pred_id = self.prediction_counter
                
                suit = pred.get('suit', '♥️')
                
                # Правильная последовательность догонов
                skip1 = self._calculate_skip_games(suit, 0)
                skip2 = self._calculate_skip_games(suit, 1)
                
                dogon1 = next_game_num + skip1
                dogon2 = dogon1 + skip2
                
                confidence_joke = self._get_funny_comment('confidence', confidence=pred['confidence'])
                suit_joke = self._get_funny_comment('suit', suit=suit)
                
                message = (
                    f"🎯 *ИНТЕЛЛЕКТУАЛЬНЫЙ ПРОГНОЗ #{pred_id}*\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"📊 *ИСТОЧНИК:* #{game_data['game_num']} ({current_time_str} МСК)\n"
                    f"🎯 *ЦЕЛЬ:* #{next_game_num} ({next_time} МСК)\n"
                    f"🃏 *МАСТЬ:* {suit} (у игрока)\n"
                    f"📈 *УВЕРЕННОСТЬ:* {int(pred['confidence']*100)}%\n"
                    f"🎲 *ТИП ИГРЫ:* {pred.get('game_type', 'unknown')}\n\n"
                    f"🗣 *КОММЕНТАРИЙ:* {confidence_joke} {suit_joke}\n\n"
                    f"🔄 *ДОГОНЫ:*\n"
                    f"• 1: #{dogon1}\n"
                    f"• 2: #{dogon2}\n\n"
                    f"📊 *СТАТИСТИКА:*\n"
                    f"• Всего: {self.predictions_stats['total']}\n"
                    f"• Успешно: {self.predictions_stats['success']}\n"
                    f"• Процент: {int(self.predictions_stats['success']/max(1,self.predictions_stats['total'])*100)}%\n\n"
                    f"⏱ {current_time_str} МСК"
                )
                
                try:
                    msg = await context.bot.send_message(
                        chat_id=OUTPUT_CHANNEL_ID,
                        text=message,
                        parse_mode='Markdown'
                    )
                    
                    self.active_predictions.append({
                        'id': pred_id,
                        'value': pred['value'],
                        'confidence': pred['confidence'],
                        'target_game': next_game_num,
                        'source_game': game_data['game_num'],
                        'msg_id': msg.message_id,
                        'status': 'pending',
                        'attempt': 0,
                        'original_suit': suit,
                        'strategy': 'same_suit',
                        'skip': skip1,
                        'timestamp': current_time
                    })
                    
                except Exception as e:
                    logger.error(f"❌ Ошибка отправки: {e}")
    
    async def check_predictions(self, current_game_num, game_data, context):
        logger.info(f"🔍 ML: проверка прогнозов по игре #{current_game_num}")
        
        for pred in list(self.active_predictions):
            if pred['status'] != 'pending':
                continue
            
            if pred['target_game'] > current_game_num:
                continue
            
            succeeded = False
            actual_game = None
            
            for game_num in range(pred['target_game'], current_game_num + 1):
                game = storage.games.get(game_num)
                if not game:
                    continue
                
                player_suits = [c['suit'] for c in game.get('player_cards', [])]
                
                suit_map_rev = {0: '♥️', 1: '♦️', 2: '♠️', 3: '♣️'}
                predicted_suit = suit_map_rev.get(int(pred['value']), '?')
                
                if any(predicted_suit == s for s in player_suits):
                    succeeded = True
                    actual_game = game_num
                    logger.info(f"ML: масть {predicted_suit} найдена в игре #{game_num}")
                    break
            
            if succeeded:
                pred['status'] = 'win'
                pred['actual_game'] = actual_game
                self.register_prediction_result(actual_game, True, game_data, pred['attempt'], 
                                              pred.get('strategy', 'same_suit'), pred.get('skip', 2))
                await self._update_prediction_message(pred, game_data, True, context)
            else:
                if pred['attempt'] < 2:
                    plan = self._get_intelligent_dogon_plan(pred, pred['attempt'], game_data)
                    
                    pred['attempt'] += 1
                    pred['strategy'] = plan['action']
                    
                    if plan['action'] == 'change':
                        pred['value'] = plan['new_value']
                        pred['strategy'] = 'changed_suit'
                        suit_map_rev = {0: '♥️', 1: '♦️', 2: '♠️', 3: '♣️'}
                        pred['original_suit'] = suit_map_rev.get(plan['new_value'], '♥️')
                    
                    pred['skip'] = plan['skip']
                    pred['target_game'] = current_game_num + plan['skip']
                    pred['status'] = 'pending'
                    
                    logger.info(f"🧠 ИНТЕЛЛЕКТУАЛЬНЫЙ догон #{pred['id']}: {plan['reason']}")
                    await self._update_prediction_dogon(pred, context, plan)
                else:
                    pred['status'] = 'loss'
                    self.register_prediction_result(current_game_num, False, game_data, pred['attempt'],
                                                  pred.get('strategy', 'same_suit'), pred.get('skip', 2))
                    await self._update_prediction_message(pred, game_data, False, context)
    
    async def _update_prediction_dogon(self, pred, context, plan):
        if not pred.get('msg_id'):
            return
        
        try:
            moscow_tz = pytz.timezone('Europe/Moscow')
            time_str = datetime.now(moscow_tz).strftime('%H:%M')
            
            suit_map_rev = {0: '♥️', 1: '♦️', 2: '♠️', 3: '♣️'}
            suit = suit_map_rev.get(int(pred['value']), '?')
            
            text = (
                f"🧠 *ИНТЕЛЛЕКТУАЛЬНЫЙ ДОГОН #{pred['id']} — ПОПЫТКА {pred['attempt']}*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📊 *ИСТОЧНИК:* #{pred['source_game']}\n"
                f"🎯 *ЦЕЛЬ:* #{pred['target_game']}\n"
                f"🃏 *МАСТЬ:* {suit}\n"
                f"📈 *УВЕРЕННОСТЬ:* {int(pred['confidence']*100)}%\n\n"
                f"🧠 *СТРАТЕГИЯ:* {plan['reason']}\n"
                f"🗣 *КОММЕНТАРИЙ:* {plan['comment']}\n\n"
                f"📊 *СТАТИСТИКА ДОГОНОВ:*\n"
                f"• Та же масть: {self.dogon_stats['same_suit']['success']}/{self.dogon_stats['same_suit']['attempts']}\n"
                f"• Смена масти: {self.dogon_stats['changed_suit']['success']}/{self.dogon_stats['changed_suit']['attempts']}\n"
                f"⏱ {time_str} МСК"
            )
            
            await context.bot.edit_message_text(
                chat_id=OUTPUT_CHANNEL_ID,
                message_id=pred['msg_id'],
                text=text,
                parse_mode='Markdown'
            )
            
        except Exception as e:
            logger.error(f"ML: ошибка обновления сообщения: {e}")
    
    async def _update_prediction_message(self, pred, game_data, succeeded, context):
        if not pred.get('msg_id'):
            return
        
        try:
            moscow_tz = pytz.timezone('Europe/Moscow')
            time_str = datetime.now(moscow_tz).strftime('%H:%M')
            
            if succeeded:
                emoji = "✅"
                status = "ЗАШЁЛ"
                joke = self._get_funny_comment('win')
                result_info = f"\n🎯 НАЙДЕНО В ИГРЕ: #{pred.get('actual_game', '?')}"
            else:
                emoji = "❌"
                status = "НЕ ЗАШЁЛ"
                joke = self._get_funny_comment('loss')
                result_info = ""
            
            suit_map_rev = {0: '♥️', 1: '♦️', 2: '♠️', 3: '♣️'}
            suit = suit_map_rev.get(int(pred['value']), '?')
            
            total = self.predictions_stats['total']
            success = self.predictions_stats['success']
            percent = int(success / max(1, total) * 100) if total > 0 else 0
            
            attempt_names = ["основная", "догон 1", "догон 2"]
            
            text = (
                f"{emoji} *ML ПРОГНОЗ #{pred['id']} {status}!*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📊 *ИСТОЧНИК:* #{pred['source_game']}\n"
                f"🎯 *ЦЕЛЬ:* #{pred['target_game']}\n"
                f"🃏 *МАСТЬ:* {suit}\n"
                f"📈 *УВЕРЕННОСТЬ:* {int(pred['confidence']*100)}%\n"
                f"🔄 *ПОПЫТКА:* {attempt_names[pred['attempt']]}\n"
                f"{result_info}\n\n"
                f"🗣 *КОММЕНТАРИЙ:* {joke}\n\n"
                f"📊 *СТАТИСТИКА:*\n"
                f"• Всего: {total}\n"
                f"• Успешно: {success}\n"
                f"• Процент: {percent}%"
            )
            
            try:
                await context.bot.edit_message_text(
                    chat_id=OUTPUT_CHANNEL_ID,
                    message_id=pred['msg_id'],
                    text=text,
                    parse_mode='Markdown'
                )
            except Exception as e:
                logger.error(f"❌ Ошибка Markdown, отправляю без форматирования: {e}")
                await context.bot.edit_message_text(
                    chat_id=OUTPUT_CHANNEL_ID,
                    message_id=pred['msg_id'],
                    text=text.replace('*', '').replace('_', ''),
                    parse_mode=None
                )
                
        except Exception as e:
            logger.error(f"ML: ошибка обновления сообщения: {e}")
    
    async def _send_anomaly_alert(self, anomalies, game_data, context):
        try:
            if self.last_anomaly_time:
                delta = datetime.now(pytz.timezone('Europe/Moscow')) - self.last_anomaly_time
                if delta.seconds < 600:
                    return
            
            text = (
                f"🚨 *ИНТЕЛЛЕКТУАЛЬНАЯ АНОМАЛИЯ*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📊 *ИГРА:* #{game_data['game_num']}\n"
            )
            
            for a in anomalies:
                text += f"• {a}\n"
            
            text += f"\n⏱ {datetime.now(pytz.timezone('Europe/Moscow')).strftime('%H:%M')} МСК"
            
            await context.bot.send_message(
                chat_id=OUTPUT_CHANNEL_ID,
                text=text,
                parse_mode='Markdown'
            )
            
            self.last_anomaly_time = datetime.now(pytz.timezone('Europe/Moscow'))
            
        except Exception as e:
            logger.error(f"ML: ошибка отправки аномалии: {e}")
    
    async def send_detailed_stats(self, context):
        try:
            moscow_tz = pytz.timezone('Europe/Moscow')
            current_time = datetime.now(moscow_tz).strftime('%H:%M')
            
            stats_text = f"🧠 *ИНТЕЛЛЕКТУАЛЬНАЯ СТАТИСТИКА НА {current_time}*\n"
            stats_text += "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            
            total = self.predictions_stats['total']
            success = self.predictions_stats['success']
            percent = int(success / max(1, total) * 100) if total > 0 else 0
            
            stats_text += f"*ОБЩАЯ СТАТИСТИКА:*\n"
            stats_text += f"✅ Успешно: {success}\n"
            stats_text += f"❌ Неудачно: {total - success}\n"
            stats_text += f"📈 Процент: {percent}%\n\n"
            
            stats_text += f"*ЭФФЕКТИВНОСТЬ ДОГОНОВ:*\n"
            stats_text += f"🔄 Та же масть: {self.dogon_stats['same_suit']['success']}/{self.dogon_stats['same_suit']['attempts']}\n"
            stats_text += f"🎯 Смена масти: {self.dogon_stats['changed_suit']['success']}/{self.dogon_stats['changed_suit']['attempts']}\n\n"
            
            stats_text += f"*ПРОПУСКИ ИГР:*\n"
            stats_text += f"⏸ 1 игра: {self.dogon_stats['skip_1']['success']}/{self.dogon_stats['skip_1']['attempts']}\n"
            stats_text += f"⏸ 2 игры: {self.dogon_stats['skip_2']['success']}/{self.dogon_stats['skip_2']['attempts']}\n"
            stats_text += f"⏸ 3 игры: {self.dogon_stats['skip_3']['success']}/{self.dogon_stats['skip_3']['attempts']}\n"
            stats_text += f"⏸ 4 игры: {self.dogon_stats['skip_4']['success']}/{self.dogon_stats['skip_4']['attempts']}\n\n"
            
            stats_text += f"*ЧАСТОТА МАСТЕЙ:*\n"
            for suit, data in self.suit_stats.items():
                stats_text += f"{suit}: {data['frequency']*100:.1f}% (лучший пропуск: {data['best_skip']})\n"
            
            stats_text += f"\n📊 *ОБЩЕЕ:*\n"
            stats_text += f"• Всего игр: {len(self.history)}\n"
            stats_text += f"• Типы: 2к({len(self.history_2cards)}) Игрок3({len(self.history_player3)}) Банкир3({len(self.history_banker3)})\n"
            stats_text += f"• Активных прогнозов: {len([p for p in self.active_predictions if p['status'] == 'pending'])}"
            
            await context.bot.send_message(
                chat_id=OUTPUT_CHANNEL_ID,
                text=stats_text,
                parse_mode='Markdown'
            )
            
        except Exception as e:
            logger.error(f"ML: ошибка отправки статистики: {e}")

# ======== ХРАНИЛИЩЕ ========
class GameStorage:
    def __init__(self):
        self.games = {}
        self.ml_predictor = IntelMLPredictor(history_size=1000)

storage = GameStorage()
lock_fd = None

class PendingGame:
    def __init__(self, game_data, first_seen):
        self.game_data = game_data
        self.first_seen = first_seen

pending_games = {}

def acquire_lock():
    global lock_fd
    try:
        lock_fd = open(LOCK_FILE, 'w')
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        logger.info(f"🔒 Блокировка: {LOCK_FILE}")
        return True
    except:
        logger.error("❌ Бот уже запущен")
        return False

def release_lock():
    global lock_fd
    if lock_fd:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()
            if os.path.exists(LOCK_FILE):
                os.unlink(LOCK_FILE)
        except:
            pass

def check_bot_token():
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/getMe"
        req = urllib.request.Request(url, method='GET')
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            if data.get('ok'):
                logger.info(f"✅ Бот @{data['result']['username']} авторизован")
                return True
    except:
        pass
    logger.error("❌ Ошибка авторизации")
    return False

def normalize_suit(s):
    if not s:
        return None
    s = str(s).strip()
    
    # Удаляем variation selectors (коды 65038-65039)
    clean_s = ''.join(c for c in s if ord(c) not in [65038, 65039])
    
    logger.info(f"🔥 normalize_suit получил: '{s}', после очистки: '{clean_s}'")
    
    if clean_s in ('♥', '❤', '♡'):
        return '♥️'
    if clean_s in ('♠', '♤'):
        return '♠️'
    if clean_s in ('♣', '♧'):
        return '♣️'
    if clean_s in ('♦', '♢'):
        return '♦️'
    
    logger.warning(f"⚠️ Неизвестный символ масти: '{s}' (очищенный: '{clean_s}')")
    return None

def extract_suits(text):
    suits = []
    for ch in text:
        norm = normalize_suit(ch)
        if norm:
            suits.append(norm)
    return suits

def extract_left_part(text):
    separators = [' 👈 ', '👈', ' - ', ' – ', '—', '-', '👉👈', '👈👉', '🔰']
    for sep in separators:
        if sep in text:
            parts = text.split(sep, 1)
            left = re.sub(r'#N\d+\.?\s*', '', parts[0].strip())
            return left
    return text.strip()

def parse_game_data(text):
    text = re.sub(r'([♥️♦️♠️♣️])\1+', r'\1', text)
    
    logger.info(f"🔥 СЫРОЙ ТЕКСТ: {repr(text)}")
    
    match = re.search(r'#N(\d+)', text)
    if not match:
        return None
    
    game_num = int(match.group(1))
    
    has_r_tag = '#R' in text
    has_x_tag = '#X' in text or '#X🟡' in text
    has_check = '✅' in text
    has_green_square = '🟩' in text
    is_tie = '🔰' in text
    
    is_complete = has_check or has_green_square or is_tie
    
    player_draws = '👈' in text
    banker_draws = '👉' in text
    
    left_part = extract_left_part(text)
    left_part = re.sub(r'([♥️♦️♠️♣️])\1+', r'\1', left_part)
    
    left_suits = extract_suits(left_part)
    
    if not left_suits:
        return None
    
    first_suit = left_suits[0] if len(left_suits) > 0 else None
    second_suit = left_suits[1] if len(left_suits) > 1 else None
    
    player_cards = []
    banker_cards = []
    
    card_pattern = r'(\d+|A|J|Q|K)\s*([♥️♦️♠️♣️])'
    
    for match in re.finditer(card_pattern, left_part):
        value, suit = match.groups()
        suit = normalize_suit(suit)
        if suit and value:
            player_cards.append({'value': value, 'suit': suit})
    
    separators = [' 👈 ', '👈', ' - ', ' – ', '—', '-', '👉👈', '👈👉']
    right_part = ""
    for sep in separators:
        if sep in text:
            right_part = text.split(sep, 1)[1]
            break
    
    right_part = re.sub(r'([♥️♦️♠️♣️])\1+', r'\1', right_part)
    
    for match in re.finditer(card_pattern, right_part):
        value, suit = match.groups()
        suit = normalize_suit(suit)
        if suit and value:
            banker_cards.append({'value': value, 'suit': suit})
    
    if len(player_cards) > 3:
        logger.warning(f"⚠️ Слишком много карт у игрока: {player_cards}, обрезаем")
        player_cards = player_cards[:3]
    
    if len(banker_cards) > 3:
        logger.warning(f"⚠️ Слишком много карт у банкира: {banker_cards}, обрезаем")
        banker_cards = banker_cards[:3]
    
    winner = None
    if '✅' in text:
        winner = 'banker'
    elif '🔰' in text:
        winner = 'tie'
    else:
        winner = 'player'
    
    total_match = re.search(r'#T(\d+)', text)
    total_sum = int(total_match.group(1)) if total_match else 0
    
    player_score = 0
    banker_score = 0
    
    score_match = re.search(r'(\d+)\s*\(', left_part)
    if score_match:
        player_score = int(score_match.group(1))
    
    score_match = re.search(r'(\d+)\s*\(', right_part)
    if score_match:
        banker_score = int(score_match.group(1))
    
    return {
        'game_num': game_num,
        'first_suit': first_suit,
        'second_suit': second_suit,
        'all_suits': left_suits,
        'left_cards': left_suits,
        'has_r_tag': has_r_tag,
        'has_x_tag': has_x_tag,
        'has_check': has_check,
        'has_green_square': has_green_square,
        'player_draws': player_draws,
        'banker_draws': banker_draws,
        'is_complete': is_complete,
        'is_tie': is_tie,
        'player_cards': player_cards,
        'banker_cards': banker_cards,
        'player_score': player_score,
        'banker_score': banker_score,
        'winner': winner,
        'total_sum': total_sum,
        'timestamp': datetime.now(pytz.timezone('Europe/Moscow'))
    }

async def check_ml_predictions(current_game_num, game_data, context):
    await storage.ml_predictor.check_predictions(current_game_num, game_data, context)

async def handle_new_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        message = None
        is_edit = False
        
        if update.channel_post:
            message = update.channel_post
            is_edit = False
        elif update.edited_channel_post:
            message = update.edited_channel_post
            is_edit = True
        else:
            return
        
        text = message.text
        if not text:
            return
        
        logger.info(f"\n{'='*60}")
        logger.info(f"📥 {'РЕДАКТИРОВАНИЕ' if is_edit else 'НОВОЕ'}: {text[:150]}...")
        
        game_data = parse_game_data(text)
        if not game_data:
            return
        
        game_num = game_data['game_num']
        
        logger.info(f"📊 Игра #{game_num}")
        
        player_cards_str = []
        for c in game_data['player_cards']:
            player_cards_str.append(f"{c['value']}{c['suit']}")
        logger.info(f"   Карты игрока: {player_cards_str}")
        
        banker_cards_str = []
        for c in game_data['banker_cards']:
            banker_cards_str.append(f"{c['value']}{c['suit']}")
        logger.info(f"   Карты банкира: {banker_cards_str}")
        
        logger.info(f"   Теги: R={game_data['has_r_tag']}, X={game_data['has_x_tag']}")
        logger.info(f"   Добор: игрок {'👈' if game_data['player_draws'] else 'нет'}, банкир {'👉' if game_data['banker_draws'] else 'нет'}")
        logger.info(f"   Завершена: {game_data['is_complete']}")
        
        if is_edit:
            logger.info(f"✏️ Редактирование игры #{game_num}")
            storage.games[game_num] = game_data
            await check_ml_predictions(game_num, game_data, context)
            
            if game_num in pending_games:
                del pending_games[game_num]
            
            await storage.ml_predictor.analyze_and_predict(game_data, context)
            return
        
        if game_data['player_draws'] or game_data['banker_draws']:
            logger.info(f"⏳ Игра #{game_num}: ожидание третьей карты")
            pending_games[game_num] = PendingGame(game_data, datetime.now())
            storage.games[game_num] = game_data
            await storage.ml_predictor.analyze_and_predict(game_data, context)
            return
        
        if not game_data['player_draws'] and not game_data['banker_draws']:
            if game_num in pending_games:
                logger.info(f"✅ Игра #{game_num}: получена полная версия")
                del pending_games[game_num]
            else:
                logger.info(f"✅ Игра #{game_num}: полная версия сразу")
            
            storage.games[game_num] = game_data
            
            if game_data['is_complete']:
                logger.info(f"🔍 Игра #{game_num} завершена, проверяем прогнозы")
                await check_ml_predictions(game_num, game_data, context)
            
            await storage.ml_predictor.analyze_and_predict(game_data, context)
        
        current_time = datetime.now()
        for pending_num in list(pending_games.keys()):
            if pending_num < game_num - 20:
                logger.info(f"🧹 Очистка ожидания игры #{pending_num}")
                del pending_games[pending_num]
        
        if len(storage.games) > 200:
            oldest = min(storage.games.keys())
            del storage.games[oldest]
        
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")

async def error_handler(update, context):
    try:
        if isinstance(context.error, Conflict):
            logger.warning("⚠️ Конфликт, выходим")
            release_lock()
            sys.exit(1)
    except:
        pass

async def check_stuck_games(context: ContextTypes.DEFAULT_TYPE):
    current_time = datetime.now()
    for game_num, pending in list(pending_games.items()):
        if (current_time - pending.first_seen).seconds > 120:
            logger.info(f"⏰ Игра #{game_num} зависла в ожидании >2 мин, проверяем")
            
            if game_num in storage.games:
                await check_ml_predictions(game_num, storage.games[game_num], context)
            
            del pending_games[game_num]

async def three_hour_stats(context: ContextTypes.DEFAULT_TYPE):
    await storage.ml_predictor.send_detailed_stats(context)

def main():
    print("\n" + "="*60)
    print("🧠 ИНТЕЛЛЕКТУАЛЬНЫЙ ML БОТ - ФИНАЛЬНАЯ ВЕРСИЯ")
    print("="*60)
    print("✅ ОДИН АКТИВНЫЙ ПРОГНОЗ")
    print("✅ ЗАЩИТА ОТ ЗАВИСШИХ (30 мин)")
    print("✅ Таймер 2-7 минут между прогнозами")
    print("✅ Догоны строго по порядку")
    print("✅ Прогнозы только на увеличение")
    print("✅ Масть только у игрока")
    print("="*60)
    
    if not acquire_lock():
        sys.exit(1)
    
    if not check_bot_token():
        release_lock()
        sys.exit(1)
    
    app = Application.builder().token(TOKEN).build()
    
    app.add_error_handler(error_handler)
    app.add_handler(MessageHandler(
        filters.Chat(INPUT_CHANNEL_ID) & filters.TEXT,
        handle_new_game
    ))
    
    if app.job_queue:
        app.job_queue.run_repeating(three_hour_stats, interval=10800, first=10)
        app.job_queue.run_repeating(check_stuck_games, interval=30, first=10)
        logger.info("✅ Планировщик запущен")
    else:
        logger.error("❌ JobQueue не доступен")
    
    try:
        app.run_polling(
            allowed_updates=['channel_post', 'edited_channel_post'],
            drop_pending_updates=True
        )
    finally:
        release_lock()

if __name__ == "__main__":
    import signal
    def signal_handler(sig, frame):
        logger.info("👋 Бот останавливается...")
        release_lock()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    main()