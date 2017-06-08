# Copyright 2015-2016 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from __future__ import absolute_import
import errno
import fcntl
import functools
import json
import os
import shutil
import signal
import struct
import time
from contextlib import contextmanager
from multiprocessing import Process

from nose.plugins.skip import SkipTest

from vdsm.constants import EXT_BRCTL, EXT_TC
from vdsm import cpuarch
from vdsm.network.ip import dhclient
from vdsm.network.ipwrapper import (
    addrAdd, linkSet, linkAdd, linkDel, IPRoute2Error, netns_add, netns_delete,
    netns_exec)
from vdsm.network.link import iface as linkiface, bond as linkbond
from vdsm.network.netlink import monitor
from vdsm.commands import execCmd
from vdsm.utils import CommandPath, memoized, random_iface_name

from . import dhcp
from . import firewall


ALTERNATIVE_BONDING_DEFAULTS = os.path.join(os.path.dirname(__file__),
                                            'static',
                                            'bonding-defaults.json')
ALTERNATIVE_BONDING_NAME2NUMERIC = os.path.join(os.path.dirname(__file__),
                                                'static',
                                                'bonding-name2numeric.json')
EXT_IP = "/sbin/ip"
_IPERF3_BINARY = CommandPath('iperf3', '/usr/bin/iperf3')


class ExecError(RuntimeError):
    def __init__(self, msg, out, err):
        super(ExecError, self).__init__(msg)
        self.out = out
        self.err = err


def check_call(cmd):
    rc, out, err = execCmd(cmd, raw=True)
    if rc != 0:
        raise ExecError(
            'Command %s returned non-zero exit status %s.' % (cmd, rc),
            out, err)


class Interface(object):

    def __init__(self, prefix='vdsm-', max_length=11):
        self.devName = random_iface_name(prefix, max_length)

    def up(self):
        linkSet(self.devName, ['up'])

    def _down(self):
        with monitor.Monitor(groups=('link',), timeout=2) as mon:
            linkSet(self.devName, ['down'])
            for event in mon:
                if (event.get('name') == self.devName and
                        event.get('state') == 'down'):
                    return

    def __repr__(self):
        return "<{0} {1!r}>".format(self.__class__.__name__, self.devName)


class Bridge(Interface):

    def addDevice(self):
        linkAdd(self.devName, 'bridge')
        self.up()

    def delDevice(self):
        self._down()
        linkDel(self.devName)

    def addIf(self, dev):
        linkSet(dev, ['master', self.devName])


@contextmanager
def bridge_device():
    bridge = Bridge()
    bridge.addDevice()
    try:
        yield bridge
    finally:
        bridge.delDevice()


class Vlan(Interface):
    def __init__(self, backing_device_name, tag):
        self.tag = tag
        self.backing_device_name = backing_device_name
        super(Vlan, self).__init__(prefix='{}.{}'.format(
            backing_device_name, tag))

    def addDevice(self):
        linkAdd(self.devName, 'vlan', link=self.backing_device_name,
                args=['id', str(self.tag)])
        self.up()

    def delDevice(self):
        self._down()
        linkDel(self.devName)


@contextmanager
def vlan_device(link, tag=16):
    vlan = Vlan(link, tag)
    vlan.addDevice()
    try:
        yield vlan
    finally:
        try:
            vlan.delDevice()
        except IPRoute2Error:
            # if the underlying device was removed beforehand, the vlan device
            # would be gone by now.
            pass


@contextmanager
def network_namespace(name):
    netns_add(name)
    try:
        yield name
    finally:
        netns_delete(name)


def _listenOnDevice(fd, icmp):
    while True:
        packet = os.read(fd, 2048)
        # check if it is an IP packet
        if (packet[12:14] == chr(0x08) + chr(0x00)):
            if packet == icmp:
                return


class Tap(Interface):

    _IFF_TAP = 0x0002
    _IFF_NO_PI = 0x1000
    arch = cpuarch.real()
    if arch == cpuarch.X86_64:
        _TUNSETIFF = 0x400454ca
    elif cpuarch.is_ppc(arch):
        _TUNSETIFF = 0x800454ca
    else:
        raise SkipTest("Unsupported Architecture %s" % arch)

    _deviceListener = None

    def addDevice(self):
        self._cloneDevice = open('/dev/net/tun', 'r+b')
        ifr = struct.pack('16sH', self.devName, self._IFF_TAP |
                          self._IFF_NO_PI)
        fcntl.ioctl(self._cloneDevice, self._TUNSETIFF, ifr)
        self.up()

    def delDevice(self):
        self._down()
        self._cloneDevice.close()

    def startListener(self, icmp):
        self._deviceListener = Process(target=_listenOnDevice,
                                       args=(self._cloneDevice.fileno(), icmp))
        self._deviceListener.start()

    def isListenerAlive(self):
        if self._deviceListener:
            return self._deviceListener.is_alive()
        else:
            return False

    def stopListener(self):
        if self._deviceListener:
            os.kill(self._deviceListener.pid, signal.SIGKILL)
            self._deviceListener.join()

    def writeToDevice(self, icmp):
        os.write(self._cloneDevice.fileno(), icmp)


