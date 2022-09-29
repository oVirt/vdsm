# SPDX-FileCopyrightText: 2012 IBM Corp.
# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import functools
import logging
import os.path
import threading
import time
import uuid

from contextlib import contextmanager
from itertools import product

import libvirt
from six.moves import zip

from vdsm import constants

from vdsm.common import cpuarch
from vdsm.common import define
from vdsm.common import exception
from vdsm.common import libvirtconnection
from vdsm.common import response
from vdsm.common import xmlutils

import vdsm.common.time

from vdsm.virt import cpumanagement
from vdsm.virt import periodic
from vdsm.virt import utils
from vdsm.virt import virdomain
from vdsm.virt import vm
from vdsm.virt import vmdevices
from vdsm.virt import vmexitreason
from vdsm.virt import vmstats
from vdsm.virt import vmstatus
from vdsm.virt import xmlconstants
from vdsm.virt.vmdevices import hwclass
from vdsm.virt.vmdevices.storage import DISK_TYPE
from vdsm.virt.vmtune import (
    io_tune_merge,
    io_tune_dom_to_values,
    io_tune_to_dom,
)

from monkeypatch import MonkeyPatch, MonkeyPatchScope
from testValidation import brokentest
from testlib import VdsmTestCase as TestCaseBase
from testlib import XMLTestCase
from testlib import make_config
from testlib import namedTemporaryDir
from testlib import permutations, expandPermutations
from testlib import recorded

from fakelib import FakeLogger

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


