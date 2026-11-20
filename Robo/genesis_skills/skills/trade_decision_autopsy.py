"""Trade Decision Autopsy Skill — explain a trade decision retrospectively."""

from typing import Dict, Any, List, Optional
from genesis_skills.manifest import SkillManifest, SkillStatus, SkillCategory, SkillInput, SkillOutput, SkillConfidence
from genesis_skills.skills.base import BaseSkill, SkillResult


class TradeDecisionAutopsySkill(BaseSkill):
    """Analyze a historical trade decision in detail."""

    MANIFEST = SkillManifest(
        skill_id="trade_decision_autopsy",
        name="Trade Decision Autopsy",
        version="1.0.0",
        status=SkillStatus.AVAILABLE,
        description="Explain retrospectively what happened in a trade and why",
        category=SkillCategory.ANALYSIS,
        inputs=[
            SkillInput(
                name="trade_id",
                type="string",
                required=True,
                description="ID of the trade to analyze"
            ),
            SkillInput(
                name="verbose",
                type="boolean",
                required=False,
                default=False,
                description="Include detailed decision trace"
            ),
        ],
        outputs=[
            SkillOutput(name="trade_id", type="string", description="Trade analyzed"),
            SkillOutput(name="strategy", type="string", description="Strategy used"),
            SkillOutput(name="decision_lineage", type="object", description="Decision path"),
            SkillOutput(name="risk_status", type="object", description="Risk state at trade"),
            SkillOutput(name="lstm_input", type="object", description="LSTM signals involved"),
        ],
        allowed_mcp_tools=[
            "get_trade_detail",
            "get_decision_trace",
            "get_commander_decision",
            "get_lstm_predictions",
            "get_risk_status",
            "list_trades",
        ],
        procedure="1. Retrieve trade detail by ID. 2. Extract decision lineage if available. 3. Get Commander decision evidence. 4. Capture risk state snapshot. 5. Include LSTM signals if verbose mode enabled. 6. Compile findings with temporal distinctions between historical and current state.",
        evidence_requirements=[
            "trade must exist in genesis_trading",
            "decision_id if available",
            "commander decision if available",
        ],
        success_criteria=[
            "trade_detail retrieved",
            "decision chain traced when available",
            "no fabricated lineage for legacy trades",
        ],
        abort_conditions=[
            "trade_id not found in database",
            "MCP unavailable",
        ],
        limitations=[
            "Legacy trades may have incomplete decision trace",
            "LSTM data may not be available for all periods",
            "Only historical analysis, no prediction",
        ],
        max_tool_calls=10,
        timeout_seconds=30,
    )

    async def execute(self, inputs: Dict[str, Any]) -> SkillResult:
        """Execute trade decision autopsy."""
        # Validate inputs
        self.validate_inputs(inputs)
        trade_id = inputs["trade_id"]
        verbose = inputs.get("verbose", False)

        findings = []
        evidence = []
        data_gaps = []
        risk_flags = []

        try:
            # Step 1: Get trade detail
            trade_response = await self.call_mcp_tool(
                "get_trade_detail",
                {"trade_id": trade_id}
            )

            if trade_response.get("error"):
                return SkillResult(
                    skill_id=self.manifest.skill_id,
                    skill_version=self.manifest.version,
                    subject=trade_id,
                    status="FAILED",
                    summary=f"Trade not found: {trade_response.get('error')}",
                    findings=[],
                    evidence=[],
                    data_gaps=[f"trade {trade_id} not in database"],
                )

            trade = trade_response.get("trade", {})
            evidence.append(self._create_evidence("get_trade_detail", data=trade))

            # Extract key information
            symbol = trade.get("symbol", "UNKNOWN")
            strategy = trade.get("strategy", "UNKNOWN")
            entry_price = trade.get("entry_price")
            exit_price = trade.get("exit_price")
            pnl = trade.get("pnl", 0)
            status = trade.get("status")

            findings.append({
                "aspect": "basic_trade_info",
                "symbol": symbol,
                "strategy": strategy,
                "status": status,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "pnl": pnl,
            })

            # Step 2: Get decision trace
            decision_id = trade.get("decision_id")
            if decision_id:
                try:
                    trace_response = await self.call_mcp_tool(
                        "get_decision_trace",
                        {"decision_id": decision_id}
                    )

                    if not trace_response.get("error"):
                        trace = trace_response.get("trace", {})
                        evidence.append(self._create_evidence("get_decision_trace", request_id=decision_id, data=trace))

                        findings.append({
                            "aspect": "decision_lineage",
                            "decision_id": decision_id,
                            "context": trace.get("context"),
                            "signals": trace.get("signals", []),
                            "thresholds": trace.get("thresholds", {}),
                        })
                    else:
                        data_gaps.append(f"Decision trace unavailable for {decision_id}")
                except Exception as e:
                    data_gaps.append(f"Error retrieving decision trace: {str(e)}")
            else:
                data_gaps.append("LEGACY_DATA_NO_LINEAGE")

            # Step 3: Get Commander decision if available
            try:
                cmd_response = await self.call_mcp_tool(
                    "get_commander_decision",
                    {"trade_id": trade_id}
                )

                if not cmd_response.get("error"):
                    cmd = cmd_response.get("decision", {})
                    evidence.append(self._create_evidence("get_commander_decision", data=cmd))

                    # Verify decision_id matches to avoid attributing unrelated decisions
                    cmd_decision_id = cmd.get("decision_id")
                    if cmd_decision_id and cmd_decision_id != decision_id:
                        risk_flags.append(f"DECISION_LINEAGE_MISMATCH: commander decision_id {cmd_decision_id} differs from trade decision_id {decision_id}")
                        data_gaps.append("Commander decision does not match trade lineage")
                    else:
                        findings.append({
                            "aspect": "commander_decision",
                            "decision": cmd.get("decision"),
                            "confidence": cmd.get("confidence"),
                            "reason": cmd.get("reason"),
                        })
            except Exception:
                pass  # Commander decision optional

            # Step 4: Get current risk status (note: this is current state, not historical)
            try:
                risk_response = await self.call_mcp_tool(
                    "get_risk_status",
                    {}
                )

                if not risk_response.get("error"):
                    risk = risk_response.get("risk_status", {})
                    evidence.append(self._create_evidence("get_risk_status", data=risk, note="current_system_state"))

                    if risk.get("status") == "blocked":
                        risk_flags.append("Current risk status: blocked (note: this is current state, not historical state at trade time)")

                    findings.append({
                        "aspect": "current_risk_state",
                        "note": "This is the current system risk status. It does not establish the risk state at the time of the historical trade.",
                        "status": risk.get("status"),
                        "pnl": risk.get("pnl"),
                        "exposure": risk.get("exposure"),
                    })
                    data_gaps.append("Historical risk state at trade time not available")
            except Exception as e:
                # Risk context optional but log unexpected errors
                data_gaps.append(f"Risk status error: {str(e)}")

            # Step 5: Get LSTM predictions if available
            if verbose:
                try:
                    lstm_response = await self.call_mcp_tool(
                        "get_lstm_predictions",
                        {"symbol": symbol, "limit": 1}
                    )

                    if not lstm_response.get("error"):
                        predictions = lstm_response.get("predictions", [])
                        if predictions:
                            pred = predictions[0]
                            evidence.append(self._create_evidence("get_lstm_predictions", data=pred))

                            # Check temporal relationship to avoid future-data leakage
                            pred_timestamp = pred.get("timestamp")
                            trade_timestamp = trade.get("timestamp")

                            temporal_note = ""
                            if pred_timestamp and trade_timestamp:
                                try:
                                    from datetime import datetime
                                    pred_time = datetime.fromisoformat(pred_timestamp) if isinstance(pred_timestamp, str) else pred_timestamp
                                    trade_time = datetime.fromisoformat(trade_timestamp) if isinstance(trade_timestamp, str) else trade_timestamp
                                    if pred_time > trade_time:
                                        temporal_note = " [RETROSPECTIVE: prediction after trade]"
                                        data_gaps.append("LSTM prediction timestamp after trade — retrospective evidence only")
                                except:
                                    temporal_note = " [TEMPORAL: timestamp unparseable]"
                                    data_gaps.append("LSTM prediction timestamp cannot be verified for ordering")

                            findings.append({
                                "aspect": "lstm_signals",
                                "predicted_price": pred.get("predicted_price"),
                                "direction": pred.get("direction_predicted"),
                                "confidence": pred.get("confidence_score"),
                                "temporal_note": temporal_note if temporal_note else "within decision timeframe",
                            })
                except Exception:
                    pass  # LSTM optional

            # Compile summary
            summary = f"Trade autopsy for {symbol}: {strategy} ({status})"
            if pnl:
                summary += f" | PnL: {pnl}"

            if data_gaps:
                summary += " | Data gaps present"

            # Determine confidence based on evidence quality
            if decision_id:
                confidence = SkillConfidence.HIGH.value
            else:
                confidence = SkillConfidence.MEDIUM.value

            return SkillResult(
                skill_id=self.manifest.skill_id,
                skill_version=self.manifest.version,
                subject=trade_id,
                status="SUCCESS",
                summary=summary,
                findings=findings,
                evidence=evidence,
                data_gaps=data_gaps,
                confidence=confidence,
                risk_flags=risk_flags,
                recommendations=[
                    "Review decision trace for pattern matching",
                    "Compare with similar historical trades",
                ] if decision_id else [],
                limitations=self.manifest.limitations,
            )

        except Exception as e:
            return SkillResult(
                skill_id=self.manifest.skill_id,
                skill_version=self.manifest.version,
                subject=trade_id,
                status="FAILED",
                summary=f"Autopsy failed: {str(e)}",
                findings=[],
                evidence=evidence,
                data_gaps=[str(e)],
            )
