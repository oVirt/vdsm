# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

from testlib import make_uuid
from testlib import VdsmTestCase
from storage.storagetestlib import fake_file_env

from vdsm.storage import constants as sc
from vdsm.storage import qemuimg
from vdsm.storage import workarounds

md_formats = dict(raw=sc.RAW_FORMAT, cow=sc.COW_FORMAT)
qemu_formats = dict(raw=qemuimg.FORMAT.RAW, cow=qemuimg.FORMAT.QCOW2)


def make_volume(env, size, md_fmt, real_fmt):
    img_id = make_uuid()
    vol_id = make_uuid()
    env.make_volume(size, img_id, vol_id, vol_format=md_formats[md_fmt])
    vol = env.sd_manifest.produceVolume(img_id, vol_id)
    op = qemuimg.create(vol.getVolumePath(), size, qemu_formats[real_fmt])
    op.run()
    return vol


class TestDetectFormat(VdsmTestCase):

    def test_bad_format_vm_conf_disk(self):
        """
        When the volume size matches the VM configuration disk size and the
        source volume reports COW even though qemuimg reports RAW then we
        expect the workaround to report both volumes as RAW.
        """
        with fake_file_env() as env:
            vol = make_volume(
                env, workarounds.VM_CONF_SIZE, md_fmt='cow', real_fmt='raw')
            self.assertTrue(workarounds.invalid_vm_conf_disk(vol))

    def test_bad_format_other_size(self):
        """
        When the volume size does not match the VM configuration disk size then
        the workaround will not be activated even when the formats don't match
        """
        size = 2 * workarounds.VM_CONF_SIZE
        with fake_file_env() as env:
            vol = make_volume(env, size, md_fmt='cow', real_fmt='raw')
            self.assertFalse(workarounds.invalid_vm_conf_disk(vol))

    def test_cow_vm_conf_disk(self):
        """
        When a VM configuration disk is actually COW format report it correctly
        """
        with fake_file_env() as env:
            vol = make_volume(
                env, workarounds.VM_CONF_SIZE, md_fmt='cow', real_fmt='cow')
            self.assertFalse(workarounds.invalid_vm_conf_disk(vol))
