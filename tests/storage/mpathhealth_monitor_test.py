#
# Copyright 2017 Red Hat, Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from __future__ import print_function

from vdsm.storage import devicemapper
from vdsm.storage import mpathhealth
from vdsm.storage import udev

from vdsm.storage.devicemapper import PathStatus


def test_no_events():
    monitor = mpathhealth.Monitor()
    assert monitor.status() == {}


def test_failed_path():
    monitor = mpathhealth.Monitor()
    event = udev.MultipathEvent(udev.PATH_FAILED, "uuid-1", "sda", 1)
    monitor.handle(event)
    expected = {
        "uuid-1": {
            "valid_paths": 1,
            "failed_paths": [
                "sda"
            ]
        }
    }
    assert monitor.status() == expected


def test_removed():
    monitor = mpathhealth.Monitor()
    event = udev.MultipathEvent(udev.PATH_FAILED, "uuid-1", "sda", 1)
    monitor.handle(event)
    event = udev.MultipathEvent(udev.MPATH_REMOVED, "uuid-1", None, None)
    monitor.handle(event)
    assert monitor.status() == {}


def test_removed_not_existing():
    monitor = mpathhealth.Monitor()
    event = udev.MultipathEvent(udev.MPATH_REMOVED, "uuid-1", None, None)
    monitor.handle(event)
    assert monitor.status() == {}


def test_multiple_mpath():
    monitor = mpathhealth.Monitor()
    events = [
        udev.MultipathEvent(udev.PATH_FAILED, "uuid-1", "sdaa", 1),
        udev.MultipathEvent(udev.PATH_FAILED, "uuid-2", "sdba", 2)
    ]
    for e in events:
        monitor.handle(e)
    expected = {
        "uuid-2": {
            "valid_paths": 2,
            "failed_paths": [
                "sdba"
            ]
        },
        "uuid-1": {
            "valid_paths": 1,
            "failed_paths": [
                "sdaa"
            ]
        }
    }
    assert monitor.status() == expected


def test_multiple_mpath_paths():
    monitor = mpathhealth.Monitor()
    events = [
        udev.MultipathEvent(udev.PATH_FAILED, "uuid-1", "sdaa", 1),
        udev.MultipathEvent(udev.PATH_FAILED, "uuid-2", "sdba", 2),
        udev.MultipathEvent(udev.PATH_FAILED, "uuid-1", "sdab", 0),
        udev.MultipathEvent(udev.PATH_FAILED, "uuid-2", "sdbb", 1)
    ]
    for e in events:
        monitor.handle(e)
    expected = {
        "uuid-2": {
            "valid_paths": 1,
            "failed_paths": [
                "sdba",
                "sdbb"
            ]
        },
        "uuid-1": {
            "valid_paths": 0,
            "failed_paths": [
                "sdaa",
                "sdab"
            ]
        }
    }
    assert monitor.status() == expected


def test_reinstated_path_no_mpath():
    monitor = mpathhealth.Monitor()
    event = udev.MultipathEvent(udev.PATH_REINSTATED, "uuid-1", "sdaa", 1)
    monitor.handle(event)
    assert monitor.status() == {}


def test_reinstated_last_path():
    monitor = mpathhealth.Monitor()
    events = [
        udev.MultipathEvent(udev.PATH_FAILED, "uuid-1", "sdaa", 1),
        udev.MultipathEvent(udev.PATH_REINSTATED, "uuid-1", "sdaa", 2)
    ]
    for e in events:
        monitor.handle(e)
    assert monitor.status() == {}


def test_reinstated__path():
    monitor = mpathhealth.Monitor()
    events = [
        udev.MultipathEvent(udev.PATH_FAILED, "uuid-1", "sdaa", 2),
        udev.MultipathEvent(udev.PATH_FAILED, "uuid-1", "sdab", 1),
        udev.MultipathEvent(udev.PATH_REINSTATED, "uuid-1", "sdaa", 2)
    ]
    for e in events:
        monitor.handle(e)
    expected = {
        "uuid-1": {
            "valid_paths": 2,
            "failed_paths": [
                "sdab"
            ]
        }
    }
    assert monitor.status() == expected


def test_start_some_failed(monkeypatch):

    def fake_status():
        return {
            'uuid-1':
                [
                    PathStatus('sda', 'F'),
                    PathStatus('sdb', 'A'),
                    PathStatus('sdc', 'A')
                ]
        }

    monkeypatch.setattr(devicemapper, 'multipath_status', fake_status)

    monitor = mpathhealth.Monitor()
    monitor.start()
    expected = {
        "uuid-1": {
            "valid_paths": 2,
            "failed_paths": [
                "sda"
            ]
        }
    }
    assert monitor.status() == expected


def test_start_all_active(monkeypatch):

    def fake_status():
        return {
            'uuid-1':
                [
                    PathStatus('sda', 'A'),
                    PathStatus('sdb', 'A'),
                    PathStatus('sdc', 'A')
                ]
        }

    monkeypatch.setattr(devicemapper, 'multipath_status', fake_status)

    monitor = mpathhealth.Monitor()
    monitor.start()
    assert monitor.status() == {}


def test_start_all_failed(monkeypatch):

    def fake_status():
        return {
            'uuid-1':
                [
                    PathStatus('sda', 'F'),
                    PathStatus('sdb', 'F'),
                    PathStatus('sdc', 'F')
                ]
        }

    monkeypatch.setattr(devicemapper, 'multipath_status', fake_status)

    monitor = mpathhealth.Monitor()
    monitor.start()
    expected = {
        "uuid-1": {
            "valid_paths": 0,
            "failed_paths": [
                "sda",
                "sdb",
                "sdc"
            ]
        }
    }
    assert monitor.status() == expected


def test_events_after_start(monkeypatch):

    def fake_status():
        return {
            'uuid-1':
                [
                    PathStatus('sda', 'F'),
                    PathStatus('sdb', 'A'),
                    PathStatus('sdc', 'A')
                ]
        }

    monkeypatch.setattr(devicemapper, 'multipath_status', fake_status)

    monitor = mpathhealth.Monitor()
    monitor.start()

    events = [
        udev.MultipathEvent(udev.PATH_FAILED, "uuid-1", "sdb", 1),
        udev.MultipathEvent(udev.PATH_FAILED, "uuid-1", "sdc", 0),
        udev.MultipathEvent(udev.PATH_REINSTATED, "uuid-1", "sda", 1)
    ]
    for e in events:
        monitor.handle(e)
    expected = {
        "uuid-1": {
            "valid_paths": 1,
            "failed_paths": [
                "sdb",
                "sdc"
            ]
        }
    }
    assert monitor.status() == expected
