# Copyright 2016-2017 Red Hat, Inc.
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
from __future__ import absolute_import

import os

from vdsm.network import ipwrapper
from vdsm.network.netlink import link
from vdsm.network.netlink.link import get_link, is_link_up
from vdsm.network.netlink.waitfor import waitfor_linkup


STATE_UP = 'up'
STATE_DOWN = 'down'

NET_PATH = '/sys/class/net'


def up(dev, admin_blocking=True, oper_blocking=False):
    """
    Set link state to UP, optionally blocking on the action.
    :param dev: iface name.
    :param admin_blocking: Block until the administrative state changes to UP.
    :param oper_blocking: Block until the link is operational.
    admin state is at kernel level, while link state is at driver level.
    """
    if admin_blocking:
        _up_blocking(dev, oper_blocking)
    else:
        ipwrapper.linkSet(dev, [STATE_UP])


def down(dev):
    ipwrapper.linkSet(dev, ['down'])


def is_up(dev):
    return is_admin_up(dev)


def is_admin_up(dev):
    return is_link_up(get_link(dev)['flags'], check_oper_status=False)


def is_oper_up(dev):
    return is_link_up(get_link(dev)['flags'], check_oper_status=True)


def is_promisc(dev):
    return bool(get_link(dev)['flags'] & link.IFF_PROMISC)


def exists(dev):
    return os.path.exists(os.path.join(NET_PATH, dev))


def set_mac_address(dev, mac_address, vf_num=None):
    if vf_num is None:
        ipwrapper.linkSet(dev, ['address', mac_address])
    else:
        ipwrapper.linkSet(dev, ['vf', str(vf_num), 'mac', mac_address])


def mac_address(dev):
    return get_link(dev)['address']


def _up_blocking(dev, link_blocking):
    with waitfor_linkup(dev, link_blocking):
        ipwrapper.linkSet(dev, [STATE_UP])
