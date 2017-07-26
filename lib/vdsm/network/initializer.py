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
from __future__ import absolute_import

import logging

from vdsm.network import lldp
from vdsm.network import netswitch
from vdsm.network.nm import networkmanager

Lldp = lldp.driver()


def init_privileged_network_components():
    networkmanager.init()
    _lldp_init()


def _lldp_init():
    """"
    Enables receiving of LLDP frames for all nics. If sending or receiving
    LLDP frames is already enabled on a nic, it is not modified.
    """
    for device in netswitch.netinfo()['nics']:
        if not Lldp.is_lldp_enabled_on_iface(device):
            try:
                Lldp.enable_lldp_on_iface(device)
            except lldp.EnableLldpError:
                logging.exception('Failed to enable LLDP on %s', device)
