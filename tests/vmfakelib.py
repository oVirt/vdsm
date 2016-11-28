#
# Copyright IBM Corp. 2012
# Copyright 2013-2016 Red Hat, Inc.
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

from contextlib import contextmanager
import logging
import os
import re
import threading
import xml.etree.ElementTree as etree

import libvirt

from vdsm import constants
from vdsm import cpuarch
from vdsm import libvirtconnection
from vdsm import response
from vdsm.utils import memoized
from vdsm.virt import sampling

import clientIF
from virt import vm

from testlib import namedTemporaryDir
from testlib import recorded
from monkeypatch import MonkeyPatchScope


_SCSI = """
<device>
    <name>scsi_{0}_0_0_0</name>
    <path>/sys/devices/pci0000:00/0000:00:1f.2/ata5/host4/target4:0:0/
{0}:0:0:0</path>
    <parent>scsi_target4_0_0</parent>
    <driver>
        <name>sd</name>
    </driver>
    <capability type='scsi'>
        <host>4</host>
        <bus>0</bus>
        <target>0</target>
        <lun>0</lun>
        <type>disk</type>
    </capability>
</device>
"""

_STORAGE = """
<device>
    <name>block_sdb_Samsung_SSD_850_PRO_256GB_{0}</name>
    <path>/sys/devices/pci0000:00/0000:00:1f.2/ata5/host4/target4:0:0/
{0}:0:0:0/block/sdb</path>
    <parent>scsi_{0}_0_0_0</parent>
    <capability type='storage'>
        <block>/dev/sdb</block>
        <bus>ata</bus>
        <drive_type>disk</drive_type>
        <model>Samsung SSD 850</model>
        <vendor>ATA</vendor>
        <serial>Samsung_SSD_850_PRO_256GB_{0}</serial>
        <size>256060514304</size>
        <logical_block_size>512</logical_block_size>
        <num_blocks>500118192</num_blocks>
    </capability>
</device>
"""

_SCSI_GENERIC = """
<device>
    <name>scsi_generic_sg{0}</name>
    <path>/sys/devices/pci0000:00/0000:00:1f.2/ata5/host4/target4:0:0/
4:0:0:0/scsi_generic/sg{0}</path>
    <parent>scsi_{0}_0_0_0</parent>
    <capability type='scsi_generic'>
        <char>/dev/sg1</char>
    </capability>
</device>
"""


def Error(code, msg="fake error"):
    e = libvirt.libvirtError(msg)
    e.err = [code, None, msg]
    return e


class Connection(object):

    def __init__(self, *args):
        self.secrets = {}

    def secretDefineXML(self, xml):
        uuid, usage_type, usage_id, description = parse_secret(xml)
        if uuid in self.secrets:
            # If a secret exists, we cannot change its usage_id
            # See libvirt/src/secret/secret_driver.c:782
            sec = self.secrets[uuid]
            if usage_id != sec.usage_id:
                raise Error(libvirt.VIR_ERR_INTERNAL_ERROR)
            sec.usage_type = usage_type
            sec.description = description
        else:
            # (usage_type, usage_id) pair must be unique
            for sec in list(self.secrets.values()):
                if sec.usage_type == usage_type and sec.usage_id == usage_id:
                    raise Error(libvirt.VIR_ERR_INTERNAL_ERROR)
            sec = Secret(self, uuid, usage_type, usage_id, description)
            self.secrets[uuid] = sec
        return sec

    def secretLookupByUUIDString(self, uuid):
        if uuid not in self.secrets:
            raise Error(libvirt.VIR_ERR_NO_SECRET)
        return self.secrets[uuid]

    def listAllSecrets(self, flags=0):
        return list(self.secrets.values())

    def domainEventRegisterAny(self, *arg):
        pass

    def listAllNetworks(self, *args):
        return []

    def nodeDeviceLookupByName(self, name):
        """
        This is a method that allows us to access hostdev XML in a test.
        Normally, libvirt holds the device XML but in case of unit testing,
        we cannot access the libvirt.

        If we want to use hostdev in a test, the XML itself must be supplied
        in tests/devices/data/${device address passed}.
        """
        fakelib_path = os.path.realpath(__file__)
        dir_name = os.path.split(fakelib_path)[0]
        xml_path = os.path.join(
            dir_name, 'devices', 'data', name + '.xml')

        device_xml = None
        with open(xml_path, 'r') as device_xml_file:
            device_xml = device_xml_file.read()

        return VirNodeDeviceStub(device_xml)

    @memoized
    def __hostdevtree(self):
        def string_to_stub(xml_template, index):
            filled_template = xml_template.format(index)
            final_xml = filled_template.replace('  ', '').replace('\n', '')
            return VirNodeDeviceStub(final_xml)

        fakelib_path = os.path.realpath(__file__)
        dir_name = os.path.split(fakelib_path)[0]
        xml_path = os.path.join(dir_name, 'devices', 'data', 'devicetree.xml')

        ret = []
        with open(xml_path, 'r') as device_xml_file:
            for device in device_xml_file:
                ret.append(VirNodeDeviceStub(device))

        for index in range(5, 1000):
            ret.append(string_to_stub(_SCSI, index))
            ret.append(string_to_stub(_STORAGE, index))
            ret.append(string_to_stub(_SCSI_GENERIC, index))

        return ret


