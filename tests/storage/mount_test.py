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
from __future__ import print_function

from contextlib import contextmanager
import errno
from tempfile import mkstemp
import os
import time

import pytest
import six

from vdsm.storage import mount
from vdsm.storage.misc import execCmd

from nose.plugins.skip import SkipTest

from testlib import VdsmTestCase
from testlib import namedTemporaryDir, temporaryPath
from testlib import expandPermutations, permutations
from testValidation import broken_on_ci
from testValidation import skipif
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


@expandPermutations
class TestMountEquality(VdsmTestCase):

    def test_eq_equal(self):
        m1 = mount.Mount("spec", "file")
        m2 = mount.Mount("spec", "file")
        self.assertTrue(m1 == m2, "%s should equal %s" % (m1, m2))

    def test_eq_subclass(self):
        class Subclass(mount.Mount):
            pass
        m1 = mount.Mount("spec", "file")
        m2 = Subclass("spec", "file")
        self.assertFalse(m1 == m2, "%s should not equal %s" % (m1, m2))

    @permutations([
        ("spec", "spec", "file1", "file2"),
        ("spec1", "spec2", "file", "file"),
    ])
    def test_eq_different(self, spec1, spec2, file1, file2):
        m1 = mount.Mount(spec1, file1)
        m2 = mount.Mount(spec2, file2)
        self.assertFalse(m1 == m2, "%s should not equal %s" % (m1, m2))

    def test_ne_equal(self):
        m1 = mount.Mount("spec", "file")
        m2 = mount.Mount("spec", "file")
        self.assertFalse(m1 != m2, "%s should equal %s" % (m1, m2))


@expandPermutations
class TestMountHash(VdsmTestCase):

    def test_equal_same_hash(self):
        m1 = mount.Mount("spec", "file")
        m2 = mount.Mount("spec", "file")
        self.assertEqual(hash(m1), hash(m2))

    def test_subclass_different_hash(self):
        class Subclass(mount.Mount):
            pass
        m1 = mount.Mount("spec", "file")
        m2 = Subclass("spec", "file")
        self.assertNotEqual(hash(m1), hash(m2))

    @permutations([
        ("spec", "spec", "file1", "file2"),
        ("spec1", "spec2", "file", "file"),
    ])
    def test_not_equal_different_hash(self, spec1, spec2, file1, file2):
        m1 = mount.Mount(spec1, file1)
        m2 = mount.Mount(spec2, file2)
        self.assertNotEqual(hash(m1), hash(m2))


@expandPermutations
class TestMount(VdsmTestCase):

    @skipif(six.PY3, "needs porting to python 3")
    @pytest.mark.skipif(os.geteuid() != 0, reason="requires root")
    @broken_on_ci("mount check fails after successful mount", name="TRAVIS_CI")
    def testLoopMount(self):
        with namedTemporaryDir() as mpath:
            # two nested with blocks to be python 2.6 friendly
            with createFloppyImage(FLOPPY_SIZE) as path:
                m = mount.Mount(path, mpath)
                m.mount(mntOpts="loop")
                try:
                    self.assertTrue(m.isMounted())
                finally:
                    m.umount()

    @skipif(six.PY3, "needs porting to python 3")
    @pytest.mark.skipif(os.geteuid() != 0, reason="requires root")
    @broken_on_ci("mount check fails after successful mount", name="TRAVIS_CI")
    def testSymlinkMount(self):
        with namedTemporaryDir() as root_dir:
            backing_image = os.path.join(root_dir, 'backing.img')
            link_to_image = os.path.join(root_dir, 'link_to_image')
            mountpoint = os.path.join(root_dir, 'mountpoint')
            with open(backing_image, 'w') as f:
                os.ftruncate(f.fileno(), 1024 ** 3)
            rc, out, err = execCmd(['/sbin/mkfs.ext2', "-F", backing_image],
                                   raw=True)
            if rc != 0:
                raise RuntimeError("Error creating filesystem: %s" % err)
            os.symlink(backing_image, link_to_image)
            os.mkdir(mountpoint)
            m = mount.Mount(link_to_image, mountpoint)
            m.mount(mntOpts="loop")
            try:
                self.assertTrue(m.isMounted())
            finally:
                m.umount()

    @permutations([
        # Only fs_spec matches
        ("server:/path", "/mnt/server:_other__path", False),

        # Only fs_file matches
        ("server:/other_path", "/mnt/server:_path", False),

        # Both fs_spec and fs_file don't match
        ("server:/other_path", "/mnt/server:_other__path", False),

        # Both match
        ("server:/path", "/mnt/server:_path", True),
    ])
    def test_is_mounted(self, fs_spec, fs_file, equality):
        """
        Verifies that both fs_spec and fs_file match the mounted target.
        """
        with fake_mounts([b"server:/path /mnt/server:_path nfs defaults 0 0"]):
            mnt = mount.Mount(fs_spec, fs_file)
            self.assertEqual(mnt.isMounted(), equality)

    @permutations([
        # NFS4 using fsid=0 - kernel display mount as server://path instead of
        # normalized server:/path
        (b"server://a/b /mnt/server:_a_b nfs defaults 0 0",),

        # Not seen yet, but it should work now
        (b"server:/a//b /mnt/server:_a_b nfs defaults 0 0",),
        (b"server:/a/b// /mnt/server:_a_b nfs defaults 0 0",),
    ])
    def test_is_mounted_normalize_kernel_mounts(self, mount_line):
        with fake_mounts([mount_line]):
            mnt = mount.Mount("server:/a/b", "/mnt/server:_a_b")
            self.assertTrue(mnt.isMounted())

    @skipif(six.PY3, "needs porting to python 3")
    def test_is_mounted_with_symlink(self):
        with namedTemporaryDir() as dir:
            file = os.path.join(dir, "file")
            open(file, "w").close()
            link_to_file = os.path.join(dir, "link_to_file")
            os.symlink(file, link_to_file)
            mountpoint = "/mnt/mountpoint"
            with fake_mounts([b"%s %s nfs defaults 0 0" %
                              (link_to_file, mountpoint)]):
                mnt = mount.Mount(link_to_file, mountpoint)
                self.assertTrue(mnt.isMounted())


