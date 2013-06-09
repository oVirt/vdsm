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

    def getIpConfig(self):
        try:
            ipaddr, netmask, gateway, bootproto, async = self.ip.getConfig()
        except AttributeError:
            ipaddr = netmask = gateway = bootproto = async = None
        return ipaddr, netmask, gateway, bootproto, async


class Nic(NetDevice):
    def __init__(self, name, configurator, ipconfig=None, mtu=None,
                 _netinfo=None):
        if _netinfo is None:
            _netinfo = netinfo.NetInfo()
        if name not in _netinfo.nics:
            raise ConfigNetworkError(ne.ERR_BAD_NIC, 'unknown nic: %s' % name)
        super(Nic, self).__init__(name, configurator, ipconfig,
                                  mtu=max(mtu, netinfo.getMtu(name)))

    def configure(self, bridge=None, bonding=None, **opts):
        self.configurator.configureNic(self, bridge=bridge, bonding=bonding,
                                       **opts)

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
        super(Vlan, self).__init__(device.name + '.' + tag, configurator,
                                   ipconfig, mtu)

    def __repr__(self):
        return 'Vlan(%s: %r)' % (self.name, self.device)

    def configure(self, bridge=None, **opts):
        self.configurator.configureVlan(self, bridge=bridge, **opts)

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
                 forwardDelay=0, stp=None):
        self.validateName(name)
        if port:
            port.master = self
        self.port = port
        self.forwardDelay = forwardDelay
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
        if not (name and len(name) <= cls.MAX_NAME_LEN and
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
        self.configurator.configureBond(self, **opts)

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
            slaves = cls._objectivizeSlaves(name, configurator, nics, mtu,
                                            _netinfo)
            if name in _netinfo.bondings:
                mtu = max(netinfo.getMtu(name), mtu)
                if not options:
                    options = _netinfo.bondings[name]['cfg'].get(
                        'BONDING_OPTS')
        elif name in _netinfo.bondings:  # Implicit bonding.
            mtu = max(netinfo.getMtu(name), mtu)
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
    def __init__(self, address=None, netmask=None, gateway=None):
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

    def __repr__(self):
        return 'IPv4(%s, %s, %s)' % (self.address, self.netmask, self.gateway)

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


class IpConfig(object):
    def __init__(self, inet, bootproto=None, blocking=False):
        if inet.address and bootproto == 'dhcp':
            raise ConfigNetworkError(ne.ERR_BAD_ADDR, 'Static and dynamic ip '
                                     'configurations are mutually exclusive.')
        self.inet = inet
        self.bootproto = bootproto
        self.async = bootproto == 'dhcp' and blocking

    def __repr__(self):
        return 'IpConfig(%r, %s)' % (self.inet, self.bootproto)

    def getConfig(self):
        try:
            ipaddr = self.inet.address
            netmask = self.inet.netmask
            gateway = self.inet.gateway
        except AttributeError:
            ipaddr = netmask = gateway = None
        return ipaddr, netmask, gateway, self.bootproto, self.async
