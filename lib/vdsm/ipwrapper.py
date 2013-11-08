# Copyright 2013 Red Hat, Inc.
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
import os

from netaddr.core import AddrFormatError
from netaddr import IPAddress
from netaddr import IPNetwork

from .config import config
from .utils import anyFnmatch
from .utils import CommandPath
from .utils import execCmd
from .utils import memoized
from .utils import pairwise

_IP_BINARY = CommandPath('ip', '/sbin/ip')
_ETHTOOL_BINARY = CommandPath('ethtool',
                              '/usr/sbin/ethtool',  # F19+
                              '/sbin/ethtool',  # EL6, ubuntu and Debian
                              '/usr/bin/ethtool',  # Arch
                              )


def _isValid(ip, verifier):
    try:
        verifier(ip)
    except (AddrFormatError, ValueError):
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


@equals
class Link(object):
    """Represents link information obtained from iproute2"""
    def __init__(self, address, index, linkType, mtu, name, qdisc, state,
                 vlanid=None, vlanprotocol=None, master=None, **kwargs):
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
        for key, value in kwargs.items():
            setattr(self, key, value)
        if linkType == LinkType.DUMMY:
            self._fakeNics = config.get('vars', 'fake_nics').split(',')
            self._hiddenNics = config.get('vars', 'hidden_nics').split(',')
        if linkType == LinkType.NIC:
            self._hiddenNics = config.get('vars', 'hidden_nics').split(',')
        if linkType == LinkType.VLAN:
            self._hiddenVlans = config.get('vars', 'hidden_vlans').split(',')

    def __repr__(self):
        return '%s: %s(%s) %s' % (self.index, self.name, self.type,
                                  self.address)

    @staticmethod
    def _parse(text):
        """
        Returns the Link attribute dictionary resulting from parsing the text.
        """
        attrs = {}
        attrs['index'], attrs['name'], data = [el.strip() for el in
                                               text.split(':', 2)]

        processedData = [el.strip() for el in
                         data.replace('\\', '', 1).split('\\')]

        baseData = (el for el in
                    processedData[0].split('>')[1].strip().split(' ') if el and
                    el != 'link/none')
        for key, value in pairwise(baseData):
            if key.startswith('link/'):
                key = 'address'
            attrs[key] = value
        if 'address' not in attrs:
            attrs['address'] = None

        if len(processedData) > 1:
            tokens = [token for token in processedData[1].split(' ') if token]
            linkType = tokens.pop(0)
            attrs['linkType'] = linkType
            attrs.update((linkType + tokens[i], tokens[i+1]) for i in
                         range(0, len(tokens)-1, 2))
        return attrs

    @classmethod
    def fromText(cls, text):
        """Creates a Link object from the textual representation from
        iproute2's "ip -o -d link show" command."""
        attrs = cls._parse(text)
        if 'linkType' not in attrs:
            attrs['linkType'] = cls._detectType(attrs['name'])
        if attrs['linkType'] in (LinkType.VLAN, LinkType.MACVLAN):
            name, device = attrs['name'].split('@')
            attrs['name'] = name
            attrs['device'] = device
        return cls(**attrs)

    @staticmethod
    @memoized
    def _detectType(name):
        """Returns the LinkType for the specified device."""
        # TODO: Add support for virtual functions
        detectedType = None
        rc, out, _ = execCmd([_ETHTOOL_BINARY.cmd, '-i', name])
        if rc == 71:  # Unkown driver, usually dummy or loopback
            if not os.path.exists('/sys/class/net/' + name):
                raise ValueError('Device %s does not exist' % name)
            if name == 'lo':
                detectedType = LinkType.LOOPBACK
            else:
                detectedType = LinkType.DUMMY
            return detectedType
        elif rc:
            raise ValueError('Unknown ethtool errcode %s when checking %s' %
                             (rc, name))
        driver = None
        for line in out:
            key, value = line.split(': ')
            if key == 'driver':
                driver = value
                break
        if driver is None:
            raise ValueError('Unknown device driver type')
        if driver in (LinkType.BRIDGE, LinkType.MACVLAN, LinkType.TUN,
                      LinkType.OVS, LinkType.TEAM):
            detectedType = driver
        elif driver == 'bonding':
            detectedType = LinkType.BOND
        elif 'VLAN' in driver or 'vlan' in driver:
            detectedType = LinkType.VLAN
        else:
            detectedType = LinkType.NIC
        return detectedType

    def isDUMMY(self):
        return self.type == LinkType.DUMMY

    def isNIC(self):
        return self.type == LinkType.NIC

    def isVLAN(self):
        return self.type == LinkType.VLAN

    def isFakeNIC(self):
        """
        Returns True iff vdsm config marks the DUMMY dev to be reported as NIC.
        """
        return self.isDUMMY() and anyFnmatch(self.name, self._fakeNics)

    def isHidden(self):
        """Returns True iff vdsm config hides the device."""
        if self.isVLAN():
            return anyFnmatch(self.name, self._hiddenVlans)
        elif self.isDUMMY() or self.isNIC():
            return anyFnmatch(self.name, self._hiddenNics)
        else:
            raise NotImplementedError


