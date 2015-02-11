#
# Copyright 2013 Red Hat, Inc.
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
from contextlib import contextmanager

from nose.plugins.skip import SkipTest

from vdsm.ipwrapper import linkAdd, linkDel, addrAdd, linkSet, IPRoute2Error
from vdsm.utils import random_iface_name


def create(prefix='dummy_', max_length=11):
    """
    Create a dummy interface with a pseudo-random suffix, e.g. dummy_ilXaYiSn7.
    Limit the name to 11 characters to make room for VLAN IDs.
    This assumes root privileges.
    """
    dummy_name = random_iface_name(prefix, max_length)
    try:
        linkAdd(dummy_name, linkType='dummy')
    except IPRoute2Error as e:
        raise SkipTest('Failed to create a dummy interface %s: %s' %
                       (dummy_name, e))
    else:
        return dummy_name


def remove(dummy_name):
    """
    Remove the dummy interface. This assumes root privileges.
    """

    try:
        linkDel(dummy_name)
    except IPRoute2Error as e:
        raise SkipTest("Unable to delete the dummy interface %s: %s" %
                       (dummy_name, e))


@contextmanager
def device(prefix='dummy_', max_length=11):
    dummy_name = create(prefix, max_length)
    try:
        yield dummy_name
    finally:
        remove(dummy_name)


def setIP(dummy_name, ipaddr, netmask, family=4):
    try:
        addrAdd(dummy_name, ipaddr, netmask, family)
    except IPRoute2Error as e:
        message = ('Failed to add the IPv%s address %s/%s to device %s: %s'
                   % (family, ipaddr, netmask, dummy_name, e))
        if family == 6:
            message += ('; NetworkManager may have set the sysctl disable_ipv6'
                        ' flag on the device, please see e.g. RH BZ #1102064')
        raise SkipTest(message)


def setLinkUp(dummy_name):
    _setLinkState(dummy_name, 'up')


def setLinkDown(dummy_name):
    _setLinkState(dummy_name, 'down')


def _setLinkState(dummy_name, state):
    try:
        linkSet(dummy_name, [state])
    except IPRoute2Error:
        raise SkipTest('Failed to bring %s to state %s' % (dummy_name, state))
