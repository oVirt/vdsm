#
# Copyright 2009-2014 Red Hat, Inc.
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
from collections import defaultdict
import errno
from glob import iglob
from datetime import datetime, timedelta
from functools import partial
from itertools import chain
import json
import logging
from netaddr import IPNetwork
import os
import shlex
import socket
import struct
import xml.etree.cElementTree as etree

import six

from . import constants
from .ipwrapper import drv_name
from .ipwrapper import DUMMY_BRIDGE
from .ipwrapper import getLink, getLinks, Link
from .ipwrapper import IPRoute2Error
from .ipwrapper import Route
from .ipwrapper import routeGet
from .ipwrapper import routeShowGateways
from . import libvirtconnection
from .netconfpersistence import RunningConfig
from .netlink import link as nl_link
from .netlink import addr as nl_addr
from .netlink import route as nl_route
from .utils import memoized


NET_CONF_DIR = '/etc/sysconfig/network-scripts/'
# ifcfg persistence directories
NET_CONF_BACK_DIR = constants.P_VDSM_LIB + 'netconfback/'

# possible names of dhclient's lease files (e.g. as NetworkManager's slave)
_DHCLIENT_LEASES_GLOBS = [
    '/var/lib/dhclient/dhclient*.lease*',  # iproute2 configurator, initscripts
    '/var/lib/NetworkManager/dhclient*-*.lease',
]

DNS_CONF_FILE = '/etc/resolv.conf'
NET_CONF_PREF = NET_CONF_DIR + 'ifcfg-'
PROC_NET_VLAN = '/proc/net/vlan/'
NET_PATH = '/sys/class/net'
BONDING_MASTERS = '/sys/class/net/bonding_masters'
BONDING_SLAVES = '/sys/class/net/%s/bonding/slaves'
BONDING_ACTIVE_SLAVE = '/sys/class/net/%s/bonding/active_slave'
BONDING_OPT = '/sys/class/net/%s/bonding/%s'
BONDING_DEFAULTS = constants.P_VDSM_LIB + 'bonding-defaults.json'
BRIDGING_OPT = '/sys/class/net/%s/bridge/%s'
_BONDING_FAILOVER_MODES = frozenset(('1', '3'))
_BONDING_LOADBALANCE_MODES = frozenset(('0', '2', '4', '5', '6'))
_EXCLUDED_BONDING_ENTRIES = frozenset((
    'slaves', 'active_slave', 'mii_status', 'queue_id', 'ad_aggregator',
    'ad_num_ports', 'ad_actor_key', 'ad_partner_key', 'ad_partner_mac'
))
_IFCFG_ZERO_SUFFIXED = frozenset(
    ('IPADDR0', 'GATEWAY0', 'PREFIX0', 'NETMASK0'))

LIBVIRT_NET_PREFIX = 'vdsm-'
DEFAULT_MTU = '1500'

OPERSTATE_UP = 'up'
OPERSTATE_UNKNOWN = 'unknown'
OPERSTATE_DOWN = 'down'
DUMMY_BRIDGE  # Appease flake8 since dummy bridge should be exported from here


def get_host_nameservers():
    """Returns a list of nameservers listed in /etc/resolv.conf"""
    with open(DNS_CONF_FILE, 'r') as file_object:
        file_text = file_object.read()
    return _parse_dnss(file_text)


def _parse_dnss(file_text):
    dnss = []
    for line in file_text.splitlines():
        words = line.strip().split()
        if words[0] == 'nameserver':
            dnss.append(words[1])
    return dnss


def _visible_devs(predicate):
    """Returns a list of visible (vdsm manageable) links for which the
    predicate is True"""
    return [dev.name for dev in getLinks() if predicate(dev) and
            not dev.isHidden()]


nics = partial(_visible_devs, Link.isNICLike)
bondings = partial(_visible_devs, Link.isBOND)
vlans = partial(_visible_devs, Link.isVLAN)
bridges = partial(_visible_devs, Link.isBRIDGE)