@expandPermutations
class TestVm(XMLTestCase):

    def __init__(self, *args, **kwargs):
        super(TestVm, self).__init__(*args, **kwargs)
        self.channelListener = None
        self.conf = {'vmName': 'testVm',
                     'vmId': '9ffe28b6-6134-4b1e-8804-1185f49c436f',
                     'smp': '8', 'maxVCpus': '160',
                     'memSize': '1024', 'memGuaranteedSize': '512'}

    def testIoTuneException(self):
        SERIAL = '54-a672-23e5b495a9ea'
        devConf = {'index': '0', 'propagateErrors': 'on', 'iface': 'virtio',
                   'name': 'vda', 'format': 'cow', 'device': 'disk',
                   'path': '/tmp/disk1.img', 'type': 'disk',
                   'readonly': 'False', 'shared': 'True', 'serial': SERIAL}
        tuneConfs = [
            {'read_iops_sec': 1000, 'total_iops_sec': 2000},
            {'read_bytes_sec': -5},
            {'aaa': 100},
            {'read_iops_sec': 'aaa'}]

        expectedExceptMsgs = [
            'A non-zero total value and non-zero read/write value for'
            ' iops_sec can not be set at the same time',
            'parameter read_bytes_sec value should be equal or greater'
            ' than zero',
            'parameter aaa name is invalid',
            'an integer is required for ioTune parameter read_iops_sec']

        for (tuneConf, exceptionMsg) in \
                zip(tuneConfs, expectedExceptMsgs):
            drive = vmdevices.storage.Drive(self.log, diskType=DISK_TYPE.FILE,
                                            **devConf)

            with pytest.raises(Exception) as cm:
                drive.iotune = tuneConf

            assert cm.value.args[0] == exceptionMsg

    def testVmPolicyOnStartup(self):
        LIMIT = '50'
        with fake.VM(_VM_PARAMS) as testvm:
            dom = fake.Domain()
            dom.setMetadata(libvirt.VIR_DOMAIN_METADATA_ELEMENT,
                            '<qos><vcpuLimit>%s</vcpuLimit></qos>' % (
                                LIMIT
                            ),
                            xmlconstants.METADATA_VM_TUNE_PREFIX,
                            xmlconstants.METADATA_VM_TUNE_URI,
                            0)
            testvm._dom = dom
            # it is bad practice to test private functions -and we know it.
            # But enduring the full VM startup is too cumbersome, and we
            # need to test this code.
            testvm._updateVcpuLimit()
            stats = testvm.getStats()
            assert stats['vcpuUserLimit'] == LIMIT

    def testGetVmPolicySucceded(self):
        with fake.VM() as testvm:
            testvm._dom = fake.Domain()
            self.assertXMLEqual(xmlutils.tostring(testvm._getVmPolicy()),
                                '<qos/>')

    def testGetVmPolicyEmptyOnNoMetadata(self):
        with fake.VM() as testvm:
            testvm._dom = fake.Domain(
                virtError=libvirt.VIR_ERR_NO_DOMAIN_METADATA)
            self.assertXMLEqual(xmlutils.tostring(testvm._getVmPolicy()),
                                '<qos/>')

    def testGetVmPolicyFailOnNoDomain(self):
        with fake.VM() as testvm:
            testvm._dom = fake.Domain(virtError=libvirt.VIR_ERR_NO_DOMAIN)
            assert testvm._getVmPolicy() is None

    def testUpdateVmPolicy(self):
        with fake.VM() as machine:
            dom = fake.Domain()
            machine._dom = dom

            policy = {
                "vcpuLimit": 50,
                "ioTune": [
                    {
                        "name": "test-device-by-name",
                        "maximum": {
                            "total_bytes_sec": 200, "total_iops_sec": 201,
                            "read_bytes_sec": 202, "read_iops_sec": 203,
                            "write_bytes_sec": 204, "write_iops_sec": 205
                        },
                        "guaranteed": {
                            "total_bytes_sec": 100, "total_iops_sec": 101,
                            "read_bytes_sec": 102, "read_iops_sec": 103,
                            "write_bytes_sec": 104, "write_iops_sec": 105
                        }
                    },
                    {
                        "path": "test-device-by-path",
                        "maximum": {
                            "total_bytes_sec": 400, "total_iops_sec": 401,
                            "read_bytes_sec": 402, "read_iops_sec": 403,
                            "write_bytes_sec": 404, "write_iops_sec": 405
                        },
                        "guaranteed": {
                            "total_bytes_sec": 300, "total_iops_sec": 301,
                            "read_bytes_sec": 302, "read_iops_sec": -1,
                            "write_bytes_sec": 304, "write_iops_sec": 305
                        }
                    }
                ]
            }

            machine.updateVmPolicy(policy)

            expected_xml = (u"""
            <qos>
                <vcpuLimit>50</vcpuLimit>
                <ioTune>
                    <device name="test-device-by-name">
                        <maximum>
                            <total_bytes_sec>200</total_bytes_sec>
                            <total_iops_sec>201</total_iops_sec>
                            <read_bytes_sec>202</read_bytes_sec>
                            <read_iops_sec>203</read_iops_sec>
                            <write_bytes_sec>204</write_bytes_sec>
                            <write_iops_sec>205</write_iops_sec>
                        </maximum>
                        <guaranteed>
                            <total_bytes_sec>100</total_bytes_sec>
                            <total_iops_sec>101</total_iops_sec>
                            <read_bytes_sec>102</read_bytes_sec>
                            <read_iops_sec>103</read_iops_sec>
                            <write_bytes_sec>104</write_bytes_sec>
                            <write_iops_sec>105</write_iops_sec>
                        </guaranteed>
                    </device>
                    <device path="test-device-by-path">
                        <maximum>
                            <total_bytes_sec>400</total_bytes_sec>
                            <total_iops_sec>401</total_iops_sec>
                            <read_bytes_sec>402</read_bytes_sec>
                            <read_iops_sec>403</read_iops_sec>
                            <write_bytes_sec>404</write_bytes_sec>
                            <write_iops_sec>405</write_iops_sec>
                        </maximum>
                        <guaranteed>
                            <total_bytes_sec>300</total_bytes_sec>
                            <total_iops_sec>301</total_iops_sec>
                            <read_bytes_sec>302</read_bytes_sec>
                            <write_bytes_sec>304</write_bytes_sec>
                            <write_iops_sec>305</write_iops_sec>
                        </guaranteed>
                    </device>
                </ioTune>
            </qos>
            """)

            self.assertXMLEqual(expected_xml, dom._metadata)

    def testCpuTune(self):
        LIMIT = 50
        with fake.VM(_VM_PARAMS) as machine:
            machine._dom = fake.Domain()
            policy = {"vcpuLimit": LIMIT}

            machine.updateVmPolicy(policy)

            stats = machine.getStats()
            assert stats['vcpuUserLimit'] == LIMIT

    def testIoTuneParser(self):
        with fake.VM() as machine:
            dom = fake.Domain()
            machine._dom = dom

            ioTuneValues = {
                "name": "test-device-by-name",
                "path": "test-path",
                "maximum": {
                    "total_bytes_sec": 200, "total_iops_sec": 201,
                    "read_bytes_sec": 202, "read_iops_sec": 203,
                    "write_bytes_sec": 204, "write_iops_sec": 205
                },
                "guaranteed": {
                    "total_bytes_sec": 100, "total_iops_sec": 101,
                    "read_bytes_sec": 102, "read_iops_sec": 103,
                    "write_bytes_sec": 104, "write_iops_sec": 105
                }
            }

            dom = io_tune_to_dom(ioTuneValues)
            parsed = io_tune_dom_to_values(dom)

            assert ioTuneValues == parsed

    def testIoTuneMerge(self):
        with fake.VM() as machine:
            dom = fake.Domain()
            machine._dom = dom

            ioTuneValues1 = {
                "path": "test-path",
                "maximum": {
                    "total_bytes_sec": 0, "total_iops_sec": 0,
                    "read_bytes_sec": 0,
                    "write_bytes_sec": 999, "write_iops_sec": 0
                },
                "guaranteed": {
                    "total_bytes_sec": 999, "total_iops_sec": 0,
                    "read_bytes_sec": 0, "read_iops_sec": 0,
                    "write_bytes_sec": 0, "write_iops_sec": 0
                }
            }

            ioTuneValues2 = {
                "name": "test-device-by-name",
                "maximum": {
                    "total_bytes_sec": 200, "total_iops_sec": 201,
                    "read_bytes_sec": 202, "read_iops_sec": 203,
                    "write_iops_sec": 205
                },
                "guaranteed": {
                    "total_bytes_sec": -1, "total_iops_sec": 101,
                    "read_bytes_sec": 102, "read_iops_sec": 103,
                    "write_bytes_sec": 104, "write_iops_sec": 105
                }
            }

            ioTuneExpectedValues = {
                "name": "test-device-by-name",
                "path": "test-path",
                "maximum": {
                    "total_bytes_sec": 200, "total_iops_sec": 201,
                    "read_bytes_sec": 202, "read_iops_sec": 203,
                    "write_bytes_sec": 999, "write_iops_sec": 205
                },
                "guaranteed": {
                    "total_bytes_sec": -1, "total_iops_sec": 101,
                    "read_bytes_sec": 102, "read_iops_sec": 103,
                    "write_bytes_sec": 104, "write_iops_sec": 105
                }
            }

            ioTuneMerged = io_tune_merge(ioTuneValues1, ioTuneValues2)

            assert ioTuneMerged == ioTuneExpectedValues

    def testUpdateExistingVmPolicy(self):
        with fake.VM() as machine:
            dom = fake.Domain()
            dom._metadata = """
            <qos>
                <vcpuLimit>999</vcpuLimit>
                <ioTune>
                    <device name='test-device-by-name'>
                        <maximum>
                            <total_bytes_sec>9999</total_bytes_sec>
                        </maximum>
                    </device>
                    <device name='other-device'>
                        <maximum>
                            <total_bytes_sec>9999</total_bytes_sec>
                        </maximum>
                    </device>
                </ioTune>
            </qos>
            """

            machine._dom = dom

            policy = {
                "vcpuLimit": 50,
                "ioTune": [
                    {
                        "name": "test-device-by-name",
                        "maximum": {
                            "total_bytes_sec": 200, "total_iops_sec": 201,
                            "read_bytes_sec": 202, "read_iops_sec": 203,
                            "write_bytes_sec": 204, "write_iops_sec": 205
                        },
                        "guaranteed": {
                            "total_bytes_sec": 100, "total_iops_sec": 101,
                            "read_bytes_sec": 102, "read_iops_sec": 103,
                            "write_bytes_sec": 104, "write_iops_sec": 105
                        }
                    },
                    {
                        "path": "test-device-by-path",
                        "maximum": {
                            "total_bytes_sec": 400, "total_iops_sec": 401,
                            "read_bytes_sec": 402, "read_iops_sec": 403,
                            "write_bytes_sec": 404, "write_iops_sec": 405
                        },
                        "guaranteed": {
                            "total_bytes_sec": 300, "total_iops_sec": 301,
                            "read_bytes_sec": 302, "read_iops_sec": 303,
                            "write_bytes_sec": 304, "write_iops_sec": 305
                        }
                    }
                ]
            }

            machine.updateVmPolicy(policy)

            expected_xml = (u"""
            <qos>
                <vcpuLimit>50</vcpuLimit>
                <ioTune>
                    <device name="test-device-by-name">
                        <maximum>
                            <total_bytes_sec>200</total_bytes_sec>
                            <total_iops_sec>201</total_iops_sec>
                            <read_bytes_sec>202</read_bytes_sec>
                            <read_iops_sec>203</read_iops_sec>
                            <write_bytes_sec>204</write_bytes_sec>
                            <write_iops_sec>205</write_iops_sec>
                        </maximum>
                        <guaranteed>
                            <total_bytes_sec>100</total_bytes_sec>
                            <total_iops_sec>101</total_iops_sec>
                            <read_bytes_sec>102</read_bytes_sec>
                            <read_iops_sec>103</read_iops_sec>
                            <write_bytes_sec>104</write_bytes_sec>
                            <write_iops_sec>105</write_iops_sec>
                        </guaranteed>
                    </device>
                    <device name="other-device">
                        <maximum>
                            <total_bytes_sec>9999</total_bytes_sec>
                        </maximum>
                    </device>
                    <device path="test-device-by-path">
                        <maximum>
                            <total_bytes_sec>400</total_bytes_sec>
                            <total_iops_sec>401</total_iops_sec>
                            <read_bytes_sec>402</read_bytes_sec>
                            <read_iops_sec>403</read_iops_sec>
                            <write_bytes_sec>404</write_bytes_sec>
                            <write_iops_sec>405</write_iops_sec>
                        </maximum>
                        <guaranteed>
                            <total_bytes_sec>300</total_bytes_sec>
                            <total_iops_sec>301</total_iops_sec>
                            <read_bytes_sec>302</read_bytes_sec>
                            <read_iops_sec>303</read_iops_sec>
                            <write_bytes_sec>304</write_bytes_sec>
                            <write_iops_sec>305</write_iops_sec>
                        </guaranteed>
                    </device>
                </ioTune>
            </qos>
            """)

            self.assertXMLEqual(expected_xml, dom._metadata)

    def testGetIoTunePolicy(self):
        with fake.VM() as machine:
            dom = fake.Domain()
            dom._metadata = """
            <qos>
                <vcpuLimit>999</vcpuLimit>
                <ioTune>
                    <device name='test-device-by-name'>
                        <maximum>
                            <total_bytes_sec>9999</total_bytes_sec>
                        </maximum>
                    </device>
                    <device name='other-device'>
                        <guaranteed>
                            <total_bytes_sec>9999</total_bytes_sec>
                        </guaranteed>
                    </device>
                </ioTune>
            </qos>
            """
            machine._dom = dom
            machine._updateIoTuneInfo()

            tunables = machine.io_tune_policy()
            expected = [
                {'name': u'test-device-by-name',
                 'maximum': {
                     u'total_bytes_sec': 9999
                 }},
                {'name': u'other-device',
                 'guaranteed': {
                     u'total_bytes_sec': 9999
                 }}
            ]
            assert tunables == expected

    @permutations([['<empty/>'], [None]])
    def testNoIoTunePolicy(self, metadata):
        with fake.VM() as machine:
            dom = fake.Domain()
            dom._metadata = metadata
            machine._dom = dom

            tunables = machine.io_tune_policy()
            assert tunables == []

    @brokentest("the test expects overwrite, the code incrementally updates")
    @permutations([
        # old_iotune
        [{}],
        [{"ioTune": {}}],
        [{"ioTune": {"total_bytes_sec": 9999}}],
        [{"ioTune": {"total_iops_sec": 9999}}],
        [{"ioTune": {"total_bytes_sec": 9999, "total_iops_sec": 9999}}],
    ])
    def testSetIoTune(self, old_iotune):

        drives = [
            vmdevices.storage.Drive(
                log=self.log,
                index=0,
                device="hdd",
                path="/dev/dummy",
                type=hwclass.DISK,
                iface="ide",
                specParams=old_iotune,
                diskType=DISK_TYPE.BLOCK
            )
        ]

        # Make the drive look like a VDSM volume
        required = ('domainID', 'imageID', 'poolID', 'volumeID')
        for p in required:
            setattr(drives[0], p, "1")

        new_iotune = {
            "write_bytes_sec": 1,
            "total_bytes_sec": 0,
            "read_bytes_sec": 2
        }

        tunables = [
            {
                "name": drives[0].name,
                "ioTune": new_iotune,
            }
        ]

        expected_io_tune = {
            drives[0].name: new_iotune,
        }

        expected_xml = """
            <disk device="hdd" snapshot="no" type="block">
                <source dev="/dev/dummy"/>
                <target bus="ide" dev="hda"/>
                <iotune>%s</iotune>
            </disk>""" % ("\n".join(["<%s>%s</%s>" % (k, v, k)
                                     for k, v in sorted(new_iotune.items())]))

        with fake.VM() as machine:
            dom = fake.Domain()
            machine._dom = dom
            for drive in drives:
                machine._devices[drive.type].append(drive)

            machine.setIoTune(tunables)

            assert expected_io_tune == dom._io_tune

            # Test that caches were properly updated
            assert drives[0].iotune == expected_io_tune[drives[0].name]
            self.assertXMLEqual(drives[0]._deviceXML, expected_xml)

    def testGetPolicyDisconnected(self):
        with fake.VM() as machine:
            machine._dom = virdomain.Disconnected(machine.id)
            policy = machine._getVmPolicy()
            assert policy is None

    def testSdIds(self):
        """
        Tests that VM storage domains in use list is in sync with the vm
        devices in use
        """
        domainID = uuid.uuid4()
        drives = [
            vmdevices.storage.Drive(
                self.log,
                index=0,
                device="disk",
                path="/dev/dummy",
                type=hwclass.DISK,
                iface="ide",
                domainID=domainID,
                imageID=uuid.uuid4(),
                poolID=uuid.uuid4(),
                volumeID=uuid.uuid4(),
                diskType=DISK_TYPE.BLOCK,
            ),
            vmdevices.storage.Drive(
                self.log,
                index=0,
                device="hdd2",
                path="/dev/dummy2",
                type=hwclass.DISK,
                iface="ide",
                diskType=DISK_TYPE.BLOCK,
            )
        ]

        with fake.VM() as machine:
            for drive in drives:
                machine._devices[drive.type].append(drive)

            assert machine.sdIds == set([domainID])

    def testVmGuestSocketFile(self):
        # No channel
        with fake.VM(self.conf) as testvm:
            assert testvm._guestSocketFile is None
        # New name
        channel = '''
<channel type="unix">
  <source mode="bind" path="/path/to/channel"/>
  <target type="virtio" name="ovirt-guest-agent.0"/>
</channel>
        '''
        with fake.VM(self.conf, xmldevices=channel) as testvm:
            assert testvm._guestSocketFile == '/path/to/channel'
        # Old name
        channel = '''
<channel type="unix">
  <source mode="bind" path="/path/to/channel"/>
  <target type="virtio" name="com.redhat.rhevm.vdsm"/>
</channel>
        '''
        with fake.VM(self.conf, xmldevices=channel) as testvm:
            assert testvm._guestSocketFile == '/path/to/channel'

    def test_spice_restore_set_passwd(self):
        devices = '''
<graphics type="spice" port="-1" autoport="yes"
          passwd="*****" passwdValidTo="1970-01-01T00:00:01" tlsPort="-1">
  <listen type="network" network="vdsm-ovirtmgmt"/>
</graphics>
'''
        with fake.VM(xmldevices=devices, create_device_objects=True) as testvm:
            out_dom_xml = testvm._correctGraphicsConfiguration(
                _load_xml('vm_restore_spice_before.xml'))

        self.assertXMLEqual(out_dom_xml,
                            _load_xml('vm_restore_spice_after.xml'))

    @MonkeyPatch(os, 'unlink', lambda _: None)
    def test_release_vm_succeeds(self):
        with fake.VM(self.conf) as testvm:
            testvm.guestAgent = fake.GuestAgent()

            dom = fake.Domain()

            status = {
                'graceful': 0,
                'forceful': 0,
            }

            def graceful(*args):
                status['graceful'] += 1
                return response.success()

            def forceful(*args):
                status['forceful'] += 1
                return response.success()

            dom.destroyFlags = graceful
            dom.destroy = forceful
            testvm._dom = dom

            testvm.releaseVm()
            assert status == {
                'graceful': 1,
                'forceful': 0,
            }

    @MonkeyPatch(os, 'unlink', lambda _: None)
    @permutations([[1], [2], [3], [9]])
    def test_releasevm_fails(self, attempts):
        with fake.VM(self.conf) as testvm:
            testvm.guestAgent = fake.GuestAgent()

            dom = fake.Domain()

            status = {
                'graceful': 0,
                'forceful': 0,
            }

            def graceful(*args):
                status['graceful'] += 1
                raise fake.Error(libvirt.VIR_ERR_SYSTEM_ERROR)

            def forceful(*args):
                status['forceful'] += 1
                return response.success()

            dom.destroyFlags = graceful
            dom.destroy = forceful
            testvm._dom = dom

            testvm.releaseVm(gracefulAttempts=attempts)
            assert status == {
                'graceful': attempts,
                'forceful': 1,
            }

    def test_acpi_enabled(self):
        with fake.VM(arch=cpuarch.X86_64, features='<acpi/>') as testvm:
            assert testvm.acpi_enabled()

    def test_acpi_disabled(self):
        with fake.VM(arch=cpuarch.X86_64) as testvm:
            assert not testvm.acpi_enabled()

    def test_hotplug_lease(self):
        params = {
            'type': hwclass.LEASE,
            'sd_id': 'sd_id',
            'lease_id': 'lease_id',
        }
        expected_conf = {
            'device': hwclass.LEASE,
            'path': '/path',
            'offset': 1048576,
        }
        expected_conf.update(params)

        # we add a serial console device to the minimal XML,
        # because this is the simplest way to trigger the
        # flow that broke in rhbz#1590063
        devices = [{
            u'device': u'console',
            u'specParams': {
                u'consoleType': u'serial',
                u'enableSocket': u'true'
            },
            u'type': u'console',
            u'deviceId': u'd0fac53d-68cf-4cbb-8c9d-5f18625f04e7',
            u'alias': u'serial0'
        }]

        with fake.VM(
                params={},
                devices=devices,
                create_device_objects=True,
                arch=cpuarch.X86_64
        ) as testvm:
            testvm._dom = FakeLeaseDomain()
            testvm.cif = FakeLeaseClientIF(expected_conf)
            res = testvm.hotplugLease(params)

            assert res.pop('vmList') is not None
            assert res == response.success()
            # Up until here we verified the hotplugLease proper.

    def testMemSize(self):
        with fake.VM() as testvm:
            assert testvm.mem_size_mb() == 4096

    def testNvdimms(self):
        # Add NVDIMM to the devices, update memory and make sure
        # mem_size_mb() returns proper size
        vm_xml = '''
        <domain type='kvm' xmlns:ovirt-vm="http://ovirt.org/vm/1.0">
            <name>nTESTING</name>
            <uuid>TESTING</uuid>
            <memory unit='KiB'>12582912</memory>
            <os>
                <type arch='x86_64' machine='pc-i440fx-2.3'>hvm</type>
            </os>
            <devices>
                <memory model='nvdimm' access='shared'>
                    <source>
                        <path>/dev/pmem0</path>
                        <alignsize unit='KiB'>131072</alignsize>
                    </source>
                    <target>
                        <size unit='KiB'>8388608</size>
                        <node>0</node>
                        <label>
                            <size unit='KiB'>128</size>
                        </label>
                    </target>
                    <address type='dimm' slot='0'/>
                </memory>
            </devices>
        </domain>
        '''
        with fake.VM(params={'xml': vm_xml}) as testvm:
            # Verify the memory is 4 GB and not 12 GB (4 + 8)
            assert testvm.mem_size_mb() == 4096


