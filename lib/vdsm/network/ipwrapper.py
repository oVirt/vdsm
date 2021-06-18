# Copyright 2013-2021 Red Hat, Inc.
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

from fnmatch import fnmatch
from glob import iglob
from ipaddress import ip_address
from ipaddress import ip_network

import errno
import os

from vdsm.common.cmdutils import CommandPath
from vdsm.common.compat import subprocess
from vdsm.common.config import config
from vdsm.network import cmd
from vdsm.network import ethtool
from vdsm.network.netlink import libnl
from vdsm.network.netlink import link

_IP_BINARY = CommandPath('ip', '/sbin/ip')

DUMMY_BRIDGE = ';vdsmdummy;'
_ROUTE_FLAGS = frozenset(
    (
        # copied from iproute2's rtnl_rtntype_n2a()
        'unicast',
        'local',
        'broadcast',
        'anycast',
        'multicast',
        'blackhole',
        'unreachable',
        'prohibit',
        'throw',
        'nat',
        'xresolve',
        'Deleted',  # copied from iproute.c
    )
)


def _isValid(ip, verifier):
    try:
        verifier(ip)
    except ValueError:
        return False

    return True


def equals(cls):
    def __eq__(self, other):
        return type(other) == cls and self.__dict__ == other.__dict__

    cls.__eq__ = __eq__
    return cls


class LinkType(object):
    """Representation of the different link types"""

    NIC = 'nic'
    VLAN = 'vlan'
    BOND = 'bond'
    BRIDGE = 'bridge'
    LOOPBACK = 'loopback'
    MACVLAN = 'macvlan'
    DUMMY = 'dummy'
    TUN = 'tun'
    OVS = 'openvswitch'
    TEAM = 'team'
    VETH = 'veth'
    VF = 'vf'