def networks():
    """
    Get dict of networks from libvirt

    :returns: dict of networkname={properties}
    :rtype: dict of dict
            { 'ovirtmgmt': { 'bridge': 'ovirtmgmt', 'bridged': True}
              'red': { 'iface': 'red', 'bridged': False}}
    """
    nets = {}
    conn = libvirtconnection.get()
    allNets = ((net, net.name()) for net in conn.listAllNetworks(0))
    for net, netname in allNets:
        if netname.startswith(LIBVIRT_NET_PREFIX):
            netname = netname[len(LIBVIRT_NET_PREFIX):]
            nets[netname] = {}
            xml = etree.fromstring(net.XMLDesc(0))
            interface = xml.find('.//interface')
            if interface is not None:
                nets[netname]['iface'] = interface.get('dev')
                nets[netname]['bridged'] = False
            else:
                nets[netname]['bridge'] = xml.find('.//bridge').get('name')
                nets[netname]['bridged'] = True
    return nets


def slaves(bonding):
    with open(BONDING_SLAVES % bonding) as f:
        return f.readline().split()


def _active_slave(bonding):
    """
    :param bonding:
    :return: active slave when one exists or '' otherwise
    """
    with open(BONDING_ACTIVE_SLAVE % bonding) as f:
        return f.readline().rstrip()


def _bondOpts(bond, keys=None):
    """ Returns a dictionary of bond option name and a values iterable. E.g.,
    {'mode': ('balance-rr', '0'), 'xmit_hash_policy': ('layer2', '0')}
    """
    if keys is None:
        paths = iglob(BONDING_OPT % (bond, '*'))
    else:
        paths = (BONDING_OPT % (bond, key) for key in keys)
    opts = {}
    for path in paths:
        with open(path) as optFile:
            opts[os.path.basename(path)] = [
                el for el in optFile.read().rstrip().split(' ') if el]
    return opts


def bondOpts(bond, keys=None):
    """
    Return a dictionary in the same format as _bondOpts(). Exclude entries that
    are not bonding options, e.g. 'ad_num_ports' or 'slaves'.
    """
    return dict(((opt, val) for (opt, val) in _bondOpts(bond, keys).iteritems()
                 if opt not in _EXCLUDED_BONDING_ENTRIES))


def _bridge_options(bridge, keys=None):
    """Returns a dictionary of bridge option name and value. E.g.,
    {'max_age': '2000', 'gc_timer': '332'}"""
    BR_KEY_BLACKLIST = ('flush',)
    if keys is None:
        paths = iglob(BRIDGING_OPT % (bridge, '*'))
    else:
        paths = (BRIDGING_OPT % (bridge, key) for key in keys)
    opts = {}
    for path in paths:
        key = os.path.basename(path)
        if key in BR_KEY_BLACKLIST:
            continue
        with open(path) as optFile:
            opts[key] = optFile.read().rstrip()
    return opts


def ports(bridge):
    return os.listdir('/sys/class/net/' + bridge + '/brif')


def getMtu(iface):
    with open('/sys/class/net/%s/mtu' % iface) as f:
        mtu = f.readline().rstrip()
    return int(mtu)


def getMaxMtu(devs, mtu):
    """
    Get the max MTU value from current state/parameter

    :param devs: iterable of network devices
    :type devs: iterable

    :param mtu: mtu value
    :type mtu: integer

    getMaxMtu return the highest value in a connection tree,
    it check if a vlan, bond that have a higher mtu value
    """
    return max([getMtu(dev) for dev in devs] + [mtu])


def _bridge_stp_state(bridge):
    with open('/sys/class/net/%s/bridge/stp_state' % bridge) as stp_file:
        stp = stp_file.readline()
    if stp == '1\n':
        return 'on'
    else:
        return 'off'


def stp_booleanize(value):
    if value is None:
        return False
    if type(value) is bool:
        return value
    if value.lower() in ('true', 'on', 'yes'):
        return True
    elif value.lower() in ('false', 'off', 'no'):
        return False
    else:
        raise ValueError('Invalid value for bridge stp')


def operstate(dev):
    with open('/sys/class/net/%s/operstate' % dev) as operstateFile:
        return operstateFile.read().strip()


def vlanSpeed(vlanName):
    """Returns the vlan's underlying device speed."""
    vlanDevName = _vlan_device(vlanName)
    vlanDev = getLink(vlanDevName)
    if vlanDev.isNIC():
        speed = nicSpeed(vlanDevName)
    elif vlanDev.isBOND():
        speed = bondSpeed(vlanDevName)
    else:
        speed = 0
    return speed


def _ibHackedSpeed(nicName):
    """If the nic is an InfiniBand device, return a speed of 10000 Mbps.

    This is only needed until the kernel reports ib*/speed, see
    https://bugzilla.redhat.com/show_bug.cgi?id=1101314
    """
    try:
        return 10000 if drv_name(nicName) == 'ib_ipoib' else 0
    except IOError:
        return 0


