# SPDX-FileCopyrightText: Sasha Tomic <tomic80@gmail.com>
# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import os
import tempfile

import pytest

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
        pytest.skip('cannot execute %s' % fpath)


class AlignmentScanTests(TestCaseBase):

    def test_help_response(self):
        validate_virtalignscan_installed()
        rc, out, err = runScanArgs("--help")
        assert rc == 0
        assert err == []

    def test_bad_path(self):
        validate_virtalignscan_installed()
        with pytest.raises(VirtAlignError):
            scanImage("nonexistent-image-name")

    @slowtest
    @brokentest("libguestfs occasionally fails to open libvirt-sock")
    def test_nonaligned_image(self):
        validate_virtalignscan_installed()
        with tempfile.NamedTemporaryFile() as img:
            mkimage(img.name, aligned=False)
            msg = scanImage(img.name)
            assert msg[0][0] == '/dev/sda1'
            assert msg[0][3] == False
            assert msg[0][4] == 'bad (alignment < 4K)'

    @slowtest
    @brokentest("libguestfs occasionally fails to open libvirt-sock")
    def test_aligned_image(self):
        validate_virtalignscan_installed()
        with tempfile.NamedTemporaryFile() as img:
            mkimage(img.name, aligned=True)
            msg = scanImage(img.name)
            assert msg[0][0] == '/dev/sda1'
            assert msg[0][3] == True
            assert msg[0][4] == 'ok'
