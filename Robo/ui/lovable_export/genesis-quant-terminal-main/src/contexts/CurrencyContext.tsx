import { createContext, useContext, useEffect, useMemo, useState } from "react";

export type DisplayCurrency = "USD" | "BRL";
type CurrencyContextValue = { currency: DisplayCurrency; setCurrency: (currency: DisplayCurrency) => void; rate: number; formatMoney: (value?: number | null, options?: { sign?: boolean }) => string };
// Fallback de segurança: evita uma conversão claramente defasada caso a API
// esteja temporariamente indisponível. A cotação exibida informa a taxa usada.
const FALLBACK_USD_BRL = 5.95;
const CurrencyContext = createContext<CurrencyContextValue | null>(null);

export function CurrencyProvider({ children }: { children: React.ReactNode }) {
  const [currency, setCurrencyState] = useState<DisplayCurrency>(() => window.localStorage.getItem("genesis-display-currency") === "BRL" ? "BRL" : "USD");
  const [rate, setRate] = useState(FALLBACK_USD_BRL);
  useEffect(() => { window.localStorage.setItem("genesis-display-currency", currency); }, [currency]);
  useEffect(() => {
    let active = true;
    const loadRate = () => fetch("https://open.er-api.com/v6/latest/USD")
      .then((response) => response.ok ? response.json() : Promise.reject(new Error("fx unavailable")))
      .then((data: { rates?: { BRL?: number } }) => {
        const nextRate = data.rates?.BRL;
        if (active && nextRate && Number.isFinite(nextRate) && nextRate > 4 && nextRate < 8) setRate(nextRate);
      })
      .catch(() => undefined);
    loadRate();
    const timer = window.setInterval(loadRate, 15 * 60 * 1000);
    return () => { active = false; window.clearInterval(timer); };
  }, []);
  const value = useMemo<CurrencyContextValue>(() => ({
    currency,
    setCurrency: setCurrencyState,
    rate,
    formatMoney: (amount, options = {}) => {
      if (amount === undefined || amount === null || !Number.isFinite(amount)) return "—";
      const converted = currency === "BRL" ? amount * rate : amount;
      const sign = options.sign && converted >= 0 ? "+" : "";
      return `${sign}${new Intl.NumberFormat("pt-BR", { style: "currency", currency: currency === "BRL" ? "BRL" : "USD", minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(converted)}`;
    },
  }), [currency, rate]);
  return <CurrencyContext.Provider value={value}>{children}</CurrencyContext.Provider>;
}

export function useCurrency() {
  const context = useContext(CurrencyContext);
  if (!context) throw new Error("useCurrency must be used inside CurrencyProvider");
  return context;
}