def nicSpeed(nicName):
    """Returns the nic speed if it is a legal value, nicName refers to a
    nic and nic is UP, 0 otherwise."""
    try:
        if operstate(nicName) == OPERSTATE_UP:
            with open('/sys/class/net/%s/speed' % nicName) as speedFile:
                s = int(speedFile.read())
            # the device may have been disabled/downed after checking
            # so we validate the return value as sysfs may return
            # special values to indicate the device is down/disabled
            if s not in (2 ** 16 - 1, 2 ** 32 - 1) and s > 0:
                return s
    except IOError as ose:
        if ose.errno == errno.EINVAL:
            return _ibHackedSpeed(nicName)
        else:
            logging.exception('cannot read %s nic speed', nicName)
    except Exception:
        logging.exception('cannot read %s speed', nicName)
    return 0


def bondSpeed(bondName):
    """Returns the bond speed if bondName refers to a bond, 0 otherwise."""
    opts = _bondOpts(bondName, keys=['slaves', 'active_slave', 'mode'])
    try:
        if opts['slaves']:
            if opts['mode'][1] in _BONDING_FAILOVER_MODES:
                active_slave = opts['active_slave']
                s = nicSpeed(active_slave[0]) if active_slave else 0
            elif opts['mode'][1] in _BONDING_LOADBALANCE_MODES:
                s = sum(nicSpeed(slave) for slave in opts['slaves'])
            return s
    except Exception:
        logging.exception('cannot read %s speed', bondName)
    return 0


def prefix2netmask(prefix):
    if not 0 <= prefix <= 32:
        raise ValueError('%s is not a valid prefix value. It must be between '
                         '0 and 32' % prefix)
    return socket.inet_ntoa(
        struct.pack("!I", int('1' * prefix + '0' * (32 - prefix), 2)))


def getDefaultGateway():
    output = routeShowGateways('main')
    return Route.fromText(output[0]) if output else None


def getIpInfo(dev, ipaddrs=None, ipv4_gateway=None):
    """Report IP addresses of a device. if there are multiple primary IP
    addresses, report in ipv4addr the one that is in the same subnet of
    ipv4_gateway, if it is supplied."""
    # TODO: support same logic for ipv6

    if ipaddrs is None:
        ipaddrs = _getIpAddrs()
    ipv4addr = ipv4netmask = ''
    ipv4addrs = []
    ipv6addrs = []

    def addr_in_gw_net(address, prefix, ipv4_gw):
        addr_net = IPNetwork('{}/{}'.format(address, prefix))
        gw_net = IPNetwork('{}/{}'.format(ipv4_gw, prefix))
        return addr_net in gw_net

    for addr in ipaddrs[dev]:
        address_cidr = nl_addr.cidr_form(addr)  # x.y.z.t/N
        if addr['family'] == 'inet':  # ipv4
            ipv4addrs.append(address_cidr)
            if nl_addr.is_primary(addr) and ipv4_gateway and ipv4addr == '':
                address, prefix = nl_addr.split(addr)
                if addr_in_gw_net(address, prefix, ipv4_gateway):
                    ipv4addr = address
                    ipv4netmask = str(IPNetwork(address_cidr).netmask)
        else:  # ipv6
            ipv6addrs.append(address_cidr)
    if ipv4addrs and ipv4addr == '':
        # If we didn't find an address in the gateway subnet (which is
        # legal if there is another route that takes us to the gateway) we
        # choose to report the first address
        ipv4addr = ipv4addrs[0].split('/')[0]
        ipv4netmask = str(IPNetwork(ipv4addrs[0]).netmask)

    return ipv4addr, ipv4netmask, ipv4addrs, ipv6addrs


def gethwaddr(dev):
    with open('/sys/class/net/%s/address' % dev) as addr:
        return addr.read().strip()


def getIfaceCfg(iface):
    ifaceCfg = {}
    try:
        with open(NET_CONF_PREF + iface) as f:
            for line in shlex.split(f, comments=True):
                k, v = line.split('=', 1)
                if k in _IFCFG_ZERO_SUFFIXED:
                    k = k[:-1]
                ifaceCfg[k] = v
    except Exception:
        pass
    return ifaceCfg


