#!/usr/bin/env python3
import os
import time

from cereal import messaging
from openpilot.system.hardware import TICI
from openpilot.common.realtime import set_core_affinity
from openpilot.system.ui.lib.application import gui_app
from openpilot.selfdrive.ui.layouts.main import MainLayout
from openpilot.selfdrive.ui.mici.layouts.main import MiciMainLayout
from openpilot.selfdrive.ui.ui_state import ui_state

BIG_UI = gui_app.big_ui()


def main():
  # The UI runs at normal (SCHED_OTHER) scheduling on its own core and must
  # never preempt the driving stack. It used to grab RT SCHED_FIFO priority 53
  # ("above plannerd and radard"): once the mici UI got heavy, a single render
  # frame would preempt locationd/paramsd/plannerd/radard mid-loop, so they
  # missed their deadlines and published valid=False -- a cascade selfdrived
  # reports as commIssue ("TAKE CONTROL IMMEDIATELY"). Every driving process is
  # SCHED_FIFO and rightfully preempts us now. Core 6 is free of the
  # control (4), planner (5), model (7) and locationd (0-3) processes.
  cores = {6, }
  set_core_affinity(list(cores))

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
      # reaffine after power save offlines our core
      if TICI and os.sched_getaffinity(0) != cores:
        try:
          set_core_affinity(list(cores))
        except OSError:
          pass

      extra_cpu = time.monotonic() - extra_start
      msg = messaging.new_message('uiDebug')
      msg.uiDebug.cpuTimeMillis = (cpu_time + extra_cpu) * 1000
      msg.uiDebug.frameTimeMillis = frame_time * 1000
      pm.send('uiDebug', msg)


if __name__ == "__main__":
  main()