class ExpectedError(Exception):
    pass


class UnexpectedError(Exception):
    pass


@expandPermutations
class TestVmDeviceHandling(TestCaseBase):
    conf = {
        'devices': [],
        'maxVCpus': '160',
        'memGuaranteedSize': '512',
        'memSize': '1024',
        'smp': '8',
        'vmId': '9ffe28b6-6134-4b1e-beef-1185f49c436f',
        'vmName': 'testVm',
    }
    xml_conf = '''<?xml version="1.0" encoding="utf-8"?>
       <domain type="kvm"
               xmlns:ovirt-tune="http://ovirt.org/vm/tune/1.0"
               xmlns:ovirt-vm="http://ovirt.org/vm/1.0">
         <name>testVm</name>
         <uuid>1234</uuid>
         <memory>1048576</memory>
         <currentMemory>1048576</currentMemory>
         <vcpu current="1">160</vcpu>
         <devices>
           <disk type='file' device='disk' snapshot='no'>
             <source file='/path/1234'/>
             <target dev='sda' bus='scsi'/>
             <serial>9876</serial>
             <address type='drive' controller='0' bus='0' target='0' unit='0'/>
           </disk>
           <controller type='scsi' index='0' model='virtio-scsi'>
             <address type='pci' domain='0x0000' bus='0x00' slot='0x05'
                      function='0x0'/>
           </controller>
           <controller type='pci' index='0' model='pci-root'/>
           <interface type='bridge'>
             <mac address='00:11:22:33:44:55'/>
             <source bridge='ovirtmgmt'/>
             <target dev='vnet0'/>
             <model type='virtio'/>
             <filterref filter='vdsm-no-mac-spoofing'/>
             <address type='pci' domain='0x0000' bus='0x00' slot='0x03'
                      function='0x0'/>
           </interface>
         </devices>
         <metadata>
             <ovirt-tune:qos/>
             <ovirt-vm:vm/>
         </metadata>
       </domain>'''

    def test_device_setup_success(self):
        devices = [fake.Device('device_{}'.format(i)) for i in range(3)]

        with fake.VM(self.conf, create_device_objects=True) as testvm:
            testvm._devices['general'] = devices
            self.assertNotRaises(testvm._setup_devices)
            assert devices[0].state == fake.SETUP
            assert devices[1].state == fake.SETUP
            assert devices[2].state == fake.SETUP

    def test_device_setup_fail_first(self):
        devices = ([fake.Device('device_0', fail_setup=ExpectedError)] +
                   [fake.Device('device_{}'.format(i)) for i in range(1, 3)])

        with fake.VM(self.conf, create_device_objects=True) as testvm:
            testvm._devices['general'] = devices
            with pytest.raises(ExpectedError):
                testvm._setup_devices()
            assert devices[0].state == fake.SETUP
            assert devices[1].state == fake.CREATED
            assert devices[2].state == fake.CREATED

    def test_device_setup_fail_second(self):
        devices = [fake.Device('device_0'),
                   fake.Device('device_1', fail_setup=ExpectedError),
                   fake.Device('device_2')]

        with fake.VM(self.conf, create_device_objects=True) as testvm:
            testvm._devices['general'] = devices
            with pytest.raises(ExpectedError):
                testvm._setup_devices()
            assert devices[0].state == fake.TEARDOWN
            assert devices[1].state == fake.SETUP
            assert devices[2].state == fake.CREATED

    def test_device_setup_fail_third(self):
        devices = [fake.Device('device_0'), fake.Device('device_1'),
                   fake.Device('device_2', fail_setup=ExpectedError)]

        with fake.VM(self.conf, create_device_objects=True) as testvm:
            testvm._devices['general'] = devices
            with pytest.raises(ExpectedError):
                testvm._setup_devices()
            assert devices[0].state == fake.TEARDOWN
            assert devices[1].state == fake.TEARDOWN
            assert devices[2].state == fake.SETUP

    def test_device_setup_correct_exception(self):
        devices = [fake.Device('device_0', fail_teardown=UnexpectedError),
                   fake.Device('device_1',
                               fail_setup=ExpectedError,
                               fail_teardown=UnexpectedError),
                   fake.Device('device_2', fail_setup=UnexpectedError)]

        with fake.VM(self.conf, create_device_objects=True) as testvm:
            testvm._devices['general'] = devices
            with pytest.raises(ExpectedError):
                testvm._setup_devices()
            assert devices[0].state == fake.TEARDOWN
            assert devices[1].state == fake.SETUP
            assert devices[2].state == fake.CREATED

    def test_device_teardown_success(self):
        devices = [fake.Device('device_{}'.format(i)) for i in range(3)]

        with fake.VM(self.conf, create_device_objects=True) as testvm:
            testvm._devices['general'] = devices
            self.assertNotRaises(testvm._setup_devices)
            self.assertNotRaises(testvm._teardown_devices)
            assert devices[0].state == fake.TEARDOWN
            assert devices[1].state == fake.TEARDOWN
            assert devices[2].state == fake.TEARDOWN

    def test_device_teardown_fail_all(self):
        devices = [fake.Device('device_{}'.format(i),
                               fail_teardown=UnexpectedError)
                   for i in range(3)]

        with fake.VM(self.conf, create_device_objects=True) as testvm:
            testvm._devices['general'] = devices
            self.assertNotRaises(testvm._setup_devices)
            self.assertNotRaises(testvm._teardown_devices)
            assert devices[0].state == fake.TEARDOWN
            assert devices[1].state == fake.TEARDOWN
            assert devices[2].state == fake.TEARDOWN

    @permutations([
        [[], '0'],
        [[0], '1'],
        [[1, 2], '0'],
        [[0, 2], '1'],
        [[0, 1], '2'],
    ])
    def test_getNextIndex(self, used, expected):
        with fake.VM(self.conf) as testvm:
            # TODO: get rid of mangling
            assert testvm._Vm__getNextIndex(used) == expected

    @permutations([
        ['', ''],
        ['123', '123'],
        ['ide', 'ide'],
        ['sata', 'sd'],
        ['scsi', 'sd'],
    ])
    def test_indiceForIface(self, iface, expected):
        with fake.VM(self.conf) as testvm:
            assert testvm._indiceForIface(iface) == expected

    @permutations([
        # We have to make sure that 'sd' key exists otherwise even defaultdict
        # will KeyError on access.
        [{'sd': []}, {'iface': 'sata'}, {'sd': [0]}],
        [{'sd': [0]}, {'iface': 'sata'}, {'sd': [0, 1]}],
        [{'sd': [1]}, {'iface': 'sata'}, {'sd': [1, 0]}],
        [{'sd': [0, 2]}, {'iface': 'sata'}, {'sd': [0, 2, 1]}],
        [{'sd': [], 'other': [0]}, {'iface': 'sata'},
         {'other': [0], 'sd': [0]}],
        [{'sd': [0]}, {'iface': 'scsi'}, {'sd': [0, 1]}],
    ])
    def test_updateDriveIndex(self, used, drv, expected):
        with fake.VM(self.conf) as testvm:
            testvm._usedIndices = used
            testvm.updateDriveIndex(drv)
            assert testvm._usedIndices == expected

    @permutations([
        [[{'iface': 'scsi', 'index': '1'}, {'iface': 'sata'}],
         [{'iface': 'scsi', 'index': '1'}, {'iface': 'sata', 'index': '0'}]],
        [[{'iface': 'scsi'}, {'iface': 'ide'}],
         [{'iface': 'scsi', 'index': '0'}, {'iface': 'ide', 'index': '0'}]],
        [[{'iface': 'scsi'}, {'iface': 'sata'}, {'iface': 'ide'}],
         [{'iface': 'scsi', 'index': '0'}, {'iface': 'sata', 'index': '1'},
          {'iface': 'ide', 'index': '0'}]],
    ])
    def test_normalizeDrivesIndices(self, drives, expected):
        with fake.VM(self.conf) as testvm:
            assert testvm.normalizeDrivesIndices(drives) == expected

    def test_xml_device_processing(self):
        with fake.VM({'xml': self.xml_conf}) as vm:
            devices = vm._make_devices()
            assert sum([len(v) for v in devices.values()]) == 2


