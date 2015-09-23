# Copyright 2015 Red Hat, Inc.
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

import errno
import fcntl
import functools
import os
import platform
import signal
import struct
from contextlib import contextmanager
from multiprocessing import Process

from nose.plugins.skip import SkipTest

from vdsm.constants import EXT_BRCTL, EXT_TC
from vdsm.ipwrapper import addrAdd, linkSet, linkAdd, linkDel, IPRoute2Error
from vdsm.netlink import monitor
from vdsm.utils import execCmd, random_iface_name

EXT_IP = "/sbin/ip"


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

    def __str__(self):
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
    arch = platform.machine()
    if arch == 'x86_64':
        _TUNSETIFF = 0x400454ca
    elif arch == 'ppc64':
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


@contextmanager
def dummy_device(prefix='dummy_', max_length=11):
    dummy_interface = Dummy(prefix, max_length)
    dummy_name = dummy_interface.create()
    try:
        yield dummy_name
    finally:
        dummy_interface.remove()


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
        yield left_side, right_side
    except IPRoute2Error:
        raise SkipTest('Failed to create a veth pair.')
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


def requires_tun(f):
    @functools.wraps(f)
    def wrapper(*a, **kw):
        if not os.path.exists("/dev/net/tun"):
            raise SkipTest("This test requires tun device")
        return f(*a, **kw)
    return wrapper
