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

from monkeypatch import MonkeyPatchScope
from testlib import VdsmTestCase as TestCaseBase
from testlib import permutations, expandPermutations
from testlib import make_config
from vdsm import qemuimg
from vdsm import commands
from vdsm import exception

QEMU_IMG = qemuimg._qemuimg.cmd

CONFIG = make_config([('irs', 'qcow2_compat', '0.10')])


class InfoTests(TestCaseBase):

    def test_parse_error(self):
        def call(cmd, **kw):
            out = ["image: leaf.img", "invalid file format line"]
            return 0, out, []

        with MonkeyPatchScope([(commands, "execCmd", call)]):
            self.assertRaises(qemuimg.QImgError, qemuimg.info, 'leaf.img')

    def test_qemu1_no_backing_file(self):
        def call(cmd, **kw):
            out = ["image: leaf.img",
                   "file format: qcow2",
                   "virtual size: 1.0G (1073741824 bytes)",
                   "disk size: 196K",
                   "cluster_size: 65536"]
            return 0, out, []

        with MonkeyPatchScope([(commands, "execCmd", call)]):
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

        with MonkeyPatchScope([(commands, "execCmd", call)]):
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

        with MonkeyPatchScope([(commands, "execCmd", call)]):
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

        with MonkeyPatchScope([(commands, "execCmd", call)]):
            info = qemuimg.info('leaf.img')
            self.assertEquals('base.img', info['backingfile'])


class CreateTests(TestCaseBase):

    def test_no_format(self):
        def create(cmd, **kw):
            expected = [QEMU_IMG, 'create', 'image']
            self.assertEqual(cmd, expected)
            return 0, '', ''

        with MonkeyPatchScope([(commands, "execCmd", create)]):
            qemuimg.create('image')

    def test_zero_size(self):
        def create(cmd, **kw):
            expected = [QEMU_IMG, 'create', 'image', '0']
            self.assertEqual(cmd, expected)
            return 0, '', ''

        with MonkeyPatchScope([(commands, "execCmd", create)]):
            qemuimg.create('image', size=0)

    def test_qcow2_compat(self):

        def create(cmd, **kw):
            expected = [QEMU_IMG, 'create', '-f', 'qcow2', '-o', 'compat=0.10',
                        'image']
            self.assertEqual(cmd, expected)
            return 0, '', ''

        with MonkeyPatchScope([(qemuimg, 'config', CONFIG),
                               (commands, 'execCmd', create)]):
            qemuimg.create('image', format='qcow2')

    def test_invalid_config(self):
        config = make_config([('irs', 'qcow2_compat', '1.2')])
        with MonkeyPatchScope([(qemuimg, 'config', config)]):
            with self.assertRaises(exception.InvalidConfiguration):
                qemuimg.create('image', format='qcow2')


class ConvertTests(TestCaseBase):

    def test_no_format(self):
        def convert(cmd, **kw):
            expected = [QEMU_IMG, 'convert', '-p', '-t', 'none', '-T', 'none',
                        'src', 'dst']
            self.assertEqual(cmd, expected)

        with MonkeyPatchScope([(qemuimg, 'QemuImgOperation', convert)]):
            qemuimg.convert('src', 'dst')

    def test_qcow2_compat(self):
        def convert(cmd, **kw):
            expected = [QEMU_IMG, 'convert', '-p', '-t', 'none', '-T', 'none',
                        'src', '-O', 'qcow2', '-o', 'compat=0.10', 'dst']
            self.assertEqual(cmd, expected)

        with MonkeyPatchScope([(qemuimg, 'config', CONFIG),
                               (qemuimg, 'QemuImgOperation', convert)]):
            qemuimg.convert('src', 'dst', dstFormat='qcow2')

    def test_qcow2_no_backing_file(self):
        def convert(cmd, **kw):
            expected = [QEMU_IMG, 'convert', '-p', '-t', 'none', '-T', 'none',
                        'src', '-O', 'qcow2', '-o', 'compat=0.10', 'dst']
            self.assertEqual(cmd, expected)

        with MonkeyPatchScope([(qemuimg, 'config', CONFIG),
                               (qemuimg, 'QemuImgOperation', convert)]):
            qemuimg.convert('src', 'dst', dstFormat='qcow2')

    def test_qcow2_backing_file(self):
        def convert(cmd, **kw):
            expected = [QEMU_IMG, 'convert', '-p', '-t', 'none', '-T', 'none',
                        'src', '-O', 'qcow2', '-o',
                        'compat=0.10,backing_file=bak', 'dst']
            self.assertEqual(cmd, expected)

        with MonkeyPatchScope([(qemuimg, 'config', CONFIG),
                               (qemuimg, 'QemuImgOperation', convert)]):
            qemuimg.convert('src', 'dst', dstFormat='qcow2',
                            backing='bak')

    def test_qcow2_backing_format(self):
        def convert(cmd, **kw):
            expected = [QEMU_IMG, 'convert', '-p', '-t', 'none', '-T', 'none',
                        'src', '-O', 'qcow2', '-o', 'compat=0.10', 'dst']
            self.assertEqual(cmd, expected)

        with MonkeyPatchScope([(qemuimg, 'config', CONFIG),
                               (qemuimg, 'QemuImgOperation', convert)]):
            qemuimg.convert('src', 'dst', dstFormat='qcow2',
                            backingFormat='qcow2')

    def test_qcow2_backing_file_and_format(self):
        def convert(cmd, **kw):
            expected = [QEMU_IMG, 'convert', '-p', '-t', 'none', '-T', 'none',
                        'src', '-O', 'qcow2', '-o',
                        'compat=0.10,backing_file=bak,backing_fmt=qcow2',
                        'dst']
            self.assertEqual(cmd, expected)

        with MonkeyPatchScope([(qemuimg, 'config', CONFIG),
                               (qemuimg, 'QemuImgOperation', convert)]):
            qemuimg.convert('src', 'dst', dstFormat='qcow2',
                            backing='bak', backingFormat='qcow2')


