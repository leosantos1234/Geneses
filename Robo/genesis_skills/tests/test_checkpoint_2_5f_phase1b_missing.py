"""Phase 1B — Missing Behavioral Coverage (Realistic).

Tests verify:
- LSTM respects limit parameter
- Nonfinite values correctly excluded
- Model inventory handling
- Sequence_input filtering
- Commander error handling
- System health graceful degradation
"""

import pytest
import asyncio
import math
from datetime import datetime, timezone
from unittest.mock import AsyncMock
from genesis_skills.manifest import SkillConfidence
from genesis_skills.skills.lstm_diagnostics import LSTMDiagnosticsSkill
from genesis_skills.skills.system_health_review import SystemHealthReviewSkill
from genesis_skills.skills.trade_decision_autopsy import TradeDecisionAutopsySkill


class TestLSTMPaginationLimit:
    """Real tests for LSTM limit parameter usage."""

    def test_lstm_uses_lookback_period_as_limit(self):
        """LSTM must pass lookback_periods as limit to MCP."""
        async def run():
            manifest = LSTMDiagnosticsSkill.MANIFEST
            mock_client = AsyncMock()

            call_log = []

            async def mock_call(tool, params):
                call_log.append((tool, dict(params) if isinstance(params, dict) else params))
                if tool == "get_model_inventory":
                    return {"models": []}
                elif tool == "get_lstm_predictions":
                    limit = params.get("limit", 10)
                    return {
                        "predictions": [
                            {"direction_predicted": "up", "direction_real": "up", "confidence_score": 0.9, "error_percent": 1.0}
                            for _ in range(min(limit, 5))
                        ]
                    }
                return {}

            mock_client.call = AsyncMock(side_effect=mock_call)
            skill = LSTMDiagnosticsSkill(manifest, mock_client)
            result = await skill.execute({"lookback_periods": 25})

            lstm_calls = [c for c in call_log if c[0] == "get_lstm_predictions"]
            assert len(lstm_calls) > 0
            assert lstm_calls[0][1].get("limit") == 25

            return True

        assert asyncio.run(run())


class TestExactNonfiniteExclusion:
    """Real tests for exact nonfinite handling."""

    def test_nan_and_inf_excluded_from_metrics(self):
        """NaN and Inf must not corrupt confidence/error averages."""
        async def run():
            manifest = LSTMDiagnosticsSkill.MANIFEST
            mock_client = AsyncMock()

            async def mock_call(tool, params):
                if tool == "get_model_inventory":
                    return {"models": []}
                elif tool == "get_lstm_predictions":
                    return {
                        "predictions": [
                            {"direction_predicted": "up", "direction_real": "up", "confidence_score": float('nan'), "error_percent": 1.0},
                            {"direction_predicted": "up", "direction_real": "up", "confidence_score": 0.9, "error_percent": float('inf')},
                            {"direction_predicted": "up", "direction_real": "up", "confidence_score": 0.85, "error_percent": float('-inf')},
                            {"direction_predicted": "up", "direction_real": "up", "confidence_score": 0.8, "error_percent": 1.5},
                            {"direction_predicted": "up", "direction_real": "up", "confidence_score": 0.9, "error_percent": 1.2},
                            {"direction_predicted": "up", "direction_real": "up", "confidence_score": 0.75, "error_percent": 1.3},
                            {"direction_predicted": "up", "direction_real": "up", "confidence_score": 0.88, "error_percent": 1.0},
                            {"direction_predicted": "up", "direction_real": "up", "confidence_score": 0.82, "error_percent": 1.4},
                            {"direction_predicted": "up", "direction_real": "up", "confidence_score": 0.91, "error_percent": 1.1},
                            {"direction_predicted": "up", "direction_real": "up", "confidence_score": 0.87, "error_percent": 1.3},
                        ]
                    }
                return {}

            mock_client.call = AsyncMock(side_effect=mock_call)
            skill = LSTMDiagnosticsSkill(manifest, mock_client)
            result = await skill.execute({})

            findings = result.findings
            pred_finding = next((f for f in findings if f.get("aspect") == "prediction_accuracy"), None)
            assert pred_finding is not None

            avg_conf = pred_finding.get("average_confidence")
            avg_error = pred_finding.get("average_error_pct")

            assert isinstance(avg_conf, (int, float)) and not math.isnan(avg_conf) and not math.isinf(avg_conf)
            assert 0.85 < avg_conf < 0.86, f"Expected ~0.853, got {avg_conf}"

            assert isinstance(avg_error, (int, float)) and not math.isnan(avg_error) and not math.isinf(avg_error)
            assert 1.2 < avg_error < 1.3, f"Expected ~1.225, got {avg_error}"

            return True

        assert asyncio.run(run())