@equals
class Link(object):
    """Represents link information obtained from iproute2"""

    _fakeNics = config.get('vars', 'fake_nics').split(',')
    _hiddenBonds = config.get('vars', 'hidden_bonds').split(',')
    _hiddenNics = config.get('vars', 'hidden_nics').split(',')
    _hiddenVlans = config.get('vars', 'hidden_vlans').split(',')

    def __init__(
        self,
        address,
        index,
        linkType,
        mtu,
        name,
        qdisc,
        state,
        vlanid=None,
        vlanprotocol=None,
        master=None,
        device=None,
        **kwargs
    ):
        self.address = address
        self.index = index
        self.type = linkType
        self.mtu = mtu
        self.name = name
        self.qdisc = qdisc
        self.state = state
        self.master = master
        if vlanid is not None:
            self.vlanid = vlanid
        if vlanprotocol is not None:
            self.vlanprotocol = vlanprotocol
        if device is not None:
            self.device = device
        for key, value in kwargs.items():
            setattr(self, key, value)

    def __repr__(self):
        return '%s: %s(%s) %s' % (
            self.index,
            self.name,
            self.type,
            self.address,
        )

    @classmethod
    def fromDict(cls, data):
        data['linkType'] = (
            data['type'] if 'type' in data else cls._detectType(data['name'])
        )
        return cls(**data)

    @staticmethod
    def _detectType(name):
        """Returns the LinkType for the specified device."""
        # TODO: Add support for virtual functions
        detectedType = None
        try:
            driver = ethtool.driver_name(name)
        except IOError as ioe:
            if ioe.errno == errno.EOPNOTSUPP:
                if name == 'lo':
                    detectedType = LinkType.LOOPBACK
                else:
                    detectedType = LinkType.DUMMY
                return detectedType
            else:
                raise  # Reraise other errors like ENODEV
        if driver in (
            LinkType.BRIDGE,
            LinkType.MACVLAN,
            LinkType.TUN,
            LinkType.OVS,
            LinkType.TEAM,
            LinkType.VETH,
        ):
            detectedType = driver
        elif driver == 'bonding':
            detectedType = LinkType.BOND
        elif 'VLAN' in driver or 'vlan' in driver:
            detectedType = LinkType.VLAN
        elif os.path.exists('/sys/class/net/%s/device/physfn/' % name):
            detectedType = LinkType.VF
        else:
            detectedType = LinkType.NIC
        return detectedType

    def isBOND(self):
        return self.type == LinkType.BOND

    def isBRIDGE(self):
        return self.type == LinkType.BRIDGE

    def isDUMMY(self):
        return self.type == LinkType.DUMMY

    def isNIC(self):
        return self.type == LinkType.NIC

    def isVETH(self):
        return self.type == LinkType.VETH

    def isVF(self):
        return self.type == LinkType.VF

    def isVLAN(self):
        return self.type == LinkType.VLAN

    def isMACVLAN(self):
        return self.type == LinkType.MACVLAN

    def isFakeNIC(self):
        """
        Returns True iff vdsm config marks the DUMMY or VETH dev to be reported
        as NIC.
        """
        if self.isDUMMY() or self.isVETH() or self.isMACVLAN():
            return _any_fnmatch(self.name, self._fakeNics)
        return False

    def isNICLike(self):
        return self.isNIC() or self.isVF() or self.isFakeNIC()

    def isHidden(self):
        """Returns True iff vdsm config hides the device."""
        if self.isVLAN():
            return _any_fnmatch(self.name, self._hiddenVlans)
        elif self.isNICLike():
            return (
                _any_fnmatch(self.name, self._hiddenNics)
                or (
                    self.master
                    and _bondExists(self.master)
                    and _any_fnmatch(self.master, self._hiddenBonds)
                )
                or (self.isVF() and self._isVFhidden())
            )
        elif self.isBOND():
            return _any_fnmatch(self.name, self._hiddenBonds)
        elif self.isBRIDGE():
            return self.name == DUMMY_BRIDGE
        return False

    def _isVFhidden(self):
        if self.address == '00:00:00:00:00:00':
            if self._is_vmfex():
                return True
        # We hide a VF if there exists a macvtap device with the same address.
        # We assume that such VFs are used by a VM and should not be reported
        # as host nics
        for path in iglob('/sys/class/net/*/address'):
            dev = os.path.basename(os.path.dirname(path))
            if (
                dev != self.name
                and _read_stripped(path) == self.address
                and self._detectType(dev) == LinkType.MACVLAN
            ):
                return True
        return False

    def _is_vmfex(self):
        return ethtool.driver_name(self.name) == 'enic'

    @property
    def oper_up(self):
        return bool(
            link.get_link(self.name)['flags'] & libnl.IfaceStatus.IFF_RUNNING
        )

    def get_promisc(self):
        return bool(
            link.get_link(self.name)['flags'] & libnl.IfaceStatus.IFF_PROMISC
        )

    def set_promisc(self, value):
        """Takes a boolean to enable/disable Link promiscuity"""
        promisc = 'on' if value else 'off'
        linkSet(self.name, ['promisc', promisc])

    promisc = property(get_promisc, set_promisc, None, 'Link promiscuity flag')


def _read_stripped(path):
    with open(path) as f:
        return f.read().strip()


def _bondExists(bondName):
    return os.path.exists('/sys/class/net/%s/bonding' % bondName)


def getLinks():
    """Return an iterator of Link objects, each per a link in the system."""
    for data in link.iter_links():
        try:
            yield Link.fromDict(data)
        except IOError:  # If a link goes missing we just don't report it
            continue


def getLink(dev):
    """Returns the Link object for the specified dev."""
    return Link.fromDict(link.get_link(dev))


