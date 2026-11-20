import os
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from dotenv import load_dotenv

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)


def _bootstrap_env():
    env_arg = sys.argv[1] if len(sys.argv) > 1 else None
    env_path = env_arg or os.getenv("ENV_FILE") or ".env-multiativos"
    env_path = os.path.abspath(env_path)
    if os.path.exists(env_path):
        load_dotenv(dotenv_path=env_path, override=True)


_bootstrap_env()

from regime.regime_detector import RegimeDetector
from utils.indicadores import calculate_indicators
from utils.logica_stop_take_adaptativo import calcular_stop_take_adaptativo


def _load_env() -> str:
    env_arg = sys.argv[1] if len(sys.argv) > 1 else None
    env_path = env_arg or os.getenv("ENV_FILE") or ".env-multiativos"
    env_path = os.path.abspath(env_path)
    if not os.path.exists(env_path):
        raise FileNotFoundError(f"ENV file not found: {env_path}")
    load_dotenv(dotenv_path=env_path, override=True)
    return env_path


def _get_env(name: str, default=None, cast=None):
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    if cast:
        try:
            return cast(raw)
        except Exception:
            return default
    return raw


def _parse_list(raw: str) -> List[str]:
    if not raw:
        return []
    return [x.strip().upper() for x in raw.split(",") if x.strip()]


def _risk_params():
    return {
        "atr_min": float(_get_env("ATR_MIN", 2.0, float)),
        "atr_mult": float(_get_env("ATR_MULT", 1.4, float)),
        "sl_pct_min": float(_get_env("SL_PCT_MIN", 0.007, float)),
        "sl_pct_max": float(_get_env("SL_PCT_MAX", 0.014, float)),
        "rrr": float(_get_env("RRR", 1.0, float)),
        "bb_dist_min": float(_get_env("BB_DIST_MIN", 40, float)),
        "bb_mult": float(_get_env("BB_MULT", 1.06, float)),
        "fixo_pct": float(_get_env("FIXO_PCT", 0.005, float)),
        "sr_lookback": int(_get_env("SR_LOOKBACK", 20, int)),
    }


def _load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    if "time" not in df.columns:
        raise ValueError(f"CSV missing time column: {path}")
    df["time"] = pd.to_datetime(df["time"], errors="coerce")
    df = df.dropna(subset=["time"]).copy()
    df = df.sort_values("time")
    return df


def _true_range(df: pd.DataFrame) -> pd.Series:
    high = df["high"]
    low = df["low"]
    close = df["close"].shift(1)
    tr = pd.concat([
        high - low,
        (high - close).abs(),
        (low - close).abs(),
    ], axis=1).max(axis=1)
    return tr


def _atr(df: pd.DataFrame, window: int) -> pd.Series:
    tr = _true_range(df)
    return tr.rolling(window=window, min_periods=window).mean()


def _returns(series: pd.Series) -> pd.Series:
    return series.pct_change()


@dataclass
class Trade:
    symbol: str
    entry_time: pd.Timestamp
    entry_price: float
    exit_time: pd.Timestamp
    exit_price: float
    pnl: float
    pnl_pct: float
    reason: str
    qty: float
    regime: int


@dataclass
class Trigger:
    time: pd.Timestamp
    level: float