def _permanent_address():
    paddr = {}
    for b in bondings():
        slave = ''
        with open('/proc/net/bonding/' + b) as f:
            for line in f:
                if line.startswith('Slave Interface: '):
                    slave = line[len('Slave Interface: '):-1]
                if line.startswith('Permanent HW addr: '):
                    paddr[slave] = line[len('Permanent HW addr: '):-1]
    return paddr


@memoized
def getAllDefaultBondingOptions():
    """
    Return default options per mode, in a dictionary of dictionaries. All keys
    are numeric modes stored as strings for coherence with 'mode' option value.
    """
    with open(BONDING_DEFAULTS) as defaults:
        return json.loads(defaults.read())


@memoized
def getDefaultBondingOptions(mode=None):
    """
    Return default options for the given mode. If it is None, return options
    for the default mode (usually '0').
    """
    defaults = getAllDefaultBondingOptions()

    if mode is None:
        mode = defaults['0']['mode'][-1]

    return defaults[mode]


def _getBondingOptions(bond):
    """
    Return non-empty options differing from defaults, excluding not actual or
    not applicable options, e.g. 'ad_num_ports' or 'slaves'.
    """
    opts = bondOpts(bond)
    mode = opts['mode'][-1] if 'mode' in opts else None
    defaults = getDefaultBondingOptions(mode)

    return dict(((opt, val[-1]) for (opt, val) in opts.iteritems()
                 if val and val != defaults.get(opt)))


def bondOptsForIfcfg(opts):
    """
    Options having symbolic values, e.g. 'mode', are presented by sysfs in
    the order symbolic name, numeric value, e.g. 'balance-rr 0'.
    Choose the numeric value from a list given by bondOpts().
    """
    return ' '.join((opt + '=' + val for (opt, val)
                     in sorted(opts.iteritems())))


def _dhcp_used(iface, ifaces_with_active_leases, net_attrs, family=4):
    if net_attrs is None:
        return iface in ifaces_with_active_leases
    else:
        try:
            if family == 4:
                return net_attrs['bootproto'] == 'dhcp'
            else:
                return net_attrs['dhcpv6']
        except KeyError:
            return False


def _getNetInfo(iface, bridged, routes, ipaddrs, dhcpv4_ifaces, dhcpv6_ifaces,
                net_attrs):
    '''Returns a dictionary of properties about the network's interface status.
    Raises a KeyError if the iface does not exist.'''
    data = {}
    try:
        if bridged:
            data.update({'ports': ports(iface),
                         'stp': _bridge_stp_state(iface)})
        else:
            # ovirt-engine-3.1 expects to see the "interface" attribute iff the
            # network is bridgeless. Please remove the attribute and this
            # comment when the version is no longer supported.
            data['interface'] = iface

        gateway = _get_gateway(routes, iface)
        ipv4addr, ipv4netmask, ipv4addrs, ipv6addrs = getIpInfo(
            iface, ipaddrs, gateway)
        data.update({'iface': iface, 'bridged': bridged,
                     'addr': ipv4addr, 'netmask': ipv4netmask,
                     'dhcpv4': _dhcp_used(iface, dhcpv4_ifaces, net_attrs),
                     'dhcpv6': _dhcp_used(iface, dhcpv6_ifaces, net_attrs,
                                          family=6),
                     'ipv4addrs': ipv4addrs,
                     'ipv6addrs': ipv6addrs,
                     'gateway': gateway,
                     'ipv6gateway': _get_gateway(routes, iface, family=6),
                     'mtu': str(getMtu(iface))})
    except (IOError, OSError) as e:
        if e.errno == errno.ENOENT:
            logging.info('Obtaining info for net %s.', iface, exc_info=True)
            raise KeyError('Network %s was not found' % iface)
        else:
            raise
    return data


def _bridgeinfo(link):
    return {'ports': ports(link.name),
            'stp': _bridge_stp_state(link.name),
            'opts': _bridge_options(link.name)}


def _nicinfo(link, paddr):
    info = {'hwaddr': link.address, 'speed': nicSpeed(link.name)}
    if paddr.get(link.name):
        info['permhwaddr'] = paddr[link.name]
    return info


def _bondinfo(link):
    return {'hwaddr': link.address, 'slaves': slaves(link.name),
            'active_slave': _active_slave(link.name),
            'opts': _getBondingOptions(link.name)}


