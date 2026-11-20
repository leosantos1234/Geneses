"""Audit model for Skill execution tracking."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional
from enum import Enum
import uuid


class AuditStatus(Enum):
    """Skill execution status."""
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"
    ABORTED = "ABORTED"


@dataclass
class SkillAuditRecord:
    """Audit record for a single skill execution."""

    skill_run_id: str
    skill_id: str
    skill_version: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    status: Optional[str] = None
    tools_called: List[str] = field(default_factory=list)
    tool_call_count: int = 0
    evidence_count: int = 0
    error_code: Optional[str] = None
    error_message: Optional[str] = None

    def finalize(
        self,
        status: str,
        tool_call_count: int,
        evidence_count: int,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None
    ) -> None:
        """Finalize audit record."""
        self.finished_at = datetime.now(timezone.utc)
        self.status = status
        self.tool_call_count = tool_call_count
        self.evidence_count = evidence_count
        self.error_code = error_code
        self.error_message = error_message

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "skill_run_id": self.skill_run_id,
            "skill_id": self.skill_id,
            "skill_version": self.skill_version,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "status": self.status,
            "tools_called": self.tools_called,
            "tool_call_count": self.tool_call_count,
            "evidence_count": self.evidence_count,
            "error_code": self.error_code,
            "error_message": self.error_message,
        }


class AuditLogger:
    """In-memory audit logger for skill executions."""

    def __init__(self):
        """Initialize audit logger."""
        self._records: List[SkillAuditRecord] = []

    def create_record(self, skill_id: str, skill_version: str) -> SkillAuditRecord:
        """Create a new audit record."""
        record = SkillAuditRecord(
            skill_run_id=str(uuid.uuid4()),
            skill_id=skill_id,
            skill_version=skill_version,
            started_at=datetime.now(timezone.utc)
        )
        self._records.append(record)
        return record

    def list_records(self) -> List[SkillAuditRecord]:
        """List all audit records."""
        return self._records.copy()

    def get_record(self, skill_run_id: str) -> Optional[SkillAuditRecord]:
        """Get audit record by skill_run_id."""
        for record in self._records:
            if record.skill_run_id == skill_run_id:
                return record
        return None

    def clear(self) -> None:
        """Clear all records (for testing)."""
        self._records.clear()


# Global audit logger instance
_global_audit_logger = AuditLogger()


def get_audit_logger() -> AuditLogger:
    """Get global audit logger."""
    return _global_audit_logger
