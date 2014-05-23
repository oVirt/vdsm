# Copyright 2011-2013 Red Hat, Inc.
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
from collections import namedtuple
from contextlib import contextmanager
import logging
import os
import re
import socket
import struct

from neterrors import ConfigNetworkError
from vdsm import netinfo
import neterrors as ne


class NetDevice(object):
    def __init__(self, name, configurator, ipconfig=None, mtu=None):
        self.name = name
        self.ip = ipconfig
        self.mtu = mtu
        self.configurator = configurator
        self.master = None

    def __str__(self):
        return self.name

    def configure(self, **opts):
        raise NotImplementedError

    @property
    def ipConfig(self):
        try:
            config = self.ip.getConfig()
        except AttributeError:
            config = IpConfig.ipConfig(*(len(IpConfig.ipConfig._fields) *
                                       [None]))
        return config

    @property
    def bridge(self):
        if isinstance(self.master, Bridge):
            return self.master
        return None

    @property
    def bond(self):
        if isinstance(self.master, Bond):
            return self.master
        return None

    @property
    def vlan(self):
        if isinstance(self.master, Vlan):
            return self.master
        return None


class Nic(NetDevice):
    def __init__(self, name, configurator, ipconfig=None, mtu=None,
                 _netinfo=None):
        if _netinfo is None:
            _netinfo = netinfo.NetInfo()
        if name not in _netinfo.nics:
            raise ConfigNetworkError(ne.ERR_BAD_NIC, 'unknown nic: %s' % name)

        if _netinfo.ifaceUsers(name):
            mtu = max(mtu, netinfo.getMtu(name))

        super(Nic, self).__init__(name, configurator, ipconfig,
                                  mtu=mtu)

    def configure(self, **opts):
        # in a limited condition, we should not touch the nic config
        if (self.vlan and
                netinfo.operstate(self.name) == netinfo.OPERSTATE_UP and
                netinfo.NetInfo().ifaceUsers(self.name) and
                self.mtu <= netinfo.getMtu(self.name)):
            return

        self.configurator.configureNic(self, **opts)

    def remove(self):
        self.configurator.removeNic(self)

    def __repr__(self):
        return 'Nic(%s)' % self.name


class Vlan(NetDevice):
    MAX_ID = 4094

    def __init__(self, device, tag, configurator, ipconfig=None, mtu=None):
        self.validateTag(tag)
        if device is None:
            raise ConfigNetworkError(ne.ERR_BAD_PARAMS, 'Missing required vlan'
                                     ' underlying device definition.')
        device.master = self
        self.device = device
        self.tag = tag
        super(Vlan, self).__init__('%s.%s' % (device.name, tag), configurator,
                                   ipconfig, mtu)

    def __repr__(self):
        return 'Vlan(%s: %r)' % (self.name, self.device)

    def configure(self, **opts):
        self.configurator.configureVlan(self, **opts)

    def remove(self):
        self.configurator.removeVlan(self)

    @classmethod
    def validateTag(cls, tag):
        try:
            if not 0 <= int(tag) <= cls.MAX_ID:
                raise ConfigNetworkError(
                    ne.ERR_BAD_VLAN, 'vlan id out of range: %r, must be '
                    '0..%s' % (tag, cls.MAX_ID))
        except ValueError:
            raise ConfigNetworkError(ne.ERR_BAD_VLAN, 'vlan id must be a '
                                     'number between 0 and %s' %
                                     cls.MAX_ID)


class Bridge(NetDevice):
    '''This class represents traditional kernel bridges.'''
    MAX_NAME_LEN = 15
    ILLEGAL_CHARS = frozenset(':. \t')

    def __init__(self, name, configurator, ipconfig=None, mtu=None, port=None,
                 stp=None):
        self.validateName(name)
        if port:
            port.master = self
        self.port = port
        self.stp = stp
        super(Bridge, self).__init__(name, configurator, ipconfig, mtu)

    def __repr__(self):
        return 'Bridge(%s: %r)' % (self.name, self.port)

    def configure(self, **opts):
        self.configurator.configureBridge(self, **opts)

    def remove(self):
        logging.debug('Removing bridge %r', self)
        self.configurator.removeBridge(self)

    @classmethod
    def validateName(cls, name):
        if not (name and 0 < len(name) <= cls.MAX_NAME_LEN and
                len(set(name) & cls.ILLEGAL_CHARS) == 0 and
                not name.startswith('-')):
            raise ConfigNetworkError(ne.ERR_BAD_BRIDGE,
                                     "Bridge name isn't valid: %r" % name)


