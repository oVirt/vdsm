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

import textwrap
from functools import partial

from testlib import VdsmTestCase as TestCaseBase
import monkeypatch
from vdsm import qemuimg
from vdsm import utils


def fakeCmd(txt, *args, **kwargs):
    txt = textwrap.dedent(txt).split('\n')
    if txt[0] == '':
        txt.pop(0)
    return (0, txt, '')


outputParseError = """
foo bar
"""


outputQemu1NoBackingFile = """
image: base.img
file format: qcow2
virtual size: 1.0G (1073741824 bytes)
disk size: 196K
cluster_size: 65536
"""


outputQemu1Backing = """
image: leaf.img
file format: qcow2
virtual size: 1.0G (1073741824 bytes)
disk size: 196K
cluster_size: 65536
backing file: base.img (actual path: /tmp/base.img)
"""


outputQemu2NoBackingFile = """
image: base.img
file format: qcow2
virtual size: 1.0G (1073741824 bytes)
disk size: 196K
cluster_size: 65536
Format specific information:
    compat: 1.1
    lazy refcounts: false
"""


outputQemu2BackingNoCluster = """
image: leaf.img
file format: qcow2
virtual size: 1.0G (1073741824 bytes)
disk size: 196K
backing file: base.img (actual path: /tmp/base.img)
Format specific information:
    compat: 1.1
    lazy refcounts: false
"""


class qemuimgTests(TestCaseBase):
    @monkeypatch.MonkeyPatch(utils, 'execCmd',
                             partial(fakeCmd, outputParseError))
    def testParseError(self):
        self.assertRaises(qemuimg.QImgError, qemuimg.info, 'unused')

    @monkeypatch.MonkeyPatch(utils, 'execCmd',
                             partial(fakeCmd, outputQemu1NoBackingFile))
    def testQemu1NoBackingFile(self):
        info = qemuimg.info('unused')
        self.assertNotIn('backingfile', info)

    @monkeypatch.MonkeyPatch(utils, 'execCmd',
                             partial(fakeCmd, outputQemu1Backing))
    def testQemu1Backing(self):
        info = qemuimg.info('unused')
        self.assertEquals('base.img', info['backingfile'])

    @monkeypatch.MonkeyPatch(utils, 'execCmd',
                             partial(fakeCmd, outputQemu2NoBackingFile))
    def testQemu2NoBackingFile(self):
        info = qemuimg.info('unused')
        self.assertEquals('qcow2', info['format'])
        self.assertEquals(1073741824, info['virtualsize'])
        self.assertEquals(65536, info['clustersize'])
        self.assertNotIn('backingfile', info)

    @monkeypatch.MonkeyPatch(utils, 'execCmd',
                             partial(fakeCmd, outputQemu2BackingNoCluster))
    def testQemu2BackingNoCluster(self):
        info = qemuimg.info('unused')
        self.assertEquals('base.img', info['backingfile'])
