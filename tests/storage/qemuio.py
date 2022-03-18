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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

"""
qemuio - wrapper for qemu-io tool

This module provides helpers for wiritng and verifying qcow2 files data.
"""

import subprocess
import time

from contextlib import contextmanager

from vdsm.common import commands
from vdsm.common import cmdutils


class VerificationError(AssertionError):
    pass


def write_pattern(path, format, offset=512, len=1024, pattern=5):
    write_cmd = 'write -P %d %d %d' % (pattern, offset, len)
    cmd = ['qemu-io', '-f', format, '-c', write_cmd, path]
    rc, out, err = commands.execCmd(cmd, raw=True)
    if rc != 0:
        raise cmdutils.Error(cmd, rc, out, err)


def verify_pattern(path, format, offset=512, len=1024, pattern=5):
    read_cmd = 'read -P %d -s 0 -l %d %d %d' % (pattern, len, offset, len)
    cmd = ['qemu-io', '-f', format, '-c', read_cmd, path]
    rc, out, err = commands.execCmd(cmd, raw=True)
    # Older qemu-io (2.10) used to exit with zero exit code and "Pattern
    # verification" error in stdout. In 2.12, non-zero code is returned when
    # pattern verification fails.
    if b"Pattern verification failed" in out:
        raise VerificationError(
            "Verification of volume %s failed. Pattern 0x%x not found at "
            "offset %s"
            % (path, pattern, offset))
    if rc != 0 or err != b"":
        raise cmdutils.Error(cmd, rc, out, err)


def abort(path):
    # Simulate qemu crash, opening the image for writing
    # and killing the process.
    subprocess.run(["qemu-io", "-c", "abort", path])


@contextmanager
def open(image, fmt, timeout=2.0):
    """
    Open image in write mode for testing access to active image.
    """
    cmd = [
        "qemu-io",
        "-c", f"open -o driver={fmt} {image}",
        "-c", "sleep 10000",
    ]
    p = subprocess.Popen(cmd)
    try:
        # Wait until image is locked.
        deadline = time.monotonic() + timeout
        while True:
            time.sleep(0.01)
            cmd = ["qemu-io", "-c", f"open -r -o driver={fmt} {image}"]
            if subprocess.run(cmd).returncode != 0:
                break

            if time.monotonic() >= deadline:
                raise RuntimeError(f"Timeout waiting until {image} is locked")

        yield
    finally:
        p.terminate()
        p.wait()