class Bond(NetDevice):
    def __init__(self, name, configurator, ipconfig=None, mtu=None, slaves=(),
                 options=None, destroyOnMasterRemoval=None):
        self.validateName(name)
        for slave in slaves:
            slave.master = self
        self.slaves = slaves
        if options is None:
            self.options = 'mode=802.3ad miimon=150'
        else:
            self.validateOptions(name, options)
            self.options = options
        self.destroyOnMasterRemoval = destroyOnMasterRemoval
        super(Bond, self).__init__(name, configurator, ipconfig, mtu)

    def __repr__(self):
        return 'Bond(%s: %r)' % (self.name, self.slaves)

    def configure(self, **opts):
        # When the bond is up and we are not changing the configuration that
        # is already applied in any way, we can skip the configuring.
        if (self.vlan and
            self.name in netinfo.bondings() and
            netinfo.operstate(self.name) == netinfo.OPERSTATE_UP and
            netinfo.NetInfo().ifaceUsers(self.name) and
            self.mtu <= netinfo.getMtu(self.name) and
            self.areOptionsApplied() and
            frozenset(slave.name for slave in self.slaves) ==
                frozenset(netinfo.slaves(self.name))):
                return

        self.configurator.configureBond(self, **opts)

    def areOptionsApplied(self):
        confOpts = [option.split('=', 1) for option in self.options.split(' ')]
        activeOpts = netinfo.bondOpts(self.name,
                                      (name for name, value in confOpts))
        return all(value in activeOpts[name] for name, value in confOpts)

    def remove(self):
        logging.debug('Removing bond %r', self)
        self.configurator.removeBond(self)

    @classmethod
    def _objectivizeSlaves(cls, name, configurator, nics, mtu, _netinfo):
        slaves = []
        for nic in nics:
            nicVlans = tuple(_netinfo.getVlansForIface(nic))
            nicNet = _netinfo.getNetworkForIface(nic)
            nicBond = _netinfo.getBondingForNic(nic)
            if nicVlans or nicNet or nicBond and nicBond != name:
                raise ConfigNetworkError(
                    ne.ERR_USED_NIC, 'nic %s already used by %s' %
                    (nic, nicVlans or nicNet or nicBond))
            slaves.append(Nic(nic, configurator, mtu=mtu,
                              _netinfo=_netinfo))
        return slaves

    @classmethod
    def objectivize(cls, name, configurator, options, nics, mtu, _netinfo,
                    destroyOnMasterRemoval=None):
        if name and nics:  # New bonding or edit bonding.
            slaves = cls._objectivizeSlaves(name, configurator, _nicSort(nics),
                                            mtu, _netinfo)
            if name in _netinfo.bondings:
                if _netinfo.ifaceUsers(name):
                    mtu = max(mtu, netinfo.getMtu(name))

                if not options:
                    options = _netinfo.bondings[name]['cfg'].get(
                        'BONDING_OPTS')
        elif name in _netinfo.bondings:  # Implicit bonding.
            if _netinfo.ifaceUsers(name):
                mtu = max(mtu, netinfo.getMtu(name))

            slaves = [Nic(nic, configurator, mtu=mtu, _netinfo=_netinfo)
                      for nic in _netinfo.getNicsForBonding(name)]
            options = _netinfo.bondings[name]['cfg'].get('BONDING_OPTS')
        else:
            raise ConfigNetworkError(ne.ERR_BAD_BONDING,
                                     'Bonding %s not specified and it is not '
                                     'already on the system' % name)
        if not slaves:
            raise ConfigNetworkError(ne.ERR_BAD_PARAMS, 'Missing required nics'
                                     ' for bonding device.')

        return cls(name, configurator, slaves=slaves, options=options, mtu=mtu,
                   destroyOnMasterRemoval=destroyOnMasterRemoval)

    @staticmethod
    def validateName(name):
        if not re.match('^bond[0-9]+$', name):
            raise ConfigNetworkError(ne.ERR_BAD_BONDING,
                                     '%r is not a valid bonding device name' %
                                     name)

    @classmethod
    def validateOptions(cls, bonding, bondingOptions):
        'Example: BONDING_OPTS="mode=802.3ad miimon=150"'
        with cls._validationBond(bonding) as bond:
            try:
                for option in bondingOptions.split():
                    key, _ = option.split('=')
                    if not os.path.exists('/sys/class/net/%s/bonding/%s' %
                                          (bond, key)):
                        raise ConfigNetworkError(ne.ERR_BAD_BONDING, '%r is '
                                                 'not a valid bonding option' %
                                                 key)
            except ValueError:
                raise ConfigNetworkError(ne.ERR_BAD_BONDING, 'Error parsing '
                                         'bonding options: %r' %
                                         bondingOptions)

    @staticmethod
    @contextmanager
    def _validationBond(bonding):
        bond_created = False
        try:
            bonding = open(netinfo.BONDING_MASTERS, 'r').read().split()[0]
        except IndexError:
            open(netinfo.BONDING_MASTERS, 'w').write('+%s\n' % bonding)
            bond_created = True
        try:
            yield bonding
        finally:
            if bond_created:
                open(netinfo.BONDING_MASTERS, 'w').write('-%s\n' % bonding)


