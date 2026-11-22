import { createContext, useContext, useEffect, useMemo, useState } from "react";

export type DisplayCurrency = "USD" | "BRL";
type CurrencyContextValue = { currency: DisplayCurrency; setCurrency: (currency: DisplayCurrency) => void; rate: number; formatMoney: (value?: number | null, options?: { sign?: boolean }) => string };
const FALLBACK_USD_BRL = 5;
const CurrencyContext = createContext<CurrencyContextValue | null>(null);

export function CurrencyProvider({ children }: { children: React.ReactNode }) {
  const [currency, setCurrencyState] = useState<DisplayCurrency>(() => window.localStorage.getItem("genesis-display-currency") === "BRL" ? "BRL" : "USD");
  const [rate, setRate] = useState(FALLBACK_USD_BRL);
  useEffect(() => { window.localStorage.setItem("genesis-display-currency", currency); }, [currency]);
  useEffect(() => {
    let active = true;
    fetch("https://api.frankfurter.app/latest?from=USD&to=BRL")
      .then((response) => response.ok ? response.json() : Promise.reject(new Error("fx unavailable")))
      .then((data: { rates?: { BRL?: number } }) => { if (active && data.rates?.BRL && Number.isFinite(data.rates.BRL)) setRate(data.rates.BRL); })
      .catch(() => undefined);
    return () => { active = false; };
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
