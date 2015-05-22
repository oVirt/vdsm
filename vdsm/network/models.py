# Copyright 2011-2014 Red Hat, Inc.
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

from vdsm import netinfo

from .errors import ConfigNetworkError
from . import errors as ne


class NetDevice(object):
    def __init__(self, name, configurator, ipv4=None, ipv6=None,
                 blockingdhcp=False, mtu=None):
        self.name = name
        self.ipv4 = ipv4 if ipv4 is not None else IPv4()
        self.ipv6 = ipv6 if ipv6 is not None else IPv6()
        self.blockingdhcp = blockingdhcp
        self.mtu = mtu
        self.configurator = configurator
        self.master = None

    def __iter__(self):
        raise NotImplementedError

    def __str__(self):
        return self.name

    def configure(self, **opts):
        raise NotImplementedError

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

    @property
    def serving_default_route(self):
        device = self
        while device:
            if device.ipv4.defaultRoute:
                return True
            device = device.master
        return False

    @property
    def backing_device(self):
        return False

    @property
    def asynchronous_dhcp(self):
        return ((self.ipv4.bootproto == 'dhcp' or self.ipv6.dhcpv6) and
                not self.blockingdhcp)


class Nic(NetDevice):
    def __init__(self, name, configurator, ipv4=None, ipv6=None,
                 blockingdhcp=False, mtu=None, _netinfo=None):
        if _netinfo is None:
            _netinfo = netinfo.NetInfo()
        if name not in _netinfo.nics:
            raise ConfigNetworkError(ne.ERR_BAD_NIC, 'unknown nic: %s' % name)

        if _netinfo.ifaceUsers(name):
            mtu = max(mtu, netinfo.getMtu(name))

        super(Nic, self).__init__(name, configurator, ipv4, ipv6, blockingdhcp,
                                  mtu)

    def configure(self, **opts):
        # in a limited condition, we should not touch the nic config
        if (self.vlan and
                netinfo.operstate(self.name) == netinfo.OPERSTATE_UP and
                netinfo.ifaceUsed(self.name) and
                self.mtu <= netinfo.getMtu(self.name)):
            return

        self.configurator.configureNic(self, **opts)

    def remove(self):
        self.configurator.removeNic(self)

    @property
    def backing_device(self):
        return True

    def __iter__(self):
        yield self
        raise StopIteration

    def __repr__(self):
        return 'Nic(%s)' % self.name


