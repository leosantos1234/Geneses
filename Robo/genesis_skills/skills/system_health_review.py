"""System Health Review Skill — operational diagnostics."""

from typing import Dict, Any
from genesis_skills.manifest import SkillManifest, SkillStatus, SkillCategory, SkillOutput, SkillConfidence
from genesis_skills.skills.base import BaseSkill, SkillResult
from genesis_skills.errors import SkillExecutionError


class SystemHealthReviewSkill(BaseSkill):
    """Comprehensive system health and operational diagnostics."""

    MANIFEST = SkillManifest(
        skill_id="system_health_review",
        name="System Health Review",
        version="1.0.0",
        status=SkillStatus.AVAILABLE,
        description="Produce diagnostic reading of Genesis operational state",
        category=SkillCategory.REVIEW,
        inputs=[],  # No inputs
        outputs=[
            SkillOutput(name="database", type="string"),
            SkillOutput(name="worker", type="string"),
            SkillOutput(name="mcp", type="string"),
            SkillOutput(name="anomalies", type="array"),
        ],
        allowed_mcp_tools=[
            "get_system_health",
            "get_risk_status",
            "list_open_positions",
            "list_decisions",
            "get_model_inventory",
        ],
        procedure="1. Check core system components (database, worker, MCP). 2. Retrieve risk management status. 3. Count open positions. 4. Check recent decision history. 5. Verify model inventory availability. 6. Classify system state as OPERATIONAL, DEGRADED, or NOT_OPERATIONAL. 7. Flag warnings for unhealthy components.",
        evidence_requirements=[
            "system_health accessible",
        ],
        success_criteria=[
            "all components checked",
            "anomalies identified",
        ],
        abort_conditions=[
            "MCP unavailable",
        ],
        limitations=[
            "Read-only diagnostics only",
            "Cannot perform repairs",
            "Snapshot at query time only",
        ],
        max_tool_calls=10,
        timeout_seconds=30,
    )

    async def execute(self, inputs: Dict[str, Any]) -> SkillResult:
        """Execute system health review."""
        self.validate_inputs(inputs)

        findings = []
        evidence = []
        data_gaps = []
        risk_flags = []

        try:
            # Step 1: Get system health
            health_response = await self.call_mcp_tool("get_system_health", {})

            if health_response.get("error"):
                return SkillResult(
                    skill_id=self.manifest.skill_id,
                    skill_version=self.manifest.version,
                    subject="system_health",
                    status="FAILED",
                    summary=f"System health unavailable: {health_response.get('error')}",
                    findings=[],
                    evidence=[],
                    data_gaps=["Cannot access system health"],
                )

            health = health_response.get("health", {})
            evidence.append(self._create_evidence("get_system_health", data=health))

            db_status = health.get("database", "UNKNOWN")
            worker_status = health.get("worker", "UNKNOWN")

            findings.append({
                "aspect": "core_components",
                "database": db_status,
                "worker": worker_status,
                "mcp": "healthy",
                "environment": health.get("environment", "UNKNOWN"),
            })

            if db_status != "healthy":
                risk_flags.append("Database not healthy")
            if worker_status != "running":
                risk_flags.append("Worker not running")

            # Step 2: Get risk status (optional source)
            try:
                risk_response = await self.call_mcp_tool("get_risk_status", {})

                if not risk_response.get("error"):
                    risk = risk_response.get("risk_status", {})
                    evidence.append(self._create_evidence("get_risk_status", data=risk))

                    findings.append({
                        "aspect": "risk_management",
                        "status": risk.get("status"),
                        "pnl": risk.get("pnl"),
                        "kill_switch": risk.get("kill_switch", "unknown"),
                    })
            except SkillExecutionError:
                data_gaps.append("Risk status unavailable (optional source)")

            # Step 3: Get open positions count (optional source)
            try:
                pos_response = await self.call_mcp_tool(
                    "list_open_positions",
                    {"limit": 100}
                )

                if not pos_response.get("error"):
                    positions = pos_response.get("positions", [])
                    evidence.append(self._create_evidence("list_open_positions", data={"count": len(positions)}))

                    findings.append({
                        "aspect": "open_positions",
                        "count": len(positions),
                    })
            except SkillExecutionError:
                data_gaps.append("Open positions unavailable (optional source)")

            # Step 4: Get recent decisions (optional source)
            try:
                dec_response = await self.call_mcp_tool(
                    "list_decisions",
                    {"limit": 10}
                )

                if not dec_response.get("error"):
                    decisions = dec_response.get("decisions", [])
                    evidence.append(self._create_evidence("list_decisions", data={"count": len(decisions)}))

                    findings.append({
                        "aspect": "decision_history",
                        "recent_decisions": len(decisions),
                    })
            except SkillExecutionError:
                data_gaps.append("Decision history unavailable (optional source)")

            # Step 5: Get model inventory (optional source)
            try:
                inv_response = await self.call_mcp_tool("get_model_inventory", {})

                if not inv_response.get("error"):
                    models = inv_response.get("models", [])
                    evidence.append(self._create_evidence("get_model_inventory", data={"count": len(models)}))

                    findings.append({
                        "aspect": "models",
                        "available_models": len(models),
                    })
            except SkillExecutionError:
                data_gaps.append("Model inventory unavailable (optional source)")

            # Compile summary
            summary = "Genesis Quant system status: "
            if db_status == "healthy" and worker_status == "running":
                summary += "OPERATIONAL"
            elif db_status == "healthy" or worker_status == "running":
                summary += "DEGRADED"
            else:
                summary += "NOT_OPERATIONAL"

            if risk_flags:
                summary += f" | {len(risk_flags)} warning(s)"

            # Determine status and confidence based on data gaps
            status = "PARTIAL" if len(data_gaps) > 0 else "SUCCESS"

            # Reduce confidence if optional sources failed
            if len(data_gaps) > 0:
                confidence = SkillConfidence.MEDIUM.value
            else:
                confidence = SkillConfidence.HIGH.value

            return SkillResult(
                skill_id=self.manifest.skill_id,
                skill_version=self.manifest.version,
                subject="system",
                status=status,
                summary=summary,
                findings=findings,
                evidence=evidence,
                data_gaps=data_gaps,
                confidence=confidence,
                risk_flags=risk_flags,
                recommendations=[
                    "No action needed" if not risk_flags else "Review flagged components for configuration or policy",
                ],
                limitations=self.manifest.limitations,
            )

        except Exception as e:
            return SkillResult(
                skill_id=self.manifest.skill_id,
                skill_version=self.manifest.version,
                subject="system",
                status="FAILED",
                summary=f"System review failed: {str(e)}",
                findings=[],
                evidence=evidence,
                data_gaps=[str(e)],
            )
