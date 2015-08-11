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


class Dummy(object):

    def __init__(self, prefix='dummy_', max_length=11):
        self.devName = random_iface_name(prefix, max_length)

    def create(self):
        """
        Create a dummy interface with a pseudo-random suffix, e.g.
        dummy_ilXaYiSn7.
        Limit the name to 11 characters to make room for VLAN IDs.
        This assumes root privileges.
        """
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

    def setLinkUp(self):
        self._setLinkState('up')

    def setLinkDown(self):
        self._setLinkState('down')

    def setIP(self, ipaddr, netmask, family=4):
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

    def _setLinkState(self, state):
        try:
            linkSet(self.devName, [state])
        except IPRoute2Error:
            raise SkipTest('Failed to bring %s to state %s' % (
                self.devName, state))


@contextmanager
def device(prefix='dummy_', max_length=11):
    dummy_interface = Dummy(prefix, max_length)
    dummy_name = dummy_interface.create()
    try:
        yield dummy_name
    finally:
        dummy_interface.remove()
