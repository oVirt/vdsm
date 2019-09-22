# Copyright 2011-2017 Red Hat, Inc.
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
import re

from vdsm.network.link import bond as link_bond
from vdsm.network.link import iface as link_iface
from vdsm.network.link.bond import sysfs_options as bond_options
from vdsm.network.netinfo import bonding, nics
from vdsm.network.netinfo.cache import CachingNetInfo
from vdsm.network.ip.address import IPv4, IPv6

from .errors import ConfigNetworkError
from . import errors as ne


class NetDevice(object):
    def __init__(
        self,
        name,
        configurator,
        ipv4=None,
        ipv6=None,
        blockingdhcp=False,
        mtu=None,
    ):
        self.name = name
        self.ipv4 = ipv4 if ipv4 is not None else IPv4()
        self.ipv6 = ipv6 if ipv6 is not None else IPv6()
        self.blockingdhcp = blockingdhcp
        self.mtu = mtu
        self.configurator = configurator
        self.master = None
        self.duid_source = None
        self.nameservers = None

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
    def backing_device(self):
        return False


class Nic(NetDevice):
    def __init__(
        self,
        name,
        configurator,
        ipv4=None,
        ipv6=None,
        blockingdhcp=False,
        mtu=None,
        _netinfo=None,
    ):
        if _netinfo is None:
            _netinfo = CachingNetInfo()
        if name not in _netinfo.nics:
            raise ConfigNetworkError(ne.ERR_BAD_NIC, 'unknown nic: %s' % name)

        if _netinfo.ifaceUsers(name):
            mtu = max(mtu or 0, link_iface.iface(name).mtu())

        super(Nic, self).__init__(
            name, configurator, ipv4, ipv6, blockingdhcp, mtu
        )

    def configure(self, **opts):
        # in a limited condition, we should not touch the nic config
        if (
            self.vlan
            and nics.operstate(self.name) == nics.OPERSTATE_UP
            and self.configurator.net_info.ifaceUsers(self.name)
            and self.mtu <= link_iface.iface(self.name).mtu()
        ):
            return

        self.configurator.configureNic(self, **opts)

    def remove(self, remove_even_if_used=False):
        self.configurator.removeNic(self, remove_even_if_used)

    @property
    def backing_device(self):
        return True

    def __iter__(self):
        yield self

    def __repr__(self):
        return 'Nic(%s)' % self.name


class Vlan(NetDevice):
    MAX_ID = 4094

    def __init__(
        self,
        device,
        tag,
        configurator,
        ipv4=None,
        ipv6=None,
        blockingdhcp=False,
        mtu=None,
        name=None,
    ):
        self.validateTag(tag)
        device.master = self
        self.device = device
        self.tag = tag
        # control for arbitrary vlan names
        name = '%s.%s' % (device.name, tag) if name is None else name
        super(Vlan, self).__init__(
            name, configurator, ipv4, ipv6, blockingdhcp, mtu
        )

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
                    ne.ERR_BAD_VLAN,
                    'vlan id out of range: %r, must be '
                    '0..%s' % (tag, cls.MAX_ID),
                )
        except ValueError:
            raise ConfigNetworkError(
                ne.ERR_BAD_VLAN,
                'vlan id must be a ' 'number between 0 and %s' % cls.MAX_ID,
            )


class Bridge(NetDevice):
    '''This class represents traditional kernel bridges.'''

    def __init__(
        self,
        name,
        configurator,
        ipv4=None,
        ipv6=None,
        blockingdhcp=False,
        mtu=None,
        port=None,
        stp=None,
    ):
        if port:
            port.master = self
        self.port = port
        self.stp = stp
        super(Bridge, self).__init__(
            name, configurator, ipv4, ipv6, blockingdhcp, mtu
        )

    def __iter__(self):
        yield self
        if self.port is None:
            return
        for dev in self.port:
            yield dev

    def __repr__(self):
        return 'Bridge(%s: %r)' % (self.name, self.port)

    def configure(self, **opts):
        self.configurator.configureBridge(self, **opts)

    def remove(self):
        logging.debug('Removing bridge %r', self)
        self.configurator.removeBridge(self)


