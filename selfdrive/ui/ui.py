#!/usr/bin/env python3
import time

from cereal import messaging
from openpilot.system.ui.lib.application import gui_app
from openpilot.selfdrive.ui.layouts.main import MainLayout
from openpilot.selfdrive.ui.mici.layouts.main import MiciMainLayout
from openpilot.selfdrive.ui.ui_state import ui_state

BIG_UI = gui_app.big_ui()


def main():
  # The UI runs at normal (SCHED_OTHER) scheduling and must never preempt the
  # driving stack. It used to grab RT SCHED_FIFO priority 53 ("above plannerd
  # and radard") via config_realtime_process; once the mici UI got heavy, a
  # single render frame preempted locationd/paramsd/plannerd/radard mid-loop,
  # so they missed their deadlines and published valid=False -- a cascade
  # selfdrived reports as commIssue ("TAKE CONTROL IMMEDIATELY"). Left on normal
  # scheduling the UI renders best-effort and every driving process (all
  # SCHED_FIFO) rightfully preempts it. No core pinning: the isolated cores
  # (isolcpus=6,7) are unreachable for a SCHED_OTHER task anyway, and the
  # housekeeping cores (0-3) are always online, so we let the scheduler place us.
  gui_app.init_window("UI")
  if BIG_UI:
    MainLayout()
  else:
    MiciMainLayout()

  pm = messaging.PubMaster(['uiDebug'])
  for should_render, frame_time, cpu_time in gui_app.render():
    extra_start = time.monotonic()
    ui_state.update()

    if should_render:
      extra_cpu = time.monotonic() - extra_start
      msg = messaging.new_message('uiDebug')
      msg.uiDebug.cpuTimeMillis = (cpu_time + extra_cpu) * 1000
      msg.uiDebug.frameTimeMillis = frame_time * 1000
      pm.send('uiDebug', msg)


if __name__ == "__main__":
  main()
