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
from __future__ import division

from vdsm.network import lldp
from vdsm.network.link.iface import iface

Lldp = lldp.driver()


def get_info(filter):
    """"
    Get LLDP information for all devices.
    """
    return {device: _get_info(device) for device in filter['devices']}


def _get_info(device):
    dev_info = {'enabled': False, 'tlvs': []}
    if iface(device).is_oper_up() and Lldp.is_lldp_enabled_on_iface(device):
        dev_info['enabled'] = True
        dev_info['tlvs'] = Lldp.get_tlvs(device)
    return dev_info
