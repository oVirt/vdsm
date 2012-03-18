#!/usr/bin/python
#
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

import os
import tempfile
from nose.tools import eq_, raises, assert_not_equals
from nose.plugins.skip import SkipTest
from testrunner import VdsmTestCase as TestCaseBase
from storage.misc import execCmd
from alignmentScan import runScanArgs, scanImage, VirtAlignError


def mkimage(imagepath, aligned=True):
    cmd = ["/bin/dd", "if=/dev/zero", "of=%s" % imagepath, "bs=4K", "count=1K"]
    r, o, e = execCmd(cmd)
    assert r == 0
    cmd = ["/sbin/sfdisk", "-uS", "--force", imagepath]
    cmd_input = "128,,\n" if aligned == True else "1,,\n"
    r, o, e = execCmd(cmd, data=cmd_input)
    assert r == 0


def validate_virtalignscan_installed():
    fpath = "/usr/bin/virt-alignment-scan"
    if not (os.path.isfile(fpath) and os.access(fpath, os.X_OK)):
        raise SkipTest('cannot execute %s' % fpath)


class AlignmentScanTests(TestCaseBase):

    def test_help_response(self):
        validate_virtalignscan_installed()
        retcode, out, err = runScanArgs("--help")
        eq_(retcode, 0)
        eq_(err, [])
        out = "\n".join(out)
        assert_not_equals(out.find("/usr/bin/virt-alignment-scan: check " \
                               "alignment of virtual machine partitions"), -1)
        assert_not_equals(out.find("Usage:"), -1)
        assert_not_equals(out.find("Options:"), -1)
        assert_not_equals(out.find("-a|--add image       Add image"), -1)
        assert_not_equals(out.find("--help"), -1)

    @raises(VirtAlignError)
    def test_bad_path(self):
        validate_virtalignscan_installed()
        scanImage("nonexistent-image-name")

    def test_nonaligned_image(self):
        validate_virtalignscan_installed()
        with tempfile.NamedTemporaryFile() as img:
            mkimage(img.name, aligned=False)
            msg = scanImage(img.name)
            eq_(msg[0][0], '/dev/sda1')
            eq_(msg[0][3], False)
            eq_(msg[0][4], 'bad (alignment < 4K)')

    def test_aligned_image(self):
        validate_virtalignscan_installed()
        with tempfile.NamedTemporaryFile() as img:
            mkimage(img.name, aligned=True)
            msg = scanImage(img.name)
            eq_(msg[0][0], '/dev/sda1')
            eq_(msg[0][3], True)
            eq_(msg[0][4], 'ok')