class Secret(object):

    def __init__(self, con, uuid, usage_type, usage_id, description):
        self.con = con
        self.uuid = uuid
        self.usage_type = usage_type
        self.usage_id = usage_id
        self.description = description
        self.value = None

    def undefine(self):
        del self.con.secrets[self.uuid]

    def UUIDString(self):
        return self.uuid

    def usageID(self):
        return self.usage_id

    def setValue(self, value):
        self.value = value


def parse_secret(xml):
    root = etree.fromstring(xml)
    uuid = root.find("./uuid").text
    usage_type = root.find("./usage/[@type]").get("type")
    if usage_type == "volume":
        usage_id = root.find("./usage/volume").text
    elif usage_type == "ceph":
        usage_id = root.find("./usage/name").text
    elif usage_type == "iscsi":
        usage_id = root.find("./usage/target").text
    else:
        raise Error(libvirt.VIR_ERR_INTERNAL_ERROR)
    try:
        description = root.find("./description").text
    except AttributeError:
        description = None
    return uuid, usage_type, usage_id, description


class IRS(object):

    def __init__(self):
        self.ready = True

    def inappropriateDevices(self, ident):
        pass


class _Server(object):
    def __init__(self, notifications):
        self.notifications = notifications

    def send(self, message, address):
        self.notifications.append((message, address))


class _Reactor(object):
    def __init__(self, notifications):
        self.server = _Server(notifications)


class JsonRpcServer(object):
    def __init__(self):
        self.notifications = []
        self.reactor = _Reactor(self.notifications)


class ClientIF(clientIF.clientIF):
    def __init__(self):
        # the bare minimum initialization for our test needs.
        self.irs = IRS()  # just to make sure nothing ever happens
        self.log = logging.getLogger('fake.ClientIF')
        self.channelListener = None
        self.vmContainerLock = threading.Lock()
        self.vmContainer = {}
        self.vmRequests = {}
        self.bindings = {}
        self._recovery = False

    def createVm(self, vmParams, vmRecover=False):
        self.vmRequests[vmParams['vmId']] = (vmParams, vmRecover)
        return response.success(vmList={})


class Domain(object):
    def __init__(self, xml='',
                 virtError=libvirt.VIR_ERR_OK,
                 errorMessage="",
                 domState=libvirt.VIR_DOMAIN_RUNNING,
                 domReason=0,
                 vmId=''):
        self._xml = xml
        self.devXml = ''
        self._virtError = virtError
        self._errorMessage = errorMessage
        self._metadata = ""
        self._io_tune = {}
        self._domState = domState
        self._domReason = domReason
        self._vmId = vmId
        self._diskErrors = {}
        self._downtimes = []

    @property
    def connected(self):
        return True

    def _failIfRequested(self):
        if self._virtError != libvirt.VIR_ERR_OK:
            raise Error(self._virtError, self._errorMessage)

    def UUIDString(self):
        return self._vmId

    def state(self, unused):
        self._failIfRequested()
        return (self._domState, self._domReason)

    def info(self):
        self._failIfRequested()
        return (self._domState, )

    def XMLDesc(self, unused):
        return self._xml

    def updateDeviceFlags(self, devXml, unused):
        self._failIfRequested()
        self.devXml = devXml

    def vcpusFlags(self, flags):
        return -1

    def metadata(self, type, uri, flags):
        self._failIfRequested()

        if not self._metadata:
            e = libvirt.libvirtError("No metadata")
            e.err = [libvirt.VIR_ERR_NO_DOMAIN_METADATA]
            raise e
        return self._metadata

    def setMetadata(self, type, xml, prefix, uri, flags):
        self._metadata = xml

    def schedulerParameters(self):
        return {'vcpu_quota': vm._NO_CPU_QUOTA,
                'vcpu_period': vm._NO_CPU_PERIOD}

    def setBlockIoTune(self, name, io_tune, flags):
        self._io_tune[name] = io_tune
        return 1

    @recorded
    def setMemory(self, target):
        self._failIfRequested()

    @recorded
    def setTime(self, time={}):
        self._failIfRequested()

    def setDiskErrors(self, diskErrors):
        self._diskErrors = diskErrors

    def diskErrors(self):
        return self._diskErrors

    def controlInfo(self):
        return (libvirt.VIR_DOMAIN_CONTROL_OK, 0, 0)

    def migrateSetMaxDowntime(self, downtime, flags):
        self._downtimes.append(downtime)

    def getDowntimes(self):
        return self._downtimes

    @recorded
    def fsFreeze(self, mountpoints=None, flags=0):
        self._failIfRequested()
        return 3  # frozen filesystems

    @recorded
    def fsThaw(self, mountpoints=None, flags=0):
        self._failIfRequested()
        return 3  # thawed filesystems

    def shutdownFlags(self, flags):
        pass

    def reboot(self, flags):
        pass


