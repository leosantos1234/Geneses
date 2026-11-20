"""Risk Review Skill — comprehensive risk analysis."""

from typing import Dict, Any
from genesis_skills.manifest import SkillManifest, SkillStatus, SkillCategory, SkillOutput, SkillConfidence
from genesis_skills.skills.base import BaseSkill, SkillResult


class RiskReviewSkill(BaseSkill):
    """Analyze current risk state comprehensively."""

    MANIFEST = SkillManifest(
        skill_id="risk_review",
        name="Risk Review",
        version="1.0.0",
        status=SkillStatus.AVAILABLE,
        description="Produce analytical reading of Genesis risk state",
        category=SkillCategory.REVIEW,
        inputs=[],  # No inputs - analyzes current state
        outputs=[
            SkillOutput(name="risk_status", type="string", description="Allow/blocked/unknown"),
            SkillOutput(name="positions", type="array", description="Open positions"),
            SkillOutput(name="concentrations", type="object", description="Risk concentrations"),
            SkillOutput(name="anomalies", type="array", description="Unusual conditions"),
        ],
        allowed_mcp_tools=[
            "get_risk_status",
            "list_open_positions",
            "list_trades",
            "get_system_health",
        ],
        procedure="1. Retrieve current risk status from Risk Guard. 2. Enumerate open positions. 3. Calculate exposure and concentrations from positions. 4. Retrieve recent trade activity. 5. Check system health status. 6. Flag anomalies and concentrations. 7. Compile analytical findings without operational recommendations.",
        evidence_requirements=[
            "risk_guard status accessible",
            "position list accessible",
            "system health accessible",
        ],
        success_criteria=[
            "risk status retrieved",
            "positions enumerated",
            "concentrations calculated",
        ],
        abort_conditions=[
            "MCP unavailable",
            "PostgreSQL unavailable",
        ],
        limitations=[
            "Read-only analysis only",
            "Cannot change risk parameters",
            "Concentrations calculated from current snapshot only",
            "Real-time exchange exposure not available",
        ],
        max_tool_calls=8,
        timeout_seconds=30,
    )

    async def execute(self, inputs: Dict[str, Any]) -> SkillResult:
        """Execute risk review."""
        self.validate_inputs(inputs)

        findings = []
        evidence = []
        data_gaps = []
        risk_flags = []

        try:
            # Step 1: Get risk status
            risk_response = await self.call_mcp_tool("get_risk_status", {})

            if risk_response.get("error"):
                return SkillResult(
                    skill_id=self.manifest.skill_id,
                    skill_version=self.manifest.version,
                    subject="risk_state",
                    status="FAILED",
                    summary=f"Risk status unavailable: {risk_response.get('error')}",
                    findings=[],
                    evidence=[],
                    data_gaps=["Risk Guard not accessible"],
                )

            risk = risk_response.get("risk_status", {})
            evidence.append(self._create_evidence("get_risk_status", data=risk))

            risk_status = risk.get("status", "UNKNOWN")
            pnl = risk.get("pnl", 0)
            exposure = risk.get("exposure", 0)

            findings.append({
                "aspect": "risk_status",
                "status": risk_status,
                "pnl": pnl,
                "exposure": exposure,
                "limits": risk.get("limits", {}),
            })

            if risk_status == "blocked":
                risk_flags.append("Risk Guard is currently blocking operations")

            # Step 2: Get open positions
            pos_response = await self.call_mcp_tool(
                "list_open_positions",
                {"limit": 100}
            )

            if pos_response.get("error"):
                data_gaps.append(f"Cannot enumerate positions: {pos_response.get('error')}")
            else:
                positions = pos_response.get("positions", [])
                evidence.append(self._create_evidence("list_open_positions", data={"count": len(positions)}))

                # Calculate concentrations
                concentration_by_symbol = {}
                total_exposure = 0

                for pos in positions:
                    symbol = pos.get("symbol", "UNKNOWN")
                    size = pos.get("quantity", 0) * pos.get("current_price", 0)
                    total_exposure += size

                    if symbol not in concentration_by_symbol:
                        concentration_by_symbol[symbol] = 0
                    concentration_by_symbol[symbol] += size

                findings.append({
                    "aspect": "positions",
                    "count": len(positions),
                    "total_exposure": total_exposure,
                    "symbols": list(concentration_by_symbol.keys()),
                })

                # Calculate concentrations (no arbitrary thresholds)
                concentrations = []
                if total_exposure > 0:
                    for symbol, exp in concentration_by_symbol.items():
                        pct = (exp / total_exposure * 100) if total_exposure else 0
                        concentrations.append({
                            "symbol": symbol,
                            "exposure": exp,
                            "percentage": round(pct, 2),
                        })

                # Identify largest concentration for observation
                if concentrations:
                    largest = max(concentrations, key=lambda x: x["percentage"])
                    risk_flags.append(f"Largest observed concentration: {largest['symbol']} at {largest['percentage']}%")

                findings.append({
                    "aspect": "concentrations",
                    "note": "Observed position concentrations. No configured threshold was provided for classification.",
                    "observed_concentrations": concentrations,
                })

            # Step 3: Get recent trade activity
            trades_response = await self.call_mcp_tool(
                "list_trades",
                {"limit": 20}
            )

            if not trades_response.get("error"):
                trades = trades_response.get("trades", [])
                evidence.append(self._create_evidence("list_trades", data={"count": len(trades)}))

                # Count by status
                closed_count = sum(1 for t in trades if t.get("status") == "closed")
                open_count = sum(1 for t in trades if t.get("status") == "open")

                findings.append({
                    "aspect": "recent_activity",
                    "recent_trades": len(trades),
                    "open_trades": open_count,
                    "closed_trades": closed_count,
                })

            # Step 4: Get system health
            health_response = await self.call_mcp_tool("get_system_health", {})

            if not health_response.get("error"):
                health = health_response.get("health", {})
                evidence.append(self._create_evidence("get_system_health", data=health))

                db_status = health.get("database", "UNKNOWN")
                worker_status = health.get("worker", "UNKNOWN")

                findings.append({
                    "aspect": "system_health",
                    "database": db_status,
                    "worker": worker_status,
                    "mcp": "healthy",
                })

                if db_status != "healthy":
                    risk_flags.append("Database health warning")
                if worker_status != "running":
                    risk_flags.append("Worker not running")

            # Compile summary
            summary = f"Risk state: {risk_status}"
            if pnl:
                summary += f" | PnL: {pnl}"
            if exposure:
                summary += f" | Exposure: {exposure}"
            if risk_flags:
                summary += f" | {len(risk_flags)} flag(s)"

            return SkillResult(
                skill_id=self.manifest.skill_id,
                skill_version=self.manifest.version,
                subject="risk_state",
                status="SUCCESS",
                summary=summary,
                findings=findings,
                evidence=evidence,
                data_gaps=data_gaps,
                confidence=SkillConfidence.HIGH.value,
                risk_flags=risk_flags,
                recommendations=[
                    "Review observed concentrations against configured risk policy" if risk_flags else "No anomalies observed",
                ],
                limitations=self.manifest.limitations,
            )

        except Exception as e:
            return SkillResult(
                skill_id=self.manifest.skill_id,
                skill_version=self.manifest.version,
                subject="risk_state",
                status="FAILED",
                summary=f"Risk review failed: {str(e)}",
                findings=[],
                evidence=evidence,
                data_gaps=[str(e)],
            )
