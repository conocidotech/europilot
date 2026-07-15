"""Onroad heads-up for traffic-sign awareness (stop / give-way / cyclestreet).

Reads euMapAdvisory: the next stop / give-way sign ahead on the matched road, or
whether the current road is a cyclestreet (fietsstraat). A HEADS-UP only -- it
never eases; it just makes an easy-to-miss sign visible. Priority: an actual sign
ahead beats the ambient fietsstraat hint. Fades in/out; only reads a message.
"""

import pyray as rl

from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.selfdrive.ui.mici.theme import ACCENT
from openpilot.system.ui.lib.application import gui_app, FontWeight
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.widgets import Widget

# kind -> (label, (r, g, b))
_SIGNS = {
  "stop": ("stop", (222, 62, 62)),          # red
  "giveWay": ("voorrang", (240, 182, 42)),  # amber
}
_CYCLESTREET = ("fietsstraat", (ACCENT.r, ACCENT.g, ACCENT.b))

_SIGN_MAX_SHOW_M = 200.0   # don't announce a sign farther than this
_PAD_X = 30
_GAP = 18
_HEIGHT = 80
_LABEL_FS = 44
_DIST_FS = 30
_TOP_OFFSET = 408   # just below the easing pill (which sits at 300)


class SignHeadsUp(Widget):
  def __init__(self):
    super().__init__()
    self._alpha = FirstOrderFilter(0.0, 0.15, 1 / gui_app.target_fps)
    self._label = ""
    self._color = (255, 255, 255)
    self._dist = -1.0
    self._font_bold = gui_app.font(FontWeight.BOLD)
    self._font_medium = gui_app.font(FontWeight.MEDIUM)

  def _update_state(self):
    label, color, dist = "", (255, 255, 255), -1.0
    if ui_state.sm.valid["euMapAdvisory"]:
      adv = ui_state.sm["euMapAdvisory"]
      kind = str(adv.signKind)
      if kind in _SIGNS and 0 <= adv.signDistance <= _SIGN_MAX_SHOW_M:
        label, color = _SIGNS[kind]
        dist = float(adv.signDistance)
      elif adv.cyclestreet:
        label, color = _CYCLESTREET

    self._alpha.update(1.0 if label else 0.0)
    if label:
      self._label, self._color, self._dist = label, color, dist

  def _render(self, rect: rl.Rectangle):
    a = self._alpha.x
    if a < 1e-2:
      return

    dist_txt = f"{int(self._dist)} m" if self._dist >= 1.0 else ""
    lw = measure_text_cached(self._font_bold, self._label, _LABEL_FS)
    dw = measure_text_cached(self._font_medium, dist_txt, _DIST_FS) if dist_txt else rl.Vector2(0, 0)

    content_w = lw.x + ((_GAP + dw.x) if dist_txt else 0.0)
    pill_w = content_w + 2 * _PAD_X
    x = rect.x + (rect.width - pill_w) / 2.0
    y = rect.y + _TOP_OFFSET

    rl.draw_rectangle_rounded(rl.Rectangle(x, y, pill_w, _HEIGHT), 0.5, 12, rl.Color(0, 0, 0, int(180 * a)))

    r, g, b = self._color
    col = rl.Color(r, g, b, int(255 * a))
    dim = rl.Color(255, 255, 255, int(150 * a))

    cx = x + _PAD_X
    rl.draw_text_ex(self._font_bold, self._label, rl.Vector2(cx, y + (_HEIGHT - lw.y) / 2.0), _LABEL_FS, 0, col)
    if dist_txt:
      cx += lw.x + _GAP
      rl.draw_text_ex(self._font_medium, dist_txt, rl.Vector2(cx, y + (_HEIGHT - dw.y) / 2.0), _DIST_FS, 0, dim)
