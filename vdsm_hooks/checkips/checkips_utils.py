#
# Copyright 2008-2016 Red Hat, Inc.
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

import os

CHECKIPV4 = 'checkipv4'
CHECKIPV6 = 'checkipv6'


def touch(net, file_dir):
    file_path = os.path.join(file_dir, net)
    with open(file_path, 'a'):
        os.utime(file_path, None)


def get_ping_addresses(net_attrs):
    ping_addresses = []
    if 'custom' in net_attrs:
        for address_type in (CHECKIPV4, CHECKIPV6):
            if address_type in net_attrs['custom']:
                ping_addresses.append(
                    (
                        address_type, net_attrs['custom'][address_type]
                    )
                )
    return ping_addresses
