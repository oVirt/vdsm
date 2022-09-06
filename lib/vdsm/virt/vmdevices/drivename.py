# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division


from vdsm.common import base26


_DEVNAMES = {
    'ide': 'hd',
    'scsi': 'sd',
    'virtio': 'vd',
    'fdc': 'fd',
    'sata': 'sd',
}


_DEVIFACES = {
    'hd': 'ide',
    'sd': 'scsi',  # SATA will be alias for SCSI
    'vd': 'virtio',
    'fd': 'fdc',
}


def make(interface, index):
    devindex = base26.encode(index)
    return _DEVNAMES.get(interface, 'hd') + devindex


def split(devname):
    prefix = devname[:2]
    if prefix not in _DEVIFACES:
        raise ValueError('Unrecognized device name: %s', devname)
    return _DEVIFACES[prefix], base26.decode(devname[2:])
