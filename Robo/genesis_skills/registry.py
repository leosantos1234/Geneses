"""Skill registry — maintains list of available skills."""

from typing import Dict, List, Optional
from genesis_skills.manifest import SkillManifest, SkillStatus
from genesis_skills.errors import SkillNotFoundError, SkillDisabledError


class SkillRegistry:
    """Central registry of available skills."""

    def __init__(self):
        """Initialize empty registry."""
        self._skills: Dict[str, SkillManifest] = {}

    def _manifest_to_dict(self, manifest: SkillManifest) -> dict:
        """Convert manifest to comparable dict (for idempotency check)."""
        return manifest.to_dict()

    def register(self, manifest: SkillManifest) -> None:
        """
        Register a skill (idempotent, rejects incompatible duplicates).

        Args:
            manifest: SkillManifest to register

        Raises:
            ValueError: If skill exists with incompatible definition
        """
        manifest.validate()

        skill_id = manifest.skill_id
        if skill_id in self._skills:
            # Skill already registered — check if identical
            existing = self._skills[skill_id]
            existing_dict = self._manifest_to_dict(existing)
            new_dict = self._manifest_to_dict(manifest)

            if existing_dict == new_dict:
                # Identical — idempotent no-op
                return
            else:
                # Incompatible — reject
                raise ValueError(
                    f"Incompatible skill registration: skill_id '{skill_id}' "
                    f"already registered with different definition"
                )

        # First registration or identical re-registration
        self._skills[skill_id] = manifest

    def unregister(self, skill_id: str) -> None:
        """Unregister a skill."""
        if skill_id in self._skills:
            del self._skills[skill_id]

    def get(self, skill_id: str) -> SkillManifest:
        """
        Get skill manifest by ID.

        Args:
            skill_id: Skill ID

        Returns:
            SkillManifest

        Raises:
            SkillNotFoundError: If skill not found
        """
        if skill_id not in self._skills:
            raise SkillNotFoundError(skill_id)

        manifest = self._skills[skill_id]

        # Check if disabled
        if manifest.status == SkillStatus.DISABLED:
            raise SkillDisabledError(skill_id)

        return manifest

    def list_available(self) -> List[Dict]:
        """
        List all available skills.

        Returns:
            List of skill metadata
        """
        result = []
        for skill_id, manifest in self._skills.items():
            if manifest.status == SkillStatus.AVAILABLE:
                result.append({
                    "skill_id": manifest.skill_id,
                    "name": manifest.name,
                    "version": manifest.version,
                    "category": manifest.category.value,
                    "authority": manifest.authority.value,
                })
        return result

    def count(self) -> int:
        """Count available skills."""
        return len([m for m in self._skills.values() if m.status == SkillStatus.AVAILABLE])

    def to_dict(self) -> Dict:
        """Export registry as dictionary."""
        return {
            skill_id: manifest.to_dict()
            for skill_id, manifest in self._skills.items()
        }


# Global registry instance
_global_registry = SkillRegistry()


def get_registry() -> SkillRegistry:
    """Get global skill registry."""
    return _global_registry