class LeadLagBacktest:
    def __init__(self):
        self.interval = int(_get_env("INTERVAL", 5, int))
        self.alts = _parse_list(_get_env("ATIVOS", ""))

        self.data_btc_csv = _get_env("DATA_BTC_CSV")
        self.data_alt_dir = _get_env("DATA_ALT_DIR")

        # BTC trigger params
        self.btc_atr_window = int(_get_env("BTC_ATR_WINDOW", 14, int))
        self.btc_atr_mean_window = int(_get_env("BTC_ATR_MEAN_WINDOW", 100, int))
        self.btc_res_lookback = int(_get_env("BTC_RESISTANCE_LOOKBACK", 50, int))
        self.btc_breakout_atr_mult = float(_get_env("BTC_BREAKOUT_ATR_MULT", 1.2, float))
        self.btc_close_above_pct = float(_get_env("BTC_CLOSE_ABOVE_PCT", 0.001, float))
        self.btc_follow_body_pct = float(_get_env("BTC_FOLLOW_THROUGH_BODY_PCT", 0.55, float))
        self.btc_pullback_max_pct = float(_get_env("BTC_PULLBACK_MAX_PCT", 0.40, float))
        self.btc_vol_max_atr = float(_get_env("BTC_VOLATILITY_MAX_ATR", 3.0, float))

        # Corr/lag/beta params
        self.corr_window_short = int(_get_env("CORR_WINDOW_SHORT", 20, int))
        self.corr_window_long = int(_get_env("CORR_WINDOW_LONG", 100, int))
        self.corr_min_short = float(_get_env("CORR_MIN_SHORT", 0.35, float))
        self.corr_min_long = float(_get_env("CORR_MIN_LONG", 0.25, float))

        self.lag_min = int(_get_env("LAG_MIN", 1, int))
        self.lag_max = int(_get_env("LAG_MAX", 5, int))
        self.lag_update_window = int(_get_env("LAG_UPDATE_WINDOW", 200, int))

        self.beta_min = float(_get_env("BETA_MIN", 0.5, float))
        self.beta_max = float(_get_env("BETA_MAX", 2.5, float))

        # Score weights
        self.w_corr = float(_get_env("SCORE_W_CORR", 0.4, float))
        self.w_beta = float(_get_env("SCORE_W_BETA", 0.2, float))
        self.w_lag = float(_get_env("SCORE_W_LAG", 0.2, float))
        self.w_liq = float(_get_env("SCORE_W_LIQ", 0.2, float))

        # Risk params for alt trades (fallback)
        self.alt_atr_window = int(_get_env("ALT_ATR_WINDOW", 14, int))
        self.alt_atr_mult = float(_get_env("ALT_ATR_MULT", 1.5, float))
        self.alt_rrr = float(_get_env("ALT_RRR", 2.0, float))

        # Trailing/BE params
        self.trailing_activation_pct = float(_get_env("TRAILING_ACTIVATION_PCT", 0.6, float))
        self.trailing_atr_mult = float(_get_env("TRAILING_ATR_MONITOR_MULT", 1.8, float))
        self.trailing_min_entry_pct = float(_get_env("TRAILING_MIN_ENTRY_PCT", 0.003, float))
        self.trailing_abs_min = float(_get_env("TRAILING_ABSOLUTE_MIN", 0.0, float))
        self.trailing_min_move_atr = float(_get_env("TRAILING_MIN_MOVE_ATR", 0.5, float))
        self.be_activation_pct = float(_get_env("BE_ACTIVATION_PCT", 0.6, float))
        self.be_offset_pct = float(_get_env("BE_OFFSET_PCT", 0.001, float))
        # Trailing telemetry (backtest)
        self.trailing_activation_events = 0
        self.trailing_offset_samples: List[float] = []
        self.trailing_status_count = 0
        self.trailing_status_true_count = 0
        self.trailing_check_count = 0

        # Costs
        self.fee_pct = float(_get_env("FEE_PCT", 0.0006, float))
        self.spread_pct = float(_get_env("SPREAD_PCT", 0.0002, float))

        # Capital / sizing
        self.start_capital = float(_get_env("START_CAPITAL", 10000.0, float))
        self.risk_pct = float(_get_env("RISK_PCT", 1.0, float))
        self.leverage = float(_get_env("LEVERAGE", 1.0, float))

        # Regime filter
        self.allowed_regimes = _parse_list(_get_env("ALLOWED_REGIMES", "")) or []

        # Dynamic symbol filter
        self.dynamic_window_days = int(_get_env("DYNAMIC_SYMBOL_WINDOW_DAYS", 0, int))
        self.dynamic_min_trades = int(_get_env("DYNAMIC_MIN_TRADES", 3, int))
        self.dynamic_min_pnl = float(_get_env("DYNAMIC_MIN_PNL", 0.0, float))
        self.dynamic_min_winrate = float(_get_env("DYNAMIC_MIN_WINRATE", 0.0, float))

    def _alt_path(self, symbol: str) -> Optional[str]:
        if not self.data_alt_dir:
            return None
        fname = f"market_data_{symbol}_{self.interval}m.csv"
        path = os.path.join(self.data_alt_dir, fname)
        if os.path.exists(path):
            return path
        # Fallbacks
        fallback1 = os.path.join(self.data_alt_dir, f"{symbol.lower()}_{self.interval}m.csv")
        if os.path.exists(fallback1):
            return fallback1
        fallback2 = os.path.join(self.data_alt_dir, f"{symbol}_{self.interval}m.csv")
        if os.path.exists(fallback2):
            return fallback2
        return None

    def _prepare_data(self) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, Dict[str, pd.DataFrame]]]:
        if not self.data_btc_csv:
            raise ValueError("DATA_BTC_CSV not set")
        btc_df = _load_csv(self.data_btc_csv)
        required = {"open", "high", "low", "close", "volume"}
        if not required.issubset(set(btc_df.columns)):
            raise ValueError("BTC CSV missing required columns")

        btc_df = btc_df.sort_values("time").reset_index(drop=True)
        btc_ind = calculate_indicators(btc_df.copy())
        if btc_ind is None or btc_ind.empty:
            raise ValueError("Failed to compute BTC indicators")
        btc_ind = btc_ind.set_index("time").sort_index()
        btc_df["ret"] = _returns(btc_df["close"])
        btc_df["atr"] = _atr(btc_df, self.btc_atr_window)
        btc_df["atr_mean"] = btc_df["atr"].rolling(
            window=self.btc_atr_mean_window,
            min_periods=self.btc_atr_mean_window,
        ).mean()

        alt_data: Dict[str, Dict[str, pd.DataFrame]] = {}
        for symbol in self.alts:
            path = self._alt_path(symbol)
            if not path:
                print(f"[WARN] CSV not found for {symbol}")
                continue
            alt_df = _load_csv(path)
            if not required.issubset(set(alt_df.columns)):
                print(f"[WARN] ALT CSV missing columns for {symbol}")
                continue
            alt_df = alt_df.sort_values("time").reset_index(drop=True)
            merged = pd.merge(
                btc_df,
                alt_df,
                on="time",
                suffixes=("_btc", "_alt"),
            )
            if merged.empty:
                print(f"[WARN] No overlapping data for {symbol}")
                continue
            merged = merged.set_index("time").sort_index()
            merged["ret_alt"] = _returns(merged["close_alt"])
            merged["ret_btc"] = merged["ret"]
            alt_ohlc = merged[["open_alt", "high_alt", "low_alt", "close_alt", "volume_alt"]].rename(
                columns={
                    "open_alt": "open",
                    "high_alt": "high",
                    "low_alt": "low",
                    "close_alt": "close",
                    "volume_alt": "volume",
                }
            )
            alt_ohlc = alt_ohlc.reset_index()
            alt_ind = calculate_indicators(alt_ohlc.copy())
            if alt_ind is None or alt_ind.empty:
                print(f"[WARN] Indicators failed for {symbol}")
                continue
            if "BB_Upper" not in alt_ind.columns and "Bollinger_Upper" in alt_ind.columns:
                alt_ind["BB_Upper"] = alt_ind["Bollinger_Upper"]
            if "BB_Lower" not in alt_ind.columns and "Bollinger_Lower" in alt_ind.columns:
                alt_ind["BB_Lower"] = alt_ind["Bollinger_Lower"]
            alt_ind = alt_ind.set_index("time").sort_index()

            merged["atr_alt"] = _atr(
                alt_ohlc.rename(
                    columns={"open": "open", "high": "high", "low": "low", "close": "close"}
                ),
                self.alt_atr_window,
            )
            alt_data[symbol] = {"merged": merged, "ind": alt_ind}
        return btc_df, btc_ind, alt_data

    def _btc_regime(self, btc_ind: pd.DataFrame, t: pd.Timestamp) -> int:
        if t not in btc_ind.index:
            slice_df = btc_ind.loc[:t].tail(200)
        else:
            slice_df = btc_ind.loc[:t].tail(200)
        if slice_df.empty:
            return 2
        try:
            return RegimeDetector().detectar(slice_df)
        except Exception:
            return 2

    def _btc_trigger(self, btc_df: pd.DataFrame, i: int) -> Optional[Trigger]:
        if i < self.btc_res_lookback:
            return None
        if i + 1 >= len(btc_df):
            return None

        row = btc_df.iloc[i]
        next_row = btc_df.iloc[i + 1]
        if pd.isna(row["atr"]) or pd.isna(row["atr_mean"]):
            return None

        resistance = btc_df["high"].iloc[i - self.btc_res_lookback:i].max()
        if resistance <= 0:
            return None

        close = float(row["close"])
        breakout = close > resistance * (1.0 + self.btc_close_above_pct)
        if not breakout:
            return None
        if (close - resistance) < (self.btc_breakout_atr_mult * float(row["atr"])):
            return None

        # Volatility guard
        if float(row["atr"]) > float(row["atr_mean"]) * self.btc_vol_max_atr:
            return None

        # Follow-through
        next_body = abs(float(next_row["close"]) - float(next_row["open"]))
        next_range = max(float(next_row["high"]) - float(next_row["low"]), 1e-9)
        body_pct = next_body / next_range
        if body_pct < self.btc_follow_body_pct:
            return None
        if float(next_row["close"]) < close:
            return None

        # Pullback
        impulse = close - resistance
        max_pullback = close - (self.btc_pullback_max_pct * impulse)
        if float(next_row["low"]) < max_pullback:
            return None

        return Trigger(time=row["time"], level=resistance)

    def _score_alt(self, merged: pd.DataFrame, t: pd.Timestamp) -> Optional[Tuple[float, int, float, float]]:
        if t not in merged.index:
            return None

        def _window(end: pd.Timestamp, length: int) -> pd.DataFrame:
            return merged.loc[:end].tail(length)

        win_short = _window(t, self.corr_window_short)
        win_long = _window(t, self.corr_window_long)
        win_lag = _window(t, self.lag_update_window)

        if len(win_short) < self.corr_window_short or len(win_long) < self.corr_window_long:
            return None

        corr_short = win_short["ret_btc"].corr(win_short["ret_alt"])
        corr_long = win_long["ret_btc"].corr(win_long["ret_alt"])
        if pd.isna(corr_short) or pd.isna(corr_long):
            return None
        if corr_short < self.corr_min_short or corr_long < self.corr_min_long:
            return None

        # Lag via max correlation
        best_lag = None
        best_corr = None
        btc_ret = win_lag["ret_btc"].to_numpy()
        alt_ret = win_lag["ret_alt"].to_numpy()
        for lag in range(self.lag_min, self.lag_max + 1):
            if len(btc_ret) <= lag or len(alt_ret) <= lag:
                continue
            c = np.corrcoef(btc_ret[:-lag], alt_ret[lag:])[0, 1]
            if np.isnan(c):
                continue
            if best_corr is None or c > best_corr:
                best_corr = c
                best_lag = lag
        if best_lag is None:
            return None

        # Beta
        btc_ret_series = win_long["ret_btc"].dropna().to_numpy()
        alt_ret_series = win_long["ret_alt"].dropna().to_numpy()
        if len(btc_ret_series) != len(alt_ret_series) or len(btc_ret_series) == 0:
            return None
        var_btc = np.var(btc_ret_series)
        if var_btc <= 0:
            return None
        cov = np.cov(btc_ret_series, alt_ret_series)[0, 1]
        beta = cov / var_btc
        if beta < self.beta_min or beta > self.beta_max:
            return None

        # Score components
        score_corr = ((corr_short - self.corr_min_short) / (1.0 - self.corr_min_short) +
                      (corr_long - self.corr_min_long) / (1.0 - self.corr_min_long)) / 2.0
        score_corr = float(np.clip(score_corr, 0.0, 1.0))

        beta_span = max(self.beta_max - 1.0, 1.0 - self.beta_min)
        score_beta = 1.0 - (abs(beta - 1.0) / beta_span)
        score_beta = float(np.clip(score_beta, 0.0, 1.0))

        if self.lag_max > self.lag_min:
            score_lag = (self.lag_max - best_lag) / (self.lag_max - self.lag_min)
        else:
            score_lag = 1.0
        score_lag = float(np.clip(score_lag, 0.0, 1.0))

        vol_avg = float(win_long["volume_alt"].mean())
        return score_corr, best_lag, score_beta, vol_avg

    def _pick_best_alt(
        self, alt_data: Dict[str, Dict[str, pd.DataFrame]], t: pd.Timestamp
    ) -> Optional[Tuple[str, float, Dict[str, pd.DataFrame]]]:
        candidates = []
        for symbol, data in alt_data.items():
            merged = data["merged"]
            scored = self._score_alt(merged, t)
            if not scored:
                continue
            score_corr, lag, score_beta, vol_avg = scored
            candidates.append((symbol, data, score_corr, lag, score_beta, vol_avg))

        if not candidates:
            return None

        vols = [c[5] for c in candidates]
        vmin, vmax = min(vols), max(vols)

        best = None
        best_score = -1.0
        for symbol, data, score_corr, lag, score_beta, vol_avg in candidates:
            if vmax > vmin:
                score_liq = (vol_avg - vmin) / (vmax - vmin)
            else:
                score_liq = 0.5
            total = (
                self.w_corr * score_corr +
                self.w_beta * score_beta +
                self.w_lag * ((self.lag_max - lag) / max(self.lag_max - self.lag_min, 1)) +
                self.w_liq * score_liq
            )
            if total > best_score:
                best_score = total
                best = (symbol, total, data)

        return best

    def _symbol_allowed(self, symbol: str, t: pd.Timestamp, history: Dict[str, List[Trade]]) -> bool:
        if self.dynamic_window_days <= 0:
            return True
        trades = history.get(symbol, [])
        if not trades:
            return True
        window_start = t - pd.Timedelta(days=self.dynamic_window_days)
        recent = [tr for tr in trades if tr.entry_time >= window_start]
        if len(recent) < self.dynamic_min_trades:
            return True
        pnl = sum(tr.pnl for tr in recent)
        wins = sum(1 for tr in recent if tr.pnl > 0)
        winrate = (wins / len(recent)) * 100.0 if recent else 0.0
        if pnl < self.dynamic_min_pnl:
            return False
        if winrate < self.dynamic_min_winrate:
            return False
        return True

    def run(self) -> List[Trade]:
        btc_df, btc_ind, alt_data = self._prepare_data()
        if not alt_data:
            print("No valid alt data found")
            return []

        trades: List[Trade] = []
        symbol_history: Dict[str, List[Trade]] = {}
        capital = float(self.start_capital)
        in_trade = False
        trade_ctx = {}

        start_idx = max(
            self.btc_res_lookback + 1,
            self.corr_window_long + 1,
            self.lag_update_window + 1,
            self.btc_atr_mean_window + 1,
        )

        i = start_idx
        while i < len(btc_df) - 1:
            row = btc_df.iloc[i]
            t = row["time"]

            if not in_trade:
                trig = self._btc_trigger(btc_df, i)
                if trig:
                    regime_now = self._btc_regime(btc_ind, trig.time)
                    if self.allowed_regimes and str(regime_now) not in self.allowed_regimes:
                        i += 1
                        continue

                    filtered = {
                        sym: data
                        for sym, data in alt_data.items()
                        if self._symbol_allowed(sym, trig.time, symbol_history)
                    }
                    best = self._pick_best_alt(filtered, trig.time) if filtered else None
                    if best:
                        symbol, score, data = best
                        merged = data["merged"]
                        ind = data["ind"]
                        # Entry on next candle
                        next_time = btc_df.iloc[i + 1]["time"]
                        if next_time in merged.index:
                            entry = merged.loc[next_time]
                            entry_price = float(entry["open_alt"])
                            risk_df = ind.loc[:next_time].tail(200)
                            stop, take, _, _ = (None, None, None, None)
                            if not risk_df.empty:
                                stop, take, _, _ = calcular_stop_take_adaptativo(
                                    risk_df.copy(), entry_price, "buy", params=_risk_params()
                                )
                            if stop is None or take is None:
                                atr_alt = float(entry["atr_alt"]) if not pd.isna(entry["atr_alt"]) else None
                                if atr_alt is None or atr_alt <= 0:
                                    i += 1
                                    continue
                                stop = entry_price - (self.alt_atr_mult * atr_alt)
                                take = entry_price + (self.alt_rrr * (entry_price - stop))
                            trade_ctx = {
                                  "symbol": symbol,
                                  "entry_time": next_time,
                                  "entry_price": entry_price,
                                  "stop": stop,
                                  "take": take,
                                  "btc_level": trig.level,
                                  "merged": merged,
                                  "capital_at_entry": capital,
                                  "regime": int(regime_now),
                                  "trailing_activated": False,
                                  "trailing_offset": 0.0,
                              }
                            in_trade = True
                            i += 1
                            continue

            if in_trade:
                symbol = trade_ctx["symbol"]
                merged = trade_ctx["merged"]
                if t in merged.index:
                    alt_row = merged.loc[t]
                    low = float(alt_row["low_alt"])
                    high = float(alt_row["high_alt"])
                    close_btc = float(alt_row["close_btc"])
                    close_alt = float(alt_row["close_alt"])
                    atr_alt = float(alt_row["atr_alt"]) if not pd.isna(alt_row["atr_alt"]) else None

                    # Trailing/BE management (long only)
                    if atr_alt is not None and atr_alt > 0:
                        entry_price = trade_ctx["entry_price"]
                        risk_dist = abs(entry_price - trade_ctx["stop"])
                        target_dist = abs(trade_ctx["take"] - entry_price)
                        move = close_alt - entry_price
                        trailing_active = bool(trade_ctx.get("trailing_activated"))
                        trailing_offset = float(trade_ctx.get("trailing_offset") or 0.0)

                        if target_dist > 0:
                            progress_pct = move / target_dist
                            min_move_atr = self.trailing_min_move_atr * atr_alt
                            need_by_pct = self.trailing_activation_pct * target_dist
                            self.trailing_status_count += 1
                            if trailing_active:
                                self.trailing_status_true_count += 1
                            print(
                                "[TRAILING STATUS] "
                                f"Ativado={trailing_active} | "
                                f"Offset={trailing_offset:.2f} | "
                                f"Progresso={progress_pct*100:.1f}% | "
                                f"DistanciaEntrada={move:.2f} | "
                                f"ATR={atr_alt:.2f} | "
                                f"MinMove={min_move_atr:.2f}"
                            )
                            self.trailing_check_count += 1
                            print(
                                "[TRAILING CHECK] "
                                f"DeltaPreco={move:.2f} | "
                                f"Necessario: {need_by_pct:.2f} ou {min_move_atr:.2f} | "
                                f"Ativacao TP%={self.trailing_activation_pct*100:.1f}% | "
                                f"Progresso={progress_pct*100:.2f}%"
                            )

                        if target_dist > 0 and move >= (self.trailing_activation_pct * target_dist):
                            if move >= (self.trailing_min_move_atr * atr_alt):
                                trailing_dist = max(
                                    self.trailing_atr_mult * atr_alt,
                                    entry_price * self.trailing_min_entry_pct,
                                    self.trailing_abs_min,
                                )
                                trade_ctx["trailing_offset"] = float(trailing_dist)
                                if not trade_ctx.get("trailing_activated"):
                                    trade_ctx["trailing_activated"] = True
                                    self.trailing_activation_events += 1
                                    self.trailing_offset_samples.append(float(trailing_dist))
                                    print(f"[TRAILING ATIVADO] Offset={trailing_dist:.2f}")
                                new_stop = close_alt - trailing_dist
                                if new_stop > trade_ctx["stop"]:
                                    trade_ctx["stop"] = new_stop

                        if target_dist > 0 and move >= (self.be_activation_pct * target_dist):
                            be_price = entry_price * (1.0 + self.be_offset_pct)
                            if be_price > trade_ctx["stop"]:
                                trade_ctx["stop"] = be_price

                    reason = None
                    exit_price = None
                    if low <= trade_ctx["stop"]:
                        reason = "stop"
                        exit_price = trade_ctx["stop"]
                    elif high >= trade_ctx["take"]:
                        reason = "take"
                        exit_price = trade_ctx["take"]
                    elif close_btc < trade_ctx["btc_level"]:
                        reason = "btc_break_fail"
                        exit_price = float(alt_row["close_alt"])

                    if reason:
                        entry_price = trade_ctx["entry_price"]
                        entry_adj = entry_price * (1.0 + self.spread_pct)
                        exit_adj = exit_price * (1.0 - self.spread_pct)
                        stop_dist = abs(entry_adj - trade_ctx["stop"])
                        capital_at_entry = float(trade_ctx.get("capital_at_entry", capital))
                        risk_amount = capital_at_entry * (self.risk_pct / 100.0) * self.leverage
                        qty = (risk_amount / stop_dist) if stop_dist > 0 else 0.0
                        fee = self.fee_pct * (entry_adj + exit_adj) * qty
                        pnl = ((exit_adj - entry_adj) * qty) - fee
                        pnl_pct = (pnl / capital_at_entry) if capital_at_entry else 0.0
                        trades.append(Trade(
                            symbol=symbol,
                            entry_time=trade_ctx["entry_time"],
                            entry_price=entry_adj,
                            exit_time=t,
                            exit_price=exit_adj,
                            pnl=pnl,
                            pnl_pct=pnl_pct,
                            reason=reason,
                            qty=qty,
                            regime=int(trade_ctx.get("regime", 2)),
                        ))
                        symbol_history.setdefault(symbol, []).append(trades[-1])
                        capital += pnl
                        in_trade = False
                        trade_ctx = {}

            i += 1

        # Trailing/backtest summary
        if self.trailing_offset_samples:
            avg_offset = float(np.mean(self.trailing_offset_samples))
        else:
            avg_offset = 0.0
        print(
            "[TRAILING SUMMARY] "
            f"ativacoes={self.trailing_activation_events} | "
            f"avg_offset={avg_offset:.2f} | "
            f"status_true={self.trailing_status_true_count}/{self.trailing_status_count} | "
            f"checks={self.trailing_check_count}"
        )
        # Exit reason summary (stop_gain vs stop)
        stop_gain = sum(1 for t in trades if t.reason == "stop" and t.pnl > 0)
        stop_loss = sum(1 for t in trades if t.reason == "stop" and t.pnl <= 0)
        take_count = sum(1 for t in trades if t.reason == "take")
        timeout_count = sum(1 for t in trades if t.reason == "timeout")
        other_count = len(trades) - (stop_gain + stop_loss + take_count + timeout_count)
        print(
            "[EXIT SUMMARY] "
            f"stop_gain={stop_gain} | "
            f"stop={stop_loss} | "
            f"take={take_count} | "
            f"timeout={timeout_count} | "
            f"other={other_count}"
        )
        return trades


