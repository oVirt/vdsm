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

from monkeypatch import MonkeyPatch, MonkeyPatchScope
from testlib import VdsmTestCase as TestCaseBase
from vdsm import qemuimg
from vdsm import utils

QEMU_IMG = qemuimg._qemuimg.cmd


class CommandTests(TestCaseBase):

    def supported(self, command, result):
        def check(arg):
            self.assertEqual(command, arg)
            return result
        return check


class InfoTests(TestCaseBase):

    def test_parse_error(self):
        def call(cmd, **kw):
            out = ["image: leaf.img", "invalid file format line"]
            return 0, out, []

        with MonkeyPatchScope([(utils, "execCmd", call)]):
            self.assertRaises(qemuimg.QImgError, qemuimg.info, 'leaf.img')

    def test_qemu1_no_backing_file(self):
        def call(cmd, **kw):
            out = ["image: leaf.img",
                   "file format: qcow2",
                   "virtual size: 1.0G (1073741824 bytes)",
                   "disk size: 196K",
                   "cluster_size: 65536"]
            return 0, out, []

        with MonkeyPatchScope([(utils, "execCmd", call)]):
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

        with MonkeyPatchScope([(utils, "execCmd", call)]):
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

        with MonkeyPatchScope([(utils, "execCmd", call)]):
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

        with MonkeyPatchScope([(utils, "execCmd", call)]):
            info = qemuimg.info('leaf.img')
            self.assertEquals('base.img', info['backingfile'])


class CreateTests(CommandTests):

    def test_no_format(self):
        def create(cmd, **kw):
            expected = [QEMU_IMG, 'create', 'image']
            self.assertEqual(cmd, expected)
            return 0, '', ''

        with MonkeyPatchScope([(utils, "execCmd", create)]):
            qemuimg.create('image')

    def test_zero_size(self):
        def create(cmd, **kw):
            expected = [QEMU_IMG, 'create', 'image', '0']
            self.assertEqual(cmd, expected)
            return 0, '', ''

        with MonkeyPatchScope([(utils, "execCmd", create)]):
            qemuimg.create('image', size=0)

    def test_qcow2_compat_unsupported(self):
        def create(cmd, **kw):
            expected = [QEMU_IMG, 'create', '-f', 'qcow2', 'image']
            self.assertEqual(cmd, expected)
            return 0, '', ''

        with MonkeyPatchScope([(qemuimg, '_supports_qcow2_compat',
                                self.supported('create', False)),
                               (utils, 'execCmd', create)]):
            qemuimg.create('image', format='qcow2')

    def test_qcow2_compat_supported(self):

        def create(cmd, **kw):
            expected = [QEMU_IMG, 'create', '-f', 'qcow2', '-o', 'compat=0.10',
                        'image']
            self.assertEqual(cmd, expected)
            return 0, '', ''

        with MonkeyPatchScope([(qemuimg, '_supports_qcow2_compat',
                                self.supported('create', True)),
                               (utils, 'execCmd', create)]):
            qemuimg.create('image', format='qcow2')