def getLinks():
    """Returns a list of Link objects for each link in the system."""
    return [Link.fromText(line) for line in linksShowDetailed()]


def getLink(dev):
    """Returns the Link object for the specified dev."""
    return Link.fromText(linksShowDetailed(dev=dev)[0])


@equals
class Route(object):
    def __init__(self, network, ipaddr=None, device=None, table=None):
        if not _isValid(network, IPNetwork):
            raise ValueError('network %s is not properly defined' % network)

        if ipaddr and not _isValid(ipaddr, IPAddress):
            raise ValueError('ipaddr %s is not properly defined' % ipaddr)

        self.network = network
        self.ipaddr = ipaddr
        self.device = device
        self.table = table

    @classmethod
    def parse(cls, text):
        """
        Returns a dictionary populated with the route attributes found in the
        textual representation.
        """
        route = text.split()
        """
        The network / first column is required, followed by key+value pairs.
        Thus, the length of a route must be odd.
        """
        if len(route) % 2 == 0:
            raise ValueError('Route %s: The length of the textual '
                             'representation of a route must be odd.' % text)

        network, params = route[0], route[1:]
        data = dict(params[i:i + 2] for i in range(0, len(params), 2))
        data['network'] = '0.0.0.0/0' if network == 'default' else network
        return data

    @classmethod
    def fromText(cls, text):
        """
            Creates a Route object from a textual representation. For the vdsm
            use case we require the network IP address and interface to reach
            the network to be provided in the text.

            Examples:
            'default via 192.168.99.254 dev eth0':
            '0.0.0.0/0 via 192.168.99.254 dev eth0 table foo':
            '200.100.50.0/16 via 11.11.11.11 dev eth2 table foo':
        """
        data = cls.parse(text)
        try:
            ipaddr = data['via']
        except KeyError:
            raise ValueError('Route %s: Routes require an IP address.' % text)
        try:
            device = data['dev']
        except KeyError:
            raise ValueError('Route %s: Routes require a device.' % text)
        table = data.get('table')

        return cls(data['network'], ipaddr=ipaddr, device=device, table=table)

    def __str__(self):
        str = '%s via %s dev %s' % (self.network, self.ipaddr, self.device)

        if self.table:
            str += ' table %s' % self.table

        return str

    def __iter__(self):
        for word in str(self).split():
            yield word


@equals
class Rule(object):
    def __init__(self, table, source=None, destination=None, srcDevice=None,
                 detached=False):
        if source:
            if not (_isValid(source, IPAddress) or
                    _isValid(source, IPNetwork)):
                raise ValueError('Source %s invalid: Not an ip address '
                                 'or network.' % source)

        if destination:
            if not (_isValid(destination, IPAddress) or
                    _isValid(destination, IPNetwork)):
                raise ValueError('Destination %s invalid: Not an ip address '
                                 'or network.' % destination)

        self.table = table
        self.source = source
        self.destination = destination
        self.srcDevice = srcDevice
        self.detached = detached

    @classmethod
    def parse(cls, text):
        isDetached = '[detached]' in text
        rule = [entry for entry in text.split() if entry != '[detached]']
        parameters = rule[1:]

        if len(rule) % 2 == 0:
            raise ValueError('Rule %s: The length of a textual representation '
                             'of a rule must be odd. ' % text)

        values = \
            dict(parameters[i:i + 2] for i in range(0, len(parameters), 2))
        values['detached'] = isDetached
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
            raise ValueError('Rule %s: Rules require "lookup" information. ' %
                             text)
        try:
            source = data['from']
        except KeyError:
            raise ValueError('Rule %s: Rules require "from" information. ' %
                             text)

        destination = data.get('to')
        if source == 'all':
            source = None
        if destination == 'all':
            destination = None
        srcDevice = data.get('dev') or data.get('iif')
        detached = data['detached']

        return cls(table, source=source, destination=destination,
                   srcDevice=srcDevice, detached=detached)

    def __str__(self):
        str = 'from '
        if self.source:
            str += self.source
        else:
            str += 'all'
        if self.destination:
            str += ' to %s' % self.destination
        if self.srcDevice:
            str += ' dev %s' % self.srcDevice

        str += ' table %s' % self.table

        return str

    def __iter__(self):
        for word in str(self).split():
            yield word


