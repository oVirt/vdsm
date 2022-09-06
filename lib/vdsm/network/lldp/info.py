# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

from vdsm.network import lldp
from vdsm.network.link.iface import iface

Lldp = lldp.driver()


def get_info(filter):
    """
    Get LLDP information for all devices.
    """
    return {device: _get_info(device) for device in filter['devices']}


def _get_info(device):
    dev_info = {'enabled': False, 'tlvs': []}
    if iface(device).is_oper_up() and Lldp.is_lldp_enabled_on_iface(device):
        dev_info['enabled'] = True
        dev_info['tlvs'] = Lldp.get_tlvs(device)
    return dev_info