@equals
class Route(object):
    def __init__(self, network, via=None, src=None, device=None, table=None):
        if network != 'local' and not _isValid(network, ip_network):
            raise ValueError('network %s is not properly defined' % network)

        if via and not _isValid(via, ip_address):
            raise ValueError('via %s is not a proper IP address' % via)

        if src and not _isValid(src, ip_address):
            raise ValueError('src %s is not a proper IP address' % src)

        self.network = network
        self.via = via
        self.src = src
        self.device = device
        self.table = table

    @classmethod
    def parse(cls, text):
        """
        Returns a dictionary populated with the route attributes found in the
        textual representation.
        """
        route = text.split()
        try:
            route = route[: route.index('\\')]
        except ValueError:
            pass

        flags = {}
        while route[0] in _ROUTE_FLAGS:
            flags[route[0]] = True
            route = route[1:]

        network = route[0]

        data = dict(route[i : i + 2] for i in range(1, len(route), 2))
        data['network'] = '0.0.0.0/0' if network == 'default' else network
        data.update(flags)
        return data

    @classmethod
    def fromText(cls, text):
        """
        Creates a Route object from a textual representation.

        Examples:
        'default via 192.168.99.254 dev eth0':
        '0.0.0.0/0 via 192.168.99.254 dev eth0 table foo':
        '200.100.50.0/16 via 11.11.11.11 dev eth2 table foo':
        'local 127.0.0.1 dev lo scope host src 127.0.0.1':
        """
        try:
            data = cls.parse(text)
        except Exception:
            raise ValueError('Route %s: Failed to parse route.' % text)

        via = data.get('via')
        src = data.get('src')
        try:
            device = data['dev']
        except KeyError:
            raise ValueError('Route %s: Routes require a device.' % text)
        table = data.get('table')

        return cls(
            data['network'], via=via, src=src, device=device, table=table
        )

    def __str__(self):
        output = str(self.network)
        if self.network == 'local':
            output += ' %s' % self.src
        if self.via:
            output += ' via %s' % self.via

        output += ' dev %s' % self.device

        if self.src:
            output += ' src %s' % self.src

        if self.table:
            output += ' table %s' % self.table

        return output

    def __iter__(self):
        for word in str(self).split():
            yield word


@equals
class Rule(object):
    def __init__(
        self,
        table,
        source=None,
        destination=None,
        srcDevice=None,
        detached=False,
        prio=None,
    ):
        if source:
            if not (
                _isValid(source, ip_address) or _isValid(source, ip_network)
            ):
                raise ValueError(
                    'Source %s invalid: Not an ip address '
                    'or network.' % source
                )

        if destination:
            if not (
                _isValid(destination, ip_address)
                or _isValid(destination, ip_network)
            ):
                raise ValueError(
                    'Destination %s invalid: Not an ip address '
                    'or network.' % destination
                )

        self.table = table
        self.source = source
        self.destination = destination
        self.srcDevice = srcDevice
        self.detached = detached
        self.prio = prio

    @classmethod
    def parse(cls, text):
        isDetached = '[detached]' in text
        rule = [entry for entry in text.split() if entry != '[detached]']
        prio = int(rule[0][:-1])
        parameters = rule[1:]

        if len(rule) % 2 == 0:
            raise ValueError(
                'Rule %s: The length of a textual representation '
                'of a rule must be odd. ' % text
            )

        values = dict(
            parameters[i : i + 2] for i in range(0, len(parameters), 2)
        )
        values['detached'] = isDetached
        values['prio'] = prio
        return values

    @classmethod
    def fromText(cls, text):
        """
        Creates a Rule object from a textual representation. Since it is
        used for source routing, the source network specified in "from" and
        the table "lookup" that shall be used for the routing must be
        specified.

        Examples:
        32766:    from all lookup main
        32767:    from 10.0.0.0/8 to 20.0.0.0/8 lookup table_100
        32768:    from all to 8.8.8.8 lookup table_200
        """
        data = cls.parse(text)
        try:
            table = data['lookup']
        except KeyError:
            raise ValueError(
                'Rule %s: Rules require "lookup" information. ' % text
            )
        try:
            source = data['from']
        except KeyError:
            raise ValueError(
                'Rule %s: Rules require "from" information. ' % text
            )

        destination = data.get('to')
        if source == 'all':
            source = None
        if destination == 'all':
            destination = None
        srcDevice = data.get('dev') or data.get('iif')
        detached = data['detached']
        prio = data['prio']

        return cls(
            table,
            source=source,
            destination=destination,
            srcDevice=srcDevice,
            detached=detached,
            prio=prio,
        )

    def __str__(self):
        output = 'from '
        if self.source:
            output += self.source
        else:
            output += 'all'
        if self.destination:
            output += ' to %s' % self.destination
        if self.srcDevice:
            output += ' dev %s' % self.srcDevice
        if self.prio:
            output += ' prio %s' % self.prio
        if self.table:
            output += ' table %s' % self.table

        return output

    def __iter__(self):
        for word in str(self).split():
            yield word


class IPRoute2Error(Exception):
    pass


class IPRoute2NoDeviceError(IPRoute2Error):
    pass


