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

from vdsm.ipwrapper import linkAdd, IPRoute2Error, linkDel
from vdsm.utils import random_iface_name


@contextmanager
def pair(prefix='veth_', max_length=15):
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
