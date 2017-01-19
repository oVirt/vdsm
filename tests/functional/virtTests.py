#
# Copyright 2012-2016 Red Hat, Inc.
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
import platform
from stat import S_IROTH
from functools import partial, wraps

from nose.plugins.skip import SkipTest

from testlib import VdsmTestCase as TestCaseBase
from testlib import permutations, expandPermutations
from testlib import temporaryPath

import verify

from vdsm import cpuarch
from vdsm.utils import CommandPath
from vdsm.virt import vmstatus
from vdsm.storage.misc import execCmd

from utils import getProxy, SUCCESS


_mkinitrd = CommandPath("mkinitrd",
                        "/usr/bin/mkinitrd",  # Fedora
                        "/sbin/mkinitrd")  # RHEL 6.x, Centos 6.x
_kernelVer = os.uname()[2]
_kernelPath = "/boot/vmlinuz-" + _kernelVer
_initramfsPath = None
_initramfsPaths = ["/boot/initramfs-%s.img" % _kernelVer,  # Fedora, RHEL
                   "/boot/initrd.img-" + _kernelVer,  # Ubuntu
                   ]
_tmpinitramfs = False

VM_MINIMAL_UPTIME = 30

_GRAPHICS_FOR_ARCH = {cpuarch.PPC64LE: 'vnc',
                      cpuarch.X86_64: 'qxl'}


class VDSMConnectionError(Exception):
    pass


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

    def __init__(self, vdsm, vmDef, distro='fedora',
                 kernelPath=None, initramfsPath=None):
        if kernelPath is None:
            kernelPath = _kernelPath
        if initramfsPath is None:
            initramfsPath = _initramfsPath
        if distro.lower() not in self.KERNEL_ARGS_DISTRO:
            raise SkipTest("Don't know how to perform direct kernel boot for "
                           "%s" % distro)

        self._template = {'vmId': '11111111-abcd-2222-ffff-333333333333',
                          'vmName': 'vdsmKernelBootVM',
                          'kvmEnable': 'true',
                          'memSize': '256',
                          'vmType': 'kvm',
                          'kernelArgs': self.KERNEL_ARGS_DISTRO[distro],
                          'kernel': kernelPath,
                          'initrd': initramfsPath}
        self._template.update(vmDef)
        self._vdsm = vdsm

    def start(self):
        self._id = self._template['vmId']
        self._vdsm.create(self._template)
        return self._id

    def stop(self):
        status, msg = self._vdsm.destroy(self._id)
        if status != SUCCESS:
            raise VDSMConnectionError(msg)
        else:
            return SUCCESS

    def __enter__(self):
        return self.start()

    def __exit__(self, type, value, traceback):
        self.stop()


@expandPermutations
class VirtTestBase(TestCaseBase, verify.DeviceMixin):
    UPSTATES = frozenset((vmstatus.UP, vmstatus.POWERING_UP))

    def setUp(self):
        self.vdsm = getProxy()

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

    def assertVmDown(self, vmid):
        result = self._getVmStatus(vmid)
        self.assertEqual(result['status'], vmstatus.DOWN)

    def assertGuestUp(self, vmid, targetUptime=0):
        result = self._getVmStatus(vmid)
        if targetUptime > 0:
            self.assertTrue(int(result['elapsedTime']) >= targetUptime)
        else:
            self.assertEqual(result['status'], vmstatus.UP)

    def _waitForBoot(self, vmid):
        self.retryAssert(partial(self.assertQemuSetupComplete, vmid),
                         timeout=10)
        self.retryAssert(partial(self.assertVmBooting, vmid),
                         timeout=3)
        self.retryAssert(partial(self.assertVmUp, vmid),
                         timeout=10)

    def _waitForStartup(self, vmid, targetUptime=0):
        self._waitForBoot(vmid)
        # 20 % more time on timeout
        self.retryAssert(partial(self.assertGuestUp, vmid, targetUptime),
                         timeout=math.ceil(targetUptime * 1.2))

    def _waitForShutdown(self, vmid):
        self.retryAssert(partial(self.assertVmDown, vmid),
                         timeout=10)

    def _verifyDevices(self, vmId):
        status, msg, stats = self.vdsm.getVmList(vmId)
        self.assertEqual(status, SUCCESS, msg)

        self.verifyDevicesConf(conf=stats['devices'])