VM_EXITS = tuple(product((define.NORMAL, define.ERROR),
                 list(vmexitreason.exitReasons.keys())))


@expandPermutations
class TestVmExit(TestCaseBase):
    @permutations(VM_EXITS)
    def testExitReason(self, exitCode, exitReason):
        """
        test of:
        exitReason round trip;
        error message is constructed correctly automatically
        """
        with fake.VM() as testvm:
            testvm.setDownStatus(exitCode, exitReason)
            stats = testvm.getStats()
            assert stats['exitReason'] == exitReason
            assert stats['exitMessage'] == \
                vmexitreason.exitReasons.get(exitReason)

    @permutations(VM_EXITS)
    def testExitReasonExplicitMessage(self, exitCode, exitReason):
        """
        test of:
        exitReason round trip;
        error message can be overridden explicitely
        """
        with fake.VM() as testvm:
            msg = "test custom error message"
            testvm.setDownStatus(exitCode, exitReason, msg)
            stats = testvm.getStats()
            assert stats['exitReason'] == exitReason
            assert stats['exitMessage'] == msg


_VM_PARAMS = {'displayPort': -1, 'displaySecurePort': -1,
              'display': 'qxl', 'displayIp': '127.0.0.1',
              'vmType': 'kvm', 'memSize': 1024}


