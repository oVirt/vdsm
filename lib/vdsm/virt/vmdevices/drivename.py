#
# Copyright 2017 Red Hat, Inc.
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
