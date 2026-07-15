"""Onroad heads-up for an upcoming NDW lane closure (wegwerk / incident).

Reads euNdwMatrixSigns.upcoming -- the matrix gantry ahead, already carrying the
closed lane indices, road and distance (filled by europilot/gateway.py). Shows
"A2 - strook 2 dicht - 800 m" while a closure is ahead, red when the sign is
flashing (active warning) else amber. A HEADS-UP only; it never touches control.
"""

import pyray as rl

from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import gui_app, FontWeight
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.widgets import Widget

_AMBER = (240, 182, 42)
_RED = (222, 62, 62)
_MAX_SHOW_M = 2000.0   # motorway gantries -> announce further out than a sign
_PAD_X = 30
_GAP = 18
_HEIGHT = 80
_TEXT_FS = 42
_DIST_FS = 30
_TOP_OFFSET = 508   # below the sign heads-up (408) and easing pill (300)


def _fmt_lanes(lanes: list[int]) -> str:
  uniq = sorted(set(lanes))
  word = "strook" if len(uniq) == 1 else "stroken"
  return f"{word} {', '.join(str(n) for n in uniq)} dicht"


def _fmt_dist(m: float) -> str:
  return f"{m / 1000:.1f} km" if m >= 1000 else f"{int(m)} m"


class LaneClosureHeadsUp(Widget):
  def __init__(self):
    super().__init__()
    self._alpha = FirstOrderFilter(0.0, 0.15, 1 / gui_app.target_fps)
    self._text = ""
    self._dist_txt = ""
    self._color = _AMBER
    self._font_bold = gui_app.font(FontWeight.BOLD)
    self._font_medium = gui_app.font(FontWeight.MEDIUM)

  def _update_state(self):
    text, dist_txt, color = "", "", _AMBER
    if ui_state.sm.valid["euNdwMatrixSigns"]:
      g = ui_state.sm["euNdwMatrixSigns"].upcoming
      lanes = list(g.closedLanes)
      if g.valid and lanes and 0 <= g.distance <= _MAX_SHOW_M:
        road = (str(g.road).strip() + " - ") if str(g.road).strip() else ""
        text = road + _fmt_lanes(lanes)
        dist_txt = _fmt_dist(g.distance)
        color = _RED if g.flashing else _AMBER

    self._alpha.update(1.0 if text else 0.0)
    if text:
      self._text, self._dist_txt, self._color = text, dist_txt, color

  def _render(self, rect: rl.Rectangle):
    a = self._alpha.x
    if a < 1e-2:
      return

    tw = measure_text_cached(self._font_bold, self._text, _TEXT_FS)
    dw = measure_text_cached(self._font_medium, self._dist_txt, _DIST_FS)

    content_w = tw.x + _GAP + dw.x
    pill_w = content_w + 2 * _PAD_X
    x = rect.x + (rect.width - pill_w) / 2.0
    y = rect.y + _TOP_OFFSET

    rl.draw_rectangle_rounded(rl.Rectangle(x, y, pill_w, _HEIGHT), 0.5, 12, rl.Color(0, 0, 0, int(180 * a)))

    r, g, b = self._color
    col = rl.Color(r, g, b, int(255 * a))
    dim = rl.Color(255, 255, 255, int(150 * a))

    cx = x + _PAD_X
    rl.draw_text_ex(self._font_bold, self._text, rl.Vector2(cx, y + (_HEIGHT - tw.y) / 2.0), _TEXT_FS, 0, col)
    cx += tw.x + _GAP
    rl.draw_text_ex(self._font_medium, self._dist_txt, rl.Vector2(cx, y + (_HEIGHT - dw.y) / 2.0), _DIST_FS, 0, dim)
