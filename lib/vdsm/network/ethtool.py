# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import array
import fcntl
import socket
import struct
from contextlib import closing

from vdsm.network.common import conversion_util

ETHTOOL_GDRVINFO = 0x00000003  # ETHTOOL Get driver info command
SIOCETHTOOL = 0x8946  # Ethtool interface
DRVINFO_FORMAT = '= I 32s 32s 32s 32s 32s 12s 5I'
IFREQ_FORMAT = '16sPi'  # device_name, buffer_pointer, buffer_len


def driver_name(device_name):
    """Returns the driver used by a device.

    Throws IOError ENODEV for non existing devices.
    Throws IOError EOPNOTSUPP for non supported devices, i.g., loopback.
    """
    encoded_name = conversion_util.to_binary(device_name)

    buff = array.array('b', b'\0' * struct.calcsize(DRVINFO_FORMAT))
    cmds = struct.pack('= I', ETHTOOL_GDRVINFO)
    buff[0 : len(cmds)] = array.array('b', cmds)  # noqa: E203
    data = struct.pack(IFREQ_FORMAT, encoded_name, *buff.buffer_info())

    with closing(socket.socket(socket.AF_INET, socket.SOCK_DGRAM)) as sock:
        fcntl.ioctl(sock, SIOCETHTOOL, data)

    (
        cmds,
        driver,
        version,
        fw_version,
        businfo,
        _,
        _,
        n_priv_flags,
        n_stats,
        testinfo_len,
        eedump_len,
        regdump_len,
    ) = struct.unpack(DRVINFO_FORMAT, buff)
    driver_str = conversion_util.to_str(driver)
    return driver_str.rstrip('\0')  # C string end with the leftmost null char
