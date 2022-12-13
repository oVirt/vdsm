# SPDX-FileCopyrightText: 2012 IBM Corp.
# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

from contextlib import contextmanager
import logging
import threading

import libvirt

from vdsm import constants, schedule
from vdsm.common import cpuarch
from vdsm.common import libvirtconnection
from vdsm.common import response
import vdsm.common.time
from vdsm.common import xmlutils
from vdsm.common.time import monotonic_time
from vdsm.common.units import MiB
from vdsm.storage import exception
from vdsm.virt import domain_descriptor
from vdsm.virt.domain_descriptor import DomainDescriptor, XmlSource
from vdsm.virt import sampling
from vdsm.virt import qemuguestagent
from vdsm.virt import vm
from vdsm.virt.vmdevices import core, storage

from testlib import namedTemporaryDir
from testlib import recorded
from monkeypatch import MonkeyPatchScope
from vmfakecon import Error, Connection


class IRS(object):

    def __init__(self):
        self.ready = True
        self.prepared_volumes = {}
        self.extend_requests = []
        self.sd_types = {}
        self.measure_info = {}

    def getDeviceVisibility(self, guid):
        pass

    def appropriateDevice(self, guid, vmid):
        pass

    def inappropriateDevices(self, ident):
        pass

    def prune_bitmaps(self, sdUUID, imgUUID, volUUID, baseUUID):
        return response.success(result=None)

    def measure(self, sdUUID, imgUUID, volUUID, dest_format, backing=True,
                baseUUID=None):
        # Return fake measure set up by the test. Not setting up anyting will
        # fail; this is a good way to simulate measure error.
        measure = self.measure_info[(volUUID, baseUUID)]
        return response.success(result=measure)

    def getVolumeSize(self, domainID, poolID, imageID, volumeID):
        key = (domainID, imageID, volumeID)
        if key not in self.prepared_volumes:
            error = exception.VolumeDoesNotExist
            return response.error_raw(error.code, error.msg)

        vol_info = self.prepared_volumes[key]

        # NOTE: The real API return strings.
        return response.success(
            apparentsize=str(vol_info['apparentsize']),
            truesize=str(vol_info['truesize']))

    def lease_info(storage_id, lease_id):
        return {
            'result': {
                'path': '/fake/path',
                'offset': 0
            }
        }

    def prepareImage(
            self, sdUUID, spUUID, imgUUID, leafUUID, allowIllegal=False):
        key = (sdUUID, imgUUID, leafUUID)
        path = "/run/storage/{}/{}/{}".format(sdUUID, imgUUID, leafUUID)
        self.prepared_volumes[key] = {"path": path}
        return response.success(
            path=path,
            info={
                "type": self.sd_types.get(sdUUID, storage.DISK_TYPE.FILE),
                "path": path,
            },
            imgVolumesInfo=None
        )

    def teardownImage(self, sdUUID, spUUID, imgUUID, volUUID=None):
        # In real code we deactivate all image volumes and volUUID is never
        # used.
        for k in list(self.prepared_volumes.keys()):
            if k[:2] == (sdUUID, imgUUID):
                del self.prepared_volumes[k]
        return response.success()

    @recorded
    def imageSyncVolumeChain(self, domainID, imageID, volumeID, newVols):
        return response.success()

    def getVolumeInfo(self, sdUUID, spUUID, imgUUID, volUUID):
        key = (sdUUID, imgUUID, volUUID)
        if key not in self.prepared_volumes:
            error = exception.VolumeDoesNotExist
            return response.error_raw(error.code, error.msg)

        # NOTE: The real API returns strings.
        vol_info = self.prepared_volumes[key].copy()
        vol_info["apparentsize"] = str(vol_info["apparentsize"])
        vol_info["truesize"] = str(vol_info["truesize"])
        vol_info["capacity"] = str(vol_info["capacity"])

        return response.success(info=vol_info)

    def setVolumeSize(self, sdUUID, spUUID, imgUUID, volUUID, capacity):
        key = (sdUUID, imgUUID, volUUID)
        if key not in self.prepared_volumes:
            error = exception.VolumeDoesNotExist
            return response.error_raw(error.code, error.msg)

        self.prepared_volumes[key]['capacity'] = capacity
        return response.success()

    def teardownVolume(self, sdUUID, imgUUID, volUUID):
        key = (sdUUID, imgUUID, volUUID)
        if key not in self.prepared_volumes:
            error = exception.VolumeDoesNotExist
            return response.error_raw(error.code, error.msg)

        del self.prepared_volumes[key]
        return response.success()

    def sendExtendMsg(self, spUUID, volDict, newSize, callbackFunc):
        # Volume extend is done async using mailbox in real code, and the
        # caller verifies that volume size has been extended by given callback
        # function. For testing purpose this method only implements the API
        # call and checks that the volume exists in the prepared volumes dict.
        # The test should check for this method call in the extend_requests,
        # and decide whether to extend the volume before invoking the callback
        # function.
        key = (volDict['domainID'], volDict["imageID"], volDict['volumeID'])
        if key not in self.prepared_volumes:
            error = exception.VolumeDoesNotExist
            return response.error_raw(error.code, error.msg)

        self.extend_requests.append((spUUID, volDict, newSize, callbackFunc))
        return response.success()

    def refreshVolume(self, sdUUID, spUUID, imgUUID, volUUID):
        return response.success()


