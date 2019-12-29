#
# Copyright IBM Corp. 2012
# Copyright 2013-2019 Red Hat, Inc.
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

from __future__ import absolute_import
from __future__ import division

import libvirt
from six.moves import zip

from vdsm.common import define
from vdsm.common import exception
from vdsm.common import hooks
from vdsm.common import libvirtconnection
from vdsm.common import password
from vdsm.common import response
from vdsm.common.units import MiB
from vdsm.config import config
from vdsm.virt import saslpasswd2
from vdsm.virt import virdomain
from vdsm.virt import vmexitreason
from vdsm.virt.vmdevices import hwclass
from vdsm.virt.vmdevices import storage

from monkeypatch import MonkeyPatch, MonkeyPatchScope
from testlib import XMLTestCase
from testlib import permutations, expandPermutations
import vmfakelib as fake

from testValidation import brokentest


_VM_PARAMS = {
    'displayPort': -1,
    'displaySecurePort': -1,
    'display': 'qxl',
    'displayIp': '127.0.0.1',
    'vmType': 'kvm',
    'memSize': 1024
}


_TICKET_PARAMS = {
    'userName': 'admin',
    'userId': 'fdfc627c-d875-11e0-90f0-83df133b58cc'
}


_GRAPHICS_DEVICE_PARAMS = {
    'deviceType': hwclass.GRAPHICS,
    'password': password.ProtectedPassword('12345678'),
    'ttl': 0,
    'existingConnAction': 'disconnect',
    'params': _TICKET_PARAMS
}