class ConvertTests(CommandTests):

    def test_no_format(self):
        def convert(cmd, **kw):
            expected = [QEMU_IMG, 'convert', '-t', 'none', 'src', 'dst']
            self.assertEqual(cmd, expected)
            return 0, '', ''

        with MonkeyPatchScope([(utils, 'watchCmd', convert),
                               (qemuimg, '_supports_src_cache',
                                self.supported('convert', False))]):
            qemuimg.convert('src', 'dst', True)

    def test_qcow2_compat_unsupported(self):
        def convert(cmd, **kw):
            expected = [QEMU_IMG, 'convert', '-t', 'none', 'src', '-O',
                        'qcow2', 'dst']
            self.assertEqual(cmd, expected)
            return 0, '', ''

        with MonkeyPatchScope([(qemuimg, '_supports_qcow2_compat',
                                self.supported('convert', False)),
                               (qemuimg, '_supports_src_cache',
                                self.supported('convert', False)),
                               (utils, 'watchCmd', convert)]):
            qemuimg.convert('src', 'dst', True, dstFormat='qcow2')

    def test_qcow2_compat_supported(self):
        def convert(cmd, **kw):
            expected = [QEMU_IMG, 'convert', '-t', 'none', 'src', '-O',
                        'qcow2', '-o', 'compat=0.10', 'dst']
            self.assertEqual(cmd, expected)
            return 0, '', ''

        with MonkeyPatchScope([(qemuimg, '_supports_qcow2_compat',
                                self.supported('convert', True)),
                               (qemuimg, '_supports_src_cache',
                                self.supported('convert', False)),
                               (utils, 'watchCmd', convert)]):
            qemuimg.convert('src', 'dst', True, dstFormat='qcow2')

    def test_qcow2_no_backing_file(self):
        def convert(cmd, **kw):
            expected = [QEMU_IMG, 'convert', '-t', 'none', 'src', '-O',
                        'qcow2', '-o', 'compat=0.10', 'dst']
            self.assertEqual(cmd, expected)
            return 0, '', ''

        with MonkeyPatchScope([(qemuimg, '_supports_qcow2_compat',
                                self.supported('convert', True)),
                               (qemuimg, '_supports_src_cache',
                                self.supported('convert', False)),
                               (utils, 'watchCmd', convert)]):
            qemuimg.convert('src', 'dst', None, dstFormat='qcow2')

    def test_qcow2_backing_file(self):
        def convert(cmd, **kw):
            expected = [QEMU_IMG, 'convert', '-t', 'none', 'src', '-O',
                        'qcow2', '-o', 'compat=0.10,backing_file=bak',
                        'dst']
            self.assertEqual(cmd, expected)
            return 0, '', ''

        with MonkeyPatchScope([(qemuimg, '_supports_qcow2_compat',
                                self.supported('convert', True)),
                               (qemuimg, '_supports_src_cache',
                                self.supported('convert', False)),
                               (utils, 'watchCmd', convert)]):
            qemuimg.convert('src', 'dst', None, dstFormat='qcow2',
                            backing='bak')

    def test_qcow2_backing_format(self):
        def convert(cmd, **kw):
            expected = [QEMU_IMG, 'convert', '-t', 'none', 'src', '-O',
                        'qcow2', '-o', 'compat=0.10', 'dst']
            self.assertEqual(cmd, expected)
            return 0, '', ''

        with MonkeyPatchScope([(qemuimg, '_supports_qcow2_compat',
                                self.supported('convert', True)),
                               (qemuimg, '_supports_src_cache',
                                self.supported('convert', False)),
                               (utils, 'watchCmd', convert)]):
            qemuimg.convert('src', 'dst', None, dstFormat='qcow2',
                            backingFormat='qcow2')

    def test_qcow2_backing_file_and_format(self):
        def convert(cmd, **kw):
            expected = [QEMU_IMG, 'convert', '-t', 'none', 'src', '-O',
                        'qcow2', '-o', 'compat=0.10,backing_file=bak,'
                        'backing_fmt=qcow2', 'dst']
            self.assertEqual(cmd, expected)
            return 0, '', ''

        with MonkeyPatchScope([(qemuimg, '_supports_qcow2_compat',
                                self.supported('convert', True)),
                               (qemuimg, '_supports_src_cache',
                                self.supported('convert', False)),
                               (utils, 'watchCmd', convert)]):
            qemuimg.convert('src', 'dst', None, dstFormat='qcow2',
                            backing='bak', backingFormat='qcow2')


def qcow2_compat_supported(cmd, **kw):
    return 0, 'Supported options:\ncompat ...\n', ''


def qcow2_compat_unsupported(cmd, **kw):
    return 0, 'Supported options:\nsize ...\n', ''


