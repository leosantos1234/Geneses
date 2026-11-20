"""Pytest configuration for MCP tests."""
import sys
from pathlib import Path

# Add Robo root to path for imports
robo_root = Path(__file__).resolve().parents[2]
if str(robo_root) not in sys.path:
    sys.path.insert(0, str(robo_root))

# Fixtures shared across test suites can be added here