@contextmanager
def fake_mounts(mount_lines):
    """
    This method gets a list of mount lines,
    fakes the /proc/mounts and /etc/mtab files
    using monkey patch with a temporary file,
    and cleans everything on the end of use.

    Usage example:
    with fake_mounts([mount_line_1, mount_line_2]):
        <do something with /proc/mounts or /etc/mtab>
    """
    data = b"".join(line + b"\n" for line in mount_lines)
    with temporaryPath(data=data) as fake_mounts:
        with monkeypatch.MonkeyPatchScope([
            (mount, '_PROC_MOUNTS_PATH', fake_mounts),
        ]):
            yield


class TestRemoteSdIsMounted(VdsmTestCase):

    @skipif(six.PY3, "needs porting to python 3")
    def test_is_mounted(self):
        with fake_mounts([b"server:/path "
                          b"/rhev/data-center/mnt/server:_path "
                          b"nfs4 defaults 0 0"]):
            self.assertTrue(mount.isMounted(
                            b"/rhev/data-center/mnt/server:_path"))

    @skipif(six.PY3, "needs porting to python 3")
    def test_is_mounted_deleted(self):
        with fake_mounts([b"server:/path "
                          br"/rhev/data-center/mnt/server:_path\040(deleted) "
                          b"nfs4 defaults 0 0"]):
            self.assertTrue(mount.isMounted(
                            b"/rhev/data-center/mnt/server:_path"))

    @skipif(six.PY3, "needs porting to python 3")
    def test_path_with_spaces(self):
        with fake_mounts(
                [br"server:/a\040b /mnt/server:_a\040b nfs4 opts 0 0"]):
            self.assertTrue(mount.isMounted(b"/mnt/server:_a b"))
            self.assertFalse(mount.isMounted(br"/mnt/server:_a\040b"))

    @skipif(six.PY3, "needs porting to python 3")
    def test_path_with_backslash(self):
        with fake_mounts(
                [br"server:/a\134040b /mnt/server:_a\134040b nfs4 opts 0 0"]):
            self.assertTrue(mount.isMounted(br"/mnt/server:_a\040b"))
            self.assertFalse(mount.isMounted(br"/mnt/server:_a\134040b"))

    @skipif(six.PY3, "needs porting to python 3")
    def test_is_not_mounted(self):
        with fake_mounts([b"server:/path "
                          b"/rhev/data-center/mnt/server:_path "
                          b"nfs4 defaults 0 0"]):
            self.assertFalse(mount.isMounted(
                             b"/rhev/data-center/mnt/server:_other_path"))


@expandPermutations
class TestIsMountedTiming(VdsmTestCase):

    @skipif(six.PY3, "needs porting to python 3")
    @pytest.mark.stress
    @permutations([[1], [50], [100], [1000]])
    def test_is_mounted(self, count):
        server = b"foobar.baz.qux.com:/var/lib/exports/%04d"
        mountpoint = (b"/rhev/data-center/mnt/foobar.baz.qux.com:_var_lib"
                      b"_exports_%04d")
        options = (b"rw,relatime,vers=3,rsize=524288,wsize=524288,namlen=255,"
                   b"soft,nosharecache,proto=tcp,timeo=600,retrans=6,sec=sys,"
                   b"mountaddr=10.35.0.102,mountvers=3,mountport=892,"
                   b"mountproto=udp,local_lock=none,addr=10.35.0.102")
        version = b"nfs"
        freq = b"0"
        passno = b"0"
        lines = []
        for i in range(count):
            line = b" ".join((server % i, mountpoint % i, options, version,
                              freq, passno))
            lines.append(line)
        with fake_mounts(lines):
            start = time.time()
            self.assertTrue(mount.isMounted(mountpoint % i))
            elapsed = time.time() - start
            print("%4d mounts: %f seconds" % (count, elapsed))
