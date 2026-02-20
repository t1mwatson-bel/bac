# ai_predict.py
import numpy as np
import joblib
import sqlite3
import pandas as pd

MODEL_FILE = 'ai_model.pkl'
ENCODER_FILE = 'suit_encoder.pkl'
DB_FILE = 'bot3_stats.db'

class AIPredictor:
    def __init__(self):
        self.model = None
        self.encoder = None
        self.metadata = None
        self.load()
    
    def load(self):
        """Загружает модель и кодировщик"""
        try:
            self.model = joblib.load(MODEL_FILE)
            self.encoder = joblib.load(ENCODER_FILE)
            self.metadata = joblib.load('ai_metadata.pkl')
            print(f"✅ AI модель загружена")
        except:
            print(f"⚠️ Модель не найдена, используется классический прогноз")
            self.model = None
    
    def predict(self, source_game_num, target_suit):
        """
        Делает предсказание для прогноза
        source_game_num: номер исходной игры
        target_suit: целевая масть (из классики)
        """
        if self.model is None:
            return None, 0.0, []
        
        try:
            # Получаем данные исходной игры
            conn = sqlite3.connect(DB_FILE)
            source_data = pd.read_sql_query(f'''
                SELECT left_suits, right_suits, has_r, has_x, is_tie 
                FROM games 
                WHERE game_num = {source_game_num}
            ''', conn)
            conn.close()
            
            if len(source_data) == 0:
                return None, 0.0, []
            
            # Формируем признаки
            features = []
            
            # Признак 1: масть в исходной игре
            if source_data.iloc[0]['right_suits']:
                right_suits = source_data.iloc[0]['right_suits'].split(',')
                if len(right_suits) > 0:
                    features.append(self.encoder.transform([right_suits[0]])[0])
                else:
                    features.append(-1)
            else:
                features.append(-1)
            
            # Признак 2-4: теги
            features.append(source_data.iloc[0]['has_r'])
            features.append(source_data.iloc[0]['has_x'])
            features.append(source_data.iloc[0]['is_tie'])
            
            # Признак 5: попытка 0 (первая)
            features.append(0)
            
            # Признак 6: целевая масть
            features.append(self.encoder.transform([target_suit])[0])
            
            # Предсказание вероятностей
            features_array = np.array(features).reshape(1, -1)
            proba = self.model.predict_proba(features_array)[0]
            
            # Вероятность выигрыша
            win_probability = proba[1]
            
            # Определяем, какую масть рекомендовать
            # Пока просто возвращаем ту же масть с вероятностью
            # В будущем можно расширить
            
            return target_suit, win_probability, self.metadata.get('feature_names', [])
            
        except Exception as e:
            print(f"❌ Ошибка AI предсказания: {e}")
            return None, 0.0, []

# Глобальный экземпляр
ai_predictor = AIPredictor()

def get_ai_prediction(source_game, classic_suit):
    """Удобная функция для вызова из бота"""
    return ai_predictor.predict(source_game, classic_suit)