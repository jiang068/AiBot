import math
import threading
import time
from dataclasses import dataclass

from ..chat.config import global_config
from src.common.logger import get_module_logger

logger = get_module_logger("mood_manager")


@dataclass
class MoodState:
    valence: float  # 愉悦度 (-1 到 1)
    arousal: float  # 唤醒度 (0 到 1)
    text: str  # 心情文本描述


class MoodManager:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self.current_mood = MoodState(valence=0.0, arousal=0.5, text="平静")
        self.decay_rate_valence = 1 - global_config.mood_decay_rate
        self.decay_rate_arousal = 1 - global_config.mood_decay_rate
        self.last_update = time.time()
        self._running = False
        self._update_thread = None
        self.emotion_map = {
            "happy": (0.8, 0.6),
            "angry": (-0.7, 0.7),
            "sad": (-0.6, 0.3),
            "surprised": (0.4, 0.8),
            "disgusted": (-0.8, 0.5),
            "fearful": (-0.7, 0.6),
            "neutral": (0.0, 0.5),
        }
        self.mood_text_map = {
            (0.5, 0.7): "兴奋",
            (0.3, 0.8): "快乐",
            (0.2, 0.65): "满足",
            (-0.5, 0.7): "愤怒",
            (-0.3, 0.8): "焦虑",
            (-0.2, 0.65): "烦躁",
            (-0.5, 0.3): "悲伤",
            (-0.3, 0.35): "疲倦",
            (-0.4, 0.15): "疲倦",
            (0.2, 0.45): "平静",
            (0.3, 0.4): "安宁",
            (0.5, 0.3): "放松",
        }

    @classmethod
    def get_instance(cls) -> "MoodManager":
        if cls._instance is None:
            cls._instance = MoodManager()
        return cls._instance

    def start_mood_update(self, update_interval: float = 1.0) -> None:
        if self._running:
            return
        self._running = True
        self._update_thread = threading.Thread(
            target=self._continuous_mood_update, args=(update_interval,), daemon=True
        )
        self._update_thread.start()

    def stop_mood_update(self) -> None:
        self._running = False
        if self._update_thread and self._update_thread.is_alive():
            self._update_thread.join()

    def _continuous_mood_update(self, update_interval: float) -> None:
        while self._running:
            self._apply_decay()
            self._update_mood_text()
            time.sleep(update_interval)

    def _apply_decay(self) -> None:
        current_time = time.time()
        time_diff = current_time - self.last_update
        self.current_mood.valence = 0.0 + self.current_mood.valence * math.exp(-self.decay_rate_valence * time_diff)
        self.current_mood.arousal = 0.5 + (self.current_mood.arousal - 0.5) * math.exp(-self.decay_rate_arousal * time_diff)
        self.current_mood.valence = max(-1.0, min(1.0, self.current_mood.valence))
        self.current_mood.arousal = max(0.0, min(1.0, self.current_mood.arousal))
        self.last_update = current_time

    def _update_mood_text(self) -> None:
        closest_mood = None
        min_distance = float("inf")
        for (v, a), text in self.mood_text_map.items():
            distance = math.sqrt((self.current_mood.valence - v) ** 2 + (self.current_mood.arousal - a) ** 2)
            if distance < min_distance:
                min_distance = distance
                closest_mood = text
        if closest_mood:
            self.current_mood.text = closest_mood

    def get_prompt(self) -> str:
        base_prompt = f"当前心情：{self.current_mood.text}。"
        if self.current_mood.valence > 0.5:
            base_prompt += "你现在心情很好，"
        elif self.current_mood.valence < -0.5:
            base_prompt += "你现在心情不太好，"
        if self.current_mood.arousal > 0.7:
            base_prompt += "情绪比较激动。"
        elif self.current_mood.arousal < 0.3:
            base_prompt += "情绪比较平静。"
        return base_prompt

    def get_current_mood(self) -> MoodState:
        return self.current_mood

    def print_mood_status(self) -> None:
        logger.info(
            f"[情绪状态] 愉悦度: {self.current_mood.valence:.2f}, "
            f"唤醒度: {self.current_mood.arousal:.2f}, "
            f"心情: {self.current_mood.text}"
        )

    def update_mood_from_emotion(self, emotion: str, intensity: float = 1.0) -> None:
        if emotion not in self.emotion_map:
            return
        valence_change, arousal_change = self.emotion_map[emotion]
        self.current_mood.valence = max(-1.0, min(1.0, self.current_mood.valence + valence_change * intensity))
        self.current_mood.arousal = max(0.0, min(1.0, self.current_mood.arousal + arousal_change * intensity))
        self._update_mood_text()