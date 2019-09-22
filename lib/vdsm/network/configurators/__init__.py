# Copyright 2013-2017 Red Hat, Inc.
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
import logging

import six
from six.moves import configparser

from vdsm.common.config import config
from vdsm.network.netconfpersistence import RunningConfig
from vdsm.network.link import iface as link_iface

from ..errors import RollbackIncomplete
from . import qos
from ..models import Bond, hierarchy_vlan_tag, hierarchy_backing_device


class Configurator(object):
    def __init__(
        self, configApplier, net_info, is_unipersistence, inRollback=False
    ):
        self.configApplier = configApplier
        self.net_info = net_info
        self._inRollback = inRollback
        self.runningConfig = None
        self.unifiedPersistence = is_unipersistence

    def __enter__(self):
        return self

    def __exit__(self, type, value, traceback):
        if type is None:
            self.commit()
        elif self._inRollback:
            # If we failed the rollback transaction, the networking system
            # is in no good state and we fail hard
            logging.error(
                'Failed rollback transaction last known good ' 'network.',
                exc_info=(type, value, traceback),
            )
            self._clean_running_config_from_removed_nets()
        else:
            leftover = self.rollback()
            if leftover:
                raise RollbackIncomplete(leftover, type, value)

    def rollback(self):
        """
        returns None when all the nets were successfully rolled back, a
        vdsm.netoconfpersistence.Config object with the not yet rolled back
        networks and bonds.
        """
        # self.runningConfig will have all the changes that were applied before
        # we needed to rollback.
        return RunningConfig().diffFrom(self.runningConfig)

    def commit(self):
        raise NotImplementedError

    def configureBridge(self, bridge, **opts):
        raise NotImplementedError

    def configureVlan(self, vlan, **opts):
        raise NotImplementedError

    def configureBond(self, bond, **opts):
        raise NotImplementedError

    def editBonding(self, bond, _netinfo):
        raise NotImplementedError

    def configureNic(self, nic, **opts):
        raise NotImplementedError

    def removeBridge(self, bridge):
        raise NotImplementedError

    def removeVlan(self, vlan):
        raise NotImplementedError

    def removeBond(self, bonding):
        raise NotImplementedError

    def removeNic(self, nic):
        raise NotImplementedError

    def configureSourceRoute(self, routes, rules, device):
        raise NotImplementedError

    def removeSourceRoute(self, routes, rules, device):
        raise NotImplementedError

    @staticmethod
    def owned_device(device):
        raise NotImplementedError

    def configureQoS(self, hostQos, top_device):
        out = hostQos.get('out')
        if out is not None:
            dev_name = hierarchy_backing_device(top_device).name
            vlan_tag = hierarchy_vlan_tag(top_device)
            qos.configure_outbound(out, dev_name, vlan_tag)

    def removeQoS(self, top_device):
        dev_name = hierarchy_backing_device(top_device).name
        vlan_tag = hierarchy_vlan_tag(top_device)
        qos.remove_outbound(dev_name, vlan_tag, self.net_info)

    def _setNewMtu(self, iface, ifaceVlans):
        """
        Update an interface's MTU when one of its users is removed.

        :param iface: interface object (bond or nic device)
        :type iface: NetDevice instance

        :param ifaceVlans: vlan devices using the interface 'iface'
        :type ifaceVlans: iterable

        :return mtu value that was applied
        """
        ifaceMtu = link_iface.iface(iface.name).mtu()
        ifaces = tuple(ifaceVlans)
        maxMtu = (
            max(link_iface.iface(dev).mtu() for dev in ifaces)
            if ifaces
            else None
        )
        if maxMtu and maxMtu < ifaceMtu:
            if isinstance(iface, Bond):
                self.configApplier.setBondingMtu(iface.name, maxMtu)
            else:
                self.configApplier.setIfaceMtu(iface.name, maxMtu)
        return maxMtu

    def _clean_running_config_from_removed_nets(self):
        # Cleanup running config from networks that have been actually
        # removed but not yet removed from running config.
        running_config = RunningConfig()
        nets2remove = six.viewkeys(running_config.networks) - six.viewkeys(
            self.runningConfig.networks
        )
        for net in nets2remove:
            running_config.removeNetwork(net)
        running_config.save()


def getEthtoolOpts(name):
    try:
        opts = config.get('vars', 'ethtool_opts.' + name)
    except configparser.NoOptionError:
        opts = config.get('vars', 'ethtool_opts')
    return opts
