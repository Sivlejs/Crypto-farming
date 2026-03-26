"""
AI-Powered Mining Optimizer for Nexus AI.

Integrates machine learning and AI to create the most efficient crypto mining
operation possible. This module uses:

- Neural network-based hashrate prediction and optimization
- Reinforcement learning for dynamic parameter tuning
- Time series analysis for profitability forecasting
- Pattern recognition for optimal coin switching
- Adaptive learning from real-time mining performance
- Market sentiment analysis for mining decisions
- Anomaly detection for hardware health monitoring

The AI learns from mining history to continuously improve efficiency,
predict optimal settings, and maximize profitability across all hardware.
"""
from __future__ import annotations

import json
import math
import os
import pickle
import random
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

from nexus.utils.logger import get_logger

logger = get_logger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# AI Mining State and Data Structures
# ══════════════════════════════════════════════════════════════════════════════

class MiningDecision(str, Enum):
    """AI mining decisions."""
    CONTINUE = "continue"           # Keep current settings
    INCREASE_INTENSITY = "increase_intensity"
    DECREASE_INTENSITY = "decrease_intensity"
    SWITCH_ALGORITHM = "switch_algorithm"
    SWITCH_COIN = "switch_coin"
    PAUSE_MINING = "pause_mining"
    OPTIMIZE_POWER = "optimize_power"
    COOL_DOWN = "cool_down"


@dataclass
class MiningSnapshot:
    """Point-in-time snapshot of mining state for learning."""
    timestamp: float
    
    # Hardware state
    gpu_id: int
    gpu_name: str
    temperature_c: float
    power_watts: float
    fan_speed_percent: float
    memory_used_mb: int
    
    # Mining state
    algorithm: str
    coin: str
    intensity: int
    hashrate: float
    accepted_shares: int
    rejected_shares: int
    
    # Settings
    core_clock_offset: int
    memory_clock_offset: int
    power_limit_percent: int
    
    # Market context
    coin_price_usd: float
    network_difficulty: float
    estimated_daily_usd: float
    
    # Environment
    ambient_temp_c: float = 25.0
    electricity_cost_kwh: float = 0.10
    
    def to_vector(self) -> np.ndarray:
        """Convert to feature vector for ML model."""
        return np.array([
            self.temperature_c / 100.0,
            self.power_watts / 500.0,
            self.fan_speed_percent / 100.0,
            self.intensity / 100.0,
            self.hashrate / 1e9,  # Normalize to GH/s scale
            self.core_clock_offset / 500.0,
            self.memory_clock_offset / 1500.0,
            self.power_limit_percent / 100.0,
            self.coin_price_usd / 100.0,
            math.log10(max(1, self.network_difficulty)) / 20.0,
            self.estimated_daily_usd / 10.0,
            self.electricity_cost_kwh,
            self.ambient_temp_c / 50.0,
            (self.accepted_shares / max(1, self.accepted_shares + self.rejected_shares)),
        ], dtype=np.float32)
    
    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "gpu_id": self.gpu_id,
            "gpu_name": self.gpu_name,
            "temperature_c": self.temperature_c,
            "power_watts": self.power_watts,
            "hashrate": self.hashrate,
            "algorithm": self.algorithm,
            "coin": self.coin,
            "intensity": self.intensity,
            "estimated_daily_usd": self.estimated_daily_usd,
        }


@dataclass
class OptimizationResult:
    """Result from AI optimization."""
    decision: MiningDecision
    confidence: float  # 0.0 - 1.0
    recommended_settings: Dict[str, Any]
    reasoning: str
    predicted_improvement_percent: float
    
    def to_dict(self) -> dict:
        return {
            "decision": self.decision.value,
            "confidence": round(self.confidence, 3),
            "recommended_settings": self.recommended_settings,
            "reasoning": self.reasoning,
            "predicted_improvement_percent": round(self.predicted_improvement_percent, 2),
        }


# ══════════════════════════════════════════════════════════════════════════════
# Neural Network for Hashrate Prediction
# ══════════════════════════════════════════════════════════════════════════════

class SimpleNeuralNetwork:
    """
    Lightweight neural network for hashrate and efficiency prediction.
    
    Uses numpy for portability (no PyTorch/TensorFlow dependency).
    Architecture: Input -> Hidden(64) -> Hidden(32) -> Output
    """
    
    # Clipping bounds for sigmoid to prevent overflow in exponential calculation
    # Values beyond these bounds would cause np.exp to overflow/underflow
    SIGMOID_CLIP_MIN = -500
    SIGMOID_CLIP_MAX = 500
    
    def __init__(self, input_size: int = 14, hidden1: int = 64, hidden2: int = 32, output_size: int = 3):
        self.input_size = input_size
        
        # Xavier initialization for weights
        self.W1 = np.random.randn(input_size, hidden1).astype(np.float32) * np.sqrt(2.0 / input_size)
        self.b1 = np.zeros(hidden1, dtype=np.float32)
        
        self.W2 = np.random.randn(hidden1, hidden2).astype(np.float32) * np.sqrt(2.0 / hidden1)
        self.b2 = np.zeros(hidden2, dtype=np.float32)
        
        self.W3 = np.random.randn(hidden2, output_size).astype(np.float32) * np.sqrt(2.0 / hidden2)
        self.b3 = np.zeros(output_size, dtype=np.float32)
        
        self.learning_rate = 0.001
        self._trained_samples = 0
    
    def _relu(self, x: np.ndarray) -> np.ndarray:
        return np.maximum(0, x)
    
    def _relu_derivative(self, x: np.ndarray) -> np.ndarray:
        return (x > 0).astype(np.float32)
    
    def _sigmoid(self, x: np.ndarray) -> np.ndarray:
        # Clip values to prevent overflow in np.exp()
        return 1 / (1 + np.exp(-np.clip(x, self.SIGMOID_CLIP_MIN, self.SIGMOID_CLIP_MAX)))
    
    def forward(self, x: np.ndarray) -> np.ndarray:
        """Forward pass through the network."""
        self._z1 = x @ self.W1 + self.b1
        self._a1 = self._relu(self._z1)
        
        self._z2 = self._a1 @ self.W2 + self.b2
        self._a2 = self._relu(self._z2)
        
        self._z3 = self._a2 @ self.W3 + self.b3
        output = self._sigmoid(self._z3)
        
        self._last_input = x
        return output
    
    def predict(self, snapshot: MiningSnapshot) -> Tuple[float, float, float]:
        """
        Predict optimal settings from a mining snapshot.
        
        Returns:
            (predicted_hashrate_improvement, optimal_intensity, optimal_power_limit)
        """
        x = snapshot.to_vector().reshape(1, -1)
        output = self.forward(x)[0]
        
        # Output interpretation:
        # [0] = hashrate improvement potential (0-1 -> 0-50%)
        # [1] = optimal intensity (0-1 -> 50-100%)
        # [2] = optimal power limit (0-1 -> 60-100%)
        
        hashrate_improvement = output[0] * 50.0  # 0-50% improvement
        optimal_intensity = 50 + output[1] * 50   # 50-100%
        optimal_power = 60 + output[2] * 40       # 60-100%
        
        return hashrate_improvement, optimal_intensity, optimal_power
    
    def train(self, x: np.ndarray, y: np.ndarray):
        """Train the network with a single sample (online learning)."""
        x = x.reshape(1, -1)
        y = y.reshape(1, -1)
        
        # Forward pass
        output = self.forward(x)
        
        # Compute loss gradient
        d_output = output - y
        
        # Backpropagation
        d_W3 = self._a2.T @ d_output
        d_b3 = d_output.sum(axis=0)
        
        d_a2 = d_output @ self.W3.T
        d_z2 = d_a2 * self._relu_derivative(self._z2)
        d_W2 = self._a1.T @ d_z2
        d_b2 = d_z2.sum(axis=0)
        
        d_a1 = d_z2 @ self.W2.T
        d_z1 = d_a1 * self._relu_derivative(self._z1)
        d_W1 = x.T @ d_z1
        d_b1 = d_z1.sum(axis=0)
        
        # Update weights
        self.W3 -= self.learning_rate * d_W3
        self.b3 -= self.learning_rate * d_b3
        self.W2 -= self.learning_rate * d_W2
        self.b2 -= self.learning_rate * d_b2
        self.W1 -= self.learning_rate * d_W1
        self.b1 -= self.learning_rate * d_b1
        
        self._trained_samples += 1
    
    def save(self, path: str):
        """Save model weights to file."""
        with open(path, 'wb') as f:
            pickle.dump({
                'W1': self.W1, 'b1': self.b1,
                'W2': self.W2, 'b2': self.b2,
                'W3': self.W3, 'b3': self.b3,
                'trained_samples': self._trained_samples,
            }, f)
    
    def load(self, path: str) -> bool:
        """Load model weights from file."""
        try:
            with open(path, 'rb') as f:
                data = pickle.load(f)
                self.W1 = data['W1']
                self.b1 = data['b1']
                self.W2 = data['W2']
                self.b2 = data['b2']
                self.W3 = data['W3']
                self.b3 = data['b3']
                self._trained_samples = data.get('trained_samples', 0)
            return True
        except Exception as e:
            logger.debug("Failed to load model: %s", e)
            return False


# ══════════════════════════════════════════════════════════════════════════════
# Reinforcement Learning Agent for Mining Optimization
# ══════════════════════════════════════════════════════════════════════════════

