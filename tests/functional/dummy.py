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
import random

from nose.plugins.skip import SkipTest

from vdsm.ipwrapper import linkAdd, linkDel, addrAdd, linkSet, IPRoute2Error


def create(prefix='dummy_'):
    """
    Creates a dummy interface, in a fixed number of attempts (100).
    The dummy interface created has a pseudo-random name (e.g. dummy_85
    in the format dummy_Number). Assumes root privileges.
    """

    for i in random.sample(range(100), 100):
        dummy_name = '%s%s' % (prefix, i)
        try:
            linkAdd(dummy_name, linkType='dummy')
        except IPRoute2Error:
            pass
        else:
            return dummy_name

    raise SkipTest('Failed to load a dummy interface')


def remove(dummyName):
    """
    Removes dummy interface dummyName. Assumes root privileges.
    """

    try:
        linkDel(dummyName)
    except IPRoute2Error as e:
        raise SkipTest("Unable to delete dummy interface %s because %s" %
                       (dummyName, e))


def setIP(dummyName, ipaddr, netmask):
    try:
        addrAdd(dummyName, ipaddr, netmask)
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