class TestExactNullDirectionMetrics:
    """Verify exact NULL direction metrics."""

    def test_exact_null_and_mismatch_counts(self):
        """Verify exact counting of evaluable vs null directions."""
        async def run():
            manifest = LSTMDiagnosticsSkill.MANIFEST
            mock_client = AsyncMock()

            async def mock_call(tool, params):
                if tool == "get_model_inventory":
                    return {"models": []}
                elif tool == "get_lstm_predictions":
                    return {
                        "predictions": [
                            # Row 1: NULL/NULL (missing)
                            {"direction_predicted": None, "direction_real": None, "confidence_score": 0.5, "error_percent": 2.0},
                            # Row 2: NULL/UP (missing)
                            {"direction_predicted": None, "direction_real": "up", "confidence_score": 0.5, "error_percent": 2.0},
                            # Row 3: UP/NULL (missing)
                            {"direction_predicted": "up", "direction_real": None, "confidence_score": 0.5, "error_percent": 2.0},
                            # Row 4: UP/UP (evaluable, correct)
                            {"direction_predicted": "up", "direction_real": "up", "confidence_score": 0.9, "error_percent": 1.0},
                            # Row 5: UP/DOWN (evaluable, incorrect)
                            {"direction_predicted": "up", "direction_real": "down", "confidence_score": 0.7, "error_percent": 3.0},
                            # Row 6: DOWN/DOWN (evaluable, correct)
                            {"direction_predicted": "down", "direction_real": "down", "confidence_score": 0.8, "error_percent": 1.5},
                            # Row 7: DOWN/DOWN (evaluable, correct)
                            {"direction_predicted": "down", "direction_real": "down", "confidence_score": 0.8, "error_percent": 1.5},
                            # Row 8: UP/UP (evaluable, correct)
                            {"direction_predicted": "up", "direction_real": "up", "confidence_score": 0.85, "error_percent": 1.2},
                            # Row 9: UP/UP (evaluable, correct)
                            {"direction_predicted": "up", "direction_real": "up", "confidence_score": 0.85, "error_percent": 1.2},
                            # Row 10: DOWN/UP (evaluable, incorrect)
                            {"direction_predicted": "down", "direction_real": "up", "confidence_score": 0.6, "error_percent": 3.0},
                        ]
                    }
                return {}

            mock_client.call = AsyncMock(side_effect=mock_call)
            skill = LSTMDiagnosticsSkill(manifest, mock_client)
            result = await skill.execute({})

            findings = result.findings
            pred_finding = next((f for f in findings if f.get("aspect") == "prediction_accuracy"), None)
            assert pred_finding is not None

            # Exact assertions:
            assert pred_finding.get("total_predictions") == 10, f"Expected 10, got {pred_finding.get('total_predictions')}"
            assert pred_finding.get("evaluable_direction_rows") == 7, f"Expected 7 evaluable, got {pred_finding.get('evaluable_direction_rows')}"
            assert pred_finding.get("missing_direction_rows") == 3, f"Expected 3 missing, got {pred_finding.get('missing_direction_rows')}"

            # Accuracy: 5 correct / 7 evaluable ≈ 71.43%
            accuracy = pred_finding.get("directional_accuracy_pct")
            assert 71 < accuracy < 72, f"Expected 71-72%, got {accuracy}"

            return True

        assert asyncio.run(run())


