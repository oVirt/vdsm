# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

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
