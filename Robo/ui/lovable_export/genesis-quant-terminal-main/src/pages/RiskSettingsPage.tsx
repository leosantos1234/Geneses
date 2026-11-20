import { useState, useMemo } from "react";

interface RiskParams {
  TP_DECISION_ZONE_PCT: number;
  TP_FORCE_EXIT_ZONE_PCT: number;
  TP_EXTENSION_STEP_PCT: number;
  TRAILING_ACTIVATION_PCT: number;
  BE_ACTIVATION_PCT: number;
}

const defaultParams: RiskParams = {
  TP_DECISION_ZONE_PCT: 0.8,
  TP_FORCE_EXIT_ZONE_PCT: 0.95,
  TP_EXTENSION_STEP_PCT: 0.5,
  TRAILING_ACTIVATION_PCT: 0.6,
  BE_ACTIVATION_PCT: 0.3,
};

const fieldLabels: Record<keyof RiskParams, string> = {
  TP_DECISION_ZONE_PCT: "TP Decision Zone (%)",
  TP_FORCE_EXIT_ZONE_PCT: "TP Force Exit Zone (%)",
  TP_EXTENSION_STEP_PCT: "TP Extension Step (%)",
  TRAILING_ACTIVATION_PCT: "Trailing Activation (%)",
  BE_ACTIVATION_PCT: "BE Activation (%)",
};

export default function RiskSettingsPage() {
  const [params, setParams] = useState<RiskParams>(defaultParams);

  const handleChange = (key: keyof RiskParams, value: string) => {
    const num = parseFloat(value);
    if (!isNaN(num)) {
      setParams((prev) => ({ ...prev, [key]: num }));
    }
  };

  const json = useMemo(() => JSON.stringify(params, null, 2), [params]);

  return (
    <div className="max-w-2xl space-y-6 animate-slide-in">
      <div>
        <h2 className="text-sm font-medium text-muted-foreground uppercase tracking-wider mb-1">Parâmetros de Risco</h2>
        <p className="text-xs text-muted-foreground">Configuração dos parâmetros de risco do sistema. (Somente UI na V1)</p>
      </div>

      <div className="space-y-4">
        {(Object.keys(fieldLabels) as (keyof RiskParams)[]).map((key) => (
          <div key={key} className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
              {fieldLabels[key]}
            </label>
            <input
              type="number"
              step="0.01"
              value={params[key]}
              onChange={(e) => handleChange(key, e.target.value)}
              className="h-9 w-full rounded-md border border-border bg-input px-3 text-sm font-mono-nums text-foreground focus:outline-none focus:ring-1 focus:ring-ring"
            />
          </div>
        ))}
      </div>

      <div>
        <h3 className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-2">JSON Preview</h3>
        <pre className="rounded-lg border border-border bg-muted/50 p-4 text-xs font-mono text-foreground overflow-x-auto">
          {json}
        </pre>
      </div>

      <button
        onClick={() => alert("Funcionalidade de salvar será implementada na V2.")}
        className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 transition-colors"
      >
        Salvar Parâmetros
      </button>
    </div>
  );
}