class Dummy(Interface):
    """
    Create a dummy interface with a pseudo-random suffix, e.g. dummy_ilXaYiSn7.
    Limit the name to 11 characters to make room for VLAN IDs. This assumes
    root privileges.
    """

    def __init__(self, prefix='dummy_', max_length=11):
        super(Dummy, self).__init__(prefix, max_length)

    def create(self):
        try:
            linkAdd(self.devName, linkType='dummy')
        except IPRoute2Error as e:
            raise SkipTest('Failed to create a dummy interface %s: %s' %
                           (self.devName, e))
        else:
            return self.devName

    def remove(self):
        """
        Remove the dummy interface. This assumes root privileges.
        """
        try:
            linkDel(self.devName)
        except IPRoute2Error as e:
            raise SkipTest("Unable to delete the dummy interface %s: %s" %
                           (self.devName, e))

    def set_ip(self, ipaddr, netmask, family=4):
        try:
            addrAdd(self.devName, ipaddr, netmask, family)
        except IPRoute2Error as e:
            message = ('Failed to add the IPv%s address %s/%s to device %s: %s'
                       % (family, ipaddr, netmask, self.devName, e))
            if family == 6:
                message += ("; NetworkManager may have set the sysctl "
                            "disable_ipv6 flag on the device, please see e.g. "
                            "RH BZ #1102064")
            raise SkipTest(message)


class IperfServer(object):
    """Starts iperf as an async process"""

    def __init__(self, host, network_ns=None):
        """host: the IP address for the server to listen on.
        network_ns: an optional network namespace for the server to run in.
        """
        self._bind_to = host
        self._net_ns = network_ns
        self._pid = None

    def start(self):
        cmd = [_IPERF3_BINARY.cmd, '--server', '--bind', self._bind_to]
        if self._net_ns is not None:
            p = netns_exec(self._net_ns, cmd)
        else:
            p = execCmd(cmd, sync=False)
        self._pid = p.pid

    def stop(self):
        os.kill(self._pid, signal.SIGTERM)


class IperfClient(object):
    def __init__(self, server_ip, bind_to, test_time, threads=1):
        """The client generates a machine readable json output that is set in
        _raw_output upon completion, and can be read using the 'out' property.
        server_ip: the ip of the corresponding iperf server
        bind_to: IP address of the client
        test_time: in seconds
        """
        self._server_ip = server_ip
        self._bind_to = bind_to
        self._test_time = test_time
        self._threads = threads
        self._raw_output = None

    def start(self):
        cmd = [_IPERF3_BINARY.cmd, '--client', self._server_ip,
               '--version4',  # only IPv4
               '--time', str(self._test_time), '--parallel',
               str(self._threads), '--bind', self._bind_to,
               '--zerocopy',  # use less cpu
               '--json']
        rc, self._raw_output, err = execCmd(cmd)
        if rc == 1 and 'No route to host' in self.out['error']:
            # it seems that it takes some time for the routes to get updated
            # on the os so that we don't get this error, hence the horrific
            # sleep here.
            # TODO: Investigate, understand, and remove this sleep.
            time.sleep(3)
            rc, self._raw_output, err = execCmd(cmd)
        if rc:
            raise Exception('iperf3 client failed: cmd=%s, rc=%s, out=%s, '
                            'err=%s' % (' '.join(cmd), rc, self._raw_output,
                                        err))

    @property
    def out(self):
        return json.loads(' '.join(self._raw_output))


@contextmanager
def dummy_device(prefix='dummy_', max_length=11):
    dummy_interface = Dummy(prefix, max_length)
    dummy_name = dummy_interface.create()
    try:
        linkiface.up(dummy_name)
        yield dummy_name
    finally:
        dummy_interface.remove()


@contextmanager
def dummy_devices(amount, prefix='dummy_', max_length=11):
    dummy_interfaces = [Dummy(prefix, max_length) for _ in range(amount)]
    created = []
    try:
        for iface in dummy_interfaces:
            iface.create()
            created.append(iface)
        yield [iface.devName for iface in created]
    finally:
        for iface in created:
            iface.remove()


@contextmanager
def bond_device(slaves, prefix='bond', max_length=11):
    check_sysfs_bond_permission()
    name = random_iface_name(prefix, max_length)
    with linkbond.Bond(name, slaves) as bond:
        bond.create()
        yield bond.master
    bond.destroy()


