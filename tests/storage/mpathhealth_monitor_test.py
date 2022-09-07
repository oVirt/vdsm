# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import pytest
import threading

from vdsm.storage import devicemapper
from vdsm.storage import mpathhealth
from vdsm.storage.devicemapper import PathStatus

MONITOR_INTERVAL = 0.001
CYCLE_TIMEOUT = 5


class FakeMultipathStatus(object):

    def __init__(self):
        self.out = {}

    def __call__(self):
        return self.out


class MonitorCallback(object):
    """
    Callable callback class used for synchronization of the health monitor
    thread with tests.

    Usage overview:

    1. tmp_monitor fixture sets the monitor callback to a MonitorCallback
       instance.
    2. Test sets the fake status input for the monitor.
    3. Test starts the monitor instance.
    4. Test calls the monitor.callback.wait() to wait for monitor to finish
       processing the fake status input.
    5. Monitor calls callback() implementation and sets the done event to
       signal the test it has finished processing the fake status input.
    6. Monitor instance halts after its first cycle on provided callback()
       implementation.
    7. Test can now assert on the monitor status output.
    8. For testing additional cycles of the same monitor, test calls
       monitor.callback.resume() after setting the next cycle fake input.
    """

    def __init__(self):
        self.ready = threading.Event()
        self.done = threading.Event()

    def __call__(self):
        """
        Called from monitor thread loop after every cycle, wait for status
        to be set by the test before starting the next cycle.
        """
        self.done.set()
        self.ready.wait()

    def wait(self):
        """
        Called by tests to wait for monitor to finish its current cycle.
        """
        self.ready.clear()
        self.done.wait()

    def resume(self):
        """
        Called by tests to resume monitor from waiting for status setup.
        """
        self.done.clear()
        self.ready.set()


@pytest.fixture
def tmp_monitor(monkeypatch):
    monkeypatch.setattr(
        devicemapper, "multipath_status", FakeMultipathStatus())
    monitor = mpathhealth.Monitor(MONITOR_INTERVAL)
    monitor.callback = MonitorCallback()
    yield monitor
    # Do not hang the monitor cycles when we are about to stop
    monitor.callback.resume()
    monitor.stop()
    monitor.wait()


def test_no_info(tmp_monitor):
    tmp_monitor.start()
    # Wait the first cycle default empty status
    tmp_monitor.callback.wait()
    assert tmp_monitor.status() == {}


def test_failed_path(tmp_monitor):
    devicemapper.multipath_status.out = {
        "uuid-1": [
            PathStatus("8:11", "F"),
            PathStatus("6:66", "A")
        ]
    }
    tmp_monitor.start()
    tmp_monitor.callback.wait()

    assert tmp_monitor.status() == {
        "uuid-1": {
            "failed_paths": ["8:11"],
            "valid_paths": 1
        }
    }


def test_removed_uuid(tmp_monitor):
    devicemapper.multipath_status.out = {
        "uuid-1": [PathStatus("8:11", "F")],
        "uuid-2": [PathStatus("6:66", "A")],
    }
    tmp_monitor.start()
    tmp_monitor.callback.wait()

    assert tmp_monitor.status() == {
        "uuid-1": {
            "failed_paths": ["8:11"],
            "valid_paths": 0
        }
    }

    devicemapper.multipath_status.out = {
        "uuid-2": [PathStatus("6:66", "A")]
    }
    tmp_monitor.callback.resume()
    tmp_monitor.callback.wait()

    assert tmp_monitor.status() == {}


def test_removed_device(tmp_monitor):
    devicemapper.multipath_status.out = {
        "uuid-1": [
            PathStatus("8:44", "A"),
            PathStatus("8:11", "A"),
            PathStatus("8:32", "F")
        ]
    }
    tmp_monitor.start()
    tmp_monitor.callback.wait()

    assert tmp_monitor.status() == {
        "uuid-1": {
            "failed_paths": ["8:32"],
            "valid_paths": 2
        }
    }

    devicemapper.multipath_status.out = {
        "uuid-1": [
            PathStatus("8:44", "A"),
            PathStatus("8:32", "F")
        ]
    }
    tmp_monitor.callback.resume()
    tmp_monitor.callback.wait()

    assert tmp_monitor.status() == {
        "uuid-1": {
            "failed_paths": ["8:32"],
            "valid_paths": 1
        }
    }


