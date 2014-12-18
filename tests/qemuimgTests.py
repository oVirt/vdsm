#
# Copyright 2014 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from testlib import VdsmTestCase as TestCaseBase
import monkeypatch
from vdsm import qemuimg
from vdsm import utils

QEMU_IMG = qemuimg._qemuimg.cmd


class FakeCmd(object):

    def __init__(self, module, name, *calls):
        self.patch = monkeypatch.Patch([(module, name, self)])
        self.calls = list(calls)

    def __call__(self, cmd, **kw):
        call = self.calls.pop(0)
        return call(cmd, **kw)

    def __enter__(self):
        self.patch.apply()

    def __exit__(self, t=None, v=None, tb=None):
        self.patch.revert()


class InfoTests(TestCaseBase):

    def test_parse_error(self):
        def call(cmd, **kw):
            out = ["image: leaf.img", "invalid file format line"]
            return 0, out, []

        with FakeCmd(utils, 'execCmd', call):
            self.assertRaises(qemuimg.QImgError, qemuimg.info, 'leaf.img')

    def test_qemu1_no_backing_file(self):
        def call(cmd, **kw):
            out = ["image: leaf.img",
                   "file format: qcow2",
                   "virtual size: 1.0G (1073741824 bytes)",
                   "disk size: 196K",
                   "cluster_size: 65536"]
            return 0, out, []

        with FakeCmd(utils, 'execCmd', call):
            info = qemuimg.info('leaf.img')
            self.assertNotIn('backingfile', info)

    def test_qemu1_backing(self):
        def call(cmd, **kw):
            out = ["image: leaf.img",
                   "file format: qcow2",
                   "virtual size: 1.0G (1073741824 bytes)",
                   "disk size: 196K",
                   "cluster_size: 65536",
                   "backing file: base.img (actual path: /tmp/base.img)"]
            return 0, out, []

        with FakeCmd(utils, 'execCmd', call):
            info = qemuimg.info('leaf.img')
            self.assertEquals('base.img', info['backingfile'])

    def test_qemu2_no_backing_file(self):
        def call(cmd, **kw):
            out = ["image: leaf.img",
                   "file format: qcow2",
                   "virtual size: 1.0G (1073741824 bytes)",
                   "disk size: 196K",
                   "cluster_size: 65536",
                   "Format specific information:",
                   "    compat: 1.1",
                   "    lazy refcounts: false"]
            return 0, out, []

        with FakeCmd(utils, 'execCmd', call):
            info = qemuimg.info('leaf.img')
            self.assertEquals('qcow2', info['format'])
            self.assertEquals(1073741824, info['virtualsize'])
            self.assertEquals(65536, info['clustersize'])
            self.assertNotIn('backingfile', info)

    def test_qemu2_backing_no_cluster(self):
        def call(cmd, **kw):
            out = ["image: leaf.img",
                   "file format: qcow2",
                   "virtual size: 1.0G (1073741824 bytes)",
                   "disk size: 196K",
                   "backing file: base.img (actual path: /tmp/base.img)",
                   "Format specific information:",
                   "    compat: 1.1",
                   "    lazy refcounts: false"]
            return 0, out, []

        with FakeCmd(utils, 'execCmd', call):
            info = qemuimg.info('leaf.img')
            self.assertEquals('base.img', info['backingfile'])


class CreateTests(TestCaseBase):

    def test_no_format(self):
        def create(cmd, **kw):
            expected = [QEMU_IMG, 'create', 'image']
            self.assertEqual(cmd, expected)
            return 0, '', ''

        with FakeCmd(utils, 'execCmd', create):
            qemuimg.create('image')

    def test_zero_size(self):
        def create(cmd, **kw):
            expected = [QEMU_IMG, 'create', 'image', '0']
            self.assertEqual(cmd, expected)
            return 0, '', ''

        with FakeCmd(utils, 'execCmd', create):
            qemuimg.create('image', size=0)

    def test_qcow2_compat_unsupported(self):
        def qcow2_compat_unsupported(cmd, **kw):
            self.check_supports_qcow2_compat(cmd, **kw)
            return 0, 'Supported options:\nsize ...\n', ''

        def create(cmd, **kw):
            expected = [QEMU_IMG, 'create', '-f', 'qcow2', 'image']
            self.assertEqual(cmd, expected)
            return 0, '', ''

        with FakeCmd(utils, 'execCmd', qcow2_compat_unsupported, create):
            qemuimg.create('image', format='qcow2')

    def test_qcow2_compat_supported(self):
        def qcow2_compat_supported(cmd, **kw):
            self.check_supports_qcow2_compat(cmd, **kw)
            return 0, 'Supported options:\ncompat ...\n', ''

        def create(cmd, **kw):
            expected = [QEMU_IMG, 'create', '-f', 'qcow2', '-o', 'compat=0.10',
                        'image']
            self.assertEqual(cmd, expected)
            return 0, '', ''

        with FakeCmd(utils, 'execCmd', qcow2_compat_supported, create):
            qemuimg.create('image', format='qcow2')

    def check_supports_qcow2_compat(self, cmd, **kw):
        expected = [QEMU_IMG, 'create', '-f', 'qcow2', '-o', '?', '/dev/null']
        self.assertEqual(cmd, expected)


