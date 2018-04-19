#
# Copyright 2016 Red Hat, Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published
# by the Free Software Foundation; either version 2 of the License, or
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
"""
Runner implemts the middle layer between container Domain and low level
Commands. Translates high level actions (e.g. start container) to
low-level command lines, and executes them.
"""

from __future__ import absolute_import
from __future__ import division

import logging
import os
import os.path

from vdsm.config import config

from . import command


PREFIX = 'vdsm-'
_SERVICE_EXT = ".service"


class OperationFailed(Exception):
    """
    Base exception for runner-related operations. Raised when a container
    failed to start, or stop.
    """


class Runner(object):

    _log = logging.getLogger('virt.containers.runner')

    def __init__(self, unit_name):
        self._unit_name = unit_name
        self._running = False

    @property
    def running(self):
        return self._running

    def stop(self):
        command.systemctl_stop(name=self._unit_name)
        self._running = False

    def start(self, *args):
        command.systemd_run(
            self._unit_name,
            config.get('containers', 'cgroup_slice'),
            *args)
        self._running = True

    def recover(self):
        self._running = True

    @classmethod
    def get_all(cls):
        output = command.systemctl_list(prefix=PREFIX)
        for item in _parse_systemctl_list_units(output):
            yield item


def _vm_uuid_from_unit(unit):
    name, ext = os.path.splitext(unit)
    if ext != _SERVICE_EXT:  # TODO: check this
        raise ValueError(unit)
    return name.replace(PREFIX, '', 1)


def _parse_systemctl_list_units(output):
    for line in output.splitlines():
        if not line:
            continue
        try:
            unit, loaded, active, sub, desc = line.split(None, 4)
        except ValueError:
            logging.warning('unexpected systemctl line: %r', line)
            continue
        if not _is_running_unit(loaded, active, sub):
            continue
        try:
            yield _vm_uuid_from_unit(unit)
        except ValueError:
            pass


def _is_running_unit(loaded, active, sub):
    return (
        loaded == 'loaded' and
        active == 'active' and
        sub == 'running'
    )