class _Server(object):
    def __init__(self, notifications):
        self.notifications = notifications

    def send(self, message, address):
        self.notifications.append((message, address))


class _Reactor(object):
    def __init__(self, notifications):
        self.server = _Server(notifications)


class _Bridge(object):
    def __init__(self):
        self.event_schema = _Schema()


class _Schema(object):
    def verify_event_params(self, sub_id, args):
        pass


class JsonRpcServer(object):
    def __init__(self):
        self.notifications = []
        self.reactor = _Reactor(self.notifications)
        self.bridge = _Bridge()


class ClientIF(object):
    def __init__(self, irs=None):
        # the bare minimum initialization for our test needs.
        self.irs = irs or IRS()
        self.log = logging.getLogger('fake.ClientIF')
        self.channelListener = None
        self.vm_container_lock = threading.Lock()
        self.vmContainer = {}
        self.vmRequests = {}
        self.bindings = {}
        self._recovery = False
        self.unknown_vm_ids = []
        self._scheduler = schedule.Scheduler(name="test.Scheduler",
                                             clock=monotonic_time)
        self._scheduler.start()
        self.qga_poller = qemuguestagent.QemuGuestAgentPoller(
            self, self.log, self._scheduler)

    def createVm(self, vmParams, vmRecover=False):
        self.vmRequests[vmParams['vmId']] = (vmParams, vmRecover)
        return response.success(vmList={})

    def getInstance(self):
        return self

    def prepareVolumePath(self, drive, vmId=None, path=None):
        if path is not None:
            return path
        elif isinstance(drive, dict):
            return drive['path']
        else:
            return drive

    def teardownVolumePath(self, paramFilespec):
        pass

    def getVMs(self):
        with self.vm_container_lock:
            return self.vmContainer.copy()

    def pop_unknown_vm_ids(self):
        ret = self.unknown_vm_ids
        self.unknown_vm_ids = []
        return ret


class Domain(object):
    def __init__(self, xml='',
                 virtError=libvirt.VIR_ERR_OK,
                 errorMessage="",
                 domState=libvirt.VIR_DOMAIN_RUNNING,
                 domReason=0,
                 vmId='', vm=None):
        if not xml and vm is not None:
            xml = vm.conf.get('xml', '')
        self._xml = xml
        self.devXml = ''
        self.virtError = virtError
        self._errorMessage = errorMessage
        self._metadata = ""
        self._io_tune = {}
        self.domState = domState
        self.domReason = domReason
        self._vmId = vmId
        self.vm = vm
        self._diskErrors = {}
        self._downtimes = []
        self.destroyed = False
        self._agent_timeout = libvirt.VIR_DOMAIN_AGENT_RESPONSE_TIMEOUT_BLOCK

    @property
    def dom(self):
        # Some code check the underlying domain's UUIDString().
        return self

    @property
    def connected(self):
        return True

    def _failIfRequested(self):
        if self.virtError != libvirt.VIR_ERR_OK:
            raise Error(self.virtError, self._errorMessage)

    def UUIDString(self):
        return self._vmId

    def state(self, unused):
        self._failIfRequested()
        return (self.domState, self.domReason)

    def info(self):
        self._failIfRequested()
        return (self.domState, )

    def XMLDesc(self, flags=0):
        return self._xml

    def updateDeviceFlags(self, devXml, unused=0):
        self._failIfRequested()
        self.devXml = devXml

    def vcpusFlags(self, flags):
        return -1

    def metadata(self, type, uri, flags=0):
        self._failIfRequested()

        if not self._metadata:
            e = libvirt.libvirtError("No metadata")
            e.err = [libvirt.VIR_ERR_NO_DOMAIN_METADATA]
            raise e
        return self._metadata

    def setMetadata(self, type, xml, prefix, uri, flags=0):
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

    def memoryStats(self):
        self._failIfRequested()
        return {
            'rss': 4 * MiB
        }

    def destroy(self):
        self.destroyed = True

    def undefineFlags(self, flags=0):
        pass

    def attachDevice(self, device_xml):
        if self._xml:
            dom = xmlutils.fromstring(self._xml)
            devices = dom.find('.//devices')
            attached_device = xmlutils.fromstring(device_xml)
            devices.append(attached_device)
            self._xml = xmlutils.tostring(dom)

    def detachDevice(self, device_xml):
        if self.vm is not None:
            dev = xmlutils.fromstring(device_xml)
            alias = core.find_device_alias(dev)
            self.vm.onDeviceRemoved(alias)

    def agentSetResponseTimeout(self, timeout, flags):
        self._agent_timeout = timeout

    def all_channels(self):
        return []


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


