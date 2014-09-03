# Copyright 2013-2014 Red Hat, Inc.
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
from contextlib import closing
from glob import iglob
import array
import errno
import fcntl
import os
import signal
import socket
import struct

from netaddr.core import AddrFormatError
from netaddr import IPAddress
from netaddr import IPNetwork

from .config import config
from .utils import anyFnmatch
from .utils import CommandPath
from .utils import execCmd
from .utils import grouper
from .netlink import link

_IP_BINARY = CommandPath('ip', '/sbin/ip')

NET_SYSFS = '/sys/class/net'
DUMMY_BRIDGE = ';vdsmdummy;'


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
    VETH = 'veth'
    VF = 'vf'


def _parseLinkLine(text):
    """Returns the a link attribute dictionary resulting from parsing the
    output of an iproute2 detailed link entry."""
    attrs = {}
    attrs['index'], attrs['name'], data = [el.strip() for el in
                                           text.split(':', 2)]

    processedData = [el.strip() for el in
                     data.replace('\\', '', 1).split('\\')]

    flags, values = processedData[0].split('>', 1)
    attrs['flags'] = frozenset(flags[1:].split(','))

    baseData = (el for el in values.strip().split(' ') if el and
                el != 'link/none')
    for key, value in grouper(baseData, 2):
        if key.startswith('link/'):
            key = 'address'
        attrs[key] = value
    if 'address' not in attrs:
        attrs['address'] = None

    if len(processedData) > 1:
        tokens = [token for token in processedData[1].split(' ') if token]
        linkType = tokens.pop(0)
        attrs['linkType'] = linkType
        attrs.update((linkType + tokens[i], tokens[i + 1]) for i in
                     range(0, len(tokens) - 1, 2))
    return attrs


@equals
class Link(object):
    """Represents link information obtained from iproute2"""
    _fakeNics = config.get('vars', 'fake_nics').split(',')
    _hiddenBonds = config.get('vars', 'hidden_bonds').split(',')
    _hiddenNics = config.get('vars', 'hidden_nics').split(',')
    _hiddenVlans = config.get('vars', 'hidden_vlans').split(',')
    IFF_RUNNING = 1 << 6

    def __init__(self, address, index, linkType, mtu, name, qdisc, state,
                 vlanid=None, vlanprotocol=None, master=None, device=None,
                 **kwargs):
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
        return '%s: %s(%s) %s' % (self.index, self.name, self.type,
                                  self.address)

    @classmethod
    def fromDict(cls, data):
        # TODO: Tune the following line to use in some cases the type when
        # libnl1 type getting is fixed
        # https://github.com/tgraf/libnl-1.1-stable/issues/1
        data['linkType'] = cls._detectType(data['name'])
        return cls(**data)

    @staticmethod
    def _detectType(name):
        """Returns the LinkType for the specified device."""
        # TODO: Add support for virtual functions
        detectedType = None
        try:
            driver = drv_name(name)
        except IOError as ioe:
            if ioe.errno == errno.EOPNOTSUPP:
                if name == 'lo':
                    detectedType = LinkType.LOOPBACK
                else:
                    detectedType = LinkType.DUMMY
                return detectedType
            else:
                raise  # Reraise other errors like ENODEV
        if driver in (LinkType.BRIDGE, LinkType.MACVLAN, LinkType.TUN,
                      LinkType.OVS, LinkType.TEAM, LinkType.VETH):
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

    def isFakeNIC(self):
        """
        Returns True iff vdsm config marks the DUMMY or VETH dev to be reported
        as NIC.
        """
        if self.isDUMMY() or self.isVETH():
            return anyFnmatch(self.name, self._fakeNics)
        return False

    def isNICLike(self):
        return self.isNIC() or self.isVF() or self.isFakeNIC()

    def isHidden(self):
        """Returns True iff vdsm config hides the device."""
        if self.isVLAN():
            return anyFnmatch(self.name, self._hiddenVlans)
        elif self.isNICLike():
            return (anyFnmatch(self.name, self._hiddenNics) or
                    (self.master and _bondExists(self.master) and
                     anyFnmatch(self.master, self._hiddenBonds)) or
                    (self.isVF() and self._isVFhidden()))
        elif self.isBOND():
            return anyFnmatch(self.name, self._hiddenBonds)
        elif self.isBRIDGE():
            return self.name == DUMMY_BRIDGE
        return False

    def _isVFhidden(self):
        if self.address == '00:00:00:00:00:00':
            return True
        # We hide a VF if there exists a macvtap device with the same address.
        # We assume that such VFs are used by a VM and should not be reported
        # as host nics
        for path in iglob('/sys/class/net/*/address'):
            dev = os.path.basename(os.path.dirname(path))
            if (dev != self.name and _read_stripped(path) == self.address and
                    self._detectType(dev) == LinkType.MACVLAN):
                return True
        return False

    @property
    def oper_up(self):
        return bool(self.flags & self.IFF_RUNNING)


