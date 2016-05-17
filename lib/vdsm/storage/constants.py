#
# Copyright 2010-2016 Red Hat, Inc.
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

from vdsm import qemuimg

STORAGE = "Storage"
SECTOR_SIZE = 512
VG_EXTENT_SIZE_MB = 128

# At the moment this is static and it has been introduced to group all the
# previous implicit references to the block size in FileVolume. In the future
# it will depend on the storage domain.
BLOCK_SIZE = 512
METADATA_SIZE = BLOCK_SIZE

FILE_VOLUME_PERMISSIONS = 0o660
LEASE_FILEEXT = ".lease"

# Temporary volume indicators
TEMP_VOL_FILEEXT = ".volatile"         # Added to FileVolume metadata filenames
TEMP_VOL_LVTAG = "OVIRT_VOL_VOLATILE"  # Tag applied to BlockVolume LVs

# StorageDomain Metadata keys
MDK_POOLS = "POOL_UUID"

# Volume Types
UNKNOWN_VOL = 0
PREALLOCATED_VOL = 1
SPARSE_VOL = 2

# Volume Format
UNKNOWN_FORMAT = 3
COW_FORMAT = 4
RAW_FORMAT = 5

# Volume Role
SHARED_VOL = 6
INTERNAL_VOL = 7
LEAF_VOL = 8

VOL_TYPE = [PREALLOCATED_VOL, SPARSE_VOL]
VOL_FORMAT = [COW_FORMAT, RAW_FORMAT]
VOL_ROLE = [SHARED_VOL, INTERNAL_VOL, LEAF_VOL]

VOLUME_TYPES = {UNKNOWN_VOL: 'UNKNOWN', PREALLOCATED_VOL: 'PREALLOCATED',
                SPARSE_VOL: 'SPARSE',
                UNKNOWN_FORMAT: 'UNKNOWN', COW_FORMAT: 'COW',
                RAW_FORMAT: 'RAW',
                SHARED_VOL: 'SHARED', INTERNAL_VOL: 'INTERNAL',
                LEAF_VOL: 'LEAF'}

ILLEGAL_VOL = "ILLEGAL"
LEGAL_VOL = "LEGAL"
FAKE_VOL = "FAKE"

FMT2STR = {
    COW_FORMAT: qemuimg.FORMAT.QCOW2,
    RAW_FORMAT: qemuimg.FORMAT.RAW,
}

BLANK_UUID = "00000000-0000-0000-0000-000000000000"


def fmt2str(format):
    return FMT2STR[format]


def type2name(volType):
    try:
        return VOLUME_TYPES[volType]
    except IndexError:
        return None


def name2type(name):
    for (k, v) in VOLUME_TYPES.iteritems():
        if v == name.upper():
            return k
    return None