class TestVmStats(TestCaseBase):

    def testGetNicStats(self):
        GBPS = 10 ** 9 // 8
        MAC = '52:54:00:59:F5:3F'
        pretime = vdsm.common.time.monotonic_time()
        with fake.VM(_VM_PARAMS) as testvm:
            res = vmstats._nic_traffic(
                testvm, fake.Nic(
                    name='vnettest', model='virtio', mac_addr=MAC
                ),
                start_sample={'net.0.rx.bytes': 2 ** 64 - 15 * GBPS,
                              'net.0.rx.pkts': 1,
                              'net.0.rx.errs': 2,
                              'net.0.rx.drop': 3,
                              'net.0.tx.bytes': 0,
                              'net.0.tx.pkts': 4,
                              'net.0.tx.errs': 5,
                              'net.0.tx.drop': 6},
                start_index=0,
                end_sample={'net.0.rx.bytes': 0,
                            'net.0.rx.pkts': 7,
                            'net.0.rx.errs': 8,
                            'net.0.rx.drop': 9,
                            'net.0.tx.bytes': 5 * GBPS,
                            'net.0.tx.pkts': 10,
                            'net.0.tx.errs': 11,
                            'net.0.tx.drop': 12},
                end_index=0)
        posttime = vdsm.common.time.monotonic_time()
        assert 'sampleTime' in res
        assert pretime <= res['sampleTime'] <= posttime, \
            'sampleTime not in [%s..%s]' % (pretime, posttime)
        del res['sampleTime']
        assert res == {
            'rxErrors': '8', 'rxDropped': '9',
            'txErrors': '11', 'txDropped': '12',
            'macAddr': MAC, 'name': 'vnettest',
            'speed': '1000', 'state': 'unknown',
            'rx': '0', 'tx': '625000000',
        }

    def testMultipleGraphicDeviceStats(self):
        device_types = ['spice', 'vnc']
        devices = '\n'.join(['''
<graphics type="{type_}" port="-1">
  <listen type="network" network="vdsm-ovirtmgmt"/>
</graphics>'''.format(type_=t) for t in device_types])
        with fake.VM(xmldevices=devices, create_device_objects=True) as testvm:
            res = testvm.getStats()
            assert res['displayInfo']
            for dev_stats, type_ in zip(res['displayInfo'], device_types):
                assert dev_stats['type'] in type_
                assert 'port' in dev_stats

    def testDiskMappingHashInStatsHash(self):
        with fake.VM(_VM_PARAMS) as testvm:
            res = testvm.getStats()
            testvm.guestAgent.diskMappingHash += 1
            assert res['hash'] != testvm.getStats()['hash']

    @MonkeyPatch(vm, 'config',
                 make_config([('vars', 'vm_command_timeout', '10')]))
    def testMonitorTimeoutResponsive(self):
        with fake.VM(_VM_PARAMS) as testvm:
            assert not testvm.isMigrating()
            stats = {'monitorResponse': '0'}
            testvm._setUnresponsiveIfTimeout(stats, 1)  # any value < timeout
            assert stats['monitorResponse'] == '0'

    @MonkeyPatch(vm, 'config',
                 make_config([('vars', 'vm_command_timeout', '1')]))
    def testMonitorTimeoutUnresponsive(self):
        with fake.VM(_VM_PARAMS) as testvm:
            assert testvm._monitorResponse == 0
            assert not testvm.isMigrating()
            stats = {'monitorResponse': '0'}
            testvm._setUnresponsiveIfTimeout(stats, 10)  # any value > timeout
            assert stats['monitorResponse'] == '-1'

    @MonkeyPatch(vm, 'config',
                 make_config([('vars', 'vm_command_timeout', '10')]))
    def testMonitorTimeoutOnAlreadyUnresponsive(self):
        with fake.VM(_VM_PARAMS) as testvm:
            self._monitorResponse = -1
            assert not testvm.isMigrating()
            stats = {'monitorResponse': '-1'}
            testvm._setUnresponsiveIfTimeout(stats, 1)  # any value < timeout
            assert stats['monitorResponse'] == '-1'


@expandPermutations
class TestLibVirtCallbacks(TestCaseBase):
    FAKE_ERROR = 'EFAKERROR'

    def test_onIOErrorPause(self):
        with fake.VM(_VM_PARAMS, runCpu=True) as testvm:
            assert testvm._guestCpuRunning
            testvm.onIOError('fakedev', self.FAKE_ERROR,
                             libvirt.VIR_DOMAIN_EVENT_IO_ERROR_PAUSE)
            assert not testvm._guestCpuRunning
            assert testvm._pause_code == self.FAKE_ERROR

    def test_onIOErrorReport(self):
        with fake.VM(_VM_PARAMS, runCpu=True) as testvm:
            assert testvm._guestCpuRunning
            testvm.onIOError('fakedev', self.FAKE_ERROR,
                             libvirt.VIR_DOMAIN_EVENT_IO_ERROR_REPORT)
            assert testvm._guestCpuRunning
            assert testvm._pause_code != self.FAKE_ERROR

    def test_onIOErrorNotSupported(self):
        """action not explicitely handled, must be skipped"""
        with fake.VM(_VM_PARAMS, runCpu=True) as testvm:
            assert testvm._guestCpuRunning
            testvm.onIOError('fakedev', self.FAKE_ERROR,
                             libvirt.VIR_DOMAIN_EVENT_IO_ERROR_NONE)
            assert testvm._guestCpuRunning
            assert testvm._pause_code is None  # no error recorded

    @permutations([
        ['net1', set()],
        ['missing', set(('net1',))],
    ])
    def test_onDeviceRemoved(self, alias, kept_aliases):
        devices = '''
<interface type='bridge'>
  <alias name="net1"/>
  <mac address='00:11:22:33:44:55'/>
  <source bridge='ovirtmgmt'/>
  <target dev='vnet0'/>
  <model type='virtio'/>
    <filterref filter='vdsm-no-mac-spoofing'/>
    <address type='pci' domain='0x0000' bus='0x00' slot='0x03'
             function='0x0'/>
</interface>
'''
        with fake.VM(_VM_PARAMS, xmldevices=devices,
                     create_device_objects=True) as testvm:
            testvm._updateDomainDescriptor = lambda *args: None
            testvm.onDeviceRemoved(alias)
            assert set([d.alias for group in testvm._devices.values()
                        for d in group]) == kept_aliases


class TestVmStatusTransitions(TestCaseBase):
    @pytest.mark.slow
    def testSavingState(self):
        with fake.VM(runCpu=True, status=vmstatus.UP) as testvm:
            testvm._dom = fake.Domain(domState=libvirt.VIR_DOMAIN_RUNNING)

            def _asyncEvent():
                testvm.onLibvirtLifecycleEvent(
                    libvirt.VIR_DOMAIN_EVENT_SUSPENDED,
                    -1, -1)

            t = threading.Thread(target=_asyncEvent)
            t.daemon = True

            def _fireAsyncEvent(*args):
                t.start()
                time.sleep(0.5)
                # pause the main thread to let the event one run

            with MonkeyPatchScope([(testvm, '_underlyingPause',
                                    _fireAsyncEvent)]):
                assert testvm.status()['status'] == vmstatus.UP
                testvm.pause(vmstatus.SAVING_STATE)
                assert testvm.status()['status'] == vmstatus.SAVING_STATE
                t.join()
                assert testvm.status()['status'] == vmstatus.SAVING_STATE
                # state must not change even after we are sure the event was
                # handled


class TestVmBalloon(TestCaseBase):
    def assertAPIFailed(self, res, specificErr=None):
        if specificErr is None:
            assert res['status']['code'] != 0
        else:
            assert res['status']['code'] == \
                define.errCode[specificErr]['status']['code']

    def testSucceed(self):
        devices = '<memballoon model="virtio" alias="balloon"/>'
        with fake.VM(
            params={'memSize': 128 * 1024},
            xmldevices=devices,
            create_device_objects=True
        ) as testvm:
            testvm._dom = fake.Domain()
            target = 256 * 1024
            testvm.setBalloonTarget(target)
            assert testvm._dom.__calls__ == [('setMemory', (target,), {})]

    def testVmWithoutDom(self):
        devices = '<memballoon model="virtio" alias="balloon"/>'
        with fake.VM(
            xmldevices=devices,
            create_device_objects=True
        ) as testvm:
            with pytest.raises(exception.BalloonError):
                testvm.setBalloonTarget(128)

    def testTargetValueNotInteger(self):
        devices = '<memballoon model="virtio" alias="balloon"/>'
        with fake.VM(
            xmldevices=devices,
            create_device_objects=True
        ) as testvm:
            with pytest.raises(exception.BalloonError):
                testvm.setBalloonTarget('foobar')

    def testLibvirtFailure(self):
        devices = '<memballoon model="virtio" alias="balloon"/>'
        with fake.VM(
            xmldevices=devices,
            create_device_objects=True
        ) as testvm:
            testvm._dom = fake.Domain(virtError=libvirt.VIR_ERR_INTERNAL_ERROR)
            # we don't care about the error code as long as is != NO_DOMAIN
            with pytest.raises(exception.BalloonError):
                testvm.setBalloonTarget(256)

    def testGetBalloonInfo(self):
        with fake.VM() as testvm:
            assert testvm.get_balloon_info() == {}

    def testSkipBalloonModelNone(self):
        devices = '<memballoon model="none" alias="balloon"/>'
        with fake.VM(
            params={'memSize': 128 * 1024},
            xmldevices=devices,
            create_device_objects=True
        ) as testvm:
            testvm._dom = fake.Domain()
            target = 256 * 1024
            testvm.setBalloonTarget(target)
            assert not hasattr(testvm._dom, '__calls__')

    def testBalloningDisabled(self):
        devices = '<memballoon model="virtio" alias="balloon"/>'
        with fake.VM(
            params={'memSize': 128 * 1024},
            xmldevices=devices,
            create_device_objects=True
        ) as testvm:
            testvm._dom = fake.Domain()
            target = 256 * 1024
            assert testvm._ballooning_enabled  # Verify the default
            testvm._ballooning_enabled = False
            testvm.setBalloonTarget(target)
            assert not hasattr(testvm._dom, '__calls__')
            assert testvm._balloon_target is None