def test_multiple_mpath(tmp_monitor):

    devicemapper.multipath_status.out = {
        "uuid-1": [
            PathStatus("8:11", "F"),
            PathStatus("6:66", "A")
        ],
        "uuid-2": [
            PathStatus("7:12", "A"),
            PathStatus("8:32", "F"),
            PathStatus("12:16", "A")
        ]
    }
    tmp_monitor.start()
    tmp_monitor.callback.wait()

    assert tmp_monitor.status() == {
        "uuid-2": {
            "failed_paths": ["8:32"],
            "valid_paths": 2
        },
        "uuid-1": {
            "failed_paths": ["8:11"],
            "valid_paths": 1
        }
    }


def test_reinstated(tmp_monitor):
    devicemapper.multipath_status.out = {
        "uuid-1": [
            PathStatus("8:11", "A"),
            PathStatus("8:32", "F"),
            PathStatus("6:66", "F")
        ]
    }
    tmp_monitor.start()
    tmp_monitor.callback.wait()

    assert tmp_monitor.status() == {
        "uuid-1": {
            "failed_paths": ["6:66", "8:32"],
            "valid_paths": 1
        }
    }

    devicemapper.multipath_status.out = {
        "uuid-1": [
            PathStatus("8:11", "A"),
            PathStatus("8:32", "A"),
            PathStatus("6:66", "F")
        ]
    }
    tmp_monitor.callback.resume()
    tmp_monitor.callback.wait()

    assert tmp_monitor.status() == {
        "uuid-1": {
            "failed_paths": ["6:66"],
            "valid_paths": 2
        }
    }


def test_reinstated_last_failed(tmp_monitor):
    devicemapper.multipath_status.out = {
        "uuid-1": [
            PathStatus("8:11", "A"),
            PathStatus("6:66", "F")
        ]
    }
    tmp_monitor.start()
    tmp_monitor.callback.wait()

    assert tmp_monitor.status() == {
        "uuid-1": {
            "failed_paths": ["6:66"],
            "valid_paths": 1
        }
    }

    devicemapper.multipath_status.out = {
        "uuid-1": [
            PathStatus("8:11", "A"),
            PathStatus("6:66", "A")
        ]
    }
    tmp_monitor.callback.resume()
    tmp_monitor.callback.wait()

    assert tmp_monitor.status() == {}


def test_error(tmp_monitor):

    def fail():
        raise RuntimeError

    def status_before_fail():
        return {
            "uuid-1": [
                PathStatus("8:11", "A"),
                PathStatus("6:66", "F")
            ]
        }

    def status_after_fail():
        return {
            "uuid-1": [
                PathStatus("8:11", "A"),
                PathStatus("6:66", "A")
            ],
            "uuid-2": [
                PathStatus("3:34", "F"),
                PathStatus("7:17", "A")
            ]
        }

    initial_health_status = {
        "uuid-1": {
            "failed_paths": ["6:66"],
            "valid_paths": 1
        }
    }

    # Testing working cycle
    devicemapper.multipath_status = status_before_fail
    tmp_monitor.start()
    tmp_monitor.callback.wait()

    assert tmp_monitor.status() == initial_health_status

    # Testing failing cycle
    devicemapper.multipath_status = fail
    tmp_monitor.callback.resume()
    tmp_monitor.callback.wait()

    # Expecting to have old status reported before failure
    assert tmp_monitor.status() == initial_health_status

    # Testing next working cycle after failure
    devicemapper.multipath_status = status_after_fail
    tmp_monitor.callback.resume()
    tmp_monitor.callback.wait()

    assert tmp_monitor.status() == {
        "uuid-2": {
            "failed_paths": ["3:34"],
            "valid_paths": 1
        }
    }
