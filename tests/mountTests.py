#
# Copyright 2012 Red Hat, Inc.
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

from contextlib import contextmanager
import errno
from tempfile import mkstemp, mkdtemp
import os
import shutil

from nose.plugins.skip import SkipTest

from testrunner import VdsmTestCase as TestCaseBase
from storage.misc import execCmd
import storage.mount as mount
from testValidation import checkSudo

FLOPPY_SIZE = (2 ** 20) * 4


@contextmanager
def createFloppyImage(size):
    fd, path = mkstemp()
    with os.fdopen(fd, "w") as f:
        f.seek(size)
        f.write('\0')

    try:
        rc, out, err = execCmd(['/sbin/mkfs.ext2', "-F", path])
    except OSError:
        try:
            rc, out, err = execCmd(['/usr/sbin/mkfs.ext2', "-F", path])
        except OSError as e:
            if e.errno == errno.ENOENT:
                raise SkipTest("cannot execute mkfs.ext2")
            raise

    if rc != 0:
        raise Exception("Could not format image", out, err)
    try:
        yield path
    finally:
        os.unlink(path)


class MountTests(TestCaseBase):
    def testLoopMount(self):
        checkSudo(["mount", "-o", "loop", "somefile", "target"])
        checkSudo(["umount", "target"])
        mpath = mkdtemp()
        with createFloppyImage(FLOPPY_SIZE) as path:
            m = mount.Mount(path, mpath)
            m.mount(mntOpts="loop")
            try:
                self.assertTrue(m.isMounted())
            finally:
                m.umount()
                shutil.rmtree(mpath)