class ConvertTests(TestCaseBase):

    def test_no_format(self):
        def convert(cmd, **kw):
            expected = [QEMU_IMG, 'convert', '-t', 'none', 'src', 'dst']
            self.assertEqual(cmd, expected)
            return 0, '', ''

        with FakeCmd(utils, 'watchCmd', convert):
            qemuimg.convert('src', 'dst', True)

    def test_qcow2_compat_unsupported(self):
        def qcow2_compat_unsupported(cmd, **kw):
            self.check_supports_qcow2_compat(cmd, **kw)
            return 0, 'Supported options:\nsize ...\n', ''

        def convert(cmd, **kw):
            expected = [QEMU_IMG, 'convert', '-t', 'none', 'src', '-O',
                        'qcow2', 'dst']
            self.assertEqual(cmd, expected)
            return 0, '', ''

        with FakeCmd(utils, 'execCmd', qcow2_compat_unsupported):
            with FakeCmd(utils, 'watchCmd', convert):
                qemuimg.convert('src', 'dst', True, dstFormat='qcow2')

    def test_qcow2_compat_supported(self):
        def convert(cmd, **kw):
            expected = [QEMU_IMG, 'convert', '-t', 'none', 'src', '-O',
                        'qcow2', '-o', 'compat=0.10', 'dst']
            self.assertEqual(cmd, expected)
            return 0, '', ''

        with FakeCmd(utils, 'execCmd', self.qcow2_compat_supported):
            with FakeCmd(utils, 'watchCmd', convert):
                qemuimg.convert('src', 'dst', True, dstFormat='qcow2')

    def test_qcow2_no_backing_file(self):
        def convert(cmd, **kw):
            expected = [QEMU_IMG, 'convert', '-t', 'none', 'src', '-O',
                        'qcow2', '-o', 'compat=0.10', 'dst']
            self.assertEqual(cmd, expected)
            return 0, '', ''

        with FakeCmd(utils, 'execCmd', self.qcow2_compat_supported):
            with FakeCmd(utils, 'watchCmd', convert):
                qemuimg.convert('src', 'dst', None, dstFormat='qcow2')

    def test_qcow2_backing_file(self):
        def convert(cmd, **kw):
            expected = [QEMU_IMG, 'convert', '-t', 'none', 'src', '-O',
                        'qcow2', '-o', 'compat=0.10,backing_file=bak',
                        'dst']
            self.assertEqual(cmd, expected)
            return 0, '', ''

        with FakeCmd(utils, 'execCmd', self.qcow2_compat_supported):
            with FakeCmd(utils, 'watchCmd', convert):
                qemuimg.convert('src', 'dst', None, dstFormat='qcow2',
                                backing='bak')

    def test_qcow2_backing_format(self):
        def convert(cmd, **kw):
            expected = [QEMU_IMG, 'convert', '-t', 'none', 'src', '-O',
                        'qcow2', '-o', 'compat=0.10', 'dst']
            self.assertEqual(cmd, expected)
            return 0, '', ''

        with FakeCmd(utils, 'execCmd', self.qcow2_compat_supported):
            with FakeCmd(utils, 'watchCmd', convert):
                qemuimg.convert('src', 'dst', None, dstFormat='qcow2',
                                backingFormat='qcow2')

    def test_qcow2_backing_file_and_format(self):
        def convert(cmd, **kw):
            expected = [QEMU_IMG, 'convert', '-t', 'none', 'src', '-O',
                        'qcow2', '-o', 'compat=0.10,backing_file=bak,'
                        'backing_fmt=qcow2', 'dst']
            self.assertEqual(cmd, expected)
            return 0, '', ''

        with FakeCmd(utils, 'execCmd', self.qcow2_compat_supported):
            with FakeCmd(utils, 'watchCmd', convert):
                qemuimg.convert('src', 'dst', None, dstFormat='qcow2',
                                backing='bak', backingFormat='qcow2')

    def check_supports_qcow2_compat(self, cmd, **kw):
        expected = [QEMU_IMG, 'convert', '-O', 'qcow2', '-o', '?', '/dev/null',
                    '/dev/null']
        self.assertEqual(cmd, expected)

    def qcow2_compat_supported(self, cmd, **kw):
        self.check_supports_qcow2_compat(cmd, **kw)
        return 0, 'Supported options:\ncompat ...\n', ''