@contextmanager
def veth_pair(prefix='veth_', max_length=15):
    """
    Yield a pair of veth devices. This assumes root privileges (currently
    required by all tests anyway).

    Both sides of the pair have a pseudo-random suffix (e.g. veth_m6Lz7uMK9c).
    """
    left_side = random_iface_name(prefix, max_length)
    right_side = random_iface_name(prefix, max_length)
    try:
        linkAdd(left_side, linkType='veth',
                args=('peer', 'name', right_side))
    except IPRoute2Error as e:
        raise SkipTest('Failed to create a veth pair: %s', e)
    try:
        yield left_side, right_side
    finally:
        # the peer device is removed by the kernel
        linkDel(left_side)


def check_brctl():
    try:
        execCmd([EXT_BRCTL, "show"])
    except OSError as e:
        if e.errno == errno.ENOENT:
            raise SkipTest("Cannot run %r: %s\nDo you have bridge-utils "
                           "installed?" % (EXT_BRCTL, e))
        raise


def requires_brctl(f):
    @functools.wraps(f)
    def wrapper(*a, **kw):
        check_brctl()
        return f(*a, **kw)
    return wrapper


def check_tc():
    dev = Bridge()
    dev.addDevice()
    try:
        check_call([EXT_TC, 'qdisc', 'add', 'dev', dev.devName, 'ingress'])
    except ExecError as e:
        raise SkipTest("%r has failed: %s\nDo you have Traffic Control kernel "
                       "modules installed?" % (EXT_TC, e.err))
    finally:
        dev.delDevice()


def requires_tc(f):
    @functools.wraps(f)
    def wrapper(*a, **kw):
        check_tc()
        return f(*a, **kw)
    return wrapper


def _check_iperf():
    if not os.access(_IPERF3_BINARY.cmd, os.X_OK):
        raise SkipTest("Cannot run %r: %s\nDo you have iperf3 installed?"
                       % _IPERF3_BINARY._cmd)


def requires_iperf3(f):
    @functools.wraps(f)
    def wrapper(*a, **kw):
        _check_iperf()
        return f(*a, **kw)
    return wrapper


@contextmanager
def dnsmasq_run(interface, dhcp_range_from=None, dhcp_range_to=None,
                dhcpv6_range_from=None, dhcpv6_range_to=None, router=None,
                ipv6_slaac_prefix=None):
    """Manages the life cycle of dnsmasq as a DHCP/RA server."""
    server = dhcp.Dnsmasq()
    server.start(interface, dhcp_range_from, dhcp_range_to,
                 dhcpv6_range_from, dhcpv6_range_to, router,
                 ipv6_slaac_prefix)

    with firewall.allow_dhcp(interface):
        try:
            yield
        finally:
            server.stop()


def requires_tun(f):
    @functools.wraps(f)
    def wrapper(*a, **kw):
        if not os.path.exists("/dev/net/tun"):
            raise SkipTest("This test requires tun device")
        return f(*a, **kw)
    return wrapper


@contextmanager
def wait_for_ipv6(iface, wait_for_scopes=None):
    """Wait for iface to get their IPv6 addresses with netlink Monitor"""
    logevents = []
    if not wait_for_scopes:
        wait_for_scopes = ['global', 'link']
    try:
        with monitor.Monitor(groups=('ipv6-ifaddr',), timeout=20) as mon:
            yield
            for event in mon:
                logevents.append(event)
                dev_name = event.get('label')
                if (dev_name == iface and
                        event.get('event') == 'new_addr' and
                        event.get('scope') in wait_for_scopes):

                    wait_for_scopes.remove(event.get('scope'))
                    if not wait_for_scopes:
                        return

    except monitor.MonitorError as e:
        if e[0] == monitor.E_TIMEOUT:
            raise Exception(
                'IPv6 addresses has not been caught within 20sec.\n'
                'Event log: {}\n'.format(logevents))
        else:
            raise


@contextmanager
def dhclient_run(iface):
    dhclient.run(iface, blocking_dhcp=True)
    try:
        yield
    finally:
        dhclient.stop(iface)


@contextmanager
def restore_resolv_conf():
    RESOLV_CONF = '/etc/resolv.conf'
    RESOLV_CONF_BACKUP = '/etc/resolv.conf.test-backup'
    shutil.copy2(RESOLV_CONF, RESOLV_CONF_BACKUP)
    try:
        yield
    finally:
        shutil.copy2(RESOLV_CONF_BACKUP, RESOLV_CONF)


def check_sysfs_bond_permission():
    if not _has_sysfs_bond_permission():
        raise SkipTest('This test requires sysfs bond write access')


@memoized
def _has_sysfs_bond_permission():
    BondSysFS = linkbond.sysfs_driver.BondSysFS
    bond = BondSysFS(random_iface_name('check_', max_length=11))
    try:
        bond.create()
        bond.destroy()
    except IOError:
        return False
    return True