class MiningRLAgent:
    """
    Reinforcement learning agent for dynamic mining optimization.
    
    Uses Q-learning with experience replay to learn optimal mining
    strategies across different market conditions and hardware states.
    """
    
    # Action space
    ACTIONS = [
        MiningDecision.CONTINUE,
        MiningDecision.INCREASE_INTENSITY,
        MiningDecision.DECREASE_INTENSITY,
        MiningDecision.OPTIMIZE_POWER,
        MiningDecision.COOL_DOWN,
    ]
    
    def __init__(
        self,
        state_size: int = 14,
        learning_rate: float = 0.01,
        discount_factor: float = 0.95,
        exploration_rate: float = 0.3,
        exploration_decay: float = 0.995,
        min_exploration: float = 0.05,
    ):
        self.state_size = state_size
        self.action_size = len(self.ACTIONS)
        
        self.lr = learning_rate
        self.gamma = discount_factor
        self.epsilon = exploration_rate
        self.epsilon_decay = exploration_decay
        self.epsilon_min = min_exploration
        
        # Q-network (simple linear approximation)
        self.Q = np.random.randn(state_size, self.action_size).astype(np.float32) * 0.01
        
        # Experience replay buffer
        self.memory: deque = deque(maxlen=10000)
        self.batch_size = 32
        
        # Performance tracking
        self._total_reward = 0.0
        self._episodes = 0
        self._last_state: Optional[np.ndarray] = None
        self._last_action: Optional[int] = None
    
    def get_state(self, snapshot: MiningSnapshot) -> np.ndarray:
        """Convert mining snapshot to RL state vector."""
        return snapshot.to_vector()
    
    def choose_action(self, state: np.ndarray, training: bool = True) -> MiningDecision:
        """
        Choose an action using epsilon-greedy policy.
        """
        if training and random.random() < self.epsilon:
            # Exploration: random action
            action_idx = random.randint(0, self.action_size - 1)
        else:
            # Exploitation: best Q-value action
            q_values = state @ self.Q
            action_idx = int(np.argmax(q_values))
        
        self._last_state = state.copy()
        self._last_action = action_idx
        
        return self.ACTIONS[action_idx]
    
    def calculate_reward(
        self,
        old_snapshot: MiningSnapshot,
        new_snapshot: MiningSnapshot,
        action: MiningDecision
    ) -> float:
        """
        Calculate reward based on mining performance change.
        
        Rewards:
        - Increased hashrate: positive
        - Reduced power consumption: positive (efficiency)
        - Stable temperature: positive
        - Reduced rejected shares: positive
        - Increased profit: strongly positive
        """
        reward = 0.0
        
        # Hashrate improvement (most important)
        if old_snapshot.hashrate > 0:
            hashrate_change = (new_snapshot.hashrate - old_snapshot.hashrate) / old_snapshot.hashrate
            reward += hashrate_change * 10.0
        
        # Power efficiency improvement
        old_efficiency = old_snapshot.hashrate / max(1, old_snapshot.power_watts)
        new_efficiency = new_snapshot.hashrate / max(1, new_snapshot.power_watts)
        if old_efficiency > 0:
            efficiency_change = (new_efficiency - old_efficiency) / old_efficiency
            reward += efficiency_change * 5.0
        
        # Temperature management
        if new_snapshot.temperature_c < old_snapshot.temperature_c:
            reward += 0.5  # Cooling down is good
        elif new_snapshot.temperature_c > 85:
            reward -= 2.0  # Overheating is bad
        
        # Share acceptance rate
        old_rate = old_snapshot.accepted_shares / max(1, old_snapshot.accepted_shares + old_snapshot.rejected_shares)
        new_rate = new_snapshot.accepted_shares / max(1, new_snapshot.accepted_shares + new_snapshot.rejected_shares)
        reward += (new_rate - old_rate) * 3.0
        
        # Profit improvement
        if old_snapshot.estimated_daily_usd > 0:
            profit_change = (new_snapshot.estimated_daily_usd - old_snapshot.estimated_daily_usd) / old_snapshot.estimated_daily_usd
            reward += profit_change * 15.0
        
        return reward
    
    def remember(self, state: np.ndarray, action: int, reward: float, next_state: np.ndarray, done: bool):
        """Store experience in replay buffer."""
        self.memory.append((state, action, reward, next_state, done))
        self._total_reward += reward
    
    def replay(self):
        """Train on a batch of experiences."""
        if len(self.memory) < self.batch_size:
            return
        
        batch = random.sample(self.memory, self.batch_size)
        
        for state, action, reward, next_state, done in batch:
            target = reward
            if not done:
                target += self.gamma * np.max(next_state @ self.Q)
            
            # Q-learning update
            current_q = state @ self.Q
            td_error = target - current_q[action]
            
            # Gradient update
            self.Q[:, action] += self.lr * td_error * state
        
        # Decay exploration rate
        if self.epsilon > self.epsilon_min:
            self.epsilon *= self.epsilon_decay
        
        self._episodes += 1
    
    def step(self, old_snapshot: MiningSnapshot, new_snapshot: MiningSnapshot, action: MiningDecision):
        """
        Complete one RL step: observe reward and learn.
        """
        state = self.get_state(old_snapshot)
        next_state = self.get_state(new_snapshot)
        action_idx = self.ACTIONS.index(action)
        reward = self.calculate_reward(old_snapshot, new_snapshot, action)
        
        self.remember(state, action_idx, reward, next_state, done=False)
        self.replay()
    
    def get_stats(self) -> dict:
        """Get agent statistics."""
        return {
            "total_reward": round(self._total_reward, 2),
            "episodes": self._episodes,
            "exploration_rate": round(self.epsilon, 4),
            "memory_size": len(self.memory),
        }
    
    def save(self, path: str):
        """Save agent state."""
        with open(path, 'wb') as f:
            pickle.dump({
                'Q': self.Q,
                'epsilon': self.epsilon,
                'total_reward': self._total_reward,
                'episodes': self._episodes,
            }, f)
    
    def load(self, path: str) -> bool:
        """Load agent state."""
        try:
            with open(path, 'rb') as f:
                data = pickle.load(f)
                self.Q = data['Q']
                self.epsilon = data.get('epsilon', self.epsilon)
                self._total_reward = data.get('total_reward', 0)
                self._episodes = data.get('episodes', 0)
            return True
        except Exception as e:
            logger.debug("Failed to load RL agent: %s", e)
            return False


# ══════════════════════════════════════════════════════════════════════════════
# Time Series Predictor for Profitability Forecasting
# ══════════════════════════════════════════════════════════════════════════════

class ProfitabilityPredictor:
    """
    Time series predictor for mining profitability forecasting.
    
    Uses exponential smoothing and trend analysis to predict:
    - Future coin prices
    - Network difficulty changes
    - Optimal mining windows
    """
    
    def __init__(self, history_size: int = 1000):
        self._history_size = history_size
        
        # Price history per coin
        self._price_history: Dict[str, deque] = {}
        
        # Difficulty history per algorithm
        self._difficulty_history: Dict[str, deque] = {}
        
        # Profitability history
        self._profit_history: Dict[str, deque] = {}
        
        # Smoothing parameters
        self._alpha = 0.3  # Recent data weight
        self._beta = 0.1   # Trend weight
    
    def record_price(self, coin: str, price_usd: float, timestamp: Optional[float] = None):
        """Record a coin price observation."""
        if coin not in self._price_history:
            self._price_history[coin] = deque(maxlen=self._history_size)
        
        self._price_history[coin].append({
            "timestamp": timestamp or time.time(),
            "price": price_usd,
        })
    
    def record_difficulty(self, algorithm: str, difficulty: float, timestamp: Optional[float] = None):
        """Record network difficulty observation."""
        if algorithm not in self._difficulty_history:
            self._difficulty_history[algorithm] = deque(maxlen=self._history_size)
        
        self._difficulty_history[algorithm].append({
            "timestamp": timestamp or time.time(),
            "difficulty": difficulty,
        })
    
    def record_profit(self, coin: str, daily_usd: float, timestamp: Optional[float] = None):
        """Record profitability observation."""
        if coin not in self._profit_history:
            self._profit_history[coin] = deque(maxlen=self._history_size)
        
        self._profit_history[coin].append({
            "timestamp": timestamp or time.time(),
            "daily_usd": daily_usd,
        })
    
    def predict_price(self, coin: str, hours_ahead: int = 24) -> Tuple[float, float]:
        """
        Predict future coin price using exponential smoothing.
        
        Returns:
            (predicted_price, confidence)
        """
        if coin not in self._price_history or len(self._price_history[coin]) < 10:
            return 0.0, 0.0
        
        history = list(self._price_history[coin])
        prices = [h["price"] for h in history]
        
        # Double exponential smoothing
        level = prices[0]
        trend = 0.0
        
        for price in prices[1:]:
            new_level = self._alpha * price + (1 - self._alpha) * (level + trend)
            trend = self._beta * (new_level - level) + (1 - self._beta) * trend
            level = new_level
        
        # Predict future
        periods = hours_ahead
        prediction = level + periods * trend
        
        # Confidence based on volatility
        if len(prices) > 10:
            volatility = np.std(prices[-10:]) / np.mean(prices[-10:])
            confidence = max(0.1, 1.0 - volatility * 5)
        else:
            confidence = 0.5
        
        return max(0, prediction), confidence
    
    def predict_difficulty(self, algorithm: str, hours_ahead: int = 24) -> Tuple[float, float]:
        """Predict future network difficulty."""
        if algorithm not in self._difficulty_history or len(self._difficulty_history[algorithm]) < 5:
            return 0.0, 0.0
        
        history = list(self._difficulty_history[algorithm])
        difficulties = [h["difficulty"] for h in history]
        
        # Simple trend prediction (difficulty usually increases)
        if len(difficulties) >= 2:
            recent_trend = (difficulties[-1] - difficulties[-2]) / max(1, difficulties[-2])
            prediction = difficulties[-1] * (1 + recent_trend * hours_ahead / 24)
            confidence = 0.7
        else:
            prediction = difficulties[-1]
            confidence = 0.5
        
        return prediction, confidence
    
    def get_best_mining_time(self, coin: str) -> Tuple[str, float]:
        """
        Determine the best time to mine a coin based on patterns.
        
        Returns:
            (recommendation, confidence)
        """
        if coin not in self._profit_history or len(self._profit_history[coin]) < 24:
            return "insufficient_data", 0.0
        
        history = list(self._profit_history[coin])
        
        # Analyze hourly patterns
        hourly_profits: Dict[int, List[float]] = {h: [] for h in range(24)}
        
        for entry in history:
            hour = datetime.fromtimestamp(entry["timestamp"]).hour
            hourly_profits[hour].append(entry["daily_usd"])
        
        # Find best hours
        hourly_avg = {h: np.mean(profits) if profits else 0 for h, profits in hourly_profits.items()}
        best_hour = max(hourly_avg, key=hourly_avg.get)
        current_hour = datetime.now().hour
        
        if hourly_avg[current_hour] >= hourly_avg[best_hour] * 0.95:
            return "mine_now", 0.8
        elif best_hour > current_hour:
            return f"wait_{best_hour - current_hour}_hours", 0.6
        else:
            return f"wait_{24 - current_hour + best_hour}_hours", 0.6
    
    def get_forecast(self, coins: List[str], hours_ahead: int = 24) -> Dict[str, dict]:
        """Get profitability forecast for multiple coins."""
        forecasts = {}
        
        for coin in coins:
            price_pred, price_conf = self.predict_price(coin, hours_ahead)
            
            forecasts[coin] = {
                "predicted_price_usd": round(price_pred, 4),
                "price_confidence": round(price_conf, 3),
                "mining_recommendation": self.get_best_mining_time(coin)[0],
            }
        
        return forecasts


# ══════════════════════════════════════════════════════════════════════════════
# Main AI Mining Optimizer
# ══════════════════════════════════════════════════════════════════════════════

