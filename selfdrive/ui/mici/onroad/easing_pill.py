"""Onroad pill showing the binding advisory ease (reason + target speed).

Observability only: it reflects euSpeedLimit.easing* -- the ease the planner is
actually applying (the lowest target among the ENABLED toggles) -- so the driver
sees WHY the car is easing off, instead of a silent speed change. It fades in
when an ease is active and out otherwise, staying unobtrusive. Never touches
control; it only reads a message.
"""

import pyray as rl

from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.selfdrive.ui.mici.theme import ACCENT
from openpilot.system.ui.lib.application import gui_app, FontWeight
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.widgets import Widget

# Dutch labels for each easing reason (euSpeedLimit.EasingReason names).
_LABELS = {
  "camera": "flitser",
  "section": "trajectcontrole",
  "roundabout": "rotonde",
  "curve": "bocht",
}

_PAD_X = 30
_GAP = 20
_HEIGHT = 92
_TARGET_FS = 60
_LABEL_FS = 38
_DIST_FS = 30
_TOP_OFFSET = 300   # below the big current-speed number (top-centre)


class EasingPill(Widget):
  def __init__(self):
    super().__init__()
    self._alpha = FirstOrderFilter(0.0, 0.15, 1 / gui_app.target_fps)
    self._reason = "none"
    self._target = 0
    self._dist = -1.0
    self._font_bold = gui_app.font(FontWeight.BOLD)
    self._font_medium = gui_app.font(FontWeight.MEDIUM)

  def _update_state(self):
    sl = ui_state.sm["euSpeedLimit"]
    active = (ui_state.sm.valid["euSpeedLimit"] and str(sl.easingReason) != "none"
              and sl.easingTarget > 0)
    self._alpha.update(1.0 if active else 0.0)
    if active:
      self._reason = str(sl.easingReason)
      self._target = int(sl.easingTarget)
      self._dist = float(sl.easingDistance)

  def _render(self, rect: rl.Rectangle):
    a = self._alpha.x
    if a < 1e-2:
      return

    label = _LABELS.get(self._reason, self._reason)
    target_txt = str(self._target)
    dist_txt = f"{int(self._dist)} m" if self._dist >= 1.0 else ""

    tw = measure_text_cached(self._font_bold, target_txt, _TARGET_FS)
    lw = measure_text_cached(self._font_medium, label, _LABEL_FS)
    dw = measure_text_cached(self._font_medium, dist_txt, _DIST_FS) if dist_txt else rl.Vector2(0, 0)

    content_w = tw.x + _GAP + lw.x + ((_GAP + dw.x) if dist_txt else 0.0)
    pill_w = content_w + 2 * _PAD_X
    x = rect.x + (rect.width - pill_w) / 2.0
    y = rect.y + _TOP_OFFSET

    bg = rl.Color(0, 0, 0, int(180 * a))
    rl.draw_rectangle_rounded(rl.Rectangle(x, y, pill_w, _HEIGHT), 0.5, 12, bg)

    accent = rl.Color(ACCENT.r, ACCENT.g, ACCENT.b, int(255 * a))
    white = rl.Color(255, 255, 255, int(235 * a))
    dim = rl.Color(255, 255, 255, int(150 * a))

    cx = x + _PAD_X
    rl.draw_text_ex(self._font_bold, target_txt, rl.Vector2(cx, y + (_HEIGHT - tw.y) / 2.0), _TARGET_FS, 0, accent)
    cx += tw.x + _GAP
    rl.draw_text_ex(self._font_medium, label, rl.Vector2(cx, y + (_HEIGHT - lw.y) / 2.0), _LABEL_FS, 0, white)
    if dist_txt:
      cx += lw.x + _GAP
      rl.draw_text_ex(self._font_medium, dist_txt, rl.Vector2(cx, y + (_HEIGHT - dw.y) / 2.0), _DIST_FS, 0, dim)
