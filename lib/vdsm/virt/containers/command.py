#
# Copyright 2016-2017 Red Hat, Inc.
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
System commands facade
"""

from __future__ import absolute_import
from __future__ import division

from vdsm.common import cmdutils
from vdsm.common import commands
from vdsm.common import supervdsm

_SYSTEMCTL = cmdutils.CommandPath("systemctl",
                                  "/bin/systemctl",
                                  "/usr/bin/systemctl",
                                  )


class Failed(Exception):
    pass


def systemd_run(unit_name, cgroup_slice, *args):
    return _result(
        supervdsm.getProxy().systemd_run(unit_name, cgroup_slice, *args)
    )


def systemctl_stop(name):
    return _result(
        supervdsm.getProxy().systemctl_stop(name)
    )


def systemctl_list(prefix):
    return _result(
        commands.execCmd([
            _SYSTEMCTL.cmd,
            'list-units',
            '--no-pager',
            '--no-legend',
            '%s*' % prefix,
        ], raw=True)
    )


def docker_net_inspect(network):
    return _result(
        supervdsm.getProxy().docker_net_inspect(network)
    )


def docker_net_create(subnet, gw, nic, network):
    return _result(
        supervdsm.getProxy().docker_net_create(subnet, gw, nic, network)
    )


def docker_net_remove(network):
    return _result(
        supervdsm.getProxy().docker_net_remove(network)
    )


def _result(ret):
    rc, out, err = ret
    if rc != 0:
        raise Failed(err)
    return out
