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

from contextlib import contextmanager
import logging
import threading

import libvirt

from vdsm import constants
from vdsm.common import cpuarch
from vdsm.common import libvirtconnection
from vdsm.common import response
import vdsm.common.time
from vdsm.common import xmlutils
from vdsm.common.units import MiB
from vdsm.virt import sampling
from vdsm.virt import vm
from vdsm.virt.vmdevices import core

from testlib import namedTemporaryDir
from testlib import recorded
from monkeypatch import MonkeyPatchScope
from vmfakecon import Error, Connection


class IRS(object):

    def __init__(self):
        self.ready = True

    def getDeviceVisibility(self, guid):
        pass

    def appropriateDevice(self, guid, vmid):
        pass

    def inappropriateDevices(self, ident):
        pass

    def getVolumeSize(self, domainID, poolID, imageID, volumeID):
        return response.success(apparentsize=1024, truesize=1024)

    def lease_info(storage_id, lease_id):
        return {
            'result': {
                'path': '/fake/path',
                'offset': 0
            }
        }


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
    def __init__(self):
        # the bare minimum initialization for our test needs.
        self.irs = IRS()  # just to make sure nothing ever happens
        self.log = logging.getLogger('fake.ClientIF')
        self.channelListener = None
        self.vm_container_lock = threading.Lock()
        self.vmContainer = {}
        self.vmRequests = {}
        self.bindings = {}
        self._recovery = False
        self.unknown_vm_ids = []

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

    def XMLDesc(self, unused):
        return self._xml

    def updateDeviceFlags(self, devXml, unused=0):
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


@contextmanager
def VM(params=None, devices=None, runCpu=False,
       arch=cpuarch.X86_64, status=None,
       cif=None, create_device_objects=False,
       post_copy=None, recover=False, vmid=None,
       resume_behavior=None, pause_time_offset=None,
       features='', xmldevices='', metadata=''):
    with namedTemporaryDir() as tmpDir:
        with MonkeyPatchScope([(constants, 'P_VDSM_RUN', tmpDir),
                               (libvirtconnection, 'get', Connection),
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
            fake._sync_metadata = lambda: None
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

    def ovs_bridge(self, network_name):
        return None

    def remove_ovs_port(bridge, port):
        pass

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
