#
# Copyright 2016 Red Hat, Inc.
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

import logging

from vdsm.storage import constants as sc
from vdsm.storage import qemuimg

log = logging.getLogger('storage.workarounds')

# Size in blocks of the conf file generated during RAM snapshot operation.
VM_CONF_SIZE_BLK = 20


def invalid_vm_conf_disk(vol):
    """
    set VM metadata images format to RAW

    Since commit 0b61c4851a528fd6354d9ab77a68085c41f35dc9 copy of internal raw
    volumes is done using 'qemu-img convert' instead of invoking 'dd'.

    Consequently, exporting VM metadata images (produced during live snapshot)
    fails on qemu-img convert - since the images 'impersonate' to qcow2 (the
    format in .meta file is cow, whereas the real format is raw).  This problem
    is documented by https://bugzilla.redhat.com/1282239 and has subsequently
    been fixed in ovirt-engine (see https://gerrit.ovirt.org/48768).

    Since VM metadata volumes with this problem may still exist in storage we
    must keep using this workaround to avoid problems with copying VM disks.
    """
    if vol.getFormat() == sc.COW_FORMAT and vol.getSize() == VM_CONF_SIZE_BLK:
        info = qemuimg.info(vol.getVolumePath())
        actual_format = info['format']

        if actual_format == qemuimg.FORMAT.RAW:
            log.warning("Incorrect volume format %r has been detected"
                        " for volume %r, using the actual format %r.",
                        qemuimg.FORMAT.QCOW2,
                        vol.volUUID,
                        qemuimg.FORMAT.RAW)
            return True
    return False
