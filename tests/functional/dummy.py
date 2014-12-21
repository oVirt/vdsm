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
    except IPRoute2Error:
        raise SkipTest('Failed to load a dummy interface')
    else:
        return dummy_name


def remove(dummyName):
    """
    Removes dummy interface dummyName. Assumes root privileges.
    """

    try:
        linkDel(dummyName)
    except IPRoute2Error as e:
        raise SkipTest("Unable to delete dummy interface %s because %s" %
                       (dummyName, e))


def setIP(dummyName, ipaddr, netmask, family=4):
    try:
        addrAdd(dummyName, ipaddr, netmask, family)
    except IPRoute2Error:
        raise SkipTest('Failed to set device ip')


def setLinkUp(dummyName):
    _setLinkState(dummyName, 'up')


def setLinkDown(dummyName):
    _setLinkState(dummyName, 'down')


def _setLinkState(dummyName, state):
    try:
        linkSet(dummyName, [state])
    except IPRoute2Error:
        raise SkipTest('Failed to bring %s to state %s' % (dummyName, state))