class Vlan(NetDevice):
    MAX_ID = 4094

    def __init__(self, device, tag, configurator, ipv4=None, ipv6=None,
                 blockingdhcp=False, mtu=None):
        self.validateTag(tag)
        if device is None:
            raise ConfigNetworkError(ne.ERR_BAD_PARAMS, 'Missing required vlan'
                                     ' underlying device definition.')
        device.master = self
        self.device = device
        self.tag = tag
        super(Vlan, self).__init__('%s.%s' % (device.name, tag), configurator,
                                   ipv4, ipv6, blockingdhcp, mtu)

    def __iter__(self):
        yield self
        for dev in self.device:
            yield dev

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

    def __init__(self, name, configurator, ipv4=None, ipv6=None,
                 blockingdhcp=False, mtu=None, port=None, stp=None):
        self.validateName(name)
        if port:
            port.master = self
        self.port = port
        self.stp = stp
        super(Bridge, self).__init__(name, configurator, ipv4, ipv6,
                                     blockingdhcp, mtu)

    def __iter__(self):
        yield self
        if self.port is None:
            raise StopIteration
        for dev in self.port:
            yield dev

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
    def __init__(self, name, configurator, ipv4=None, ipv6=None,
                 blockingdhcp=False, mtu=None, slaves=(), options=None,
                 destroyOnMasterRemoval=None):
        self.validateName(name)
        for slave in slaves:
            slave.master = self
        self.slaves = slaves
        if options is None:
            self.options = 'mode=802.3ad miimon=150'
        else:
            self.validateOptions(name, options)
            self.options = self._reorderOptions(options)
        self.destroyOnMasterRemoval = destroyOnMasterRemoval
        super(Bond, self).__init__(name, configurator, ipv4, ipv6,
                                   blockingdhcp, mtu)

    def __iter__(self):
        yield self
        for slave in self.slaves:
            for dev in slave:
                yield dev
        raise StopIteration

    def __repr__(self):
        return 'Bond(%s: %r)' % (self.name, self.slaves)

    def configure(self, **opts):
        # When the bond is up and we are not changing the configuration that
        # is already applied in any way, we can skip the configuring.
        if (self.vlan and
            self.name in netinfo.bondings() and
            (not self.configurator.unifiedPersistence or
             self.name in self.configurator.runningConfig.bonds) and
            netinfo.operstate(self.name) == netinfo.OPERSTATE_UP and
            netinfo.ifaceUsed(self.name) and
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
        if nics:  # New bonding or edit bonding.
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
            raise ConfigNetworkError(ne.ERR_BAD_PARAMS,
                                     'Missing required nics on a bonding %s '
                                     'that is unknown to Vdsm ' % name)

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
            with open(netinfo.BONDING_MASTERS, 'r') as info:
                bonding = info.read().split()[0]
        except IndexError:
            with open(netinfo.BONDING_MASTERS, 'w') as info:
                info.write('+%s\n' % bonding)
            bond_created = True
        try:
            yield bonding
        finally:
            if bond_created:
                with open(netinfo.BONDING_MASTERS, 'w') as info:
                    info.write('-%s\n' % bonding)

    @staticmethod
    def _reorderOptions(options):
        """Order the mode first and the rest of options alphabetically."""
        if not options.strip():
            return ''

        opts = dict((option.split('=', 1) for option in options.split()))

        mode = opts.pop('mode', None)
        opts = sorted(opts.iteritems())
        if mode:
            opts.insert(0, ('mode', mode))

        return ' '.join((opt + '=' + val for (opt, val) in opts))

    @property
    def backing_device(self):
        return True


class IPv4(object):
    def __init__(self, address=None, netmask=None, gateway=None,
                 defaultRoute=None, bootproto=None):
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
        if address and bootproto == 'dhcp':
            raise ConfigNetworkError(
                ne.ERR_BAD_ADDR, 'Mixing of static and dynamic IPv4 '
                'configuration is currently not supported.')
        self.address = address
        self.netmask = netmask
        self.gateway = gateway
        self.defaultRoute = defaultRoute
        self.bootproto = bootproto

    def __nonzero__(self):
        return bool(self.address or self.bootproto)

    def __repr__(self):
        return 'IPv4(%s, %s, %s, %s, %s)' % (self.address, self.netmask,
                                             self.gateway, self.defaultRoute,
                                             self.bootproto)

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
    def __init__(self, address=None, gateway=None, defaultRoute=None,
                 ipv6autoconf=None, dhcpv6=None):
        if address:
            self.validateAddress(address)
            if gateway:
                self.validateGateway(gateway)
        elif gateway:
            raise ConfigNetworkError(ne.ERR_BAD_ADDR, 'Specified gateway but '
                                     'not ip address.')
        if address and (ipv6autoconf or dhcpv6):
            raise ConfigNetworkError(
                ne.ERR_BAD_ADDR, 'Mixing of static and dynamic IPv6 '
                'configuration is currently not supported.')
        self.address = address
        self.gateway = gateway
        self.defaultRoute = defaultRoute
        self.ipv6autoconf = ipv6autoconf
        self.dhcpv6 = dhcpv6

    def __nonzero__(self):
        return bool(self.address or self.ipv6autoconf or self.dhcpv6)

    def __repr__(self):
        return 'IPv6(%s, %s, %s, %s, %s)' % (
            self.address, self.gateway, self.defaultRoute, self.ipv6autoconf,
            self.dhcpv6)

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


def hierarchy_vlan_tag(device):
    """Returns the vlan tag of the network hierarchy if any"""
    vlan_tag = None
    for dev in device:
        vlan_tag = getattr(dev, 'tag', None)
        if vlan_tag is not None:
            break
    return vlan_tag


def hierarchy_backing_device(device):
    """Returns the backing device of a network hierarchy, i.e., a bond if
    the network is bonded, a nic otherwise (an no nic-less net)"""
    for dev in device:
        if dev.backing_device:
            return dev
    return None