class ChangeBlockDevTests(TestCaseBase):
    def test_update_drive_parameters_failure(self):
        with fake.VM() as testvm:
            testvm.log = FakeLogger()

            # to make the update fail, the simplest way is to have
            # no devices whatsoever
            assert testvm._devices == vmdevices.common.empty_dev_map()
            assert testvm.conf['devices'] == []

            # the method will swallow all the errors
            testvm.updateDriveParameters({'name': 'vda'})

            # nothing should be added...
            assert testvm._devices == vmdevices.common.empty_dev_map()
            assert testvm.conf['devices'] == []

            # ... and the reason for no update should be logged
            assert testvm.log.messages


class FakeVm(vm.Vm):
    """
    Fake Vm required for testing code that does not care about vm state,
    invoking libvirt apis via Vm._dom, and logging via Vm.log.
    """

    log = logging.getLogger()

    def __init__(self, dom):
        self._dom = dom
        self._qga_lock = threading.Lock()


class FreezingTests(TestCaseBase):

    def setUp(self):
        self.dom = fake.Domain()
        self.vm = FakeVm(self.dom)

    def test_freeze(self):
        res = self.vm.freeze()
        assert res == response.success()
        assert self.dom.__calls__ == [("fsFreeze", (), {})]

    def test_thaw(self):
        res = self.vm.thaw()
        assert res == response.success()
        assert self.dom.__calls__ == [("fsThaw", (), {})]


class FreezingGuestAgentUnresponsiveTests(TestCaseBase):

    expected = response.error("nonresp", message="fake error")

    def setUp(self):
        self.dom = fake.Domain(
            virtError=libvirt.VIR_ERR_AGENT_UNRESPONSIVE,
            errorMessage="fake error")
        self.vm = FakeVm(self.dom)

    def test_freeze(self):
        res = self.vm.freeze()
        assert res == self.expected

    def test_thaw(self):
        res = self.vm.thaw()
        assert res == self.expected


class FreezingUnsupportedTests(TestCaseBase):

    expected = response.error("unsupportedOperationErr", message="fake error")

    def setUp(self):
        self.dom = fake.Domain(
            virtError=libvirt.VIR_ERR_NO_SUPPORT,
            errorMessage="fake error")
        self.vm = FakeVm(self.dom)

    def test_freeze(self):
        res = self.vm.freeze()
        assert res == self.expected

    def test_thaw(self):
        res = self.vm.thaw()
        assert res == self.expected


class FreezingUnexpectedErrorTests(TestCaseBase):

    def setUp(self):
        self.dom = fake.Domain(
            virtError=libvirt.VIR_ERR_INTERNAL_ERROR,
            errorMessage="fake error")
        self.vm = FakeVm(self.dom)

    def test_freeze(self):
        res = self.vm.freeze()
        assert res == response.error("freezeErr", message="fake error")

    def test_thaw(self):
        res = self.vm.thaw()
        assert res == response.error("thawErr", message="fake error")


def err_no_domain():
    error = libvirt.libvirtError("No such domain")
    error.err = [libvirt.VIR_ERR_NO_DOMAIN]
    return error


class FakePersistentDomain(object):

    def __init__(self, undefined, uuid, name, state):
        self.id = uuid
        self.name = name
        self._state = state
        self.undefined = undefined

    def state(self, flags):
        return self._state, 0

    def undefineFlags(self, flags=0):
        if self.id in self.undefined:
            raise err_no_domain()
        self.undefined.append(self.id)


class FakePersistentConnection(object):

    def __init__(self, domains):
        self.domains = domains

    def _no_domain_error(self):
        raise err_no_domain()

    def lookupByUUIDString(self, uuid):
        for d in self.domains:
            if d.id == uuid:
                return d
        else:
            raise self._no_domain_error()

    def lookupByName(self, name):
        for d in self.domains:
            if d.name == name:
                return d
        else:
            raise self._no_domain_error()


class FakePersistentVm(object):

    def __init__(self):
        self.id = '123'
        self.name = 'foo'
        self.log = logging.getLogger()


@expandPermutations
class TestVmPersistency(TestCaseBase):

    @permutations([
        ((('123', 'bar', libvirt.VIR_DOMAIN_SHUTOFF),
          ('456', 'foo', libvirt.VIR_DOMAIN_SHUTOFF),),
         ['123', '456'],),
        ((('123', 'bar', libvirt.VIR_DOMAIN_CRASHED),
          ('456', 'foo', libvirt.VIR_DOMAIN_SHUTOFF),),
         ['123', '456'],),
        ((('123', 'foo', libvirt.VIR_DOMAIN_SHUTOFF),
          ('456', 'bar', libvirt.VIR_DOMAIN_SHUTOFF),),
         ['123'],),
        ((('123', 'foo', libvirt.VIR_DOMAIN_SHUTOFF),
          ('456', 'bar', libvirt.VIR_DOMAIN_RUNNING),),
         ['123'],),
        ((('123', 'bar', libvirt.VIR_DOMAIN_SHUTOFF),
          ('456', 'foo', libvirt.VIR_DOMAIN_RUNNING),),
         None,),
        ((('123', 'bar', libvirt.VIR_DOMAIN_RUNNING),
          ('456', 'foo', libvirt.VIR_DOMAIN_SHUTOFF),),
         None,),
        ((('456', 'bar', libvirt.VIR_DOMAIN_RUNNING),
          ('789', 'baz', libvirt.VIR_DOMAIN_SHUTOFF),),
         [],),
    ])
    def test_domain_cleanup(self, domain_specs, result):
        undefined = []
        domains = [FakePersistentDomain(undefined, *s) for s in domain_specs]
        connection = FakePersistentConnection(domains)
        if result is None:
            with pytest.raises(exception.VMExists):
                vm._undefine_stale_domain(FakePersistentVm(), connection)
        else:
            vm._undefine_stale_domain(FakePersistentVm(), connection)
            assert undefined == result


