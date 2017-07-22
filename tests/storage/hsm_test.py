#
# Copyright 2016-2017 Red Hat, Inc.
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

from contextlib import contextmanager

from monkeypatch import MonkeyPatchScope
from testlib import make_config
from testlib import make_uuid
from testlib import VdsmTestCase
from testlib import permutations, expandPermutations

from storage.storagetestlib import (
    fake_file_env,
    make_file_volume,
)

from vdsm.storage import constants as sc
from vdsm.storage import exception as se
from vdsm.storage import hsm
from vdsm.storage import qemuimg


class FakeHSM(hsm.HSM):
    def __init__(self):
        pass


@expandPermutations
class TestVerifyUntrustedVolume(VdsmTestCase):
    SIZE = 1024 * 1024

    @permutations(((sc.RAW_FORMAT,), (sc.COW_FORMAT,)))
    def test_ok(self, vol_fmt):
        with self.fake_volume(vol_fmt) as vol:
            qemu_fmt = sc.FMT2STR[vol_fmt]
            qemuimg.create(vol.volumePath, size=self.SIZE, format=qemu_fmt)
            h = FakeHSM()
            self.assertNotRaises(h.verify_untrusted_volume,
                                 'sp', vol.sdUUID, vol.imgUUID, vol.volUUID)

    @permutations((
        (sc.RAW_FORMAT, qemuimg.FORMAT.QCOW2),
        (sc.COW_FORMAT, qemuimg.FORMAT.RAW),
    ))
    def test_wrong_format_raises(self, vol_fmt, qemu_fmt):
        with self.fake_volume(vol_fmt) as vol:
            qemuimg.create(vol.volumePath, size=self.SIZE, format=qemu_fmt)
            h = FakeHSM()
            self.assertRaises(se.ImageVerificationError,
                              h.verify_untrusted_volume,
                              'sp', vol.sdUUID, vol.imgUUID, vol.volUUID)

    def test_backingfile_raises(self):
        with self.fake_volume(sc.COW_FORMAT) as vol:
            qemu_fmt = qemuimg.FORMAT.QCOW2
            qemuimg.create(vol.volumePath, size=self.SIZE, format=qemu_fmt,
                           backing='foo')
            h = FakeHSM()
            self.assertRaises(se.ImageVerificationError,
                              h.verify_untrusted_volume,
                              'sp', vol.sdUUID, vol.imgUUID, vol.volUUID)

    def test_unsupported_compat(self):
        with self.fake_volume(sc.COW_FORMAT) as vol:
            info = {"format": qemuimg.FORMAT.QCOW2, "compat": "BAD"}
            with MonkeyPatchScope([(qemuimg, 'info', lambda unused: info)]):
                h = FakeHSM()
                self.assertRaises(se.ImageVerificationError,
                                  h.verify_untrusted_volume, 'sp',
                                  vol.sdUUID, vol.imgUUID, vol.volUUID)

    @permutations((
        ('0.10', '0.10', 4),
        ('1.1', '0.10', 4),
        ('0.10', '1.1', 4),
        ('1.1', '1.1', 4),
        ('0.10', '0.10', 3),
        ('1.1', '1.1', 3),
    ))
    def test_valid_qcow2_compat(self, hsm_compat, config_compat, sd_version):
        with self.fake_volume(vol_fmt=sc.COW_FORMAT,
                              sd_version=sd_version) as vol:
            create_conf = make_config([('irs', 'qcow2_compat', config_compat)])
            info = {"format": qemuimg.FORMAT.QCOW2, "compat": hsm_compat}
            with MonkeyPatchScope([(qemuimg, 'config', create_conf),
                                   (qemuimg, 'info', lambda unused: info)]):
                qemuimg.create(vol.volumePath, size=self.SIZE,
                               format=qemuimg.FORMAT.QCOW2)
                h = FakeHSM()
                self.assertNotRaises(h.verify_untrusted_volume, 'sp',
                                     vol.sdUUID, vol.imgUUID, vol.volUUID)

    @permutations((
        ('1.1', '0.10', 3),
    ))
    def test_disabled_compat_raises(self, hsm_compat, config_compat,
                                    sd_version):
        with self.fake_volume(vol_fmt=sc.COW_FORMAT,
                              sd_version=sd_version) as vol:
            create_conf = make_config([('irs', 'qcow2_compat', config_compat)])
            info = {"format": qemuimg.FORMAT.QCOW2, "compat": hsm_compat}
            with MonkeyPatchScope([(qemuimg, 'config', create_conf),
                                   (qemuimg, 'info', lambda unused: info)]):
                qemuimg.create(vol.volumePath, size=self.SIZE,
                               format=qemuimg.FORMAT.QCOW2)
                h = FakeHSM()
                self.assertRaises(se.ImageVerificationError,
                                  h.verify_untrusted_volume, 'sp',
                                  vol.sdUUID, vol.imgUUID, vol.volUUID)

    def test_compat_not_checked_for_raw(self):
        with self.fake_volume(sc.RAW_FORMAT) as vol:
            info = {"format": qemuimg.FORMAT.RAW, "compat": "BAD"}
            with MonkeyPatchScope([(qemuimg, 'info', lambda unused: info)]):
                h = FakeHSM()
                self.assertNotRaises(h.verify_untrusted_volume, 'sp',
                                     vol.sdUUID, vol.imgUUID, vol.volUUID)

    @contextmanager
    def fake_volume(self, vol_fmt, sd_version=3):
        with fake_file_env(sd_version=sd_version) as env:
            img_id = make_uuid()
            vol_id = make_uuid()
            make_file_volume(env.sd_manifest, self.SIZE, img_id, vol_id,
                             vol_format=vol_fmt)
            yield env.sd_manifest.produceVolume(img_id, vol_id)
