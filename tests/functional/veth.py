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

import dummy
from vdsm.ipwrapper import linkAdd, IPRoute2Error


def create():
    """
    Creates a veth interface with a pseudo-random name for both endpoints (e.g.
    veth_85 and veth_31 in the format veth_Number). Assumes root privileges.
    """

    deviceNumbers = random.sample(range(100), 2)
    leftPoint = 'veth_%s' % deviceNumbers[0]
    rightPoint = 'veth_%s' % deviceNumbers[1]
    try:
        linkAdd(leftPoint, linkType='veth', args=('peer', 'name', rightPoint))
    except IPRoute2Error:
        pass
    else:
        return (leftPoint, rightPoint)

    raise SkipTest('Failed to create a veth interface')


# the pair device gets removed automatically
remove = dummy.remove
remove


setIP = dummy.setIP
setIP


setLinkUp = dummy.setLinkUp
setLinkUp


setLinkDown = dummy.setLinkDown
setLinkDown