def _bondOptsCompat(info):
    """Add legacy ifcfg option if missing."""
    if info['opts'] and 'BONDING_OPTS' not in info['cfg']:
        info['cfg']['BONDING_OPTS'] = bondOptsForIfcfg(info['opts'])


def _vlaninfo(link):
    return {'iface': link.device, 'vlanid': link.vlanid}


def _devinfo(link, routes, ipaddrs, dhcpv4_ifaces, dhcpv6_ifaces):
    gateway = _get_gateway(routes, link.name)
    ipv4addr, ipv4netmask, ipv4addrs, ipv6addrs = getIpInfo(
        link.name, ipaddrs, gateway)
    info = {'addr': ipv4addr,
            'cfg': getIfaceCfg(link.name),
            'ipv4addrs': ipv4addrs,
            'ipv6addrs': ipv6addrs,
            'gateway': gateway,
            'ipv6gateway': _get_gateway(routes, link.name, family=6),
            'dhcpv4': link.name in dhcpv4_ifaces,  # to be refined if a network
            'dhcpv6': link.name in dhcpv6_ifaces,  # is not configured for DHCP
            'mtu': str(link.mtu),
            'netmask': ipv4netmask}
    if 'BOOTPROTO' not in info['cfg']:
        info['cfg']['BOOTPROTO'] = 'dhcp' if info['dhcpv4'] else 'none'
    return info


def _propose_updates_to_reported_dhcp(network_info, networking):
    """
    Report DHCPv4/6 of a network's topmost device based on the network's
    configuration, to fix bug #1184497 (DHCP still being reported for hours
    after a network got static IP configuration, as reporting is based on
    dhclient leases).
    """
    updated_networking = dict(bondings={}, bridges={}, nics={}, vlans={})
    network_device = network_info['iface']

    for devices in ('bridges', 'vlans', 'bondings', 'nics'):
        dev_info = networking[devices].get(network_device)
        if dev_info:
            cfg = {}
            updated_networking[devices][network_device] = {
                'dhcpv4': network_info['dhcpv4'],
                'dhcpv6': network_info['dhcpv6'],
                'cfg': cfg,
            }
            cfg['BOOTPROTO'] = 'dhcp' if network_info['dhcpv4'] else 'none'
            break

    return updated_networking


def _update_reported_dhcp(replacement, networking):
    """
    For each network device (representing a network), apply updates to reported
    DHCP-related fields, as prepared by _propose_updates_to_reported_dhcp.
    """
    for device_type, devices in six.iteritems(replacement):
        for device_name, replacement_device_info in six.iteritems(devices):
            device_info = networking[device_type][device_name]
            device_info['dhcpv4'] = replacement_device_info['dhcpv4']
            device_info['dhcpv6'] = replacement_device_info['dhcpv6']
            # Remove when cluster level < 3.6 is no longer supported and thus
            # it is not necessary to report ifcfg-like BOOTPROTO field.
            if replacement_device_info['cfg']:
                device_info['cfg'].update(replacement_device_info['cfg'])


def _parse_expiry_time(expiry_time):
    EPOCH = 'epoch '

    if expiry_time == 'never':
        return None
    elif expiry_time.startswith(EPOCH):
        since_epoch = expiry_time[len(EPOCH):]
        return datetime.utcfromtimestamp(float(since_epoch))

    else:
        return datetime.strptime(expiry_time, '%w %Y/%m/%d %H:%M:%S')


def _parse_lease_file(lease_file):
    IFACE = '  interface "'
    IFACE_END = '";\n'
    EXPIRE = '  expire '  # DHCPv4
    STARTS = '      starts '  # DHCPv6
    MAX_LIFE = '      max-life '
    VALUE_END = ';\n'

    family = None
    iface = None
    lease6_starts = None
    dhcpv4_ifaces, dhcpv6_ifaces = set(), set()

    for line in lease_file:
        if line == 'lease {\n':
            family = 4
            iface = None
            continue

        elif line == 'lease6 {\n':
            family = 6
            iface = None
            continue

        if family and line.startswith(IFACE) and line.endswith(IFACE_END):
            iface = line[len(IFACE):-len(IFACE_END)]

        elif family == 4:
            if line.startswith(EXPIRE):
                end = line.find(';')
                if end == -1:
                    continue  # the line should always contain a ;

                expiry_time = _parse_expiry_time(line[len(EXPIRE):end])
                if expiry_time is not None and datetime.utcnow() > expiry_time:
                    family = None
                    continue

            elif line == '}\n':
                family = None
                if iface:
                    dhcpv4_ifaces.add(iface)

        elif family == 6:
            if line.startswith(STARTS) and line.endswith(VALUE_END):
                timestamp = float(line[len(STARTS):-len(VALUE_END)])
                lease6_starts = datetime.utcfromtimestamp(timestamp)

            elif (lease6_starts and line.startswith(MAX_LIFE) and
                    line.endswith(VALUE_END)):
                seconds = float(line[len(MAX_LIFE):-len(VALUE_END)])
                max_life = timedelta(seconds=seconds)
                if datetime.utcnow() > lease6_starts + max_life:
                    family = None
                    continue

            elif line == '}\n':
                family = None
                if iface:
                    dhcpv6_ifaces.add(iface)

    return dhcpv4_ifaces, dhcpv6_ifaces