def drv_name(devName):
    """Returns the driver used by a device.
    Throws IOError ENODEV for non existing devices.
    Throws IOError EOPNOTSUPP for non supported devices, i.g., loopback."""
    ETHTOOL_GDRVINFO = 0x00000003  # ETHTOOL Get driver info command
    SIOCETHTOOL = 0x8946  # Ethtool interface
    DRVINFO_FORMAT = '= I 32s 32s 32s 32s 32s 12s 5I'
    IFREQ_FORMAT = '16sPi'  # device_name, buffer_pointer, buffer_len
    buff = array.array('c', b'\0' * struct.calcsize(DRVINFO_FORMAT))
    cmd = struct.pack('= I', ETHTOOL_GDRVINFO)
    buff[0:len(cmd)] = array.array('c', cmd)
    data = struct.pack(IFREQ_FORMAT, devName, *buff.buffer_info())
    with closing(socket.socket(socket.AF_INET, socket.SOCK_DGRAM)) as sock:
        fcntl.ioctl(sock, SIOCETHTOOL, data)
    (cmd, driver, version, fw_version, businfo, _, _, n_priv_flags, n_stats,
     testinfo_len, eedump_len, regdump_len) = struct.unpack(DRVINFO_FORMAT,
                                                            buff)
    return driver.rstrip('\0')  # C string end with the leftmost null char


def _read_stripped(path):
    with open(path) as f:
        return f.read().strip()


def _bondExists(bondName):
    return os.path.exists('/sys/class/net/%s/bonding' % bondName)


def getLinks():
    """Returns a list of Link objects for each link in the system."""
    links = []
    for data in link.iter_links():
        try:
            links.append(Link.fromDict(data))
        except IOError:  # If a link goes missing we just don't report it
            continue
    return links


def getLink(dev):
    """Returns the Link object for the specified dev."""
    return Link.fromDict(link.get_link(dev))


@equals
class Route(object):
    def __init__(self, network, via=None, src=None, device=None, table=None):
        if network != 'local' and not _isValid(network, IPNetwork):
            raise ValueError('network %s is not properly defined' % network)

        if via and not _isValid(via, IPAddress):
            raise ValueError('via %s is not a proper IP address' % via)

        if src and not _isValid(src, IPAddress):
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

        network = route[0]
        if network == 'local':
            params = route[2:]
        else:
            params = route[1:]

        data = dict(params[i:i + 2] for i in range(0, len(params), 2))
        data['network'] = '0.0.0.0/0' if network == 'default' else network
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

        return cls(data['network'], via=via, src=src, device=device,
                   table=table)

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
        output = 'from '
        if self.source:
            output += self.source
        else:
            output += 'all'
        if self.destination:
            output += ' to %s' % self.destination
        if self.srcDevice:
            output += ' dev %s' % self.srcDevice

        output += ' table %s' % self.table

        return output

    def __iter__(self):
        for word in str(self).split():
            yield word


