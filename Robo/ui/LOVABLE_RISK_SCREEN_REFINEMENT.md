Refine only the `/settings/risk` screen of the Genesis Quant frontend.
Do not change other routes.

Goal:
- Make this screen look like a professional risk desk tool, not a basic form.
- Keep V1 as UI-only (no backend persistence yet).

Visual direction:
- Premium dark trading desk style.
- Clear visual hierarchy, compact spacing, strong typography.
- Responsive (desktop + mobile).

Layout:
1. Header block
- Title: `Parâmetros de Risco`
- Subtitle: `Configuração operacional de saída e proteção`
- Right side: badge with current profile (`Conservador`, `Moderado`, `Agressivo`).

2. Main grid (2 columns on desktop, 1 on mobile)
- Left: parameter cards grouped by objective
  - Group A `Proteção de Lucro`
    - `TP_DECISION_ZONE_PCT`
    - `TP_FORCE_EXIT_ZONE_PCT`
    - `TP_EXTENSION_STEP_PCT`
  - Group B `Proteção de Capital`
    - `TRAILING_ACTIVATION_PCT`
    - `BE_ACTIVATION_PCT`
- Right: impact panel
  - `Resumo Operacional` in natural language
  - `Alertas de configuração` (if values are too aggressive or inconsistent)
  - collapsed/expandable `JSON técnico`

Component behavior for each parameter:
- Use slider + numeric input synchronized.
- Show min/recommended/max ranges.
- Show inline helper text explaining operational effect.
- Show warning state when outside recommended range.

Recommended ranges:
- `TP_DECISION_ZONE_PCT`: min 0.80, recommended 0.90-0.98, max 0.99
- `TP_FORCE_EXIT_ZONE_PCT`: min 0.85, recommended 0.93-0.99, max 0.995
- `TP_EXTENSION_STEP_PCT`: min 0.02, recommended 0.05-0.20, max 0.50
- `TRAILING_ACTIVATION_PCT`: min 0.20, recommended 0.40-0.80, max 0.95
- `BE_ACTIVATION_PCT`: min 0.10, recommended 0.30-0.70, max 0.90

Profile logic (derived from current values):
- `Conservador`: force exit high, extension step low, trailing/be not too cedo.
- `Moderado`: balanced ranges.
- `Agressivo`: extension step high and activation thresholds very early.

Operational summary examples:
- If values are conservative:
  - `Sistema prioriza realização perto do alvo e menor risco de devolução de lucro.`
- If aggressive:
  - `Sistema prioriza capturar tendência longa, com maior exposição a pullbacks.`

Validation rules:
- `TP_FORCE_EXIT_ZONE_PCT` must be >= `TP_DECISION_ZONE_PCT`
- show clear error if violated.
- disable `Apply` button when invalid.

Actions:
- `Reset to defaults`
- `Apply (UI only)`
- `Export JSON`

Default values:
```json
{
  "TP_DECISION_ZONE_PCT": 0.95,
  "TP_FORCE_EXIT_ZONE_PCT": 0.95,
  "TP_EXTENSION_STEP_PCT": 0.12,
  "TRAILING_ACTIVATION_PCT": 0.60,
  "BE_ACTIVATION_PCT": 0.30
}
```

Technical constraints:
- Keep strong TypeScript types for form state.
- Reuse existing design system components.
- Show loading skeletons and empty/error states where applicable.

