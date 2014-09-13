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
from vdsm import qemuimg
from vdsm import utils


class FakeExecCmd(object):

    def __init__(self, *calls):
        self.calls = list(calls)
        self.saved = None

    def __call__(self, cmd, **kw):
        call = self.calls.pop(0)
        return call(cmd, **kw)

    def __enter__(self):
        self.saved = utils.execCmd
        utils.execCmd = self

    def __exit__(self, t=None, v=None, tb=None):
        utils.execCmd = self.saved


class QemuimgTests(TestCaseBase):

    def test_parse_error(self):
        def call(cmd, **kw):
            out = ["image: leaf.img", "invalid file format line"]
            return 0, out, []

        with FakeExecCmd(call):
            self.assertRaises(qemuimg.QImgError, qemuimg.info, 'leaf.img')

    def test_qemu1_no_backing_file(self):
        def call(cmd, **kw):
            out = ["image: leaf.img",
                   "file format: qcow2",
                   "virtual size: 1.0G (1073741824 bytes)",
                   "disk size: 196K",
                   "cluster_size: 65536"]
            return 0, out, []

        with FakeExecCmd(call):
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

        with FakeExecCmd(call):
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

        with FakeExecCmd(call):
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

        with FakeExecCmd(call):
            info = qemuimg.info('leaf.img')
            self.assertEquals('base.img', info['backingfile'])


class QemuimgCreateTests(TestCaseBase):

    def test_no_format(self):

        def create_no_format(cmd, **kw):
            assert cmd == [qemuimg._qemuimg.cmd, 'create', 'image']
            return 0, '', ''

        with FakeExecCmd(create_no_format):
            qemuimg.create('image')

    def test_qcow2_compat_not_supported(self):

        def qcow2_compat_not_supported(cmd, **kw):
            assert cmd == [qemuimg._qemuimg.cmd, 'create', '-f', 'qcow2', '-o',
                           '?', '/dev/null']
            return 0, 'Supported options:\nsize ...\n', ''

        def create_qcow2_no_compat(cmd, **kw):
            assert cmd == [qemuimg._qemuimg.cmd, 'create', '-f', 'qcow2',
                           'image']
            return 0, '', ''

        with FakeExecCmd(qcow2_compat_not_supported, create_qcow2_no_compat):
            qemuimg.create('image', format='qcow2')

    def test_qcow2_compat_supported(self):

        def qcow2_compat_supported(cmd, **kw):
            assert cmd == [qemuimg._qemuimg.cmd, 'create', '-f', 'qcow2', '-o',
                           '?', '/dev/null']
            return 0, 'Supported options:\ncompat ...\n', ''

        def create_qcow2_compat(cmd, **kw):
            assert cmd == [qemuimg._qemuimg.cmd, 'create', '-f', 'qcow2', '-o',
                           'compat=0.10', 'image']
            return 0, '', ''

        with FakeExecCmd(qcow2_compat_supported, create_qcow2_compat):
            qemuimg.create('image', format='qcow2')