class Bond(NetDevice):
    def __init__(
        self,
        name,
        configurator,
        ipv4=None,
        ipv6=None,
        blockingdhcp=False,
        mtu=None,
        slaves=(),
        options=None,
        hwaddr=None,
        on_removal_just_detach_from_network=False,
    ):
        for slave in slaves:
            slave.master = self
        self.slaves = slaves
        options = options or ''
        self.validateOptions(options)
        self.options = self._reorderOptions(options)
        self.hwaddr = hwaddr
        self.on_removal_just_detach_from_network = (
            on_removal_just_detach_from_network
        )
        super(Bond, self).__init__(
            name, configurator, ipv4, ipv6, blockingdhcp, mtu
        )

    def __iter__(self):
        yield self
        for slave in self.slaves:
            for dev in slave:
                yield dev

    def __repr__(self):
        return 'Bond(%s: %r)' % (self.name, self.slaves)

    def configure(self, **opts):
        # When the bond is up and we are not changing the configuration that
        # is already applied in any way, we can skip the configuring.
        if (
            self.vlan
            and self.name in bonding.bondings()
            and (
                not self.configurator.unifiedPersistence
                or self.name in self.configurator.runningConfig.bonds
            )
            and nics.operstate(self.name) == nics.OPERSTATE_UP
            and self.configurator.net_info.ifaceUsers(self.name)
            and self.mtu <= link_iface.iface(self.name).mtu()
            and not self._bond_hwaddr_changed()
            and self.areOptionsApplied()
            and frozenset(slave.name for slave in self.slaves)
            == frozenset(link_bond.Bond(self.name).slaves)
        ):
            return

        self.configurator.configureBond(self, **opts)

    def _bond_hwaddr_changed(self):
        return (
            self.hwaddr
            and self.hwaddr != link_iface.iface(self.name).address()
        )

    def areOptionsApplied(self):
        # TODO: this method returns True iff self.options are a subset of the
        # TODO: current bonding options. VDSM should probably compute if the
        # TODO: non-default settings are equal to the non-default state.
        options = self.options
        if options == '':
            return True
        confOpts = [option.split('=', 1) for option in options.split(' ')]
        activeOpts = bond_options.properties(
            self.name, filter_properties=(name for name, _ in confOpts)
        )

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
                    ne.ERR_USED_NIC,
                    'nic %s already used by %s'
                    % (nic, nicVlans or nicNet or nicBond),
                )
            slaves.append(Nic(nic, configurator, mtu=mtu, _netinfo=_netinfo))
        return slaves

    @classmethod
    def objectivize(
        cls,
        name,
        configurator,
        options,
        nics,
        mtu,
        _netinfo,
        hwaddr,
        on_removal_just_detach_from_network=False,
    ):

        if nics:  # New bonding or edit bonding.
            slaves = cls._objectivizeSlaves(
                name, configurator, _nicSort(nics), mtu, _netinfo
            )
            if name in _netinfo.bondings:
                if _netinfo.ifaceUsers(name):
                    mtu = max(mtu or 0, link_iface.iface(name).mtu())

                if not options:
                    options = _netinfo.bondings[name].get('opts')
                    options = Bond._dict2list(options)
        elif name in _netinfo.bondings:  # Implicit bonding.
            if _netinfo.ifaceUsers(name):
                mtu = max(mtu or 0, link_iface.iface(name).mtu())

            slaves = [
                Nic(nic, configurator, mtu=mtu, _netinfo=_netinfo)
                for nic in _netinfo.getNicsForBonding(name)
            ]
            options = _netinfo.bondings[name].get('opts')
            options = Bond._dict2list(options)
        else:
            raise ConfigNetworkError(
                ne.ERR_BAD_PARAMS,
                'Missing required nics on a bonding %s '
                'that is unknown to Vdsm ' % name,
            )

        detach = on_removal_just_detach_from_network  # argument is too long
        return cls(
            name,
            configurator,
            slaves=slaves,
            options=options,
            mtu=mtu,
            hwaddr=hwaddr,
            on_removal_just_detach_from_network=detach,
        )

    @classmethod
    def validateOptions(cls, bondingOptions):
        'Example: BONDING_OPTS="mode=802.3ad miimon=150"'
        mode = 'balance-rr'
        try:
            for option in bondingOptions.split():
                key, value = option.split('=', 1)
                if key == 'mode':
                    mode = value
        except ValueError:
            raise ConfigNetworkError(
                ne.ERR_BAD_BONDING,
                'Error parsing ' 'bonding options: %r' % bondingOptions,
            )

        mode = bonding.numerize_bond_mode(mode)
        defaults = bonding.getDefaultBondingOptions(mode)

        for option in bondingOptions.split():
            key, _ = option.split('=', 1)
            if key not in defaults:
                raise ConfigNetworkError(
                    ne.ERR_BAD_BONDING,
                    '%r is not a ' 'valid bonding option' % key,
                )

    @staticmethod
    def _reorderOptions(options):
        """Order the mode first and the rest of options alphabetically."""
        if not options.strip():
            return ''

        opts = dict((option.split('=', 1) for option in options.split()))

        mode = opts.pop('mode', None)
        opts = sorted(six.iteritems(opts))
        if mode:
            opts.insert(0, ('mode', mode))

        return ' '.join((opt + '=' + val for (opt, val) in opts))

    @property
    def backing_device(self):
        return True

    @staticmethod
    def _dict2list(options):
        options = options or {}
        return ' '.join((opt + '=' + val for opt, val in options.items()))


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
