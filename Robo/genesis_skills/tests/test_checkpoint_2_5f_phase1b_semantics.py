"""Phase 1B — Semantic Corrections for Production Skills.

Tests verify:
- Temporal leakage protection
- Removed arbitrary thresholds
- Categorical confidence enum
- Null direction handling
- Non-finite value handling
- No operational commands
- No sequence_input exposure
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from genesis_skills.manifest import SkillConfidence
from genesis_skills.skills.trade_decision_autopsy import TradeDecisionAutopsySkill
from genesis_skills.skills.risk_review import RiskReviewSkill
from genesis_skills.skills.lstm_diagnostics import LSTMDiagnosticsSkill
from genesis_skills.skills.system_health_review import SystemHealthReviewSkill


class TestTradeDecisionAutopsySemantics:
    """Test trade autopsy temporal correctness."""

    def test_temporal_leakage_code_present(self):
        """Trade autopsy must include current_risk_state aspect (temporal leakage fix)."""
        # Simply verify the code contains the fix
        import inspect
        source = inspect.getsource(TradeDecisionAutopsySkill.execute)
        assert "current_risk_state" in source
        assert "historical" in source.lower()
        assert "current" in source.lower()

    def test_legacy_trade_gets_legacy_data_no_lineage_flag(self):
        """Legacy trade without decision_id must have LEGACY_DATA_NO_LINEAGE."""
        async def run():
            manifest = TradeDecisionAutopsySkill.MANIFEST
            mock_client = AsyncMock()

            mock_client.call = AsyncMock(side_effect=lambda tool, params: {
                "trade": {
                    "trade_id": "LEGACY123",
                    "symbol": "ETH",
                    "strategy": "old",
                    "status": "closed",
                    "entry_price": 2000,
                    "exit_price": 2100,
                    "pnl": 100,
                    # Note: no decision_id
                }
            } if tool == "get_trade_detail" else {
                "risk_status": {"status": "allow"}
            })

            skill = TradeDecisionAutopsySkill(manifest, mock_client)
            result = await skill.execute({"trade_id": "LEGACY123"})

            # Must have data_gaps flag
            assert "LEGACY_DATA_NO_LINEAGE" in result.data_gaps

            return True

        assert asyncio.run(run())

    def test_confidence_is_enum_not_numeric(self):
        """Confidence must be string enum, not numeric."""
        async def run():
            manifest = TradeDecisionAutopsySkill.MANIFEST
            mock_client = AsyncMock()

            mock_client.call = AsyncMock(return_value={"trade": {"trade_id": "T1"}})

            skill = TradeDecisionAutopsySkill(manifest, mock_client)
            result = await skill.execute({"trade_id": "T1"})

            # Confidence must be string, not float
            assert isinstance(result.confidence, str)
            assert result.confidence in [SkillConfidence.HIGH.value,
                                        SkillConfidence.MEDIUM.value,
                                        SkillConfidence.LOW.value,
                                        SkillConfidence.NOT_APPLICABLE.value]

            return True

        assert asyncio.run(run())


class TestRiskReviewSemantics:
    """Test risk review removed arbitrary thresholds."""

    def test_no_arbitrary_30_percent_threshold(self):
        """Risk review must not classify concentration as 'high' based on 30% threshold."""
        async def run():
            manifest = RiskReviewSkill.MANIFEST
            mock_client = AsyncMock()

            # Return 35% concentration
            mock_client.call = AsyncMock(side_effect=lambda tool, params: {
                "risk_status": {"status": "allow", "pnl": 10000, "exposure": 100000},
            } if tool == "get_risk_status" else {
                "positions": [
                    {"symbol": "BTC", "quantity": 10, "current_price": 50000},  # 500k exposure
                    {"symbol": "ETH", "quantity": 2000, "current_price": 2000},  # 4M exposure = 88.8%
                ],
            } if tool == "list_open_positions" else {
                "trades": [],
            } if tool == "list_trades" else {
                "health": {"database": "healthy", "worker": "running"},
            })

            skill = RiskReviewSkill(manifest, mock_client)
            result = await skill.execute({})

            findings_str = str(result.findings)
            # Must NOT have "high concentration" classification
            assert "high concentration" not in findings_str.lower()
            # Must say "observed concentrations" or similar factual language
            assert "observed" in findings_str.lower() or "concentration" in findings_str.lower()

            return True

        assert asyncio.run(run())

    def test_no_continue_operations_command(self):
        """Must not recommend 'continue operations'."""
        async def run():
            manifest = RiskReviewSkill.MANIFEST
            mock_client = AsyncMock()

            mock_client.call = AsyncMock(side_effect=lambda tool, params: {
                "risk_status": {"status": "allow"},
            } if tool == "get_risk_status" else {
                "positions": [],
            } if tool == "list_open_positions" else {
                "trades": [],
            } if tool == "list_trades" else {
                "health": {"database": "healthy"},
            })

            skill = RiskReviewSkill(manifest, mock_client)
            result = await skill.execute({})

            recommendations_str = str(result.recommendations).lower()
            assert "continue operations" not in recommendations_str
            assert "continue normal" not in recommendations_str

            return True

        assert asyncio.run(run())

    def test_confidence_is_enum_not_numeric(self):
        """Confidence must be enum, not numeric."""
        async def run():
            manifest = RiskReviewSkill.MANIFEST
            mock_client = AsyncMock()

            mock_client.call = AsyncMock(side_effect=lambda tool, params: {
                "risk_status": {"status": "allow"},
            } if tool == "get_risk_status" else {
                "positions": [],
            } if tool == "list_open_positions" else {
                "trades": [],
            } if tool == "list_trades" else {
                "health": {},
            })

            skill = RiskReviewSkill(manifest, mock_client)
            result = await skill.execute({})

            assert isinstance(result.confidence, str)
            assert result.confidence in [SkillConfidence.HIGH.value,
                                        SkillConfidence.MEDIUM.value,
                                        SkillConfidence.LOW.value,
                                        SkillConfidence.NOT_APPLICABLE.value]

            return True

        assert asyncio.run(run())


class TestLSTMDiagnosticsSemantics:
    """Test LSTM diagnostics correctness."""

    def test_none_direction_not_counted_correct(self):
        """None == None must not be counted as correct directional prediction."""
        async def run():
            manifest = LSTMDiagnosticsSkill.MANIFEST
            mock_client = AsyncMock()

            # Return predictions: half with None directions, half with matching directions
            mock_client.call = AsyncMock(side_effect=lambda tool, params: {
                "models": [],
            } if tool == "get_model_inventory" else {
                "predictions": [
                    {"direction_predicted": None, "direction_real": None, "confidence_score": 0.5, "error_percent": 2.0},
                    {"direction_predicted": None, "direction_real": None, "confidence_score": 0.5, "error_percent": 2.0},
                    {"direction_predicted": "up", "direction_real": "up", "confidence_score": 0.9, "error_percent": 1.0},
                    {"direction_predicted": "up", "direction_real": "up", "confidence_score": 0.9, "error_percent": 1.0},
                    {"direction_predicted": "down", "direction_real": "down", "confidence_score": 0.8, "error_percent": 1.5},
                    {"direction_predicted": "down", "direction_real": "down", "confidence_score": 0.8, "error_percent": 1.5},
                    {"direction_predicted": "up", "direction_real": "down", "confidence_score": 0.7, "error_percent": 3.0},
                    {"direction_predicted": "down", "direction_real": "up", "confidence_score": 0.6, "error_percent": 3.0},
                    {"direction_predicted": "up", "direction_real": "up", "confidence_score": 0.85, "error_percent": 1.2},
                    {"direction_predicted": "up", "direction_real": "up", "confidence_score": 0.85, "error_percent": 1.2},
                ]
            })

            skill = LSTMDiagnosticsSkill(manifest, mock_client)
            result = await skill.execute({})

            findings_str = str(result.findings)
            # Must show missing direction rows
            assert "missing_direction" in findings_str.lower() or "evaluable" in findings_str.lower()

            return True

        assert asyncio.run(run())

    def test_no_arbitrary_20_drift_threshold(self):
        """Must not classify drift as degradation based on arbitrary >20 threshold."""
        async def run():
            manifest = LSTMDiagnosticsSkill.MANIFEST
            mock_client = AsyncMock()

            # Return predictions with 25% drift
            mock_client.call = AsyncMock(side_effect=lambda tool, params: {
                "models": [{"name": "lstm_v1"}],
            } if tool == "get_model_inventory" else {
                "predictions": [
                    {"direction_predicted": "up", "direction_real": "up", "confidence_score": 0.9, "error_percent": 2.0},
                    {"direction_predicted": "down", "direction_real": "down", "confidence_score": 0.8, "error_percent": 2.0},
                    {"direction_predicted": "up", "direction_real": "up", "confidence_score": 0.85, "error_percent": 2.0},
                    {"direction_predicted": "up", "direction_real": "up", "confidence_score": 0.9, "error_percent": 2.0},
                    {"direction_predicted": "down", "direction_real": "down", "confidence_score": 0.88, "error_percent": 2.0},
                    # Recent: 5% error (vs historical 2%)
                    {"direction_predicted": "up", "direction_real": "up", "confidence_score": 0.7, "error_percent": 5.0},
                    {"direction_predicted": "up", "direction_real": "up", "confidence_score": 0.7, "error_percent": 5.0},
                    {"direction_predicted": "down", "direction_real": "down", "confidence_score": 0.7, "error_percent": 5.0},
                    {"direction_predicted": "up", "direction_real": "up", "confidence_score": 0.7, "error_percent": 5.0},
                    {"direction_predicted": "down", "direction_real": "down", "confidence_score": 0.7, "error_percent": 5.0},
                ]
            })

            skill = LSTMDiagnosticsSkill(manifest, mock_client)
            result = await skill.execute({})

            findings_str = str(result.findings)
            # Must NOT have "degradation detected" or similar classification
            assert "degradation" not in findings_str.lower()
            assert "drift_detected" not in findings_str.lower()
            # Must show factual drift metric
            assert "error_trend" in findings_str.lower() or "drift" in findings_str.lower()

            return True

        assert asyncio.run(run())

    def test_lstm_confidence_uses_enum(self):
        """Confidence must use enum, not numeric."""
        import inspect
        source = inspect.getsource(LSTMDiagnosticsSkill.execute)
        # Verify enum usage in code
        assert "SkillConfidence" in source
        # Verify no numeric confidence like 0.9, 0.7
        assert "confidence=0.9" not in source
        assert "confidence=0.7" not in source
        # Verify no arbitrary drift threshold
        assert "drift > 20" not in source


class TestSystemHealthSemantics:
    """Test system health observational only."""

    def test_no_continue_operations_recommendation(self):
        """Must not recommend 'continue operations'."""
        async def run():
            manifest = SystemHealthReviewSkill.MANIFEST
            mock_client = AsyncMock()

            mock_client.call = AsyncMock(side_effect=lambda tool, params: {
                "health": {"database": "healthy", "worker": "running"},
            } if tool == "get_system_health" else {
                "risk_status": {"status": "allow"},
            } if tool == "get_risk_status" else {
                "positions": [],
            } if tool == "list_open_positions" else {
                "decisions": [],
            } if tool == "list_decisions" else {
                "models": [],
            })

            skill = SystemHealthReviewSkill(manifest, mock_client)
            result = await skill.execute({})

            recommendations_str = str(result.recommendations).lower()
            assert "continue operations" not in recommendations_str

            return True

        assert asyncio.run(run())

    def test_confidence_is_enum_not_numeric(self):
        """Confidence must be enum, not numeric."""
        async def run():
            manifest = SystemHealthReviewSkill.MANIFEST
            mock_client = AsyncMock()

            mock_client.call = AsyncMock(side_effect=lambda tool, params: {
                "health": {"database": "healthy"},
            } if tool == "get_system_health" else {
                "risk_status": {"status": "allow"},
            } if tool == "get_risk_status" else {
                "positions": [],
            } if tool == "list_open_positions" else {
                "decisions": [],
            } if tool == "list_decisions" else {
                "models": [],
            })

            skill = SystemHealthReviewSkill(manifest, mock_client)
            result = await skill.execute({})

            assert isinstance(result.confidence, str)
            assert result.confidence in [SkillConfidence.HIGH.value,
                                        SkillConfidence.MEDIUM.value,
                                        SkillConfidence.LOW.value,
                                        SkillConfidence.NOT_APPLICABLE.value]

            return True

        assert asyncio.run(run())


class TestOutputContract:
    """Test all Skills follow standard output contract."""

    def test_standard_fields_present(self):
        """All Skills must return standard SkillResult fields."""
        async def run():
            skills_to_test = [
                (TradeDecisionAutopsySkill, {"trade_id": "T1"}),
                (RiskReviewSkill, {}),
                (LSTMDiagnosticsSkill, {}),
                (SystemHealthReviewSkill, {}),
            ]

            for skill_class, inputs in skills_to_test:
                manifest = skill_class.MANIFEST
                mock_client = AsyncMock()
                mock_client.call = AsyncMock(return_value={})

                skill = skill_class(manifest, mock_client)
                result = await skill.execute(inputs)

                # Check standard fields
                assert hasattr(result, 'skill_id')
                assert hasattr(result, 'skill_version')
                assert hasattr(result, 'subject')
                assert hasattr(result, 'status')
                assert hasattr(result, 'summary')
                assert hasattr(result, 'findings')
                assert hasattr(result, 'evidence')
                assert hasattr(result, 'data_gaps')
                assert hasattr(result, 'confidence')
                assert hasattr(result, 'risk_flags')
                assert hasattr(result, 'recommendations')
                assert hasattr(result, 'limitations')

            return True

        assert asyncio.run(run())


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