class TestSequenceInputNotExposed:
    """Verify sensitive data is redacted."""

    def test_sequence_input_excluded_from_output(self):
        """sequence_input field must not appear in serialized output."""
        async def run():
            manifest = LSTMDiagnosticsSkill.MANIFEST
            mock_client = AsyncMock()

            async def mock_call(tool, params):
                if tool == "get_model_inventory":
                    return {"models": []}
                elif tool == "get_lstm_predictions":
                    return {
                        "predictions": [
                            {
                                "direction_predicted": "up",
                                "direction_real": "up",
                                "confidence_score": 0.9,
                                "error_percent": 1.0,
                                "sequence_input": ["SHOULD_NOT_APPEAR"],
                                "secret_feature": "HIDDEN"
                            }
                            for _ in range(15)
                        ]
                    }
                return {}

            mock_client.call = AsyncMock(side_effect=mock_call)
            skill = LSTMDiagnosticsSkill(manifest, mock_client)
            result = await skill.execute({})

            # Serialize and check
            if hasattr(result, 'to_dict'):
                result_dict = result.to_dict()
                result_str = str(result_dict)
            else:
                result_str = str(result)

            assert "sequence_input" not in result_str.lower()
            assert "SHOULD_NOT_APPEAR" not in result_str
            assert "secret_feature" not in result_str.lower()

            return True

        assert asyncio.run(run())


class TestCommanderErrorHandling:
    """Real tests for commander MCP errors."""

    def test_commander_unavailable_gracefully_handled(self):
        """Commander error must not crash autopsy."""
        async def run():
            manifest = TradeDecisionAutopsySkill.MANIFEST
            mock_client = AsyncMock()

            async def mock_call(tool, params):
                if tool == "get_trade_detail":
                    return {"trade": {"trade_id": "T1", "symbol": "BTC"}}
                elif tool == "get_decision_trace":
                    return {"trace": {}}
                elif tool == "get_commander_decision":
                    return {"error": "COMMANDER_TIMEOUT"}
                elif tool == "get_risk_status":
                    return {"risk_status": {}}
                return {}

            mock_client.call = AsyncMock(side_effect=mock_call)
            skill = TradeDecisionAutopsySkill(manifest, mock_client)
            result = await skill.execute({"trade_id": "T1"})

            # Should complete despite commander error
            assert result.status in ["SUCCESS", "PARTIAL"]
            # Should not fabricate commander finding
            has_commander = any(f.get("aspect") == "commander_decision" for f in result.findings if isinstance(f, dict))
            assert not has_commander

            return True

        assert asyncio.run(run())


class TestSystemHealthGracefulDegradation:
    """Real tests for system health observational semantics."""

    def test_system_health_no_root_cause_inference(self):
        """System health must not infer root causes without evidence."""
        async def run():
            manifest = SystemHealthReviewSkill.MANIFEST
            mock_client = AsyncMock()

            async def mock_call(tool, params):
                if tool == "get_system_health":
                    return {"health": {"database": "healthy", "worker": "running"}}
                elif tool == "get_model_inventory":
                    return {"models": []}
                elif tool == "list_open_positions":
                    return {"positions": []}
                elif tool == "get_risk_status":
                    return {"risk_status": {"status": "allow"}}
                elif tool == "list_decisions":
                    return {"decisions": []}
                return {}

            mock_client.call = AsyncMock(side_effect=mock_call)
            skill = SystemHealthReviewSkill(manifest, mock_client)
            result = await skill.execute({})

            # Should return success when all tools available
            assert result.status == "SUCCESS"
            # No fabricated root causes
            recommendations_str = str(result.recommendations).lower()
            assert "credentials" not in recommendations_str
            assert "firewall" not in recommendations_str

            return True

        assert asyncio.run(run())


class TestModelInventoryReporting:
    """Real tests for model inventory reporting."""

    def test_model_count_reported(self):
        """Model inventory must report actual model count."""
        async def run():
            manifest = LSTMDiagnosticsSkill.MANIFEST
            mock_client = AsyncMock()

            async def mock_call(tool, params):
                if tool == "get_model_inventory":
                    return {
                        "models": [
                            {"name": "lstm_v1", "status": "active"},
                            {"name": "lstm_v2", "status": "active"},
                            {"name": "xgb_model", "status": "inactive"},
                        ]
                    }
                elif tool == "get_lstm_predictions":
                    return {"predictions": [
                        {"direction_predicted": "up", "direction_real": "up", "confidence_score": 0.9, "error_percent": 1.0}
                        for _ in range(15)
                    ]}
                return {}

            mock_client.call = AsyncMock(side_effect=mock_call)
            skill = LSTMDiagnosticsSkill(manifest, mock_client)
            result = await skill.execute({})

            findings = result.findings
            inv_finding = next((f for f in findings if f.get("aspect") == "model_inventory"), None)
            assert inv_finding is not None
            assert inv_finding.get("total_models") == 3
            assert len(inv_finding.get("models", [])) == 3

            return True

        assert asyncio.run(run())


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
