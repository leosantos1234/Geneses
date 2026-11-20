"""LSTM Diagnostics Skill — analyze model predictions."""

from typing import Dict, Any
import math
from genesis_skills.manifest import SkillManifest, SkillStatus, SkillCategory, SkillInput, SkillOutput, SkillConfidence
from genesis_skills.skills.base import BaseSkill, SkillResult


class LSTMDiagnosticsSkill(BaseSkill):
    """Diagnose LSTM model behavior from historical predictions."""

    MANIFEST = SkillManifest(
        skill_id="lstm_diagnostics",
        name="LSTM Diagnostics",
        version="1.0.0",
        status=SkillStatus.AVAILABLE,
        description="Analyze model prediction accuracy and behavior",
        category=SkillCategory.DIAGNOSTIC,
        inputs=[
            SkillInput(
                name="model_name",
                type="string",
                required=False,
                description="Model to analyze (all if not specified)"
            ),
            SkillInput(
                name="symbol",
                type="string",
                required=False,
                description="Symbol to focus on"
            ),
            SkillInput(
                name="lookback_periods",
                type="integer",
                required=False,
                default=30,
                description="Number of periods to analyze"
            ),
        ],
        outputs=[
            SkillOutput(name="model_name", type="string"),
            SkillOutput(name="accuracy", type="number"),
            SkillOutput(name="drift", type="number"),
            SkillOutput(name="predictions_analyzed", type="integer"),
        ],
        allowed_mcp_tools=[
            "get_lstm_predictions",
            "get_model_inventory",
            "list_trades",
            "get_trade_detail",
        ],
        procedure="1. Retrieve model inventory. 2. Fetch LSTM predictions for specified window. 3. Analyze directional accuracy. 4. Calculate average confidence and error metrics. 5. Detect drift in recent predictions vs historical average. 6. Report findings without model training or inference.",
        evidence_requirements=[
            "model inventory available",
            "recent predictions available",
        ],
        success_criteria=[
            "predictions retrieved",
            "accuracy calculated",
            "no inference triggered",
        ],
        abort_conditions=[
            "MCP unavailable",
            "No predictions available",
        ],
        limitations=[
            "Analysis only, no training or inference",
            "Window selection based on stored predictions only",
            "Requires minimum 10 predictions for statistical validity",
            "Cannot differentiate ACTIVE/INACTIVE by file age alone",
        ],
        max_tool_calls=10,
        timeout_seconds=30,
    )

    async def execute(self, inputs: Dict[str, Any]) -> SkillResult:
        """Execute LSTM diagnostics."""
        self.validate_inputs(inputs)

        model_name = inputs.get("model_name")
        symbol = inputs.get("symbol")
        lookback = inputs.get("lookback_periods", 30)

        findings = []
        evidence = []
        data_gaps = []

        try:
            # Step 1: Get model inventory
            inv_response = await self.call_mcp_tool("get_model_inventory", {})

            if inv_response.get("error"):
                data_gaps.append(f"Model inventory unavailable: {inv_response.get('error')}")
            else:
                models = inv_response.get("models", [])
                evidence.append(self._create_evidence("get_model_inventory", data={"count": len(models)}))

                findings.append({
                    "aspect": "model_inventory",
                    "total_models": len(models),
                    "models": [m.get("name") for m in models],
                })

            # Step 2: Get predictions with pagination (OFFSET/LIMIT based)
            all_predictions = []
            offset = 0
            pagination_incomplete = False

            while self.tool_call_count < self.manifest.max_tool_calls:
                pred_args = {
                    "limit": lookback,
                    "offset": offset
                }
                if symbol:
                    pred_args["symbol"] = symbol
                if model_name:
                    pred_args["model_name"] = model_name

                pred_response = await self.call_mcp_tool("get_lstm_predictions", pred_args)

                if pred_response.get("error"):
                    if offset == 0:
                        # First page failed
                        return SkillResult(
                            skill_id=self.manifest.skill_id,
                            skill_version=self.manifest.version,
                            subject=model_name or "all_models",
                            status="PARTIAL",
                            summary="No predictions available for analysis",
                            findings=findings,
                            evidence=evidence,
                            data_gaps=["No LSTM predictions in database"],
                        )
                    else:
                        # Middle of pagination failed
                        break

                page_predictions = pred_response.get("predictions", [])
                page_count = len(page_predictions)

                # MCP contract does not expose stable prediction_id
                # Collect all predictions as-is without deduplication attempt
                for pred in page_predictions:
                    all_predictions.append(pred)

                evidence.append(self._create_evidence(
                    "get_lstm_predictions",
                    data={"offset": offset, "count": page_count, "total_collected": len(all_predictions)}
                ))

                # Check if more pages exist
                if page_count < lookback:
                    # Last page reached
                    break

                # Move to next page
                offset += lookback

            # Check if pagination was interrupted by tool call limit
            if offset > 0 and page_count >= lookback and self.tool_call_count >= self.manifest.max_tool_calls:
                pagination_incomplete = True
                data_gaps.append("Pagination incomplete: max_tool_calls limit reached")

            predictions = all_predictions
            if not predictions:
                data_gaps.append("No predictions returned")

            evidence.append(self._create_evidence("get_lstm_predictions", data={"total": len(predictions)}))

            # Analyze predictions
            correct_direction = 0
            evaluable_direction = 0
            errors = []
            confidences = []
            missing_directions = 0

            if len(predictions) >= 10:
                for pred in predictions:
                    # MCP response uses "direction" field for both predicted and real
                    pred_dir = pred.get("direction_predicted")  # For test mocks
                    real_dir = pred.get("direction_real")        # For test mocks

                    # But actual MCP may have different structure - handle both
                    if pred_dir is None and "direction" in pred:
                        # If single direction field, cannot evaluate accuracy
                        pred_dir = None
                        real_dir = None

                    conf = pred.get("confidence_score") or pred.get("confidence", 0)
                    error = pred.get("error_percent", 0)

                    # Safely handle non-finite values
                    if isinstance(conf, (int, float)) and math.isfinite(conf):
                        confidences.append(conf)
                    if isinstance(error, (int, float)) and math.isfinite(error):
                        errors.append(error)

                    # Directional accuracy only if BOTH directions are present and valid
                    if pred_dir is not None and real_dir is not None:
                        evaluable_direction += 1
                        if pred_dir == real_dir:
                            correct_direction += 1
                    else:
                        missing_directions += 1

                # Calculate metrics based on evaluable data
                accuracy = (correct_direction / evaluable_direction * 100) if evaluable_direction > 0 else 0
                avg_confidence = sum(confidences) / len(confidences) if confidences else 0
                avg_error = sum(errors) / len(errors) if errors else 0

                # Calculate drift as factual metric (no classification)
                recent_errors = errors[-5:] if len(errors) >= 5 else errors
                recent_avg = sum(recent_errors) / len(recent_errors) if recent_errors else 0
                historical_avg = avg_error
                drift = ((recent_avg - historical_avg) / historical_avg * 100) if historical_avg else 0

                findings.append({
                    "aspect": "prediction_accuracy",
                    "total_predictions": len(predictions),
                    "evaluable_direction_rows": evaluable_direction,
                    "missing_direction_rows": missing_directions,
                    "directional_accuracy_pct": round(accuracy, 2) if evaluable_direction > 0 else "N/A",
                    "average_confidence": round(avg_confidence, 3) if confidences else "N/A",
                    "average_error_pct": round(avg_error, 2) if errors else "N/A",
                    "recent_error_trend_pct": round(drift, 2) if errors else "N/A",
                    "note": "Error trend shows recent vs historical average comparison. No configured drift threshold was provided for classification."
                })
            else:
                data_gaps.append(f"Insufficient predictions ({len(predictions)} < 10)")

            summary = f"LSTM analysis: {len(predictions)} predictions"
            if evaluable_direction > 0:
                summary += f" | Evaluable directions: {evaluable_direction}"

            # Confidence based on data completeness
            if len(predictions) >= 20 and evaluable_direction >= 10:
                confidence = SkillConfidence.HIGH.value
            elif len(predictions) >= 10 and evaluable_direction >= 5:
                confidence = SkillConfidence.MEDIUM.value
            else:
                confidence = SkillConfidence.LOW.value

            risk_flags = []
            if missing_directions > 0:
                risk_flags.append(f"Missing direction values: {missing_directions}")

            # Determine status: PARTIAL if pagination incomplete or insufficient data
            status = "PARTIAL" if (pagination_incomplete or len(predictions) < 10) else "SUCCESS"

            return SkillResult(
                skill_id=self.manifest.skill_id,
                skill_version=self.manifest.version,
                subject=model_name or symbol or "all_models",
                status=status,
                summary=summary,
                findings=findings,
                evidence=evidence,
                data_gaps=data_gaps,
                confidence=confidence,
                risk_flags=risk_flags,
                limitations=self.manifest.limitations,
            )

        except Exception as e:
            return SkillResult(
                skill_id=self.manifest.skill_id,
                skill_version=self.manifest.version,
                subject=model_name or "diagnostics",
                status="FAILED",
                summary=f"LSTM diagnostics failed: {str(e)}",
                findings=findings,
                evidence=evidence,
                data_gaps=[str(e)],
            )
