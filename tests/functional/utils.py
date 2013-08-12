#
# Copyright 2013 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#
from collections import namedtuple
from contextlib import contextmanager
from functools import wraps
import time
import threading

from vdsm import netinfo
from vdsm import vdscli
from vdsm import utils


SUCCESS = 0
Qos = namedtuple('Qos', 'inbound outbound')


service = utils.CommandPath("service",
                            "/sbin/service",      # EL6
                            "/usr/sbin/service",  # Fedora
                            )


def cleanupNet(func):
    """
    Restored a previously persisted network config
    in case of a test failure, traceback is kept.
    Assumes root privileges.
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            func(*args, **kwargs)
        except Exception:
            # cleanup
            restoreNetConfig()
            raise
    return wrapper


def restoreNetConfig():
    cmd_service = [service.cmd, "vdsm-restore-net-config", "restart"]
    utils.execCmd(cmd_service, sudo=True)


class VdsProxy(object):
    """
    Vdscli wrapper to save tests
    from common boilerplate code.
    """

    def __init__(self):
        self.vdscli = vdscli.connect()
        self.netinfo = \
            netinfo.NetInfo(self.vdscli.getVdsCapabilities()['info'])

    def netinfo_altering(func):
        """Updates the cached information that might have been altered by an
        api call that has side-effects on the server."""
        @wraps(func)
        def call_and_update(self, *args, **kwargs):
            ret = func(self, *args, **kwargs)
            self.netinfo = \
                netinfo.NetInfo(self.vdscli.getVdsCapabilities()['info'])
            return ret
        return call_and_update

    def _get_net_args(self, vlan, bond, nics, opts):
        if vlan is None:
            vlan = ''
        if bond is None:
            bond = ''
        if nics is None:
            nics = ''
        if opts is None:
            opts = {}
        return [vlan, bond, nics, opts]

    def save_config(self):
        self.vdscli.setSafeNetworkConfig()

    @netinfo_altering
    def addNetwork(self, bridge, vlan=None, bond=None, nics=None, opts=None):
        result = self.vdscli.addNetwork(bridge,
                                        *self._get_net_args(vlan, bond, nics,
                                                            opts))
        return result['status']['code'], result['status']['message']

    @netinfo_altering
    def delNetwork(self, bridge, vlan=None, bond=None, nics=None, opts=None):
        result = self.vdscli.delNetwork(bridge,
                                        *self._get_net_args(vlan, bond, nics,
                                                            opts))
        return result['status']['code'], result['status']['message']

    @netinfo_altering
    def editNetwork(self, oldBridge, newBridge, vlan=None, bond=None,
                    nics=None, opts=None):
        result = self.vdscli.editNetwork(oldBridge, newBridge,
                                         *self._get_net_args(vlan, bond, nics,
                                                             opts))
        return result['status']['code'], result['status']['message']

    @netinfo_altering
    def setupNetworks(self, networks, bonds, options):
        result = self.vdscli.setupNetworks(networks, bonds, options)
        return result['status']['code'], result['status']['message']

    def networkExists(self, network_name, bridged=None):
        return network_name in self.netinfo.networks and \
            (bridged is None or
             self.netinfo.networks[network_name]['bridged'] == bridged)

    def bondExists(self, bond_name, nics=None):
        return bond_name in self.netinfo.bondings and \
            (not nics or set(nics) ==
             set(self.netinfo.bondings[bond_name]['slaves']))

    def vlanExists(self, vlan_name):
        dev = vlan_name.split('.')[0]
        return vlan_name in self.netinfo.vlans and \
            (not dev or dev ==
             self.netinfo.vlans[vlan_name]['iface'])

    def getMtu(self, name):
        if name in self.netinfo.networks:
            return self.netinfo.networks[name]['mtu']
        elif name in self.netinfo.vlans:
            return self.netinfo.vlans[name]['mtu']
        elif name in self.netinfo.bondings:
            return self.netinfo.bondings[name]['mtu']
        elif name in self.netinfo.nics:
            return self.netinfo.nics[name]['mtu']
        return None

    @contextmanager
    def pinger(self):
        """Keeps pinging vdsm for operations that need it"""
        def ping():
            while not done:
                self.vdscli.ping()
                time.sleep(1)
        try:
            done = False
            pinger_thread = threading.Thread(target=ping)
            pinger_thread.start()
            yield
        except Exception:
            raise
        finally:
            done = True

    def networkQos(self, networkName):
        network = self.netinfo.networks[networkName]
        return network['qosInbound'], network['qosOutbound']

    def setMOMPolicy(self, policyStr):
        result = self.vdscli.setMOMPolicy(policyStr)
        return result['status']['code'], result['status']['message']

    def setBalloonTarget(self, vmId, target):
        result = self.vdscli.setBalloonTarget(vmId, target)
        return result['status']['code'], result['status']['message']

    def getVdsStats(self):
        result = self.vdscli.getVdsStats()
        return result['status']['code'], result['status']['message'],\
            result['info']

    def getAllVmStats(self):
        result = self.vdscli.getAllVmStats()
        return result['status']['code'], result['status']['message'],\
            result['statsList']

    def getVmStats(self, vmId):
        result = self.vdscli.getVmStats(vmId)
        if 'statsList' in result:
            return result['status']['code'], result['status']['message'],\
                result['statsList'][0]
        else:
            return result['status']['code'], result['status']['message']

    def getVdsCapabilities(self):
        result = self.vdscli.getVdsCapabilities()
        return result['status']['code'], result['status']['message'],\
            result['info']

    def create(self, vmParams):
        result = self.vdscli.create(vmParams)
        return result['status']['code'], result['status']['message']

    def destroy(self, vmId):
        result = self.vdscli.destroy(vmId)
        return result['status']['code'], result['status']['message']

    def hotplugNic(self, params):
        result = self.vdscli.hotplugNic(params)
        return result['status']['code'], result['status']['message']

    def hotunplugNic(self, params):
        result = self.vdscli.hotunplugNic(params)
        return result['status']['code'], result['status']['message']

    def hotplugDisk(self, params):
        result = self.vdscli.hotplugDisk(params)
        return result['status']['code'], result['status']['message']

    def hotunplugDisk(self, params):
        result = self.vdscli.hotunplugDisk(params)
        return result['status']['code'], result['status']['message']