class IPv4(object):
    def __init__(self, address=None, netmask=None, gateway=None,
                 defaultRoute=None):
        if address:
            if not netmask:
                raise ConfigNetworkError(ne.ERR_BAD_ADDR, 'Must specify '
                                         'netmask to configure ip for '
                                         'network.')
            self.validateAddress(address)
            self.validateNetmask(netmask)
            if gateway:
                self.validateGateway(gateway)
        else:
            if netmask or gateway:
                raise ConfigNetworkError(ne.ERR_BAD_ADDR, 'Specified netmask '
                                         'or gateway but not ip address.')
        self.address = address
        self.netmask = netmask
        self.gateway = gateway
        self.defaultRoute = defaultRoute

    def __repr__(self):
        return 'IPv4(%s, %s, %s, %s)' % (self.address, self.netmask,
                                         self.gateway, self.defaultRoute)

    @classmethod
    def validateAddress(cls, address):
        try:
            socket.inet_pton(socket.AF_INET, address)
        except socket.error:
            raise ConfigNetworkError(ne.ERR_BAD_ADDR, '%r is not a valid IPv4 '
                                     'address.' % address)

    @classmethod
    def validateNetmask(cls, netmask):
        try:
            cls.validateAddress(netmask)
        except ConfigNetworkError as cne:
            cne.message = '%r is not a valid IPv4 netmask.' % netmask
            raise
        num = struct.unpack('>I', socket.inet_aton(netmask))[0]
        if num & (num - 1) != (num << 1) & 0xffffffff:
            raise ConfigNetworkError(ne.ERR_BAD_ADDR, '%r is not a valid IPv4 '
                                     'netmask.' % netmask)

    @classmethod
    def validateGateway(cls, gateway):
        '''Validates the gateway form.'''
        try:
            cls.validateAddress(gateway)
        except ConfigNetworkError as cne:
            cne.message = '%r is not a valid IPv4 gateway' % gateway
            raise


