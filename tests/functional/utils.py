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
from contextlib import contextmanager
from functools import wraps
import inspect
import socket
import time
import threading

from vdsm.config import config
from vdsm.utils import retry
from vdsm import ipwrapper
from vdsm import netinfo
from vdsm import vdscli
from vdsm.netconfpersistence import RunningConfig
import supervdsm


SUCCESS = 0


def cleanupRules(func):
    """
    Restores previous routing rules
    in case of a test failure, traceback is kept.
    Assumes root privileges.
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            base = ipwrapper.ruleList()
            func(*args, **kwargs)
        except Exception:
            restoreRules(base)
            raise

    return wrapper


def restoreRules(base):
    current = ipwrapper.ruleList()
    added = set(current) - set(base)
    for rule in added:
        ipwrapper.ruleDel(ipwrapper.Rule.fromText(rule))


class VdsProxy(object):
    """
    Vdscli wrapper to save tests
    from common boilerplate code.
    """

    def __init__(self):
        retry(self.start, (socket.error, KeyError), tries=30)

    def start(self):
        self.vdscli = vdscli.connect()
        self.netinfo = self._get_netinfo()
        if config.get('vars', 'net_persistence') == 'unified':
            self.config = RunningConfig()
        else:
            self.config = None

    def __getattr__(self, attr):
        """
        When accessing nonexistant attribute it is looked up in self.vdscli
        and usual tuple
            (result['status']['code'], result['status']['message'])
        is returned
        """
        if hasattr(self.vdscli, attr):
            def wrapper(*args, **kwargs):
                result = getattr(self.vdscli, attr)(*args, **kwargs)
                return _parse_result(result)
            return wrapper

        raise AttributeError(attr)

    def netinfo_altering(func):
        """Updates the cached information that might have been altered by an
        api call that has side-effects on the server."""
        @wraps(func)
        def call_and_update(self, *args, **kwargs):
            ret = func(self, *args, **kwargs)
            self.netinfo = self._get_netinfo()
            if self.config is not None:
                self.config = RunningConfig()
            return ret
        return call_and_update

    def _get_netinfo(self):
        response = self.getVdsCapabilities()
        try:
            return netinfo.NetInfo(response[2])
        except IndexError:
            raise Exception('VdsProxy: getVdsCapabilities failed. '
                            'code:%s msg:%s' % (response[0], response[1]))

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
    def refreshNetinfo(self):
        pass

    @netinfo_altering
    def restoreNetConfig(self):
        supervdsm.getProxy().restoreNetworks()

    @netinfo_altering
    def addNetwork(self, bridge, vlan=None, bond=None, nics=None, opts=None):
        result = self.vdscli.addNetwork(bridge,
                                        *self._get_net_args(vlan, bond, nics,
                                                            opts))
        return _parse_result(result)

    @netinfo_altering
    def delNetwork(self, bridge, vlan=None, bond=None, nics=None, opts=None):
        result = self.vdscli.delNetwork(bridge,
                                        *self._get_net_args(vlan, bond, nics,
                                                            opts))
        return _parse_result(result)

    @netinfo_altering
    def editNetwork(self, oldBridge, newBridge, vlan=None, bond=None,
                    nics=None, opts=None):
        result = self.vdscli.editNetwork(oldBridge, newBridge,
                                         *self._get_net_args(vlan, bond, nics,
                                                             opts))
        return _parse_result(result)

    @netinfo_altering
    def setupNetworks(self, networks, bonds, options):
        stack = inspect.stack()
        options['_caller'] = stack[2][3]  # add calling method for logs
        result = self.vdscli.setupNetworks(networks, bonds, options)
        return _parse_result(result)

    def _vlanInRunningConfig(self, devName, vlanId):
        for net, attrs in self.config.networks.iteritems():
            if (vlanId == attrs.get('vlan') and
                    (attrs.get('bonding') == devName or
                     attrs.get('nic') == devName)):
                return True
        return False

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

    def getVdsStats(self):
        result = self.vdscli.getVdsStats()
        return _parse_result(result, 'info')

    def getAllVmStats(self):
        result = self.vdscli.getAllVmStats()
        return _parse_result(result, 'statsList')

    def getRoute(self, ip):
        result = self.vdscli.getRoute(ip)
        return _parse_result(result, 'info')

    def getVmStats(self, vmId):
        result = self.vdscli.getVmStats(vmId)
        if 'statsList' in result:
            code, msg, stats = _parse_result(result, 'statsList')
            return code, msg, stats[0]
        else:
            return _parse_result(result)

    def getVmList(self, vmId):
        result = self.vdscli.list('true', [vmId])
        code, msg, vm_list = _parse_result(result, 'vmList')
        return code, msg, vm_list[0]

    def getVdsCapabilities(self):
        result = self.vdscli.getVdsCapabilities()
        return _parse_result(result, 'info')

    def updateVmPolicy(self, vmId, vcpuLimit):
        result = self.vdscli.updateVmPolicy([vmId, vcpuLimit])
        return _parse_result(result)


def _parse_result(result, return_value=None):
    status = result['status']
    code = status['code']
    msg = status['message']

    if code == SUCCESS and return_value is not None:
        return code, msg, result[return_value]
    else:
        return code, msg
