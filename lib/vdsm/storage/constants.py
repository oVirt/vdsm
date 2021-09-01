#
# Copyright 2010-2017 Red Hat, Inc.
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

from vdsm import constants
from vdsm.common.config import config
from vdsm.common.units import MiB
from vdsm.storage import qemuimg

# Stroage pool.
POOL_MASTER_DOMAIN = "mastersd"

# ResourceManager Lock Namespaces
STORAGE = "00_storage"
IMAGE_NAMESPACE = '01_img'
VOLUME_NAMESPACE = '02_vol'
LVM_ACTIVATION_NAMESPACE = '03_lvm'

# These namespace do not need to be registered as they are not used by the
# resource manager, but by sanlock. We only need the namespace to perform
# the lock sorting.
VOLUME_LEASE_NAMESPACE = "04_lease"
EXTERNAL_LEASE_NAMESPACE = "05_external_lease"

VG_EXTENT_SIZE = 128 * MiB
COW_OVERHEAD = 1.1

# The minimal size used to limit internal volume size. This is mainly used
# when calculating volume optimal size.
MIN_CHUNK = 8 * VG_EXTENT_SIZE

# TODO: This constant is useful only file base storage, it should be moved to
# some constant module specific to file based storage once we have such module.
# Specific stat(2) block size as defined in the man page
STAT_BYTES_PER_BLOCK = 512

# Supported block sizes in bytes
BLOCK_SIZE_512 = 512
BLOCK_SIZE_4K = 4096

METADATA_SIZE = BLOCK_SIZE_512

# Vdsm will detect the underlying storage block size if the storage domain
# supports this.
BLOCK_SIZE_AUTO = 0

# This value is not supported as user input, but it may be returned when
# detecting underlying storage block size, meaning that the underlying
# storage does not enforce minimal block size for direct I/O.
BLOCK_SIZE_NONE = 1

# sanlock possible alignment values, that set a lockspace size
# In combination with a block size (see above)
# they set a limit of supported number of hosts:
# - SANLK_RES_ALIGN1M | SANLK_RES_SECTOR512: max_hosts 2000
# - SANLK_RES_ALIGN1M | SANLK_RES_SECTOR4K:  max_hosts 250
# - SANLK_RES_ALIGN2M | SANLK_RES_SECTOR4K:  max_hosts 500
# - SANLK_RES_ALIGN4M | SANLK_RES_SECTOR4K:  max_hosts 1000
# - SANLK_RES_ALIGN8M | SANLK_RES_SECTOR4K:  max_hosts 2000
ALIGNMENT_1M = 1 * MiB
ALIGNMENT_2M = 2 * MiB
ALIGNMENT_4M = 4 * MiB
ALIGNMENT_8M = 8 * MiB

# A maximum number of hosts for both 512B & 4K block sizes.
HOSTS_MAX = 2000
# Block size/alignment mapping to the number of hosts.
HOSTS_512_1M = HOSTS_MAX
HOSTS_4K_1M = 250
HOSTS_4K_2M = 500
HOSTS_4K_4M = 1000
HOSTS_4K_8M = HOSTS_MAX

FILE_VOLUME_PERMISSIONS = 0o660
LEASE_FILEEXT = ".lease"

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

DATA_DISKTYPE = "DATA"  # Data disk
ISOF_DISKTYPE = "ISOF"  # ISO disk
MEMD_DISKTYPE = "MEMD"  # Memory dump disk
MEMM_DISKTYPE = "MEMM"  # Memory metadata disk
OVFS_DISKTYPE = "OVFS"  # OVF disk
HEVD_DISKTYPE = "HEVD"  # Hosted Engine VM disk
HESD_DISKTYPE = "HESD"  # Hosted Engine Sanlock disk
HEMD_DISKTYPE = "HEMD"  # Hosted Engine metadata disk
HECI_DISKTYPE = "HECI"  # Hosted Engine configuration image
SCRD_DISKTYPE = "SCRD"  # VM backup scratch disk

# Engine < 4.2, or engine with compatibility level < 4.2 created data disks
# with this disk type.
LEGACY_DATA_DISKTYPE = "2"

# virt-v2v -o rhv/vdsm created data disks with wrong disk type
LEGACY_V2V_DATA_DISKTYPE = "1"

VOL_DISKTYPE = frozenset([
    DATA_DISKTYPE,
    ISOF_DISKTYPE,
    MEMD_DISKTYPE,
    MEMM_DISKTYPE,
    OVFS_DISKTYPE,
    HEVD_DISKTYPE,
    HESD_DISKTYPE,
    HEMD_DISKTYPE,
    HECI_DISKTYPE,
    SCRD_DISKTYPE,
    LEGACY_DATA_DISKTYPE,
    LEGACY_V2V_DATA_DISKTYPE,
])

_TYPE2NAME = {
    UNKNOWN_VOL: 'UNKNOWN',
    PREALLOCATED_VOL: 'PREALLOCATED',
    SPARSE_VOL: 'SPARSE',
    UNKNOWN_FORMAT: 'UNKNOWN',
    COW_FORMAT: 'COW',
    RAW_FORMAT: 'RAW',
    SHARED_VOL: 'SHARED',
    INTERNAL_VOL: 'INTERNAL',
    LEAF_VOL: 'LEAF'
}

_NAME2TYPE = {v: k for k, v in _TYPE2NAME.items()}

ILLEGAL_VOL = "ILLEGAL"
LEGAL_VOL = "LEGAL"
FAKE_VOL = "FAKE"

# Volume info statuses
VOL_STATUS_OK = "OK"
VOL_STATUS_INIT = "INIT"
VOL_STATUS_INVALID = "INVALID"
VOL_STATUS_REMOVED = "REMOVED"

