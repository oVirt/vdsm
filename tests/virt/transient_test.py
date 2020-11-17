# Copyright 2018 Red Hat, Inc.
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
import stat

from vdsm.common.units import MiB
from vdsm.storage import sdc
from vdsm.storage import qemuimg

from vdsm.virt import vm
from vdsm.virt.vmdevices.storage import DISK_TYPE
from vdsm.virt.vmdevices.storage import DRIVE_SHARED_TYPE

from monkeypatch import MonkeyPatch
from testlib import VdsmTestCase
from testlib import make_config
from testlib import namedTemporaryDir
from testlib import permutations, expandPermutations

VIRTUAL_SIZE = 10 * MiB
QCOW2_COMPAT = '1.1'


class FakeVM(vm.Vm):

    def __init__(self):
        pass


class FakeSDCache(object):

    def produce_manifest(self, sdUUID):
        return self

    def qcow2_compat(self):
        return QCOW2_COMPAT


@expandPermutations
class TestTransient(VdsmTestCase):

    def test_not_transient_drive(self):
        drive = {
            'diskType': DISK_TYPE.FILE,
            'format': 'cow',
            'path': '/original/path',
        }
        testvm = FakeVM()
        testvm._prepareTransientDisks([drive])

        self.assertEqual(drive['diskType'], DISK_TYPE.FILE)
        self.assertEqual(drive['path'], '/original/path')
        self.assertEqual(drive['format'], 'cow')

    @MonkeyPatch(vm, 'config', make_config([]))
    @MonkeyPatch(sdc, 'sdCache', FakeSDCache())
    @permutations([['raw'], ['cow']])
    def test_transient(self, img_format):
        with namedTemporaryDir() as tmpdir:
            original_path = os.path.join(tmpdir, 'base')
            self.create_image(original_path, img_format)

            drive = {
                'diskType': DISK_TYPE.BLOCK,
                'domainID': 'domainid',
                'format': img_format,
                'path': original_path,
                'shared': DRIVE_SHARED_TYPE.TRANSIENT,
                'volumeID': 'volumeid',
            }
            vm.config.set('vars', 'transient_disks_repository', tmpdir)
            testvm = FakeVM()
            testvm._prepareTransientDisks([drive])

            self.check_drive(drive, original_path, tmpdir)

    def check_drive(self, drive, original_path, tmpdir):
        self.assertEqual(drive['diskType'], DISK_TYPE.FILE)
        self.assertEqual(drive['format'], 'cow')
        self.assertTrue(drive['path'].startswith(tmpdir),
                        "%s does not start with %s" % (drive['path'], tmpdir))

        file_stat = os.stat(drive['path'])
        self.assertEqual(stat.S_IMODE(file_stat.st_mode), 0o660)

        transient_info = qemuimg.info(drive['path'])
        self.assertEqual(transient_info['format'], qemuimg.FORMAT.QCOW2)
        self.assertEqual(transient_info['virtual-size'], VIRTUAL_SIZE)
        self.assertEqual(
            transient_info['format-specific']['data']['compat'], QCOW2_COMPAT)
        self.assertEqual(transient_info['backing-filename'], original_path)

    def create_image(self, img_path, img_format):
        if img_format == 'raw':
            with open(img_path, 'w') as f:
                f.truncate(VIRTUAL_SIZE)
        elif img_format == 'cow':
            op = qemuimg.create(
                img_path,
                size=VIRTUAL_SIZE,
                format=qemuimg.FORMAT.QCOW2,
                qcow2Compat=QCOW2_COMPAT)
            op.run()
        else:
            raise AssertionError("invalid format: %s" % img_format)
