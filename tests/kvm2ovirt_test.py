# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

from collections import namedtuple
from contextlib import contextmanager
from monkeypatch import MonkeyPatchScope
from vdsm.common import libvirtconnection
from vdsm import v2v

from testlib import VdsmTestCase as TestCaseBase, namedTemporaryDir
from testlib import permutations, expandPermutations
from v2v_testlib import VM_SPECS, MockVirDomain, MockVirConnect, FakeVolume
from vdsm import kvm2ovirt
import os
import uuid


KVM2OvirtEnv = namedtuple('KVM2OvirtEnv', ['password', 'destination'])


@contextmanager
def make_env():
    """
    creating a password and destination file for kvm2ovirt tests
    we use password file to pass the password to kvm2ovirt
    kvm2ovirt need a file (destination) to write the disk data that
    it copies from libvirt
    """
    with namedTemporaryDir() as base:
        passpath = os.path.join(base, 'passwd')
        with open(passpath, 'w') as tempfile:
            tempfile.write('password')

        destpath = os.path.join(base, 'dest')
        with open(destpath, 'wb') as tempfile:
            pass

        yield KVM2OvirtEnv(passpath, destpath)


@expandPermutations
class TestKvm2Ovirt(TestCaseBase):
    def setUp(self):
        self._vms = [MockVirDomain(*spec) for spec in VM_SPECS]

    def test_download_volume(self):
        conn = MockVirConnect(vms=self._vms)

        def connect(uri, username, password):
            return conn

        with MonkeyPatchScope([
            (libvirtconnection, 'open_connection', connect),
        ]), make_env() as env:
            args = ['kvm2ovirt',
                    '--uri', 'qemu+tcp://domain',
                    '--username', 'user',
                    '--password-file', env.password,
                    '--source', '/fake/source',
                    '--dest', env.destination,
                    '--storage-type', 'volume',
                    '--vm-name', self._vms[0].name(),
                    '--allocation', 'sparse']

            kvm2ovirt.main(args)

            with open(env.destination) as f:
                actual = f.read()
            self.assertEqual(actual, FakeVolume().data())

    def test_download_path(self):
        conn = MockVirConnect(vms=self._vms)

        def connect(uri, username, password):
            return conn

        with MonkeyPatchScope([
            (libvirtconnection, 'open_connection', connect),
        ]), make_env() as env:
            args = ['kvm2ovirt',
                    '--uri', 'qemu+tcp://domain',
                    '--username', 'user',
                    '--password-file', env.password,
                    '--source', '/fake/source',
                    '--dest', env.destination,
                    '--storage-type', 'path',
                    '--vm-name', self._vms[0].name(),
                    '--allocation', 'preallocated']

            kvm2ovirt.main(args)

            with open(env.destination) as f:
                actual = f.read()
            self.assertEqual(actual, FakeVolume().data())

    @permutations([
                  [None, None],
                  ['root', 'passwd'],
                  ])
    def test_common_download_file_username(self, username, passwd):
        conn = MockVirConnect(vms=self._vms)

        def connect(uri, username, password):
            return conn

        with MonkeyPatchScope([
            (libvirtconnection, 'open_connection', connect),
        ]), make_env() as env:
            vmInfo = {'vmName': self._vms[0].name()}
            kvm = v2v.KVMCommand('qemu+tcp://domain', username, passwd,
                                 vmInfo, uuid.uuid4(), None)
            if passwd:
                kvm._passwd_file = env.password
            kvm._source_images = lambda: (['/fake/source'], ['file'])
            kvm._dest_images = lambda: [env.destination]

            kvm2ovirt.main(kvm._command())

            with open(env.destination) as f:
                actual = f.read()
            self.assertEqual(actual, FakeVolume().data())
