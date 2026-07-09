"""Dutch motorway matrix signs (MSI) as a dynamic speed-limit source.

Merge-safe: this package is new and does not modify any upstream openpilot code.
"""

from europilot.ndw.match import Gantry, GantryIndex, Match
from europilot.ndw.static_index import Sign, load_signs

__all__ = ["Gantry", "GantryIndex", "Match", "Sign", "load_signs"]