class IPRoute2Error(Exception):
    pass


def _execCmd(command):
    returnCode, output, error = execCmd(command)

    if returnCode:
        raise IPRoute2Error(error)

    return output


def routeList():
    command = [_IP_BINARY.cmd, 'route']
    return _execCmd(command)


def routeShowAllDefaultGateways():
    command = [_IP_BINARY.cmd, 'route', 'show', 'to', '0.0.0.0/0', 'table',
               'all']
    return _execCmd(command)


def routeShowTable(table):
    command = [_IP_BINARY.cmd, 'route', 'show', 'table', table]
    return _execCmd(command)


def routeAdd(route):
    command = [_IP_BINARY.cmd, 'route', 'add']
    command += route
    _execCmd(command)


def routeDel(route):
    command = [_IP_BINARY.cmd, 'route', 'del']
    command += route
    _execCmd(command)


def _getValidEntries(constructor, iterable):
    for entry in iterable:
        try:
            yield constructor(entry)
        except ValueError:
            pass


def routeExists(route):
    return route in _getValidEntries(constructor=Route.fromText,
                                     iterable=routeShowTable('all'))


def ruleList():
    command = [_IP_BINARY.cmd, 'rule']
    return _execCmd(command)


def ruleAdd(rule):
    command = [_IP_BINARY.cmd, 'rule', 'add']
    command += rule
    _execCmd(command)


def ruleDel(rule):
    command = [_IP_BINARY.cmd, 'rule', 'del']
    command += rule
    _execCmd(command)


def ruleExists(rule):
    return rule in _getValidEntries(constructor=Rule.fromText,
                                    iterable=ruleList())


def linkShowDev(dev):
    command = [_IP_BINARY.cmd, '-d', 'link', 'show', 'dev', dev]
    return _execCmd(command)


def linksShowDetailed(dev=None):
    """Returns a list of detailed link information (each device output
    collapsed into a single line). If a device is specified, only that link
    output will be returned (if present)."""
    command = [_IP_BINARY.cmd, '-d', '-o', 'link']
    if dev:
        command += ['show', 'dev', dev]
    return _execCmd(command)


def addrAdd(dev, ipaddr, netmask):
    command = [_IP_BINARY.cmd, 'addr', 'add', 'dev', dev, '%s/%s' %
               (ipaddr, netmask)]
    _execCmd(command)


def addrFlush(dev):
    command = [_IP_BINARY.cmd, 'addr', 'flush', 'dev', dev]
    _execCmd(command)


def linkAdd(name, linkType, link=None, args=()):
    command = [_IP_BINARY.cmd, 'link', 'add']
    if link:
        command += ['link', link]

    command += ['name', name, 'type', linkType]

    command.extend(args)

    _execCmd(command)


def linkSet(dev, linkArgs):
    command = [_IP_BINARY.cmd, 'link', 'set', 'dev', dev]
    command += linkArgs
    _execCmd(command)


def linkDel(dev):
    command = [_IP_BINARY.cmd, 'link', 'del', 'dev', dev]
    _execCmd(command)


MonitorEvent = namedtuple('MonitorEvent', ['device', 'flags', 'state'])


class Monitor():
    """Minimal wrapper over `ip monitor link`"""

    def __init__(self):
        self.proc = None

    def start(self):
        self.proc = execCmd([_IP_BINARY.cmd, 'monitor', 'link'], sync=False)

    def stop(self):
        self.proc.kill()

    @classmethod
    def _parse(cls, text):
        changes = []
        for line in text.splitlines():
            if line.startswith(' '):
                continue

            tokens = line.split()
            if not tokens[1].endswith(':'):
                continue

            device = tokens[1][:-1]
            flags = frozenset(tokens[2][1:-1].split(','))
            values = dict(tokens[i:i + 2] for i in range(3, len(tokens), 2))

            changes.append(MonitorEvent(device, flags, values.get('state')))

        return changes

    def events(self):
        out, _ = self.proc.communicate()

        return self._parse(out)
