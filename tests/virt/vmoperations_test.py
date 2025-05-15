# SPDX-FileCopyrightText: 2012 IBM Corp.
# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import libvirt

from vdsm import numa
from vdsm.common import define
from vdsm.common import exception
from vdsm.common import hooks
from vdsm.common import libvirtconnection
from vdsm.common import password
from vdsm.common import response
from vdsm.common.units import MiB
from vdsm.config import config
from vdsm.virt import cpumanagement
from vdsm.virt import saslpasswd2
from vdsm.virt import virdomain
from vdsm.virt import vmexitreason
from vdsm.virt.vmdevices import hwclass

from monkeypatch import MonkeyPatch, MonkeyPatchScope
from testlib import XMLTestCase
from testlib import permutations, expandPermutations

from testValidation import brokentest

from . import vmfakelib as fake
import pytest


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
            assert 'timeOffset' not in testvm.getStats()

    @MonkeyPatch(libvirtconnection, 'get', lambda x: fake.Connection())
    @permutations([[define.NORMAL], [define.ERROR]])
    def testTimeOffsetRoundtrip(self, exitCode):
        with fake.VM({'timeOffset': self.BASE_OFFSET}) as testvm:
            testvm.setDownStatus(exitCode, vmexitreason.GENERIC_ERROR)
            assert testvm.getStats()['timeOffset'] == self.BASE_OFFSET

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
                assert vmOffset == str(lastOffset + offset)
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
            assert testvm.getStats()['timeOffset'] == \
                str(self.UPDATE_OFFSETS[-1])

    @MonkeyPatch(libvirtconnection, 'get', lambda x: fake.Connection())
    @permutations([[define.NORMAL], [define.ERROR]])
    def testTimeOffsetUpdateIfPresent(self, exitCode):
        with fake.VM({'timeOffset': self.BASE_OFFSET}) as testvm:
            for offset in self.UPDATE_OFFSETS:
                testvm.onRTCUpdate(offset)
            # beware of type change!
            testvm.setDownStatus(exitCode, vmexitreason.GENERIC_ERROR)
            assert testvm.getStats()['timeOffset'] == \
                str(self.BASE_OFFSET + self.UPDATE_OFFSETS[-1])

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
            assert params == _TICKET_PARAMS

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
        device_xml = '<devices>%s</devices>' % (graphics_xml,)

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

            assert password.unprotect(params['password']) == \
                testvm.pwd
            assert params['params']['vncUsername'] == testvm.username

    def testClearSaslPasswordNoFips(self):
        graphics_params = dict(_GRAPHICS_DEVICE_PARAMS)
        del graphics_params['existingConnAction']
        device = self.GRAPHIC_DEVICES[1]  # VNC
        graphics_xml = ('<graphics type="%s" port="5900"/>' %
                        (device['device'],))
        device_xml = '<devices>%s</devices>' % (graphics_xml,)

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

            assert params['params']['vncUsername'] == testvm.username

    def testDomainNotRunningWithoutDomain(self):
        with fake.VM() as testvm:
            assert not testvm.isDomainRunning()

    def testDomainNotRunningByState(self):
        with fake.VM() as testvm:
            testvm._dom = fake.Domain(domState=libvirt.VIR_DOMAIN_SHUTDOWN)
            assert not testvm.isDomainRunning()

    def testDomainIsRunning(self):
        with fake.VM() as testvm:
            testvm._dom = fake.Domain(domState=libvirt.VIR_DOMAIN_RUNNING)
            assert testvm.isDomainRunning()

    def testDomainIsReadyForCommands(self):
        with fake.VM() as testvm:
            testvm._dom = fake.Domain()
            assert testvm.isDomainReadyForCommands()

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
            assert not testvm.isDomainReadyForCommands()

    def testDomainNoneNotReadyForCommands(self):
        with fake.VM() as testvm:
            assert not testvm.isDomainReadyForCommands()

    def testReadyForCommandsRaisesLibvirtError(self):
        def _fail(*args):
            # anything != NO_DOMAIN is good
            raise_libvirt_error(libvirt.VIR_ERR_INTERNAL_ERROR,
                                "Fake internal error")

        with fake.VM() as testvm:
            dom = fake.Domain()
            dom.controlInfo = _fail
            testvm._dom = dom
            with pytest.raises(libvirt.libvirtError):
                testvm.isDomainReadyForCommands()

    def testReadPauseCodeDomainRunning(self):
        with fake.VM() as testvm:
            testvm._dom = fake.Domain(domState=libvirt.VIR_DOMAIN_RUNNING)
            assert testvm._readPauseCode() == 'NOERR'

    def testReadPauseCodeDomainPausedCrash(self):
        with fake.VM() as testvm:
            # if paused for different reason we must not extend the disk
            # so anything else is ok
            dom = fake.Domain(domState=libvirt.VIR_DOMAIN_PAUSED,
                              domReason=libvirt.VIR_DOMAIN_PAUSED_CRASHED)
            testvm._dom = dom
            assert testvm._readPauseCode() != 'ENOSPC'

    def testReadPauseCodeDomainPausedENOSPC(self):
        with fake.VM() as testvm:
            dom = fake.Domain(domState=libvirt.VIR_DOMAIN_PAUSED,
                              domReason=libvirt.VIR_DOMAIN_PAUSED_IOERROR)
            dom.setDiskErrors({'vda': libvirt.VIR_DOMAIN_DISK_ERROR_NO_SPACE,
                               'hdc': libvirt.VIR_DOMAIN_DISK_ERROR_NONE})
            testvm._dom = dom
            assert testvm._readPauseCode() == 'ENOSPC'

    def testReadPauseCodeDomainPausedEIO(self):
        with fake.VM() as testvm:
            dom = fake.Domain(domState=libvirt.VIR_DOMAIN_PAUSED,
                              domReason=libvirt.VIR_DOMAIN_PAUSED_IOERROR)
            dom.setDiskErrors({'vda': libvirt.VIR_DOMAIN_DISK_ERROR_NONE,
                               'hdc': libvirt.VIR_DOMAIN_DISK_ERROR_UNSPEC})
            testvm._dom = dom
            assert testvm._readPauseCode() == 'EIO'

    @permutations([[1000, 24], [900, 0], [1200, -128]])
    def testSetCpuTuneQuote(self, quota, offset):
        with fake.VM() as testvm:
            # we need a different behaviour with respect to
            # plain fake.Domain. Seems simpler to just add
            # a new special-purpose trivial fake here.
            testvm._dom = ChangingSchedulerDomain(offset)
            testvm.setCpuTuneQuota(quota)
            assert quota + offset == testvm._vcpuTuneInfo['vcpu_quota']

    @permutations([[100000, 128], [150000, 0], [9999, -99]])
    def testSetCpuTunePeriod(self, period, offset):
        with fake.VM() as testvm:
            # same as per testSetCpuTuneQuota
            testvm._dom = ChangingSchedulerDomain(offset)
            testvm.setCpuTunePeriod(period)
            assert period + offset == testvm._vcpuTuneInfo['vcpu_period']

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

                assert res == response.error(vdsm_error)

    @MonkeyPatch(numa, 'update', lambda: None)
    @MonkeyPatch(numa, 'cpu_topology', lambda:
                 numa.CpuTopology(1, 6, 6, [0, 1, 2, 3, 4, 5]))
    def testAssignCpusets(self):
        with fake.VM() as testvm:
            dom = fake.Domain()
            dom.vcpu_pinning = {}

            def pinVcpu(vcpu, cpuset):
                dom.vcpu_pinning[vcpu] = cpuset

            dom.pinVcpu = pinVcpu
            dom.setVcpusFlags = lambda vcpus, flags: None
            testvm._dom = dom
            testvm._updateDomainDescriptor = lambda: None

            testvm._assignCpusets(['0', '2-3', '1,4-5'])
            pinning = dom.vcpu_pinning
            assert len(pinning) == 3
            assert pinning[0] == (True, False, False, False, False, False)
            assert pinning[1] == (False, False, True, True, False, False)
            assert pinning[2] == (False, True, False, False, True, True)

    @MonkeyPatch(hooks, 'before_set_num_of_cpus', lambda: None)
    def testSetNumberOfVcpusWrongCpusets(self):
        with fake.VM() as testvm:
            testvm._cpu_policy = cpumanagement.CPU_POLICY_DEDICATED
            with pytest.raises(exception.MissingParameter):
                testvm.setNumberOfCpus(4)
            # Check length matches number of CPUs
            with pytest.raises(exception.InvalidParameter):
                testvm.setNumberOfCpus(4, ['1', '2', '3'])
            with pytest.raises(exception.InvalidParameter):
                testvm.setNumberOfCpus(4, ['1', '2', '3', '4', '5'])

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

            with pytest.raises(exception.SpiceTicketError):
                self._updateGraphicsDevice(
                    testvm, device, _GRAPHICS_DEVICE_PARAMS
                )

    def testAcpiShutdownDisconnected(self):
        with fake.VM() as testvm:
            testvm._dom = virdomain.Disconnected(vmid='testvm')
            assert response.is_error(testvm.acpiShutdown())

    def testAcpiShutdownConnected(self):
        with fake.VM() as testvm:
            testvm._dom = fake.Domain(vmId='testvm')
            assert not response.is_error(testvm.acpiShutdown())

    def testAcpiRebootDisconnected(self):
        with fake.VM() as testvm:
            testvm._dom = virdomain.Disconnected(vmid='testvm')
            assert response.is_error(testvm.acpiReboot())

    def testAcpiRebootConnected(self):
        with fake.VM() as testvm:
            testvm._dom = fake.Domain(vmId='testvm')
            assert not response.is_error(testvm.acpiReboot())

    @permutations([
        # length should be 1
        [[], exception.InvalidParameter],
        # length should be 1
        [['2', '4'], exception.InvalidParameter],
        # cannot be arbitrary string
        [['abc'], exception.InvalidParameter],
    ])
    def test_process_migration_cpusets_invalid(self, cpusets, exception):
        with fake.VM() as testvm:
            with pytest.raises(exception):
                testvm._validate_migration_cpusets(cpusets)


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
