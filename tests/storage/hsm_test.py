#
# Copyright 2016-2018 Red Hat, Inc.
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
from __future__ import division

import os

from contextlib import contextmanager

import pytest

from monkeypatch import MonkeyPatchScope
from testlib import make_config
from testlib import make_uuid

from storage.storagetestlib import (
    fake_file_env,
    make_file_volume,
    make_qemu_chain,
)

from vdsm.common.units import MiB
from vdsm.storage import constants as sc
from vdsm.storage import exception as se
from vdsm.storage import hsm
from vdsm.storage import qemuimg


class FakeHSM(hsm.HSM):
    def __init__(self):
        pass


class TestVerifyUntrustedVolume(object):
    SIZE = MiB

    @pytest.mark.parametrize('vol_fmt,', [sc.RAW_FORMAT, sc.COW_FORMAT])
    def test_ok(self, vol_fmt):
        with self.fake_volume(vol_fmt) as vol:
            qemu_fmt = sc.FMT2STR[vol_fmt]
            op = qemuimg.create(vol.volumePath, size=self.SIZE,
                                format=qemu_fmt)
            op.run()
            h = FakeHSM()
            h.verify_untrusted_volume(
                'sp', vol.sdUUID, vol.imgUUID, vol.volUUID)

    @pytest.mark.parametrize('vol_fmt,qemu_fmt', [
        (sc.RAW_FORMAT, qemuimg.FORMAT.QCOW2),
        (sc.COW_FORMAT, qemuimg.FORMAT.RAW),
    ])
    def test_wrong_format_raises(self, vol_fmt, qemu_fmt):
        with self.fake_volume(vol_fmt) as vol:
            op = qemuimg.create(vol.volumePath, size=self.SIZE,
                                format=qemu_fmt)
            op.run()
            h = FakeHSM()
            with pytest.raises(se.ImageVerificationError):
                h.verify_untrusted_volume(
                    'sp', vol.sdUUID, vol.imgUUID, vol.volUUID)

    @pytest.mark.parametrize('vol_fmt,qemu_fmt', [
        (sc.RAW_FORMAT, qemuimg.FORMAT.RAW),
        (sc.COW_FORMAT, qemuimg.FORMAT.QCOW2),
    ])
    def test_bigger_size_raises(self, vol_fmt, qemu_fmt):
        with self.fake_volume(vol_fmt) as vol:
            op = qemuimg.create(
                vol.volumePath,
                size=self.SIZE + sc.BLOCK_SIZE_4K,
                format=qemu_fmt)
            op.run()
            h = FakeHSM()
            with pytest.raises(se.ImageVerificationError):
                h.verify_untrusted_volume(
                    'sp', vol.sdUUID, vol.imgUUID, vol.volUUID)

    @pytest.mark.parametrize('vol_fmt,qemu_fmt', [
        (sc.RAW_FORMAT, qemuimg.FORMAT.RAW),
        (sc.COW_FORMAT, qemuimg.FORMAT.QCOW2),
    ])
    def test_smaller_size_ok(self, vol_fmt, qemu_fmt):
        # Engine < 4.2.6 rounds disk size to a multiple of 1G, creating disks
        # with incorrect virtual size. To be compatible with old engines we
        # cannot fail verification in this case.
        with self.fake_volume(vol_fmt) as vol:
            op = qemuimg.create(
                vol.volumePath,
                size=self.SIZE - sc.BLOCK_SIZE_4K,
                format=qemu_fmt)
            op.run()
            h = FakeHSM()
            h.verify_untrusted_volume(
                'sp', vol.sdUUID, vol.imgUUID, vol.volUUID)

    def test_valid_with_backingfile(self):
        with fake_file_env() as env:
            vol = make_qemu_chain(env, self.SIZE, sc.COW_FORMAT, 2)[1]
            h = FakeHSM()
            h.verify_untrusted_volume(
                'sp', vol.sdUUID, vol.imgUUID, vol.volUUID)

    def test_valid_without_backingfile(self):
        with fake_file_env() as env:
            vol = make_qemu_chain(env, self.SIZE, sc.COW_FORMAT, 2)[0]
            h = FakeHSM()
            h.verify_untrusted_volume(
                'sp', vol.sdUUID, vol.imgUUID, vol.volUUID)

    def test_wrong_backingfile(self):
        with fake_file_env() as env:
            vol = make_qemu_chain(env, self.SIZE, sc.COW_FORMAT, 2)[1]
            # Simulate upload image with wrong backing_file.
            wrong_volume = os.path.join(
                os.path.dirname(vol.volumePath), "wrong")
            open(wrong_volume, "w").close()
            op = qemuimg.rebase(vol.volumePath, "wrong", unsafe=True)
            op.run()
            h = FakeHSM()
            with pytest.raises(se.ImageVerificationError):
                h.verify_untrusted_volume(
                    'sp', vol.sdUUID, vol.imgUUID, vol.volUUID)

    def test_unexpected_backing_file(self):
        with self.fake_volume(sc.COW_FORMAT) as vol:
            # Simulate upload of qcow2 with unexpected backing file.
            unexpected_volume = os.path.join(
                os.path.dirname(vol.volumePath), "unexpected")
            open(unexpected_volume, "w").close()
            op = qemuimg.rebase(vol.volumePath, 'unexpected', unsafe=True)
            op.run()
            h = FakeHSM()
            with pytest.raises(se.ImageVerificationError):
                h.verify_untrusted_volume(
                    'sp', vol.sdUUID, vol.imgUUID, vol.volUUID)

    def test_missing_backing_file(self):
        with fake_file_env() as env:
            vol = make_qemu_chain(env, self.SIZE, sc.COW_FORMAT, 2)[1]
            # Simulate upload of image without backing file to a a snapshot
            op = qemuimg.create(vol.volumePath, size=self.SIZE,
                                format=qemuimg.FORMAT.QCOW2)
            op.run()
            h = FakeHSM()
            with pytest.raises(se.ImageVerificationError):
                h.verify_untrusted_volume(
                    'sp', vol.sdUUID, vol.imgUUID, vol.volUUID)

    @pytest.mark.parametrize('hsm_compat,config_compat,sd_version', [
        ('0.10', '0.10', 4),
        ('1.1', '0.10', 4),
        ('0.10', '1.1', 4),
        ('1.1', '1.1', 4),
        ('0.10', '0.10', 3),
        ('1.1', '1.1', 3),
    ])
    def test_valid_qcow2_compat(self, hsm_compat, config_compat, sd_version):
        with self.fake_volume(vol_fmt=sc.COW_FORMAT,
                              sd_version=sd_version) as vol:
            create_conf = make_config([('irs', 'qcow2_compat', config_compat)])
            with MonkeyPatchScope([(qemuimg, 'config', create_conf)]):
                op = qemuimg.create(vol.volumePath, size=self.SIZE,
                                    format=qemuimg.FORMAT.QCOW2,
                                    qcow2Compat=hsm_compat)
                op.run()
                h = FakeHSM()
                h.verify_untrusted_volume(
                    'sp', vol.sdUUID, vol.imgUUID, vol.volUUID)

    @pytest.mark.parametrize('hsm_compat,config_compat,sd_version', [
        ('1.1', '0.10', 3),
    ])
    def test_disabled_compat_raises(self, hsm_compat, config_compat,
                                    sd_version):
        with self.fake_volume(vol_fmt=sc.COW_FORMAT,
                              sd_version=sd_version) as vol:
            create_conf = make_config([('irs', 'qcow2_compat', config_compat)])
            with MonkeyPatchScope([(qemuimg, 'config', create_conf)]):
                op = qemuimg.create(vol.volumePath, size=self.SIZE,
                                    format=qemuimg.FORMAT.QCOW2,
                                    qcow2Compat=hsm_compat)
                op.run()
                h = FakeHSM()
                with pytest.raises(se.ImageVerificationError):
                    h.verify_untrusted_volume(
                        'sp', vol.sdUUID, vol.imgUUID, vol.volUUID)

    @contextmanager
    def fake_volume(self, vol_fmt, sd_version=3):
        with fake_file_env(sd_version=sd_version) as env:
            img_id = make_uuid()
            vol_id = make_uuid()
            make_file_volume(env.sd_manifest, self.SIZE, img_id, vol_id,
                             vol_format=vol_fmt)
            yield env.sd_manifest.produceVolume(img_id, vol_id)


class FakePool(object):
    """
    Fake storage pool class implementing the extend volume interface.
    """
    spUUID = '5d928855-b09b-47a7-b920-bd2d2eb5808c'

    def __init__(self):
        self.size = None

    def extendVolume(self, sdUUID, volUUID, size, isShuttingDown):
        self.size = size

    def is_connected(self):
        return True


@pytest.mark.parametrize("size, expected_size_mb", [
    (100 * MiB, 100),
    (100 * MiB - 1, 100),
    (100 * MiB + 1, 101),
])
def test_extend_volume(monkeypatch, fake_task, size, expected_size_mb):
    h = FakeHSM()
    pool = FakePool()

    monkeypatch.setattr(hsm.HSM, "getPool", lambda self, spUUID: pool)
    h.extendVolume(
        sdUUID=None, spUUID=None, imgUUID=None, volumeUUID=None, size=size)

    assert pool.size == expected_size_mb