class CheckTests(TestCaseBase):

    def test_offset_with_stats(self):
        def call(cmd, **kw):
            out = ["No errors were found on the image.",
                   "65157/98304 = 66.28% allocated, 0.00% fragmented, 0.00% "
                   "compressed clusters",
                   "Image end offset: 4271243264"]
            return 0, out, []

        with MonkeyPatchScope([(commands, "execCmd", call)]):
            check = qemuimg.check('unused')
            self.assertEquals(4271243264, check['offset'])

    def test_offset_without_stats(self):
        def call(cmd, **kw):
            out = ["No errors were found on the image.",
                   "Image end offset: 4271243264"]
            return 0, out, []

        with MonkeyPatchScope([(commands, "execCmd", call)]):
            check = qemuimg.check('unused')
            self.assertEquals(4271243264, check['offset'])

    def test_offset_no_match(self):
        def call(cmd, **kw):
            out = ["All your base are belong to us."]
            return 0, out, []

        with MonkeyPatchScope([(commands, "execCmd", call)]):
            self.assertRaises(qemuimg.QImgError, qemuimg.check, 'unused')


@expandPermutations
class QemuImgProgressTests(TestCaseBase):
    PROGRESS_FORMAT = "    (%.2f/100%%)\r"

    @staticmethod
    def _progress_iterator():
        for value in xrange(0, 10000, 1):
            yield value / 100.0

    def test_failure(self):
        p = qemuimg.QemuImgOperation(['false'])
        self.assertRaises(qemuimg.QImgError, p.wait)

    def test_progress_simple(self):
        p = qemuimg.QemuImgOperation(['true'])

        for progress in self._progress_iterator():
            p._recvstdout(self.PROGRESS_FORMAT % progress)
            self.assertEquals(p.progress, progress)

        p.wait()
        self.assertEquals(p.finished, True)

    @permutations([
        (("    (1/100%)\r", "    (2/100%)\r"), (1, 2)),
        (("    (1/10", "0%)\r    (2/10"),      (0, 1)),
        (("    (1/10", "0%)\r    (2/100%)\r"), (0, 2)),
    ])
    def test_partial(self, output_list, progress_list):
        p = qemuimg.QemuImgOperation(['true'])

        for output, progress in zip(output_list, progress_list):
            p._recvstdout(output)
            self.assertEquals(p.progress, progress)
        p.wait()
        self.assertEquals(p.finished, True)

    def test_progress_batch(self):
        p = qemuimg.QemuImgOperation(['true'])

        p._recvstdout(
            (self.PROGRESS_FORMAT % 10.00) +
            (self.PROGRESS_FORMAT % 25.00) +
            (self.PROGRESS_FORMAT % 33.33))

        self.assertEquals(p.progress, 33.33)

        p.wait()
        self.assertEquals(p.finished, True)

    def test_unexpected_output(self):
        p = qemuimg.QemuImgOperation(['true'])

        self.assertRaises(ValueError, p._recvstdout, 'Hello World\r')

        p._recvstdout('Hello ')
        self.assertRaises(ValueError, p._recvstdout, 'World\r')

        p.wait()
        self.assertEquals(p.finished, True)
