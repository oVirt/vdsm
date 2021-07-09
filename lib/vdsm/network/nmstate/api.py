# Copyright 2019-2021 Red Hat, Inc.
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

import logging

from libnmstate import apply as state_apply
from libnmstate import show as state_show

from vdsm.network.common.switch_util import split_switch_type
from vdsm.network.netconfpersistence import RunningConfig

from .bond import Bond
from .bridge_util import is_autoconf_enabled as util_is_autoconf_enabled
from .bridge_util import is_dhcp_enabled as util_is_dhcp_enabled
from .bridge_util import translate_config
from .linux_bridge import LinuxBridgeNetwork as LinuxBrNet
from .ovs.info import OvsInfo
from .ovs.info import OvsNetInfo
from .ovs.network import generate_state as ovs_generate_state
from .schema import Interface
from .sriov import create_sriov_state
from .state import CurrentState


def setup(desired_state, verify_change):
    state_apply(desired_state, verify_change=verify_change)


def generate_state(networks, bondings):
    """Generate a new nmstate state given VDSM setup state format"""
    rconfig = RunningConfig()
    current_state = get_current_state()

    ovs_nets, linux_br_nets = split_switch_type(networks, rconfig.networks)
    ovs_bonds, linux_br_bonds = split_switch_type(bondings, rconfig.bonds)
    ovs_requested = ovs_nets or ovs_bonds
    linux_br_requested = linux_br_nets or linux_br_bonds

    net_state = (
        ovs_generate_state(networks, rconfig.networks, current_state)
        if ovs_requested
        else LinuxBrNet.generate_state(
            networks, rconfig.networks, current_state
        )
    )

    net_state.add_bond_state(Bond.generate_state(bondings, rconfig.bonds))
    net_state.update_mtu(linux_br_requested, current_state.interfaces_state)

    return net_state.state()


def get_current_state():
    state = state_show()
    return CurrentState(state)


def is_dhcp_enabled(ifstate, family):
    family_info = ifstate[family]
    return util_is_dhcp_enabled(family_info)


def is_autoconf_enabled(ifstate):
    family_info = ifstate[Interface.IPV6]
    return util_is_autoconf_enabled(family_info)


def ovs_netinfo(base_netinfo, running_networks, current_state):
    rnets_config = translate_config(running_networks)
    ovs_info = OvsInfo(rnets_config, current_state)
    netinfo = OvsNetInfo(ovs_info, base_netinfo, rnets_config, current_state)
    netinfo.create_netinfo()


def update_num_vfs(device, num_vfs):
    desired_state = create_sriov_state(device, num_vfs)
    logging.info('Desired state: %s', desired_state)

    setup(desired_state, verify_change=True)
