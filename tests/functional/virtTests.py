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
import math
import tempfile
from stat import S_IROTH
from functools import partial, wraps

from nose.plugins.skip import SkipTest

from testrunner import VdsmTestCase as TestCaseBase
from testrunner import permutations, expandPermutations

from vdsm.utils import CommandPath
import storageTests as storage
from storage.misc import execCmd
from storage.misc import RollbackContext

from utils import VdsProxy, SUCCESS

_mkinitrd = CommandPath("mkinitrd", "/usr/bin/mkinitrd")
_modprobe = CommandPath("modprobe",
                        "/usr/sbin/modprobe",  # Fedora, Ubuntu
                        "/sbin/modprobe")  # RHEL6
_kernelVer = os.uname()[2]
_kernelPath = "/boot/vmlinuz-" + _kernelVer
_initramfsPath = None
_initramfsPaths = ["/boot/initramfs-%s.img" % _kernelVer,  # Fedora, RHEL
                   "/boot/initrd.img-" + _kernelVer,  # Ubuntu
                   ]
_tmpinitramfs = False


def setUpModule():
    # global used in order to keep the iniramfs image persistent across
    # different VM tests
    global _initramfsPath
    global _tmpinitramfs
    _initramfsPath = _detectBootImages(_initramfsPaths)
    if _initramfsPath is None:
        _initramfsPath = _genInitramfs()
        _tmpinitramfs = True


def tearDownModule():
    if _tmpinitramfs:
        os.unlink(_initramfsPath)


def _detectBootImages(initramfsPaths):
    if not os.path.isfile(_kernelPath):
        raise SkipTest("Can not locate kernel image for release %s" %
                       _kernelVer)
    if not (os.stat(_kernelPath).st_mode & S_IROTH):
        raise SkipTest("qemu process can not read the file "
                       "%s" % _kernelPath)

    initramfsPaths = filter(os.path.isfile, initramfsPaths)
    if len(initramfsPaths) > 0:
        if (os.stat(initramfsPaths[0]).st_mode & S_IROTH):
            return initramfsPaths[0]
    return None


def _genInitramfs():
    fd, path = tempfile.mkstemp()
    cmd = [_mkinitrd.cmd, "-f", path, _kernelVer]
    rc, out, err = execCmd(cmd, sudo=False)
    os.chmod(path, 0o644)
    return path


