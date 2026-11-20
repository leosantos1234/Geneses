# Copilot Instructions - Trading Bot Genesis

## 🎯 System Overview
This is an automated cryptocurrency trading bot for Bybit exchange, focusing on **breakout trading strategies** with advanced risk management. The system operates in real-time using 5-minute candles (M5), with adaptive stop-loss/take-profit logic and AI-driven trade management.

## 🏗️ Architecture & Data Flow

### Core Components
1. **Entry Engine** ([trading_bot_geneses.py](trading_bot_geneses.py)) - Main orchestrator, strategy selection via backtest
2. **Order Execution** ([compra1.py](compra1.py)) - Places market orders with SL/TP via Bybit API
3. **Trade Monitoring** ([monitoramento.py](monitoramento.py)) - Real-time position management with trailing stops
4. **Trade Manager** ([gestor_de_trade.py](gestor_de_trade.py)) - Mid-trade adjustments and score evaluation

### Data Flow
```
Market Data (M5) → Indicators Calculation → Breakout Detection
    ↓
Strategy Backtest → Select Best Strategy → Entry Validation
    ↓
create_order() → Bybit API → Position Created
    ↓
monitorar_ordem_ate_encerrar() → Continuous Monitoring with Trailing Stop
    ↓
Trade Closure → SQLite Log (trades.db)
```

## 🔒 Critical State Management

### Trade State Persistence
The system uses **file-based locks** to prevent duplicate entries:
- `ordem_ativa_{SYMBOL}.flag` - Created on entry, removed on exit
- Always check `ordem_ainda_aberta()` before placing new orders

### `trade_state` Dictionary (monitoramento.py)
```python
trade_state = {
    "sl_inicial": float,        # Frozen at entry (NEVER recalculated)
    "tp_inicial": float,        # Frozen at entry
    "congelado": bool,          # Once True, SL/TP are immutable
    "sl_congelado_corretora": bool,  # Broker controls trailing
    "parcial_executada": bool,  # 30% profit-taking completed
    "fase": "entrada" | "pos_parcial",
    "entry_ts": float,          # Unix timestamp
    "atr_entry": float,         # ATR frozen at entry
    "imunidade_breakout": bool, # Breakout grace period active
    "modo": "breakout" | "tendencia"
}
```

**CRITICAL**: Once `congelado=True`, NEVER recalculate SL/TP. Use frozen values only.

## 🎛️ Environment Variables Pattern

### Multi-Asset Configuration
Each asset has its own `.env` file (`.env-btc`, `.env-eth`, etc.):
```bash
# Launch with specific asset config
python trading_bot_geneses.py .env-btc
```

### Key Variables
- `MODO_GESTAO` - "CORRETORA" (broker trailing) or "ROBO" (bot-controlled)
- `MODO_VALIDACAO` - "agressivo" (score≥3), "moderado" (≥8), "conservador" (≥10)
- `USAR_ORDEM_LIMITE` - "True" to enable professional limit order entry (default: False)
- `OFFSET_ENTRADA_ATR_FRAC` - ATR fraction for limit price offset (default: 0.10)
- `TIMEOUT_ENTRADA_SEGUNDOS` - Max seconds to wait for limit order fill (default: 180)
- `ATIVOS` - Trading pair (e.g., "BTCUSDT")
- `INTERVAL` - Timeframe (5 for 5-minute candles)
- `BREAKOUT_IMUNIDADE_ATR_FRAC` - Grace period tolerance (0.25 ATR)

## 🚨 Common Pitfalls & Solutions

### 1. SL/TP Validation Failure
**Problem**: Bybit rejects orders with SL ≥ entry_price (BUY) or SL ≤ entry_price (SELL)

