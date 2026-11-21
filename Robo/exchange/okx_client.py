"""Cliente OKX V5 com resposta normalizada para o contrato legado do robô.

Esta primeira etapa é deliberadamente somente leitura. Operações que alteram
estado falham fechado até a camada de ordens ser validada separadamente.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

import requests

from exchange.write_guard import assert_exchange_write_allowed
from utils.resilience import call_with_backoff


class ExchangeCapabilityError(RuntimeError):
    pass


class OKXClient:
    def __init__(self) -> None:
        self.api_key = os.getenv("OKX_API_KEY")
        self.api_secret = os.getenv("OKX_API_SECRET")
        self.passphrase = os.getenv("OKX_API_PASSPHRASE")
        self.demo = os.getenv("OKX_DEMO_TRADING", "true").strip().lower() in (
            "1", "true", "yes", "on", "sim"
        )
        self.base_url = os.getenv("OKX_API_BASE_URL", "https://www.okx.com").rstrip("/")
        self.timeout = float(os.getenv("EXCHANGE_HTTP_TIMEOUT", "10"))
        self._leverage_configured = False
        self.http = requests.Session()
        if not all((self.api_key, self.api_secret, self.passphrase)):
            raise RuntimeError(
                "Credenciais OKX ausentes: OKX_API_KEY, OKX_API_SECRET e "
                "OKX_API_PASSPHRASE."
            )

    @staticmethod
    def _timestamp() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    def _headers(self, method: str, request_path: str, body: str = "") -> dict[str, str]:
        timestamp = self._timestamp()
        payload = timestamp + method.upper() + request_path + body
        signature = base64.b64encode(
            hmac.new(self.api_secret.encode(), payload.encode(), hashlib.sha256).digest()
        ).decode()
        headers = {
            "OK-ACCESS-KEY": self.api_key,
            "OK-ACCESS-SIGN": signature,
            "OK-ACCESS-TIMESTAMP": timestamp,
            "OK-ACCESS-PASSPHRASE": self.passphrase,
            "Content-Type": "application/json",
        }
        if self.demo:
            headers["x-simulated-trading"] = "1"
        return headers

    def _request(self, method: str, path: str, *, params: dict | None = None, payload: dict | None = None, private: bool = False) -> dict:
        return call_with_backoff(
            "okx",
            lambda: self._request_once(method, path, params=params, payload=payload, private=private),
            retry=method.upper() == "GET",
            operation_name=f"{method.upper()} {path}",
        )

    def _request_once(self, method: str, path: str, *, params: dict | None = None, payload: dict | None = None, private: bool = False) -> dict:
        query = urlencode([(key, value) for key, value in (params or {}).items() if value is not None])
        request_path = path + (f"?{query}" if query else "")
        body = json.dumps(payload or {}, separators=(",", ":")) if method.upper() != "GET" else ""
        headers = self._headers(method, request_path, body) if private else {"Content-Type": "application/json"}
        if private and self.demo:
            headers["x-simulated-trading"] = "1"
        response = self.http.request(
            method.upper(), self.base_url + request_path, headers=headers,
            data=body or None, timeout=self.timeout,
        )
        if not response.ok:
            try:
                details = response.json()
                message = details.get("msg") or details.get("code") or response.text[:200]
            except ValueError:
                message = response.text[:200]
            raise RuntimeError(f"OKX HTTP {response.status_code}: {message}")
        data = response.json()
        if str(data.get("code", "0")) != "0":
            raise RuntimeError(
                f"OKX API error {data.get('code')}: {data.get('msg') or data.get('sMsg') or ''} "
                f"response={data}"
            )
        return data

    @staticmethod
    def _inst_id(symbol: str, category: str = "linear") -> str:
        raw = symbol.upper().replace("/", "-")
        if "-" in raw:
            if raw.endswith(("-SWAP", "-SPOT")):
                return raw
            if category == "linear" and raw.endswith("-USDT"):
                return f"{raw}-SWAP"
            return raw
        if raw.endswith("USDT"):
            base = raw[:-4]
            return f"{base}-USDT-SWAP" if category == "linear" else f"{base}-USDT"
        return raw

    @staticmethod
    def _bar(interval: str) -> str:
        value = str(interval).lower().strip()
        aliases = {
            "60": "1H", "60m": "1H", "1h": "1H", "1hour": "1H",
            "120": "2H", "120m": "2H", "2h": "2H",
            "240": "4H", "240m": "4H", "4h": "4H",
            "360": "6H", "360m": "6H", "6h": "6H",
            "720": "12H", "720m": "12H", "12h": "12H",
            # A OKX usa `1D` para o candle diário. Sem este alias, o
            # fallback abaixo transforma `D`/`1D` em `dm`/`1dm`, que é
            # rejeitado pelo endpoint /api/v5/market/candles.
            "d": "1D", "1d": "1D", "1day": "1D",
        }
        if value in aliases:
            return aliases[value]
        if value.endswith("m"):
            return value
        return f"{value}m"

    @staticmethod
    def _bybit_response(rows: list) -> dict:
        return {"retCode": 0, "retMsg": "OK", "result": {"list": rows}}

    @staticmethod
    def _decimal_text(value, field: str) -> str:
        try:
            number = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError(f"Valor invalido para {field}: {value!r}") from exc
        if number <= 0:
            raise ValueError(f"Valor deve ser positivo para {field}: {value!r}")
        return format(number, "f")

    def build_order_payload(self, *, category="linear", symbol, side, qty, order_type="Market", price=None, reduce_only=False, pos_side=None, contract_size=None) -> dict:
        """Monta payload OKX sem enviar requisição.

        Para contratos perpétuos, ``qty`` precisa ser convertido para contratos
        usando ``contract_size``. A função recusa conversão implícita para
        impedir que uma quantidade em BTC seja interpretada como contratos.
        """
        if category != "linear":
            raise ValueError("A camada OKX atual suporta somente contratos lineares.")
        side_value = str(side).lower()
        if side_value in ("buy", "comprar", "long"):
            side_value = "buy"
        elif side_value in ("sell", "vender", "short"):
            side_value = "sell"
        else:
            raise ValueError(f"Lado invalido: {side!r}")
        size_mode = os.getenv("OKX_SIZE_MODE", "base").strip().lower()
        if size_mode == "contracts":
            size = self._decimal_text(qty, "qty")
        else:
            if not contract_size:
                raise ValueError("contract_size obrigatorio para converter quantidade em contratos OKX.")
            base_qty = Decimal(self._decimal_text(qty, "qty"))
            ct_val = Decimal(self._decimal_text(contract_size, "contract_size"))
            contracts = base_qty / ct_val
            if contracts != contracts.to_integral_value():
                raise ValueError(f"Quantidade {qty} nao representa contratos inteiros OKX.")
            size = format(contracts, "f")
        order_type_value = str(order_type).lower().replace("-", "_")
        if order_type_value in ("postonly", "post_only"):
            ord_type = "post_only"
        elif order_type_value == "market":
            ord_type = "market"
        else:
            ord_type = "limit"

        payload = {
            "instId": self._inst_id(symbol, category),
            "tdMode": os.getenv("OKX_TD_MODE", "isolated"),
            "side": side_value,
            "ordType": ord_type,
            "sz": size,
        }
        if payload["ordType"] in ("limit", "post_only"):
            payload["px"] = self._decimal_text(price, "price")
        if reduce_only:
            payload["reduceOnly"] = "true"
        if pos_side:
            payload["posSide"] = str(pos_side).lower()
        return payload

    def get_kline(self, *, category="linear", symbol, interval="60", limit=200, **_kwargs) -> dict:
        data = self._request("GET", "/api/v5/market/candles", params={
            "instId": self._inst_id(symbol, category), "bar": self._bar(interval), "limit": min(int(limit), 300),
        })
        rows = []
        for row in data.get("data", []):
            # OKX: ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm
            rows.append([row[0], row[1], row[2], row[3], row[4], row[5], row[7] if len(row) > 7 else row[6]])
        return self._bybit_response(rows)

    def get_tickers(self, *, category="linear", symbol=None, **_kwargs) -> dict:
        data = self._request("GET", "/api/v5/market/ticker", params={
            "instId": self._inst_id(symbol, category) if symbol else None,
        })
        rows = [{"symbol": item.get("instId"), "lastPrice": item.get("last")} for item in data.get("data", [])]
        return self._bybit_response(rows)

    def get_orderbook(self, *, category="linear", symbol, limit=25, **_kwargs) -> dict:
        """Retorna o melhor bid/ask no formato esperado pelos módulos legados."""
        data = self._request("GET", "/api/v5/market/books", params={
            "instId": self._inst_id(symbol, category),
            "sz": min(max(int(limit), 1), 400),
        })
        book = (data.get("data") or [{}])[0]
        bids = book.get("bids") or []
        asks = book.get("asks") or []
        return {
            "retCode": 0,
            "retMsg": "OK",
            "result": {"b": bids, "a": asks},
        }

    def get_server_time(self, **_kwargs) -> dict:
        data = self._request("GET", "/api/v5/public/time")
        return {"retCode": 0, "result": {"timeSecond": data.get("data", [{}])[0].get("ts")}}

    def get_instruments_info(self, *, category="linear", symbol=None, **_kwargs) -> dict:
        inst_type = "SWAP" if category == "linear" else "SPOT"
        data = self._request("GET", "/api/v5/public/instruments", params={"instType": inst_type})
        wanted = self._inst_id(symbol, category) if symbol else None
        rows = []
        for item in data.get("data", []):
            if wanted and item.get("instId") != wanted:
                continue
            rows.append({
                "symbol": item.get("instId"),
                "lotSizeFilter": {"qtyStep": item.get("lotSz"), "minOrderQty": item.get("minSz")},
                "priceFilter": {"tickSize": item.get("tickSz")},
                "contractSize": item.get("ctVal"),
            })
        return self._bybit_response(rows)

    def get_trade_fee(self, *, category="linear", symbol=None, **_kwargs) -> dict:
        """Consulta a taxa maker/taker efetiva da conta para o instrumento."""
        inst_type = "SWAP" if category == "linear" else str(category).upper()
        params = {"instType": inst_type}
        if symbol:
            inst = self._inst_id(symbol, category)
            if inst_type in ("SWAP", "FUTURES", "OPTION"):
                params["instFamily"] = inst.removesuffix("-SWAP")
            else:
                params["instId"] = inst
        data = self._request("GET", "/api/v5/account/trade-fee", params=params, private=True)
        return self._bybit_response(data.get("data", []))

    def get_funding_rate(self, *, category="linear", symbol, **_kwargs) -> dict:
        """Consulta a taxa de funding atual do perpétuo, sem autenticação."""
        data = self._request("GET", "/api/v5/public/funding-rate", params={
            "instId": self._inst_id(symbol, category),
        })
        return self._bybit_response(data.get("data", []))

    def get_funding_rate_history(self, *, category="linear", symbol,
                                 before=None, after=None, limit=100, **_kwargs) -> dict:
        """Consulta o histórico de funding para alimentar backtests por período."""
        params = {
            "instId": self._inst_id(symbol, category),
            "limit": min(int(limit), 100),
            "before": before,
            "after": after,
        }
        data = self._request("GET", "/api/v5/public/funding-rate-history", params=params)
        return self._bybit_response(data.get("data", []))

    def get_wallet_balance(self, *, accountType="UNIFIED", coin=None, **_kwargs) -> dict:
        data = self._request("GET", "/api/v5/account/balance", params={"ccy": coin}, private=True)
        rows = []
        for account in data.get("data", []):
            coins = []
            for item in account.get("details", []):
                equity = item.get("eq") or item.get("cashBal") or "0"
                available = item.get("availEq") or item.get("availBal") or "0"
                coins.append({
                    "coin": item.get("ccy"),
                    "walletBalance": equity,
                    "equity": equity,
                    "totalAvailableBalance": available,
                    "availableToWithdraw": available,
                })
            rows.append({
                "totalEquity": account.get("totalEq"),
                "totalAvailableBalance": account.get("adjEq") or account.get("availEq"),
                "coin": coins,
            })
        return self._bybit_response(rows)

    def get_funding_balance(self, *, coin=None, **_kwargs) -> dict:
        """Consulta o saldo parado na conta Funding da OKX."""
        data = self._request("GET", "/api/v5/asset/balances", params={"ccy": coin}, private=True)
        rows = []
        for item in data.get("data", []):
            balance = item.get("bal") or "0"
            available = item.get("availBal") or balance
            rows.append({
                "coin": item.get("ccy"),
                "walletBalance": balance,
                "equity": balance,
                "totalAvailableBalance": available,
                "availableToWithdraw": available,
            })
        return self._bybit_response(rows)

    def get_positions(self, *, category="linear", symbol=None, **_kwargs) -> dict:
        data = self._request("GET", "/api/v5/account/positions", params={
            "instType": "SWAP", "instId": self._inst_id(symbol, category) if symbol else None,
        }, private=True)
        rows = []
        for item in data.get("data", []):
            row = dict(item)
            row["symbol"] = item.get("instId")
            row["size"] = item.get("pos", "0")
            pos_side = str(item.get("posSide") or "").lower()
            try:
                raw_size = float(item.get("pos") or 0)
            except (TypeError, ValueError):
                raw_size = 0.0
            row["positionIdx"] = {"long": 1, "short": 2}.get(pos_side, 0)
            # Compatibilidade com os módulos legados de monitoramento.
            # A OKX chama o preço médio da posição de ``avgPx``;
            # os módulos antigos procuram ``entryPrice``/``avgPrice``.
            row["avgPrice"] = item.get("avgPx")
            row["entryPrice"] = item.get("avgPx")
            row["avgEntryPrice"] = item.get("avgPx")
            close_algos = item.get("closeOrderAlgo") or []
            if isinstance(close_algos, list) and close_algos:
                active_algo = next(
                    (algo for algo in close_algos
                     if str(algo.get("state") or "").lower() not in ("canceled", "cancelled", "order_failed")),
                    close_algos[0],
                )
                if active_algo.get("slTriggerPx") not in (None, "", "0"):
                    row["stopLoss"] = active_algo.get("slTriggerPx")
                if active_algo.get("tpTriggerPx") not in (None, "", "0"):
                    row["takeProfit"] = active_algo.get("tpTriggerPx")
            # Para a posição, o lado econômico é derivado de posSide.
            # Em hedge mode, ``side`` da ordem pode não existir na resposta.
            if pos_side in ("long", "short"):
                row["side"] = "Buy" if pos_side == "long" else "Sell"
            elif raw_size != 0:
                # Em modo net a OKX pode retornar posSide=net; o sinal de
                # pos informa a direção econômica da posição.
                row["side"] = "Sell" if raw_size < 0 else "Buy"
            rows.append(row)
        return self._bybit_response(rows)

    @staticmethod
    def _is_read_only() -> bool:
        return not (
            os.getenv("OKX_READ_ONLY", "true").strip().lower() in ("0", "false", "no", "off")
            and os.getenv("ENABLE_LIVE_TRADING", "false").strip().lower() in ("1", "true", "yes", "on", "sim")
            and os.getenv("ALLOW_EXCHANGE_WRITE", "false").strip().lower() in ("1", "true", "yes", "on", "sim")
            and os.getenv("GENESIS_LIVE_EXECUTION_AUTHORIZATION", "").strip() == "I_UNDERSTAND_REAL_TRADING"
        )

    def _ensure_writable(self, operation: str) -> None:
        if self._is_read_only():
            raise ExchangeCapabilityError(f"Operacao {operation} bloqueada: modo somente leitura.")
        assert_exchange_write_allowed(operation)

    def set_leverage(self, *, category="linear", symbol, leverage=None,
                     mgnMode=None, posSide=None, **_kwargs):
        """Aplica a alavancagem da conta para o instrumento antes da ordem."""
        self._ensure_writable("set_leverage")
        if category != "linear":
            raise ValueError("A configuracao de alavancagem atual suporta apenas linear.")
        value = self._decimal_text(leverage or os.getenv("LEVERAGE", "2"), "leverage")
        if Decimal(value) < 1 or Decimal(value) > 125:
            raise ValueError("A alavancagem deve estar entre 1x e 125x.")
        payload = {
            "instId": self._inst_id(symbol, category),
            "lever": value,
            "mgnMode": mgnMode or os.getenv("OKX_TD_MODE", "isolated"),
        }
        if posSide:
            payload["posSide"] = str(posSide).lower()
        data = self._request("POST", "/api/v5/account/set-leverage", payload=payload, private=True)
        return self._bybit_response(data.get("data", []))

    @staticmethod
    def _normalize_order_rows(data: dict) -> dict:
        rows = []
        for item in data.get("data", []):
            row = dict(item)
            if item.get("ordId"):
                row["orderId"] = item["ordId"]
            if item.get("algoId") and not row.get("orderId"):
                # O breakout usa o identificador do algo até o gatilho
                # disparar; exponha-o no mesmo campo legado.
                row["orderId"] = item["algoId"]
            if item.get("clOrdId"):
                row["orderLinkId"] = item["clOrdId"]
            row["symbol"] = item.get("instId")
            rows.append(row)
        return OKXClient._bybit_response(rows)

    def place_order(self, *, category="linear", symbol, side, order_type="Market", qty,
                    price=None, reduce_only=False, pos_side=None,
                    orderType=None, timeInForce=None, time_in_force=None,
                    orderLinkId=None, clOrdId=None, **_kwargs):
        self._ensure_writable("place_order")
        # Compatibilidade com os nomes usados pelos módulos legados.
        effective_order_type = orderType or order_type
        effective_time_in_force = timeInForce or time_in_force
        if str(effective_time_in_force or "").lower().replace("-", "") == "postonly":
            effective_order_type = "PostOnly"
        if os.getenv("OKX_AUTO_SET_LEVERAGE", "true").strip().lower() in ("1", "true", "yes", "on", "sim"):
            if not self._leverage_configured:
                self.set_leverage(category=category, symbol=symbol, leverage=os.getenv("LEVERAGE", "2"), posSide=pos_side)
                self._leverage_configured = True
        contract_size = _kwargs.get("contract_size")
        if category == "linear" and os.getenv("OKX_SIZE_MODE", "base").strip().lower() != "contracts" and not contract_size:
            info = self.get_instruments_info(category=category, symbol=symbol)
            rows = info.get("result", {}).get("list", []) if isinstance(info, dict) else []
            if rows:
                contract_size = rows[0].get("contractSize")
            if not contract_size:
                raise ValueError(f"contract_size indisponível para o instrumento OKX {symbol}.")

        payload = self.build_order_payload(
            category=category, symbol=symbol, side=side, qty=qty,
            order_type=effective_order_type, price=price, reduce_only=reduce_only,
            pos_side=pos_side, contract_size=contract_size,
        )
        client_order_id = clOrdId or orderLinkId
        if client_order_id:
            payload["clOrdId"] = str(client_order_id)
        data = self._request("POST", "/api/v5/trade/order", payload=payload, private=True)
        return self._normalize_order_rows(data)

    def place_algo_order(self, *, category="linear", symbol, side, qty,
                         trigger_price, order_price, trigger_by="last",
                         pos_side=None, order_link_id=None, **_kwargs):
        """Envia uma ordem condicional de entrada (trigger + limit) na OKX.

        O endpoint de ordens normais ignora ``triggerPrice``. Breakout precisa
        do endpoint ``order-algo`` para que o limite só seja criado depois do
        rompimento confirmado.
        """
        self._ensure_writable("place_algo_order")
        if category != "linear":
            raise ValueError("Ordens condicionais da camada OKX suportam apenas linear.")
        if str(side).lower() not in ("buy", "sell"):
            raise ValueError(f"Lado invalido: {side!r}")

        info = self.get_instruments_info(category=category, symbol=symbol)
        rows = info.get("result", {}).get("list", []) if isinstance(info, dict) else []
        item = rows[0] if rows else {}
        contract_size = item.get("contractSize")
        if not contract_size:
            raise ValueError(f"contract_size indisponivel para o instrumento OKX {symbol}.")

        size_mode = os.getenv("OKX_SIZE_MODE", "base").strip().lower()
        if size_mode == "contracts":
            size = self._decimal_text(qty, "qty")
        else:
            base_qty = Decimal(self._decimal_text(qty, "qty"))
            ct_val = Decimal(self._decimal_text(contract_size, "contract_size"))
            contracts = base_qty / ct_val
            if contracts != contracts.to_integral_value():
                raise ValueError(f"Quantidade {qty} nao representa contratos inteiros OKX.")
            size = format(contracts, "f")

        payload = {
            "instId": self._inst_id(symbol, category),
            "tdMode": os.getenv("OKX_TD_MODE", "isolated"),
            "side": str(side).lower(),
            "ordType": "trigger",
            "sz": size,
            "triggerPx": self._decimal_text(trigger_price, "trigger_price"),
            "orderPx": self._decimal_text(order_price, "order_price"),
            "triggerPxType": self._trigger_type(trigger_by),
        }
        if pos_side in ("long", "short"):
            payload["posSide"] = pos_side
        if order_link_id:
            payload["clOrdId"] = str(order_link_id)

        data = self._request("POST", "/api/v5/trade/order-algo", payload=payload, private=True)
        for item in data.get("data", []) or []:
            if str(item.get("sCode", "0")) != "0":
                raise RuntimeError(f"OKX ordem condicional rejeitada: {item.get('sCode')} {item.get('sMsg') or ''}".strip())
        return self._normalize_order_rows(data)

    def get_algo_order(self, *, category="linear", symbol, algoId=None, **_kwargs):
        if not algoId:
            raise ValueError("Informe algoId para consultar a ordem condicional.")
        params = {"instType": "SWAP" if category == "linear" else "SPOT",
                  "instId": self._inst_id(symbol, category), "algoId": str(algoId),
                  "ordType": "trigger"}
        for endpoint in ("/api/v5/trade/orders-algo-pending",
                         "/api/v5/trade/orders-algo-history"):
            data = self._request("GET", endpoint, params=params, private=True)
            rows = data.get("data", []) or []
            if rows:
                return self._normalize_order_rows(data)
        return self._bybit_response([])

    def get_order(self, *, category="linear", symbol, orderId=None, ordId=None,
                  orderLinkId=None, clOrdId=None, **_kwargs):
        order_id = ordId or orderId
        if not order_id and not (clOrdId or orderLinkId):
            raise ValueError("Informe orderId/ordId ou orderLinkId/clOrdId.")
        params = {"instId": self._inst_id(symbol, category)}
        if order_id:
            params["ordId"] = str(order_id)
        else:
            params["clOrdId"] = str(clOrdId or orderLinkId)
        data = self._request("GET", "/api/v5/trade/order", params=params, private=True)
        return self._normalize_order_rows(data)

    def wait_for_order(self, *, category="linear", symbol, orderId=None, ordId=None,
                       orderLinkId=None, clOrdId=None, timeout=30, interval=1, **_kwargs):
        import time
        deadline = time.monotonic() + float(timeout)
        last = None
        while time.monotonic() <= deadline:
            last = self.get_order(category=category, symbol=symbol, orderId=orderId,
                                  ordId=ordId, orderLinkId=orderLinkId, clOrdId=clOrdId)
            rows = last.get("result", {}).get("list", [])
            state = rows[0].get("state") if rows else None
            if state in {"filled", "canceled", "mmp_canceled", "order_failed"}:
                return last
            time.sleep(float(interval))
        return last or self._bybit_response([])

    def cancel_order(self, *, category="linear", symbol, orderId=None, ordId=None,
                     orderLinkId=None, clOrdId=None, **_kwargs):
        self._ensure_writable("cancel_order")
        order_id = ordId or orderId
        payload = {"instId": self._inst_id(symbol, category)}
        if order_id:
            payload["ordId"] = str(order_id)
        elif clOrdId or orderLinkId:
            payload["clOrdId"] = str(clOrdId or orderLinkId)
        else:
            raise ValueError("Informe orderId/ordId ou orderLinkId/clOrdId.")
        data = self._request("POST", "/api/v5/trade/cancel-order", payload=payload, private=True)
        return self._normalize_order_rows(data)

    def cancel_algo_order(self, *, category="linear", symbol, algoId=None,
                          algo_id=None, **_kwargs):
        """Cancela uma proteção algorítmica (TP/SL) existente na OKX."""
        self._ensure_writable("cancel_algo_order")
        identifier = algoId or algo_id
        if not identifier:
            raise ValueError("Informe algoId/algo_id para cancelar a proteção algorítmica.")
        payload = [{
            "instId": self._inst_id(symbol, category),
            "algoId": str(identifier),
        }]
        data = self._request("POST", "/api/v5/trade/cancel-algos", payload=payload, private=True)
        return self._normalize_order_rows(data)

    def amend_algo_order(self, *, category="linear", symbol, algoId=None,
                         algo_id=None, stopLoss=None, takeProfit=None,
                         slTriggerBy="LastPrice", tpTriggerBy="LastPrice",
                         **_kwargs):
        """Altera TP/SL de um algo existente sem cancelar sua proteção."""
        self._ensure_writable("amend_algo_order")
        identifier = algoId or algo_id
        if not identifier:
            raise ValueError("Informe algoId/algo_id para alterar a proteção algorítmica.")
        payload = {
            "instId": self._inst_id(symbol, category),
            "algoId": str(identifier),
            "cxlOnFail": False,
        }
        if stopLoss not in (None, "", 0, "0"):
            payload.update({
                "newSlTriggerPx": self._decimal_text(stopLoss, "stopLoss"),
                "newSlOrdPx": "-1",
                "newSlTriggerPxType": self._trigger_type(slTriggerBy),
            })
        if takeProfit not in (None, "", 0, "0"):
            payload.update({
                "newTpTriggerPx": self._decimal_text(takeProfit, "takeProfit"),
                "newTpOrdPx": "-1",
                "newTpTriggerPxType": self._trigger_type(tpTriggerBy),
            })
        data = self._request("POST", "/api/v5/trade/amend-algos", payload=payload, private=True)
        for item in data.get("data", []) or []:
            if str(item.get("sCode", "0")) != "0":
                raise RuntimeError(
                    f"OKX amend-algo rejeitado: {item.get('sCode')} {item.get('sMsg') or ''}".strip()
                )
        return self._normalize_order_rows(data)

    def get_pending_algo_orders(self, *, category="linear", symbol=None,
                                ordType=None, **_kwargs):
        """Retorna algos pendentes da OKX, incluindo trailing stops."""
        params = {"instType": "SWAP" if category == "linear" else "SPOT"}
        if symbol:
            params["instId"] = self._inst_id(symbol, category)
        if ordType:
            params["ordType"] = str(ordType)
        return self._request(
            "GET",
            "/api/v5/trade/orders-algo-pending",
            params=params,
            private=True,
        )

    def set_trailing_stop(self, *, category="linear", symbol,
                          callbackSpread=None, callbackRatio=None,
                          activePx=None, side=None, posSide=None,
                          replace_existing=False, **_kwargs):
        """Cria um trailing stop nativo (ordType=move_order_stop) na OKX."""
        self._ensure_writable("set_trailing_stop")
        if category != "linear":
            raise ValueError("Trailing da camada OKX atual suporta apenas linear.")
        has_spread = callbackSpread not in (None, "", 0, "0")
        has_ratio = callbackRatio not in (None, "", 0, "0")
        if has_spread == has_ratio:
            raise ValueError("Informe exatamente callbackSpread ou callbackRatio.")

        positions = self.get_positions(category=category, symbol=symbol).get("result", {}).get("list", [])
        position = next((item for item in positions if abs(float(item.get("size") or 0)) > 0), None)
        position_size = abs(float(position.get("size") or 0)) if position else 0.0
        if position:
            detected_pos_side = str(position.get("posSide") or "").lower()
            if posSide is None and detected_pos_side in ("long", "short"):
                posSide = detected_pos_side
            if side is None:
                side = "sell" if detected_pos_side == "long" else "buy"
                if detected_pos_side not in ("long", "short"):
                    side = "sell" if float(position.get("size") or 0) > 0 else "buy"
        if str(side or "").lower() not in ("buy", "sell"):
            raise ValueError("Nao foi possivel determinar o lado do trailing.")

        if replace_existing:
            pending = self.get_pending_algo_orders(
                category=category, symbol=symbol, ordType="move_order_stop"
            )
            for item in pending.get("data", []) or []:
                algo_id = item.get("algoId") or item.get("algo_id")
                if algo_id:
                    self.cancel_algo_order(category=category, symbol=symbol, algoId=algo_id)

        payload = {
            "instId": self._inst_id(symbol, category),
            "tdMode": os.getenv("OKX_TD_MODE", "isolated"),
            "side": str(side).lower(),
            "ordType": "move_order_stop",
            # Para trailing stop a OKX exige a quantidade em ``sz``.
            # ``closeFraction`` é aceito em alguns TP/SL conditional/oco,
            # mas não deve ser usado aqui.
            "reduceOnly": True,
        }
        if position_size <= 0:
            raise ValueError("Nao foi possivel determinar o tamanho da posicao para o trailing.")
        payload["sz"] = self._decimal_text(position_size, "sz")
        if posSide in ("long", "short"):
            payload["posSide"] = posSide
        if has_spread:
            payload["callbackSpread"] = self._decimal_text(callbackSpread, "callbackSpread")
        else:
            payload["callbackRatio"] = self._decimal_text(callbackRatio, "callbackRatio")
        if activePx not in (None, "", 0, "0"):
            payload["activePx"] = self._decimal_text(activePx, "activePx")

        data = self._request("POST", "/api/v5/trade/order-algo", payload=payload, private=True)
        for item in data.get("data", []) or []:
            if str(item.get("sCode", "0")) != "0":
                raise RuntimeError(
                    f"OKX trailing rejeitado: {item.get('sCode')} {item.get('sMsg') or ''}".strip()
                )
        return self._normalize_order_rows(data)

    def get_open_orders(self, *, category="linear", symbol=None, **kwargs):
        params = {"instType": "SWAP" if category == "linear" else "SPOT"}
        if symbol:
            params["instId"] = self._inst_id(symbol, category)
        order_id = kwargs.get("orderId") or kwargs.get("ordId")
        if order_id:
            params["ordId"] = str(order_id)
        data = self._request("GET", "/api/v5/trade/orders-pending", params=params, private=True)
        return self._normalize_order_rows(data)

    def cancel_all_orders(self, *, category="linear", symbol=None, **kwargs):
        """Cancela todas as ordens pendentes do instrumento na OKX."""
        self._ensure_writable("cancel_all_orders")
        pending = self.get_open_orders(category=category, symbol=symbol, **kwargs)
        if not isinstance(pending, dict) or pending.get("retCode") != 0:
            return pending

        rows = pending.get("result", {}).get("list", []) or []
        canceled = []
        for order in rows:
            order_id = order.get("orderId") or order.get("ordId")
            if not order_id:
                continue
            canceled.append(
                self.cancel_order(
                    category=category,
                    symbol=symbol,
                    orderId=order_id,
                )
            )
        return self._bybit_response(canceled)

    def get_order_history(self, *, category="linear", symbol=None, **kwargs):
        params = {"instType": "SWAP" if category == "linear" else "SPOT"}
        if symbol:
            params["instId"] = self._inst_id(symbol, category)
        order_id = kwargs.get("orderId") or kwargs.get("ordId")
        if order_id:
            params["ordId"] = str(order_id)
        if kwargs.get("limit"):
            params["limit"] = min(int(kwargs["limit"]), 100)
        data = self._request("GET", "/api/v5/trade/orders-history", params=params, private=True)
        return self._normalize_order_rows(data)

    def get_order_list(self, **kwargs):
        return self.get_open_orders(**kwargs)

    def set_trading_stop(self, *, category="linear", symbol, stopLoss=None,
                         takeProfit=None, slTriggerBy="LastPrice",
                         tpTriggerBy="LastPrice", positionIdx=None,
                         posSide=None, side=None, replace_existing=False,
                         **_kwargs):
        self._ensure_writable("set_trading_stop")
        if category != "linear":
            raise ValueError("Stop/take da camada OKX atual suporta apenas linear.")
        has_sl = stopLoss not in (None, "", 0, "0")
        has_tp = takeProfit not in (None, "", 0, "0")
        if not has_sl and not has_tp:
            raise ValueError("Informe stopLoss, takeProfit ou ambos.")
        position_size = 0.0
        positions = self.get_positions(category=category, symbol=symbol).get("result", {}).get("list", [])
        if side is None or posSide is None:
            position = next((item for item in positions if abs(float(item.get("size", 0) or 0)) > 0), None)
            if position:
                position_size = abs(float(position.get("size") or 0))
                detected_pos_side = str(position.get("posSide") or "").lower()
                if detected_pos_side in ("long", "short"):
                    posSide = posSide or detected_pos_side
                    side = side or ("sell" if detected_pos_side == "long" else "buy")
                elif side is None:
                    # Em modo net, a direção vem do sinal do tamanho.
                    size = float(position.get("size") or 0)
                    if size != 0:
                        side = "buy" if size > 0 else "sell"
                    # Não envia posSide=net: em net mode ele deve ser omitido.
                    posSide = None
        else:
            position = next((item for item in positions if abs(float(item.get("size", 0) or 0)) > 0), None)
            if position:
                position_size = abs(float(position.get("size") or 0))
        if side is None:
            raise ValueError("Nao foi possivel determinar o lado de fechamento da posicao.")
        payload = {
            "instId": self._inst_id(symbol, category),
            "tdMode": os.getenv("OKX_TD_MODE", "isolated"),
            "side": str(side).lower(),
            "ordType": "oco" if has_sl and has_tp else "conditional",
        }
        # A OKX exige exatamente um dos campos: ``sz`` ou ``closeFraction``.
        # Em modo net, o fechamento total usa closeFraction=1 + reduceOnly.
        # Em hedge mode, usa-se sz junto com posSide.
        if posSide in ("long", "short") and position_size > 0:
            payload["sz"] = self._decimal_text(position_size, "sz")
        else:
            payload["closeFraction"] = "1"
            payload["reduceOnly"] = True
        if posSide:
            payload["posSide"] = str(posSide).lower()
        if has_sl:
            payload.update({"slTriggerPx": self._decimal_text(stopLoss, "stopLoss"), "slOrdPx": "-1", "slTriggerPxType": self._trigger_type(slTriggerBy)})
        if has_tp:
            payload.update({"tpTriggerPx": self._decimal_text(takeProfit, "takeProfit"), "tpOrdPx": "-1", "tpTriggerPxType": self._trigger_type(tpTriggerBy)})

        # A OKX não atualiza um OCO existente ao receber outro order-algo;
        # é necessário cancelar a proteção anterior antes de substituí-la.
        # A opção é explícita para não cancelar proteções durante a entrada.
        active_algos = []
        previous_sl = None
        previous_tp = None
        for item in positions:
            for algo in item.get("closeOrderAlgo") or []:
                state = str(algo.get("state") or "").lower()
                algo_id = algo.get("algoId") or algo.get("algo_id")
                if algo_id and state not in ("canceled", "cancelled", "order_failed"):
                    active_algos.append(str(algo_id))
                    previous_sl = previous_sl or algo.get("slTriggerPx")
                    previous_tp = previous_tp or algo.get("tpTriggerPx")
        if replace_existing and active_algos:
            # Preferir amend-algos: a OKX mantém o OCO ativo mesmo se a nova
            # distância de breakeven/trailing for rejeitada.
            if hasattr(self, "amend_algo_order"):
                return self.amend_algo_order(
                    category=category,
                    symbol=symbol,
                    algoId=active_algos[0],
                    stopLoss=stopLoss,
                    takeProfit=takeProfit,
                    slTriggerBy=slTriggerBy,
                    tpTriggerBy=tpTriggerBy,
                )
            for algo_id in dict.fromkeys(active_algos):
                self.cancel_algo_order(category=category, symbol=symbol, algoId=algo_id)
            # O cancelamento do algo é assíncrono na OKX. Aguarda a posição
            # refletir a remoção antes de criar o novo OCO.
            for _ in range(5):
                time.sleep(0.25)
                refreshed = self.get_positions(category=category, symbol=symbol)
                refreshed_rows = refreshed.get("result", {}).get("list", []) or []
                active_remaining = any(
                    any(
                        (algo.get("algoId") or algo.get("algo_id"))
                        and str(algo.get("state") or "").lower()
                        not in ("canceled", "cancelled", "order_failed")
                        for algo in (item.get("closeOrderAlgo") or [])
                    )
                    for item in refreshed_rows
                )
                if not active_remaining:
                    break

        try:
            data = self._request("POST", "/api/v5/trade/order-algo", payload=payload, private=True)
        except Exception:
            # Fail-safe: se a substituição falhar depois de cancelar o OCO,
            # tenta restaurar imediatamente a proteção anterior.
            if replace_existing and (previous_sl or previous_tp):
                restore = dict(payload)
                restore.pop("slTriggerPx", None)
                restore.pop("tpTriggerPx", None)
                restore.pop("slTriggerPxType", None)
                restore.pop("tpTriggerPxType", None)
                if previous_sl not in (None, "", "0"):
                    restore.update({
                        "slTriggerPx": self._decimal_text(previous_sl, "stopLoss"),
                        "slOrdPx": "-1",
                        "slTriggerPxType": self._trigger_type(slTriggerBy),
                    })
                if previous_tp not in (None, "", "0"):
                    restore.update({
                        "tpTriggerPx": self._decimal_text(previous_tp, "takeProfit"),
                        "tpOrdPx": "-1",
                        "tpTriggerPxType": self._trigger_type(tpTriggerBy),
                    })
                try:
                    self._request("POST", "/api/v5/trade/order-algo", payload=restore, private=True)
                except Exception:
                    pass
            raise
        for item in data.get("data", []) or []:
            if str(item.get("sCode", "0")) != "0":
                raise RuntimeError(
                    f"OKX order-algo rejeitado: {item.get('sCode')} {item.get('sMsg') or ''}".strip()
                )
        return self._normalize_order_rows(data)

    @staticmethod
    def _trigger_type(value) -> str:
        raw = str(value or "last").lower().replace("price", "")
        return {"last": "last", "mark": "mark", "index": "index"}.get(raw, "last")

    def get_closed_pnl(self, *, category="linear", symbol=None, **kwargs):
        params = {"instType": "SWAP" if category == "linear" else "SPOT"}
        if symbol:
            params["instId"] = self._inst_id(symbol, category)
        if kwargs.get("limit"):
            params["limit"] = min(int(kwargs["limit"]), 100)
        data = self._request("GET", "/api/v5/account/positions-history", params=params, private=True)
        return self._normalize_order_rows(data)