def _get_dhclient_ifaces(lease_files_globs=_DHCLIENT_LEASES_GLOBS):
    """Return a pair of sets containing ifaces configured using dhclient (-6)

    dhclient stores DHCP leases to file(s) whose names can be specified
    by the lease_files_globs parameter (an iterable of glob strings).
    """
    dhcpv4_ifaces, dhcpv6_ifaces = set(), set()

    for lease_files_glob in lease_files_globs:
        for lease_path in iglob(lease_files_glob):
            with open(lease_path) as lease_file:
                found_dhcpv4, found_dhcpv6 = _parse_lease_file(lease_file)
                dhcpv4_ifaces.update(found_dhcpv4)
                dhcpv6_ifaces.update(found_dhcpv6)

    return dhcpv4_ifaces, dhcpv6_ifaces


def _getIpAddrs():
    addrs = defaultdict(list)
    for addr in nl_addr.iter_addrs():
        addrs[addr['label']].append(addr)
    return addrs


def _get_gateway(routes_by_dev, dev, family=4,
                 table=nl_route._RT_TABLE_UNSPEC):
    """
    Return the default gateway for a device and an address family
    :param routes_by_dev: dictionary from device names to a list of routes.
    :type routes_by_dev: dict[str]->list[dict[str]->str]
    """
    routes = routes_by_dev[dev]

    # VDSM's source routing thread creates a separate table (with an ID derived
    # currently from an IPv4 address) for each device so we have to look for
    # the gateway in all tables (RT_TABLE_UNSPEC), not just the 'main' one.
    gateways = [r for r in routes if r['destination'] == 'none' and
                (r.get('table') == table or
                 table == nl_route._RT_TABLE_UNSPEC) and
                r['scope'] == 'global' and
                r['family'] == ('inet6' if family == 6 else 'inet')
                ]
    if not gateways:
        return '::' if family == 6 else ''
    elif len(gateways) == 1:
        return gateways[0]['gateway']
    else:
        unique_gateways = frozenset(route['gateway'] for route in gateways)
        if len(unique_gateways) == 1:
            gateway, = unique_gateways
            logging.debug('The gateway %s is duplicated for the device %s',
                          gateway, dev)
            return gateway
        else:
            # We could pick the first gateway or the one with the lowest metric
            # but, in general, there are also routing rules in the game so we
            # should probably ask the kernel somehow.
            logging.error('Multiple IPv%s gateways for the device %s in table '
                          '%s: %r', family, dev, table, gateways)
            return '::' if family == 6 else ''


def _get_routes():
    """Returns all the routes data dictionaries"""
    routes = defaultdict(list)
    for route in nl_route.iter_routes():
        oif = route.get('oif')
        if oif is not None:
            routes[oif].append(route)
    return routes


def libvirtNets2vdsm(nets, running_config=None, routes=None, ipAddrs=None,
                     dhcpv4_ifaces=None, dhcpv6_ifaces=None):
    if running_config is None:
        running_config = RunningConfig()
    if routes is None:
        routes = _get_routes()
    if ipAddrs is None:
        ipAddrs = _getIpAddrs()
    if dhcpv4_ifaces is None or dhcpv6_ifaces is None:
        dhcpv4_ifaces, dhcpv6_ifaces = _get_dhclient_ifaces()
    d = {}
    for net, netAttr in nets.iteritems():
        try:
            # Pass the iface if the net is _not_ bridged, the bridge otherwise
            d[net] = _getNetInfo(netAttr.get('iface', net), netAttr['bridged'],
                                 routes, ipAddrs, dhcpv4_ifaces, dhcpv6_ifaces,
                                 running_config.networks.get(net, None))
        except KeyError:
            continue  # Do not report missing libvirt networks.
    return d


