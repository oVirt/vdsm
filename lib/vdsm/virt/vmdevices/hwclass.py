# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division


DISK = 'disk'
NIC = 'interface'
GRAPHICS = 'graphics'
HOSTDEV = 'hostdev'
LEASE = 'lease'


# libvirt is not giving back aliases
WITHOUT_ALIAS = GRAPHICS

# devices that needs updates per-host basis from Vdsm
TO_REFRESH = (
    DISK,  # needed because of local preparation, localdisk hook
    NIC,  # needed by many network-related hooks (vmfex, ovn)
)

HOTPLUGGABLE = (DISK, NIC, LEASE,)
