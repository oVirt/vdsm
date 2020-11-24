# Copyright 2015-2020 Red Hat, Inc.
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
from __future__ import division
import fcntl
import os
import shutil
import signal
import struct
import time
from contextlib import contextmanager
from multiprocessing import Process
import logging

import pytest

from vdsm.common import cpuarch
from vdsm.network import cmd
from vdsm.network.ip import address
from vdsm.network.ipwrapper import (
    addrAdd,
    linkSet,
    linkAdd,
    linkDel,
    IPRoute2Error,
)
from vdsm.network.link import bond as linkbond
from vdsm.network.link.iface import random_iface_name
from vdsm.network.lldpad import lldptool
from vdsm.network.netinfo import routes
from vdsm.network.netlink import monitor
from vdsm.common.cache import memoized
from vdsm.common.proc import pgrep

from . import dhcp
from . import firewall


class IpFamily(object):
    IPv4 = 4
    IPv6 = 6


class Interface(object):
    def __init__(self, prefix='vdsm-', max_length=11):
        self.dev_name = random_iface_name(prefix, max_length)

    @staticmethod
    def from_existing_dev_name(dev_name):
        return Interface(dev_name, len(dev_name))

    def add_ip(self, ip_addr, prefix_len, family):
        try:
            addrAdd(self.dev_name, ip_addr, prefix_len, family)
        except IPRoute2Error as e:
            message = (
                f'Failed to add the IPv{family} address {family}/{prefix_len}'
                f'to device {self.dev_name}: {e}'
            )
            pytest.skip(message)

    def up(self):
        linkSet(self.dev_name, ['up'])

    def down(self):
        with monitor.object_monitor(groups=('link',), timeout=2) as mon:
            linkSet(self.dev_name, ['down'])
            for event in mon:
                if (
                    event.get('name') == self.dev_name
                    and event.get('state') == 'down'
                ):
                    return

    def set_managed(self):
        cmd.exec_sync(['nmcli', 'dev', 'set', self.dev_name, 'managed', 'yes'])

    def remove(self):
        linkDel(self.dev_name)
        cmd.exec_sync(['nmcli', 'con', 'del', self.dev_name])

    def __repr__(self):
        return "<{0} {1!r}>".format(self.__class__.__name__, self.dev_name)


class Vlan(Interface):
    def __init__(self, backing_device_name, tag):
        self.tag = tag
        self.backing_device_name = backing_device_name
        vlan_name = f'{backing_device_name}.{tag}'
        super(Vlan, self).__init__(vlan_name, len(vlan_name))

    def create(self):
        linkAdd(
            self.dev_name,
            'vlan',
            link=self.backing_device_name,
            args=['id', str(self.tag)],
        )
        self.up()
        self.set_managed()


@contextmanager
def vlan_device(link, tag=16):
    vlan = Vlan(link, tag)
    vlan.create()
    try:
        yield vlan.dev_name
    finally:
        try:
            vlan.remove()
        except IPRoute2Error:
            # if the underlying device was removed beforehand, the vlan device
            # would be gone by now.
            pass


def _listen_on_device(fd, icmp):
    while True:
        packet = os.read(fd, 2048)
        # check if it is an IP packet
        if packet[12:14] == b'\x08\x00':
            if packet == icmp:
                return


class Tap(Interface):

    _IFF_TAP = 0x0002
    _IFF_NO_PI = 0x1000
    arch = cpuarch.real()
    if arch in (cpuarch.X86_64, cpuarch.S390X):
        _TUNSETIFF = 0x400454CA
    elif cpuarch.is_ppc(arch):
        _TUNSETIFF = 0x800454CA
    else:
        pytest.skip("Unsupported Architecture %s" % arch)

    _device_listener = None

    def create(self):
        self._clone_device = open('/dev/net/tun', 'r+b', buffering=0)
        ifr = struct.pack(
            b'16sH', self.dev_name.encode(), self._IFF_TAP | self._IFF_NO_PI
        )
        fcntl.ioctl(self._clone_device, self._TUNSETIFF, ifr)
        self.set_managed()
        self.up()

    def remove(self):
        self.down()
        self._clone_device.close()

    def start_listener(self, icmp):
        self._device_listener = Process(
            target=_listen_on_device, args=(self._clone_device.fileno(), icmp)
        )
        self._device_listener.start()

    def is_listener_alive(self):
        if self._device_listener:
            return self._device_listener.is_alive()
        else:
            return False

    def stop_listener(self):
        if self._device_listener:
            os.kill(self._device_listener.pid, signal.SIGKILL)
            self._device_listener.join()

    def write_to_device(self, icmp):
        os.write(self._clone_device.fileno(), icmp)


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
            linkAdd(self.dev_name, linkType='dummy')
            self.set_managed()
            self.up()
        except IPRoute2Error as e:
            pytest.skip(
                f'Failed to create a dummy interface {self.dev_name}: {e}'
            )
        else:
            return self.dev_name


