# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from vdsm.network.link.bond.sysfs_options import BONDING_MODES_NUMBER_TO_NAME
from vdsm.network.link.setup import parse_bond_options

from .schema import BondSchema
from .schema import Interface
from .schema import InterfaceIP
from .schema import InterfaceState
from .schema import InterfaceType


class Bond(object):
    def __init__(self, name, attrs):
        self._name = name
        self._attrs = attrs
        self._to_remove = attrs.get('remove', False)

    @property
    def name(self):
        return self._name

    @property
    def state(self):
        iface_state = {
            Interface.NAME: self._name,
            Interface.TYPE: InterfaceType.BOND,
        }

        if self._to_remove:
            extra_state = {Interface.STATE: InterfaceState.ABSENT}
        else:
            extra_state = self._create()

        iface_state.update(extra_state)
        return iface_state

    def is_new(self, running_bonds):
        return not self._to_remove and self._name not in running_bonds

    @staticmethod
    def generate_state(bondings, running_bonds):
        bonds = (
            Bond(bondname, bondattrs)
            for bondname, bondattrs in bondings.items()
        )
        state = {}
        for bond in bonds:
            ifstate = bond.state
            if bond.is_new(running_bonds):
                ifstate[Interface.IPV4] = {InterfaceIP.ENABLED: False}
                ifstate[Interface.IPV6] = {InterfaceIP.ENABLED: False}
            state[bond.name] = ifstate
        return state

    def _create(self):
        iface_state = {Interface.STATE: InterfaceState.UP}
        mac = self._attrs.get('hwaddr')
        if mac:
            iface_state[Interface.MAC] = mac
        bond_state = iface_state[BondSchema.CONFIG_SUBTREE] = {}
        bond_state[BondSchema.PORT] = sorted(self._attrs['nics'])

        options = parse_bond_options(self._attrs.get('options'))
        if options:
            bond_state[BondSchema.OPTIONS_SUBTREE] = options
        mode = self._translate_mode(mode=options.pop('mode', 'balance-rr'))
        bond_state[BondSchema.MODE] = mode
        return iface_state

    def _translate_mode(self, mode):
        return BONDING_MODES_NUMBER_TO_NAME[mode] if mode.isdigit() else mode
