#
# Copyright 2015-2020 Hat, Inc.
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
from __future__ import absolute_import
from __future__ import division

import io
from functools import partial

from vdsm.network.ipwrapper import Link
from vdsm.network.link import dpdk
from .misc import visible_devs

OPERSTATE_UP = 'up'


nics = partial(visible_devs, Link.isNICLike)


def operstate(nic_name):
    if dpdk.is_dpdk(nic_name):
        return dpdk.operstate(nic_name)
    with io.open('/sys/class/net/%s/operstate' % nic_name) as operstateFile:
        return operstateFile.read().strip()


def info(link):
    return {'hwaddr': link.address}