class IPRoute2Error(Exception):
    pass


class MonitorError(Exception):
    pass


def _execCmd(command):
    returnCode, output, error = execCmd(command)

    if returnCode:
        raise IPRoute2Error(error)

    return output


def routeList():
    command = [_IP_BINARY.cmd, 'route']
    return _execCmd(command)


def routeShowGateways(table):
    command = [_IP_BINARY.cmd, 'route', 'show', 'to', '0.0.0.0/0', 'table',
               table]
    return _execCmd(command)


def routeShowTable(table):
    command = [_IP_BINARY.cmd, 'route', 'show', 'table', table]
    return _execCmd(command)


def routeAdd(route, family=4, dev=None):
    command = [_IP_BINARY.cmd, '-%s' % family, 'route', 'add']
    command += route
    if dev is not None:
        command += ['dev', dev]
    _execCmd(command)


def routeDel(route):
    command = [_IP_BINARY.cmd, 'route', 'del']
    command += route
    _execCmd(command)


def routeGet(ipAddress):
    command = [_IP_BINARY.cmd, 'route', 'get']
    command += ipAddress
    return _execCmd(command)


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


def addrAdd(dev, ipaddr, netmask, family=4):
    command = [_IP_BINARY.cmd, '-%s' % family, 'addr', 'add', 'dev', dev,
               '%s/%s' % (ipaddr, netmask)]
    _execCmd(command)


def addrFlush(dev, family='both'):
    """
    Remove IP addresses from the interface, optionally by protocol family.

    If the 'family' parameter is different than 4 or 6, flush all addresses.
    If it is 6, remove only globally-routable addresses to preserve
    a link-local address necessary for DHCPv6 functionality.
    """
    family = ['-%s' % family] if family in (4, 6) else []
    command = [_IP_BINARY.cmd] + family + ['addr', 'flush', 'dev', dev]
    if family == 6:
        command += ['scope', 'global']
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


MonitorEvent = namedtuple('MonitorEvent', ['index', 'device', 'flags',
                                           'state'])


class Monitor(object):
    """Wrapper over `ip monitor link`. Usage:
    Get events collected while the monitor was running:

        mon = Monitor()
        mon.start()
        ....
        mon.stop()
        for event in mon:
            handle event

    Monitoring events forever:
        mon = Monitor()
        mon.start()
        for event in mon:
            handle event

    Note that the underlying `ip monitor` process is killed when its controlled
    thread dies, so as not to leave stray processes when Vdsm crahsed.
    """
    _DELETED_TEXT = 'Deleted'
    LINK_STATE_DELETED = 'DELETED'

    def __init__(self):
        self.proc = None

    def __iter__(self):
        if self.proc is None:
            raise MonitorError('The monitor has not run yet')
        for line in self.proc.stdout:
            yield self._parseLine(line)

    def start(self):
        self.proc = execCmd([_IP_BINARY.cmd, '-d', '-o', 'monitor', 'link'],
                            deathSignal=signal.SIGKILL,
                            sync=False)
        self.proc.blocking = True

    def stop(self):
        self.proc.kill()
        self.proc.wait()

    @classmethod
    def _parseLine(cls, line):
        state = None
        if line.startswith(cls._DELETED_TEXT):
            state = cls.LINK_STATE_DELETED
            line = line[len(cls._DELETED_TEXT):]

        data = _parseLinkLine(line)
        # Consider everything with an '@' symbol a vlan/macvlan/macvtap
        # since that's how iproute2 reports it and there is currently no
        # disambiguation (iproute bug https://bugzilla.redhat.com/1042799
        data['name'] = data['name'].split('@', 1)[0]
        state = state if state or 'state' not in data else data['state']
        return MonitorEvent(data['index'], data['name'], data['flags'], state)

    @classmethod
    def _parse(cls, text):
        return [cls._parseLine(line) for line in text.splitlines()]
