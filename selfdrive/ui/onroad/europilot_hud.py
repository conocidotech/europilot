import pyray as rl

from cereal import messaging
from openpilot.common.swaglog import cloudlog
from openpilot.system.ui.lib.application import gui_app, FontWeight
from openpilot.system.ui.lib.text_measure import measure_text_cached

from europilot.hud_format import context_chip, sign_number, source_badge

# European speed-limit roundel: white face, red ring, black number.
SIGN_D = 168
RING = 18

_RED = rl.Color(201, 41, 41, 255)
_WHITE = rl.WHITE
_BLACK = rl.Color(20, 20, 20, 255)
_BADGE_BG = rl.Color(0, 0, 0, 160)
_BADGE_FG = rl.Color(255, 255, 255, 220)
_CHIP_BG = rl.Color(46, 106, 199, 220)  # NL sign blue


class EuropilotHud:
  """Advisory-only onroad overlay: the fused NL speed limit + road context.

  Owns its own SubMaster so it stays self-contained -- no change to ui_state.
  Fails closed and silent: missing or invalid messages, or any error at all,
  draw nothing and never disturb the rest of the UI (this is advisory data, it
  must never take the screen down).
  """

  def __init__(self):
    try:
      self._sm = messaging.SubMaster(["euSpeedLimit", "euMapAdvisory"])
    except Exception:
      cloudlog.exception("europilot HUD: could not subscribe; disabled")
      self._sm = None
    self._font_bold = gui_app.font(FontWeight.BOLD)
    self._font_semi = gui_app.font(FontWeight.SEMI_BOLD)

  def render(self, rect: rl.Rectangle) -> None:
    if self._sm is None:
      return
    try:
      self._sm.update(0)
      self._draw(rect)
    except Exception:
      cloudlog.exception("europilot HUD render failed")

  def _draw(self, rect: rl.Rectangle) -> None:
    sl = self._sm["euSpeedLimit"] if self._sm.valid["euSpeedLimit"] else None
    number = sign_number(sl.valid, sl.speedLimit) if sl is not None else None
    if number is None:
      return  # nothing trustworthy to show

    # Roundel top-left, below the MAX box and clear of the current-speed readout.
    r = SIGN_D // 2
    cx = int(rect.x + 60 + r)
    cy = int(rect.y + 300 + r)

    rl.draw_circle(cx, cy, r, _RED)
    rl.draw_circle(cx, cy, r - RING, _WHITE)

    fs = 84 if len(number) < 3 else 66
    size = measure_text_cached(self._font_bold, number, fs)
    rl.draw_text_ex(self._font_bold, number,
                    rl.Vector2(cx - size.x / 2, cy - size.y / 2), fs, 0, _BLACK)

    y = cy + r + 14
    badge = source_badge(str(sl.source))
    if badge:
      y = self._pill(cx, y, badge, _BADGE_BG, _BADGE_FG)

    adv = self._sm["euMapAdvisory"] if self._sm.valid["euMapAdvisory"] else None
    if adv is not None:
      chip = context_chip(adv.valid, adv.inResidential, adv.cyclestreet)
      if chip:
        self._pill(cx, y, chip, _CHIP_BG, _WHITE)

  def _pill(self, cx: int, y: int, text: str, bg: rl.Color, fg: rl.Color) -> int:
    fs = 34
    size = measure_text_cached(self._font_semi, text, fs)
    pad_x, pad_y = 20, 8
    w = int(size.x) + 2 * pad_x
    h = int(size.y) + 2 * pad_y
    x = int(cx - w / 2)
    rl.draw_rectangle_rounded(rl.Rectangle(x, y, w, h), 0.5, 8, bg)
    rl.draw_text_ex(self._font_semi, text, rl.Vector2(x + pad_x, y + pad_y), fs, 0, fg)
    return y + h + 8
