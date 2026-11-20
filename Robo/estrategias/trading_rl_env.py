import pandas as pd
import numpy as np
import gymnasium as gym
from stable_baselines3 import PPO
from stable_baselines3.common.env_checker import check_env
from gymnasium import spaces

class TradingEnv(gym.Env):
    """
    Ambiente personalizado de trading para Reinforcement Learning.
    """
    def __init__(self, df):
        super(TradingEnv, self).__init__()
        self.df = df
        self.current_step = 0
        self.initial_balance = 10000
        self.balance = self.initial_balance
        self.positions = 0
        self.net_worth = self.initial_balance
        self.action_space = spaces.Discrete(3)  # 0 = Hold, 1 = Buy, 2 = Sell
        self.observation_space = spaces.Box(low=0, high=np.inf, shape=(len(df.columns),), dtype=np.float32)

    def reset(self, seed=None, options=None):
        """
        Redefine o ambiente para o estado inicial.
        """
        super().reset(seed=seed)  # Define a seed para consistência em testes
        self.current_step = 0
        self.balance = self.initial_balance
        self.positions = 0
        self.net_worth = self.initial_balance
        return self._next_observation(), {}

    def _next_observation(self):
        return self.df.iloc[self.current_step].values.astype(np.float32)

    def step(self, action):
        current_price = self.df.iloc[self.current_step]['close']
        reward = 0

        if action == 1:  # Buy
            self.positions += self.balance / current_price
            self.balance = 0
        elif action == 2:  # Sell
            self.balance += self.positions * current_price
            self.positions = 0

        self.net_worth = self.balance + self.positions * current_price
        reward = self.net_worth - self.initial_balance

        self.current_step += 1
        terminated = self.current_step >= len(self.df) - 1
        truncated = False  # Defina como True se houver uma condição para truncamento

        return self._next_observation(), reward, terminated, truncated, {}

    def render(self):
        print(f"Step: {self.current_step}, Net Worth: {self.net_worth}")

def gerar_dados_simulados():
    data = {
        "close": np.random.uniform(low=100, high=200, size=100),
        "high": np.random.uniform(low=150, high=250, size=100),
        "low": np.random.uniform(low=50, high=100, size=100),
        "volume": np.random.uniform(low=1000, high=10000, size=100),
    }
    return pd.DataFrame(data)

# Gerar dados simulados
df = gerar_dados_simulados()
print(df.head())

# Criar o ambiente
env = TradingEnv(df)
check_env(env)

# Treinar o modelo
model = PPO("MlpPolicy", env, verbose=1)
model.learn(total_timesteps=100000)

# Salvar o modelo
model.save("ppo_trading_model")