class Bridge(Interface):
    def create(self):
        linkAdd(self.dev_name, 'bridge')
        self.set_managed()
        self.up()

    def add_port(self, dev):
        linkSet(dev, ['master', self.dev_name])


class VethPair(object):
    def __init__(self, prefix='veth_', max_length=15):
        self.left_side = Interface(prefix, max_length)
        self.right_side = Interface(prefix, max_length)

    def create(self):
        linkAdd(
            self.left_side.dev_name,
            linkType='veth',
            args=('peer', 'name', self.right_side.dev_name),
        )
        self.left_side.set_managed()
        self.right_side.set_managed()
        self.left_side.up()
        self.right_side.up()

    def remove(self):
        self.left_side.remove()
        cmd.exec_sync(['nmcli', 'con', 'del', self.right_side.dev_name])


@contextmanager
def dummy_device(prefix='dummy_', max_length=11):
    dummy_interface = Dummy(prefix, max_length)
    dummy_interface.create()
    try:
        yield dummy_interface.dev_name
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
        yield [iface.dev_name for iface in created]
    finally:
        for iface in created:
            iface.remove()


@contextmanager
def bond_device(slaves=(), prefix='bond_', max_length=11):
    check_sysfs_bond_permission()
    name = random_iface_name(prefix, max_length)
    with linkbond.Bond(name, slaves) as bond:
        bond.create()
        yield bond
    bond.destroy()


@contextmanager
def veth_pair(prefix='veth_', max_length=15):
    """
    Yield a pair of veth devices. This assumes root privileges (currently
    required by all tests anyway).

    Both sides of the pair have a pseudo-random suffix (e.g. veth_m6Lz7uMK9c).
    """
    pair = VethPair(prefix, max_length)
    try:
        pair.create()
    except IPRoute2Error as e:
        pytest.skip('Failed to create a veth pair: %s', e)
    try:
        yield pair.left_side.dev_name, pair.right_side.dev_name
    finally:
        pair.remove()


@contextmanager
def enable_lldp_on_ifaces(ifaces, rx_only):
    for interface in ifaces:
        lldptool.enable_lldp_on_iface(interface, rx_only)
    # We must give a chance for the LLDP messages to be received.
    time.sleep(2)
    try:
        yield
    finally:
        for interface in ifaces:
            lldptool.disable_lldp_on_iface(interface)


@contextmanager
def bridge_device():
    bridge = Bridge()
    bridge.create()
    try:
        yield bridge.dev_name
    finally:
        bridge.remove()


def nm_is_running():
    return len(pgrep('NetworkManager')) > 0


@contextmanager
def dnsmasq_run(
    interface,
    dhcp_range_from=None,
    dhcp_range_to=None,
    dhcpv6_range_from=None,
    dhcpv6_range_to=None,
    router=None,
    ipv6_slaac_prefix=None,
):
    """Manages the life cycle of dnsmasq as a DHCP/RA server."""
    server = dhcp.Dnsmasq()
    server.start(
        interface,
        dhcp_range_from,
        dhcp_range_to,
        dhcpv6_range_from,
        dhcpv6_range_to,
        router,
        ipv6_slaac_prefix,
    )

    try:
        with firewall.allow_dhcp(interface):
            try:
                yield
            finally:
                server.stop()
    except firewall.FirewallError as e:
        pytest.skip('Failed to allow DHCP traffic in firewall: %s' % e)


