"""Skill manifest validation and definition."""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from enum import Enum


class SkillStatus(Enum):
    """Skill status."""
    DRAFT = "DRAFT"
    AVAILABLE = "AVAILABLE"
    DISABLED = "DISABLED"


class SkillAuthority(Enum):
    """Skill authority level."""
    ANALYTICAL_READ_ONLY = "ANALYTICAL_READ_ONLY"


class SkillCategory(Enum):
    """Skill category."""
    ANALYSIS = "analysis"
    DIAGNOSTIC = "diagnostic"
    REVIEW = "review"


class SkillConfidence(Enum):
    """Deterministic confidence classification based on evidence quality."""
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass
class SkillInput:
    """Skill input parameter."""
    name: str
    type: str
    required: bool = False
    description: str = ""
    default: Any = None
    validation: Optional[Dict[str, Any]] = None


@dataclass
class SkillOutput:
    """Skill output field."""
    name: str
    type: str
    description: str = ""


@dataclass
class SkillManifest:
    """Skill manifest -- declarative definition."""

    # Identity
    skill_id: str
    name: str
    version: str
    status: SkillStatus

    # Metadata
    description: str
    category: SkillCategory
    authority: SkillAuthority = SkillAuthority.ANALYTICAL_READ_ONLY

    # Interface
    inputs: List[SkillInput] = field(default_factory=list)
    outputs: List[SkillOutput] = field(default_factory=list)

    # MCP authorization
    allowed_mcp_tools: List[str] = field(default_factory=list)

    # Procedure: declarative steps (not executable code)
    procedure: str = ""

    # Execution contract
    preconditions: List[str] = field(default_factory=list)
    success_criteria: List[str] = field(default_factory=list)
    failure_conditions: List[str] = field(default_factory=list)
    abort_conditions: List[str] = field(default_factory=list)

    # Limitations
    limitations: List[str] = field(default_factory=list)
    evidence_requirements: List[str] = field(default_factory=list)

    # Side effects
    side_effects: str = "NONE"

    # Constraints
    max_tool_calls: int = 20
    timeout_seconds: int = 60

    def validate(self) -> bool:
        """Validate manifest."""
        # Check required string fields (non-empty after strip)
        if not self.skill_id or not self.skill_id.strip():
            raise ValueError("skill_id is required and cannot be empty")
        if not self.name or not self.name.strip():
            raise ValueError("name is required and cannot be empty")
        if not self.version or not self.version.strip():
            raise ValueError("version is required and cannot be empty")
        if not self.description or not self.description.strip():
            raise ValueError("description is required and cannot be empty")
        if not self.procedure or not self.procedure.strip():
            raise ValueError("procedure is required and cannot be empty or whitespace")

        # Check authority
        if self.authority != SkillAuthority.ANALYTICAL_READ_ONLY:
            raise ValueError("Only ANALYTICAL_READ_ONLY authority is allowed")

        # Check side effects
        if self.side_effects != "NONE":
            raise ValueError("Side effects must be NONE")

        # Check max_tool_calls
        if not isinstance(self.max_tool_calls, int) or isinstance(self.max_tool_calls, bool):
            raise ValueError("max_tool_calls must be a positive integer")
        if self.max_tool_calls <= 0:
            raise ValueError("max_tool_calls must be greater than 0")

        # Check timeout_seconds
        if isinstance(self.timeout_seconds, bool):
            raise ValueError("timeout_seconds must be numeric, not boolean")
        if not isinstance(self.timeout_seconds, (int, float)):
            raise ValueError("timeout_seconds must be numeric")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than 0")
        # Check for NaN/Inf
        import math
        if math.isnan(self.timeout_seconds) or math.isinf(self.timeout_seconds):
            raise ValueError("timeout_seconds cannot be NaN or Infinity")

        # Check that tools are valid
        valid_tools = {
            "get_system_health",
            "list_open_positions",
            "list_trades",
            "get_trade_detail",
            "get_risk_status",
            "get_decision_trace",
            "list_decisions",
            "get_lstm_predictions",
            "get_model_inventory",
            "get_commander_decision",
        }

        if not self.allowed_mcp_tools:
            raise ValueError("At least one allowed_mcp_tools must be specified")

        for tool in self.allowed_mcp_tools:
            if tool not in valid_tools:
                raise ValueError(f"Unknown MCP tool: {tool}")

        # Validate inputs structure if present
        for input_def in self.inputs:
            if not isinstance(input_def, SkillInput):
                raise ValueError(f"Invalid input definition: {input_def}")
            if not input_def.name or not input_def.name.strip():
                raise ValueError("Input name cannot be empty")
            if not input_def.type or not input_def.type.strip():
                raise ValueError("Input type cannot be empty")

        return True

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "skill_id": self.skill_id,
            "name": self.name,
            "version": self.version,
            "status": self.status.value,
            "description": self.description,
            "category": self.category.value,
            "authority": self.authority.value,
            "inputs": [{"name": i.name, "type": i.type, "required": i.required} for i in self.inputs],
            "outputs": [{"name": o.name, "type": o.type} for o in self.outputs],
            "allowed_mcp_tools": self.allowed_mcp_tools,
            "procedure": self.procedure,
            "preconditions": self.preconditions,
            "success_criteria": self.success_criteria,
            "failure_conditions": self.failure_conditions,
            "abort_conditions": self.abort_conditions,
            "limitations": self.limitations,
            "evidence_requirements": self.evidence_requirements,
            "side_effects": self.side_effects,
            "max_tool_calls": self.max_tool_calls,
            "timeout_seconds": self.timeout_seconds,
        }