def requireKVM(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        status, msg, result = self.vdsm.getVdsCapabilities()
        self.assertEqual(status, SUCCESS, msg)
        if result['kvmEnabled']:
            return method(self, *args, **kwargs)
        else:
            raise SkipTest('KVM is not enabled')
    return wrapped


class RunningVm(object):
    KERNEL_ARGS_DISTRO = {
        'fedora': 'rd.break=cmdline rd.shell rd.skipfsck',
        'rhel': 'rd.break=cmdline rd.shell rd.skipfsck'}

    def __init__(self, vdsm, vmDef, distro='fedora'):
        if distro.lower() not in self.KERNEL_ARGS_DISTRO:
            raise SkipTest("Don't know how to perform direct kernel boot for "
                           "%s" % distro)

        self._template = {'vmId': '11111111-abcd-2222-ffff-333333333333',
                          'vmName': 'vdsmKernelBootVM',
                          'display': 'vnc',
                          'kvmEnable': 'true',
                          'memSize': '256',
                          'vmType': 'kvm',
                          'kernelArgs': self.KERNEL_ARGS_DISTRO[distro],
                          'kernel': _kernelPath,
                          'initrd': _initramfsPath}
        self._template.update(vmDef)
        self._vdsm = vdsm

    def __enter__(self):
        self._id = self._template['vmId']
        self._vdsm.create(self._template)
        return self._id

    def __exit__(self, type, value, traceback):
        status, msg = self._vdsm.destroy(self._id)
        if status != SUCCESS:
            raise Exception(msg)


@expandPermutations
class VirtTest(TestCaseBase):
    UPSTATES = frozenset(('Up', 'Powering up', 'Running'))

    def setUp(self):
        self.vdsm = VdsProxy()

    def _getVmStatus(self, vmid):
        status, msg, result = self.vdsm.getVmStats(vmid)
        self.assertEqual(status, SUCCESS, msg)
        return result

    def assertQemuSetupComplete(self, vmid):
        result = self._getVmStatus(vmid)
        self.assertTrue(result['status'] != 'WaitForLaunch',
                        'VM is not booting!')

    def assertVmBooting(self, vmid):
        result = self._getVmStatus(vmid)
        self.assertTrue(result['status'] != 'Down',
                        'VM is not booting!')

    def assertVmUp(self, vmid):
        result = self._getVmStatus(vmid)
        self.assertTrue(result['status'] in self.UPSTATES)

    def assertGuestUp(self, vmid, targetUptime=0):
        result = self._getVmStatus(vmid)
        if targetUptime > 0:
            self.assertTrue(int(result['elapsedTime']) >= targetUptime)
        else:
            self.assertEquals(result['status'], 'Up')

    def _waitForStartup(self, vmid, targetUptime=0):
        self.retryAssert(partial(self.assertQemuSetupComplete, vmid),
                         timeout=10)
        self.retryAssert(partial(self.assertVmBooting, vmid),
                         timeout=3)
        self.retryAssert(partial(self.assertVmUp, vmid),
                         timeout=10)
        # 20 % more time on timeout
        self.retryAssert(partial(self.assertGuestUp, vmid, targetUptime),
                         timeout=math.ceil(targetUptime * 1.2))

    def testInitramfsReadable(self):
        # initialize initramfs paths
        self.assertFalse(_tmpinitramfs)

    @requireKVM
    def testSimpleVm(self):
        customization = {'vmId': '77777777-ffff-3333-bbbb-222222222222',
                         'vmName': 'testSimpleVm'}

        with RunningVm(self.vdsm, customization) as vm:
            self._waitForStartup(vm, 10)

    @requireKVM
    @permutations([['localfs'], ['iscsi'], ['nfs']])
    def testVmWithStorage(self, backendType):
        disk = storage.StorageTest()
        disk.setUp()
        conf = storage.storageLayouts[backendType]
        drives = disk.generateDriveConf(conf)
        customization = {'vmId': '88888888-eeee-ffff-aaaa-111111111111',
                         'vmName': 'testVmWithStorage' + backendType,
                         'drives': drives}

        with RollbackContext() as rollback:
            disk.createVdsmStorageLayout(conf, 3, rollback)
            with RunningVm(self.vdsm, customization) as vm:
                self._waitForStartup(vm, 10)

    @requireKVM
    @permutations([['hotplugNic'], ['virtioNic'], ['smartcard'],
                   ['hotplugDisk']])
    def testVmWithDevice(self, *devices):
        customization = {'vmId': '77777777-ffff-3333-bbbb-222222222222',
                         'vmName': 'testVm', 'devices': []}
        storageLayout = storage.storageLayouts['localfs']
        diskSpecs = storage.StorageTest.generateDriveConf(storageLayout)
        pciSpecs = {'bus': '0x00', 'domain': '0x0000',
                    'function': '0x0', 'type': 'pci'}
        ccidSpecs = {'slot': '0', 'controller': '0', 'type': 'ccid'}
        pciSlots = [dict({'slot': '0x01'}, **pciSpecs),
                    dict({'slot': '0x02'}, **pciSpecs),
                    dict({'slot': '0x03'}, **pciSpecs)]
        deviceDef = {'virtioNic': {'nicModel': 'virtio',
                                   'macAddr': '52:54:00:59:F5:3F',
                                   'network': '', 'address': pciSlots[2],
                                   'device': 'bridge', 'type': 'interface',
                                   'linkActive': True,
                                   'filter': 'no-mac-spoofing'},
                     'hotplugNic': {'vmId': customization['vmId'],
                                    'nic': {'nicModel': 'virtio',
                                            'macAddr': '52:54:00:59:F5:2F',
                                            'network': '',
                                            'address': pciSlots[1],
                                            'device': 'bridge',
                                            'type': 'interface',
                                            'linkActive': True,
                                            'filter': 'no-mac-spoofing'}},
                     'smartcard': {'type': 'smartcard', 'device': 'smartcard',
                                   'address': ccidSpecs,
                                   'alias': 'smartcard', 'specParams':
                                   {'type': 'spicevmc',
                                    'mode': 'passthrough'}},
                     'hotplugDisk': {'vmId': customization['vmId'],
                                     'drive': diskSpecs}}

        for device in devices:
            if 'hotplug' not in device:
                customization['devices'].append(deviceDef[device])

        with RunningVm(self.vdsm, customization) as vm:
            self._waitForStartup(vm, 10)

            if 'hotplugNic' in devices:
                self.retryAssert(partial(self.vdsm.hotplugNic,
                                         deviceDef['hotplugNic']), timeout=10)
                self.retryAssert(partial(self.vdsm.hotunplugNic,
                                         deviceDef['hotplugNic']), timeout=10)

            if 'hotplugDisk' in devices:
                self.retryAssert(partial(self.vdsm.hotplugDisk,
                                         deviceDef['hotplugDisk']), timeout=10)
                self.retryAssert(partial(self.vdsm.hotunplugDisk,
                                         deviceDef['hotplugDisk']), timeout=10)