class AIMiningOptimizer:
    """
    Central AI system for mining optimization.
    
    Combines multiple AI components:
    - Neural network for hashrate prediction
    - RL agent for dynamic optimization
    - Time series predictor for forecasting
    - Pattern recognition for anomaly detection
    
    This creates an intelligent mining operation that continuously
    learns and improves its efficiency.
    """
    
    def __init__(self, data_dir: str = "/tmp/nexus_ai_mining"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        # AI Components
        self.hashrate_predictor = SimpleNeuralNetwork()
        self.rl_agent = MiningRLAgent()
        self.profit_predictor = ProfitabilityPredictor()
        
        # State tracking
        self._gpu_snapshots: Dict[int, deque] = {}  # Per-GPU history
        self._last_optimization: Dict[int, float] = {}
        self._optimization_interval = 60.0  # Seconds between optimizations
        
        # Learning state
        self._learning_enabled = True
        self._online_learning = True
        
        # Performance metrics
        self._total_optimizations = 0
        self._successful_optimizations = 0
        self._hashrate_improvements: List[float] = []
        
        # Lock for thread safety
        self._lock = threading.Lock()
        
        # Load saved models
        self._load_models()
        
        logger.info("AI Mining Optimizer initialized")
    
    def _load_models(self):
        """Load saved AI models."""
        nn_path = self.data_dir / "hashrate_nn.pkl"
        rl_path = self.data_dir / "rl_agent.pkl"
        
        if nn_path.exists():
            if self.hashrate_predictor.load(str(nn_path)):
                logger.info("Loaded hashrate prediction model (%d samples)", 
                           self.hashrate_predictor._trained_samples)
        
        if rl_path.exists():
            if self.rl_agent.load(str(rl_path)):
                logger.info("Loaded RL agent (episodes: %d)", self.rl_agent._episodes)
    
    def save_models(self):
        """Save AI models to disk."""
        nn_path = self.data_dir / "hashrate_nn.pkl"
        rl_path = self.data_dir / "rl_agent.pkl"
        
        self.hashrate_predictor.save(str(nn_path))
        self.rl_agent.save(str(rl_path))
        
        logger.info("AI models saved")
    
    def record_snapshot(self, snapshot: MiningSnapshot):
        """
        Record a mining snapshot for learning.
        
        The AI learns from every snapshot to improve its predictions
        and optimization decisions.
        """
        with self._lock:
            gpu_id = snapshot.gpu_id
            
            if gpu_id not in self._gpu_snapshots:
                self._gpu_snapshots[gpu_id] = deque(maxlen=1000)
            
            self._gpu_snapshots[gpu_id].append(snapshot)
            
            # Record for time series prediction
            if snapshot.coin:
                self.profit_predictor.record_price(snapshot.coin, snapshot.coin_price_usd, snapshot.timestamp)
                self.profit_predictor.record_profit(snapshot.coin, snapshot.estimated_daily_usd, snapshot.timestamp)
            
            if snapshot.algorithm:
                self.profit_predictor.record_difficulty(snapshot.algorithm, snapshot.network_difficulty, snapshot.timestamp)
            
            # Online learning if enabled
            if self._online_learning and len(self._gpu_snapshots[gpu_id]) >= 2:
                self._learn_from_snapshots(gpu_id)
    
    def _learn_from_snapshots(self, gpu_id: int):
        """Learn from recent snapshots."""
        snapshots = list(self._gpu_snapshots[gpu_id])
        if len(snapshots) < 2:
            return
        
        old_snapshot = snapshots[-2]
        new_snapshot = snapshots[-1]
        
        # Train neural network
        x = old_snapshot.to_vector()
        
        # Target: what actually happened
        actual_improvement = 0.0
        if old_snapshot.hashrate > 0:
            actual_improvement = (new_snapshot.hashrate - old_snapshot.hashrate) / old_snapshot.hashrate
        
        y = np.array([
            min(1.0, max(0.0, actual_improvement / 0.5 + 0.5)),  # Normalize to 0-1
            new_snapshot.intensity / 100.0,
            new_snapshot.power_watts / (old_snapshot.power_watts if old_snapshot.power_watts > 0 else 100) * 0.5,
        ], dtype=np.float32)
        
        self.hashrate_predictor.train(x, y)
    
    def optimize(self, snapshot: MiningSnapshot) -> OptimizationResult:
        """
        Get AI optimization recommendation for current state.
        
        This is the main entry point for mining optimization decisions.
        """
        with self._lock:
            self._total_optimizations += 1
            
            # Check if we should optimize
            last_opt = self._last_optimization.get(snapshot.gpu_id, 0)
            if time.time() - last_opt < self._optimization_interval:
                return OptimizationResult(
                    decision=MiningDecision.CONTINUE,
                    confidence=0.5,
                    recommended_settings={},
                    reasoning="Optimization interval not reached",
                    predicted_improvement_percent=0.0,
                )
            
            self._last_optimization[snapshot.gpu_id] = time.time()
            
            # Get neural network prediction
            improvement, opt_intensity, opt_power = self.hashrate_predictor.predict(snapshot)
            
            # Get RL agent decision
            state = self.rl_agent.get_state(snapshot)
            rl_decision = self.rl_agent.choose_action(state, training=self._learning_enabled)
            
            # Combine recommendations
            result = self._combine_recommendations(
                snapshot, improvement, opt_intensity, opt_power, rl_decision
            )
            
            return result
    
    def _combine_recommendations(
        self,
        snapshot: MiningSnapshot,
        nn_improvement: float,
        nn_intensity: float,
        nn_power: float,
        rl_decision: MiningDecision,
    ) -> OptimizationResult:
        """Combine recommendations from different AI components."""
        
        reasoning_parts = []
        recommended_settings = {}
        
        # Temperature check (highest priority)
        if snapshot.temperature_c >= 85:
            return OptimizationResult(
                decision=MiningDecision.COOL_DOWN,
                confidence=0.95,
                recommended_settings={
                    "intensity": max(50, snapshot.intensity - 20),
                    "power_limit_percent": max(60, snapshot.power_watts / 5 - 10),
                },
                reasoning="GPU temperature critical - reducing load to prevent damage",
                predicted_improvement_percent=-10.0,
            )
        
        # High rejection rate check
        total_shares = snapshot.accepted_shares + snapshot.rejected_shares
        if total_shares > 10:
            reject_rate = snapshot.rejected_shares / total_shares
            if reject_rate > 0.05:
                reasoning_parts.append(f"High rejection rate ({reject_rate:.1%})")
                recommended_settings["intensity"] = max(70, snapshot.intensity - 10)
        
        # Neural network recommendations
        if nn_improvement > 5.0:
            reasoning_parts.append(f"NN predicts {nn_improvement:.1f}% improvement potential")
            
            if abs(nn_intensity - snapshot.intensity) > 5:
                recommended_settings["intensity"] = int(nn_intensity)
                reasoning_parts.append(f"Adjust intensity: {snapshot.intensity} -> {int(nn_intensity)}")
        
        # RL decision integration
        if rl_decision == MiningDecision.INCREASE_INTENSITY and snapshot.temperature_c < 75:
            recommended_settings["intensity"] = min(100, snapshot.intensity + 5)
            reasoning_parts.append("RL suggests increasing intensity")
        elif rl_decision == MiningDecision.DECREASE_INTENSITY:
            recommended_settings["intensity"] = max(50, snapshot.intensity - 5)
            reasoning_parts.append("RL suggests decreasing intensity")
        elif rl_decision == MiningDecision.OPTIMIZE_POWER:
            recommended_settings["power_limit_percent"] = int(nn_power)
            reasoning_parts.append("RL suggests power optimization")
        
        # Determine final decision
        if recommended_settings:
            if "intensity" in recommended_settings:
                if recommended_settings["intensity"] > snapshot.intensity:
                    decision = MiningDecision.INCREASE_INTENSITY
                else:
                    decision = MiningDecision.DECREASE_INTENSITY
            elif "power_limit_percent" in recommended_settings:
                decision = MiningDecision.OPTIMIZE_POWER
            else:
                decision = MiningDecision.CONTINUE
        else:
            decision = MiningDecision.CONTINUE
            reasoning_parts.append("Current settings appear optimal")
        
        # Calculate confidence
        confidence = 0.5
        if self.hashrate_predictor._trained_samples > 100:
            confidence += 0.2
        if self.rl_agent._episodes > 50:
            confidence += 0.2
        if len(reasoning_parts) > 2:
            confidence += 0.1
        confidence = min(0.95, confidence)
        
        return OptimizationResult(
            decision=decision,
            confidence=confidence,
            recommended_settings=recommended_settings,
            reasoning="; ".join(reasoning_parts) if reasoning_parts else "Monitoring",
            predicted_improvement_percent=nn_improvement,
        )
    
    def learn_from_result(self, old_snapshot: MiningSnapshot, new_snapshot: MiningSnapshot, action_taken: MiningDecision):
        """
        Learn from the result of an optimization action.
        
        Call this after applying an optimization to update the AI's understanding.
        """
        if not self._learning_enabled:
            return
        
        with self._lock:
            # Update RL agent
            self.rl_agent.step(old_snapshot, new_snapshot, action_taken)
            
            # Track success
            if old_snapshot.hashrate > 0:
                improvement = (new_snapshot.hashrate - old_snapshot.hashrate) / old_snapshot.hashrate
                self._hashrate_improvements.append(improvement)
                
                if improvement > 0:
                    self._successful_optimizations += 1
            
            # Periodic model saving
            if self._total_optimizations % 100 == 0:
                self.save_models()
    
    def get_profit_forecast(self, coins: List[str], hours_ahead: int = 24) -> Dict[str, dict]:
        """Get AI profitability forecast for coins."""
        return self.profit_predictor.get_forecast(coins, hours_ahead)
    
    def get_recommended_coin(self, available_coins: List[str], gpu_name: str) -> Tuple[str, float]:
        """
        Get AI recommendation for which coin to mine.
        
        Returns:
            (recommended_coin, confidence)
        """
        if not available_coins:
            return "", 0.0
        
        forecasts = self.get_profit_forecast(available_coins, hours_ahead=6)
        
        # Score each coin
        best_coin = available_coins[0]
        best_score = 0.0
        
        for coin in available_coins:
            forecast = forecasts.get(coin, {})
            
            score = 0.0
            
            # Price trend
            if forecast.get("price_confidence", 0) > 0.5:
                score += forecast.get("predicted_price_usd", 0) * forecast["price_confidence"]
            
            # Mining timing
            if forecast.get("mining_recommendation") == "mine_now":
                score *= 1.2
            
            if score > best_score:
                best_score = score
                best_coin = coin
        
        confidence = min(0.9, 0.5 + len(self._hashrate_improvements) / 1000)
        
        return best_coin, confidence
    
    def get_stats(self) -> dict:
        """Get AI optimizer statistics."""
        avg_improvement = 0.0
        if self._hashrate_improvements:
            avg_improvement = np.mean(self._hashrate_improvements[-100:]) * 100
        
        success_rate = 0.0
        if self._total_optimizations > 0:
            success_rate = self._successful_optimizations / self._total_optimizations * 100
        
        return {
            "total_optimizations": self._total_optimizations,
            "successful_optimizations": self._successful_optimizations,
            "success_rate_percent": round(success_rate, 2),
            "avg_hashrate_improvement_percent": round(avg_improvement, 3),
            "learning_enabled": self._learning_enabled,
            "online_learning": self._online_learning,
            "nn_trained_samples": self.hashrate_predictor._trained_samples,
            "rl_agent_stats": self.rl_agent.get_stats(),
            "gpu_histories": {
                gpu_id: len(snapshots) 
                for gpu_id, snapshots in self._gpu_snapshots.items()
            },
        }
    
    def enable_learning(self, enabled: bool = True):
        """Enable or disable learning."""
        self._learning_enabled = enabled
        logger.info("AI learning %s", "enabled" if enabled else "disabled")
    
    def set_online_learning(self, enabled: bool = True):
        """Enable or disable online (real-time) learning."""
        self._online_learning = enabled
        logger.info("Online learning %s", "enabled" if enabled else "disabled")


# ══════════════════════════════════════════════════════════════════════════════
# Global instances and helpers
# ══════════════════════════════════════════════════════════════════════════════

_ai_optimizer: Optional[AIMiningOptimizer] = None


def get_ai_mining_optimizer() -> AIMiningOptimizer:
    """Get the singleton AI mining optimizer instance."""
    global _ai_optimizer
    if _ai_optimizer is None:
        _ai_optimizer = AIMiningOptimizer()
    return _ai_optimizer


def create_mining_snapshot(
    gpu_id: int,
    gpu_name: str,
    temperature_c: float,
    power_watts: float,
    fan_speed_percent: float,
    memory_used_mb: int,
    algorithm: str,
    coin: str,
    intensity: int,
    hashrate: float,
    accepted_shares: int,
    rejected_shares: int,
    core_clock_offset: int = 0,
    memory_clock_offset: int = 0,
    power_limit_percent: int = 100,
    coin_price_usd: float = 0.0,
    network_difficulty: float = 1.0,
    estimated_daily_usd: float = 0.0,
    electricity_cost_kwh: float = 0.10,
) -> MiningSnapshot:
    """Helper to create a mining snapshot."""
    return MiningSnapshot(
        timestamp=time.time(),
        gpu_id=gpu_id,
        gpu_name=gpu_name,
        temperature_c=temperature_c,
        power_watts=power_watts,
        fan_speed_percent=fan_speed_percent,
        memory_used_mb=memory_used_mb,
        algorithm=algorithm,
        coin=coin,
        intensity=intensity,
        hashrate=hashrate,
        accepted_shares=accepted_shares,
        rejected_shares=rejected_shares,
        core_clock_offset=core_clock_offset,
        memory_clock_offset=memory_clock_offset,
        power_limit_percent=power_limit_percent,
        coin_price_usd=coin_price_usd,
        network_difficulty=network_difficulty,
        estimated_daily_usd=estimated_daily_usd,
        electricity_cost_kwh=electricity_cost_kwh,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Advanced AI Components (v2 Upgrades)
# ══════════════════════════════════════════════════════════════════════════════

class DeepQNetwork:
    """
    Deep Q-Network (DQN) for advanced mining decision making.
    
    Improvements over basic Q-learning:
    - Non-linear function approximation via neural network
    - Experience replay for stable learning
    - Target network for reduced overestimation
    - Dueling architecture for better action-value estimation
    - Prioritized experience replay for efficient learning
    """
    
    ACTIONS = [
        MiningDecision.CONTINUE,
        MiningDecision.INCREASE_INTENSITY,
        MiningDecision.DECREASE_INTENSITY,
        MiningDecision.OPTIMIZE_POWER,
        MiningDecision.COOL_DOWN,
        MiningDecision.SWITCH_ALGORITHM,
        MiningDecision.SWITCH_COIN,
    ]
    
    # Bounds for numerical stability
    CLIP_MIN = -500
    CLIP_MAX = 500
    
    def __init__(
        self,
        state_size: int = 14,
        hidden_sizes: Tuple[int, ...] = (128, 64, 32),
        learning_rate: float = 0.0005,
        discount_factor: float = 0.99,
        exploration_rate: float = 0.5,
        exploration_decay: float = 0.998,
        min_exploration: float = 0.01,
        target_update_freq: int = 100,
        batch_size: int = 64,
        memory_size: int = 50000,
    ):
        self.state_size = state_size
        self.action_size = len(self.ACTIONS)
        self.hidden_sizes = hidden_sizes
        
        self.lr = learning_rate
        self.gamma = discount_factor
        self.epsilon = exploration_rate
        self.epsilon_decay = exploration_decay
        self.epsilon_min = min_exploration
        self.target_update_freq = target_update_freq
        self.batch_size = batch_size
        
        # Initialize networks (main and target)
        self._init_networks()
        
        # Prioritized experience replay
        self.memory: deque = deque(maxlen=memory_size)
        self.priorities: deque = deque(maxlen=memory_size)
        self.priority_alpha = 0.6  # Priority exponent
        self.priority_beta = 0.4   # Importance sampling weight
        self.priority_beta_increment = 0.001
        
        # Training stats
        self._episodes = 0
        self._steps = 0
        self._total_reward = 0.0
        self._losses: deque = deque(maxlen=1000)
        self._q_values: deque = deque(maxlen=1000)
    
    def _init_networks(self):
        """Initialize main and target networks."""
        # Main network weights
        self.weights = []
        self.biases = []
        
        layer_sizes = [self.state_size] + list(self.hidden_sizes) + [self.action_size]
        
        for i in range(len(layer_sizes) - 1):
            # He initialization for ReLU/Leaky ReLU activations
            # This helps prevent vanishing/exploding gradients by scaling weights
            # based on the number of input connections: std = sqrt(2/n_in)
            w = np.random.randn(layer_sizes[i], layer_sizes[i+1]).astype(np.float32)
            w *= np.sqrt(2.0 / layer_sizes[i])
            b = np.zeros(layer_sizes[i+1], dtype=np.float32)
            self.weights.append(w)
            self.biases.append(b)
        
        # Target network (copy of main)
        self.target_weights = [w.copy() for w in self.weights]
        self.target_biases = [b.copy() for b in self.biases]
    
    def _relu(self, x: np.ndarray) -> np.ndarray:
        return np.maximum(0, x)
    
    def _leaky_relu(self, x: np.ndarray, alpha: float = 0.01) -> np.ndarray:
        return np.where(x > 0, x, alpha * x)
    
    def _softmax(self, x: np.ndarray) -> np.ndarray:
        x_shifted = x - np.max(x, axis=-1, keepdims=True)
        exp_x = np.exp(np.clip(x_shifted, self.CLIP_MIN, self.CLIP_MAX))
        return exp_x / np.sum(exp_x, axis=-1, keepdims=True)
    
    def forward(self, state: np.ndarray, use_target: bool = False) -> np.ndarray:
        """Forward pass through network."""
        weights = self.target_weights if use_target else self.weights
        biases = self.target_biases if use_target else self.biases
        
        x = state
        for i, (w, b) in enumerate(zip(weights, biases)):
            x = x @ w + b
            # Use leaky ReLU for all but last layer
            if i < len(weights) - 1:
                x = self._leaky_relu(x)
        
        return x
    
    def get_q_values(self, state: np.ndarray) -> np.ndarray:
        """Get Q-values for all actions given a state."""
        if state.ndim == 1:
            state = state.reshape(1, -1)
        return self.forward(state)
    
    def choose_action(self, snapshot: MiningSnapshot, training: bool = True) -> MiningDecision:
        """Choose action using epsilon-greedy policy with temperature scaling."""
        state = snapshot.to_vector().reshape(1, -1)
        q_values = self.get_q_values(state)[0]
        
        # Track Q-values for monitoring
        self._q_values.append(float(np.mean(q_values)))
        
        if training and random.random() < self.epsilon:
            # Exploration with softmax-based action selection
            # Higher Q-values still have higher probability
            temperature = 1.0 + self.epsilon  # Temperature decreases as epsilon decreases
            probs = self._softmax(q_values / temperature)
            action_idx = np.random.choice(self.action_size, p=probs)
        else:
            # Exploitation: best Q-value action
            action_idx = int(np.argmax(q_values))
        
        return self.ACTIONS[action_idx]
    
    def remember(
        self, 
        state: np.ndarray, 
        action: int, 
        reward: float, 
        next_state: np.ndarray, 
        done: bool
    ):
        """Store experience with priority."""
        # Calculate initial priority (use maximum priority for new experiences)
        max_priority = max(self.priorities) if self.priorities else 1.0
        
        self.memory.append((state, action, reward, next_state, done))
        self.priorities.append(max_priority)
        
        self._total_reward += reward
    
    def _sample_batch(self) -> Tuple[np.ndarray, ...]:
        """Sample batch with prioritized experience replay."""
        if len(self.memory) < self.batch_size:
            return None
        
        # Calculate sampling probabilities
        priorities = np.array(self.priorities, dtype=np.float32)
        priorities = priorities ** self.priority_alpha
        probs = priorities / priorities.sum()
        
        # Sample indices
        indices = np.random.choice(len(self.memory), self.batch_size, p=probs, replace=False)
        
        # Calculate importance sampling weights
        n = len(self.memory)
        weights = (n * probs[indices]) ** (-self.priority_beta)
        weights /= weights.max()  # Normalize
        
        # Increase beta for more uniform sampling over time
        self.priority_beta = min(1.0, self.priority_beta + self.priority_beta_increment)
        
        batch = [self.memory[i] for i in indices]
        states = np.array([b[0] for b in batch], dtype=np.float32)
        actions = np.array([b[1] for b in batch], dtype=np.int32)
        rewards = np.array([b[2] for b in batch], dtype=np.float32)
        next_states = np.array([b[3] for b in batch], dtype=np.float32)
        dones = np.array([b[4] for b in batch], dtype=np.float32)
        
        return states, actions, rewards, next_states, dones, indices, weights
    
    def train(self):
        """Train network with prioritized experience replay."""
        batch = self._sample_batch()
        if batch is None:
            return
        
        states, actions, rewards, next_states, dones, indices, is_weights = batch
        
        # Double DQN: use main network to select action, target to evaluate
        next_q_main = self.forward(next_states, use_target=False)
        next_actions = np.argmax(next_q_main, axis=1)
        next_q_target = self.forward(next_states, use_target=True)
        next_q_values = next_q_target[np.arange(self.batch_size), next_actions]
        
        # Calculate targets
        targets = rewards + self.gamma * next_q_values * (1 - dones)
        
        # Get current Q-values
        current_q = self.forward(states)
        current_q_actions = current_q[np.arange(self.batch_size), actions]
        
        # Calculate TD errors for priority updates
        td_errors = np.abs(targets - current_q_actions)
        
        # Update priorities
        for i, idx in enumerate(indices):
            self.priorities[idx] = td_errors[i] + 1e-6  # Small constant to avoid zero priority
        
        # Calculate loss (weighted by importance sampling)
        loss = np.mean(is_weights * (targets - current_q_actions) ** 2)
        self._losses.append(float(loss))
        
        # Gradient descent (simplified backpropagation)
        # Target gradient
        target_grad = 2 * (current_q_actions - targets) * is_weights / self.batch_size
        
        # Backpropagate through network
        grad = np.zeros_like(current_q)
        grad[np.arange(self.batch_size), actions] = target_grad
        
        # Update weights layer by layer
        activations = [states]
        x = states
        for i, (w, b) in enumerate(zip(self.weights, self.biases)):
            x = x @ w + b
            if i < len(self.weights) - 1:
                x = self._leaky_relu(x)
            activations.append(x)
        
        for i in range(len(self.weights) - 1, -1, -1):
            if i < len(self.weights) - 1:
                # Leaky ReLU derivative
                grad = grad * np.where(activations[i + 1] > 0, 1.0, 0.01)
            
            dw = activations[i].T @ grad
            db = np.sum(grad, axis=0)
            
            # Gradient clipping
            dw = np.clip(dw, -1.0, 1.0)
            db = np.clip(db, -1.0, 1.0)
            
            # Update weights
            self.weights[i] -= self.lr * dw
            self.biases[i] -= self.lr * db
            
            if i > 0:
                grad = grad @ self.weights[i].T
        
        self._steps += 1
        
        # Update target network periodically
        if self._steps % self.target_update_freq == 0:
            self._update_target_network()
        
        # Decay exploration rate
        if self.epsilon > self.epsilon_min:
            self.epsilon *= self.epsilon_decay
    
    def _update_target_network(self):
        """Soft update target network weights."""
        tau = 0.01  # Soft update coefficient
        for i in range(len(self.weights)):
            self.target_weights[i] = tau * self.weights[i] + (1 - tau) * self.target_weights[i]
            self.target_biases[i] = tau * self.biases[i] + (1 - tau) * self.target_biases[i]
    
    def step(self, old_snapshot: MiningSnapshot, new_snapshot: MiningSnapshot, action: MiningDecision):
        """Complete one step of learning."""
        state = old_snapshot.to_vector()
        next_state = new_snapshot.to_vector()
        action_idx = self.ACTIONS.index(action)
        reward = self._calculate_reward(old_snapshot, new_snapshot, action)
        
        self.remember(state, action_idx, reward, next_state, done=False)
        self.train()
        
        self._episodes += 1
    
    def _calculate_reward(
        self, 
        old: MiningSnapshot, 
        new: MiningSnapshot, 
        action: MiningDecision
    ) -> float:
        """
        Advanced reward function with multi-objective optimization.
        
        Rewards efficiency (hashrate/power), stability, and profitability.
        """
        reward = 0.0
        
        # Hashrate improvement (primary objective)
        if old.hashrate > 0:
            hashrate_change = (new.hashrate - old.hashrate) / old.hashrate
            reward += hashrate_change * 15.0
        
        # Power efficiency (hashrate per watt)
        old_efficiency = old.hashrate / max(1, old.power_watts)
        new_efficiency = new.hashrate / max(1, new.power_watts)
        if old_efficiency > 0:
            efficiency_change = (new_efficiency - old_efficiency) / old_efficiency
            reward += efficiency_change * 10.0
        
        # Profit improvement (most important)
        if old.estimated_daily_usd > 0:
            profit_change = (new.estimated_daily_usd - old.estimated_daily_usd) / old.estimated_daily_usd
            reward += profit_change * 20.0
        elif new.estimated_daily_usd > 0:
            reward += new.estimated_daily_usd * 2.0
        
        # Temperature management
        if new.temperature_c < old.temperature_c:
            reward += 0.5  # Cooling is good
        if new.temperature_c > 85:
            reward -= 5.0  # Overheating penalty
        elif new.temperature_c > 80:
            reward -= 2.0  # Warning zone
        elif new.temperature_c < 70:
            reward += 0.3  # Good temperature bonus
        
        # Share acceptance rate
        old_rate = old.accepted_shares / max(1, old.accepted_shares + old.rejected_shares)
        new_rate = new.accepted_shares / max(1, new.accepted_shares + new.rejected_shares)
        reward += (new_rate - old_rate) * 5.0
        
        # Stability bonus (penalize excessive changes)
        if action == MiningDecision.CONTINUE and new.hashrate >= old.hashrate * 0.99:
            reward += 0.5  # Reward stable operation
        
        # Action-specific adjustments
        if action == MiningDecision.COOL_DOWN and new.temperature_c < old.temperature_c:
            reward += 2.0  # Successful cooldown
        
        return reward
    
    def get_stats(self) -> dict:
        """Get DQN statistics."""
        avg_loss = np.mean(self._losses) if self._losses else 0.0
        avg_q = np.mean(self._q_values) if self._q_values else 0.0
        
        return {
            "episodes": self._episodes,
            "steps": self._steps,
            "exploration_rate": round(self.epsilon, 4),
            "total_reward": round(self._total_reward, 2),
            "memory_size": len(self.memory),
            "avg_loss": round(avg_loss, 6),
            "avg_q_value": round(avg_q, 4),
            "priority_beta": round(self.priority_beta, 4),
        }
    
    def save(self, path: str):
        """Save DQN state."""
        with open(path, 'wb') as f:
            pickle.dump({
                'weights': self.weights,
                'biases': self.biases,
                'target_weights': self.target_weights,
                'target_biases': self.target_biases,
                'epsilon': self.epsilon,
                'priority_beta': self.priority_beta,
                'episodes': self._episodes,
                'steps': self._steps,
                'total_reward': self._total_reward,
            }, f)
    
    def load(self, path: str) -> bool:
        """Load DQN state."""
        try:
            with open(path, 'rb') as f:
                data = pickle.load(f)
                self.weights = data['weights']
                self.biases = data['biases']
                self.target_weights = data['target_weights']
                self.target_biases = data['target_biases']
                self.epsilon = data.get('epsilon', self.epsilon)
                self.priority_beta = data.get('priority_beta', self.priority_beta)
                self._episodes = data.get('episodes', 0)
                self._steps = data.get('steps', 0)
                self._total_reward = data.get('total_reward', 0)
            return True
        except Exception as e:
            logger.debug("Failed to load DQN: %s", e)
            return False


class TransformerMiningPredictor:
    """
    Transformer-based sequence model for mining performance prediction.
    
    Uses self-attention to capture temporal patterns in mining data,
    predicting future hashrate, efficiency, and optimal settings.
    
    This is a simplified implementation that doesn't require PyTorch/TensorFlow.
    """
    
    def __init__(
        self,
        input_dim: int = 14,
        model_dim: int = 64,
        num_heads: int = 4,
        num_layers: int = 2,
        sequence_length: int = 10,
        output_dim: int = 5,  # [hashrate_pred, efficiency_pred, opt_intensity, opt_power, confidence]
    ):
        self.input_dim = input_dim
        self.model_dim = model_dim
        self.num_heads = num_heads
        self.num_layers = num_layers
        self.seq_len = sequence_length
        self.output_dim = output_dim
        
        self.head_dim = model_dim // num_heads
        
        # Input projection
        self.W_input = np.random.randn(input_dim, model_dim).astype(np.float32) * 0.1
        
        # Positional encoding
        self.positional_encoding = self._create_positional_encoding()
        
        # Multi-head attention weights (per layer)
        self.attention_layers = []
        for _ in range(num_layers):
            layer = {
                'W_q': np.random.randn(model_dim, model_dim).astype(np.float32) * 0.1,
                'W_k': np.random.randn(model_dim, model_dim).astype(np.float32) * 0.1,
                'W_v': np.random.randn(model_dim, model_dim).astype(np.float32) * 0.1,
                'W_o': np.random.randn(model_dim, model_dim).astype(np.float32) * 0.1,
                'W_ff1': np.random.randn(model_dim, model_dim * 4).astype(np.float32) * 0.1,
                'W_ff2': np.random.randn(model_dim * 4, model_dim).astype(np.float32) * 0.1,
                'layer_norm1_gamma': np.ones(model_dim, dtype=np.float32),
                'layer_norm1_beta': np.zeros(model_dim, dtype=np.float32),
                'layer_norm2_gamma': np.ones(model_dim, dtype=np.float32),
                'layer_norm2_beta': np.zeros(model_dim, dtype=np.float32),
            }
            self.attention_layers.append(layer)
        
        # Output projection
        self.W_output = np.random.randn(model_dim, output_dim).astype(np.float32) * 0.1
        self.b_output = np.zeros(output_dim, dtype=np.float32)
        
        # Training stats
        self._trained_sequences = 0
        self.lr = 0.0001
    
    def _create_positional_encoding(self) -> np.ndarray:
        """Create sinusoidal positional encoding."""
        pe = np.zeros((self.seq_len, self.model_dim), dtype=np.float32)
        position = np.arange(self.seq_len)[:, np.newaxis]
        div_term = np.exp(np.arange(0, self.model_dim, 2) * (-np.log(10000.0) / self.model_dim))
        
        pe[:, 0::2] = np.sin(position * div_term)
        pe[:, 1::2] = np.cos(position * div_term)
        
        return pe
    
    def _layer_norm(self, x: np.ndarray, gamma: np.ndarray, beta: np.ndarray, eps: float = 1e-6) -> np.ndarray:
        """Layer normalization."""
        mean = np.mean(x, axis=-1, keepdims=True)
        var = np.var(x, axis=-1, keepdims=True)
        return gamma * (x - mean) / np.sqrt(var + eps) + beta
    
    def _scaled_dot_product_attention(
        self, 
        Q: np.ndarray, 
        K: np.ndarray, 
        V: np.ndarray,
        mask: Optional[np.ndarray] = None
    ) -> np.ndarray:
        """Compute scaled dot-product attention."""
        d_k = Q.shape[-1]
        scores = Q @ K.transpose(0, 2, 1) / np.sqrt(d_k)
        
        if mask is not None:
            scores = np.where(mask, scores, -1e9)
        
        # Stable softmax
        scores_shifted = scores - np.max(scores, axis=-1, keepdims=True)
        weights = np.exp(scores_shifted)
        weights = weights / np.sum(weights, axis=-1, keepdims=True)
        
        return weights @ V
    
    def _multi_head_attention(self, x: np.ndarray, layer: dict) -> np.ndarray:
        """Multi-head self-attention."""
        batch_size, seq_len, _ = x.shape
        
        Q = x @ layer['W_q']
        K = x @ layer['W_k']
        V = x @ layer['W_v']
        
        # Reshape for multi-head
        Q = Q.reshape(batch_size, seq_len, self.num_heads, self.head_dim).transpose(0, 2, 1, 3)
        K = K.reshape(batch_size, seq_len, self.num_heads, self.head_dim).transpose(0, 2, 1, 3)
        V = V.reshape(batch_size, seq_len, self.num_heads, self.head_dim).transpose(0, 2, 1, 3)
        
        # Attention per head
        attn_outputs = []
        for h in range(self.num_heads):
            attn_out = self._scaled_dot_product_attention(Q[:, h], K[:, h], V[:, h])
            attn_outputs.append(attn_out)
        
        # Concatenate heads
        concat = np.concatenate(attn_outputs, axis=-1)
        
        # Output projection
        return concat @ layer['W_o']
    
    def _feed_forward(self, x: np.ndarray, layer: dict) -> np.ndarray:
        """Position-wise feed-forward network."""
        h = np.maximum(0, x @ layer['W_ff1'])  # ReLU
        return h @ layer['W_ff2']
    
    def forward(self, sequence: np.ndarray) -> np.ndarray:
        """
        Forward pass through transformer.
        
        Args:
            sequence: Shape (batch_size, seq_len, input_dim) or (seq_len, input_dim)
        
        Returns:
            predictions: Shape (batch_size, output_dim) or (output_dim,)
        """
        single_input = sequence.ndim == 2
        if single_input:
            sequence = sequence[np.newaxis, ...]
        
        batch_size, seq_len, _ = sequence.shape
        
        # Pad or truncate sequence
        if seq_len < self.seq_len:
            padding = np.zeros((batch_size, self.seq_len - seq_len, self.input_dim), dtype=np.float32)
            sequence = np.concatenate([padding, sequence], axis=1)
        elif seq_len > self.seq_len:
            sequence = sequence[:, -self.seq_len:, :]
        
        # Input projection + positional encoding
        x = sequence @ self.W_input + self.positional_encoding
        
        # Transformer layers
        for layer in self.attention_layers:
            # Self-attention with residual
            attn_out = self._multi_head_attention(x, layer)
            x = self._layer_norm(x + attn_out, layer['layer_norm1_gamma'], layer['layer_norm1_beta'])
            
            # Feed-forward with residual
            ff_out = self._feed_forward(x, layer)
            x = self._layer_norm(x + ff_out, layer['layer_norm2_gamma'], layer['layer_norm2_beta'])
        
        # Use last position for prediction
        last_hidden = x[:, -1, :]
        
        # Output projection with sigmoid for bounded outputs
        output = last_hidden @ self.W_output + self.b_output
        output = 1 / (1 + np.exp(-np.clip(output, -10, 10)))  # Sigmoid
        
        if single_input:
            return output[0]
        return output
    
    def predict(self, snapshots: List[MiningSnapshot]) -> Dict[str, float]:
        """
        Predict optimal settings from a sequence of snapshots.
        
        Returns dictionary with:
        - predicted_hashrate_change: Expected % change in hashrate
        - predicted_efficiency_change: Expected % change in efficiency
        - optimal_intensity: Recommended intensity (0-100)
        - optimal_power_limit: Recommended power limit (60-100)
        - confidence: Prediction confidence (0-1)
        """
        if len(snapshots) < 2:
            return {
                "predicted_hashrate_change": 0.0,
                "predicted_efficiency_change": 0.0,
                "optimal_intensity": 75,
                "optimal_power_limit": 85,
                "confidence": 0.1,
            }
        
        # Convert snapshots to sequence
        sequence = np.array([s.to_vector() for s in snapshots], dtype=np.float32)
        
        # Get predictions
        output = self.forward(sequence)
        
        return {
            "predicted_hashrate_change": float((output[0] - 0.5) * 50),  # -25% to +25%
            "predicted_efficiency_change": float((output[1] - 0.5) * 40),  # -20% to +20%
            "optimal_intensity": float(50 + output[2] * 50),  # 50-100
            "optimal_power_limit": float(60 + output[3] * 40),  # 60-100
            "confidence": float(output[4]),
        }
    
    def train_on_sequence(self, snapshots: List[MiningSnapshot], actual_outcome: Dict[str, float]):
        """Train on a sequence with known outcome (simplified gradient descent)."""
        if len(snapshots) < 2:
            return
        
        sequence = np.array([s.to_vector() for s in snapshots], dtype=np.float32)
        output = self.forward(sequence)
        
        # Create target from actual outcome
        target = np.array([
            (actual_outcome.get("hashrate_change", 0) / 50) + 0.5,
            (actual_outcome.get("efficiency_change", 0) / 40) + 0.5,
            (actual_outcome.get("intensity", 75) - 50) / 50,
            (actual_outcome.get("power_limit", 85) - 60) / 40,
            actual_outcome.get("success", 0.5),
        ], dtype=np.float32)
        target = np.clip(target, 0, 1)
        
        # Simple gradient descent on output layer
        error = output - target
        grad = error * output * (1 - output)  # Sigmoid derivative
        
        # Update output weights (simplified)
        self.W_output -= self.lr * np.outer(np.ones(self.model_dim), grad)
        self.b_output -= self.lr * grad
        
        self._trained_sequences += 1
    
    def get_stats(self) -> dict:
        return {
            "trained_sequences": self._trained_sequences,
            "model_dim": self.model_dim,
            "num_heads": self.num_heads,
            "num_layers": self.num_layers,
            "sequence_length": self.seq_len,
        }
    
    def save(self, path: str):
        """Save transformer state."""
        with open(path, 'wb') as f:
            pickle.dump({
                'W_input': self.W_input,
                'attention_layers': self.attention_layers,
                'W_output': self.W_output,
                'b_output': self.b_output,
                'trained_sequences': self._trained_sequences,
            }, f)
    
    def load(self, path: str) -> bool:
        """Load transformer state."""
        try:
            with open(path, 'rb') as f:
                data = pickle.load(f)
                self.W_input = data['W_input']
                self.attention_layers = data['attention_layers']
                self.W_output = data['W_output']
                self.b_output = data['b_output']
                self._trained_sequences = data.get('trained_sequences', 0)
            return True
        except Exception as e:
            logger.debug("Failed to load transformer: %s", e)
            return False


class EnsembleMiningOptimizer:
    """
    Ensemble model that combines multiple AI techniques for robust optimization.
    
    Combines:
    - Neural network (hashrate prediction)
    - DQN (decision making)
    - Transformer (sequence modeling)
    - Basic RL (baseline)
    
    Uses weighted voting based on recent performance to select best predictions.
    """
    
    def __init__(self):
        self.nn = SimpleNeuralNetwork()
        self.dqn = DeepQNetwork()
        self.transformer = TransformerMiningPredictor()
        self.basic_rl = MiningRLAgent()
        
        # Model weights (adaptive based on performance)
        self.model_weights = {
            "nn": 0.25,
            "dqn": 0.35,
            "transformer": 0.25,
            "basic_rl": 0.15,
        }
        
        # Performance tracking
        self._model_successes: Dict[str, int] = {k: 0 for k in self.model_weights}
        self._model_total: Dict[str, int] = {k: 0 for k in self.model_weights}
        self._recent_predictions: deque = deque(maxlen=100)
    
    def get_ensemble_decision(
        self, 
        snapshot: MiningSnapshot, 
        snapshot_history: List[MiningSnapshot]
    ) -> Tuple[MiningDecision, float, Dict[str, Any]]:
        """
        Get ensemble decision by combining all models.
        
        Returns:
            (decision, confidence, details)
        """
        decisions = {}
        confidences = {}
        details = {}
        
        # Get NN prediction
        nn_improvement, nn_intensity, nn_power = self.nn.predict(snapshot)
        decisions["nn"] = MiningDecision.CONTINUE
        if nn_improvement > 10:
            if nn_intensity > snapshot.intensity + 5:
                decisions["nn"] = MiningDecision.INCREASE_INTENSITY
            elif nn_intensity < snapshot.intensity - 5:
                decisions["nn"] = MiningDecision.DECREASE_INTENSITY
        confidences["nn"] = min(0.9, 0.5 + self.nn._trained_samples / 500)
        details["nn"] = {"improvement": nn_improvement, "intensity": nn_intensity, "power": nn_power}
        
        # Get DQN decision
        decisions["dqn"] = self.dqn.choose_action(snapshot, training=False)
        confidences["dqn"] = 1.0 - self.dqn.epsilon
        details["dqn"] = self.dqn.get_stats()
        
        # Get Transformer prediction
        if len(snapshot_history) >= 3:
            trans_pred = self.transformer.predict(snapshot_history)
            decisions["transformer"] = MiningDecision.CONTINUE
            if trans_pred["predicted_hashrate_change"] > 5 and trans_pred["confidence"] > 0.6:
                if trans_pred["optimal_intensity"] > snapshot.intensity + 5:
                    decisions["transformer"] = MiningDecision.INCREASE_INTENSITY
                elif trans_pred["optimal_intensity"] < snapshot.intensity - 5:
                    decisions["transformer"] = MiningDecision.DECREASE_INTENSITY
            confidences["transformer"] = trans_pred["confidence"]
            details["transformer"] = trans_pred
        else:
            decisions["transformer"] = MiningDecision.CONTINUE
            confidences["transformer"] = 0.3
            details["transformer"] = {"note": "insufficient_history"}
        
        # Get basic RL decision
        decisions["basic_rl"] = self.basic_rl.choose_action(
            self.basic_rl.get_state(snapshot), training=False
        )
        confidences["basic_rl"] = 1.0 - self.basic_rl.epsilon
        details["basic_rl"] = self.basic_rl.get_stats()
        
        # Weighted voting
        vote_scores: Dict[MiningDecision, float] = {}
        for model_name, decision in decisions.items():
            weight = self.model_weights[model_name]
            confidence = confidences[model_name]
            score = weight * confidence
            
            if decision not in vote_scores:
                vote_scores[decision] = 0.0
            vote_scores[decision] += score
        
        # Select best decision
        best_decision = max(vote_scores, key=vote_scores.get)
        total_score = sum(vote_scores.values())
        ensemble_confidence = vote_scores[best_decision] / total_score if total_score > 0 else 0.5
        
        # Record prediction for later evaluation
        self._recent_predictions.append({
            "timestamp": time.time(),
            "decisions": decisions.copy(),
            "final_decision": best_decision,
            "confidence": ensemble_confidence,
            "snapshot_hashrate": snapshot.hashrate,
        })
        
        return best_decision, ensemble_confidence, {
            "model_decisions": {k: v.value for k, v in decisions.items()},
            "model_confidences": confidences,
            "vote_scores": {k.value: v for k, v in vote_scores.items()},
            "model_weights": self.model_weights.copy(),
            "model_details": details,
        }
    
    def learn_from_outcome(
        self, 
        old_snapshot: MiningSnapshot, 
        new_snapshot: MiningSnapshot, 
        action_taken: MiningDecision,
        snapshot_history: List[MiningSnapshot]
    ):
        """Update all models and adjust weights based on outcome."""
        # Determine if outcome was successful
        success = new_snapshot.hashrate >= old_snapshot.hashrate * 0.99
        if new_snapshot.estimated_daily_usd > old_snapshot.estimated_daily_usd:
            success = True
        
        # Update each model
        self.nn.train(old_snapshot.to_vector(), np.array([
            min(1.0, max(0.0, (new_snapshot.hashrate / max(1, old_snapshot.hashrate) - 0.5))),
            new_snapshot.intensity / 100.0,
            new_snapshot.power_watts / max(100, old_snapshot.power_watts) * 0.5,
        ], dtype=np.float32))
        
        self.dqn.step(old_snapshot, new_snapshot, action_taken)
        self.basic_rl.step(old_snapshot, new_snapshot, action_taken)
        
        if len(snapshot_history) >= 3:
            actual = {
                "hashrate_change": (new_snapshot.hashrate - old_snapshot.hashrate) / max(1, old_snapshot.hashrate) * 100,
                "efficiency_change": 0,
                "intensity": new_snapshot.intensity,
                "power_limit": new_snapshot.power_limit_percent,
                "success": 1.0 if success else 0.0,
            }
            self.transformer.train_on_sequence(snapshot_history, actual)
        
        # Update model weights based on which predictions were correct
        if self._recent_predictions:
            last_pred = self._recent_predictions[-1]
            for model_name, decision in last_pred.get("decisions", {}).items():
                self._model_total[model_name] += 1
                
                # Check if this model's prediction matched the successful outcome
                if decision == action_taken and success:
                    self._model_successes[model_name] += 1
                elif decision != action_taken and not success:
                    self._model_successes[model_name] += 1
        
        # Periodically rebalance weights
        if sum(self._model_total.values()) > 0 and sum(self._model_total.values()) % 20 == 0:
            self._rebalance_weights()
    
    def _rebalance_weights(self):
        """Rebalance model weights based on recent performance."""
        new_weights = {}
        total_score = 0.0
        
        for model_name in self.model_weights:
            total = self._model_total.get(model_name, 0)
            successes = self._model_successes.get(model_name, 0)
            
            if total > 10:
                accuracy = successes / total
            else:
                accuracy = 0.5  # Default until we have data
            
            # Weight = accuracy with minimum floor
            new_weights[model_name] = max(0.1, accuracy)
            total_score += new_weights[model_name]
        
        # Normalize
        for model_name in new_weights:
            self.model_weights[model_name] = new_weights[model_name] / total_score
        
        logger.info("Ensemble weights rebalanced: %s", self.model_weights)
    
    def get_stats(self) -> dict:
        accuracies = {}
        for model in self.model_weights:
            total = self._model_total.get(model, 0)
            successes = self._model_successes.get(model, 0)
            accuracies[model] = round(successes / total * 100, 2) if total > 0 else 0.0
        
        return {
            "model_weights": self.model_weights.copy(),
            "model_accuracies_percent": accuracies,
            "total_predictions": sum(self._model_total.values()),
            "nn_stats": {"trained_samples": self.nn._trained_samples},
            "dqn_stats": self.dqn.get_stats(),
            "transformer_stats": self.transformer.get_stats(),
            "basic_rl_stats": self.basic_rl.get_stats(),
        }
    
    def save(self, base_path: str):
        """Save all models."""
        path = Path(base_path)
        self.nn.save(str(path / "ensemble_nn.pkl"))
        self.dqn.save(str(path / "ensemble_dqn.pkl"))
        self.transformer.save(str(path / "ensemble_transformer.pkl"))
        self.basic_rl.save(str(path / "ensemble_basic_rl.pkl"))
        
        with open(str(path / "ensemble_meta.pkl"), 'wb') as f:
            pickle.dump({
                'model_weights': self.model_weights,
                'model_successes': dict(self._model_successes),
                'model_total': dict(self._model_total),
            }, f)
    
    def load(self, base_path: str):
        """Load all models."""
        path = Path(base_path)
        self.nn.load(str(path / "ensemble_nn.pkl"))
        self.dqn.load(str(path / "ensemble_dqn.pkl"))
        self.transformer.load(str(path / "ensemble_transformer.pkl"))
        self.basic_rl.load(str(path / "ensemble_basic_rl.pkl"))
        
        try:
            with open(str(path / "ensemble_meta.pkl"), 'rb') as f:
                data = pickle.load(f)
                self.model_weights = data.get('model_weights', self.model_weights)
                self._model_successes = data.get('model_successes', self._model_successes)
                self._model_total = data.get('model_total', self._model_total)
        except Exception as e:
            logger.debug("Failed to load ensemble meta: %s", e)


class AnomalyDetector:
    """
    Advanced anomaly detection for mining hardware health monitoring.
    
    Uses statistical methods and learned patterns to detect:
    - Temperature anomalies
    - Hashrate drops
    - Power consumption spikes
    - Share rejection patterns
    - Hardware degradation
    """
    
    # Window sizes for efficiency trend analysis
    RECENT_WINDOW_SIZE = 10
    HISTORICAL_WINDOW_SIZE = 40
    MIN_SAMPLES_FOR_TREND = 50
    EFFICIENCY_DEGRADATION_THRESHOLD = 0.85  # 15% degradation triggers alert
    
    def __init__(self, window_size: int = 100):
        self.window_size = window_size
        
        # Rolling statistics per GPU
        self._stats: Dict[int, Dict[str, deque]] = {}
        
        # Anomaly thresholds (standard deviations)
        self.temp_threshold = 3.0
        self.hashrate_threshold = 2.5
        self.power_threshold = 2.0
        self.rejection_threshold = 3.0
        
        # Anomaly history
        self._anomalies: deque = deque(maxlen=1000)
        self._anomaly_counts: Dict[str, int] = {}
    
    def _get_gpu_stats(self, gpu_id: int) -> Dict[str, deque]:
        if gpu_id not in self._stats:
            self._stats[gpu_id] = {
                "temperature": deque(maxlen=self.window_size),
                "hashrate": deque(maxlen=self.window_size),
                "power": deque(maxlen=self.window_size),
                "rejection_rate": deque(maxlen=self.window_size),
                "efficiency": deque(maxlen=self.window_size),
            }
        return self._stats[gpu_id]
    
    def record(self, snapshot: MiningSnapshot):
        """Record a snapshot and check for anomalies."""
        stats = self._get_gpu_stats(snapshot.gpu_id)
        
        # Record values
        stats["temperature"].append(snapshot.temperature_c)
        stats["hashrate"].append(snapshot.hashrate)
        stats["power"].append(snapshot.power_watts)
        
        total_shares = snapshot.accepted_shares + snapshot.rejected_shares
        rejection_rate = snapshot.rejected_shares / max(1, total_shares)
        stats["rejection_rate"].append(rejection_rate)
        
        efficiency = snapshot.hashrate / max(1, snapshot.power_watts)
        stats["efficiency"].append(efficiency)
    
    def check_anomalies(self, snapshot: MiningSnapshot) -> List[Dict[str, Any]]:
        """Check for anomalies in the latest snapshot."""
        anomalies = []
        stats = self._get_gpu_stats(snapshot.gpu_id)
        
        # Need enough data for statistical analysis
        if len(stats["temperature"]) < 10:
            return anomalies
        
        # Temperature anomaly
        temp_mean = np.mean(stats["temperature"])
        temp_std = np.std(stats["temperature"]) + 1e-6
        temp_z = (snapshot.temperature_c - temp_mean) / temp_std
        
        if abs(temp_z) > self.temp_threshold:
            anomaly = {
                "type": "temperature",
                "severity": "high" if temp_z > 4 else "medium",
                "value": snapshot.temperature_c,
                "expected_range": (temp_mean - 2*temp_std, temp_mean + 2*temp_std),
                "z_score": temp_z,
                "message": f"Temperature anomaly: {snapshot.temperature_c:.1f}°C (expected {temp_mean:.1f}±{2*temp_std:.1f}°C)",
            }
            anomalies.append(anomaly)
            self._record_anomaly(anomaly)
        
        # Hashrate anomaly (only care about drops)
        if len(stats["hashrate"]) >= 10 and any(h > 0 for h in stats["hashrate"]):
            hr_mean = np.mean([h for h in stats["hashrate"] if h > 0])
            hr_std = np.std([h for h in stats["hashrate"] if h > 0]) + 1e-6
            hr_z = (snapshot.hashrate - hr_mean) / hr_std
            
            if hr_z < -self.hashrate_threshold:
                anomaly = {
                    "type": "hashrate_drop",
                    "severity": "high" if hr_z < -4 else "medium",
                    "value": snapshot.hashrate,
                    "expected_range": (hr_mean - 2*hr_std, hr_mean + 2*hr_std),
                    "z_score": hr_z,
                    "message": f"Hashrate drop: {snapshot.hashrate:.2f} H/s (expected {hr_mean:.2f}±{2*hr_std:.2f} H/s)",
                }
                anomalies.append(anomaly)
                self._record_anomaly(anomaly)
        
        # Power anomaly
        power_mean = np.mean(stats["power"])
        power_std = np.std(stats["power"]) + 1e-6
        power_z = (snapshot.power_watts - power_mean) / power_std
        
        if abs(power_z) > self.power_threshold:
            anomaly = {
                "type": "power",
                "severity": "medium",
                "value": snapshot.power_watts,
                "expected_range": (power_mean - 2*power_std, power_mean + 2*power_std),
                "z_score": power_z,
                "message": f"Power anomaly: {snapshot.power_watts:.1f}W (expected {power_mean:.1f}±{2*power_std:.1f}W)",
            }
            anomalies.append(anomaly)
            self._record_anomaly(anomaly)
        
        # Rejection rate anomaly
        total_shares = snapshot.accepted_shares + snapshot.rejected_shares
        if total_shares > 0:
            rejection_rate = snapshot.rejected_shares / total_shares
            rej_mean = np.mean(stats["rejection_rate"])
            rej_std = np.std(stats["rejection_rate"]) + 1e-6
            rej_z = (rejection_rate - rej_mean) / rej_std
            
            if rej_z > self.rejection_threshold or rejection_rate > 0.1:
                anomaly = {
                    "type": "rejection_rate",
                    "severity": "high" if rejection_rate > 0.2 else "medium",
                    "value": rejection_rate,
                    "z_score": rej_z,
                    "message": f"High share rejection: {rejection_rate*100:.1f}% (normal: {rej_mean*100:.1f}%)",
                }
                anomalies.append(anomaly)
                self._record_anomaly(anomaly)
        
        # Efficiency degradation (long-term trend)
        if len(stats["efficiency"]) >= self.MIN_SAMPLES_FOR_TREND:
            recent_eff = np.mean(list(stats["efficiency"])[-self.RECENT_WINDOW_SIZE:])
            historical_eff = np.mean(list(stats["efficiency"])[:self.HISTORICAL_WINDOW_SIZE])
            if historical_eff > 0 and recent_eff < historical_eff * self.EFFICIENCY_DEGRADATION_THRESHOLD:
                anomaly = {
                    "type": "efficiency_degradation",
                    "severity": "medium",
                    "value": recent_eff,
                    "historical_value": historical_eff,
                    "degradation_percent": (1 - recent_eff/historical_eff) * 100,
                    "message": f"Efficiency degraded by {(1-recent_eff/historical_eff)*100:.1f}% - possible hardware issue",
                }
                anomalies.append(anomaly)
                self._record_anomaly(anomaly)
        
        return anomalies
    
    def _record_anomaly(self, anomaly: Dict[str, Any]):
        """Record anomaly for history tracking."""
        anomaly["timestamp"] = time.time()
        self._anomalies.append(anomaly)
        
        atype = anomaly["type"]
        self._anomaly_counts[atype] = self._anomaly_counts.get(atype, 0) + 1
    
    def get_health_score(self, gpu_id: int) -> float:
        """
        Get hardware health score (0-100).
        
        100 = Perfect health
        0 = Critical issues
        """
        if gpu_id not in self._stats:
            return 100.0
        
        score = 100.0
        
        # Deduct for recent anomalies
        recent_time = time.time() - 3600  # Last hour
        recent_anomalies = [a for a in self._anomalies if a["timestamp"] > recent_time]
        
        for anomaly in recent_anomalies:
            if anomaly["severity"] == "high":
                score -= 15
            else:
                score -= 5
        
        # Deduct for temperature trends
        stats = self._stats[gpu_id]
        if len(stats["temperature"]) >= 10:
            avg_temp = np.mean(stats["temperature"])
            if avg_temp > 85:
                score -= 20
            elif avg_temp > 80:
                score -= 10
            elif avg_temp > 75:
                score -= 5
        
        # Deduct for efficiency trends
        if len(stats["efficiency"]) >= 20:
            recent = np.mean(list(stats["efficiency"])[-10:])
            older = np.mean(list(stats["efficiency"])[:10])
            if older > 0 and recent < older * 0.9:
                score -= 10
        
        return max(0, min(100, score))
    
    def get_stats(self) -> dict:
        return {
            "anomaly_counts": self._anomaly_counts.copy(),
            "total_anomalies": len(self._anomalies),
            "recent_anomalies": [
                {k: v for k, v in a.items() if k != "timestamp"} 
                for a in list(self._anomalies)[-10:]
            ],
            "gpus_monitored": list(self._stats.keys()),
        }


class HyperparameterTuner:
    """
    Automatic hyperparameter tuning for mining optimization.
    
    Uses Bayesian optimization to find optimal:
    - Mining intensity
    - Power limits
    - Memory/core clocks
    - AI learning rates
    """
    
    def __init__(self):
        # Parameter bounds
        self.bounds = {
            "intensity": (50, 100),
            "power_limit": (60, 100),
            "core_clock_offset": (-200, 200),
            "memory_clock_offset": (-500, 1000),
        }
        
        # Observed data points
        self._observations: List[Dict[str, Any]] = []
        
        # Best found parameters
        self._best_params: Optional[Dict[str, float]] = None
        self._best_score: float = float('-inf')
        
        # Exploration vs exploitation
        self._exploration_weight = 0.2
    
    def suggest_params(self, current_params: Dict[str, float]) -> Dict[str, float]:
        """
        Suggest new parameters to try.
        
        Uses a combination of:
        - Random exploration
        - Gaussian process-inspired local search around best known point
        """
        if len(self._observations) < 10:
            # Pure exploration phase
            return self._random_params()
        
        if random.random() < self._exploration_weight:
            # Exploration
            return self._random_params()
        else:
            # Exploitation: sample around best known point
            if self._best_params is None:
                return self._random_params()
            
            suggested = {}
            for param, (low, high) in self.bounds.items():
                best_val = self._best_params.get(param, (low + high) / 2)
                # Add noise proportional to range
                noise_scale = (high - low) * 0.1
                new_val = best_val + np.random.normal(0, noise_scale)
                suggested[param] = max(low, min(high, new_val))
            
            return suggested
    
    def _random_params(self) -> Dict[str, float]:
        """Generate random parameters within bounds."""
        return {
            param: random.uniform(low, high)
            for param, (low, high) in self.bounds.items()
        }
    
    def record_observation(self, params: Dict[str, float], score: float):
        """Record an observation (params -> score mapping)."""
        self._observations.append({
            "params": params.copy(),
            "score": score,
            "timestamp": time.time(),
        })
        
        if score > self._best_score:
            self._best_score = score
            self._best_params = params.copy()
            logger.info("New best hyperparameters found: %s (score: %.4f)", params, score)
    
    def get_best_params(self) -> Tuple[Optional[Dict[str, float]], float]:
        """Get best found parameters and their score."""
        return self._best_params, self._best_score
    
    def get_stats(self) -> dict:
        return {
            "total_observations": len(self._observations),
            "best_score": self._best_score,
            "best_params": self._best_params,
            "exploration_weight": self._exploration_weight,
        }


# ══════════════════════════════════════════════════════════════════════════════
# Enhanced AI Mining Optimizer (v2)
# ══════════════════════════════════════════════════════════════════════════════

class EnhancedAIMiningOptimizer(AIMiningOptimizer):
    """
    Enhanced AI Mining Optimizer with advanced capabilities (v2).
    
    Adds:
    - Ensemble model combining DQN, Transformer, and basic RL
    - Advanced anomaly detection
    - Automatic hyperparameter tuning
    - Multi-objective optimization (hashrate, efficiency, profit)
    - Improved learning from outcomes
    """
    
    def __init__(self, data_dir: str = "/tmp/nexus_ai_mining_v2"):
        # Initialize parent
        super().__init__(data_dir)
        
        # v2 Components
        self.ensemble = EnsembleMiningOptimizer()
        self.anomaly_detector = AnomalyDetector()
        self.hyperparameter_tuner = HyperparameterTuner()
        
        # v2 State
        self._use_ensemble = True
        self._auto_tune = True
        self._snapshot_sequences: Dict[int, List[MiningSnapshot]] = {}
        self._max_sequence_length = 20
        
        # v2 Metrics
        self._v2_optimizations = 0
        self._anomalies_detected = 0
        self._params_tuned = 0
        
        # Load enhanced models
        self._load_enhanced_models()
        
        logger.info("Enhanced AI Mining Optimizer v2 initialized")
    
    def _load_enhanced_models(self):
        """Load v2 models if available."""
        try:
            self.ensemble.load(str(self.data_dir))
            logger.info("Loaded ensemble models")
        except Exception:
            pass
    
    def save_models(self):
        """Save all models including v2 enhancements."""
        super().save_models()
        self.ensemble.save(str(self.data_dir))
        logger.info("Saved all AI models including v2 enhancements")
    
    def record_snapshot(self, snapshot: MiningSnapshot):
        """Record snapshot with enhanced tracking."""
        super().record_snapshot(snapshot)
        
        # Track for sequence modeling
        gpu_id = snapshot.gpu_id
        if gpu_id not in self._snapshot_sequences:
            self._snapshot_sequences[gpu_id] = []
        
        self._snapshot_sequences[gpu_id].append(snapshot)
        if len(self._snapshot_sequences[gpu_id]) > self._max_sequence_length:
            self._snapshot_sequences[gpu_id] = self._snapshot_sequences[gpu_id][-self._max_sequence_length:]
        
        # Anomaly detection
        self.anomaly_detector.record(snapshot)
        anomalies = self.anomaly_detector.check_anomalies(snapshot)
        if anomalies:
            self._anomalies_detected += len(anomalies)
            for anomaly in anomalies:
                logger.warning("Mining anomaly detected: %s", anomaly["message"])
    
    def optimize(self, snapshot: MiningSnapshot) -> OptimizationResult:
        """Enhanced optimization using ensemble and additional techniques."""
        self._v2_optimizations += 1
        
        # Check for anomalies first
        anomalies = self.anomaly_detector.check_anomalies(snapshot)
        if any(a["severity"] == "high" for a in anomalies):
            # Critical anomaly - take immediate action
            return OptimizationResult(
                decision=MiningDecision.COOL_DOWN,
                confidence=0.95,
                recommended_settings={
                    "intensity": max(50, snapshot.intensity - 20),
                },
                reasoning=f"Critical anomaly detected: {anomalies[0]['message']}",
                predicted_improvement_percent=-10.0,
            )
        
        # Get snapshot history for sequence modeling
        history = self._snapshot_sequences.get(snapshot.gpu_id, [])
        
        if self._use_ensemble and len(history) >= 3:
            # Use ensemble for decision
            decision, confidence, details = self.ensemble.get_ensemble_decision(snapshot, history)
            
            # Translate decision to recommended settings
            recommended_settings = {}
            if decision == MiningDecision.INCREASE_INTENSITY:
                recommended_settings["intensity"] = min(100, snapshot.intensity + 5)
            elif decision == MiningDecision.DECREASE_INTENSITY:
                recommended_settings["intensity"] = max(50, snapshot.intensity - 5)
            elif decision == MiningDecision.OPTIMIZE_POWER:
                trans_pred = details.get("model_details", {}).get("transformer", {})
                if trans_pred and "optimal_power_limit" in trans_pred:
                    recommended_settings["power_limit_percent"] = int(trans_pred["optimal_power_limit"])
            
            # Get hyperparameter suggestions if auto-tuning enabled
            if self._auto_tune and self._v2_optimizations % 10 == 0:
                current_params = {
                    "intensity": snapshot.intensity,
                    "power_limit": snapshot.power_limit_percent,
                    "core_clock_offset": snapshot.core_clock_offset,
                    "memory_clock_offset": snapshot.memory_clock_offset,
                }
                suggested = self.hyperparameter_tuner.suggest_params(current_params)
                
                # Blend suggestions with ensemble decision
                if "intensity" not in recommended_settings:
                    recommended_settings["intensity"] = int(suggested["intensity"])
                
                self._params_tuned += 1
            
            return OptimizationResult(
                decision=decision,
                confidence=confidence,
                recommended_settings=recommended_settings,
                reasoning=f"Ensemble decision ({confidence*100:.1f}% confidence). Models: {details.get('model_decisions', {})}",
                predicted_improvement_percent=details.get("model_details", {}).get("transformer", {}).get("predicted_hashrate_change", 0),
            )
        else:
            # Fall back to parent implementation
            return super().optimize(snapshot)
    
    def learn_from_result(self, old_snapshot: MiningSnapshot, new_snapshot: MiningSnapshot, action_taken: MiningDecision):
        """Enhanced learning with all v2 components."""
        super().learn_from_result(old_snapshot, new_snapshot, action_taken)
        
        # Update ensemble
        history = self._snapshot_sequences.get(old_snapshot.gpu_id, [])
        self.ensemble.learn_from_outcome(old_snapshot, new_snapshot, action_taken, history)
        
        # Record for hyperparameter tuning
        if self._auto_tune:
            params = {
                "intensity": old_snapshot.intensity,
                "power_limit": old_snapshot.power_limit_percent,
                "core_clock_offset": old_snapshot.core_clock_offset,
                "memory_clock_offset": old_snapshot.memory_clock_offset,
            }
            
            # Score based on efficiency improvement
            old_efficiency = old_snapshot.hashrate / max(1, old_snapshot.power_watts)
            new_efficiency = new_snapshot.hashrate / max(1, new_snapshot.power_watts)
            score = new_efficiency / max(0.001, old_efficiency) - 1.0  # % improvement
            
            self.hyperparameter_tuner.record_observation(params, score)
    
    def get_stats(self) -> dict:
        """Get comprehensive v2 statistics."""
        base_stats = super().get_stats()
        
        health_scores = {}
        for gpu_id in self._snapshot_sequences:
            health_scores[f"gpu_{gpu_id}"] = self.anomaly_detector.get_health_score(gpu_id)
        
        return {
            **base_stats,
            "v2_enhancements": {
                "enabled": True,
                "use_ensemble": self._use_ensemble,
                "auto_tune": self._auto_tune,
                "v2_optimizations": self._v2_optimizations,
                "anomalies_detected": self._anomalies_detected,
                "params_tuned": self._params_tuned,
            },
            "ensemble_stats": self.ensemble.get_stats(),
            "anomaly_stats": self.anomaly_detector.get_stats(),
            "hyperparameter_stats": self.hyperparameter_tuner.get_stats(),
            "hardware_health_scores": health_scores,
        }
    
    def enable_ensemble(self, enabled: bool = True):
        """Enable or disable ensemble optimization."""
        self._use_ensemble = enabled
        logger.info("Ensemble optimization %s", "enabled" if enabled else "disabled")
    
    def enable_auto_tune(self, enabled: bool = True):
        """Enable or disable automatic hyperparameter tuning."""
        self._auto_tune = enabled
        logger.info("Auto-tuning %s", "enabled" if enabled else "disabled")


# Update the singleton to use enhanced optimizer
_enhanced_ai_optimizer: Optional[EnhancedAIMiningOptimizer] = None


def get_enhanced_ai_mining_optimizer() -> EnhancedAIMiningOptimizer:
    """Get the singleton enhanced AI mining optimizer instance."""
    global _enhanced_ai_optimizer
    if _enhanced_ai_optimizer is None:
        _enhanced_ai_optimizer = EnhancedAIMiningOptimizer()
    return _enhanced_ai_optimizer