class ConfStub(object):

    def __init__(self, conf):
        self.conf = conf


DEFAULT_DOMAIN_XML = '''
<domain type='kvm' xmlns:ovirt-vm="http://ovirt.org/vm/1.0">
  <name>n{vm_id}</name>
  <uuid>{vm_id}</uuid>
  <memory unit='KiB'>4194304</memory>
  <vcpu current='1'>16</vcpu>
  <os>
    <type arch='x86_64' machine='pc-i440fx-2.3'>hvm</type>
  </os>
  {features}
  {devices}
  {metadata}
</domain>
'''


def default_domain_xml(vm_id='TESTING', features='', devices='', metadata=''):

    def xmlsnippet(content, tag):
        if content:
            snippet = ('<{tag}>{content}</{tag}>'.
                       format(tag=tag, content=content))
        else:
            snippet = '<{tag}/>'.format(tag=tag)
        return snippet

    features_xml = xmlsnippet(features, 'features')
    devices_xml = xmlsnippet(devices, 'devices')
    metadata_xml = xmlsnippet(metadata, 'metadata')
    return DEFAULT_DOMAIN_XML.format(
        vm_id=vm_id,
        features=features_xml,
        devices=devices_xml,
        metadata=metadata_xml
    )


domain_descriptor_init = DomainDescriptor.__init__


def fake_domain_descriptor_init(self, xmlStr, xml_source=XmlSource.LIBVIRT):
    domain_descriptor_init(self, xmlStr)


@contextmanager
def VM(params=None, devices=None, runCpu=False,
       arch=cpuarch.X86_64, status=None,
       cif=None, create_device_objects=False,
       post_copy=None, recover=False, vmid=None,
       resume_behavior=None, pause_code=None, pause_time_offset=None,
       features='', xmldevices='', metadata=''):
    with namedTemporaryDir() as tmpDir:
        with MonkeyPatchScope([
                (constants, 'P_VDSM_RUN', tmpDir),
                (libvirtconnection, 'get', Connection),
                (domain_descriptor.DomainDescriptor, '__init__',
                 fake_domain_descriptor_init),
        ]):
            if params is None:
                params = {}
            if vmid is None:
                vmid = params.get('vmId', 'TESTING')
            if 'xml' not in params:
                params = params.copy()
                params['xml'] = default_domain_xml(
                    vm_id=vmid,
                    features=features,
                    devices=xmldevices,
                    metadata=metadata
                )
            cif = ClientIF() if cif is None else cif
            fake = vm.Vm(cif, params, recover=recover)
            cif.vmContainer[fake.id] = fake
            fake._update_metadata = lambda: None
            fake.send_status_event = lambda **kwargs: None
            fake.arch = arch
            fake.guestAgent = GuestAgent()
            fake.conf['devices'] = [] if devices is None else devices
            if create_device_objects:
                fake._devices = fake._make_devices()
                fake._getUnderlyingVmDevicesInfo()
            fake._guestCpuRunning = runCpu
            if status is not None:
                fake._lastStatus = status
            if post_copy is not None:
                fake._post_copy = post_copy
            if resume_behavior is not None:
                fake._resume_behavior = resume_behavior
            fake._pause_code = pause_code
            if pause_time_offset is not None:
                fake._pause_time = (vdsm.common.time.monotonic_time() -
                                    pause_time_offset)
            sampling.stats_cache.add(fake.id)
            yield fake


def run_with_vms(func, vm_specs):
    vm_kwargs = list(vm_specs)
    vms = []

    def make_vms():
        if not vm_kwargs:
            return func(vms)
        kwargs = vm_kwargs.pop(0)
        with VM(**kwargs) as vm:
            vms.append(vm)
            make_vms()
    make_vms()


class SuperVdsm(object):
    def __init__(self, exception=None):
        self._exception = exception
        self.prepared_path = None
        self.prepared_path_group = None
        self.mirrored_networks = []

    def getProxy(self):
        return self

    def prepareVmChannel(self, path, group=None):
        self.prepared_path = path
        self.prepared_path_group = group

    def setPortMirroring(self, network, nic_name):
        self.mirrored_networks.append((network, nic_name,))

    def unsetPortMirroring(self, network, nic_name):
        self.mirrored_networks.remove((network, nic_name,))


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


class MigrationSourceThread(object):

    def __init__(self, *args, **kwargs):
        self.status = response.success()
        self._alive = False

    def getStat(self):
        pass

    def start(self):
        self._alive = True

    def is_alive(self):
        return self._alive

    def migrating(self):
        return self.is_alive()

    isAlive = is_alive


class Nic(object):

    def __init__(self, name, model, mac_addr):
        self.name = name
        self.nicModel = model
        self.macAddr = mac_addr


def libvirt_error(err, message):
    e = libvirt.libvirtError(message)
    e.err = err
    return e
