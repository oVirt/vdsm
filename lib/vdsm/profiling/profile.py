# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division
"""
profiling facade.
"""

from . import cpu
from . import memory


def start():
    cpu.start()
    memory.start()


def stop():
    cpu.stop()
    memory.stop()


def status():
    res = {}
    for profiler in (cpu, memory):
        res[profiler.__name__] = {
            "enabled": profiler.is_enabled(),
            "running": profiler.is_running()
        }
    return res