def get(vdsmnets=None):
    networking = {'bondings': {}, 'bridges': {}, 'networks': {}, 'nics': {},
                  'vlans': {}}
    paddr = _permanent_address()
    ipaddrs = _getIpAddrs()
    dhcpv4_ifaces, dhcpv6_ifaces = _get_dhclient_ifaces()
    routes = _get_routes()
    running_config = RunningConfig()

    if vdsmnets is None:
        libvirt_nets = networks()
        networking['networks'] = libvirtNets2vdsm(libvirt_nets, running_config,
                                                  routes, ipaddrs,
                                                  dhcpv4_ifaces, dhcpv6_ifaces)
    else:
        networking['networks'] = vdsmnets

    for dev in (link for link in getLinks() if not link.isHidden()):
        if dev.isBRIDGE():
            devinfo = networking['bridges'][dev.name] = _bridgeinfo(dev)
        elif dev.isNICLike():
            devinfo = networking['nics'][dev.name] = _nicinfo(dev, paddr)
        elif dev.isBOND():
            devinfo = networking['bondings'][dev.name] = _bondinfo(dev)
        elif dev.isVLAN():
            devinfo = networking['vlans'][dev.name] = _vlaninfo(dev)
        else:
            continue
        devinfo.update(_devinfo(dev, routes, ipaddrs, dhcpv4_ifaces,
                                dhcpv6_ifaces))
        if dev.isBOND():
            _bondOptsCompat(devinfo)

    for network_name, network_info in six.iteritems(networking['networks']):
        if network_info['bridged']:
            network_info['cfg'] = networking['bridges'][network_name]['cfg']
        updates = _propose_updates_to_reported_dhcp(network_info, networking)
        _update_reported_dhcp(updates, networking)

    return networking


def isVlanned(dev):
    return any(vlan.startswith(dev + '.') for vlan in vlans())


def _vlan_device(vlan):
    """ Return the device of the given VLAN. """
    vlanLink = getLink(vlan)
    return vlanLink.device


def _vlan_id(vlan):
    """ Return the ID of the given VLAN. """
    vlanLink = getLink(vlan)
    return int(vlanLink.vlanid)


def getIpAddresses():
    "Return a list of the host's IPv4 addresses"
    return [addr['address'] for addr in nl_addr.iter_addrs() if
            addr['family'] == 'inet']


def IPv4toMapped(ip):
    """Return an IPv6 IPv4-mapped address for the IPv4 address"""
    mapped = None

    try:
        ipv6bin = '\x00' * 10 + '\xff\xff' + socket.inet_aton(ip)
        mapped = socket.inet_ntop(socket.AF_INET6, ipv6bin)
    except socket.error as e:
        logging.debug("getIfaceByIP: %s", e)

    return mapped


def getRouteDeviceTo(destinationIP):
    """Return the name of the device leading to destinationIP or the empty
       string if none is found"""
    try:
        route = routeGet([destinationIP])[0]
    except (IPRoute2Error, IndexError):
        logging.exception('Could not route to %s', destinationIP)
        return ''

    try:
        return Route.fromText(route).device
    except ValueError:
        logging.exception('Could not parse route %s', route)
        return ''


def getDeviceByIP(ip):
    """
    Get network device by IP address
    :param ip: String representing IPv4 or IPv6
    """
    for addr in nl_addr.iter_addrs():
        address = addr['address'].split('/')[0]
        if ((addr['family'] == 'inet' and
             ip in (address, IPv4toMapped(address))) or (
                addr['family'] == 'inet6' and ip == address)):
            return addr['label']
    return ''