def _summary(trades: List[Trade]):
    if not trades:
        print("No trades executed")
        return
    pnl = sum(t.pnl for t in trades)
    wins = sum(1 for t in trades if t.pnl > 0)
    winrate = (wins / len(trades)) * 100.0
    avg_win = np.mean([t.pnl for t in trades if t.pnl > 0]) if wins else 0.0
    avg_loss = np.mean([t.pnl for t in trades if t.pnl <= 0]) if wins < len(trades) else 0.0
    start_capital = float(_get_env("START_CAPITAL", 10000.0, float))
    end_capital = start_capital + pnl
    print(f"Trades: {len(trades)}")
    print(f"Net PnL: {pnl:.2f}")
    print(f"Start Capital: {start_capital:.2f}")
    print(f"End Capital: {end_capital:.2f}")
    print(f"Winrate: {winrate:.2f}%")
    print(f"Avg Win: {avg_win:.4f}")
    print(f"Avg Loss: {avg_loss:.4f}")

    def _split(trs, start, end):
        if not start or not end:
            return trs
        start_dt = pd.to_datetime(start)
        end_dt = pd.to_datetime(end)
        return [t for t in trs if start_dt <= t.entry_time <= end_dt]

    is_start = _get_env("IS_START")
    is_end = _get_env("IS_END")
    oos_start = _get_env("OOS_START")
    oos_end = _get_env("OOS_END")

    is_trades = _split(trades, is_start, is_end) if is_start and is_end else []
    oos_trades = _split(trades, oos_start, oos_end) if oos_start and oos_end else []

    def _print_block(label, trs):
        if not trs:
            return
        pnl_l = sum(t.pnl for t in trs)
        wins_l = sum(1 for t in trs if t.pnl > 0)
        winrate_l = (wins_l / len(trs)) * 100.0 if trs else 0.0
        end_capital_l = start_capital + pnl_l
        print(
            f"{label} Trades: {len(trs)} | PnL: {pnl_l:.2f} | "
            f"End Capital: {end_capital_l:.2f} | Winrate: {winrate_l:.2f}%"
        )

    _print_block("IS", is_trades)
    _print_block("OOS", oos_trades)

    output_dir = _get_env("OUTPUT_DIR", os.path.join(ROOT_DIR, "resultados_backtest"))
    os.makedirs(output_dir, exist_ok=True)
    trades_df = pd.DataFrame([t.__dict__ for t in trades])
    out_path = os.path.join(output_dir, "leadlag_trades.csv")
    trades_df.to_csv(out_path, index=False)

    summary = trades_df.groupby("symbol")["pnl"].agg(["count", "sum", "mean"])
    summary_path = os.path.join(output_dir, "leadlag_summary_by_symbol.csv")
    summary.to_csv(summary_path)

    if "regime" in trades_df.columns:
        regime_summary = trades_df.groupby("regime")["pnl"].agg(["count", "sum", "mean"])
        regime_path = os.path.join(output_dir, "leadlag_summary_by_regime.csv")
        regime_summary.to_csv(regime_path)

    if not trades_df.empty:
        equity = trades_df.sort_values("entry_time").copy()
        equity["equity"] = start_capital + equity["pnl"].cumsum()
        equity_path = os.path.join(output_dir, "leadlag_equity_curve.csv")
        equity[["entry_time", "equity"]].to_csv(equity_path, index=False)

    if is_trades:
        pd.DataFrame([t.__dict__ for t in is_trades]).to_csv(
            os.path.join(output_dir, "leadlag_trades_is.csv"), index=False
        )
    if oos_trades:
        pd.DataFrame([t.__dict__ for t in oos_trades]).to_csv(
            os.path.join(output_dir, "leadlag_trades_oos.csv"), index=False
        )


if __name__ == "__main__":
    env_path = _load_env()
    print(f"Loaded ENV: {env_path}")
    bt = LeadLagBacktest()
    trades = bt.run()
    _summary(trades)