class IPRoute2AlreadyExistsError(IPRoute2Error):
    pass


def _exec_cmd(command):
    returnCode, output, error = cmd.exec_sync(command)

    if returnCode:
        if 'Cannot find device' in error:
            exc = IPRoute2NoDeviceError
        elif 'File exists' in error:
            exc = IPRoute2AlreadyExistsError
        else:
            exc = IPRoute2Error
        raise exc(returnCode, error.splitlines())
    return output.splitlines()


def routeShowGateways(table):
    command = [
        _IP_BINARY.cmd,
        'route',
        'show',
        'to',
        '0.0.0.0/0',
        'table',
        table,
    ]
    return _exec_cmd(command)


def route6_show_gateways(table):
    command = [
        _IP_BINARY.cmd,
        '-6',
        'route',
        'show',
        'to',
        '::/0',
        'table',
        table,
    ]
    return _exec_cmd(command)


def routeShowTable(table):
    command = [_IP_BINARY.cmd, '-oneline', 'route', 'show', 'table', table]
    return _exec_cmd(command)


def routeAdd(route, family=4, dev=None):
    command = [_IP_BINARY.cmd, '-%s' % family, 'route', 'add']
    command += route
    if dev is not None:
        command += ['dev', dev]
    _exec_cmd(command)


def routeDel(route, family):
    command = [_IP_BINARY.cmd, '-%s' % family, 'route', 'del']
    command += route
    _exec_cmd(command)


def routeGet(ipAddress):
    command = [_IP_BINARY.cmd, 'route', 'get']
    command += ipAddress
    return _exec_cmd(command)


def ruleList():
    command = [_IP_BINARY.cmd, 'rule']
    return _exec_cmd(command)


def ruleAdd(rule):
    command = [_IP_BINARY.cmd, 'rule', 'add']
    command += rule
    _exec_cmd(command)


def ruleDel(rule):
    command = [_IP_BINARY.cmd, 'rule', 'del']
    command += rule
    _exec_cmd(command)


def addrAdd(dev, ipaddr, netmask, family=4):
    command = [
        _IP_BINARY.cmd,
        '-%s' % family,
        'addr',
        'add',
        'dev',
        dev,
        '%s/%s' % (ipaddr, netmask),
    ]
    _exec_cmd(command)


def addrDel(dev, ipaddr, netmask, family):
    command = [
        _IP_BINARY.cmd,
        '-%s' % family,
        'addr',
        'del',
        'dev',
        dev,
        '%s/%s' % (ipaddr, netmask),
    ]
    _exec_cmd(command)


def addrFlush(dev, family='both'):
    """
    Remove globally-routable IP addresses from the interface, optionally by
    protocol family.

    Link-local address must be kept not to harm DHCPv6 functionality.
    """
    family_param = ['-%s' % family] if family in (4, 6) else []
    command = (
        [_IP_BINARY.cmd]
        + family_param
        + ['addr', 'flush', 'dev', dev, 'scope', 'global']
    )
    _exec_cmd(command)


def linkAdd(name, linkType, link=None, args=()):
    command = [_IP_BINARY.cmd, 'link', 'add']
    if link:
        command += ['link', link]

    command += ['name', name, 'type', linkType]

    command.extend(args)

    _exec_cmd(command)


def linkSet(dev, linkArgs):
    command = [_IP_BINARY.cmd, 'link', 'set', 'dev', dev]
    command += linkArgs
    _exec_cmd(command)


def linkDel(dev):
    command = [_IP_BINARY.cmd, 'link', 'del', 'dev', dev]
    _exec_cmd(command)


def netns_add(name):
    _exec_cmd([_IP_BINARY.cmd, 'netns', 'add', name])


def netns_delete(name):
    _exec_cmd([_IP_BINARY.cmd, 'netns', 'delete', name])


def netns_exec(netns_name, command):
    cmds = [_IP_BINARY.cmd, 'netns', 'exec', netns_name] + command
    return subprocess.Popen(cmds)


def link_set_netns(device, netns_name):
    return linkSet(device, ['netns', netns_name])


def _any_fnmatch(name, patterns):
    """Returns True if any element in the patterns iterable fnmatches name."""
    return any(fnmatch(name, pattern) for pattern in patterns)