@contextmanager
def wait_for_ipv6(iface, wait_for_scopes=None):
    """Wait for iface to get their IPv6 addresses with netlink Monitor"""
    logevents = []
    if not wait_for_scopes:
        wait_for_scopes = ['global', 'link']
    try:
        with monitor.object_monitor(
            groups=('ipv6-ifaddr',), timeout=20
        ) as mon:
            yield
            for event in mon:
                logevents.append(event)
                dev_name = event.get('label')
                if (
                    dev_name == iface
                    and event.get('event') == 'new_addr'
                    and event.get('scope') in wait_for_scopes
                ):

                    wait_for_scopes.remove(event.get('scope'))
                    if not wait_for_scopes:
                        return

    except monitor.MonitorError as e:
        if e.args[0] == monitor.E_TIMEOUT:
            raise Exception(
                'IPv6 addresses has not been caught within 20sec.\n'
                'Event log: {}\n'.format(logevents)
            )
        else:
            raise


@contextmanager
def dhcp_client_run(iface, family=4):
    cmd.exec_sync(
        [
            'nmcli',
            'con',
            'modify',
            iface,
            'ipv{}.method'.format(family),
            'auto',
        ]
    )
    cmd.exec_sync(['nmcli', 'con', 'up', iface])
    try:
        yield
    finally:
        cmd.exec_sync(
            [
                'nmcli',
                'con',
                'modify',
                iface,
                'ipv{}.method'.format(family),
                'disabled',
            ]
        )
        cmd.exec_sync(['nmcli', 'con', 'up', iface])


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
    if not has_sysfs_bond_permission():
        pytest.skip('This test requires sysfs bond write access')


@contextmanager
def preserve_default_route():
    ipv4_dg_data = routes.getDefaultGateway()
    ipv4_gateway = ipv4_dg_data.via if ipv4_dg_data else None
    ipv4_device = ipv4_dg_data.device if ipv4_dg_data else None

    ipv6_dg_data = routes.ipv6_default_gateway()
    ipv6_gateway = ipv6_dg_data.via if ipv6_dg_data else None
    ipv6_device = ipv6_dg_data.device if ipv6_dg_data else None

    try:
        yield
    finally:
        if ipv4_gateway and not routes.is_default_route(
            ipv4_gateway, routes.get_routes()
        ):
            address.set_default_route(ipv4_gateway, family=4, dev=ipv4_device)
        if ipv6_gateway and not routes.is_ipv6_default_route(ipv6_gateway):
            address.set_default_route(ipv6_gateway, family=6, dev=ipv6_device)


@contextmanager
def running(runnable):
    runnable.start()
    try:
        yield runnable
    finally:
        runnable.stop()


@memoized
def has_sysfs_bond_permission():
    BondSysFS = linkbond.sysfs_driver.BondSysFS
    bond = BondSysFS(random_iface_name('check_', max_length=11))
    try:
        bond.create()
        bond.destroy()
    except IOError:
        return False
    return True


class KernelModule(object):
    SYSFS_MODULE_PATH = '/sys/module'
    CMD_MODPROBE = 'modprobe'

    def __init__(self, name):
        self._name = name

    def exists(self):
        return os.path.exists(
            os.path.join(KernelModule.SYSFS_MODULE_PATH, self._name)
        )

    def load(self):
        if not self.exists():
            ret, out, err = cmd.exec_sync(
                [KernelModule.CMD_MODPROBE, self._name]
            )
            if ret != 0:
                logging.warning(
                    'Unable to load %s module, out=%s, err=%s',
                    self._name,
                    out,
                    err,
                )


def running_on_centos():
    with open('/etc/redhat-release') as f:
        return 'CentOS Linux release' in f.readline()


def running_on_fedora(ver=''):
    with open('/etc/redhat-release') as f:
        return 'Fedora release {}'.format(ver) in f.readline()


def running_on_travis_ci():
    return 'TRAVIS_CI' in os.environ


def running_on_ovirt_ci():
    return 'CI' in os.environ or 'OVIRT_CI' in os.environ


class FakeNotifier:
    def __init__(self):
        self.calls = []

    def notify(self, event_id, params=None):
        self.calls.append((event_id, params))
