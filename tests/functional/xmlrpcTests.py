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

import os
import tempfile
import time
from contextlib import contextmanager

from testrunner import VdsmTestCase as TestCaseBase
from nose.plugins.skip import SkipTest

from vdsm.config import config
from vdsm import vdscli
from storage.misc import execCmd
from vdsm.utils import CommandPath

if not config.getboolean('vars', 'xmlrpc_enable'):
    raise SkipTest("XML-RPC Bindings are disabled")

_mkinitrd = CommandPath("mkinird", "/usr/bin/mkinitrd")


@contextmanager
def kernelBootImages():
    kernelVer = os.uname()[2]
    kernelPath = "/boot/vmlinuz-" + kernelVer
    initramfsPath = "/boot/initramfs-%s.img" % kernelVer

    if not os.path.isfile(kernelPath):
        raise SkipTest("Can not locate kernel image for release %s" %
                       kernelVer)

    if os.path.isfile(initramfsPath):
        # There is an initramfs shipped with the distro, use it
        try:
            yield (kernelPath, initramfsPath)
        finally:
            pass
    else:
        # Generate an initramfs on demand, use it, delete it
        initramfsPath = genInitramfs(kernelVer)
        try:
            yield (kernelPath, initramfsPath)
        finally:
            os.unlink(initramfsPath)


def genInitramfs(kernelVer):
    fd, path = tempfile.mkstemp()
    cmd = [_mkinitrd.cmd, "-f", path, kernelVer]
    rc, out, err = execCmd(cmd, sudo=False)
    os.chmod(path, 0644)
    return path


class XMLRPCTest(TestCaseBase):
    UPSTATES = frozenset(('Up', 'Powering up', 'Running'))

    def setUp(self):
        isSSL = config.getboolean('vars', 'ssl')
        if isSSL and os.geteuid() != 0:
            raise SkipTest("Must be root to use SSL connection to server")
        self.s = vdscli.connect(useSSL=isSSL)

    def testGetCaps(self):
        r = self.s.getVdsCapabilities()
        self.assertVdsOK(r)

    def assertVmUp(self, vmid):
        r = self.s.getVmStats(vmid)
        self.assertVdsOK(r)
        self.myAssertIn(r['statsList'][0]['status'], self.UPSTATES)

    def assertGuestUp(self, vmid):
        r = self.s.getVmStats(vmid)
        self.assertVdsOK(r)
        self.assertEquals(r['statsList'][0]['status'], 'Up')

    def myAssertIn(self, member, container, msg=None):
        "Poor man's reimplementation of Python2.7's unittest.assertIn"

        if hasattr(self, 'assertIn'):
            return self.assertIn(member, container, msg)

        if msg is None:
            msg = '%r not found in %r' % (member, container)

        self.assertTrue(member in container, msg)

    def retryAssert(self, assertion, retries, delay=1):
        '''retry an "assertion" every "delay" seconds for "retries" times.
        assertion should be a closure, raises AssertionError when assert fails.
        '''
        for t in xrange(retries):
            try:
                assertion()
                break
            except AssertionError:
                pass
            time.sleep(delay)
        else:
            assertion()

    def assertVdsOK(self, vdsResult):
        # code == 0 means OK
        self.assertEquals(vdsResult['status']['code'], 0)

    def skipNoKVM(self):
        r = self.s.getVdsCapabilities()
        self.assertVdsOK(r)
        if r['info']['kvmEnabled'] != 'true':
            raise SkipTest('KVM is not enabled')

    def testStartEmptyVM(self):
        self.skipNoKVM()

        VMID = '66666666-ffff-4444-bbbb-333333333333'

        r = self.s.create({'memSize': '100', 'display': 'vnc', 'vmId': VMID,
                           'vmName': 'foo'})
        self.assertVdsOK(r)
        try:
            self.retryAssert(lambda: self.assertVmUp(VMID), 20)
        finally:
            # FIXME: if the server dies now, we end up with a leaked VM.
            r = self.s.destroy(VMID)
            self.assertVdsOK(r)

    def testStartSmallVM(self):
        self.skipNoKVM()

        def assertVMAndGuestUp():
            self.assertVmUp(VMID)
            self.assertGuestUp(VMID)

        VMID = '77777777-ffff-3333-bbbb-222222222222'

        with kernelBootImages() as (kernelPath, initramfsPath):
            conf = {'display': 'vnc',
                    'kernel': kernelPath,
                    'initrd': initramfsPath,
                    # The initramfs is generated by dracut. The following
                    # arguments will be interpreted by init scripts created by
                    # dracut.
                    'kernelArgs': 'rd.break=cmdline rd.shell rd.skipfsck',
                    'kvmEnable': 'true',
                    'memSize': '256',
                    'vmId': VMID,
                    'vmName': 'vdsm_testSmallVM',
                    'vmType': 'kvm'}

            try:
                self.assertVdsOK(self.s.create(conf))
                # wait 65 seconds for VM to come up until timeout
                self.retryAssert(assertVMAndGuestUp, 65, 1)
            finally:
                destroyResult = self.s.destroy(VMID)

        self.assertVdsOK(destroyResult)