_FMT2STR = {
    COW_FORMAT: qemuimg.FORMAT.QCOW2,
    RAW_FORMAT: qemuimg.FORMAT.RAW,
}

_STR2FMT = {v: k for k, v in _FMT2STR.items()}

BLANK_UUID = "00000000-0000-0000-0000-000000000000"

UUID_GLOB_PATTERN = '*-*-*-*-*'

REMOVED_IMAGE_PREFIX = "_remove_me_"
ZEROED_IMAGE_PREFIX = REMOVED_IMAGE_PREFIX + "ZERO_"


def fmt2str(fmt):
    return _FMT2STR[fmt]


def str2fmt(s):
    return _STR2FMT[s]


def type2name(volType):
    return _TYPE2NAME[volType]


def name2type(name):
    return _NAME2TYPE[name.upper()]


# Volume meta data fields
CAPACITY = "CAP"  # Added in 4.3
TYPE = "TYPE"
FORMAT = "FORMAT"
DISKTYPE = "DISKTYPE"
VOLTYPE = "VOLTYPE"
PUUID = "PUUID"
DOMAIN = "DOMAIN"
CTIME = "CTIME"
IMAGE = "IMAGE"
DESCRIPTION = "DESCRIPTION"
LEGALITY = "LEGALITY"
MTIME = "MTIME"
GENERATION = "GEN"  # Added in 4.1

# In block storage, metadata size is limited to BLOCK_SIZE (512), to
# ensure that metadata is written atomically. This is big enough for the
# actual metadata, but may not be big enough for the description field.
# Since a disk may be created on file storage, and moved to block
# storage, the metadata size must be limited on all types of storage.
#
# The description field is limited to 500 characters in the engine side.
# Since ovirt 3.5, the description field is using JSON format, keeping
# both alias and description. In OVF_STORE disks, the description field
# holds additional data such as content size and date.
#
# Here is the worst case metadata format:
#
# CTIME=1440935038                            # int(time.time())
# DESCRIPTION=                                # text|JSON
# DISKTYPE=OVFS                               # 4 bytes string in v>=4.2
# DOMAIN=75f8a1bb-4504-4314-91ca-d9365a30692b # uuid
# FORMAT=COW                                  # RAW|COW
# IMAGE=75f8a1bb-4504-4314-91ca-d9365a30692b  # uuid
# LEGALITY=ILLEGAL                            # ILLEGAL|LEGAL|FAKE
# MTIME=0                                     # always 0 (v4 only)
# PUUID=75f8a1bb-4504-4314-91ca-d9365a30692b  # uuid
# SIZE=18014398509481983                      # size in blocks (<=v4)
# CAP=9223372036854775808                     # capacity in bytes (>=v5)
# TYPE=PREALLOCATED                           # PREALLOCATED|UNKNOWN|SPARSE
# VOLTYPE=INTERNAL                            # INTERNAL|SHARED|LEAF
# GEN=999                                     # int
# EOF
#
# For more info why this is the worst possible case, see
# tests/storage/volume_metadata_test.py.
#
# On V4 This content requires up to 276 bytes, leaving 236 bytes for the
# description.
#
# On V5 this content requires 270 bytes, leaving 242 bytes for the description
# field.
#
# OVF_STORE JSON description format needs up to 175 bytes.
#
# We use a limit of 210 bytes for the description field, leaving couple
# of bytes for unexpected future changes. This should good enough for
# ascii values, but limit non-ascii values, which are encoded by engine
# using 4 bytes per character.
DESCRIPTION_SIZE = 210

# The GEN metadata key may not exist in volume metadata since it has been added
# after many volumes had been created on storage.  When missing, we default its
# value to 0 which will be written back to the metadata during the next change.
# Generation is a monotonically increasing integer that will wrap back to 0
# after reaching its maximum value.
DEFAULT_GENERATION = 0
MAX_GENERATION = 999  # Since this is represented in ASCII, limit to 3 places

# Block volume metadata tags
TAG_PREFIX_MD = "MD_"
TAG_PREFIX_IMAGE = "IU_"
TAG_PREFIX_PARENT = "PU_"
TAG_VOL_UNINIT = "OVIRT_VOL_INITIALIZING"
VOLUME_TAGS = [TAG_PREFIX_PARENT,
               TAG_PREFIX_IMAGE,
               TAG_PREFIX_MD]

SUPPORTED_BLOCKSIZE = (512,)

# This is the domain version translation list
# DO NOT CHANGE OLD VALUES ONLY APPEND
DOMAIN_VERSIONS = (0, 2, 3, 4, 5)

# This contains the domains versions that this VDSM
# accepts currently its all of the version but in the
# future we might slice it (eg. tuple(DOMAIN_VERSION[1:]))
SUPPORTED_DOMAIN_VERSIONS = DOMAIN_VERSIONS

P_VDSM_LIB = os.path.join(constants.P_VDSM_LIB, 'storage/')
P_VDSM_STORAGE = os.path.join(constants.P_VDSM_RUN, 'storage/')

# Storage repository
DOMAIN_MNT_POINT = 'mnt'
REPO_DATA_CENTER = config.get('irs', 'repository')
REPO_MOUNT_DIR = os.path.join(REPO_DATA_CENTER, DOMAIN_MNT_POINT)

# TODO: Consider totally removing it in the future.
# Global process pool name.
GLOBAL_OOP = 'Global'

# Job statuses for external lease metadata
JOB_STATUS_PENDING = "PENDING"
JOB_STATUS_FAILED = "FAILED"
JOB_STATUS_SUCCEEDED = "SUCCEEDED"
JOB_STATUS_FENCED = "FENCED"