class BlockIoTuneTests(TestCaseBase):

    def setUp(self):
        self.iotune_low = {
            'total_bytes_sec': 0,
            'read_bytes_sec': 1000,
            'write_bytes_sec': 1000,
            'total_iops_sec': 0,
            'write_iops_sec': 0,
            'read_iops_sec': 0
        }
        self.iotune_high = {
            'total_bytes_sec': 0,
            'read_bytes_sec': 2000,
            'write_bytes_sec': 2000,
            'total_iops_sec': 0,
            'write_iops_sec': 0,
            'read_iops_sec': 0
        }
        self.iotune_wrong = {
            'total_bytes_sec': 'XXX',
            'read_bytes_sec': 1000,
            'write_bytes_sec': 1000,
            'total_iops_sec': 0,
            'write_iops_sec': 0,
            'read_iops_sec': 0
        }

        self.drive = FakeBlockIoTuneDrive('vda', path='/fake/path/vda')

        self.dom = FakeBlockIoTuneDomain()
        self.dom.iotunes = {self.drive.name: self.iotune_low.copy()}

    @MonkeyPatch(vm, 'isVdsmImage', lambda *args: True)
    def test_get_fills_cache(self):
        with fake.VM() as testvm:
            testvm._dom = self.dom
            testvm._devices[hwclass.DISK] = (self.drive,)

            res = testvm.io_tune_values()
            assert res
            assert self.dom.__calls__ == \
                [('blockIoTune',
                    (self.drive.name, libvirt.VIR_DOMAIN_AFFECT_LIVE),
                    {})]

            res = testvm.io_tune_values()
            assert res
            assert self.dom.__calls__ == \
                [('blockIoTune',
                    (self.drive.name, libvirt.VIR_DOMAIN_AFFECT_LIVE),
                    {})]

    @MonkeyPatch(vm, 'isVdsmImage', lambda *args: True)
    def test_set_updates_cache(self):
        with fake.VM() as testvm:
            testvm._dom = self.dom
            testvm._devices[hwclass.DISK] = (self.drive,)

            tunables = [
                {"name": self.drive.name, "ioTune": self.iotune_high}
            ]

            res = testvm.io_tune_values()
            self.assert_iotune_in_response(res, self.iotune_low)

            testvm.setIoTune(tunables)

            res = testvm.io_tune_values()
            self.assert_iotune_in_response(res, self.iotune_high)

            assert len(self.dom.__calls__) == 2
            self.assert_nth_call_to_dom_is(0, 'blockIoTune')
            self.assert_nth_call_to_dom_is(1, 'setBlockIoTune')

    @MonkeyPatch(vm, 'isVdsmImage', lambda *args: True)
    def test_set_fills_cache(self):
        with fake.VM() as testvm:
            testvm._dom = self.dom
            testvm._devices[hwclass.DISK] = (self.drive,)

            tunables = [
                {"name": self.drive.name, "ioTune": self.iotune_high}
            ]

            testvm.setIoTune(tunables)

            res = testvm.io_tune_values()
            self.assert_iotune_in_response(res, self.iotune_high)

            assert len(self.dom.__calls__) == 1
            self.assert_nth_call_to_dom_is(0, 'setBlockIoTune')

    @MonkeyPatch(vm, 'isVdsmImage', lambda *args: True)
    def test_cold_cache_set_preempts_get(self):
        with fake.VM() as testvm:
            testvm._dom = self.dom
            testvm._devices[hwclass.DISK] = (self.drive,)

            def _interleaved_update():
                # this will run in the middle of io_tune_values()
                tunables = [
                    {"name": self.drive.name, "ioTune": self.iotune_high}
                ]
                testvm.setIoTune(tunables)

            self.dom.callback = _interleaved_update
            self.assert_iotune_in_response(
                testvm.io_tune_values(),
                self.iotune_low
            )

            assert self.dom.iotunes == \
                {self.drive.name: self.iotune_high}

    @MonkeyPatch(vm, 'isVdsmImage', lambda *args: True)
    def test_set_iotune_invalid(self):
        with fake.VM() as testvm:
            testvm._dom = self.dom
            testvm._devices[hwclass.DISK] = (self.drive,)

            tunables = [
                {"name": self.drive.name, "ioTune": self.iotune_wrong}
            ]

            with pytest.raises(exception.UpdateIOTuneError):
                testvm.setIoTune(tunables)

    @MonkeyPatch(utils, 'config',
                 make_config([('sanlock', 'io_timeout', '1')]))
    def test_exit_with_error_on_resume(self):
        with fake.VM() as testvm:
            pretime = vdsm.common.time.monotonic_time() - 30.0
            testvm._pause_time = pretime
            testvm._resume_behavior = vm.ResumeBehavior.KILL

            testvm._dom = fake.Domain()

            with pytest.raises(vm.DestroyedOnResumeError):
                testvm.maybe_resume()

            testvm.onLibvirtLifecycleEvent(
                libvirt.VIR_DOMAIN_EVENT_STOPPED,
                libvirt.VIR_DOMAIN_EVENT_STOPPED_SHUTDOWN,
                None)

            stats = testvm.getStats()
            assert stats['status'] == vmstatus.DOWN
            assert stats['exitCode'] == define.ERROR
            assert stats['exitReason'] == \
                vmexitreason.DESTROYED_ON_PAUSE_TIMEOUT

    _PAUSED_VMS = {'auto_resume':
                   {'pause_time_offset': 81.0,
                    'resume_behavior': vm.ResumeBehavior.AUTO_RESUME,
                    'pause': True, 'pause_code': 'EIO',
                    'expected_status': vmstatus.PAUSED},
                   'leave_paused':
                   {'pause_time_offset': 81.0,
                    'resume_behavior': vm.ResumeBehavior.LEAVE_PAUSED,
                    'pause': True, 'pause_code': 'EIO',
                    'expected_status': vmstatus.PAUSED},
                   'kill':
                   {'pause_time_offset': 81.0,
                    'resume_behavior': vm.ResumeBehavior.KILL,
                    'pause': True, 'pause_code': 'EIO',
                    'expected_status': vmstatus.DOWN},
                   'paused':
                   {'pause_time_offset': 81.0,
                    'pause': True,
                    'expected_status': vmstatus.PAUSED},
                   'paused_now':
                   {'pause_time_offset': 0.0,
                    'resume_behavior': vm.ResumeBehavior.KILL,
                    'pause': True, 'pause_code': 'EIO',
                    'expected_status': vmstatus.PAUSED},
                   'paused_later':
                   {'pause_time_offset': 70.0,
                    'resume_behavior': vm.ResumeBehavior.KILL,
                    'pause': True, 'pause_code': 'EIO',
                    'expected_status': vmstatus.PAUSED},
                   'running':
                   {'pause_time_offset': 81.0,
                    'pause': False,
                    'expected_status': vmstatus.WAIT_FOR_LAUNCH},
                   }

    def test_kill_long_paused(self):
        cif = fake.ClientIF()
        test = functools.partial(self._kill_long_paused, cif)
        vm_params = []
        for vmid, params in self._PAUSED_VMS.items():
            params = {name: value for name, value in params.items()
                      if name in ('pause_time_offset', 'resume_behavior',)}
            params['cif'] = cif
            params['vmid'] = vmid
            vm_params.append(params)
        fake.run_with_vms(test, vm_params)

    def _kill_long_paused(self, cif, vms):
        spec = self._PAUSED_VMS
        for vm_ in cif.getVMs().values():
            vm_._dom = fake.Domain()
        for vm_ in cif.getVMs().values():
            if not spec[vm_.id]['pause']:
                continue
            vm_.set_last_status(vmstatus.PAUSED)
            pause_code = spec[vm_.id].get('pause_code')
            if pause_code is not None:
                vm_._pause_code = pause_code
        periodic._kill_long_paused_vms(cif)
        for vm_ in cif.getVMs().values():
            expected_status = spec[vm_.id]['expected_status']
            assert vm_.lastStatus == expected_status, \
                ("Wrong status of `%s': actual=%s, expected=%s" %
                 (vm_.id, vm_.lastStatus, expected_status,))

    @MonkeyPatch(vm, 'isVdsmImage', lambda *args: True)
    def test_io_tune_policy_values(self):
        with fake.VM() as testvm:
            testvm._dom = self.dom
            testvm._devices[hwclass.DISK] = (self.drive,)
            assert testvm.io_tune_policy_values() == {
                'current_values': [{
                    'ioTune': self.iotune_low,
                    'name': self.drive.name,
                    'path': self.drive.path,
                }],
                'policy': []
            }

    @MonkeyPatch(vm, 'isVdsmImage', lambda *args: True)
    def test_io_tune_policy_values_handle_exceptions(self):
        with fake.VM() as testvm:
            testvm._dom = virdomain.Disconnected(testvm.id)
            testvm._devices[hwclass.DISK] = (self.drive,)
            assert testvm.io_tune_policy_values() == {}

    def assert_nth_call_to_dom_is(self, nth, call):
        assert self.dom.__calls__[nth][0] == call

    def assert_iotune_in_response(self, res, iotune):
        assert res[0]['ioTune'] == iotune


class FakeBlockIoTuneDrive(object):

    def __init__(self, name, path=None):
        self.name = name
        self.path = path or os.path.join('fake', 'path', name)
        self.iotune = {}
        self._deviceXML = ''

    def getXML(self):
        return xmlutils.fromstring('<fake />')


class FakeBlockIoTuneDomain(object):

    def __init__(self):
        self.iotunes = {}
        self.callback = None

    @recorded
    def blockIoTune(self, name, flags=0):
        ret = self.iotunes.get(name, {}).copy()
        if self.callback is not None:
            self.callback()
        return ret

    @recorded
    def setBlockIoTune(self, name, iotune, flags=0):
        self.iotunes[name] = iotune.copy()


@expandPermutations
class SyncGuestTimeTests(TestCaseBase):

    def _make_vm(self, virt_error=None):
        dom = fake.Domain(virtError=virt_error)
        return FakeVm(dom)

    @MonkeyPatch(time, 'time', lambda: 1234567890.125)
    def test_success(self):
        vm = self._make_vm()
        vm.syncGuestTime()
        assert vm._dom.__calls__ == [
            ('setTime', (), {'time': {'seconds': 1234567890,
                                      'nseconds': 125000000}})
        ]

    @permutations([[libvirt.VIR_ERR_AGENT_UNRESPONSIVE],
                   [libvirt.VIR_ERR_NO_SUPPORT],
                   [libvirt.VIR_ERR_INTERNAL_ERROR]])
    def test_swallow_expected_errors(self, virt_error):
        vm = self._make_vm(virt_error=virt_error)
        with self.assertNotRaises():
            vm.syncGuestTime()


