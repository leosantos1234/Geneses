"""Recursion guard for skill execution."""

from contextvars import ContextVar
from typing import FrozenSet, Optional, Tuple

# ContextVar to track currently executing skills (immutable frozenset)
_executing_skills: ContextVar[FrozenSet[str]] = ContextVar('executing_skills', default=frozenset())


def is_skill_executing(skill_id: str) -> bool:
    """Check if a skill is currently executing (recursion check)."""
    skills = _executing_skills.get()
    return skill_id in skills


def mark_skill_executing(skill_id: str) -> Tuple:
    """
    Mark a skill as currently executing.

    Returns a token for resetting in finally block.
    """
    skills = _executing_skills.get()
    new_skills = skills | {skill_id}
    token = _executing_skills.set(new_skills)
    return token


def reset_skill_execution(token: Tuple) -> None:
    """Reset skill execution state using token from mark_skill_executing."""
    _executing_skills.reset(token)
