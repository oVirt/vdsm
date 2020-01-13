#
# Copyright (c) 2016 Red Hat, Inc.
# Copyright (c) 2012, Sasha Tomic <tomic80@gmail.com>
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
import tempfile
from nose.tools import eq_, raises
from nose.plugins.skip import SkipTest
from vdsm.common.units import GiB
from vdsm.storage.misc import execCmd
from testlib import VdsmTestCase as TestCaseBase
from testValidation import brokentest, slowtest
from vdsm.alignmentScan import runScanArgs, scanImage, VirtAlignError


def mkimage(imagepath, aligned=True):
    open(imagepath, "wb").truncate(4 * GiB)
    cmd = ["/sbin/sfdisk", "-uS", "--force", imagepath]
    cmd_input = "128,,\n" if aligned else "1,,\n"
    rc, out, err = execCmd(cmd, data=cmd_input)
    assert rc == 0


def validate_virtalignscan_installed():
    fpath = "/usr/bin/virt-alignment-scan"
    if not (os.path.isfile(fpath) and os.access(fpath, os.X_OK)):
        raise SkipTest('cannot execute %s' % fpath)


class AlignmentScanTests(TestCaseBase):

    def test_help_response(self):
        validate_virtalignscan_installed()
        rc, out, err = runScanArgs("--help")
        eq_(rc, 0)
        eq_(err, [])

    @raises(VirtAlignError)
    def test_bad_path(self):
        validate_virtalignscan_installed()
        scanImage("nonexistent-image-name")

    @slowtest
    @brokentest("libguestfs occasionally fails to open libvirt-sock")
    def test_nonaligned_image(self):
        validate_virtalignscan_installed()
        with tempfile.NamedTemporaryFile() as img:
            mkimage(img.name, aligned=False)
            msg = scanImage(img.name)
            eq_(msg[0][0], '/dev/sda1')
            eq_(msg[0][3], False)
            eq_(msg[0][4], 'bad (alignment < 4K)')

    @slowtest
    @brokentest("libguestfs occasionally fails to open libvirt-sock")
    def test_aligned_image(self):
        validate_virtalignscan_installed()
        with tempfile.NamedTemporaryFile() as img:
            mkimage(img.name, aligned=True)
            msg = scanImage(img.name)
            eq_(msg[0][0], '/dev/sda1')
            eq_(msg[0][3], True)
            eq_(msg[0][4], 'ok')