class IPv6(object):
    def __init__(self, address=None, gateway=None, defaultRoute=None):
        if address:
            self.validateAddress(address)
            if gateway:
                self.validateGateway(gateway)
        elif gateway:
            raise ConfigNetworkError(ne.ERR_BAD_ADDR, 'Specified prefixlen '
                                     'or gateway but not ip address.')
        self.address = address
        self.gateway = gateway
        self.defaultRoute = defaultRoute

    def __repr__(self):
        return 'IPv6(%s, %s, %s)' % (self.address, self.gateway,
                                     self.defaultRoute)

    @classmethod
    def validateAddress(cls, address):
        addr = address.split('/', 1)
        try:
            socket.inet_pton(socket.AF_INET6, addr[0])
        except socket.error:
            raise ConfigNetworkError(ne.ERR_BAD_ADDR, '%r is not a valid IPv6 '
                                     'address.' % address)
        if len(addr) == 2:
            cls.validatePrefixlen(addr[1])

    @classmethod
    def validatePrefixlen(cls, prefixlen):
        try:
            prefixlen = int(prefixlen)
            if prefixlen < 0 or prefixlen > 127:
                raise ConfigNetworkError(ne.ERR_BAD_ADDR, '%r is not valid '
                                         'IPv6 prefixlen.' % prefixlen)
        except ValueError:
            raise ConfigNetworkError(ne.ERR_BAD_ADDR, '%r is not valid '
                                     'IPv6 prefixlen.' % prefixlen)

    @classmethod
    def validateGateway(cls, gateway):
        try:
            cls.validateAddress(gateway)
        except ConfigNetworkError as cne:
            cne.message = '%r is not a valid IPv6 gateway.'
            raise


class IpConfig(object):
    ipConfig = namedtuple('ipConfig', ['ipaddr', 'netmask', 'gateway',
                                       'defaultRoute', 'ipv6addr',
                                       'ipv6gateway', 'ipv6defaultRoute',
                                       'bootproto', 'async', 'ipv6autoconf',
                                       'dhcpv6'])

    def __init__(self, inet4=None, inet6=None, bootproto=None, blocking=False,
                 ipv6autoconf=None, dhcpv6=None):
        if inet4 is None and inet6 is None:
            raise ConfigNetworkError(ne.ERR_BAD_ADDR, 'You need to specify '
                                     'IPv4 or IPv6 or both address.')
        if ((inet4 and inet4.address and bootproto == 'dhcp') or
           (inet6 and inet6.address and (ipv6autoconf or dhcpv6))):
            raise ConfigNetworkError(ne.ERR_BAD_ADDR, 'Static and dynamic ip '
                                     'configurations are mutually exclusive.')
        self.inet4 = inet4
        self.inet6 = inet6
        self.bootproto = bootproto
        self.async = bootproto == 'dhcp' and not blocking
        self.ipv6autoconf = ipv6autoconf
        self.dhcpv6 = dhcpv6

    def __repr__(self):
        return 'IpConfig(%r, %r, %s, %s, %s)' % (self.inet4, self.inet6,
                                                 self.bootproto,
                                                 self.ipv6autoconf,
                                                 self.dhcpv6)

    def getConfig(self):
        try:
            ipaddr = self.inet4.address
            netmask = self.inet4.netmask
            gateway = self.inet4.gateway
            defaultRoute = self.inet4.defaultRoute
        except AttributeError:
            ipaddr = netmask = gateway = defaultRoute = None
        try:
            ipv6addr = self.inet6.address
            ipv6gateway = self.inet6.gateway
            ipv6defaultRoute = self.inet6.defaultRoute
        except AttributeError:
            ipv6addr = ipv6gateway = ipv6defaultRoute = None
        return self.ipConfig(ipaddr, netmask, gateway, defaultRoute, ipv6addr,
                             ipv6gateway, ipv6defaultRoute, self.bootproto,
                             self.async, self.ipv6autoconf, self.dhcpv6)


def _nicSort(nics):
    """
    Return a list of nics/interfaces ordered by name. We need it to enslave nic
    to bonding in the same order that initscripts does it. Then it can
    obtain the same master mac address by iproute2 as ifcfg.
    """

    nicsList = []
    nicsRexp = re.compile("^(\D*)(\d*)(.*)$")

    for nicName in nics:
        nicSre = nicsRexp.match(nicName)
        prefix, stridx, postfix = nicSre.groups((nicName, "0", ""))

        try:
            intidx = int(stridx)
        except ValueError:
            intidx = 0

        nicsList.append((prefix, intidx, stridx + postfix))

    return [x + z for x, y, z in sorted(nicsList)]