class NetInfo(object):
    def __init__(self, _netinfo=None):
        if _netinfo is None:
            _netinfo = get()

        self.networks = _netinfo['networks']
        self.vlans = _netinfo['vlans']
        self.nics = _netinfo['nics']
        self.bondings = _netinfo['bondings']
        self.bridges = _netinfo['bridges']

    def updateDevices(self):
        """Updates the object device information while keeping the cached
        network information."""
        _netinfo = get(vdsmnets=self.networks)
        self.networks = _netinfo['networks']
        self.vlans = _netinfo['vlans']
        self.nics = _netinfo['nics']
        self.bondings = _netinfo['bondings']
        self.bridges = _netinfo['bridges']

    def getNetworksAndVlansForIface(self, iface):
        """ Returns tuples of (bridge/network, vlan) connected to  nic/bond """
        return chain(self._getBridgedNetworksAndVlansForIface(iface),
                     self._getBridgelessNetworksAndVlansForIface(iface))

    def _getBridgedNetworksAndVlansForIface(self, iface):
        """ Returns tuples of (bridge, vlan) connected to nic/bond """
        for network, netdict in self.networks.iteritems():
            if netdict['bridged']:
                for interface in netdict['ports']:
                    if iface == interface:
                        yield (network, None)
                    elif interface.startswith(iface + '.'):
                        yield (network, _vlan_id(interface))

    def _getBridgelessNetworksAndVlansForIface(self, iface):
        """ Returns tuples of (network, vlan) connected to nic/bond """
        for network, netdict in self.networks.iteritems():
            if not netdict['bridged']:
                if iface == netdict['iface']:
                    yield (network, None)
                elif netdict['iface'].startswith(iface + '.'):
                    yield (network, _vlan_id(netdict['iface']))

    def getVlansForIface(self, iface):
        for vlandict in six.itervalues(self.vlans):
            if iface == vlandict['iface']:
                yield vlandict['vlanid']

    def getNetworkForIface(self, iface):
        """ Return the network attached to nic/bond """
        for network, netdict in self.networks.iteritems():
            if ('ports' in netdict and iface in netdict['ports'] or
                    iface == netdict['iface']):
                return network

    def getBridgedNetworkForIface(self, iface):
        """ Return all bridged networks attached to nic/bond """
        for bridge, netdict in self.networks.iteritems():
            if netdict['bridged'] and iface in netdict['ports']:
                return bridge

    def getNicsForBonding(self, bond):
        bondAttrs = self.bondings[bond]
        return bondAttrs['slaves']

    def getBondingForNic(self, nic):
        bondings = [b for (b, attrs) in self.bondings.iteritems() if
                    nic in attrs['slaves']]
        if bondings:
            assert len(bondings) == 1, \
                "Unexpected configuration: More than one bonding per nic"
            return bondings[0]
        return None

    def getNicsVlanAndBondingForNetwork(self, network):
        vlan = None
        vlanid = None
        bonding = None
        lnics = []

        if self.networks[network]['bridged']:
            ports = self.networks[network]['ports']
        else:
            ports = []
            interface = self.networks[network]['iface']
            ports.append(interface)

        for port in ports:
            if port in self.vlans:
                assert vlan is None
                nic = _vlan_device(port)
                vlanid = _vlan_id(port)
                vlan = port  # vlan devices can have an arbitrary name
                assert self.vlans[port]['iface'] == nic
                port = nic
            if port in self.bondings:
                assert bonding is None
                bonding = port
                lnics += self.bondings[bonding]['slaves']
            elif port in self.nics:
                lnics.append(port)

        return lnics, vlan, vlanid, bonding

    def ifaceUsers(self, iface):
        "Returns a list of entities using the interface"
        users = set()
        for n, ndict in self.networks.iteritems():
            if ndict['bridged'] and iface in ndict['ports']:
                users.add(n)
            elif not ndict['bridged'] and iface == ndict['iface']:
                users.add(n)
        for b, bdict in self.bondings.iteritems():
            if iface in bdict['slaves']:
                users.add(b)
        for v, vdict in self.vlans.iteritems():
            if iface == vdict['iface']:
                users.add(v)
        return users


def ifaceUsed(iface):
    """Lightweight implementation of bool(Netinfo.ifaceUsers()) that does not
    require a NetInfo object."""
    if os.path.exists(os.path.join(NET_PATH, iface, 'brport')):  # Is it a port
        return True
    for linkDict in nl_link.iter_links():
        if linkDict['name'] == iface and 'master' in linkDict:  # Is it a slave
            return True
        if linkDict.get('device') == iface and linkDict.get('type') == 'vlan':
            return True  # it backs a VLAN
    for name, info in networks().iteritems():
        if info.get('iface') == iface:
            return True
    return False


def vlanDevsForIface(iface):
    for linkDict in nl_link.iter_links():
        if linkDict.get('device') == iface:
            yield linkDict['name']