@expandPermutations
class MetadataTests(TestCaseBase):

    _TEST_XML = u'''<?xml version="1.0" encoding="utf-8"?>
<domain type="kvm" xmlns:ovirt-vm="http://ovirt.org/vm/1.0">
  <uuid>TESTING</uuid>
  <metadata>
    <ovirt-vm:vm>
      <ovirt-vm:version type="float">4.2</ovirt-vm:version>
      <ovirt-vm:custom>
        <ovirt-vm:foo>bar</ovirt-vm:foo>
        <ovirt-vm:fizz>buzz</ovirt-vm:fizz>
      </ovirt-vm:custom>
    </ovirt-vm:vm>
  </metadata>
  <devices/>
</domain>'''

    _TEST_XML_CLUSTER_VERSION = u'''<?xml version="1.0" encoding="utf-8"?>
<domain type="kvm" xmlns:ovirt-vm="http://ovirt.org/vm/1.0">
  <uuid>TESTING</uuid>
  <metadata>
    <ovirt-vm:vm>
      <ovirt-vm:version type="float">4.2</ovirt-vm:version>
      <ovirt-vm:clusterVersion>4.2</ovirt-vm:clusterVersion>
      <ovirt-vm:custom>
        <ovirt-vm:foo>bar</ovirt-vm:foo>
        <ovirt-vm:fizz>buzz</ovirt-vm:fizz>
      </ovirt-vm:custom>
    </ovirt-vm:vm>
  </metadata>
  <devices/>
</domain>'''

    _TEST_XML_LAUNCH_PAUSED = u'''<?xml version="1.0" encoding="utf-8"?>
<domain type="kvm" xmlns:ovirt-vm="http://ovirt.org/vm/1.0">
  <uuid>TESTING</uuid>
  <metadata>
    <ovirt-vm:vm>
      <ovirt-vm:launchPaused>true</ovirt-vm:launchPaused>
    </ovirt-vm:vm>
  </metadata>
  <devices/>
</domain>'''

    _TEST_XML_IMPLIED_PIN = u'''<?xml version="1.0" encoding="utf-8"?>
<domain type="kvm" xmlns:ovirt-vm="http://ovirt.org/vm/1.0">
  <uuid>TESTING</uuid>
  <devices/>
  <cputune>
    <vcpupin vcpu='0' cpuset='10'/>
  </cputune>
</domain>'''

    _TEST_XML_DEDICATED = u'''<?xml version="1.0" encoding="utf-8"?>
<domain type="kvm" xmlns:ovirt-vm="http://ovirt.org/vm/1.0">
  <uuid>TESTING</uuid>
  <metadata>
    <ovirt-vm:vm>
      <ovirt-vm:cpuPolicy>dedicated</ovirt-vm:cpuPolicy>
    </ovirt-vm:vm>
  </metadata>
  <devices/>
</domain>'''

    @contextmanager
    def test_vm(self, test_xml=None):
        with namedTemporaryDir() as tmp_dir:
            with MonkeyPatchScope([
                (constants, 'P_VDSM_RUN', tmp_dir),
                (libvirtconnection, 'get', fake.Connection),
            ]):
                params = {
                    'vmId': 'TESTING',
                    'vmName': 'nTESTING',
                    'xml': self._TEST_XML if test_xml is None else test_xml,
                }
                cif = fake.ClientIF()
                yield vm.Vm(cif, params)

    def test_conf_devices_empty(self):
        with self.test_vm() as testvm:
            assert testvm.conf['devices'] == []

    def test_custom_properties(self):
        with self.test_vm() as testvm:
            assert testvm._custom == \
                {
                    'vmId': 'TESTING',
                    'custom':
                    {
                        'foo': 'bar',
                        'fizz': 'buzz',
                    },
                }

    @permutations([
        (3, 6, True,),
        (4, 1, True,),
        (4, 2, True,),
        (4, 3, False,),
        (5, 1, False,),
    ])
    def test_min_cluster_version(self, major, minor, result):
        with self.test_vm(test_xml=self._TEST_XML_CLUSTER_VERSION) as testvm:
            assert testvm.min_cluster_version(major, minor) == result

    @permutations([
        (3, 6, False,),
        (4, 1, False,),
        (4, 2, False,),
        (4, 3, False,),
        (5, 1, False,),
    ])
    def test_void_cluster_version(self, major, minor, result):
        with self.test_vm(test_xml=self._TEST_XML) as testvm:
            assert testvm.min_cluster_version(major, minor) == result

    def test_launch_paused_default_false(self):
        with self.test_vm(test_xml=self._TEST_XML) as testvm:
            assert not testvm._launch_paused

    def test_launch_paused(self):
        with self.test_vm(test_xml=self._TEST_XML_LAUNCH_PAUSED) as testvm:
            assert testvm._launch_paused

    @permutations([
        (_TEST_XML, cpumanagement.CPU_POLICY_NONE),
        (_TEST_XML_IMPLIED_PIN, cpumanagement.CPU_POLICY_MANUAL),
        (_TEST_XML_DEDICATED, cpumanagement.CPU_POLICY_DEDICATED),
    ])
    def test_cpu_policy(self, xml, expected):
        with self.test_vm(test_xml=xml) as testvm:
            assert testvm.cpu_policy() == expected

    def test_manually_pinned_cpus(self):
        with self.test_vm(test_xml=self._TEST_XML_IMPLIED_PIN) as testvm:
            assert testvm.manually_pinned_cpus() == frozenset([0])


class TestQgaContext(TestCaseBase):

    def test_default_timeout(self):
        with fake.VM() as testvm:
            testvm._dom = fake.Domain()
            assert testvm._dom._agent_timeout == \
                libvirt.VIR_DOMAIN_AGENT_RESPONSE_TIMEOUT_BLOCK
            with testvm.qga_context():
                assert testvm._dom._agent_timeout == \
                    libvirt.VIR_DOMAIN_AGENT_RESPONSE_TIMEOUT_BLOCK
            assert testvm._dom._agent_timeout == \
                libvirt.VIR_DOMAIN_AGENT_RESPONSE_TIMEOUT_BLOCK

    def test_reset_default_timeout(self):
        with fake.VM() as testvm:
            testvm._dom = fake.Domain()
            with testvm.qga_context(10):
                assert testvm._dom._agent_timeout == 10
            assert testvm._dom._agent_timeout == \
                libvirt.VIR_DOMAIN_AGENT_RESPONSE_TIMEOUT_BLOCK

    def test_libvirtError_not_handled(self):
        with fake.VM() as testvm:
            testvm._dom = fake.Domain()
            with pytest.raises(libvirt.libvirtError):
                with testvm.qga_context():
                    # This exception should be propagated outside the context
                    raise libvirt.libvirtError("Some error")

    def test_unlock_clean(self):
        with fake.VM() as testvm:
            testvm._dom = fake.Domain()
            with testvm.qga_context():
                assert testvm._qga_lock.locked()
            # Make sure the lock was released properly
            assert not testvm._qga_lock.locked()

    def test_unlock_dirty(self):
        with fake.VM() as testvm:
            testvm._dom = fake.Domain()
            with pytest.raises(libvirt.libvirtError):
                with testvm.qga_context():
                    assert testvm._qga_lock.locked()
                    # Simulate error condition
                    raise libvirt.libvirtError("Some error")
            # Make sure the lock was released properly
            assert not testvm._qga_lock.locked()

    def test_handle_lock_timeout(self):
        with fake.VM() as testvm:
            testvm._dom = fake.Domain()
            # Lock it before entering the context
            testvm._qga_lock.acquire()
            with pytest.raises(exception.NonResponsiveGuestAgent):
                with testvm.qga_context(1):
                    # We should not get here because the attempt to lock should
                    # time out and qga_context should raise an exception.
                    pass


class FakeLeaseDomain(object):

    def attachDevice(self, device_xml):
        pass

    def XMLDesc(self, flags=0):
        return '<domain/>'

    def all_channels(self):
        return []


class FakeLeaseIRS(object):
    def __init__(self, conf):
        self._conf = conf

    def lease_info(self, lease):
        return response.success(result=self._conf)


class FakeLeaseClientIF(object):
    def __init__(self, conf):
        self.irs = FakeLeaseIRS(conf)


def _load_xml(name):
    test_path = os.path.realpath(__file__)
    data_path = os.path.join(
        os.path.split(test_path)[0], '..', 'devices', 'data')

    with open(os.path.join(data_path, name), 'r') as f:
        return f.read()
