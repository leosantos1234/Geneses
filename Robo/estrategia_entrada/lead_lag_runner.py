import os
import time
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from dotenv import load_dotenv

from estrategia_entrada.estrategia_breakout_filtros import existe_ordem_aberta
from regime.regime_detector import RegimeDetector
from utils.indicadores import calculate_indicators
from utils.logica_stop_take_adaptativo import calcular_stop_take_adaptativo
from utils.logger_config import configurar_logger_global
from utils.utils_market import get_market_data_bybit
from compra1 import create_bybit_session, executar_ordem_completa, get_saldo_disponivel


@dataclass
class Trigger:
    time: pd.Timestamp
    level: float


@dataclass
class ScoredAlt:
    symbol: str
    score: float
    lag: int
    score_corr: float
    score_beta: float
    score_liq: float


class LeadLagRunner:
    def __init__(self, env_path: str):
        load_dotenv(dotenv_path=env_path, override=True)
        configurar_logger_global('lead_lag.log')
        self.logger = logging.getLogger(__name__)

        self.env_path = env_path
        self.interval = str(_get_env('INTERVAL', '5'))
        self.limit = int(_get_env('LEAD_LAG_KLINE_LIMIT', 400, int))
        self.poll_seconds = int(_get_env('LEAD_LAG_POLL_SECONDS', int(self.interval) * 60, int))
        self.heartbeat_every = int(_get_env('LEAD_LAG_HEARTBEAT_EVERY', 60, int))
        self.status_every = int(_get_env('LEAD_LAG_STATUS_EVERY', 5, int))
        self.alts = _parse_list(_get_env('ATIVOS', ''))

        # BTC trigger params
        self.btc_atr_window = int(_get_env('BTC_ATR_WINDOW', 14, int))
        self.btc_atr_mean_window = int(_get_env('BTC_ATR_MEAN_WINDOW', 100, int))
        self.btc_res_lookback = int(_get_env('BTC_RESISTANCE_LOOKBACK', 50, int))
        self.btc_breakout_atr_mult = float(_get_env('BTC_BREAKOUT_ATR_MULT', 1.2, float))
        self.btc_close_above_pct = float(_get_env('BTC_CLOSE_ABOVE_PCT', 0.001, float))
        self.btc_follow_body_pct = float(_get_env('BTC_FOLLOW_THROUGH_BODY_PCT', 0.55, float))
        self.btc_pullback_max_pct = float(_get_env('BTC_PULLBACK_MAX_PCT', 0.40, float))
        self.btc_vol_max_atr = float(_get_env('BTC_VOLATILITY_MAX_ATR', 3.0, float))

        # Corr/lag/beta params
        self.corr_window_short = int(_get_env('CORR_WINDOW_SHORT', 20, int))
        self.corr_window_long = int(_get_env('CORR_WINDOW_LONG', 100, int))
        self.corr_min_short = float(_get_env('CORR_MIN_SHORT', 0.35, float))
        self.corr_min_long = float(_get_env('CORR_MIN_LONG', 0.25, float))

        self.lag_min = int(_get_env('LAG_MIN', 1, int))
        self.lag_max = int(_get_env('LAG_MAX', 5, int))
        self.lag_update_window = int(_get_env('LAG_UPDATE_WINDOW', 200, int))

        self.beta_min = float(_get_env('BETA_MIN', 0.5, float))
        self.beta_max = float(_get_env('BETA_MAX', 2.5, float))

        # Score weights
        self.w_corr = float(_get_env('SCORE_W_CORR', 0.4, float))
        self.w_beta = float(_get_env('SCORE_W_BETA', 0.2, float))
        self.w_lag = float(_get_env('SCORE_W_LAG', 0.2, float))
        self.w_liq = float(_get_env('SCORE_W_LIQ', 0.2, float))

        # Risk params
        self.risk_pct = float(_get_env('RISK_PCT', 1.0, float))
        self.start_capital = float(_get_env('START_CAPITAL', 10000.0, float))
        self.use_limit_entry = str(_get_env('USAR_ORDEM_LIMITE', 'False')).strip().lower() in ['true', '1', 'yes']

        # Regime filter
        self.allowed_regimes = _parse_list(_get_env('ALLOWED_REGIMES', ''))

        self.last_trigger_time: Optional[pd.Timestamp] = None
        self.session = create_bybit_session()

    def _fetch_klines(self, symbol: str) -> pd.DataFrame:
        df = get_market_data_bybit(
            symbol,
            session=self.session,
            interval=self.interval,
            limit=self.limit,
            category='linear',
        )
        if df is None or df.empty:
            return pd.DataFrame()
        df = df.sort_values('time').reset_index(drop=True)
        return df

    def _get_last_candle_time(self, symbol: str) -> Optional[pd.Timestamp]:
        df = get_market_data_bybit(
            symbol,
            session=self.session,
            interval=self.interval,
            limit=2,
            category='linear',
        )
        if df is None or df.empty:
            return None
        df = df.sort_values('time').reset_index(drop=True)
        return df.iloc[-1]['time']

    def _btc_trigger(self, btc_df: pd.DataFrame, i: int) -> Optional[Trigger]:
        if i < self.btc_res_lookback:
            return None
        if i + 1 >= len(btc_df):
            return None

        row = btc_df.iloc[i]
        next_row = btc_df.iloc[i + 1]
        if pd.isna(row['atr']) or pd.isna(row['atr_mean']):
            return None

        resistance = btc_df['high'].iloc[i - self.btc_res_lookback:i].max()
        if resistance <= 0:
            return None

        close = float(row['close'])
        breakout = close > resistance * (1.0 + self.btc_close_above_pct)
        if not breakout:
            return None
        if (close - resistance) < (self.btc_breakout_atr_mult * float(row['atr'])):
            return None

        if float(row['atr']) > float(row['atr_mean']) * self.btc_vol_max_atr:
            return None

        next_body = abs(float(next_row['close']) - float(next_row['open']))
        next_range = max(float(next_row['high']) - float(next_row['low']), 1e-9)
        body_pct = next_body / next_range
        if body_pct < self.btc_follow_body_pct:
            return None
        if float(next_row['close']) < close:
            return None

        impulse = close - resistance
        max_pullback = close - (self.btc_pullback_max_pct * impulse)
        if float(next_row['low']) < max_pullback:
            return None

        return Trigger(time=row['time'], level=resistance)

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

        corr_short = win_short['ret_btc'].corr(win_short['ret_alt'])
        corr_long = win_long['ret_btc'].corr(win_long['ret_alt'])
        if pd.isna(corr_short) or pd.isna(corr_long):
            return None
        if corr_short < self.corr_min_short or corr_long < self.corr_min_long:
            return None

        best_lag = None
        best_corr = None
        btc_ret = win_lag['ret_btc'].to_numpy()
        alt_ret = win_lag['ret_alt'].to_numpy()
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

        btc_ret_series = win_long['ret_btc'].dropna().to_numpy()
        alt_ret_series = win_long['ret_alt'].dropna().to_numpy()
        if len(btc_ret_series) != len(alt_ret_series) or len(btc_ret_series) == 0:
            return None
        var_btc = np.var(btc_ret_series)
        if var_btc <= 0:
            return None
        cov = np.cov(btc_ret_series, alt_ret_series)[0, 1]
        beta = cov / var_btc
        if beta < self.beta_min or beta > self.beta_max:
            return None

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

        vol_avg = float(win_long['volume_alt'].mean())
        return score_corr, best_lag, score_beta, vol_avg

    def _pick_best_alt(self, alt_data: Dict[str, Dict[str, pd.DataFrame]], t: pd.Timestamp) -> Optional[ScoredAlt]:
        candidates = []
        for symbol, data in alt_data.items():
            merged = data['merged']
            scored = self._score_alt(merged, t)
            if not scored:
                continue
            score_corr, lag, score_beta, vol_avg = scored
            candidates.append((symbol, score_corr, lag, score_beta, vol_avg))

        if not candidates:
            return None

        vols = [c[4] for c in candidates]
        vmin, vmax = min(vols), max(vols)

        best = None
        best_score = -1.0
        for symbol, score_corr, lag, score_beta, vol_avg in candidates:
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
                best = ScoredAlt(
                    symbol=symbol,
                    score=total,
                    lag=lag,
                    score_corr=score_corr,
                    score_beta=score_beta,
                    score_liq=score_liq,
                )

        return best

    def _risk_params(self) -> Dict[str, float]:
        return {
            'atr_min': float(_get_env('ATR_MIN', 2.0, float)),
            'atr_mult': float(_get_env('ATR_MULT', 1.4, float)),
            'sl_pct_min': float(_get_env('SL_PCT_MIN', 0.007, float)),
            'sl_pct_max': float(_get_env('SL_PCT_MAX', 0.014, float)),
            'rrr': float(_get_env('RRR', 1.0, float)),
            'bb_dist_min': float(_get_env('BB_DIST_MIN', 40, float)),
            'bb_mult': float(_get_env('BB_MULT', 1.06, float)),
            'fixo_pct': float(_get_env('FIXO_PCT', 0.005, float)),
            'sr_lookback': int(_get_env('SR_LOOKBACK', 20, int)),
        }

    def _calc_qty(self, entry_price: float, stop_price: float) -> float:
        stop_dist = abs(entry_price - stop_price)
        if stop_dist <= 0:
            return 0.0
        saldo = get_saldo_disponivel('USDT')
        if not saldo or saldo <= 0:
            saldo = self.start_capital
        risk_usdt = saldo * (self.risk_pct / 100.0)
        qty = risk_usdt / stop_dist
        return max(qty, 0.0)

    def _build_alt_data(self, btc_df: pd.DataFrame) -> Dict[str, Dict[str, pd.DataFrame]]:
        alt_data: Dict[str, Dict[str, pd.DataFrame]] = {}
        for symbol in self.alts:
            alt_df = self._fetch_klines(symbol)
            if alt_df.empty:
                continue
            merged = pd.merge(
                btc_df,
                alt_df,
                on='time',
                suffixes=('_btc', '_alt'),
            )
            if merged.empty:
                continue
            merged = merged.set_index('time').sort_index()
            merged['ret_btc'] = merged['close_btc'].pct_change()
            merged['ret_alt'] = merged['close_alt'].pct_change()
            merged['atr_alt'] = _atr(
                merged.rename(columns={
                    'open_alt': 'open',
                    'high_alt': 'high',
                    'low_alt': 'low',
                    'close_alt': 'close',
                }),
                int(_get_env('ALT_ATR_WINDOW', 14, int)),
            )

            alt_ohlc = merged[[
                'open_alt', 'high_alt', 'low_alt', 'close_alt', 'volume_alt'
            ]].rename(columns={
                'open_alt': 'open',
                'high_alt': 'high',
                'low_alt': 'low',
                'close_alt': 'close',
                'volume_alt': 'volume',
            }).reset_index()

            alt_ind = calculate_indicators(alt_ohlc.copy())
            if alt_ind is None or alt_ind.empty:
                continue
            alt_ind = alt_ind.set_index('time').sort_index()

            alt_data[symbol] = {
                'merged': merged,
                'ind': alt_ind,
                'df': alt_ohlc,
            }
        return alt_data

    def run_once(self) -> None:
        if not self.session:
            self.logger.error('Bybit session unavailable')
            return

        btc_df = self._fetch_klines('BTCUSDT')
        if btc_df.empty or len(btc_df) < max(
            self.btc_res_lookback + 2,
            self.corr_window_long + 2,
            self.lag_update_window + 2,
            self.btc_atr_mean_window + 2,
        ):
            self.logger.info('BTC data not enough for lead-lag')
            return

        btc_df = btc_df.sort_values('time').reset_index(drop=True)
        btc_df['atr'] = _atr(btc_df, self.btc_atr_window)
        btc_df['atr_mean'] = btc_df['atr'].rolling(
            window=self.btc_atr_mean_window,
            min_periods=self.btc_atr_mean_window,
        ).mean()

        i = len(btc_df) - 2
        trigger = self._btc_trigger(btc_df, i)
        if not trigger:
            self.logger.info('BTC trigger not confirmed')
            return

        if self.last_trigger_time is not None and trigger.time <= self.last_trigger_time:
            self.logger.info('Trigger already processed')
            return

        btc_ind = calculate_indicators(btc_df.copy())
        if btc_ind is None or btc_ind.empty:
            self.logger.warning('BTC indicators unavailable')
            return

        try:
            regime_now = RegimeDetector().detectar(btc_ind, symbol='BTCUSDT')
        except Exception as exc:
            self.logger.warning(f'Regime detection failed: {exc}')
            regime_now = None

        if self.allowed_regimes and regime_now is not None:
            if str(regime_now) not in self.allowed_regimes:
                self.logger.info(f'Regime {regime_now} blocked by ALLOWED_REGIMES')
                return

        alt_data = self._build_alt_data(btc_df)
        if not alt_data:
            self.logger.info('No alt data available')
            return

        best = self._pick_best_alt(alt_data, trigger.time)
        if not best:
            self.logger.info('No alt candidate met score filters')
            return

        symbol = best.symbol
        if existe_ordem_aberta(self.session, symbol):
            self.logger.info(f'Open order/position exists for {symbol}')
            return

        merged = alt_data[symbol]['merged']
        if trigger.time not in merged.index:
            self.logger.info('Trigger time not in merged data')
            return

        entry_time = merged.index[-1]
        entry_price = float(merged.loc[entry_time]['close_alt'])

        ind = alt_data[symbol]['ind']
        risk_df = ind.loc[:entry_time].tail(200)
        stop, take, _, _ = (None, None, None, None)
        if not risk_df.empty:
            stop, take, _, _ = calcular_stop_take_adaptativo(
                risk_df.copy(), entry_price, 'buy', params=self._risk_params()
            )

        if stop is None or take is None:
            atr_alt = float(merged.loc[entry_time].get('atr_alt', 0.0) or 0.0)
            if atr_alt <= 0:
                self.logger.info('ATR fallback unavailable for stop/take')
                return
            stop = entry_price - (float(_get_env('ALT_ATR_MULT', 1.5, float)) * atr_alt)
            take = entry_price + (float(_get_env('ALT_RRR', 2.0, float)) * (entry_price - stop))

        qty = self._calc_qty(entry_price, stop)
        if qty <= 0:
            self.logger.info('Qty computed as zero, skipping trade')
            return

        self.logger.info(
            f'LEAD_LAG signal: {symbol} entry={entry_price:.4f} stop={stop:.4f} take={take:.4f} qty={qty:.6f}'
        )

        executar_ordem_completa(
            self.session,
            symbol=symbol,
            side='buy',
            qty=qty,
            entry_price=entry_price,
            stop_loss=stop,
            take_profit=take,
            strategy='lead_lag',
            order_type='Market',
            usar_ordem_limite=self.use_limit_entry,
        )

        self.last_trigger_time = trigger.time

    def run_forever(self) -> None:
        self.logger.info('Lead-lag runner started')
        tick = 0
        last_candle_time = None
        while True:
            tick += 1
            self.logger.info(f'Loop ativo {tick}: aguardando novo candle ou evento...')
            try:
                candle_time = self._get_last_candle_time('BTCUSDT')
                if candle_time is None:
                    self.logger.info('Falha ao obter candle BTC. Tentando novamente...')
                elif last_candle_time is None:
                    last_candle_time = candle_time
                    self.logger.info(f'⏳ Nenhum novo candle ({self.interval}m). Aguardando próximo fechamento...')
                elif candle_time <= last_candle_time:
                    self.logger.info(f'⏳ Nenhum novo candle ({self.interval}m). Aguardando próximo fechamento...')
                else:
                    last_candle_time = candle_time
                    self.logger.info('🆕 Novo candle detectado. Avaliando gatilho...')
                    self.run_once()
            except Exception as exc:
                self.logger.error(f'Lead-lag loop error: {exc}', exc_info=True)
            time.sleep(max(self.status_every, 1))


def _get_env(name: str, default=None, cast=None):
    raw = os.getenv(name)
    if raw is None or raw == '':
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
    return [x.strip().upper() for x in raw.split(',') if x.strip()]


def _true_range(df: pd.DataFrame) -> pd.Series:
    high = df['high']
    low = df['low']
    close = df['close'].shift(1)
    tr = pd.concat([
        high - low,
        (high - close).abs(),
        (low - close).abs(),
    ], axis=1).max(axis=1)
    return tr


def _atr(df: pd.DataFrame, window: int) -> pd.Series:
    tr = _true_range(df)
    return tr.rolling(window=window, min_periods=window).mean()


def run_lead_lag(env_path: str) -> None:
    runner = LeadLagRunner(env_path)
    runner.run_forever()