@expandPermutations
class VirtTest(VirtTestBase):
    @requireKVM
    def testSimpleVm(self):
        customization = {'vmId': '77777777-ffff-3333-bbbb-222222222222',
                         'vmName': 'testSimpleVm',
                         'devices': [],
                         'display': _GRAPHICS_FOR_ARCH[platform.machine()]}

        with RunningVm(self.vdsm, customization) as vm:
            self._waitForStartup(vm, VM_MINIMAL_UPTIME)
            self._verifyDevices(vm)

    @requireKVM
    def testComplexVm(self):
        customization = {'vmId': '77777777-ffff-3333-bbbb-222222222222',
                         'vmName': 'testComplexVm',
                         'display': _GRAPHICS_FOR_ARCH[platform.machine()],
                         'devices': [
                             {'type': 'sound', 'device': 'ac97'},
                             {'type': 'sound', 'device': 'ich6'},
                             {'type': 'video', 'device': 'qxl'},
                             {'type': 'video', 'device': 'qxl'},
                             {'type': 'graphics', 'device': 'spice'},
                             {'type': 'controller', 'device': 'virtio-serial'},
                             {'type': 'controller', 'device': 'usb'},
                             {'type': 'balloon', 'device': 'memballoon',
                              'specParams': {'model': 'virtio'}},
                             {'type': 'watchdog', 'device': 'wawtchdog'},
                             {'type': 'smartcard', 'device': 'smartcard',
                              'specParams': {'type': 'spicevmc',
                                             'mode': 'passthrough'}},
                             {'type': 'console', 'device': 'console'},
                             {'nicModel': 'virtio', 'device': 'bridge',
                              'macAddr': '52:54:00:59:F5:3F', 'network': '',
                              'type': 'interface'},
                             {'nicModel': 'virtio', 'device': 'bridge',
                              'macAddr': '52:54:00:59:FF:FF', 'network': '',
                              'type': 'interface'},
                         ]}

        status, msg, caps = self.vdsm.getVdsCapabilities()
        self.assertEqual(status, SUCCESS, msg)

        if caps['rngSources']:
            for _ in range(0, 2):
                customization['devices'].append(
                    {'type': 'rng', 'model': 'virtio', 'device': 'rng',
                     'specParams': {'source': caps['rngSources'][0]}})

        with RunningVm(self.vdsm, customization) as vm:
            self._waitForStartup(vm, VM_MINIMAL_UPTIME)
            self._verifyDevices(vm)

    @requireKVM
    def testHeadlessVm(self):
        customization = {'vmId': '77777777-ffff-3333-bbbb-222222222222',
                         'vmName': 'testHeadlessVm',
                         'devices': []}

        with RunningVm(self.vdsm, customization) as vm:
            self._waitForStartup(vm, VM_MINIMAL_UPTIME)

    @requireKVM
    @permutations([['hotplugNic'], ['virtioNic'], ['smartcard'],
                   ['hotplugDisk'], ['virtioRng']])
    def testVmWithDevice(self, *devices):
        customization = {'vmId': '77777777-ffff-3333-bbbb-222222222222',
                         'vmName': 'testVm', 'devices': [],
                         'display': _GRAPHICS_FOR_ARCH[platform.machine()]}
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
                                    'mode': 'passthrough'}}}

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
            self._verifyDevices(vm)

            if 'hotplugNic' in devices:
                self.retryAssert(partial(self.vdsm.hotplugNic,
                                         deviceDef['hotplugNic']), timeout=10)
                self.retryAssert(partial(self.vdsm.hotunplugNic,
                                         deviceDef['hotplugNic']), timeout=10)

    @permutations([['self'], ['specParams'], ['vmPayload']])
    def testVmWithCdrom(self, pathLocation):
        customization = {'vmId': '77777777-ffff-3333-bbbb-222222222222',
                         'devices': [],
                         'vmName':
                         'testVmWithCdrom_%s' % pathLocation,
                         'display': _GRAPHICS_FOR_ARCH[platform.machine()]}

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
                self._verifyDevices(vm)

                status, msg, stats = self.vdsm.getVmList(vm)
                self.assertEqual(status, SUCCESS, msg)
                for device in stats['devices']:
                    if device['device'] == 'cdrom':
                        if 'vmPayload' in cdrom['specParams']:
                            cdrom['path'] = vmPayloadName
                        self.assertEqual(device['path'], cdrom['path'])
                        self.assertEqual(device['specParams']['path'],
                                         cdrom['specParams']['path'])

    @permutations([['vnc'], ['spice']])
    def testVmDefinitionGraphics(self, displayType):
        devices = [{'type': 'graphics', 'device': displayType}]
        customization = {'vmId': '77777777-ffff-3333-cccc-222222222222',
                         'vmName': 'testGraphicsDeviceVm',
                         'devices': devices,
                         'display': 'qxlnc'}

        with RunningVm(self.vdsm, customization) as vm:
            self._waitForStartup(vm, VM_MINIMAL_UPTIME)
            self._verifyDevices(vm)

            status, msg, stats = self.vdsm.getVmStats(vm)
            self.assertEqual(status, SUCCESS, msg)
            self.assertEqual(stats['displayInfo'][0]['type'],
                             displayType)
            self.assertEqual(stats['displayType'],
                             'qxl' if displayType == 'spice' else 'vnc')

    @permutations([['vnc', 'spice'], ['spice', 'vnc']])
    def testVmDefinitionMultipleGraphics(self, primary, secondary):
        devices = [{'type': 'graphics', 'device': primary},
                   {'type': 'graphics', 'device': secondary}]
        customization = {'vmId': '77777777-ffff-3333-cccc-222222222222',
                         'vmName': 'testMultipleGraphicsDeviceVm',
                         'devices': devices,
                         'display': 'qxlnc'}

        with RunningVm(self.vdsm, customization) as vm:
            self._waitForStartup(vm, VM_MINIMAL_UPTIME)
            self._verifyDevices(vm)

            status, msg, stats = self.vdsm.getVmStats(vm)
            self.assertEqual(status, SUCCESS, msg)
            for dispInfo, dispType in zip(stats['displayInfo'],
                                          (primary, secondary)):
                self.assertEqual(dispInfo['type'], dispType)
            self.assertEqual(stats['displayType'],
                             'qxl' if primary == 'spice' else 'vnc')

    def testVmWithSla(self):
        customization = {'vmId': '99999999-aaaa-ffff-bbbb-111111111111',
                         'vmName': 'testVmWithSla',
                         'devices': [],
                         'display': _GRAPHICS_FOR_ARCH[platform.machine()]}

        with RunningVm(self.vdsm, customization) as vm:
            self._waitForStartup(vm, VM_MINIMAL_UPTIME)
            self._verifyDevices(vm)

            status, msg, stats = self.vdsm.getVmStats(vm)
            self.assertEqual(status, SUCCESS, msg)
            self.vdsm.updateVmPolicy(customization['vmId'],
                                     '50')
            self.assertEqual(status, SUCCESS, msg)
