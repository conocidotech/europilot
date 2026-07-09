"""Dutch motorway matrix signs, served by app.europilot.eu.

Merge-safe: this package is new and does not modify any upstream openpilot code.
It contains no third-party address and no third-party credential; see the binding
document `architectuur-data-gateway`.
"""

from europilot.ndw.client import MatrixSignClient
from europilot.ndw.match import GantryIndex
from europilot.ndw.types import Display, Gantry, Match, Sign

__all__ = ["Display", "Gantry", "GantryIndex", "Match", "MatrixSignClient", "Sign"]