@expandPermutations
class TestVmOperations(XMLTestCase):
    # just numbers, no particular meaning
    UPDATE_OFFSETS = [-3200, 3502, -2700, 3601]
    BASE_OFFSET = 42

    VNC_DEVICE = {'type': 'graphics', 'device': 'vnc', 'port': '-1'}
    SPICE_DEVICE = {'type': 'graphics', 'device': 'spice', 'port': '-1'}

    GRAPHIC_DEVICES = [
        SPICE_DEVICE,
        VNC_DEVICE,
    ]

    @MonkeyPatch(libvirtconnection, 'get', lambda x: fake.Connection())
    @permutations([[define.NORMAL], [define.ERROR]])
    def testTimeOffsetNotPresentByDefault(self, exitCode):
        with fake.VM() as testvm:
            testvm.setDownStatus(exitCode, vmexitreason.GENERIC_ERROR)
            self.assertFalse('timeOffset' in testvm.getStats())

    @MonkeyPatch(libvirtconnection, 'get', lambda x: fake.Connection())
    @permutations([[define.NORMAL], [define.ERROR]])
    def testTimeOffsetRoundtrip(self, exitCode):
        with fake.VM({'timeOffset': self.BASE_OFFSET}) as testvm:
            testvm.setDownStatus(exitCode, vmexitreason.GENERIC_ERROR)
            self.assertEqual(testvm.getStats()['timeOffset'],
                             self.BASE_OFFSET)

    @MonkeyPatch(libvirtconnection, 'get', lambda x: fake.Connection())
    @permutations([[define.NORMAL], [define.ERROR]])
    def testTimeOffsetRoundtriupAcrossInstances(self, exitCode):
        # bz956741
        lastOffset = 0
        for offset in self.UPDATE_OFFSETS:
            with fake.VM({'timeOffset': lastOffset}) as testvm:
                testvm.onRTCUpdate(offset)
                testvm.setDownStatus(exitCode, vmexitreason.GENERIC_ERROR)
                vmOffset = testvm.getStats()['timeOffset']
                self.assertEqual(vmOffset, str(lastOffset + offset))
                # the field in getStats is str, not int
                lastOffset = int(vmOffset)

    @MonkeyPatch(libvirtconnection, 'get', lambda x: fake.Connection())
    @permutations([[define.NORMAL], [define.ERROR]])
    def testTimeOffsetUpdateIfAbsent(self, exitCode):
        # bz956741 (-like, simpler case)
        with fake.VM() as testvm:
            for offset in self.UPDATE_OFFSETS:
                testvm.onRTCUpdate(offset)
            # beware of type change!
            testvm.setDownStatus(exitCode, vmexitreason.GENERIC_ERROR)
            self.assertEqual(testvm.getStats()['timeOffset'],
                             str(self.UPDATE_OFFSETS[-1]))

    @MonkeyPatch(libvirtconnection, 'get', lambda x: fake.Connection())
    @permutations([[define.NORMAL], [define.ERROR]])
    def testTimeOffsetUpdateIfPresent(self, exitCode):
        with fake.VM({'timeOffset': self.BASE_OFFSET}) as testvm:
            for offset in self.UPDATE_OFFSETS:
                testvm.onRTCUpdate(offset)
            # beware of type change!
            testvm.setDownStatus(exitCode, vmexitreason.GENERIC_ERROR)
            self.assertEqual(testvm.getStats()['timeOffset'],
                             str(self.BASE_OFFSET + self.UPDATE_OFFSETS[-1]))

    def testUpdateSingleDeviceGraphics(self):
        devXmls = (
            '<graphics connected="disconnect" passwd="12345678"'
            ' port="5900" type="spice"/>',
            '<graphics passwd="12345678" port="5900" type="vnc"/>')
        for device, devXml in zip(self.GRAPHIC_DEVICES, devXmls):
            graphics_xml = ('<graphics type="{device}" port="{port}"/>'.
                            format(**device))
            domXml = '''
                <devices>
                    <graphics type="%s" port="5900" />
                </devices>''' % device['device']
            self._verifyDeviceUpdate(device, graphics_xml, domXml, devXml,
                                     _GRAPHICS_DEVICE_PARAMS)

    def testUpdateSingleDeviceGraphicsNoConnected(self):
        graphics_params = dict(_GRAPHICS_DEVICE_PARAMS)
        del graphics_params['existingConnAction']
        devXmls = (
            '<graphics passwd="12345678"'
            ' port="5900" type="spice"/>',
            '<graphics passwd="12345678" port="5900" type="vnc"/>')
        for device, devXml in zip(self.GRAPHIC_DEVICES, devXmls):
            graphics_xml = ('<graphics type="{device}" port="{port}"/>'.
                            format(**device))
            domXml = '''
                <devices>
                    <graphics type="%s" port="5900" />
                </devices>''' % device['device']
            self._verifyDeviceUpdate(device, graphics_xml, domXml, devXml,
                                     graphics_params)

    def testUpdateMultipleDeviceGraphics(self):
        devXmls = (
            '<graphics connected="disconnect" passwd="12345678"'
            ' port="5900" type="spice"/>',
            '<graphics passwd="12345678" port="5901" type="vnc"/>')
        domXml = '''
            <devices>
                <graphics type="spice" port="5900" />
                <graphics type="vnc" port="5901" />
            </devices>'''
        graphics_xml = ''
        for device in self.GRAPHIC_DEVICES:
            graphics_xml += ('<graphics type="{device}" port="{port}"/>'.
                             format(**device))
        for device, devXml in zip(self.GRAPHIC_DEVICES, devXmls):
            self._verifyDeviceUpdate(
                device, graphics_xml, domXml, devXml,
                _GRAPHICS_DEVICE_PARAMS)

    def _updateGraphicsDevice(self, testvm, device_type, graphics_params):
        def _check_ticket_params(domXML, conf, params):
            self.assertEqual(params, _TICKET_PARAMS)

        def _fake_set_vnc_pwd(username, pwd):
            pass

        with MonkeyPatchScope([(hooks, 'before_vm_set_ticket',
                                _check_ticket_params),
                               (saslpasswd2, 'set_vnc_password',
                                _fake_set_vnc_pwd)]):
            params = {'graphicsType': device_type}
            params.update(graphics_params)
            return testvm.updateDevice(params)

    def _verifyDeviceUpdate(self, device, allDevices, domXml, devXml,
                            graphics_params):
        with fake.VM(xmldevices=allDevices) as testvm:
            testvm._dom = fake.Domain(domXml)

            self._updateGraphicsDevice(testvm, device['device'],
                                       graphics_params)

            self.assertXMLEqual(testvm._dom.devXml, devXml)

    def testSetSaslPasswordInFips(self):
        graphics_params = dict(_GRAPHICS_DEVICE_PARAMS)
        del graphics_params['existingConnAction']
        device = self.GRAPHIC_DEVICES[1]  # VNC
        graphics_xml = ('<graphics type="%s" port="5900"/>' %
                        (device['device'],))
        device_xml = '<devices>%s</devices>''' % (graphics_xml,)

        with fake.VM(xmldevices=graphics_xml) as testvm:
            def _fake_set_vnc_pwd(username, pwd):
                testvm.pwd = pwd
                testvm.username = username

            testvm._dom = fake.Domain(device_xml)
            testvm.pwd = "invalid"
            params = {'graphicsType': device['device']}
            params.update(graphics_params)
            params['params']['fips'] = 'true'
            params['params']['vncUsername'] = 'vnc-123-456'

            with MonkeyPatchScope([(saslpasswd2, 'set_vnc_password',
                                    _fake_set_vnc_pwd)]):
                testvm.updateDevice(params)

            self.assertEqual(password.unprotect(params['password']),
                             testvm.pwd)
            self.assertEqual(params['params']['vncUsername'], testvm.username)

    def testClearSaslPasswordNoFips(self):
        graphics_params = dict(_GRAPHICS_DEVICE_PARAMS)
        del graphics_params['existingConnAction']
        device = self.GRAPHIC_DEVICES[1]  # VNC
        graphics_xml = ('<graphics type="%s" port="5900"/>' %
                        (device['device'],))
        device_xml = '<devices>%s</devices>''' % (graphics_xml,)

        with fake.VM(xmldevices=graphics_xml) as testvm:
            def _fake_remove_pwd(username):
                testvm.username = username

            testvm._dom = fake.Domain(device_xml)
            params = {'graphicsType': device['device']}
            params.update(graphics_params)
            params['params']['vncUsername'] = 'vnc-123-456'

            with MonkeyPatchScope([(saslpasswd2, 'remove_vnc_password',
                                    _fake_remove_pwd)]):
                testvm.updateDevice(params)

            self.assertEqual(params['params']['vncUsername'], testvm.username)

    def testDomainNotRunningWithoutDomain(self):
        with fake.VM() as testvm:
            self.assertFalse(testvm._isDomainRunning())

    def testDomainNotRunningByState(self):
        with fake.VM() as testvm:
            testvm._dom = fake.Domain(domState=libvirt.VIR_DOMAIN_SHUTDOWN)
            self.assertFalse(testvm._isDomainRunning())

    def testDomainIsRunning(self):
        with fake.VM() as testvm:
            testvm._dom = fake.Domain(domState=libvirt.VIR_DOMAIN_RUNNING)
            self.assertTrue(testvm._isDomainRunning())

    def testDomainIsReadyForCommands(self):
        with fake.VM() as testvm:
            testvm._dom = fake.Domain()
            self.assertTrue(testvm.isDomainReadyForCommands())

    @permutations([
        # code, text
        [libvirt.VIR_ERR_NO_DOMAIN, "Disappeared domain"],
        [libvirt.VIR_ERR_OPERATION_INVALID, "Operation invalid"],
    ])
    def testIgnoreKnownErrors(self, code, text):
        def _fail(*args):
            raise_libvirt_error(code, text)

        with fake.VM() as testvm:
            dom = fake.Domain()
            dom.controlInfo = _fail
            testvm._dom = dom
            self.assertFalse(testvm.isDomainReadyForCommands())

    def testDomainNoneNotReadyForCommands(self):
        with fake.VM() as testvm:
            self.assertFalse(testvm.isDomainReadyForCommands())

    def testReadyForCommandsRaisesLibvirtError(self):
        def _fail(*args):
            # anything != NO_DOMAIN is good
            raise_libvirt_error(libvirt.VIR_ERR_INTERNAL_ERROR,
                                "Fake internal error")

        with fake.VM() as testvm:
            dom = fake.Domain()
            dom.controlInfo = _fail
            testvm._dom = dom
            self.assertRaises(libvirt.libvirtError,
                              testvm.isDomainReadyForCommands)

    def testReadPauseCodeDomainRunning(self):
        with fake.VM() as testvm:
            testvm._dom = fake.Domain(domState=libvirt.VIR_DOMAIN_RUNNING)
            self.assertEqual(testvm._readPauseCode(), 'NOERR')

    def testReadPauseCodeDomainPausedCrash(self):
        with fake.VM() as testvm:
            # if paused for different reason we must not extend the disk
            # so anything else is ok
            dom = fake.Domain(domState=libvirt.VIR_DOMAIN_PAUSED,
                              domReason=libvirt.VIR_DOMAIN_PAUSED_CRASHED)
            testvm._dom = dom
            self.assertNotEqual(testvm._readPauseCode(), 'ENOSPC')

    def testReadPauseCodeDomainPausedENOSPC(self):
        with fake.VM() as testvm:
            dom = fake.Domain(domState=libvirt.VIR_DOMAIN_PAUSED,
                              domReason=libvirt.VIR_DOMAIN_PAUSED_IOERROR)
            dom.setDiskErrors({'vda': libvirt.VIR_DOMAIN_DISK_ERROR_NO_SPACE,
                               'hdc': libvirt.VIR_DOMAIN_DISK_ERROR_NONE})
            testvm._dom = dom
            self.assertEqual(testvm._readPauseCode(), 'ENOSPC')

    def testReadPauseCodeDomainPausedEIO(self):
        with fake.VM() as testvm:
            dom = fake.Domain(domState=libvirt.VIR_DOMAIN_PAUSED,
                              domReason=libvirt.VIR_DOMAIN_PAUSED_IOERROR)
            dom.setDiskErrors({'vda': libvirt.VIR_DOMAIN_DISK_ERROR_NONE,
                               'hdc': libvirt.VIR_DOMAIN_DISK_ERROR_UNSPEC})
            testvm._dom = dom
            self.assertEqual(testvm._readPauseCode(), 'EIO')

    @permutations([[1000, 24], [900, 0], [1200, -128]])
    def testSetCpuTuneQuote(self, quota, offset):
        with fake.VM() as testvm:
            # we need a different behaviour with respect to
            # plain fake.Domain. Seems simpler to just add
            # a new special-purpose trivial fake here.
            testvm._dom = ChangingSchedulerDomain(offset)
            testvm.setCpuTuneQuota(quota)
            self.assertEqual(quota + offset,
                             testvm._vcpuTuneInfo['vcpu_quota'])

    @permutations([[100000, 128], [150000, 0], [9999, -99]])
    def testSetCpuTunePeriod(self, period, offset):
        with fake.VM() as testvm:
            # same as per testSetCpuTuneQuota
            testvm._dom = ChangingSchedulerDomain(offset)
            testvm.setCpuTunePeriod(period)
            self.assertEqual(period + offset,
                             testvm._vcpuTuneInfo['vcpu_period'])

    @brokentest("sometimes on CI tries to connect to libvirt")
    @permutations([[libvirt.VIR_ERR_OPERATION_DENIED, 'setNumberOfCpusErr',
                    'Failed to set the number of cpus'],
                   [libvirt.VIR_ERR_NO_DOMAIN, 'noVM', None]])
    def testSetNumberOfVcpusFailed(self, virt_error, vdsm_error,
                                   error_message):
        def _fail(*args):
            raise_libvirt_error(virt_error, error_message)

        with MonkeyPatchScope([(hooks, 'before_set_num_of_cpus',
                                lambda: None)]):
            with fake.VM() as testvm:
                dom = fake.Domain()
                dom.setVcpusFlags = _fail
                testvm._dom = dom

                res = testvm.setNumberOfCpus(4)  # random value

                self.assertEqual(res, response.error(vdsm_error))

    def testUpdateDeviceGraphicsFailed(self):
        with fake.VM(devices=self.GRAPHIC_DEVICES) as testvm:
            message = 'fake timeout while setting ticket'
            device = 'spice'
            domXml = '''
                <devices>
                    <graphics type="%s" port="5900" />
                </devices>''' % device

            def _fail(*args):
                raise virdomain.TimeoutError(defmsg=message)

            domain = fake.Domain(domXml)
            domain.updateDeviceFlags = _fail
            testvm._dom = domain

            self.assertRaises(
                exception.SpiceTicketError,
                self._updateGraphicsDevice,
                testvm,
                device,
                _GRAPHICS_DEVICE_PARAMS
            )

    def testAcpiShutdownDisconnected(self):
        with fake.VM() as testvm:
            testvm._dom = virdomain.Disconnected(vmid='testvm')
            self.assertTrue(response.is_error(testvm.acpiShutdown()))

    def testAcpiShutdownConnected(self):
        with fake.VM() as testvm:
            testvm._dom = fake.Domain(vmId='testvm')
            self.assertFalse(response.is_error(testvm.acpiShutdown()))

    def testAcpiRebootDisconnected(self):
        with fake.VM() as testvm:
            testvm._dom = virdomain.Disconnected(vmid='testvm')
            self.assertTrue(response.is_error(testvm.acpiReboot()))

    def testAcpiRebootConnected(self):
        with fake.VM() as testvm:
            testvm._dom = fake.Domain(vmId='testvm')
            self.assertFalse(response.is_error(testvm.acpiReboot()))

    @permutations([
        # info, expected
        ({'readonly': True, 'diskType': storage.DISK_TYPE.BLOCK}, []),
        ({'readonly': True, 'diskType': storage.DISK_TYPE.FILE}, []),
        ({'readonly': False, 'diskType': storage.DISK_TYPE.FILE}, []),
        ({'readonly': False, 'diskType': storage.DISK_TYPE.FILE}, []),
        ({'readonly': False, 'diskType': storage.DISK_TYPE.BLOCK,
          'format': 'raw'}, []),
        ({'readonly': False, 'diskType': storage.DISK_TYPE.BLOCK}, ['vda']),
        ({'readonly': False, 'diskType': storage.DISK_TYPE.FILE,
          'diskReplicate': {
              'format': 'cow', 'diskType': storage.DISK_TYPE.BLOCK}
          },
         ['vda']),
    ])
    def testGetChunkedDrives(self, disk_conf, expected):
        with fake.VM() as testvm:
            vda = storage.Drive(self.log, **drive_config(**disk_conf))
            testvm._devices[hwclass.DISK] = [vda]

            drives = [drive.name for drive in testvm.getChunkedDrives()]
            self.assertEqual(drives, expected)


def _mem_committed(mem_size_mb):
    """
    Legacy algorithm found in oVirt <= 4.1
    """
    memory = mem_size_mb
    memory += config.getint('vars', 'guest_ram_overhead')
    return memory * MiB


class ChangingSchedulerDomain(object):

    def __init__(self, offset=10):
        self._offset = offset
        self._params = {}

    def setSchedulerParameters(self, params):
        for k, v in params.items():
            self._params[k] = int(v) + self._offset

    def schedulerParameters(self):
        return self._params


def raise_libvirt_error(code, message):
    err = libvirt.libvirtError(defmsg=message)
    err.err = [code]
    raise err


def drive_config(**kw):
    """ Return drive configuration updated from **kw """
    conf = {
        'device': 'disk',
        'format': 'cow',
        'iface': 'virtio',
        'index': '0',
        'path': '/path/to/volume',
        'propagateErrors': 'off',
        'shared': 'none',
        'type': 'disk',
    }
    conf.update(kw)
    return conf
