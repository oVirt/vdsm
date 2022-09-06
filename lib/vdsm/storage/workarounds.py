# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import

import logging

from vdsm.storage import constants as sc
from vdsm.storage import qemuimg

log = logging.getLogger('storage.workarounds')

# Size in bytes of the conf file generated during RAM snapshot operation.
# Engine sends value of 20 * 512 == 10240 bytes.
VM_CONF_SIZE = 10240


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
    if (vol.getFormat() == sc.COW_FORMAT and
            vol.getCapacity() == VM_CONF_SIZE):
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
