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
import stat

from nose.plugins.skip import SkipTest

from testrunner import VdsmTestCase as TestCaseBase
from testrunner import namedTemporaryDir
from storage.misc import execCmd
import storage.mount as mount
from testValidation import checkSudo
import monkeypatch

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
        with namedTemporaryDir() as mpath:
            # two nested with blocks to be python 2.6 friendly
            with createFloppyImage(FLOPPY_SIZE) as path:
                m = mount.Mount(path, mpath)
                m.mount(mntOpts="loop")
                try:
                    self.assertTrue(m.isMounted())
                finally:
                    m.umount()


class IterMountsPerfTests(TestCaseBase):
    line_fmt = ('%(fs_spec)s\t%(fs_file)s\t%(fs_vfstype)s'
                '\t%(fs_mntops)s\t%(fs_freq)s\t%(fs_passno)s\n')

    @classmethod
    def _createFiles(cls, path, size):
        mounts_path = os.path.join(path, 'mounts')
        mtab_path = os.path.join(path, 'mtab')

        with open(mounts_path, 'w') as mounts:
            with open(mtab_path, 'w') as mtab:
                for i in xrange(100):
                    mnt = mount.MountRecord('/dev/sda%d' % i,
                                            '/some/path/%d' % i,
                                            'btrfs',
                                            'rw,relatime',
                                            '0', '0')
                    mounts.write(cls.line_fmt % mnt._asdict())
                    mtab.write(cls.line_fmt % mnt._asdict())

                for i in xrange(size):
                    mounts_mnt = mount.MountRecord('/dev/loop%d' % i,
                                                   '/mnt/loop%d' % i,
                                                   'xfs',
                                                   'rw,foobar,errors=continue',
                                                   '0', '0')
                    mtab_mnt = mount.MountRecord('/images/loop%d.img' % i,
                                                 '/mnt/loop%d' % i,
                                                 'xfs',
                                                 'rw,loop=/dev/loop%d' % i,
                                                 '0', '0')
                    mounts.write(cls.line_fmt % mounts_mnt._asdict())
                    mtab.write(cls.line_fmt % mtab_mnt._asdict())

        return (mounts_path, mtab_path)

    def setUp(self):
        self._temp_dir = mkdtemp()
        mounts, mtab = self._createFiles(self._temp_dir, 1000)
        old_stat = os.stat

        def mock_stat(path):
            if path.startswith('/dev/loop'):
                return old_stat('/')
            return old_stat(path)
        self._patch = monkeypatch.Patch([(mount, '_PROC_MOUNTS_PATH', mounts),
                                         (mount, '_ETC_MTAB_PATH', mtab),
                                         (mount, '_SYS_DEV_BLOCK_PATH',
                                          self._temp_dir),
                                         (mount, '_loopFsSpecs', {}),
                                         (os, 'stat', mock_stat),
                                         (stat, 'S_ISBLK', lambda x: True)])
        self._patch.apply()

    def tearDown(self):
        shutil.rmtree(self._temp_dir)
        self._patch.revert()

    def test1000EntriesValidate(self):
        mounts = list(mount.iterMounts())
        for entry in mounts:
            mountpoint = entry.fs_file
            if mountpoint.startswith('/mnt/loop'):
                expected_spec = ('/images/loop%s.img' %
                                 mountpoint[len('/mnt/loop'):])
                self.assertEquals(entry.fs_spec, expected_spec)

    def test1000EntriesTwice(self):
        list(mount.iterMounts())
        list(mount.iterMounts())

    def testLookupWipe(self):
        with open(mount._PROC_MOUNTS_PATH) as f:
            old_mounts = f.read()
        with open(mount._ETC_MTAB_PATH) as f:
            old_mtab = f.read()

        self.assertEquals(len(list(mount.iterMounts())), 1100)
        mounts_mnt = mount.MountRecord('/dev/loop10001',
                                       '/mnt/loop10001',
                                       'xfs',
                                       'rw,foobar,errors=continue',
                                       '0', '0')
        mtab_mnt = mount.MountRecord('/images/loop10001.img',
                                     '/mnt/loop%d',
                                     'xfs',
                                     'rw,loop=/dev/loop10001',
                                     '0', '0')

        with open(mount._PROC_MOUNTS_PATH, 'a') as f:
            f.write(self.line_fmt % mounts_mnt._asdict())
        with open(mount._ETC_MTAB_PATH, 'a') as f:
            f.write(self.line_fmt % mtab_mnt._asdict())

        self.assertEquals(len(list(mount.iterMounts())), 1101)
        self.assertEquals(
            len(filter(lambda x: x.fs_spec == '/images/loop10001.img',
                       mount.iterMounts())),
            1)
        self.assertTrue('/dev/loop10001' in mount._getLoopFsSpecs())

        with open(mount._PROC_MOUNTS_PATH, 'w') as f:
            f.write(old_mounts)
        with open(mount._ETC_MTAB_PATH, 'w') as f:
            f.write(old_mtab)

        self.assertEquals(len(list(mount.iterMounts())), 1100)
        self.assertEquals(
            len(filter(lambda x: x.fs_spec == '/images/loop10001.img',
                       mount.iterMounts())),
            0)
        self.assertFalse('/dev/loop10001' in mount._getLoopFsSpecs())
