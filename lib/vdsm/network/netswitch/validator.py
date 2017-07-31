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

import six

from vdsm.network import errors as ne
from vdsm.network.kernelconfig import KernelConfig


def validate_southbound_devices_usages(nets, ni):
    kernel_config = KernelConfig(ni)

    for requested_net, net_info in six.viewitems(nets):
        if 'remove' in net_info:
            kernel_config.removeNetwork(requested_net)

    for requested_net, net_info in six.viewitems(nets):
        if 'remove' in net_info:
            continue
        kernel_config.setNetwork(requested_net, net_info)

    underlying_devices = []
    for net_attrs in six.viewvalues(kernel_config.networks):
        vlan = net_attrs.get('vlan')
        if 'bonding' in net_attrs:
            underlying_devices.append((net_attrs['bonding'], vlan))
        elif 'nic' in net_attrs:
            underlying_devices.append((net_attrs['nic'], vlan))

    if len(set(underlying_devices)) < len(underlying_devices):
        raise ne.ConfigNetworkError(
            ne.ERR_BAD_PARAMS,
            'multiple networks/similar vlans cannot be'
            ' defined on a single underlying device. '
            'kernel networks: {}\nrequested networks: {}'.format(
                kernel_config.networks,
                nets))
