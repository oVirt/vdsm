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
import logging
from stat import S_IROTH
from functools import partial, wraps

from nose.plugins.skip import SkipTest

from testrunner import VdsmTestCase as TestCaseBase
from testrunner import permutations, expandPermutations
from testrunner import temporaryPath

from vdsm.utils import CommandPath, RollbackContext
import storageTests as storage
from storage.misc import execCmd

from utils import VdsProxy, SUCCESS

from virt import vmstatus

_mkinitrd = CommandPath("mkinitrd",
                        "/usr/bin/mkinitrd",  # Fedora
                        "/sbin/mkinitrd")  # RHEL 6.x, Centos 6.x
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

VM_MINIMAL_UPTIME = 30


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
    logging.warning('Generating a temporary initramfs image')
    fd, path = tempfile.mkstemp()
    cmd = [_mkinitrd.cmd, "-f", path, _kernelVer]
    rc, out, err = execCmd(cmd)
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
    UPSTATES = frozenset((vmstatus.UP, vmstatus.POWERING_UP))

    def setUp(self):
        self.vdsm = VdsProxy()

    def _getVmStatus(self, vmid):
        status, msg, result = self.vdsm.getVmStats(vmid)
        self.assertEqual(status, SUCCESS, msg)
        return result

    def assertQemuSetupComplete(self, vmid):
        result = self._getVmStatus(vmid)
        self.assertTrue(result['status'] != vmstatus.WAIT_FOR_LAUNCH,
                        'VM is not booting!')

    def assertVmBooting(self, vmid):
        result = self._getVmStatus(vmid)
        self.assertTrue(result['status'] != vmstatus.DOWN,
                        'VM is not booting!')

    def assertVmUp(self, vmid):
        result = self._getVmStatus(vmid)
        self.assertIn(result['status'], self.UPSTATES)

    def assertGuestUp(self, vmid, targetUptime=0):
        result = self._getVmStatus(vmid)
        if targetUptime > 0:
            self.assertTrue(int(result['elapsedTime']) >= targetUptime)
        else:
            self.assertEquals(result['status'], vmstatus.UP)

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

    @requireKVM
    def testSimpleVm(self):
        customization = {'vmId': '77777777-ffff-3333-bbbb-222222222222',
                         'vmName': 'testSimpleVm'}

        with RunningVm(self.vdsm, customization) as vm:
            self._waitForStartup(vm, VM_MINIMAL_UPTIME)

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
                self._waitForStartup(vm, VM_MINIMAL_UPTIME)

    @requireKVM
    @permutations([['hotplugNic'], ['virtioNic'], ['smartcard'],
                   ['hotplugDisk'], ['virtioRng']])
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

        if 'virtioRng' in devices:
            status, msg, caps = self.vdsm.getVdsCapabilities()
            self.assertEqual(status, SUCCESS, msg)

            if not caps['rngSources']:
                raise SkipTest('No suitable rng source on host found')
            # we can safely pick any device as long, as it exists
            deviceDef['virtioRng'] = {'type': 'rng', 'model': 'virtio',
                                      'specParams': {'bytes': '1234',
                                                     'period': '20000',
                                                     'source':
                                                     caps['rngSources'][0]}}

        for device in devices:
            if 'hotplug' not in device:
                customization['devices'].append(deviceDef[device])

        with RunningVm(self.vdsm, customization) as vm:
            self._waitForStartup(vm, VM_MINIMAL_UPTIME)

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

    @permutations([['self'], ['specParams'], ['vmPayload']])
    def testVmWithCdrom(self, pathLocation):
        customization = {'vmId': '77777777-ffff-3333-bbbb-222222222222',
                         'devices': [],
                         'vmName':
                         'testVmWithCdrom_%s' % pathLocation}

        # echo -n testPayload | md5sum
        # d37e46c24c78b1aed33496107afdb44b
        vmPayloadName = ('/var/run/vdsm/payload/%s.'
                         'd37e46c24c78b1aed33496107afdb44b'
                         '.img' % customization['vmId'])

        cdrom = {'index': '2', 'iface': 'ide', 'specParams':
                 {}, 'readonly': 'true', 'path':
                 '', 'device': 'cdrom', 'shared':
                 'false', 'type': 'disk'}

        with temporaryPath(0o666) as path:
            cdromPaths = {'self': {'path': path, 'specParams':
                                   {'path': '/dev/null'}},
                          'specParams': {'path': '', 'specParams':
                                         {'path': path}},
                          'vmPayload': {'path': '', 'specParams':
                                        {'path': '',
                                         'vmPayload': {'volId': 'testConfig',
                                                       'file': {'testPayload':
                                                                ''}}}}}
            cdrom.update(cdromPaths[pathLocation])
            customization['devices'].append(cdrom)

            with RunningVm(self.vdsm, customization) as vm:
                self._waitForStartup(vm, 10)

                status, msg, stats = self.vdsm.getVmList(vm)
                self.assertEqual(status, SUCCESS, msg)
                for device in stats['devices']:
                    if device['device'] == 'cdrom':
                        if 'vmPayload' in cdrom['specParams']:
                            cdrom['path'] = vmPayloadName
                        self.assertEqual(device['path'], cdrom['path'])
                        self.assertEqual(device['specParams']['path'],
                                         cdrom['specParams']['path'])
