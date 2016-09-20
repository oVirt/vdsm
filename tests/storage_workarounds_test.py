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
import uuid

from testlib import VdsmTestCase
from storagetestlib import fake_file_env

from vdsm import qemuimg
from vdsm.storage import constants as sc
from vdsm.storage import workarounds


md_formats = dict(raw=sc.RAW_FORMAT, cow=sc.COW_FORMAT)
qemu_formats = dict(raw=qemuimg.FORMAT.RAW, cow=qemuimg.FORMAT.QCOW2)
VM_CONF_SIZE = workarounds.VM_CONF_SIZE_BLK * sc.BLOCK_SIZE


def make_volume(env, size, md_fmt, real_fmt):
    img_id = str(uuid.uuid4())
    vol_id = str(uuid.uuid4())
    env.make_volume(size, img_id, vol_id, vol_format=md_formats[md_fmt])
    vol = env.sd_manifest.produceVolume(img_id, vol_id)
    qemuimg.create(vol.getVolumePath(), size, qemu_formats[real_fmt])
    return vol


class DetectFormatTest(VdsmTestCase):

    def test_bad_format_vm_conf_disk(self):
        """
        When the volume size matches the VM configuration disk size and the
        source volume reports COW even though qemuimg reports RAW then we
        expect the workaround to report both volumes as RAW.
        """
        with fake_file_env() as env:
            src = make_volume(env, VM_CONF_SIZE, md_fmt='cow', real_fmt='raw')
            dst = make_volume(env, VM_CONF_SIZE, md_fmt='cow', real_fmt='raw')
            self.assertEqual((qemuimg.FORMAT.RAW, qemuimg.FORMAT.RAW),
                             workarounds.detect_format(src, dst))

    def test_bad_format_other_size(self):
        """
        When the volume size does not match the VM configuration disk size then
        the workaround will not be activated even when the formats don't match
        """
        size = 2 * VM_CONF_SIZE
        with fake_file_env() as env:
            src = make_volume(env, size, md_fmt='cow', real_fmt='raw')
            dst = make_volume(env, size, md_fmt='cow', real_fmt='raw')
            self.assertEqual((qemuimg.FORMAT.QCOW2, qemuimg.FORMAT.QCOW2),
                             workarounds.detect_format(src, dst))

    def test_cow_vm_conf_disk(self):
        """
        When a VM configuration disk is actually COW format report it correctly
        """
        with fake_file_env() as env:
            src = make_volume(env, VM_CONF_SIZE, md_fmt='cow', real_fmt='cow')
            dst = make_volume(env, VM_CONF_SIZE, md_fmt='cow', real_fmt='cow')
            self.assertEqual((qemuimg.FORMAT.QCOW2, qemuimg.FORMAT.QCOW2),
                             workarounds.detect_format(src, dst))