class SupportsQcow2ComaptTests(TestCaseBase):

    @MonkeyPatch(utils, 'execCmd', qcow2_compat_supported)
    def test_create_supported(self, **kw):
        self.assertTrue(qemuimg._supports_qcow2_compat('create'))

    @MonkeyPatch(utils, 'execCmd', qcow2_compat_unsupported)
    def test_create_unsupported(self, **kw):
        self.assertFalse(qemuimg._supports_qcow2_compat('create'))

    @MonkeyPatch(utils, 'execCmd', qcow2_compat_supported)
    def test_convert_supported(self, **kw):
        self.assertTrue(qemuimg._supports_qcow2_compat('convert'))

    @MonkeyPatch(utils, 'execCmd', qcow2_compat_unsupported)
    def test_convert_unsupported(self, **kw):
        self.assertFalse(qemuimg._supports_qcow2_compat('convert'))


def src_cache_supported(cmd, **kw):
    out = """
  compare [-f fmt] [-F fmt] [-T src_cache] ...
  convert [-c] [-p] [-q] [-n] [-f fmt] [-t cache] [-T src_cache] ...
  rebase [-q] [-f fmt] [-t cache] [-T src_cache] [-p] ...
  resize [-q] filename [+ | -]size ...
"""

    return 0, out, ''


def rebase_src_cache_unsupported(cmd, **kw):
    out = """
  compare [-f fmt] [-F fmt] [-T src_cache] ...
  convert [-c] [-p] [-q] [-n] [-f fmt] [-t cache] [-T src_cache] ...
  rebase [-q] [-f fmt] [-t cache] [-p] ...
  resize [-q] filename [+ | -]size ...
"""

    return 0, out, ''


def convert_src_cache_unsupported(cmd, **kw):
    out = """
  compare [-f fmt] [-F fmt] [-T src_cache] ...
  convert [-c] [-p] [-q] [-n] [-f fmt] [-t cache] ...
  rebase [-q] [-f fmt] [-t cache] [-T src_cache] [-p] ...
  resize [-q] filename [+ | -]size ...
"""

    return 0, out, ''


class SupportsSrcCacheTests(TestCaseBase):

    def tearDown(self):
        qemuimg._supports_src_cache.invalidate()

    @MonkeyPatch(utils, 'execCmd', src_cache_supported)
    def test_rebase_supported(self, **kw):
        self.assertTrue(qemuimg._supports_src_cache('rebase'))

    @MonkeyPatch(utils, 'execCmd', rebase_src_cache_unsupported)
    def test_rebase_unsupported(self, **kw):
        self.assertFalse(qemuimg._supports_src_cache('rebase'))

    @MonkeyPatch(utils, 'execCmd', src_cache_supported)
    def test_convert_supported(self, **kw):
        self.assertTrue(qemuimg._supports_src_cache('convert'))

    @MonkeyPatch(utils, 'execCmd', convert_src_cache_unsupported)
    def test_convert_unsupported(self, **kw):
        self.assertFalse(qemuimg._supports_src_cache('convert'))


class CheckTests(TestCaseBase):

    def test_offset_with_stats(self):
        def call(cmd, **kw):
            out = ["No errors were found on the image.",
                   "65157/98304 = 66.28% allocated, 0.00% fragmented, 0.00% "
                   "compressed clusters",
                   "Image end offset: 4271243264"]
            return 0, out, []

        with MonkeyPatchScope([(utils, "execCmd", call)]):
            check = qemuimg.check('unused')
            self.assertEquals(4271243264, check['offset'])

    def test_offset_without_stats(self):
        def call(cmd, **kw):
            out = ["No errors were found on the image.",
                   "Image end offset: 4271243264"]
            return 0, out, []

        with MonkeyPatchScope([(utils, "execCmd", call)]):
            check = qemuimg.check('unused')
            self.assertEquals(4271243264, check['offset'])

    def test_offset_no_match(self):
        def call(cmd, **kw):
            out = ["All your base are belong to us."]
            return 0, out, []

        with MonkeyPatchScope([(utils, "execCmd", call)]):
            self.assertRaises(qemuimg.QImgError, qemuimg.check, 'unused')