class GuestAgent(object):
    def __init__(self):
        self.guestDiskMapping = {}
        self.diskMappingHash = 0

    def getGuestInfo(self):
        return {
            'username': 'Unknown',
            'session': 'Unknown',
            'memUsage': 0,
            'appsList': [],
            'guestIPs': '',
            'guestFQDN': '',
            'disksUsage': [],
            'netIfaces': [],
            'memoryStats': {},
            'guestCPUCount': -1}

    def stop(self):
        pass


class VirNodeDeviceStub(object):

    def __init__(self, xml):
        self.xml = xml
        self._name = re.search('(?<=<name>).*?(?=</name>)', xml).group(0)
        self.capability = re.search('(?<=capability type=[\'"]).*?(?=[\'"]>)',
                                    xml).group(0)

    def XMLDesc(self, flags=0):
        return self.xml

    def name(self):
        return self._name

    # unfortunately, in real environment these are the most problematic calls
    # but in order to test them, we would put host in danger of removing
    # device needed to run properly (such as nic)

    # the name dettach is defined like *this* in libvirt API, known mistake
    def dettach(self):
        pass

    def reAttach(self):
        pass


class ConfStub(object):

    def __init__(self, conf):
        self.conf = conf


@contextmanager
def VM(params=None, devices=None, runCpu=False,
       arch=cpuarch.X86_64, status=None,
       cif=None, create_device_objects=False):
    with namedTemporaryDir() as tmpDir:
        with MonkeyPatchScope([(constants, 'P_VDSM_RUN', tmpDir + '/'),
                               (libvirtconnection, 'get', Connection),
                               (vm.Vm, 'send_status_event',
                                   lambda _, **kwargs: None)]):
            vmParams = {'vmId': 'TESTING'}
            vmParams.update({} if params is None else params)
            cif = ClientIF() if cif is None else cif
            fake = vm.Vm(cif, vmParams)
            cif.vmContainer[fake.id] = fake
            fake.arch = arch
            fake.guestAgent = GuestAgent()
            fake.conf['devices'] = [] if devices is None else devices
            if create_device_objects:
                fake._devices = fake._devMapFromDevSpecMap(
                    fake._devSpecMapFromConf())
            fake._guestCpuRunning = runCpu
            if status is not None:
                fake._lastStatus = status
            sampling.stats_cache.add(fake.id)
            yield fake


class SuperVdsm(object):
    def __init__(self, exception=None, pid=42):
        self._exception = exception
        self._pid = pid
        self.prepared_path = None
        self.prepared_path_group = None

    def getProxy(self):
        return self

    def getVmPid(self, vmname):
        if self._exception:
            raise self._exception()
        return self._pid

    def getVcpuNumaMemoryMapping(self, vmName):
        return {0: [0, 1], 1: [0, 1], 2: [0, 1], 3: [0, 1]}

    def prepareVmChannel(self, path, group=None):
        self.prepared_path = path
        self.prepared_path_group = group


class SampleWindow:
    def __init__(self):
        self._samples = [(0, 1, 19590000000, 1),
                         (1, 1, 10710000000, 1),
                         (2, 1, 19590000000, 0),
                         (3, 1, 19590000000, 2)]

    def stats(self):
        return [], self._samples, 15

    def last(self):
        return self._samples


class CpuCoreSample(object):

    def __init__(self, samples):
        self._samples = samples

    def getCoreSample(self, key):
        return self._samples.get(key)


class HostSample(object):

    def __init__(self, timestamp, samples):
        self.timestamp = timestamp
        self.cpuCores = CpuCoreSample(samples)


CREATED = "created"
SETUP = "setup"
TEARDOWN = "teardown"


class Device(object):
    log = logging.getLogger('fake.Device')

    def __init__(self, device, fail_setup=None, fail_teardown=None):
        self.fail_setup = fail_setup
        self.fail_teardown = fail_teardown
        self.device = device
        self.state = CREATED

    @recorded
    def setup(self):
        assert self.state is CREATED
        self.state = SETUP

        if self.fail_setup:
            raise self.fail_setup

        self.log.info("%s setup", self.device)

    @recorded
    def teardown(self):
        assert self.state is SETUP
        self.state = TEARDOWN

        if self.fail_teardown:
            raise self.fail_teardown

        self.log.info("%s teardown", self.device)