**Solution**: Always validate before sending (see [monitoramento.py](monitoramento.py#L510-L540)):
```python
if side.lower() == "buy":
    if sl >= entry_price:
        sl = entry_price * 0.99  # Force valid SL
    if tp <= entry_price:
        tp = entry_price * 1.01
```

### 2. Trailing Stop Not Activating
**Check**:
- `MODO_GESTAO == "ROBO"` required for bot control
- Progress must reach `TRAILING_ACTIVATION_PCT` (default 30%)
- `trailing_offset` must be calculated from ATR and distance from entry

### 3. Multiple Entry Attempts
**Root Cause**: Lock file not cleaned up after exit

**Fix**: Always call `remove_lock_fisico(symbol)` in exception handlers:
```python
try:
    # Trade logic
finally:
    if os.path.exists(get_lock_path(symbol)):
        remove_lock_fisico(symbol)
```

## 🛠️ Development Workflows

### Running the Bot
```powershell
# Activate environment
.\venv_tf\Scripts\Activate.ps1

# Launch for specific asset
python trading_bot_geneses.py .env-btc

# Monitor logs
Get-Content -Path "trading_bot_geneses.log" -Wait
```

### Testing Strategy Without Real Orders
Use [teste_manual_breakout.py](teste_manual_breakout.py) with `simulador_breakout.py` API mock.

### Database Inspection
```powershell
sqlite3 trades.db "SELECT * FROM trades ORDER BY timestamp DESC LIMIT 10"
```

## 📦 Key Modules & Their Roles

### `utils/logica_stop_take_adaptativo.py`
Calculates SL/TP based on market context:
- **volatilidade_alta** → ATR-based stops (wider)
- **lateral** → Support/resistance levels
- **range_bollinger** → Bollinger band edges
- **tendencia_direcional** → Fixed percentage

### `utils/trade_transition_manager.py`
Manages breakout lifecycle (M5 timeframe):
- **EARLY** (0-15 min) - Requires 0.35 ATR move
- **FULL** (15-30 min) - Requires 0.60 ATR move
- **TIME_STOP** (>30 min) - Force exit if stagnant
- **MIGRAR_TENDENCIA** - Upgrade to trend-following

### `banco_trade/db_trades.py`
SQLite persistence with fields:
- `trade_id` (UUID), `symbol`, `side`, `entry_price`, `exit_price`
- `strategy`, `stop_loss`, `take_profit`, `trailing_stop`
- Technical indicators at entry: `rsi_entrada`, `atr_entrada`, etc.

## 🔧 Code Conventions

### Logging Standards
- `logger.info()` - Normal flow, decisions made
- `logger.warning()` - Recoverable issues (e.g., API retry)
- `logger.error()` - Failures requiring attention
- Use emojis for visual categorization: 🔥 (price), 🛡️ (protection), ⚠️ (warning)

### Entry Price Handling
ALWAYS retrieve `avgPrice` from Bybit positions API, never trust order response:
```python
pos = session.get_positions(category="linear", symbol=symbol)
entry_price = float(pos["result"]["list"][0].get("avgPrice", 0))
```

### Quantity Adjustments
Bybit enforces `minOrderQty` and `qtyStep`. Use:
```python
qty = ajustar_quantidade_para_minimo(session, symbol, qty)
```

## 🚀 Integration Points

### Bybit API ([compra1.py](compra1.py))
```python
from pybit.unified_trading import HTTP
session = HTTP(
    testnet=False,
    api_key=BYBIT_API_KEY,
    api_secret=BYBIT_API_SECRET
)
```

### Professional Limit Order Entry ([utils/ordem_limite_entrada.py](utils/ordem_limite_entrada.py))
Enable with `USAR_ORDEM_LIMITE=True` in `.env` file:
```python
# Entry separates INTENTION from EXECUTION
resultado = executar_entrada_por_limite(
    api=session,
    symbol=symbol,
    side=side,
    qty=qty,
    entry_price=breakout_price,
    atr=atr_value
)
# Returns None if timeout - no fees wasted on failed breakouts
```

**Benefits**:
- Avoids entering false breakouts
- Waits for price confirmation before committing
- Cancels automatically after timeout (default 180s)
- No fees on cancelled entries
- Better entry prices (limit with ATR offset)

### Telegram Notifications
```python
from utils.utils_telegram import send_telegram_message
send_telegram_message("🚀 Entry executed!")
```

### External AI Service (Optional)
```python
API_IA_URL = os.getenv("API_IA_URL", "http://localhost:8000")
# Used for sentiment analysis and ML predictions
```

## 📊 Monitoring & Debugging

### Real-Time Position Check
```python
def ordem_ainda_aberta(api, symbol):
    posicoes = api.get_positions(category="linear", symbol=symbol)
    for p in posicoes["result"]["list"]:
        if float(p.get("size", 0)) > 0:
            return True
    return False
```

### Trailing Stop Status
Check [monitoramento.py](monitoramento.py#L640-L650) logs:
```
[TRAILING STATUS] Ativado=True | Offset=120.50 | Progresso=42.3% | DistânciaEntrada=85.20
```

## 🎓 Learning Resources

- **Breakout Strategy**: See `estrategia_entrada/estrategia_breakout_filtros.py`
- **Backtesting**: `Backtest/backtesting_geral.py` with historical data
- **Risk Calculation**: `utils/risco_sl_tp.py` - ATR-based position sizing

---

**When in doubt**: Check `monitoramento.py` - it's the single source of truth for active trade management.
