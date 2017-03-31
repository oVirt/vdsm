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
import threading

import libvirt

from vdsm import constants
from vdsm import containersconnection
from vdsm import cpuarch
from vdsm import libvirtconnection
from vdsm.common import response
from vdsm.virt import sampling

import clientIF
from virt import vm

from testlib import namedTemporaryDir
from testlib import recorded
from monkeypatch import MonkeyPatchScope
from vmfakecon import Error, Connection


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


@contextmanager
def VM(params=None, devices=None, runCpu=False,
       arch=cpuarch.X86_64, status=None,
       cif=None, create_device_objects=False,
       post_copy=None):
    with namedTemporaryDir() as tmpDir:
        with MonkeyPatchScope([(constants, 'P_VDSM_RUN', tmpDir + '/'),
                               (libvirtconnection, 'get', Connection),
                               (containersconnection, 'get', Connection),
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
            if post_copy is not None:
                fake._post_copy = post_copy
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

    isAlive = is_alive
