#
# Copyright 2008-2017 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#


# stdlib imports
from collections import defaultdict, namedtuple
from contextlib import contextmanager
import itertools
import logging
import os
import tempfile
import threading
import time
import uuid
import xml.etree.ElementTree as ET

# 3rd party libs imports
import libvirt

# vdsm imports
from vdsm.common import api
from vdsm.common import exception
from vdsm.common import fileutils
from vdsm.common import logutils
from vdsm.common import response
import vdsm.common.time
from vdsm import constants
from vdsm import containersconnection
from vdsm import cpuarch
from vdsm import hooks
from vdsm import host
from vdsm import hugepages
from vdsm import libvirtconnection
from vdsm import osinfo
from vdsm import qemuimg
from vdsm import supervdsm
from vdsm import utils
from vdsm.config import config
from vdsm.common import concurrent
from vdsm.common import conv
from vdsm.common.compat import pickle
from vdsm.common.define import ERROR, NORMAL, doneCode, errCode
from vdsm.common.logutils import SimpleLogAdapter, volume_chain_to_str
from vdsm.host import caps
from vdsm.network import api as net_api
from vdsm.storage import fileUtils
from vdsm.storage import outOfProcess as oop
from vdsm.virt import guestagent
from vdsm.virt import libvirtxml
from vdsm.virt import metadata
from vdsm.virt import migration
from vdsm.virt import recovery
from vdsm.virt import sampling
from vdsm.virt import vmchannels
from vdsm.virt import vmexitreason
from vdsm.virt import virdomain
from vdsm.virt import vmstats
from vdsm.virt import vmstatus
from vdsm.virt import vmtune
from vdsm.virt import vmxml
from vdsm.virt import xmlconstants
from vdsm.virt.domain_descriptor import DomainDescriptor
from vdsm.virt.domain_descriptor import MutableDomainDescriptor
from vdsm.virt import vmdevices
from vdsm.virt.vmdevices import hwclass
from vdsm.virt.vmdevices.storage import DISK_TYPE, VolumeNotFound
from vdsm.virt.vmpowerdown import VmShutdown, VmReboot
from vdsm.virt.utils import isVdsmImage, cleanup_guest_socket, is_kvm

# local imports. TODO: move to vdsm.storage
from storage import sd
from storage import sdc

# A libvirt constant for undefined cpu quota
_NO_CPU_QUOTA = 0

# A libvirt constant for undefined cpu period
_NO_CPU_PERIOD = 0


class VolumeError(RuntimeError):
    def __str__(self):
        return "Bad volume specification " + RuntimeError.__str__(self)


class DoubleDownError(RuntimeError):
    pass


class ImprobableResizeRequestError(RuntimeError):
    pass


class BlockJobExistsError(Exception):
    pass


class BlockCopyActiveError(Exception):
    msg = "Block copy job {self.job_id} is not ready for commit"

    def __init__(self, job_id):
        self.job_id = job_id

    def __str__(self):
        return self.msg.format(self=self)


VALID_STATES = (vmstatus.DOWN, vmstatus.MIGRATION_DESTINATION,
                vmstatus.MIGRATION_SOURCE, vmstatus.PAUSED,
                vmstatus.POWERING_DOWN, vmstatus.REBOOT_IN_PROGRESS,
                vmstatus.RESTORING_STATE, vmstatus.SAVING_STATE,
                vmstatus.UP, vmstatus.WAIT_FOR_LAUNCH)


class ConsoleDisconnectAction:
    NONE = 'NONE'
    LOCK_SCREEN = 'LOCK_SCREEN'
    SHUTDOWN = 'SHUTDOWN'
    LOGOUT = 'LOGOUT'
    REBOOT = 'REBOOT'


# These strings are representing libvirt virDomainEventType values
# http://libvirt.org/html/libvirt-libvirt-domain.html#virDomainEventType
_EVENT_STRINGS = (
    "Defined",
    "Undefined",
    "Started",
    "Suspended",
    "Resumed",
    "Stopped",
    "Shutdown",
    "PM-Suspended",
    "Crashed",
)


def _not_migrating(vm, *args, **kwargs):
    if vm.isMigrating():
        raise exception.MigrationInProgress(vmId=vm.id)


def eventToString(event):
    try:
        return _EVENT_STRINGS[event]
    except IndexError:
        return "Unknown (%i)" % event


class SetLinkAndNetworkError(Exception):
    pass


class UpdatePortMirroringError(Exception):
    pass


VolumeSize = namedtuple("VolumeSize",
                        ["apparentsize", "truesize"])


class MigrationError(Exception):
    pass


class StorageUnavailableError(Exception):
    pass


class HotunplugTimeout(Exception):
    pass


class MissingLibvirtDomainError(Exception):
    def __init__(self, reason=vmexitreason.LIBVIRT_DOMAIN_MISSING):
        super(MissingLibvirtDomainError, self).__init__(
            vmexitreason.exitReasons.get(reason, 'Missing VM'))
        self.reason = reason


@contextmanager
def domain_required():
    try:
        yield
    except libvirt.libvirtError as e:
        # always bubble up this exception.
        if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
            raise MissingLibvirtDomainError()
        else:
            raise


class DestroyedOnStartupError(Exception):
    """
    The VM was destroyed while it was starting up.
    This most likely happens because the startup is very slow.
    """


_MIGRATION_ORIGIN = '_MIGRATION_ORIGIN'
_FILE_ORIGIN = '_FILE_ORIGIN'


class _AlteredState(object):

    def __init__(self, origin=None, path=None, destination=None,
                 from_snapshot=False):
        self.origin = origin
        self.path = path
        self.from_snapshot = from_snapshot
        self.destination = destination

    def __nonzero__(self):
        return self.origin is not None


class Vm(object):
    """
    Used for abstracting communication between various parts of the
    system and Qemu.

    Runs Qemu in a subprocess and communicates with it, and monitors
    its behaviour.
    """

    log = logging.getLogger("virt.vm")
    # limit threads number until the libvirt lock will be fixed
    _ongoingCreations = threading.BoundedSemaphore(4)

    def _makeChannelPath(self, deviceName):
        return constants.P_LIBVIRT_VMCHANNELS + self.id + '.' + deviceName

    @api.logged(on='vdsm.api')
    def __init__(self, cif, params, recover=False):
        """
        Initialize a new VM instance.

        :param cif: The client interface that creates this VM.
        :type cif: :class:`clientIF.clientIF`
        :param params: The VM parameters.
        :type params: dict
        :param recover: Signal if the Vm is recovering;
        :type recover: bool
        """
        self.recovering = recover
        if 'migrationDest' in params:
            self._lastStatus = vmstatus.MIGRATION_DESTINATION
            self._altered_state = _AlteredState(
                _MIGRATION_ORIGIN, destination=params.pop('migrationDest'))
        elif 'restoreState' in params:
            self._lastStatus = vmstatus.RESTORING_STATE
            self._altered_state = _AlteredState(
                _FILE_ORIGIN, path=params.pop('restoreState'),
                from_snapshot=params.pop('restoreFromSnapshot', False))
        else:
            self._lastStatus = vmstatus.WAIT_FOR_LAUNCH
            self._altered_state = _AlteredState()
        # we need to make sure the 'devices' key exists in vm.conf regardless
        # how the Vm is initialized, either through XML or from conf.
        self.conf = {'_blockJobs': {}, 'clientIp': '', 'devices': []}
        self.conf.update(params)
        self.arch = cpuarch.effective()
        self._src_domain_xml = params.get('_srcDomXML')
        if self._src_domain_xml is not None:
            self._domain = DomainDescriptor(self._src_domain_xml)
        elif 'xml' in params:
            self._domain = DomainDescriptor(params['xml'])
        else:
            # If no direct XML representation is available then use a minimal,
            # but still correct, one.  More complete domain will be available
            # and assigned once the VM is started.
            dom = libvirtxml.Domain(self.conf, self.log, self.arch)
            self._domain = DomainDescriptor(dom.toxml())
        self.id = self._domain.id
        self._dom = virdomain.Disconnected(self.id)
        self.cif = cif
        self._custom = {'vmId': self.id}
        if 'xml' in params:
            md = metadata.from_xml(params['xml'])
            self._custom['custom'] = md.get('custom', {})
            self._destroy_on_reboot = md.get('destroy_on_reboot', False)
            for key in ('agentChannelName', 'guestAgentAPIVersion',):
                value = md.get(key)
                if value:
                    self.conf[key] = value
        else:
            self._custom['custom'] = params.get('custom', {})
            self._destroy_on_reboot = False
        self.log = SimpleLogAdapter(self.log, {"vmId": self.id})
        self._destroy_requested = threading.Event()
        self._recovery_file = recovery.File(self.id)
        self._monitorResponse = 0
        self._post_copy = migration.PostCopyPhase.NONE
        self._consoleDisconnectAction = ConsoleDisconnectAction.LOCK_SCREEN
        self._confLock = threading.Lock()
        self._jobsLock = threading.Lock()
        self._statusLock = threading.Lock()
        self._creationThread = concurrent.thread(self._startUnderlyingVm,
                                                 name="vm/" + self.id[:8])
        if recover and params.get('status') == vmstatus.MIGRATION_SOURCE:
            self.log.info("Recovering possibly last_migrating VM")
            last_migrating = True
        else:
            last_migrating = False
        self._migrationSourceThread = migration.SourceThread(
            self, recovery=last_migrating)
        self._incomingMigrationFinished = threading.Event()
        self._incoming_migration_vm_running = threading.Event()
        self._volPrepareLock = threading.Lock()
        self._initTimePauseCode = None
        self._initTimeRTC = int(self.conf.get('timeOffset', 0))
        self._guestEvent = vmstatus.POWERING_UP
        self._guestEventTime = 0
        self._guestCpuRunning = False
        self._guestCpuLock = threading.Lock()
        if recover and 'xml' in params and 'startTime' in md:
            self._startTime = md['startTime']
        else:
            self._startTime = time.time() - \
                float(self.conf.pop('elapsedTimeOffset', 0))

        self._usedIndices = defaultdict(list)  # {'ide': [], 'virtio' = []}
        self.disableDriveMonitor()
        self._vmStartEvent = threading.Event()
        self._vmAsyncStartError = None
        self._vmCreationEvent = threading.Event()
        self.stopped_migrated_event_processed = threading.Event()
        self._pathsPreparedEvent = threading.Event()
        self._devices = vmdevices.common.empty_dev_map()

        if is_kvm(self._custom):
            self._connection = libvirtconnection.get(cif)
        else:
            self._connection = containersconnection.get(cif)
        self._agent_channel_name = self.conf.get('agentChannelName',
                                                 vmchannels.LEGACY_DEVICE_NAME)
        self._guestSocketFile = self._makeChannelPath(self._agent_channel_name)
        self._qemuguestSocketFile = self._makeChannelPath(
            vmchannels.QEMU_GA_DEVICE_NAME)
        self._guest_agent_api_version = self.conf.pop('guestAgentAPIVersion',
                                                      None)
        self.guestAgent = guestagent.GuestAgent(
            self._guestSocketFile, self.cif.channelListener, self.log,
            self._onGuestStatusChange,
            self._guest_agent_api_version)
        self._released = threading.Event()
        self._releaseLock = threading.Lock()
        self._watchdogEvent = {}
        self._powerDownEvent = threading.Event()
        self._liveMergeCleanupThreads = {}
        self._shutdownLock = threading.Lock()
        self._shutdownReason = None
        self._vcpuLimit = None
        self._vcpuTuneInfo = {}
        self._ioTuneLock = threading.Lock()
        self._ioTuneInfo = []
        self._ioTuneValues = {}
        self._vmJobs = None
        self._clientPort = ''
        self._monitorable = False
        self._migration_downtime = None

    @property
    def monitorable(self):
        if self._altered_state or \
           self.post_copy != migration.PostCopyPhase.NONE:
            return False
        return self._monitorable

    @property
    def start_time(self):
        return self._startTime

    @property
    def domain(self):
        return self._domain

    @property
    def post_copy(self):
        return self._post_copy

    @property
    def hugepages(self):
        custom = self._custom['custom']
        hugepages_enabled = int(custom.get('hugepages', 0))
        return hugepages_enabled > 0

    @property
    def hugepagesz(self):
        custom = self._custom['custom']
        hugepagesz = int(custom.get('hugepages', 0))
        if hugepagesz not in hugepages.supported():
            default_size = hugepages.DEFAULT_HUGEPAGESIZE[cpuarch.real()]
            self.log.warning('Invalid hugepage size configured, '
                             'falling back to the default size %s',
                             default_size)
            return default_size
        return hugepagesz

    @property
    def nr_hugepages(self):
        # Integer ceiling (m + n - 1) // n.
        return (
            (self.mem_size_mb() * 1024 + self.hugepagesz - 1) //
            self.hugepagesz
        )

    def _get_lastStatus(self):
        # note that we don't use _statusLock here. One of the reasons is the
        # non-obvious recursive locking in the following flow:
        # set_last_status() -> saveState() -> status() -> _get_lastStatus().
        status = self._lastStatus
        if not self._guestCpuRunning and status in vmstatus.PAUSED_STATES:
            return vmstatus.PAUSED
        return status

    def set_last_status(self, value, check_last_status=None):
        with self._statusLock:
            if check_last_status is not None and \
               self._lastStatus != check_last_status:
                # The point of this check is to avoid using _statusLock outside
                # this method. We may want to set a certain status during
                # recovery, but we want to give a priority to status changes
                # that can happen concurrently such as those based on life
                # cycle events. If that happens, we don't override the
                # concurrently set status and simply return.
                return
            if self._lastStatus == vmstatus.DOWN:
                self.log.warning(
                    'trying to set state to %s when already Down',
                    value)
                if value == vmstatus.DOWN:
                    raise DoubleDownError
                else:
                    return
            if value not in VALID_STATES:
                self.log.error('setting state to %s', value)
            if self._lastStatus != value:
                self.saveState()
                self._lastStatus = value

    def send_status_event(self, **kwargs):
        stats = {'status': self._getVmStatus()}
        stats.update(kwargs)
        self._notify('VM_status', stats)

    def send_migration_status_event(self):
        migrate_status = self.migrateStatus()
        postcopy = self._post_copy == migration.PostCopyPhase.RUNNING
        status = {
            'progress': migrate_status['progress'],
            'postcopy': postcopy,
        }
        if 'downtime' in migrate_status:
            status['downtime'] = migrate_status['downtime']
        self._notify('VM_migration_status', status)

    def _notify(self, operation, params):
        sub_id = '|virt|%s|%s' % (operation, self.id)
        self.cif.notify(sub_id, {self.id: params})

    def _onGuestStatusChange(self):
        self.send_status_event(**self._getGuestStats())

    def _get_status_time(self):
        """
        Value provided by this method is used to order messages
        containing changed status on the engine side.
        """
        return str(int(vdsm.common.time.monotonic_time() * 1000))

    lastStatus = property(_get_lastStatus, set_last_status)

    def __getNextIndex(self, used):
        for n in xrange(max(used or [0]) + 2):
            if n not in used:
                idx = n
                break
        return str(idx)

    def _normalizeVdsmImg(self, drv):
        drv['reqsize'] = drv.get('reqsize', '0')  # Backward compatible
        if 'device' not in drv:
            drv['device'] = 'disk'

        if drv['device'] == 'disk':
            volsize = self._getVolumeSize(drv['domainID'], drv['poolID'],
                                          drv['imageID'], drv['volumeID'])
            drv['truesize'] = str(volsize.truesize)
            drv['apparentsize'] = str(volsize.apparentsize)
        else:
            drv['truesize'] = 0
            drv['apparentsize'] = 0

    def _dev_spec_update_with_vm_conf(self, dev):
        dev['vmid'] = self.id
        if dev['type'] == hwclass.GRAPHICS:
            if 'specParams' not in dev:
                dev['specParams'] = {}
            if 'displayNetwork' not in dev['specParams']:
                dev['specParams']['displayNetwork'] = self.conf.get(
                    'displayNetwork'
                )
        if dev['type'] in (hwclass.DISK, hwclass.NIC):
            vm_custom = self._custom['custom']
            self.log.debug('device %s: adding VM custom properties %s',
                           dev['type'], vm_custom)
            dev['vm_custom'] = vm_custom
        return dev

    def _devSpecMapFromConf(self):
        """
        Return the "devices" section of this Vm's conf.
        If missing, create it according to old API.
        """
        devices = vmdevices.common.empty_dev_map()

        # while this code is running, Vm is queryable for status(),
        # thus we must fix devices in an atomic way, hence the deep copy
        with self._confLock:
            devConf = utils.picklecopy(self.conf['devices'])

        for dev in devConf:
            dev = self._dev_spec_update_with_vm_conf(dev)
            try:
                devices[dev['type']].append(dev)
            except KeyError:
                if 'type' not in dev or dev['type'] != 'channel':
                    self.log.warn("Unknown type found, device: '%s' "
                                  "found", dev)
                devices[hwclass.GENERAL].append(dev)

        self._checkDeviceLimits(devices)

        # Normalize vdsm images
        for drv in devices[hwclass.DISK]:
            if isVdsmImage(drv):
                try:
                    self._normalizeVdsmImg(drv)
                except StorageUnavailableError:
                    # storage unavailable is not fatal on recovery;
                    # the storage subsystem monitors the devices
                    # and will notify when they come up later.
                    if not self.recovering:
                        raise

        self.normalizeDrivesIndices(devices[hwclass.DISK])

        # Preserve old behavior. Since libvirt add a memory balloon device
        # to all guests, we need to specifically request not to add it.
        self._normalizeBalloonDevice(devices[hwclass.BALLOON])

        return devices

    def _normalizeBalloonDevice(self, balloonDevices):
        EMPTY_BALLOON = {'type': hwclass.BALLOON,
                         'device': 'memballoon',
                         'specParams': {
                             'model': 'none'}}

        # TODO: in the engine XML path we will need to fetch this data
        # from the device metadata

        # Avoid overriding the saved balloon target value on recovery.
        if not self.recovering:
            for dev in balloonDevices:
                dev['target'] = int(self.conf.get('memSize')) * 1024
                dev['minimum'] = int(
                    self.conf.get('memGuaranteedSize', '0')
                ) * 1024

        if not balloonDevices:
            balloonDevices.append(EMPTY_BALLOON)

    def _checkDeviceLimits(self, devices):
        # libvirt only support one watchdog and one console device
        for device in (hwclass.WATCHDOG, hwclass.CONSOLE):
            if len(devices[device]) > 1:
                raise ValueError("only a single %s device is "
                                 "supported" % device)
        graphDevTypes = set()
        for dev in devices[hwclass.GRAPHICS]:
            if dev.get('device') not in graphDevTypes:
                graphDevTypes.add(dev.get('device'))
            else:
                raise ValueError("only a single graphic device "
                                 "per type is supported")

    def updateDriveIndex(self, drv):
        drv['index'] = self.__getNextIndex(self._usedIndices[
            self._indiceForIface(drv['iface'])
        ])
        self._usedIndices[self._indiceForIface(drv['iface'])].append(
            int(drv['index'])
        )

    def normalizeDrivesIndices(self, confDrives):
        drives = [(order, drv) for order, drv in enumerate(confDrives)]
        indexed = []
        for order, drv in drives:
            idx = drv.get('index')
            if idx is not None:
                self._usedIndices[self._indiceForIface(drv['iface'])].append(
                    int(idx)
                )
                indexed.append(order)

        for order, drv in drives:
            if order not in indexed:
                self.updateDriveIndex(drv)

        return [drv for order, drv in drives]

    def _indiceForIface(self, iface):
        '''
        Small helper to group certain interfaces under the same *bucket*.
        This is done to avoid interfaces with same node name (sd*) from
        colliding.
        '''
        if iface == 'sata' or iface == 'scsi':
            return 'sd'
        return iface

    def run(self):
        self._creationThread.start()
        self._vmStartEvent.wait()
        if self._vmAsyncStartError:
            return self._vmAsyncStartError

        return response.success(vmList=self.status())

    def mem_size_mb(self):
        mem_size_mb = self._domain.get_memory_size()
        if mem_size_mb is None:
            self._updateDomainDescriptor()
            mem_size_mb = self._domain.get_memory_size()
        return mem_size_mb

    def memory_info(self):
        """
        Return type is dict with keys:
        - commit (int): committed memory for the VM (Kbytes)
        - rss (int): resident memory used by the VM (kbytes)
        """
        memory = self.mem_size_mb()
        memory += config.getint('vars', 'guest_ram_overhead')
        mem_stats = {'commit': 2 ** 10 * memory}
        try:
            dom_stats = self._dom.memoryStats()
        except libvirt.libvirtError:
            # just skip for this cycle, no real harm
            pass
        except virdomain.NotConnectedError:
            # race on startup/shutdown, no real harm
            pass
        else:
            mem_stats['rss'] = dom_stats['rss']
        return mem_stats

    def hibernate(self, dst):
        hooks.before_vm_hibernate(self._dom.XMLDesc(0), self._custom)
        fname = self.cif.prepareVolumePath(dst)
        try:
            self._dom.save(fname)
        finally:
            self.cif.teardownVolumePath(dst)

    def prepare_migration(self):
        for dev in self._customDevices():
            hooks.before_device_migrate_source(
                dev._deviceXML, self._custom, dev.custom)
        hooks.before_vm_migrate_source(self._dom.XMLDesc(0), self._custom)

    def _startUnderlyingVm(self):
        self.log.debug("Start")
        acquired = False
        if self._altered_state.origin == _MIGRATION_ORIGIN:
            self.log.debug('Acquiring incoming migration semaphore.')
            acquired = migration.incomingMigrations.acquire(blocking=False)
            if not acquired:
                self._vmAsyncStartError = response.error('migrateLimit')
                self._vmStartEvent.set()
                return

        self.saveState()
        self._vmStartEvent.set()
        try:
            with self._ongoingCreations:
                self._vmCreationEvent.set()
                try:
                    with domain_required():
                        self._run()
                except MissingLibvirtDomainError:
                    # always bubble up this exception.
                    # we cannot continue without a libvirt domain object,
                    # not even on recovery, to avoid state desync or worse
                    # split-brain scenarios.
                    raise
                except Exception as e:
                    if not self.recovering:
                        raise
                    else:
                        self.log.info("Skipping errors on recovery",
                                      exc_info=True)

            if self._altered_state and self.lastStatus != vmstatus.DOWN:
                self._completeIncomingMigration()
            if self.lastStatus == vmstatus.MIGRATION_DESTINATION:
                # Waiting for post-copy migration to finish before we can
                # change status to UP.
                self._incomingMigrationFinished.wait(
                    config.getint('vars', 'migration_destination_timeout'))
                # Wait a bit to increase the chance that downtime is reported
                # from the source before we report that the VM is UP on the
                # destination.  This makes migration completion handling in
                # Engine easier.
                time.sleep(1)

            if self.recovering and \
               self._lastStatus == vmstatus.WAIT_FOR_LAUNCH and \
               self._migrationSourceThread.recovery:
                self._recover_status()
            else:
                self.lastStatus = vmstatus.UP
            if self._initTimePauseCode:
                with self._confLock:
                    self.conf['pauseCode'] = self._initTimePauseCode
                if self._initTimePauseCode == 'ENOSPC':
                    self.cont()
            else:
                try:
                    with self._confLock:
                        del self.conf['pauseCode']
                except KeyError:
                    pass

            self.recovering = False
            self.saveState()

            self.send_status_event(**self._getRunningVmStats())

        except MissingLibvirtDomainError as e:
            # we cannot ever deal with this error, not even on recovery.
            self.setDownStatus(
                self.conf.get('exitCode', ERROR),
                self.conf.get('exitReason', e.reason),
                self.conf.get('exitMessage', ''))
            self.recovering = False
        except DestroyedOnStartupError:
            # this could not happen on recovery
            self.setDownStatus(NORMAL, vmexitreason.DESTROYED_ON_STARTUP)
        except MigrationError:
            self.log.exception("Failed to start a migration destination vm")
            self.setDownStatus(ERROR, vmexitreason.MIGRATION_FAILED)
        except Exception as e:
            if self.recovering:
                self.log.info("Skipping errors on recovery", exc_info=True)
            else:
                self.log.exception("The vm start process failed")
                self.setDownStatus(ERROR, vmexitreason.GENERIC_ERROR, str(e))
        finally:
            if acquired:
                self.log.debug('Releasing incoming migration semaphore')
                migration.incomingMigrations.release()

    def _recover_status(self):
        try:
            state, reason = self._dom.state(0)
        except libvirt.libvirtError:
            # We proceed with the best effort setting in case of error.
            self.set_last_status(vmstatus.UP, vmstatus.WAIT_FOR_LAUNCH)
            return
        if state == libvirt.VIR_DOMAIN_PAUSED:
            if reason == libvirt.VIR_DOMAIN_PAUSED_POSTCOPY:
                self.set_last_status(vmstatus.MIGRATION_SOURCE,
                                     vmstatus.WAIT_FOR_LAUNCH)
                self._initTimePauseCode = 'POSTCOPY'
                self._post_copy = migration.PostCopyPhase.RUNNING
            elif reason == libvirt.VIR_DOMAIN_PAUSED_MIGRATION:
                self.set_last_status(vmstatus.MIGRATION_SOURCE,
                                     vmstatus.WAIT_FOR_LAUNCH)
            elif reason == libvirt.VIR_DOMAIN_PAUSED_POSTCOPY_FAILED:
                self.log.warning("VM is after post-copy failure, "
                                 "destroying it: %s" % (self.id,))
                self.setDownStatus(
                    ERROR, vmexitreason.POSTCOPY_MIGRATION_FAILED)
                self.destroy()
            else:
                self.set_last_status(vmstatus.PAUSED, vmstatus.WAIT_FOR_LAUNCH)
        elif state == libvirt.VIR_DOMAIN_RUNNING:
            try:
                job_stats = self._dom.jobStats()
            except libvirt.libvirtError:
                self.set_last_status(vmstatus.UP, vmstatus.WAIT_FOR_LAUNCH)
            else:
                if migration.ongoing(job_stats):
                    self.set_last_status(vmstatus.MIGRATION_SOURCE,
                                         vmstatus.WAIT_FOR_LAUNCH)
                    self._migrationSourceThread.start()
                else:
                    self.set_last_status(vmstatus.UP, vmstatus.WAIT_FOR_LAUNCH)
        else:
            self.log.error("Unexpected VM state: %s (reason %s)",
                           state, reason)

    def disableDriveMonitor(self):
        self._driveMonitorEnabled = False

    def enableDriveMonitor(self):
        self._driveMonitorEnabled = True

    def driveMonitorEnabled(self):
        return self._driveMonitorEnabled

    def preparePaths(self):
        drives = self._devSpecMapFromConf()[hwclass.DISK]
        self._preparePathsForDrives(drives)

    def _preparePathsForDrives(self, drives):
        for drive in drives:
            with self._volPrepareLock:
                if self._destroy_requested.is_set():
                    # A destroy request has been issued, exit early
                    break
                drive['path'] = self.cif.prepareVolumePath(drive, self.id)
                if isVdsmImage(drive):
                    # This is the only place we support manipulation of a
                    # prepared image, required for the localdisk hook. The hook
                    # may change drive parameters like path and format.
                    modified = hooks.after_disk_prepare(drive, self._custom)
                    drive.update(modified)
        else:
            # Now we got all the resources we needed
            self.enableDriveMonitor()

    def _prepareTransientDisks(self, drives):
        for drive in drives:
            self._createTransientDisk(drive)

    def _getShutdownReason(self, stopped_shutdown):
        exit_code = NORMAL
        with self._shutdownLock:
            reason = self._shutdownReason
        if stopped_shutdown:
            # do not overwrite admin shutdown, if present
            if reason is None:
                # seen_shutdown is used to detect VMs that have been
                # stopped by sending them SIG_TERM (e.g. system shutdown).
                # In that case libvirt and qemu report a user initiated
                # shutdown that is not correct.
                # BZ on libvirt: https://bugzilla.redhat.com/1384007
                seen_shutdown = not self.guestAgent or \
                    self.guestAgent.has_seen_shutdown()
                if seen_shutdown:
                    reason = vmexitreason.USER_SHUTDOWN
                else:
                    reason = vmexitreason.HOST_SHUTDOWN
                    exit_code = ERROR
        return exit_code, reason

    def _onQemuDeath(self, exit_code, reason):
        self.log.info('underlying process disconnected')
        self._dom = virdomain.Disconnected(self.id)
        # Try release VM resources first, if failed stuck in 'Powering Down'
        # state
        self._destroy_requested.set()
        result = self.releaseVm()
        if not result['status']['code']:
            if reason is None:
                self.setDownStatus(ERROR, vmexitreason.LOST_QEMU_CONNECTION)
            else:
                self.setDownStatus(exit_code, reason)
        self._powerDownEvent.set()

    def _loadCorrectedTimeout(self, base, doubler=20, load=None):
        """
        Return load-corrected base timeout

        :param base: base timeout, when system is idle
        :param doubler: when (with how many running VMs) should base timeout be
                        doubled
        :param load: current load, number of VMs by default
        """
        if load is None:
            load = len(self.cif.vmContainer)
        return base * (doubler + load) / doubler

    def saveState(self):
        self._recovery_file.save(self)

        try:
            self._updateDomainDescriptor()
        except Exception:
            # we do not care if _dom suddenly died now
            pass

    def onReboot(self):
        try:
            self.log.info('reboot event')
            self._startTime = time.time()
            self._guestEventTime = self._startTime
            self._guestEvent = vmstatus.REBOOT_IN_PROGRESS
            self._powerDownEvent.set()
            self.saveState()
            # this always triggers onStatusChange event, which
            # also sends back status event to Engine.
            self.guestAgent.onReboot()
            if self.conf.get('volatileFloppy'):
                self._ejectFloppy()
                self.log.debug('ejected volatileFloppy')
            if self._destroy_on_reboot:
                self.doDestroy(reason=vmexitreason.DESTROYED_ON_REBOOT)
        except Exception:
            self.log.exception("Reboot event failed")

    def onConnect(self, clientIp='', clientPort=''):
        if clientIp:
            with self._confLock:
                self.conf['clientIp'] = clientIp
            self._clientPort = clientPort

    def set_destroy_on_reboot(self):
        # TODO: when https://bugzilla.redhat.com/show_bug.cgi?id=1460677 is
        # implemented, change to use native libvirt API
        self._destroy_on_reboot = True
        self._update_metadata()

    @logutils.traceback()
    def _timedDesktopLock(self):
        # This is not a definite fix, we're aware that there is still the
        # possibility of a race condition, however this covers more cases
        # than before and a quick gain
        if (not self.conf.get('clientIp', '') and
           not self._destroy_requested.is_set()):
            delay = config.get('vars', 'user_shutdown_timeout')
            timeout = config.getint('vars', 'sys_shutdown_timeout')
            CDA = ConsoleDisconnectAction
            if self._consoleDisconnectAction == CDA.LOCK_SCREEN:
                self.guestAgent.desktopLock()
            elif self._consoleDisconnectAction == CDA.LOGOUT:
                self.guestAgent.desktopLogoff(False)
            elif self._consoleDisconnectAction == CDA.REBOOT:
                self.shutdown(delay=delay, reboot=True, timeout=timeout,
                              message='Scheduled reboot on disconnect',
                              force=True)
            elif self._consoleDisconnectAction == CDA.SHUTDOWN:
                self.shutdown(delay=delay, reboot=False, timeout=timeout,
                              message='Scheduled shutdown on disconnect',
                              force=True)

    def onDisconnect(self, detail=None, clientIp='', clientPort=''):
        if self.conf['clientIp'] != clientIp:
            self.log.debug('Ignoring disconnect event because ip differs')
            return
        if self._clientPort and self._clientPort != clientPort:
            self.log.debug('Ignoring disconnect event because ports differ')
            return

        self.conf['clientIp'] = ''
        # This is a hack to mitigate the issue of spice-gtk not respecting the
        # configured secure channels. Spice-gtk is always connecting first to
        # a non-secure channel and the server tells the client then to connect
        # to a secure channel. However as a result of this we're getting events
        # of false positive disconnects and we need to ensure that we're really
        # having a disconnected client
        # This timer is supposed to delay the call to lock the desktop of the
        # guest. And only lock it, if it there was no new connect.
        # This is detected by the clientIp being set or not.
        #
        # Multiple desktopLock calls won't matter if we're really disconnected
        # It is not harmful. And the threads will exit after 2 seconds anyway.
        _DESKTOP_LOCK_TIMEOUT = 2
        timer = threading.Timer(_DESKTOP_LOCK_TIMEOUT, self._timedDesktopLock)
        timer.start()

    def onRTCUpdate(self, timeOffset):
        newTimeOffset = str(self._initTimeRTC + int(timeOffset))
        self.log.debug('new rtc offset %s', newTimeOffset)
        with self._confLock:
            self.conf['timeOffset'] = newTimeOffset

    def _getExtendCandidates(self):
        ret = []

        for drive in self._chunkedDrives():
            try:
                capacity, alloc, physical = self._getExtendInfo(drive)
            except libvirt.libvirtError as e:
                self.log.error("Unable to get watermarks for drive %s: %s",
                               drive.name, e)
                continue

            ret.append((drive, drive.volumeID, capacity, alloc, physical))

        return ret

    def _chunkedDrives(self):
        """
        Return list of chunked drives, or non-chunked drives replicating to
        chunked replica drive.
        """
        return [drive for drive in self._devices[hwclass.DISK]
                if drive.chunked or drive.replicaChunked]

    def _getExtendInfo(self, drive):
        """
        Return extension info for a chunked drive or drive replicating to
        chunked replica volume.
        """
        capacity, alloc, physical = self._dom.blockInfo(drive.path, 0)

        # Libvirt reports watermarks only for the source drive, but for
        # file-based drives it reports the same alloc and physical, which
        # breaks our extend logic. Since drive is chunked, we must have a
        # disk-based replica, so we get the physical size from the replica.

        if not drive.chunked:
            replica = drive.diskReplicate
            volsize = self._getVolumeSize(replica["domainID"],
                                          replica["poolID"],
                                          replica["imageID"],
                                          replica["volumeID"])
            physical = volsize.apparentsize

        return capacity, alloc, physical

    def _shouldExtendVolume(self, drive, volumeID, capacity, alloc, physical):
        nextPhysSize = drive.getNextVolumeSize(physical, capacity)

        # NOTE: the intent of this check is to prevent faulty images to
        # trick qemu in requesting extremely large extensions (BZ#998443).
        # Probably the definitive check would be comparing the allocated
        # space with capacity + format_overhead. Anyway given that:
        #
        # - format_overhead is tricky to be computed (it depends on few
        #   assumptions that may change in the future e.g. cluster size)
        # - currently we allow only to extend by one chunk at time
        #
        # the current check compares alloc with the next volume size.
        # It should be noted that alloc cannot be directly compared with
        # the volume physical size as it includes also the clusters not
        # written yet (pending).
        if alloc > nextPhysSize:
            msg = ("Improbable extension request for volume %s on domain "
                   "%s, pausing the VM to avoid corruptions (capacity: %s, "
                   "allocated: %s, physical: %s, next physical size: %s)" %
                   (volumeID, drive.domainID, capacity, alloc, physical,
                    nextPhysSize))
            self.log.error(msg)
            self.pause(pauseCode='EOTHER')
            raise ImprobableResizeRequestError(msg)

        if physical >= drive.getMaxVolumeSize(capacity):
            # The volume was extended to the maximum size. physical may be
            # larger than maximum volume size since it is rounded up to the
            # next lvm extent.
            return False

        if physical - alloc < drive.watermarkLimit:
            return True
        return False

    def needsDriveMonitoring(self):
        """
        Return True if a vm needs drive monitoring in this cycle.

        This is called every 2 seconds (configurable) by the periodic system.
        If this returns True, the periodic system will invoke
        extendDrivesIfNeeded during this periodic cycle.
        """
        return self._driveMonitorEnabled and bool(self._chunkedDrives())

    def extendDrivesIfNeeded(self):
        try:
            extend = [x for x in self._getExtendCandidates()
                      if self._shouldExtendVolume(*x)]
        except ImprobableResizeRequestError:
            return False

        for drive, volumeID, capacity, alloc, physical in extend:
            self.log.info(
                "Requesting extension for volume %s on domain %s (apparent: "
                "%s, capacity: %s, allocated: %s, physical: %s)",
                volumeID, drive.domainID, drive.apparentsize, capacity,
                alloc, physical)
            self.extendDriveVolume(drive, volumeID, physical, capacity)

        return len(extend) > 0

    def extendDriveVolume(self, vmDrive, volumeID, curSize, capacity):
        """
        Extend drive volume and its replica volume during replication.

        Must be called only when the drive or its replica are chunked.
        """
        newSize = vmDrive.getNextVolumeSize(curSize, capacity)

        # If drive is replicated to a block device, we extend first the
        # replica, and handle drive later in __afterReplicaExtension.

        if vmDrive.replicaChunked:
            self.__extendDriveReplica(vmDrive, newSize)
        else:
            self.__extendDriveVolume(vmDrive, volumeID, newSize)

    def __refreshDriveVolume(self, volInfo):
        self.cif.irs.refreshVolume(volInfo['domainID'], volInfo['poolID'],
                                   volInfo['imageID'], volInfo['volumeID'])

    def __verifyVolumeExtension(self, volInfo):
        self.log.debug("Refreshing drive volume for %s (domainID: %s, "
                       "volumeID: %s)", volInfo['name'], volInfo['domainID'],
                       volInfo['volumeID'])

        self.__refreshDriveVolume(volInfo)
        volSize = self._getVolumeSize(volInfo['domainID'], volInfo['poolID'],
                                      volInfo['imageID'], volInfo['volumeID'])

        self.log.debug("Verifying extension for volume %s, requested size %s, "
                       "current size %s", volInfo['volumeID'],
                       volInfo['newSize'], volSize.apparentsize)

        if volSize.apparentsize < volInfo['newSize']:
            raise RuntimeError(
                "Volume extension failed for %s (domainID: %s, volumeID: %s)" %
                (volInfo['name'], volInfo['domainID'], volInfo['volumeID']))

        return volSize

    def __afterReplicaExtension(self, volInfo):
        self.__verifyVolumeExtension(volInfo)
        vmDrive = self._findDriveByName(volInfo['name'])
        if vmDrive.chunked:
            self.log.debug("Requesting extension for the original drive: %s "
                           "(domainID: %s, volumeID: %s)",
                           vmDrive.name, vmDrive.domainID, vmDrive.volumeID)
            self.__extendDriveVolume(vmDrive, vmDrive.volumeID,
                                     volInfo['newSize'])

    def __extendDriveVolume(self, vmDrive, volumeID, newSize):
        volInfo = {
            'domainID': vmDrive.domainID,
            'imageID': vmDrive.imageID,
            'internal': vmDrive.volumeID != volumeID,
            'name': vmDrive.name,
            'newSize': newSize,
            'poolID': vmDrive.poolID,
            'volumeID': volumeID,
        }
        self.log.debug("Requesting an extension for the volume: %s", volInfo)
        self.cif.irs.sendExtendMsg(
            vmDrive.poolID,
            volInfo,
            newSize,
            self.__afterVolumeExtension)

    def __extendDriveReplica(self, drive, newSize):
        volInfo = {
            'domainID': drive.diskReplicate['domainID'],
            'imageID': drive.diskReplicate['imageID'],
            'name': drive.name,
            'newSize': newSize,
            'poolID': drive.diskReplicate['poolID'],
            'volumeID': drive.diskReplicate['volumeID'],
        }
        self.log.debug("Requesting an extension for the volume "
                       "replication: %s", volInfo)
        self.cif.irs.sendExtendMsg(drive.poolID,
                                   volInfo,
                                   newSize,
                                   self.__afterReplicaExtension)

    def __afterVolumeExtension(self, volInfo):
        # Check if the extension succeeded.  On failure an exception is raised
        # TODO: Report failure to the engine.
        volSize = self.__verifyVolumeExtension(volInfo)

        # Only update apparentsize and truesize if we've resized the leaf
        if not volInfo['internal']:
            vmDrive = self._findDriveByName(volInfo['name'])
            vmDrive.apparentsize = volSize.apparentsize
            vmDrive.truesize = volSize.truesize

        try:
            self.cont()
        except libvirt.libvirtError:
            self.log.warn("VM %s can't be resumed", self.id, exc_info=True)
        self._setWriteWatermarks()

    def _acquireCpuLockWithTimeout(self):
        timeout = self._loadCorrectedTimeout(
            config.getint('vars', 'vm_command_timeout'))
        end = time.time() + timeout
        while not self._guestCpuLock.acquire(False):
            time.sleep(0.1)
            if time.time() > end:
                raise RuntimeError('waiting more than %ss for _guestCpuLock' %
                                   timeout)

    def cont(self, afterState=vmstatus.UP, guestCpuLocked=False,
             ignoreStatus=False):
        """
        Continue execution of the VM.

        :param ignoreStatus: True, if the operation must be performed
                             regardless of the VM's status, False otherwise.
                             Default: False

                             By default, cont() returns error if the VM is in
                             one of the following states:

                             vmstatus.MIGRATION_SOURCE
                                 Migration is in progress, VM status should not
                                 be changed till the migration finishes.

                             vmstatus.SAVING_STATE
                                 Hibernation is in progress, VM status should
                                 not be changed till the hibernation finishes.

                             vmstatus.DOWN
                                 VM is down, continuing is not possible from
                                 this state.
        """
        if not guestCpuLocked:
            self._acquireCpuLockWithTimeout()
        try:
            if (not ignoreStatus and
                    self.lastStatus in (vmstatus.MIGRATION_SOURCE,
                                        vmstatus.SAVING_STATE,
                                        vmstatus.DOWN)):
                self.log.error('cannot cont while %s', self.lastStatus)
                return response.error('unexpected')
            self._underlyingCont()
            self._setGuestCpuRunning(self._isDomainRunning(),
                                     guestCpuLocked=True)
            self._logGuestCpuStatus('continue')
            self._lastStatus = afterState
            try:
                with self._confLock:
                    del self.conf['pauseCode']
            except KeyError:
                pass
        finally:
            if not guestCpuLocked:
                self._guestCpuLock.release()

        self.send_status_event()
        return response.success()

    def pause(self, afterState=vmstatus.PAUSED, guestCpuLocked=False,
              pauseCode='NOERR'):
        if not guestCpuLocked:
            self._acquireCpuLockWithTimeout()
        try:
            with self._confLock:
                self.conf['pauseCode'] = pauseCode
            self._underlyingPause()
            self._setGuestCpuRunning(self._isDomainRunning(),
                                     guestCpuLocked=True)
            self._logGuestCpuStatus('pause')
            self._lastStatus = afterState
        finally:
            if not guestCpuLocked:
                self._guestCpuLock.release()

        self.send_status_event()
        return response.success()

    def _setGuestCpuRunning(self, isRunning, guestCpuLocked=False):
        """
        here we want to synchronize the access to guestCpuRunning
        made by callback with the pause/cont methods.
        To do so we reuse guestCpuLocked.
        """
        if not guestCpuLocked:
            self._acquireCpuLockWithTimeout()
        try:
            self._guestCpuRunning = isRunning
        finally:
            if not guestCpuLocked:
                self._guestCpuLock.release()

    def _syncGuestTime(self):
        """
        Try to set VM time to the current value.  This is typically useful when
        clock wasn't running on the VM for some time (e.g. during suspension or
        migration), especially if the time delay exceeds NTP tolerance.

        It is not guaranteed that the time is actually set (it depends on guest
        environment, especially QEMU agent presence) or that the set time is
        very precise (NTP in the guest should take care of it if needed).
        """
        t = time.time()
        seconds = int(t)
        nseconds = int((t - seconds) * 10**9)
        try:
            self._dom.setTime(time={'seconds': seconds, 'nseconds': nseconds})
        except libvirt.libvirtError as e:
            template = "Failed to set time: %s"
            code = e.get_error_code()
            if code == libvirt.VIR_ERR_AGENT_UNRESPONSIVE:
                self.log.debug(template, "QEMU agent unresponsive")
            elif code == libvirt.VIR_ERR_NO_SUPPORT:
                self.log.debug(template, "Not supported")
            else:
                self.log.error(template, e)
        except virdomain.NotConnectedError:
            # The highest priority is not to let this method crash and thus
            # disrupt its caller in any way.  So we swallow this error here,
            # to be absolutely safe.
            self.log.debug("Failed to set time: not connected")
        else:
            self.log.debug('Time updated to: %d.%09d', seconds, nseconds)

    def shutdown(self, delay, message, reboot, timeout, force):
        if self.lastStatus == vmstatus.DOWN:
            raise exception.NoSuchVM()

        delay = int(delay)

        self._guestEventTime = time.time()
        if reboot:
            self._guestEvent = vmstatus.REBOOT_IN_PROGRESS
            powerDown = VmReboot(self, delay, message, timeout, force,
                                 self._powerDownEvent)
        else:
            self._guestEvent = vmstatus.POWERING_DOWN
            powerDown = VmShutdown(self, delay, message, timeout, force,
                                   self._powerDownEvent)
        return powerDown.start()

    def _cleanupDrives(self, *drives):
        """
        Clean up drives related stuff. Sample usage:

        self._cleanupDrives()
        self._cleanupDrives(drive)
        self._cleanupDrives(drive1, drive2, drive3)
        self._cleanupDrives(*drives_list)
        """
        drives = drives or self._devices[hwclass.DISK]
        # clean them up
        with self._volPrepareLock:
            for drive in drives:
                try:
                    self._removeTransientDisk(drive)
                except Exception:
                    self.log.warning("Drive transient volume deletion failed "
                                     "for drive %s", drive, exc_info=True)
                    # Skip any exception as we don't want to interrupt the
                    # teardown process for any reason.
                try:
                    self.cif.teardownVolumePath(drive)
                except Exception:
                    self.log.exception("Drive teardown failure for %s",
                                       drive)

    def _cleanupFloppy(self):
        """
        Clean up floppy drive
        """
        if self.conf.get('volatileFloppy'):
            try:
                self.log.debug("Floppy %s cleanup" % self.conf['floppy'])
                fileutils.rm_file(self.conf['floppy'])
            except Exception:
                pass

    def _cleanupGuestAgent(self):
        """
        Try to stop the guest agent and clean up its socket
        """
        try:
            self.guestAgent.stop()
        except Exception:
            pass

        cleanup_guest_socket(self._guestSocketFile)

    def setDownStatus(self, code, exitReasonCode, exitMessage=''):
        if not exitMessage:
            exitMessage = vmexitreason.exitReasons.get(exitReasonCode,
                                                       'VM terminated')
        event_data = {}
        try:
            self.lastStatus = vmstatus.DOWN
            with self._confLock:
                self.conf['exitCode'] = code
                if self._altered_state.origin == _FILE_ORIGIN:
                    self.conf['exitMessage'] = (
                        "Wake up from hibernation failed" +
                        ((":" + exitMessage) if exitMessage else ''))
                else:
                    self.conf['exitMessage'] = exitMessage
                self.conf['exitReason'] = exitReasonCode
            self.log.info("Changed state to Down: %s (code=%i)",
                          exitMessage, exitReasonCode)
            # Engine doesn't like duplicated events (e.g. Down, Down).
            # but this cannot happen in this flow, because
            # if some flows tries to setDownStatus a VM already Down,
            # it will explode with DoubleDownError, thus this code
            # will never reach this point and no event will be emitted.
            event_data = self._getExitedVmStats()
        except DoubleDownError:
            pass
        if self.post_copy == migration.PostCopyPhase.NONE:
            try:
                self.guestAgent.stop()
            except Exception:
                pass
        self.saveState()
        if event_data:
            self.send_status_event(**event_data)

    def status(self, fullStatus=True):
        # used by API.Global.getVMList
        if not fullStatus:
            return {'vmId': self.id, 'status': self.lastStatus,
                    'statusTime': self._get_status_time()}

        with self._confLock:
            self.conf['status'] = self.lastStatus
            # Filter out any internal keys
            status = dict((k, v) for k, v in self.conf.iteritems()
                          if not k.startswith("_"))
            status['vmId'] = self.id
            status['guestDiskMapping'] = self.guestAgent.guestDiskMapping
            status['statusTime'] = self._get_status_time()
            return utils.picklecopy(status)

    def getStats(self):
        """
        Used by API.Vm.getStats.

        WARNING: This method should only gather statistics by copying data.
        Especially avoid costly and dangerous direct calls to the _dom
        attribute. Use the periodic operations instead!
        """
        stats = {'statusTime': self._get_status_time()}
        if self.lastStatus == vmstatus.DOWN:
            stats.update(self._getDownVmStats())
        else:
            stats.update(self._getConfigVmStats())
            if self.isMigrating() and self.post_copy:
                # Stats are on the destination during post-copy migration,
                # except for migration progress, which is always on the source.
                stats['migrationProgress'] = self._get_vm_migration_progress()
                stats.update(self._getVmPauseCodeStats())
            else:
                stats.update(self._getRunningVmStats())
                stats.update(self._getGuestStats())
            stats['status'] = self._getVmStatus()
        return stats

    def _getDownVmStats(self):
        stats = {
            'vmId': self.id,
            'status': self.lastStatus
        }
        stats.update(self._getExitedVmStats())
        return stats

    def _getExitedVmStats(self):
        stats = {
            'exitCode': self.conf['exitCode'],
            'exitMessage': self.conf['exitMessage'],
            'exitReason': self.conf['exitReason']}
        if 'timeOffset' in self.conf:
            stats['timeOffset'] = self.conf['timeOffset']
        return stats

    def _getConfigVmStats(self):
        """
        provides all the stats which will not change after a VM is booted.
        Please note that some values are provided by client (engine)
        but can change as a result of interaction with libvirt
        """
        stats = {
            'vmId': self.id,
            'vmName': self.name,
            'vmType': self.conf['vmType'],
            'kvmEnable': self.conf.get('kvmEnable', 'true'),
            'acpiEnable': 'true' if self.acpi_enabled() else 'false'}
        if 'cdrom' in self.conf:
            stats['cdrom'] = self.conf['cdrom']
        return stats

    def _getRunningVmStats(self):
        """
        gathers all the stats which can change while a VM is running.
        """
        stats = {
            'elapsedTime': str(int(time.time() - self._startTime)),
            'monitorResponse': str(self._monitorResponse),
            'timeOffset': self.conf.get('timeOffset', '0'),
            'clientIp': self.conf.get('clientIp', ''),
        }

        stats.update(self._getVmPauseCodeStats())
        if self.isMigrating():
            stats['migrationProgress'] = self._get_vm_migration_progress()

        try:
            # Prevent races with the creation thread.
            # Mimicing < 3.5 code, the creation thread marks the VM as
            # monitorable only after the stats cache is initialized.
            # Here we need to do the reverse: check first if a VM is
            # monitorable, and only if it is, consider the stats_age.
            monitorable = self._monitorable
            vm_sample = sampling.stats_cache.get(self.id)
            decStats = vmstats.produce(self,
                                       vm_sample.first_value,
                                       vm_sample.last_value,
                                       vm_sample.interval)
            if monitorable:
                self._setUnresponsiveIfTimeout(stats, vm_sample.stats_age)
        except Exception:
            self.log.exception("Error fetching vm stats")
        else:
            stats.update(vmstats.translate(decStats))

        stats.update(self._getGraphicsStats())
        stats['hash'] = str(hash((self._domain.devices_hash,
                                  self.guestAgent.diskMappingHash)))
        if self._watchdogEvent:
            stats['watchdogEvent'] = self._watchdogEvent
        if self._vcpuLimit:
            stats['vcpuUserLimit'] = self._vcpuLimit

        stats.update(self._getVmJobsStats())

        stats.update(self._getVmTuneStats())
        return stats

    def _getVmTuneStats(self):
        stats = {}

        # Handling the case where quota is not set, setting to 0.
        # According to libvirt API:"A quota with value 0 means no value."
        # The value does not have to be present in some transient cases
        vcpu_quota = self._vcpuTuneInfo.get('vcpu_quota', _NO_CPU_QUOTA)
        if vcpu_quota != _NO_CPU_QUOTA:
            stats['vcpuQuota'] = str(vcpu_quota)

        # Handling the case where period is not set, setting to 0.
        # According to libvirt API:"A period with value 0 means no value."
        # The value does not have to be present in some transient cases
        vcpu_period = self._vcpuTuneInfo.get('vcpu_period', _NO_CPU_PERIOD)
        if vcpu_period != _NO_CPU_PERIOD:
            stats['vcpuPeriod'] = vcpu_period

        return stats

    def _getVmJobsStats(self):
        stats = {}

        # vmJobs = {} is a valid output and should be reported.
        # means 'jobs finishing' to Engine.
        #
        # default value for self._vmJobs is None, this means
        # "VDSM does not know yet", thus should not report anything to Engine.
        # Once Vm.updateVmJobs run at least once, VDSM will know for sure.
        if self._vmJobs is not None:
            stats['vmJobs'] = self._vmJobs

        return stats

    def _getVmPauseCodeStats(self):
        stats = {}
        with self._confLock:
            if 'pauseCode' in self.conf:
                stats['pauseCode'] = self.conf['pauseCode']
        return stats

    def _getVmStatus(self):
        def _getVmStatusFromGuest():
            GUEST_WAIT_TIMEOUT = 60
            now = time.time()
            if now - self._guestEventTime < 5 * GUEST_WAIT_TIMEOUT and \
                    self._guestEvent == vmstatus.POWERING_DOWN:
                return self._guestEvent
            if self.guestAgent and self.guestAgent.isResponsive() and \
                    self.guestAgent.getStatus():
                return self.guestAgent.getStatus()
            if now - self._guestEventTime < GUEST_WAIT_TIMEOUT:
                return self._guestEvent
            return vmstatus.UP

        if self.lastStatus == vmstatus.MIGRATION_SOURCE and \
           self.post_copy == migration.PostCopyPhase.RUNNING:
            # We are still in MIGRATION_SOURCE state, but Engine developers
            # prefer to get the actual libvirt state, which is PAUSED during
            # post-copy migration (until it switches to DOWN).
            return vmstatus.PAUSED
        statuses = (vmstatus.SAVING_STATE, vmstatus.RESTORING_STATE,
                    vmstatus.MIGRATION_SOURCE, vmstatus.MIGRATION_DESTINATION,
                    vmstatus.PAUSED, vmstatus.DOWN)
        if self.lastStatus in statuses:
            return self.lastStatus
        elif self.isMigrating():
            if self._migrationSourceThread.hibernating:
                return vmstatus.SAVING_STATE
            else:
                return vmstatus.MIGRATION_SOURCE
        elif self.lastStatus == vmstatus.UP:
            return _getVmStatusFromGuest()
        else:
            return self.lastStatus

    def _get_vm_migration_progress(self):
        return self.migrateStatus()['progress']

    def _getGraphicsStats(self):
        def getInfo(dev):
            return {
                'type': dev.device,
                'port': dev.port,
                'tlsPort': dev.tlsPort,
                'ipAddress': dev.specParams.get('displayIp', '0'),
            }

        display_info = [
            getInfo(dev) for dev in self._devices[hwclass.GRAPHICS]
        ]

        stats = {'displayInfo': display_info}
        if 'display' in self.conf and display_info:
            dev = display_info[0]
            stats['displayType'] = (
                'qxl' if dev['type'] == 'spice' else 'vnc'
            )
            stats['displayPort'] = dev['port']
            stats['displaySecurePort'] = dev['tlsPort']
            stats['displayIp'] = dev['ipAddress']
        # else headless VM
        return stats

    def _getGuestStats(self):
        stats = self.guestAgent.getGuestInfo()
        realMemUsage = int(stats['memUsage'])
        if realMemUsage != 0:
            memUsage = (100 - float(realMemUsage) /
                        self.mem_size_mb() * 100)
        else:
            memUsage = 0
        stats['memUsage'] = utils.convertToStr(int(memUsage))
        return stats

    def isMigrating(self):
        return self._migrationSourceThread.migrating()

    def hasTransientDisks(self):
        for drive in self._devices[hwclass.DISK]:
            if drive.transientDisk:
                return True
        return False

    @api.logged(on='vdsm.api')
    @api.guard(_not_migrating)
    def migrate(self, params):
        self._acquireCpuLockWithTimeout()
        try:
            # It is unlikely, but we could receive migrate()
            # request right after a VM was started or right
            # after a VM just went down
            if self._lastStatus in (vmstatus.WAIT_FOR_LAUNCH,
                                    vmstatus.DOWN):
                raise exception.NoSuchVM()
            if self.hasTransientDisks():
                return response.error('transientErr')
            self._migration_downtime = None
            self._migrationSourceThread = migration.SourceThread(
                self, **params)
            self._migrationSourceThread.start()
            self._migrationSourceThread.getStat()
            self.send_status_event()
            return self._migrationSourceThread.status
        finally:
            self._guestCpuLock.release()

    def migrateStatus(self):
        status = self._migrationSourceThread.getStat()
        if self._migration_downtime is not None:
            status['downtime'] = self._migration_downtime
            status['progress'] = 100
        return status

    def onJobCompleted(self, args):
        if (not self._migrationSourceThread.started and
            not self._migrationSourceThread.recovery) or \
           self._migrationSourceThread.hibernating:
            return
        stats = args[0]
        if self.post_copy == migration.PostCopyPhase.RUNNING:
            # downtime_net doesn't make sense and is not available after
            # post-copy.  So we must use `downtime' here, which is somewhat
            # different value and is not resilient against time differences
            # on the hosts.
            key = 'downtime'
        else:
            key = 'downtime_net'
        self._migration_downtime = stats.get(key)
        self.send_migration_status_event()
        if self._migrationSourceThread.recovery:
            # This Vdsm instance didn't start the migration and the source
            # thread is not running. We can't rely on the source thread to put
            # the VM in the proper state and we must do it here (just on best
            # effort base).
            self._finish_migration_recovery()

    def _finish_migration_recovery(self):
        try:
            state, reason = self._dom.state(0)
        except libvirt.libvirtError as e:
            if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                # Migration successfully finished, domain already gone;
                # we handle it the same way below as a still present domain
                # in a shut off state.
                self.log.info("Domain is gone after migration recovery")
                state = libvirt.VIR_DOMAIN_SHUTOFF
                reason = libvirt.VIR_DOMAIN_SHUTOFF_MIGRATED
            else:
                raise
        if state == libvirt.VIR_DOMAIN_SHUTOFF and \
           reason == libvirt.VIR_DOMAIN_SHUTOFF_MIGRATED:
            self.setDownStatus(NORMAL, vmexitreason.MIGRATION_SUCCEEDED)
            if self.post_copy == migration.PostCopyPhase.RUNNING:
                # Engine doesn't call destroy after post-copy.
                self.destroy()
        else:
            self.log.warning("Unhandled state after a recovered migration: "
                             "%s, %s", state, reason)

    @api.logged(on='vdsm.api')
    def migrateCancel(self):
        self._acquireCpuLockWithTimeout()
        try:
            self._migrationSourceThread.stop()
            self._migrationSourceThread.status['status']['message'] = \
                'Migration process cancelled'
            return self._migrationSourceThread.status
        except libvirt.libvirtError as e:
            if e.get_error_code() == libvirt.VIR_ERR_OPERATION_INVALID:
                self.log.warning("Failed to cancel migration: %s", e)
                return response.error('migCancelErr')
            raise
        except virdomain.NotConnectedError:
            return response.error('migCancelErr')
        finally:
            self._guestCpuLock.release()

    def _getSerialConsole(self):
        """
        Return serial console device.
        If no serial console device is available, return 'None'.
        """
        for console in self._devices[hwclass.CONSOLE]:
            if console.isSerial:
                return console
        return None

    @api.logged(on='vdsm.api')
    def migrateChangeParams(self, params):
        self._acquireCpuLockWithTimeout()

        try:
            if self._migrationSourceThread.hibernating:
                return response.error('migNotInProgress')

            if not self.isMigrating():
                return response.error('migNotInProgress')

            if 'maxBandwidth' in params:
                self._migrationSourceThread.set_max_bandwidth(
                    int(params['maxBandwidth']))
        except libvirt.libvirtError as e:
            if e.get_error_code() == libvirt.VIR_ERR_OPERATION_INVALID:
                return response.error('migNotInProgress')
            raise
        except virdomain.NotConnectedError:
            return response.error('migNotInProgress')

        finally:
            self._guestCpuLock.release()

        return response.success()

    @api.logged(on='vdsm.api')
    def switch_migration_to_post_copy(self):
        """
        Request to switch the currently running migration to post-copy mode.

        :return: Whether the request was successful.
        :rtype: bool

        .. note::

          This call just requests switching the migration to post-copy mode
          asynchronously.  The actual change of the migration mode should
          happen shortly afterwards and it should be reported via the
          corresponding libvirt event.
        """
        self.log.info('Switching to post-copy migration')
        self.guestAgent.stop()
        try:
            self._dom.migrateStartPostCopy(0)
        except libvirt.libvirtError:
            try:
                self.guestAgent.start()
            except Exception:
                self.log.exception("Failed to start guest agent after "
                                   "unsuccessful switch to post-copy "
                                   "migration")
            return False
        self._post_copy = migration.PostCopyPhase.REQUESTED
        return True

    def _customDevices(self):
        """
            Get all devices that have custom properties
        """

        for devType in self._devices:
            for dev in self._devices[devType]:
                if dev.custom:
                    yield dev

    def _process_devices(self):
        """
        Create all devices and run before_device_create hook script for devices
        with custom properties

        The resulting device xml is cached in dev._deviceXML.
        """

        devices_xml = vmdevices.common.empty_dev_map()
        for dev_type, dev_objs in self._devices.items():
            for dev in dev_objs:
                try:
                    dev_xml = dev.getXML()
                except vmdevices.core.SkipDevice:
                    self.log.info('Skipping device %s.', dev.device)
                    continue

                deviceXML = vmxml.format_xml(dev_xml)

                if getattr(dev, "custom", {}):
                    deviceXML = hooks.before_device_create(
                        deviceXML, self._custom, dev.custom)
                    dev_xml = vmxml.parse_xml(deviceXML)

                dev._deviceXML = deviceXML

                devices_xml[dev_type].append(dev_xml)
        return devices_xml

    def _prepare_hugepages(self):
        if not config.getboolean('performance', 'use_dynamic_hugepages'):
            self.log.info('Dynamic hugepage allocation disabled.')
            return

        vm_mem_size_kb = self.mem_size_mb() * 1024

        with hugepages.lock:
            to_allocate = hugepages.calculate_required_allocation(
                self.cif, self.nr_hugepages, self.hugepagesz
            )

            self.log.info(
                'Allocating %s (%s) hugepages (memsize %s)',
                to_allocate,
                self.hugepagesz,
                vm_mem_size_kb
            )

            hugepages.alloc(to_allocate, self.hugepagesz)

    def _buildDomainXML(self):
        if 'xml' in self.conf:
            xml_str = self.conf['xml']
            xml_str = vmdevices.graphics.fixDisplayNetworks(xml_str)
            xml_str = vmdevices.lease.fixLeases(self.cif.irs, xml_str)
            xml_str = vmdevices.network.fixNetworks(xml_str)
            if cpuarch.is_x86(self.arch):
                osd = osinfo.version()
                osVersion = osd.get('version', '') + '-' + \
                    osd.get('release', '')
                serialNumber = self.conf.get('serial', host.uuid())
                xml_str = xml_str.replace('OS-NAME:',
                                          constants.SMBIOS_OSNAME)
                xml_str = xml_str.replace('OS-VERSION:',
                                          osVersion)
                xml_str = xml_str.replace('HOST-SERIAL:',
                                          serialNumber)
            return xml_str

        serial_console = self._getSerialConsole()

        domxml = libvirtxml.Domain(self.conf, self.log, self.arch)
        domxml.appendOs(use_serial_console=(serial_console is not None))

        if self.hugepages:
            self._prepare_hugepages()
            domxml.appendMemoryBacking(self.hugepagesz)

        if cpuarch.is_x86(self.arch):
            osd = osinfo.version()

            osVersion = osd.get('version', '') + '-' + osd.get('release', '')
            serialNumber = self.conf.get('serial', host.uuid())

            domxml.appendSysinfo(
                osname=constants.SMBIOS_OSNAME,
                osversion=osVersion,
                serialNumber=serialNumber)

        domxml.appendClock()

        if cpuarch.is_x86(self.arch):
            domxml.appendFeatures()

        domxml.appendCpu()

        if 'numaTune' in self.conf:
            domxml.appendNumaTune()
        else:
            if (config.getboolean('vars', 'host_numa_scheduling') and
                    self._devices[hwclass.HOSTDEV]):
                domxml.appendHostdevNumaTune(
                    list(itertools.chain(*self._devices.values())))

        domxml._appendAgentDevice(self._guestSocketFile.decode('utf-8'),
                                  self._agent_channel_name)
        domxml._appendAgentDevice(self._qemuguestSocketFile.decode('utf-8'),
                                  vmchannels.QEMU_GA_DEVICE_NAME)
        domxml.appendInput()

        if self.arch == cpuarch.PPC64:
            domxml.appendEmulator()

        devices_xml = self._process_devices()
        for dev_type, dev_objs in devices_xml.items():
            for dev in dev_objs:
                domxml._devices.appendChild(etree_element=dev)

        for dev_objs in self._devices.values():
            for dev in dev_objs:
                for elem in dev.get_extra_xmls():
                    domxml._devices.appendChild(etree_element=elem)

        return domxml.toxml()

    def _cleanup(self):
        """
        General clean up routine
        """
        self._cleanupDrives()
        self._cleanupFloppy()
        self._cleanupGuestAgent()
        self._teardown_devices()
        cleanup_guest_socket(self._qemuguestSocketFile)
        self._cleanupStatsCache()
        for con in self._devices[hwclass.CONSOLE]:
            con.cleanup()
        if self.hugepages:
            self._cleanup_hugepages()

    def _cleanup_hugepages(self):
        if not config.getboolean('performance', 'use_dynamic_hugepages'):
            self.log.info('Dynamic hugepage allocation disabled.')
            return

        vm_mem_size_kb = self.mem_size_mb() * 1024

        with hugepages.lock:
            to_deallocate = hugepages.calculate_required_deallocation(
                self.nr_hugepages, self.hugepagesz
            )

            self.log.info(
                'Deallocating %s (%s) hugepages (memsize %s)',
                to_deallocate,
                self.hugepagesz,
                vm_mem_size_kb
            )

            try:
                hugepages.dealloc(to_deallocate, self.hugepagesz)
            except Exception:
                self.log.info('Deallocation of hugepages failed')

    def _teardown_devices(self, devices=None):
        """
        Runs after the underlying libvirt domain was destroyed.
        """
        if devices is None:
            devices = list(itertools.chain(*self._devices.values()))

        for device in devices:
            try:
                device.teardown()
            except Exception:
                self.log.exception('Failed to tear down device %s, device in '
                                   'inconsistent state', device.device)

    def _cleanupRecoveryFile(self):
        self._recovery_file.cleanup()

    def _cleanupStatsCache(self):
        try:
            sampling.stats_cache.remove(self.id)
        except KeyError:
            self.log.warn('timestamp already removed from stats cache')

    def _isDomainRunning(self):
        try:
            status = self._dom.info()
        except virdomain.NotConnectedError:
            # Known reasons for this:
            # * on migration destination, and migration not yet completed.
            # * self._dom may be disconnected asynchronously (_onQemuDeath).
            #   If so, the VM is shutting down or already shut down.
            return False
        else:
            return status[0] == libvirt.VIR_DOMAIN_RUNNING

    def _getUnderlyingVmDevicesInfo(self):
        """
        Obtain underlying vm's devices info from libvirt.
        """
        vmdevices.common.update_device_info(self, self._devices)

    def _updateAgentChannels(self):
        """
        We moved the naming of guest agent channel sockets. To keep backwards
        compatability we need to make symlinks from the old channel sockets, to
        the new naming scheme.
        This is necessary to prevent incoming migrations, restoring of VMs and
        the upgrade of VDSM with running VMs to fail on this.
        """
        known_channel_names = (vmchannels.QEMU_GA_DEVICE_NAME,
                               self._agent_channel_name)
        for name, path in self._domain.all_channels():
            if name not in known_channel_names:
                continue

            uuidPath = self._makeChannelPath(name)
            if path != uuidPath:
                # When this path is executed, we're having VM created on
                # VDSM > 4.13

                # The to be created symlink might not have been cleaned up due
                # to an unexpected stop of VDSM therefore We're going to clean
                # it up now
                if os.path.islink(uuidPath):
                    os.unlink(uuidPath)

                # We don't want an exception to be thrown when the path already
                # exists
                if not os.path.exists(uuidPath):
                    os.symlink(path, uuidPath)
                else:
                    self.log.error("Failed to make a agent channel symlink "
                                   "from %s -> %s for channel %s", path,
                                   uuidPath, name)

    def _domDependentInit(self):
        if self._destroy_requested.is_set():
            # reaching here means that Vm.destroy() was called before we could
            # handle it. We must handle it now
            try:
                self._dom.destroy()
            except Exception:
                pass
            raise DestroyedOnStartupError()

        if not self._dom.connected:
            raise MissingLibvirtDomainError(vmexitreason.LIBVIRT_START_FAILED)

        sampling.stats_cache.add(self.id)
        self._monitorable = config.getboolean('sampling', 'enable')

        if is_kvm(self._custom):
            self._vmDependentInit()
        else:
            self._containerDependentInit()

    def _vmDependentInit(self):
        self._guestEventTime = self._startTime

        self._updateDomainDescriptor()

        # REQUIRED_FOR migrate from vdsm-4.16
        #
        # We need to clean out unknown devices that are created for
        # RNG devices by VDSM 3.5 and are left in the configuration
        # after upgrade to 3.6.
        self._fixLegacyRngConf()

        self._getUnderlyingVmDevicesInfo()
        self._updateAgentChannels()

        # Currently there is no protection agains mirroring a network twice,
        if not self.recovering:
            for nic in self._devices[hwclass.NIC]:
                if hasattr(nic, 'portMirroring'):
                    for network in nic.portMirroring:
                        supervdsm.getProxy().setPortMirroring(network,
                                                              nic.name)

        try:
            self.guestAgent.start()
        except Exception:
            self.log.exception("Failed to connect to guest agent channel")

        if self.lastStatus == vmstatus.RESTORING_STATE:
            try:
                self.guestAgent.events.after_hibernation()
            except Exception:
                self.log.exception("Unexpected error on guest after "
                                   "hibernation notification")
        elif self.conf.get('enableGuestEvents', False):
            if self.lastStatus == vmstatus.MIGRATION_DESTINATION:
                try:
                    self.guestAgent.events.after_migration()
                except Exception:
                    self.log.exception("Unexpected error on guest after "
                                       "migration notification")

        # Drop enableGuestEvents from conf - Not required from here anymore
        self.conf.pop('enableGuestEvents', None)

        for con in self._devices[hwclass.CONSOLE]:
            con.prepare()

        self._guestCpuRunning = self._isDomainRunning()
        self._logGuestCpuStatus('domain initialization')
        if self.lastStatus not in (vmstatus.MIGRATION_DESTINATION,
                                   vmstatus.RESTORING_STATE):
            self._initTimePauseCode = self._readPauseCode()
        if not self.recovering and self._initTimePauseCode:
            with self._confLock:
                self.conf['pauseCode'] = self._initTimePauseCode
            if self._initTimePauseCode == 'ENOSPC':
                self.cont()

        self._dom_vcpu_setup()
        self._updateIoTuneInfo()

    def _containerDependentInit(self):
        self._guestEventTime = self._startTime
        self._guestCpuRunning = self._isDomainRunning()
        self._logGuestCpuStatus('domain initialization')

    def _dom_vcpu_setup(self):
        if 'xml' not in self.conf:
            nice = int(self.conf.get('nice', '0'))
            nice = max(min(nice, 19), 0)

            # if cpuShares weren't configured we derive the value from the
            # niceness, cpuShares has no unit, it is only meaningful when
            # compared to other VMs (and can't be negative)
            cpuShares = int(self.conf.get('cpuShares', str((20 - nice) * 51)))
            cpuShares = max(cpuShares, 0)

            try:
                self._dom.setSchedulerParameters({'cpu_shares': cpuShares})
            except Exception:
                self.log.warning('failed to set Vm niceness', exc_info=True)

        self._updateVcpuTuneInfo()
        self._updateVcpuLimit()

    def _setup_devices(self):
        """
        Runs before the underlying libvirt domain is created.

        Handle setup of all devices. If some device cannot be setup,
        go through the devices that were successfully setup and tear
        them down, logging all exceptions we encounter. Exception is then
        raised as we cannot continue the VM creation due to device failures.
        """
        done = []

        for dev_objects in self._devices.values():
            for dev_object in dev_objects[:]:
                try:
                    dev_object.setup()
                except Exception:
                    self.log.exception("Failed to setup device %s",
                                       dev_object.device)
                    self._teardown_devices(done)
                    raise
                else:
                    done.append(dev_object)

    def _run(self):
        self.log.info("VM wrapper has started")
        dev_spec_map = self._devSpecMapFromConf()

        # recovery flow note:
        # we do not start disk stats collection here since
        # in the recovery flow irs may not be ready yet.
        # Disk stats collection is started from clientIF at the end
        # of the recovery process.
        if not self.recovering:
            vmdevices.lease.prepare(self.cif.irs, dev_spec_map[hwclass.LEASE])
            self._preparePathsForDrives(dev_spec_map[hwclass.DISK])
            self._prepareTransientDisks(dev_spec_map[hwclass.DISK])
            self._updateDevices(dev_spec_map)
            # We need to save conf here before we actually run VM.
            # It's not enough to save conf only on status changes as we did
            # before, because if vdsm will restarted between VM run and conf
            # saving we will fail in inconsistent state during recovery.
            # So, to get proper device objects during VM recovery flow
            # we must to have updated conf before VM run
            self.saveState()

        self._devices = vmdevices.common.dev_map_from_dev_spec_map(
            dev_spec_map, self.log
        )

        # We should set this event as a last part of drives initialization
        self._pathsPreparedEvent.set()

        initDomain = self._altered_state.origin != _MIGRATION_ORIGIN
        # we need to complete the initialization, including
        # domDependentInit, after the migration is completed.

        if not self.recovering:
            self._setup_devices()

        if self.recovering:
            self._dom = virdomain.Notifying(
                self._connection.lookupByUUIDString(self.id),
                self._timeoutExperienced)
            for dev in self._devices[hwclass.NIC]:
                dev.recover()
        elif self._altered_state.origin == _MIGRATION_ORIGIN:
            pass  # self._dom will be disconnected until migration ends.
        elif self._altered_state.origin == _FILE_ORIGIN:
            # TODO: for unknown historical reasons, we call this hook also
            # on this flow. Issues:
            # - we will also call the more specific before_vm_dehibernate
            # - we feed the hook with wrong XML
            # - we ignore the output of the hook
            hooks.before_vm_start(self._buildDomainXML(), self._custom)

            fromSnapshot = self._altered_state.from_snapshot
            srcDomXML = self._src_domain_xml
            if fromSnapshot:
                srcDomXML = self._correctDiskVolumes(srcDomXML)
                srcDomXML = self._correctGraphicsConfiguration(srcDomXML)
            hooks.before_vm_dehibernate(srcDomXML, self._custom,
                                        {'FROM_SNAPSHOT': str(fromSnapshot)})

            # TODO: this is debug information. For 3.6.x we still need to
            # see the XML even with 'info' as default level.
            self.log.info(srcDomXML)

            restore_path = self._altered_state.path
            fname = self.cif.prepareVolumePath(restore_path)
            try:
                if fromSnapshot:
                    self._connection.restoreFlags(fname, srcDomXML, 0)
                else:
                    self._connection.restore(fname)
            finally:
                self.cif.teardownVolumePath(restore_path)

            self._dom = virdomain.Notifying(
                self._connection.lookupByUUIDString(self.id),
                self._timeoutExperienced)
        else:

            flags = libvirt.VIR_DOMAIN_NONE
            with self._confLock:
                if 'launchPaused' in self.conf:
                    flags |= libvirt.VIR_DOMAIN_START_PAUSED
                    self.conf['pauseCode'] = 'NOERR'
                    del self.conf['launchPaused']
            hooks.dump_vm_launch_flags_to_file(self.id, flags)
            try:
                domxml = hooks.before_vm_start(self._buildDomainXML(),
                                               self._custom)
                flags = hooks.load_vm_launch_flags_from_file(self.id)

                # TODO: this is debug information. For 3.6.x we still need to
                # see the XML even with 'info' as default level.
                self.log.info(domxml)

                self._dom = virdomain.Notifying(
                    self._connection.createXML(domxml, flags),
                    self._timeoutExperienced)
                self._update_metadata()
                hooks.after_vm_start(self._dom.XMLDesc(0), self._custom)
                for dev in self._customDevices():
                    hooks.after_device_create(dev._deviceXML, self._custom,
                                              dev.custom)
            finally:
                hooks.remove_vm_launch_flags_file(self.id)

        if initDomain:
            self._domDependentInit()

    def _updateDevices(self, devices):
        """
        Update self.conf with updated devices
        For old type vmParams, new 'devices' key will be
        created with all devices info
        """
        newDevices = []
        for dev in devices.values():
            newDevices.extend(dev)

        with self._confLock:
            self.conf['devices'] = newDevices

    def _correctDiskVolumes(self, srcDomXML):
        """
        Replace each volume in the given XML with the latest volume
        that the image has.
        Each image has a newer volume than the one that appears in the
        XML, which was the latest volume of the image at the time the
        snapshot was taken, since we create new volume when we preview
        or revert to snapshot.
        """
        domain = MutableDomainDescriptor(srcDomXML)
        for element in domain.get_device_elements('disk'):
            if vmxml.attr(element, 'device') in ('disk', 'lun', '',):
                self._changeDisk(element)
        return domain.xml

    def _correctGraphicsConfiguration(self, domXML):
        """
        Fix the configuration of graphics device after resume.
        Make sure the ticketing settings are right
        """

        domObj = ET.fromstring(domXML)
        for devXml in domObj.findall('.//devices/graphics'):
            try:
                devObj = self._lookupDeviceByIdentification(
                    hwclass.GRAPHICS, devXml.get('type'))
            except LookupError:
                self.log.warning('configuration mismatch: graphics device '
                                 'type %s found in domain XML, but not among '
                                 'VM devices' % devXml.get('type'))
            else:
                devObj.setupPassword(devXml)
        return ET.tostring(domObj)

    def _changeDisk(self, disk_element):
        diskType = vmxml.attr(disk_element, 'type')
        if diskType not in ['file', 'block']:
            return
        serial = vmxml.text(vmxml.find_first(disk_element, 'serial'))
        for vm_drive in self._devices[hwclass.DISK]:
            if vm_drive.serial == serial:
                # update the type
                disk_type = 'block' if vm_drive.blockDev else 'file'
                vmxml.set_attr(disk_element, 'type', disk_type)
                # update the path
                source = vmxml.find_first(disk_element, 'source')
                disk_attr = 'dev' if vm_drive.blockDev else 'file'
                vmxml.set_attr(source, disk_attr, vm_drive.path)
                # update the format (the disk might have been collapsed)
                driver = vmxml.find_first(disk_element, 'driver')
                drive_format = 'qcow2' if vm_drive.format == 'cow' else 'raw'
                vmxml.set_attr(driver, 'type', drive_format)
                break

    @api.logged(on='vdsm.api')
    @api.guard(_not_migrating)
    def hotplugNic(self, params):
        nicParams = params['nic']
        nic = vmdevices.network.Interface(self.log, **nicParams)
        nicXml = vmxml.format_xml(nic.getXML(), pretty=True)
        nicXml = hooks.before_nic_hotplug(
            nicXml, self._custom, params=nic.custom
        )
        nic._deviceXML = nicXml
        # TODO: this is debug information. For 3.6.x we still need to
        # see the XML even with 'info' as default level.
        self.log.info("Hotplug NIC xml: %s", nicXml)

        try:
            nic.setup()
            self._dom.attachDevice(nicXml)
        except libvirt.libvirtError as e:
            self.log.exception("Hotplug failed")
            nicXml = hooks.after_nic_hotplug_fail(
                nicXml, self._custom, params=nic.custom)
            if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                raise exception.NoSuchVM()
            return response.error('hotplugNic', e.message)
        else:
            # FIXME!  We may have a problem here if vdsm dies right after
            # we sent command to libvirt and before save conf. In this case
            # we will gather almost all needed info about this NIC from
            # the libvirt during recovery process.
            device_conf = self._devices[hwclass.NIC]
            device_conf.append(nic)
            with self._confLock:
                self.conf['devices'].append(nicParams)
            self.saveState()
            vmdevices.network.Interface.update_device_info(self, device_conf)
            hooks.after_nic_hotplug(nicXml, self._custom,
                                    params=nic.custom)

        if hasattr(nic, 'portMirroring'):
            mirroredNetworks = []
            try:
                # pylint: disable=no-member
                for network in nic.portMirroring:
                    supervdsm.getProxy().setPortMirroring(network, nic.name)
                    mirroredNetworks.append(network)
            # The better way would be catch the proper exception.
            # One of such exceptions is TrafficControlException, but
            # I am not sure that we'll get it for all traffic control errors.
            # In any case we need below rollback for all kind of failures.
            except Exception as e:
                self.log.exception("setPortMirroring for network %s failed",
                                   network)
                nicParams['portMirroring'] = mirroredNetworks
                self.hotunplugNic({'nic': nicParams})
                return response.error('hotplugNic', e.message)

        return {'status': doneCode, 'vmList': self.status()}

    def _lookupDeviceByIdentification(self, devType, devIdent):
        for dev in self._devices[devType][:]:
            try:
                if dev.device == devIdent:
                    return dev
            except AttributeError:
                continue
        raise LookupError('Device object for device identified as %s '
                          'of type %s not found' % (devIdent, devType))

    @api.logged(on='vdsm.api')
    @api.guard(_not_migrating)
    def hostdevHotplug(self, dev_specs):
        dev_objects = []
        for dev_spec in dev_specs:
            dev_object = vmdevices.hostdevice.HostDevice(self.log, **dev_spec)
            dev_objects.append(dev_object)
            try:
                dev_object.setup()
            except libvirt.libvirtError:
                # We couldn't detach one of the devices. Halt.
                self.log.exception('Could not detach a device from a host.')
                return response.error('hostdevDetachErr')

        assigned_devices = []

        # Hard part is done, we have detached all devices without errors.
        # We now have to add devices to the VM while ignoring placeholders.
        for dev_spec, dev_object in zip(dev_specs, dev_objects):
            try:
                dev_xml = vmxml.format_xml(dev_object.getXML())
            except vmdevices.core.SkipDevice:
                self.log.info('Skipping device %s.', dev_object.device)
                continue

            dev_object._deviceXML = dev_xml
            self.log.info("Hotplug hostdev xml: %s", dev_xml)

            try:
                self._dom.attachDevice(dev_xml)
            except libvirt.libvirtError:
                self.log.exception('Skipping device %s.', dev_object.device)
                continue

            assigned_devices.append(dev_object.device)

            self._devices[hwclass.HOSTDEV].append(dev_object)

            with self._confLock:
                self.conf['devices'].append(dev_spec)
            self.saveState()
            vmdevices.hostdevice.HostDevice.update_device_info(
                self, self._devices[hwclass.HOSTDEV])

        return response.success(assignedDevices=assigned_devices)

    @api.logged(on='vdsm.api')
    @api.guard(_not_migrating)
    def hostdevHotunplug(self, dev_names):
        device_objects = []
        unplugged_devices = []

        for dev_name in dev_names:
            dev_object = None
            for dev in self._devices[hwclass.HOSTDEV][:]:
                if dev.device == dev_name:
                    dev_object = dev
                    device_objects.append(dev)
                    break

            if dev_object:
                device_xml = vmxml.format_xml(dev_object.getXML())
                self.log.debug('Hotunplug hostdev xml: %s', device_xml)
            else:
                self.log.error('Hotunplug hostdev failed (continuing) - '
                               'device not found: %s', dev_name)
                continue

            self._devices[hwclass.HOSTDEV].remove(dev_object)
            dev_spec = None
            for dev in self.conf['devices'][:]:
                if (dev['type'] == hwclass.HOSTDEV and
                        dev['device'] == dev_object.device):
                    dev_spec = dev
                    with self._confLock:
                        self.conf['devices'].remove(dev)
                    break

            self.saveState()

            try:
                self._dom.detachDevice(device_xml)
                self._waitForDeviceRemoval(dev_object)
            except HotunplugTimeout as e:
                self.log.error('%s', e)
                self._hostdev_hotunplug_restore(dev_object, dev_spec)
                continue
            except libvirt.libvirtError as e:
                self.log.exception('Hotunplug failed (continuing)')
                self._hostdev_hotunplug_restore(dev_object, dev_spec)
                continue

            dev_object.teardown()
            unplugged_devices.append(dev_name)

        return response.success(unpluggedDevices=unplugged_devices)

    def _hostdev_hotunplug_restore(self, dev_object, dev_spec):
        with self._confLock:
            self.conf['devices'].append(dev_spec)
        self._devices[hwclass.HOSTDEV].append(dev_object)
        self.saveState()

    def _lookupDeviceByPath(self, path):
        for dev in self._devices[hwclass.DISK][:]:
            try:
                if dev.path == path:
                    return dev
            except AttributeError:
                continue
        raise LookupError('Device instance for device with path {0} not found'
                          ''.format(path))

    def _lookupConfByPath(self, path):
        for devConf in self.conf['devices'][:]:
            if devConf.get('path') == path:
                return devConf
        raise LookupError('Configuration of device with path {0} not found'
                          ''.format(path))

    def _updateInterfaceDevice(self, params):
        try:
            netDev = vmdevices.common.lookup_device_by_alias(
                self._devices, hwclass.NIC, params['alias'])
            netConf = vmdevices.common.lookup_conf_by_alias(
                self.conf['devices'], hwclass.NIC, params['alias'])

            linkValue = 'up' if conv.tobool(
                params.get('linkActive', netDev.linkActive)) else 'down'
            network = params.get('network', netDev.network)
            if network == '':
                network = net_api.DUMMY_BRIDGE
                linkValue = 'down'
            custom = params.get('custom', {})
            specParams = params.get('specParams')

            netsToMirror = params.get('portMirroring',
                                      netConf.get('portMirroring', []))

            with self.setLinkAndNetwork(netDev, netConf, linkValue, network,
                                        custom, specParams):
                with self.updatePortMirroring(netConf, netsToMirror):
                    return {'status': doneCode, 'vmList': self.status()}
        except (LookupError,
                SetLinkAndNetworkError,
                UpdatePortMirroringError) as e:
            return response.error('updateDevice', e.message)

    @contextmanager
    def migration_parameters(self, params):
        with self._confLock:
            self.conf['_migrationParams'] = params
        try:
            yield
        finally:
            with self._confLock:
                del self.conf['_migrationParams']

    @contextmanager
    def setLinkAndNetwork(self, dev, conf, linkValue, networkValue, custom,
                          specParams=None):
        vnicXML = dev.getXML()
        source = vmxml.find_first(vnicXML, 'source')
        vmxml.set_attr(source, 'bridge', networkValue)
        try:
            link = vmxml.find_first(vnicXML, 'link')
        except vmxml.NotFound:
            link = vnicXML.appendChildWithArgs('link')
        vmxml.set_attr(link, 'state', linkValue)
        vmdevices.network.update_bandwidth_xml(dev, vnicXML, specParams)
        vnicStrXML = vmxml.format_xml(vnicXML, pretty=True)
        try:
            try:
                vnicStrXML = hooks.before_update_device(
                    vnicStrXML, self._custom, custom)
                self._dom.updateDeviceFlags(vnicStrXML,
                                            libvirt.VIR_DOMAIN_AFFECT_LIVE)
                dev._deviceXML = vnicStrXML
                self.log.info("Nic has been updated:\n %s" % vnicStrXML)
                hooks.after_update_device(vnicStrXML, self._custom, custom)
            except Exception as e:
                self.log.warn('Request failed: %s', vnicStrXML, exc_info=True)
                hooks.after_update_device_fail(
                    vnicStrXML, self._custom, custom
                )
                raise SetLinkAndNetworkError(str(e))
            yield
        except Exception:
            # Rollback link and network.
            self.log.warn('Rolling back link and net for: %s', dev.alias,
                          exc_info=True)
            self._dom.updateDeviceFlags(vmxml.format_xml(vnicXML),
                                        libvirt.VIR_DOMAIN_AFFECT_LIVE)
            raise
        else:
            # Update the device and the configuration.
            dev.network = conf['network'] = networkValue
            conf['linkActive'] = linkValue == 'up'
            setattr(dev, 'linkActive', linkValue == 'up')
            dev.custom = custom

    @contextmanager
    def updatePortMirroring(self, conf, networks):
        devName = conf['name']
        netsToDrop = [net for net in conf.get('portMirroring', [])
                      if net not in networks]
        netsToAdd = [net for net in networks
                     if net not in conf.get('portMirroring', [])]
        mirroredNetworks = []
        droppedNetworks = []
        try:
            for network in netsToDrop:
                supervdsm.getProxy().unsetPortMirroring(network, devName)
                droppedNetworks.append(network)
            for network in netsToAdd:
                supervdsm.getProxy().setPortMirroring(network, devName)
                mirroredNetworks.append(network)
            yield
        except Exception as e:
            self.log.exception(
                "%s for network %s failed",
                'setPortMirroring' if network in netsToAdd else
                'unsetPortMirroring',
                network)
            # In case we fail, we rollback the Network mirroring.
            for network in mirroredNetworks:
                supervdsm.getProxy().unsetPortMirroring(network, devName)
            for network in droppedNetworks:
                supervdsm.getProxy().setPortMirroring(network, devName)
            raise UpdatePortMirroringError(str(e))
        else:
            # Update the conf with the new mirroring.
            conf['portMirroring'] = networks

    def _updateGraphicsDevice(self, params):
        graphics = self._findGraphicsDeviceXMLByType(params['graphicsType'])
        if graphics is not None:
            result = self._setTicketForGraphicDev(
                graphics, params['password'], params['ttl'],
                params.get('existingConnAction'),
                params.get('disconnectAction'), params['params'])
            if result['status']['code'] == 0:
                result['vmList'] = self.status()
            return result
        else:
            return response.error('updateDevice')

    @api.logged(on='vdsm.api')
    def updateDevice(self, params):
        if params.get('deviceType') == hwclass.NIC:
            return self._updateInterfaceDevice(params)
        elif params.get('deviceType') == hwclass.GRAPHICS:
            return self._updateGraphicsDevice(params)
        else:
            return response.error('noimpl')

    @api.logged(on='vdsm.api')
    @api.guard(_not_migrating)
    def hotunplugNic(self, params):
        nicParams = params['nic']

        # Find NIC object in vm's NICs list
        nic = None
        for dev in self._devices[hwclass.NIC][:]:
            if dev.macAddr.lower() == nicParams['macAddr'].lower():
                nic = dev
                break

        if nic:
            if 'portMirroring' in nicParams:
                for network in nicParams['portMirroring']:
                    supervdsm.getProxy().unsetPortMirroring(network, nic.name)

            nicXml = vmxml.format_xml(nic.getXML(), pretty=True)
            hooks.before_nic_hotunplug(nicXml, self._custom,
                                       params=nic.custom)
            # TODO: this is debug information. For 3.6.x we still need to
            # see the XML even with 'info' as default level.
            self.log.info("Hotunplug NIC xml: %s", nicXml)
        else:
            self.log.error("Hotunplug NIC failed - NIC not found: %s",
                           nicParams)
            return response.error('hotunplugNic', "NIC not found")

        # Remove found NIC from vm's NICs list
        if nic:
            self._devices[hwclass.NIC].remove(nic)
        # Find and remove NIC device from vm's conf
        nicDev = None
        for dev in self.conf['devices'][:]:
            if (dev['type'] == hwclass.NIC and
                    dev['macAddr'].lower() == nicParams['macAddr'].lower()):
                with self._confLock:
                    self.conf['devices'].remove(dev)
                nicDev = dev
                break

        self.saveState()

        try:
            self._dom.detachDevice(nicXml)
            self._waitForDeviceRemoval(nic)
            nic.teardown()
        except HotunplugTimeout as e:
            self.log.error("%s", e)
            self._rollback_nic_hotunplug(nicDev, nic)
            hooks.after_nic_hotunplug_fail(nicXml, self._custom,
                                           params=nic.custom)
            return response.error('hotunplugNic', "%s" % e)
        except libvirt.libvirtError as e:
            self.log.exception("Hotunplug failed")
            if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                raise exception.NoSuchVM()
            self._rollback_nic_hotunplug(nicDev, nic)
            hooks.after_nic_hotunplug_fail(nicXml, self._custom,
                                           params=nic.custom)
            return response.error('hotunplugNic', e.message)

        hooks.after_nic_hotunplug(nicXml, self._custom,
                                  params=nic.custom)
        return {'status': doneCode, 'vmList': self.status()}

    # Restore NIC device in vm's conf and _devices
    def _rollback_nic_hotunplug(self, nic_dev, nic):
        if nic_dev:
            with self._confLock:
                self.conf['devices'].append(nic_dev)
        if nic:
            self._devices[hwclass.NIC].append(nic)
        self.saveState()

    @api.logged(on='vdsm.api')
    @api.guard(_not_migrating)
    def hotplugMemory(self, params):
        memParams = params.get('memory', {})
        device = vmdevices.core.Memory(self.log, **memParams)

        deviceXml = vmxml.format_xml(device.getXML())
        deviceXml = hooks.before_memory_hotplug(deviceXml)
        device._deviceXML = deviceXml
        self.log.debug("Hotplug memory xml: %s", deviceXml)

        try:
            self._dom.attachDevice(deviceXml)
        except libvirt.libvirtError as e:
            self.log.exception("hotplugMemory failed")
            if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                raise exception.NoSuchVM()
            return response.error('hotplugMem', e.message)

        self._devices[hwclass.MEMORY].append(device)
        with self._confLock:
            self.conf['devices'].append(memParams)
        self._updateDomainDescriptor()
        device.update_device_info(self, self._devices[hwclass.MEMORY])
        # TODO: this is raceful (as the similar code of hotplugDisk
        # and hotplugNic, as a concurrent call of hotplug can change
        # vm.conf before we return.
        self.saveState()

        hooks.after_memory_hotplug(deviceXml)

        return {'status': doneCode, 'vmList': self.status()}

    @api.logged(on='vdsm.api')
    @api.guard(_not_migrating)
    def hotunplugMemory(self, params):
        device = vmdevices.common.lookup_device_by_alias(
            self._devices, hwclass.MEMORY, params['memory']['alias'])
        device_xml = vmxml.format_xml(device.getXML())
        self.log.info("Hotunplug memory xml: %s", device_xml)

        try:
            self._dom.detachDevice(device_xml)
        except libvirt.libvirtError as e:
            if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                raise exception.NoSuchVM(vmId=self.id)
            raise exception.HotunplugMemFailed(str(e), vmId=self.id)

        return response.success()

    @api.logged(on='vdsm.api')
    @api.guard(_not_migrating)
    def setNumberOfCpus(self, numberOfCpus):
        self.log.debug("Setting number of cpus to : %s", numberOfCpus)
        hooks.before_set_num_of_cpus()
        try:
            self._dom.setVcpusFlags(numberOfCpus,
                                    libvirt.VIR_DOMAIN_AFFECT_CURRENT)
        except libvirt.libvirtError as e:
            self.log.exception("setNumberOfCpus failed")
            if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                raise exception.NoSuchVM()
            return response.error('setNumberOfCpusErr', e.message)

        self.conf['smp'] = str(numberOfCpus)
        self.saveState()
        hooks.after_set_num_of_cpus()
        return {'status': doneCode, 'vmList': self.status()}

    def _updateVcpuLimit(self):
        qos = self._getVmPolicy()
        if qos is not None:
            try:
                vcpuLimit = vmxml.find_first(qos, "vcpuLimit")
                self._vcpuLimit = vmxml.text(vcpuLimit)
            except vmxml.NotFound:
                # missing vcpuLimit node
                self._vcpuLimit = None

    def _updateIoTuneInfo(self):
        qos = self._getVmPolicy()
        if qos is None:
            self._ioTuneInfo = []
            return

        io_tune = vmxml.find_first(qos, "ioTune", None)
        if io_tune is None:
            self._ioTuneInfo = []
            return

        self._ioTuneInfo = vmtune.io_tune_dom_all_to_list(io_tune)

    @api.logged(on='vdsm.api')
    @api.guard(_not_migrating)
    def updateVmPolicy(self, params):
        """
        Update the QoS policy settings for VMs.

        The params argument contains the actual properties we are about to
        set. It must not be empty.

        Supported properties are:

        vcpuLimit - the CPU usage hard limit
        ioTune - the IO limits

        In the case not all properties are provided, the missing properties'
        setting will be left intact.

        If there is an error during the processing, this function
        immediately stops and returns. Remaining properties are not
        processed.

        :param params: dictionary mapping property name to its value
        :type params: dict[str] -> anything

        :return: standard vdsm result structure
        """
        if not params:
            self.log.error("updateVmPolicy got an empty policy.")
            return response.error('MissParam',
                                  'updateVmPolicy got an empty policy.')

        #
        # Get the current QoS block
        metadata_modified = False
        qos = self._getVmPolicy()
        if qos is None:
            return response.error('updateVmPolicyErr')

        #
        # Process provided properties, remove property after it is processed

        if 'vcpuLimit' in params:
            # Remove old value
            vcpuLimit = vmxml.find_first(qos, "vcpuLimit", None)
            if vcpuLimit is not None:
                vmxml.remove_child(qos, vcpuLimit)

            vcpuLimit = vmxml.Element("vcpuLimit")
            vcpuLimit.appendTextNode(str(params["vcpuLimit"]))
            vmxml.append_child(qos, vcpuLimit)

            metadata_modified = True
            self._vcpuLimit = params.pop('vcpuLimit')

        if 'ioTune' in params:
            ioTuneParams = params["ioTune"]

            for ioTune in ioTuneParams:
                if ("path" in ioTune) or ("name" in ioTune):
                    continue

                self.log.debug("IoTuneParams: %s", str(ioTune))

                try:
                    # All 4 IDs are required to identify a device
                    # If there is a valid reason why not all 4 are required,
                    # please change the code

                    disk = self._findDriveByUUIDs({
                        'domainID': ioTune["domainID"],
                        'poolID': ioTune["poolID"],
                        'imageID': ioTune["imageID"],
                        'volumeID': ioTune["volumeID"]})

                    self.log.debug("Device path: %s", disk.path)
                    ioTune["name"] = disk.name
                    ioTune["path"] = disk.path

                except LookupError as e:
                    return response.error('updateVmPolicyErr', e.message)

            if ioTuneParams:
                io_tunes = []

                io_tune_element = vmxml.find_first(qos, "ioTune", None)
                if io_tune_element is not None:
                    io_tunes = vmtune.io_tune_dom_all_to_list(io_tune_element)
                    vmxml.remove_child(qos, io_tune_element)

                vmtune.io_tune_update_list(io_tunes, ioTuneParams)

                vmxml.append_child(qos, vmtune.io_tune_list_to_dom(io_tunes))

                metadata_modified = True

                self._ioTuneInfo = io_tunes

            del params['ioTune']

        # Check remaining fields in params and report the list of unsupported
        # params to the log

        if params:
            self.log.warn("updateVmPolicy got unknown parameters: %s",
                          ", ".join(params.iterkeys()))

        #
        # Save modified metadata

        if metadata_modified:
            metadata_xml = vmxml.format_xml(qos)

            try:
                self._dom.setMetadata(libvirt.VIR_DOMAIN_METADATA_ELEMENT,
                                      metadata_xml,
                                      xmlconstants.METADATA_VM_TUNE_PREFIX,
                                      xmlconstants.METADATA_VM_TUNE_URI,
                                      0)
            except libvirt.libvirtError as e:
                self.log.exception("updateVmPolicy failed")
                if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                    raise exception.NoSuchVM()
                else:
                    return response.error('updateVmPolicyErr', e.message)

        return {'status': doneCode}

    def _getVmPolicy(self):
        """
        This method gets the current qos block from the libvirt metadata.
        If there is not any, it will create a new empty DOM tree with
        the <qos> root element.

        :return: XML DOM object representing the root qos element
        """

        metadata_xml = "<%s></%s>" % (
            xmlconstants.METADATA_VM_TUNE_ELEMENT,
            xmlconstants.METADATA_VM_TUNE_ELEMENT
        )

        try:
            metadata_xml = self._dom.metadata(
                libvirt.VIR_DOMAIN_METADATA_ELEMENT,
                xmlconstants.METADATA_VM_TUNE_URI,
                0)
        except virdomain.NotConnectedError:
            self.log.warning("Failed to get metadata, domain not connected.")
            return None
        except libvirt.libvirtError as e:
            if e.get_error_code() != libvirt.VIR_ERR_NO_DOMAIN_METADATA:
                self.log.exception("getVmPolicy failed")
                return None

        metadata = vmxml.parse_xml(metadata_xml)
        return vmxml.find_first(
            metadata,
            xmlconstants.METADATA_VM_TUNE_ELEMENT,
            None)

    def _findDeviceByNameOrPath(self, device_name, device_path):
        for device in self._devices[hwclass.DISK]:
            if ((device.name == device_name or
                ("path" in device and device["path"] == device_path)) and
                    isVdsmImage(device)):
                return device
        else:
            return None

    def getIoTunePolicyResponse(self):
        tunables = self.getIoTunePolicy()
        return response.success(ioTunePolicyList=tunables)

    def getIoTunePolicy(self):
        return self._ioTuneInfo

    def getIoTune(self):
        result = self.getIoTuneResponse()
        if response.is_error(result):
            return []
        return result.get('ioTuneList', [])

    def getIoTuneResponse(self):
        resultList = []

        for device in self.getDiskDevices():
            if not isVdsmImage(device):
                continue

            try:
                need_update = False
                with self._ioTuneLock:
                    ioTune = self._ioTuneValues.get(device.name)

                if not ioTune:
                    need_update = True
                    res = self._dom.blockIoTune(
                        device.name,
                        libvirt.VIR_DOMAIN_AFFECT_LIVE)

                    # use only certain fields, otherwise
                    # Drive._validateIoTuneParams will not pass
                    ioTune = {k: res[k] for k in (
                        'total_bytes_sec', 'read_bytes_sec',
                        'write_bytes_sec', 'total_iops_sec',
                        'write_iops_sec', 'read_iops_sec')}

                resultList.append({
                    'name': device.name,
                    'path': device.path,
                    'ioTune': ioTune})

                if need_update:
                    with self._ioTuneLock:
                        if not self._ioTuneValues.get(device.name):
                            self._ioTuneValues[device.name] = ioTune

            except libvirt.libvirtError as e:
                self.log.exception("getVmIoTune failed")
                if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                    raise exception.NoSuchVM()
                else:
                    self.log.error('updateIoTuneErr', e.message)
                    return response.error('updateIoTuneErr', e.message)

        return response.success(ioTuneList=resultList)

    @api.logged(on='vdsm.api')
    def setIoTune(self, tunables):
        for io_tune_change in tunables:
            device_name = io_tune_change.get('name', None)
            device_path = io_tune_change.get('path', None)
            io_tune = io_tune_change['ioTune']

            # Find the proper device object
            found_device = self._findDeviceByNameOrPath(device_name,
                                                        device_path)
            if found_device is None:
                raise exception.UpdateIOTuneError(
                    "Device {} not found".format(device_name))

            # Merge the update with current values
            old_io_tune = found_device.iotune
            old_io_tune.update(io_tune)
            io_tune = old_io_tune

            # Verify the ioTune params
            try:
                vmtune.validate_io_tune_params(io_tune)
            except ValueError:
                raise exception.UpdateIOTuneError('Invalid ioTune value')

            try:
                self._dom.setBlockIoTune(found_device.name, io_tune,
                                         libvirt.VIR_DOMAIN_AFFECT_LIVE)
            except libvirt.libvirtError as e:
                self.log.exception("setVmIoTune failed")
                if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                    raise exception.NoSuchVM()
                else:
                    raise exception.UpdateIOTuneError(e.message)

            with self._ioTuneLock:
                self._ioTuneValues[found_device.name] = io_tune

            # TODO: improve once libvirt gets support for iotune events
            #       see https://bugzilla.redhat.com/show_bug.cgi?id=1114492
            found_device.iotune = io_tune

            # Make sure the cached XML representation is valid as well
            xml = vmxml.format_xml(found_device.getXML())
            # TODO: this is debug information. For 3.6.x we still need to
            # see the XML even with 'info' as default level.
            self.log.info("New device XML for %s: %s",
                          found_device.name, xml)
            found_device._deviceXML = xml

        return response.success()

    def _createTransientDisk(self, diskParams):
        if (diskParams.get('shared', None) !=
           vmdevices.storage.DRIVE_SHARED_TYPE.TRANSIENT):
            return

        # FIXME: This should be replaced in future the support for transient
        # disk in libvirt (BZ#832194)
        driveFormat = (
            qemuimg.FORMAT.QCOW2 if diskParams['format'] == 'cow' else
            qemuimg.FORMAT.RAW
        )

        transientHandle, transientPath = tempfile.mkstemp(
            dir=config.get('vars', 'transient_disks_repository'),
            prefix="%s-%s." % (diskParams['domainID'], diskParams['volumeID']))

        try:
            sdDom = sdc.sdCache.produce_manifest(diskParams['domainID'])
            qemuimg.create(transientPath,
                           format=qemuimg.FORMAT.QCOW2,
                           qcow2Compat=sdDom.qcow2_compat(),
                           backing=diskParams['path'],
                           backingFormat=driveFormat)
            os.fchmod(transientHandle, 0o660)
        except Exception:
            os.unlink(transientPath)  # Closing after deletion is correct
            self.log.exception("Failed to create the transient disk for "
                               "volume %s", diskParams['volumeID'])
        finally:
            os.close(transientHandle)

        diskParams['path'] = transientPath
        diskParams['format'] = 'cow'

    def _removeTransientDisk(self, drive):
        if drive.transientDisk:
            os.unlink(drive.path)

    @api.logged(on='vdsm.api')
    @api.guard(_not_migrating)
    def hotplugDisk(self, params):
        diskParams = params.get('drive', {})
        diskParams['path'] = self.cif.prepareVolumePath(diskParams)

        if isVdsmImage(diskParams):
            self._normalizeVdsmImg(diskParams)
            self._createTransientDisk(diskParams)

        self.updateDriveIndex(diskParams)
        drive = vmdevices.storage.Drive(self.log, **diskParams)

        if drive.hasVolumeLeases:
            return response.error('noimpl')

        driveXml = vmxml.format_xml(drive.getXML(), pretty=True)
        # TODO: this is debug information. For 3.6.x we still need to
        # see the XML even with 'info' as default level.
        self.log.info("Hotplug disk xml: %s" % (driveXml))

        driveXml = hooks.before_disk_hotplug(driveXml, self._custom,
                                             params=drive.custom)
        drive._deviceXML = driveXml
        try:
            self._dom.attachDevice(driveXml)
        except libvirt.libvirtError as e:
            self.log.exception("Hotplug failed")
            self.cif.teardownVolumePath(diskParams)
            if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                raise exception.NoSuchVM()
            return response.error('hotplugDisk', e.message)
        else:
            # FIXME!  We may have a problem here if vdsm dies right after
            # we sent command to libvirt and before save conf. In this case
            # we will gather almost all needed info about this drive from
            # the libvirt during recovery process.
            device_conf = self._devices[hwclass.DISK]
            device_conf.append(drive)

            with self._confLock:
                self.conf['devices'].append(diskParams)
            self.saveState()
            vmdevices.storage.Drive.update_device_info(self, device_conf)
            hooks.after_disk_hotplug(driveXml, self._custom,
                                     params=drive.custom)

        return {'status': doneCode, 'vmList': self.status()}

    @api.logged(on='vdsm.api')
    @api.guard(_not_migrating)
    def hotunplugDisk(self, params):
        diskParams = params.get('drive', {})
        diskParams['path'] = self.cif.prepareVolumePath(diskParams)

        try:
            drive = self._findDriveByUUIDs(diskParams)
        except LookupError:
            self.log.error("Hotunplug disk failed - Disk not found: %s",
                           diskParams)
            return response.error('hotunplugDisk', "Disk not found")

        if drive.hasVolumeLeases:
            return response.error('noimpl')

        driveXml = vmxml.format_xml(drive.getXML(), pretty=True)
        # TODO: this is debug information. For 3.6.x we still need to
        # see the XML even with 'info' as default level.
        self.log.info("Hotunplug disk xml: %s", driveXml)

        hooks.before_disk_hotunplug(driveXml, self._custom,
                                    params=drive.custom)
        try:
            self._dom.detachDevice(driveXml)
            self._waitForDeviceRemoval(drive)
        except HotunplugTimeout as e:
            self.log.error("%s", e)
            return response.error('hotunplugDisk', "%s" % e)
        except libvirt.libvirtError as e:
            self.log.exception("Hotunplug failed")
            if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                raise exception.NoSuchVM()
            return response.error('hotunplugDisk', e.message)
        else:
            self._devices[hwclass.DISK].remove(drive)

            # Find and remove disk device from vm's conf
            for dev in self.conf['devices'][:]:
                if dev['type'] == hwclass.DISK and dev['path'] == drive.path:
                    with self._confLock:
                        self.conf['devices'].remove(dev)
                    break

            self.saveState()
            hooks.after_disk_hotunplug(driveXml, self._custom,
                                       params=drive.custom)
            self._cleanupDrives(drive)

        return {'status': doneCode, 'vmList': self.status()}

    @api.logged(on='vdsm.api')
    @api.guard(_not_migrating)
    def hotplugLease(self, params):
        vmdevices.lease.prepare(self.cif.irs, [params])
        lease = vmdevices.lease.Device(self.conf, self.log, **params)

        leaseXml = vmxml.format_xml(lease.getXML(), pretty=True)
        self.log.info("Hotplug lease xml: %s", leaseXml)

        try:
            self._dom.attachDevice(leaseXml)
        except libvirt.libvirtError as e:
            # TODO: repeated in many places, move to domain wrapper?
            if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                raise exception.NoSuchVM(vmId=self.id)
            raise exception.HotplugLeaseFailed(reason=str(e), lease=lease)

        self._devices[hwclass.LEASE].append(lease)

        with self._confLock:
            self.conf['devices'].append(params)
        self.saveState()

        return response.success(vmList=self.status())

    @api.logged(on='vdsm.api')
    @api.guard(_not_migrating)
    def hotunplugLease(self, params):
        try:
            lease = vmdevices.lease.find_device(self._devices, params)
        except LookupError:
            raise exception.HotunplugLeaseFailed(reason="No such lease",
                                                 lease=params)

        leaseXml = vmxml.format_xml(lease.getXML(), pretty=True)
        self.log.info("Hotunplug lease xml: %s", leaseXml)

        try:
            self._dom.detachDevice(leaseXml)
            self._waitForDeviceRemoval(lease)
        except HotunplugTimeout as e:
            raise exception.HotunplugLeaseFailed(reason=str(e), lease=lease)
        except libvirt.libvirtError as e:
            # TODO: repeated in many places, move to domain wrapper?
            if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                raise exception.NoSuchVM(vmId=self.id)
            raise exception.HotunplugLeaseFailed(reason=str(e), lease=lease)

        self._devices[hwclass.LEASE].remove(lease)

        try:
            conf = vmdevices.lease.find_conf(self.conf, lease)
        except LookupError:
            # Unepected, but should not break successful unplug.
            self.log.warning("No conf for lease %s", lease)
        else:
            with self._confLock:
                self.conf['devices'].remove(conf)
            self.saveState()

        return response.success(vmList=self.status())

    def _waitForDeviceRemoval(self, device):
        """
        As stated in libvirt documentary, after detaching a device using
        virDomainDetachDeviceFlags, we need to verify that this device
        has actually been detached:
        libvirt.org/html/libvirt-libvirt-domain.html#virDomainDetachDeviceFlags

        This function waits for the device to be detached.

        Currently we use virDomainDetachDevice. However- That function behaves
        the same in that matter. (Currently it is not documented at libvirt's
        API docs- but after contacting libvirt's guys it turned out that this
        is true. Bug 1257280 opened for fixing the documentation.)
        TODO: remove this comment when the documentation will be fixed.

        :param device: Device to wait for
        """
        self.log.debug("Waiting for hotunplug to finish")
        with utils.stopwatch("Hotunplug %r" % device):
            deadline = (vdsm.common.time.monotonic_time() +
                        config.getfloat('vars', 'hotunplug_timeout'))
            sleep_time = config.getfloat('vars', 'hotunplug_check_interval')
            while device.is_attached_to(self._dom.XMLDesc(0)):
                time.sleep(sleep_time)
                if vdsm.common.time.monotonic_time() > deadline:
                    raise HotunplugTimeout("Timeout detaching %r" % device)

    def _readPauseCode(self):
        state, reason = self._dom.state(0)

        if (state == libvirt.VIR_DOMAIN_PAUSED and
           reason == libvirt.VIR_DOMAIN_PAUSED_IOERROR):

            diskErrors = self._dom.diskErrors()
            for device, error in diskErrors.iteritems():
                if error == libvirt.VIR_DOMAIN_DISK_ERROR_NO_SPACE:
                    self.log.warning('device %s out of space', device)
                    return 'ENOSPC'
                elif error == libvirt.VIR_DOMAIN_DISK_ERROR_UNSPEC:
                    # Mapping to 'EOTHER' may not be exact.
                    # It is still safer than EIO given the VDSM mechanics.
                    self.log.warning('device %s reported I/O error',
                                     device)
                    return 'EOTHER'
                # else error == libvirt.VIR_DOMAIN_DISK_ERROR_NONE
                # so no worries.

        return 'NOERR'

    def isDomainReadyForCommands(self):
        """
        Returns True if the domain is reported to be in the safest condition
        to accept commands.
        False negative (domain is reported NOT ready, but it is) is possible
        False positive (domain is reported ready, but it is NOT) is avoided
        """
        try:
            state, details, stateTime = self._dom.controlInfo()
        except virdomain.NotConnectedError:
            # this method may be called asynchronously by periodic
            # operations. Thus, we must use a try/except block
            # to avoid racy checks.
            return False
        except libvirt.libvirtError as e:
            if e.get_error_code() in (
                libvirt.VIR_ERR_NO_DOMAIN,  # race on shutdown
                libvirt.VIR_ERR_OPERATION_INVALID,  # race on migration end
            ):
                return False
            else:
                raise
        else:
            return state == libvirt.VIR_DOMAIN_CONTROL_OK

    def _timeoutExperienced(self, timeout):
        if timeout:
            self._monitorResponse = -1
        else:
            self._monitorResponse = 0

    def _completeIncomingMigration(self):
        if self._altered_state.origin == _FILE_ORIGIN:
            self.cont()
            fromSnapshot = self._altered_state.from_snapshot
            self._altered_state = _AlteredState()
            hooks.after_vm_dehibernate(self._dom.XMLDesc(0), self._custom,
                                       {'FROM_SNAPSHOT': fromSnapshot})
            self._syncGuestTime()
        elif self._altered_state.origin == _MIGRATION_ORIGIN:
            if self._needToWaitForMigrationToComplete():
                finished, timeout = self._waitForUnderlyingMigration()
                if self._destroy_requested.is_set():
                    raise DestroyedOnStartupError()
                self._attachLibvirtDomainAfterMigration(finished, timeout)
            # else domain connection already established earlier
            self._domDependentInit()
            self._altered_state = _AlteredState()
            hooks.after_vm_migrate_destination(
                self._dom.XMLDesc(0), self._custom)

            for dev in self._customDevices():
                hooks.after_device_migrate_destination(
                    dev._deviceXML, self._custom, dev.custom)

            # We refrain from syncing time in this path.  There are two basic
            # reasons:
            # 1. The jump change in the time (as performed by QEMU) may cause
            #    undesired effects like unnecessary timeouts, false alerts
            #    (think about logging excessive SQL command execution times),
            #    etc.  This is not what users expect when performing live
            #    migrations.
            # 2. The user can simply run NTP on the VM to keep the time right
            #    and smooth after migrations.  On the contrary to suspensions,
            #    there is no danger of excessive delays preventing NTP from
            #    operation.

        self._src_domain_xml = None  # just to save memory
        with self._confLock:
            if 'guestIPs' in self.conf:
                del self.conf['guestIPs']
            if 'guestFQDN' in self.conf:
                del self.conf['guestFQDN']
            if 'username' in self.conf:
                del self.conf['username']
        self.saveState()
        self._update_metadata()   # to store agent API version
        self.log.info("End of migration")

    def _needToWaitForMigrationToComplete(self):
        if not self.recovering:
            # if not recovering, we are in a base flow and need
            # to wait for migration to complete
            return True

        try:
            if not self._isDomainRunning():
                # migration still in progress during recovery
                return True
        except libvirt.libvirtError:
            self.log.exception('migration failed while recovering!')
            raise MigrationError()
        else:
            self.log.info('migration completed while recovering!')
            return False

    def _waitForUnderlyingMigration(self):
        timeout = config.getint('vars', 'migration_destination_timeout')
        self.log.debug("Waiting %s seconds for end of migration", timeout)
        finished = self._incoming_migration_vm_running.wait(timeout)
        return finished, timeout

    def _attachLibvirtDomainAfterMigration(self, migrationFinished, timeout):
        try:
            # Would fail if migration isn't successful,
            # or restart vdsm if connection to libvirt was lost
            self._dom = virdomain.Notifying(
                self._connection.lookupByUUIDString(self.id),
                self._timeoutExperienced)

            if not migrationFinished:
                state = self._dom.state(0)
                if state[0] == libvirt.VIR_DOMAIN_PAUSED:
                    if state[1] == libvirt.VIR_DOMAIN_PAUSED_MIGRATION:
                        raise MigrationError("Migration Error - Timed out "
                                             "(did not receive success "
                                             "event)")
                self.log.debug("NOTE: incoming_migration_vm_running event has "
                               "not been set and wait timed out after %d "
                               "seconds. Current VM state: %d, reason %d. "
                               "Continuing with VM initialization anyway.",
                               timeout, state[0], state[1])
        except libvirt.libvirtError as e:
            if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                if not migrationFinished:
                    newMsg = ('%s - Timed out '
                              '(did not receive success event)' %
                              (e.args[0] if len(e.args) else
                               'Migration Error'))
                    e.args = (newMsg,) + e.args[1:]
                raise MigrationError(e.get_error_message())
            raise

    def _underlyingCont(self):
        hooks.before_vm_cont(self._dom.XMLDesc(0), self._custom)
        self._dom.resume()

    def _underlyingPause(self):
        hooks.before_vm_pause(self._dom.XMLDesc(0), self._custom)
        self._dom.suspend()

    def _findDriveByName(self, name):
        for device in self._devices[hwclass.DISK][:]:
            if device.name == name:
                return device
        raise LookupError("No such drive: '%s'" % name)

    def _findDriveByUUIDs(self, drive):
        """Find a drive given its definition"""

        if "domainID" in drive:
            tgetDrv = (drive["domainID"], drive["imageID"],
                       drive["volumeID"])

            for device in self._devices[hwclass.DISK][:]:
                if not hasattr(device, "domainID"):
                    continue
                if (device.domainID, device.imageID,
                        device.volumeID) == tgetDrv:
                    return device

        elif "GUID" in drive:
            for device in self._devices[hwclass.DISK][:]:
                if not hasattr(device, "GUID"):
                    continue
                if device.GUID == drive["GUID"]:
                    return device

        elif "UUID" in drive:
            for device in self._devices[hwclass.DISK][:]:
                if not hasattr(device, "UUID"):
                    continue
                if device.UUID == drive["UUID"]:
                    return device

        elif drive.get('diskType') == DISK_TYPE.NETWORK:
            for device in self._devices[hwclass.DISK][:]:
                if device.diskType != DISK_TYPE.NETWORK:
                    continue
                if device.path == drive["path"]:
                    return device

        raise LookupError("No such drive: '%s'" % drive)

    def _findDriveConfigByName(self, name):
        devices = self.conf["devices"][:]
        for device in devices:
            if device['type'] == hwclass.DISK and device.get("name") == name:
                return device
        raise LookupError("No such disk %r" % name)

    def updateDriveVolume(self, vmDrive):
        if not vmDrive.device == 'disk' or not isVdsmImage(vmDrive):
            return

        try:
            volSize = self._getVolumeSize(
                vmDrive.domainID, vmDrive.poolID, vmDrive.imageID,
                vmDrive.volumeID)
        except StorageUnavailableError as e:
            self.log.error("Unable to update drive %s volume size: %s",
                           vmDrive.name, e)
            return

        vmDrive.truesize = volSize.truesize
        vmDrive.apparentsize = volSize.apparentsize

    def updateDriveParameters(self, driveParams):
        """Update the drive with the new volume information"""

        # Updating the vmDrive object
        for vmDrive in self._devices[hwclass.DISK][:]:
            if vmDrive.name == driveParams["name"]:
                for k, v in driveParams.iteritems():
                    setattr(vmDrive, k, v)
                self.updateDriveVolume(vmDrive)
                break
        else:
            self.log.error("Unable to update the drive object for: %s",
                           driveParams["name"])

        # Updating the VM configuration
        try:
            conf = self._findDriveConfigByName(driveParams["name"])
        except LookupError:
            self.log.error("Unable to update the device configuration ",
                           "for disk %s", driveParams["name"])
        else:
            with self._confLock:
                conf.update(driveParams)
            self.saveState()

    @api.logged(on='vdsm.api')
    def freeze(self):
        """
        Freeze every mounted filesystems within the guest (hence guest agent
        may be required depending on hypervisor used).
        """
        self.log.info("Freezing guest filesystems")

        try:
            frozen = self._dom.fsFreeze()
        except libvirt.libvirtError as e:
            self.log.warning("Unable to freeze guest filesystems: %s", e)
            code = e.get_error_code()
            if code == libvirt.VIR_ERR_AGENT_UNRESPONSIVE:
                name = "nonresp"
            elif code == libvirt.VIR_ERR_NO_SUPPORT:
                name = "unsupportedOperationErr"
            else:
                name = "freezeErr"
            return response.error(name, message=e.get_error_message())

        self.log.info("%d guest filesystems frozen", frozen)
        return response.success()

    @api.logged(on='vdsm.api')
    def thaw(self):
        """
        Thaw every mounted filesystems within the guest (hence guest agent may
        be required depending on hypervisor used).
        """
        self.log.info("Thawing guest filesystems")

        try:
            thawed = self._dom.fsThaw()
        except libvirt.libvirtError as e:
            self.log.warning("Unable to thaw guest filesystems: %s", e)
            code = e.get_error_code()
            if code == libvirt.VIR_ERR_AGENT_UNRESPONSIVE:
                name = "nonresp"
            elif code == libvirt.VIR_ERR_NO_SUPPORT:
                name = "unsupportedOperationErr"
            else:
                name = "thawErr"
            return response.error(name, message=e.get_error_message())

        self.log.info("%d guest filesystems thawed", thawed)
        return response.success()

    @api.logged(on='vdsm.api')
    @api.guard(_not_migrating)
    def snapshot(self, snapDrives, memoryParams, frozen=False):
        """Live snapshot command"""

        def _normSnapDriveParams(drive):
            """Normalize snapshot parameters"""

            if "baseVolumeID" in drive:
                baseDrv = {"device": "disk",
                           "domainID": drive["domainID"],
                           "imageID": drive["imageID"],
                           "volumeID": drive["baseVolumeID"]}
                tgetDrv = baseDrv.copy()
                tgetDrv["volumeID"] = drive["volumeID"]

            elif "baseGUID" in drive:
                baseDrv = {"GUID": drive["baseGUID"]}
                tgetDrv = {"GUID": drive["GUID"]}

            elif "baseUUID" in drive:
                baseDrv = {"UUID": drive["baseUUID"]}
                tgetDrv = {"UUID": drive["UUID"]}

            else:
                baseDrv, tgetDrv = (None, None)

            return baseDrv, tgetDrv

        def _rollbackDrives(newDrives):
            """Rollback the prepared volumes for the snapshot"""

            for vmDevName, drive in newDrives.iteritems():
                try:
                    self.cif.teardownVolumePath(drive)
                except Exception:
                    self.log.exception("Unable to teardown drive: %s",
                                       vmDevName)

        def _memorySnapshot(memoryVolumePath):
            """Libvirt snapshot XML"""

            return vmxml.Element('memory',
                                 snapshot='external',
                                 file=memoryVolumePath)

        def _vmConfForMemorySnapshot():
            """Returns the needed vm configuration with the memory snapshot"""

            return {'restoreFromSnapshot': True,
                    '_srcDomXML': self._dom.XMLDesc(0),
                    'elapsedTimeOffset': time.time() - self._startTime}

        def _padMemoryVolume(memoryVolPath, sdUUID):
            sdType = sd.name2type(
                self.cif.irs.getStorageDomainInfo(sdUUID)['info']['type'])
            if sdType in sd.FILE_DOMAIN_TYPES:
                if sdType == sd.NFS_DOMAIN:
                    oop.getProcessPool(sdUUID).fileUtils. \
                        padToBlockSize(memoryVolPath)
                else:
                    fileUtils.padToBlockSize(memoryVolPath)

        snap = vmxml.Element('domainsnapshot')
        disks = vmxml.Element('disks')
        newDrives = {}
        vmDrives = {}

        for drive in snapDrives:
            baseDrv, tgetDrv = _normSnapDriveParams(drive)

            try:
                self._findDriveByUUIDs(tgetDrv)
            except LookupError:
                # The vm is not already using the requested volume for the
                # snapshot, continuing.
                pass
            else:
                # The snapshot volume is the current one, skipping
                self.log.debug("The volume is already in use: %s", tgetDrv)
                continue  # Next drive

            try:
                vmDrive = self._findDriveByUUIDs(baseDrv)
            except LookupError:
                # The volume we want to snapshot doesn't exist
                self.log.error("The base volume doesn't exist: %s", baseDrv)
                return response.error('snapshotErr')

            if vmDrive.hasVolumeLeases:
                self.log.error('disk %s has volume leases', vmDrive.name)
                return response.error('noimpl')

            if vmDrive.transientDisk:
                self.log.error('disk %s is a transient disk', vmDrive.name)
                return response.error('transientErr')

            vmDevName = vmDrive.name

            newDrives[vmDevName] = tgetDrv.copy()
            newDrives[vmDevName]["diskType"] = vmDrive.diskType
            newDrives[vmDevName]["poolID"] = vmDrive.poolID
            newDrives[vmDevName]["name"] = vmDevName
            newDrives[vmDevName]["format"] = "cow"

            # We need to keep track of the drive object because we cannot
            # safely access the blockDev property until after prepareVolumePath
            vmDrives[vmDevName] = vmDrive

        preparedDrives = {}

        for vmDevName, vmDevice in newDrives.iteritems():
            # Adding the device before requesting to prepare it as we want
            # to be sure to teardown it down even when prepareVolumePath
            # failed for some unknown issue that left the volume active.
            preparedDrives[vmDevName] = vmDevice
            try:
                newDrives[vmDevName]["path"] = \
                    self.cif.prepareVolumePath(newDrives[vmDevName])
            except Exception:
                self.log.exception('unable to prepare the volume path for '
                                   'disk %s', vmDevName)
                _rollbackDrives(preparedDrives)
                return response.error('snapshotErr')

            drive = vmDrives[vmDevName]
            snapelem = drive.get_snapshot_xml(vmDevice)
            disks.appendChild(snapelem)

        snap.appendChild(disks)

        snapFlags = (libvirt.VIR_DOMAIN_SNAPSHOT_CREATE_REUSE_EXT |
                     libvirt.VIR_DOMAIN_SNAPSHOT_CREATE_NO_METADATA)

        if memoryParams:
            # Save the needed vm configuration
            # TODO: this, as other places that use pickle.dump
            # directly to files, should be done with outOfProcess
            vmConfVol = memoryParams['dstparams']
            vmConfVolPath = self.cif.prepareVolumePath(vmConfVol)
            vmConf = _vmConfForMemorySnapshot()
            try:
                # Use r+ to avoid truncating the file, see BZ#1282239
                with open(vmConfVolPath, "r+") as f:
                    pickle.dump(vmConf, f)
            finally:
                self.cif.teardownVolumePath(vmConfVol)

            # Adding the memory volume to the snapshot xml
            memoryVol = memoryParams['dst']
            memoryVolPath = self.cif.prepareVolumePath(memoryVol)
            snap.appendChild(_memorySnapshot(memoryVolPath))
        else:
            snapFlags |= libvirt.VIR_DOMAIN_SNAPSHOT_CREATE_DISK_ONLY

        # When creating memory snapshot libvirt will pause the vm
        should_freeze = not (memoryParams or frozen)

        snapxml = vmxml.format_xml(snap)
        # TODO: this is debug information. For 3.6.x we still need to
        # see the XML even with 'info' as default level.
        self.log.info(snapxml)

        # We need to stop the drive monitoring for two reasons, one is to
        # prevent spurious libvirt errors about missing drive paths (since
        # we're changing them), and also to prevent to trigger a drive
        # extension for the new volume with the apparent size of the old one
        # (the apparentsize is updated as last step in updateDriveParameters)
        self.disableDriveMonitor()

        try:
            if should_freeze:
                freezed = self.freeze()
            try:
                self.log.info("Taking a live snapshot (drives=%s, memory=%s)",
                              ', '.join(drive["name"] for drive in
                                        newDrives.values()),
                              memoryParams is not None)
                self._dom.snapshotCreateXML(snapxml, snapFlags)
                self.log.info("Completed live snapshot")
            except libvirt.libvirtError:
                self.log.exception("Unable to take snapshot")
                return response.error('snapshotErr')
            finally:
                # Must always thaw, even if freeze failed; in case the guest
                # did freeze the filesystems, but failed to reply in time.
                # Libvirt is using same logic (see src/qemu/qemu_driver.c).
                if should_freeze:
                    self.thaw()

            # We are padding the memory volume with block size of zeroes
            # because qemu-img truncates files such that their size is
            # round down to the closest multiple of block size (bz 970559).
            # This code should be removed once qemu-img will handle files
            # with size that is not multiple of block size correctly.
            if memoryParams:
                _padMemoryVolume(memoryVolPath, memoryVol['domainID'])

            for drive in newDrives.values():  # Update the drive information
                try:
                    self.updateDriveParameters(drive)
                except Exception:
                    # Here it's too late to fail, the switch already happened
                    # and there's nothing we can do, we must to proceed anyway
                    # to report the live snapshot success.
                    self.log.exception("Failed to update drive information"
                                       " for '%s'", drive)
        finally:
            self.enableDriveMonitor()
            if memoryParams:
                self.cif.teardownVolumePath(memoryVol)

        # Returning quiesce to notify the manager whether the guest agent
        # froze and flushed the filesystems or not.
        quiesce = should_freeze and freezed["status"]["code"] == 0
        return {'status': doneCode, 'quiesce': quiesce}

    @api.logged(on='vdsm.api')
    def diskReplicateStart(self, srcDisk, dstDisk):
        try:
            drive = self._findDriveByUUIDs(srcDisk)
        except LookupError:
            self.log.error("Unable to find the disk for '%s'", srcDisk)
            return response.error('imageErr')

        if drive.hasVolumeLeases:
            return response.error('noimpl')

        if drive.transientDisk:
            return response.error('transientErr')

        replica = dstDisk.copy()

        replica['device'] = 'disk'
        replica['format'] = 'cow'
        replica.setdefault('cache', drive.cache)
        replica.setdefault('propagateErrors', drive.propagateErrors)

        # First mark the disk as replicated, so if we crash after the volume is
        # prepared, we clean up properly in diskReplicateFinish.
        try:
            self._setDiskReplica(drive, replica)
        except Exception:
            self.log.error("Unable to set the replication for disk '%s' with "
                           "destination '%s'", drive.name, replica)
            return response.error('replicaErr')

        try:
            replica['path'] = self.cif.prepareVolumePath(replica)
            try:
                # Add information required during replication, and persist it
                # so migration can continue after vdsm crash.
                if utils.isBlockDevice(replica['path']):
                    replica['diskType'] = DISK_TYPE.BLOCK
                else:
                    replica['diskType'] = DISK_TYPE.FILE
                self._updateDiskReplica(drive)

                self._startDriveReplication(drive)
            except Exception:
                self.cif.teardownVolumePath(replica)
                raise
        except Exception:
            self.log.exception("Unable to start replication for %s to %s",
                               drive.name, replica)
            self._delDiskReplica(drive)
            return response.error('replicaErr')

        if drive.chunked or drive.replicaChunked:
            try:
                capacity, alloc, physical = self._getExtendInfo(drive)
                self.extendDriveVolume(drive, drive.volumeID, physical,
                                       capacity)
            except Exception:
                self.log.exception("Initial extension request failed for %s",
                                   drive.name)

        return {'status': doneCode}

    @api.logged(on='vdsm.api')
    def diskReplicateFinish(self, srcDisk, dstDisk):
        try:
            drive = self._findDriveByUUIDs(srcDisk)
        except LookupError:
            self.log.error("Drive not found (srcDisk: %r)", srcDisk)
            return response.error('imageErr')

        if drive.hasVolumeLeases:
            self.log.error("Drive has volume leases, replication not "
                           "supported (drive: %r, srcDisk: %r)",
                           drive.name, srcDisk)
            return response.error('noimpl')

        if drive.transientDisk:
            self.log.error("Transient disk, replication not supported "
                           "(drive: %r, srcDisk: %r)", drive.name, srcDisk)
            return response.error('transientErr')

        if not drive.isDiskReplicationInProgress():
            self.log.error("No replication in progress (drive: %r, "
                           "srcDisk: %r)", drive.name, srcDisk)
            return response.error('replicaErr')

        # Looking for the replication blockJob info (checking its presence)
        blkJobInfo = self._dom.blockJobInfo(drive.name, 0)

        if (not isinstance(blkJobInfo, dict) or
                'cur' not in blkJobInfo or 'end' not in blkJobInfo):
            self.log.error("Replication job not found (drive: %r, "
                           "srcDisk: %r, job: %r)",
                           drive.name, srcDisk, blkJobInfo)

            # Making sure that we don't have any stale information
            self._delDiskReplica(drive)
            return response.error('replicaErr')

        # Checking if we reached the replication mode ("mirroring" in libvirt
        # and qemu terms)
        if blkJobInfo['cur'] != blkJobInfo['end']:
            self.log.error("Replication job unfinished (drive: %r, "
                           "srcDisk: %r, job: %r)",
                           drive.name, srcDisk, blkJobInfo)
            return response.error('unavail')

        dstDiskCopy = dstDisk.copy()

        # Updating the destination disk device and name, the device is used by
        # prepareVolumePath (required to fill the new information as the path)
        # and the name is used by updateDriveParameters.
        dstDiskCopy.update({'device': drive.device, 'name': drive.name})
        dstDiskCopy['path'] = self.cif.prepareVolumePath(dstDiskCopy)

        if srcDisk != dstDisk:
            self.log.debug("Stopping the disk replication switching to the "
                           "destination drive: %s", dstDisk)
            blockJobFlags = libvirt.VIR_DOMAIN_BLOCK_JOB_ABORT_PIVOT
            diskToTeardown = srcDisk

            # We need to stop monitoring drives in order to avoid spurious
            # errors from the stats threads during the switch from the old
            # drive to the new one. This applies only to the case where we
            # actually switch to the destination.
            self.disableDriveMonitor()
        else:
            self.log.debug("Stopping the disk replication remaining on the "
                           "source drive: %s", dstDisk)
            blockJobFlags = 0
            diskToTeardown = drive.diskReplicate

        try:
            # Stopping the replication
            self._dom.blockJobAbort(drive.name, blockJobFlags)
        except Exception:
            self.log.exception("Unable to stop the replication for"
                               " the drive: %s", drive.name)
            try:
                self.cif.teardownVolumePath(drive.diskReplicate)
            except Exception:
                # There is nothing we can do at this point other than logging
                self.log.exception("Unable to teardown the replication "
                                   "destination disk")
            return response.error('changeDisk')  # Finally is evaluated
        else:
            try:
                self.cif.teardownVolumePath(diskToTeardown)
            except Exception:
                # There is nothing we can do at this point other than logging
                self.log.exception("Unable to teardown the previous chain: %s",
                                   diskToTeardown)
            self.updateDriveParameters(dstDiskCopy)
        finally:
            self._delDiskReplica(drive)
            self.enableDriveMonitor()

        return {'status': doneCode}

    def _startDriveReplication(self, drive):
        destxml = vmxml.format_xml(drive.getReplicaXML())
        self.log.debug("Replicating drive %s to %s", drive.name, destxml)

        flags = (libvirt.VIR_DOMAIN_BLOCK_COPY_SHALLOW |
                 libvirt.VIR_DOMAIN_BLOCK_COPY_REUSE_EXT)

        self._dom.blockCopy(drive.name, destxml, flags=flags)

    def _setDiskReplica(self, drive, replica):
        """
        This utility method is used to set the disk replication information
        both in the live object used by vdsm and the vm configuration
        dictionary that is stored on disk (so that the information is not
        lost across restarts).
        """
        if drive.isDiskReplicationInProgress():
            raise RuntimeError("Disk '%s' already has an ongoing "
                               "replication" % drive.name)

        conf = self._findDriveConfigByName(drive.name)
        with self._confLock:
            conf['diskReplicate'] = replica
        self.saveState()

        drive.diskReplicate = replica

    def _updateDiskReplica(self, drive):
        """
        Update the persisted copy of drive replica.
        """
        if not drive.isDiskReplicationInProgress():
            raise RuntimeError("Disk '%s' does not have an ongoing "
                               "replication" % drive.name)

        conf = self._findDriveConfigByName(drive.name)
        with self._confLock:
            conf['diskReplicate'] = drive.diskReplicate
        self.saveState()

    def _delDiskReplica(self, drive):
        """
        This utility method is the inverse of _setDiskReplica, look at the
        _setDiskReplica description for more information.
        """
        del drive.diskReplicate

        conf = self._findDriveConfigByName(drive.name)
        with self._confLock:
            del conf['diskReplicate']
        self.saveState()

    def _diskSizeExtendCow(self, drive, newSizeBytes):
        try:
            # Due to an old bug in libvirt (BZ#963881) this call used to be
            # broken for NFS domains when squash_root was enabled.  This has
            # been fixed since libvirt-0.10.2-29
            curVirtualSize = self._dom.blockInfo(drive.name)[0]
        except libvirt.libvirtError:
            self.log.exception("An error occurred while getting the current "
                               "disk size")
            return response.error('resizeErr')

        if curVirtualSize > newSizeBytes:
            self.log.error(
                "Requested extension size %s for disk %s is smaller "
                "than the current size %s", newSizeBytes, drive.name,
                curVirtualSize)
            return response.error('resizeErr')

        # Uncommit the current volume size (mark as in transaction)
        self._setVolumeSize(drive.domainID, drive.poolID, drive.imageID,
                            drive.volumeID, 0)

        try:
            self._dom.blockResize(drive.name, newSizeBytes,
                                  libvirt.VIR_DOMAIN_BLOCK_RESIZE_BYTES)
        except libvirt.libvirtError:
            self.log.exception(
                "An error occurred while trying to extend the disk %s "
                "to size %s", drive.name, newSizeBytes)
            return response.error('updateDevice')
        finally:
            # Note that newVirtualSize may be larger than the requested size
            # because of rounding in qemu.
            try:
                newVirtualSize = self._dom.blockInfo(drive.name)[0]
            except libvirt.libvirtError:
                self.log.exception("An error occurred while getting the "
                                   "updated disk size")
                return response.error('resizeErr')
            self._setVolumeSize(drive.domainID, drive.poolID, drive.imageID,
                                drive.volumeID, newVirtualSize)

        return {'status': doneCode, 'size': str(newVirtualSize)}

    def _diskSizeExtendRaw(self, drive, newSizeBytes):
        # Picking up the volume size extension
        self.__refreshDriveVolume({
            'domainID': drive.domainID, 'poolID': drive.poolID,
            'imageID': drive.imageID, 'volumeID': drive.volumeID,
        })

        volSize = self._getVolumeSize(
            drive.domainID, drive.poolID, drive.imageID, drive.volumeID)

        # For the RAW device we use the volumeInfo apparentsize rather
        # than the (possibly) wrong size provided in the request.
        if volSize.apparentsize != newSizeBytes:
            self.log.info(
                "The requested extension size %s is different from "
                "the RAW device size %s", newSizeBytes, volSize.apparentsize)

        # At the moment here there's no way to fetch the previous size
        # to compare it with the new one. In the future blockInfo will
        # be able to return the value (fetched from qemu).

        try:
            self._dom.blockResize(drive.name, volSize.apparentsize,
                                  libvirt.VIR_DOMAIN_BLOCK_RESIZE_BYTES)
        except libvirt.libvirtError:
            self.log.warn(
                "Libvirt failed to notify the new size %s to the "
                "running VM, the change will be available at the ",
                "reboot", volSize.apparentsize, exc_info=True)
            return response.error('updateDevice')

        return {'status': doneCode, 'size': str(volSize.apparentsize)}

    def diskSizeExtend(self, driveSpecs, newSizeBytes):
        try:
            newSizeBytes = int(newSizeBytes)
        except ValueError:
            return response.error('resizeErr')

        try:
            drive = self._findDriveByUUIDs(driveSpecs)
        except LookupError:
            return response.error('imageErr')

        try:
            if drive.format == "cow":
                return self._diskSizeExtendCow(drive, newSizeBytes)
            else:
                return self._diskSizeExtendRaw(drive, newSizeBytes)
        except Exception:
            self.log.exception("Unable to extend disk %s to size %s",
                               drive.name, newSizeBytes)
            return response.error('updateDevice')

    def onWatchdogEvent(self, action):
        def actionToString(action):
            # the following action strings come from the comments of
            # virDomainEventWatchdogAction in include/libvirt/libvirt.h
            # of libvirt source.
            actionStrings = ("No action, watchdog ignored",
                             "Guest CPUs are paused",
                             "Guest CPUs are reset",
                             "Guest is forcibly powered off",
                             "Guest is requested to gracefully shutdown",
                             "No action, a debug message logged")

            try:
                return actionStrings[action]
            except IndexError:
                return "Received unknown watchdog action(%s)" % action

        actionEnum = ['ignore', 'pause', 'reset', 'destroy', 'shutdown', 'log']
        self._watchdogEvent["time"] = time.time()
        self._watchdogEvent["action"] = actionEnum[action]
        self.log.info("Watchdog event comes from guest %s. "
                      "Action: %s", self.name,
                      actionToString(action))

    @api.logged(on='vdsm.api')
    def changeCD(self, cdromspec):
        if isinstance(cdromspec, basestring):
            # < 4.0 - known cdrom interface/index
            drivespec = cdromspec
            if cpuarch.is_ppc(self.arch):
                blockdev = 'sda'
            else:
                blockdev = 'hdc'
            iface = None
        else:
            # > 4.0 - variable cdrom interface/index
            drivespec = cdromspec['path']
            blockdev = vmdevices.storage.makeName(
                cdromspec['iface'], cdromspec['index'])
            iface = cdromspec['iface']

        return self._changeBlockDev('cdrom', blockdev, drivespec, iface,
                                    force=bool(drivespec))

    @api.logged(on='vdsm.api')
    def changeFloppy(self, drivespec):
        return self._changeBlockDev('floppy', 'fda', drivespec)

    def _changeBlockDev(self, vmDev, blockdev, drivespec, iface=None,
                        force=True):
        try:
            path = self.cif.prepareVolumePath(drivespec)
        except VolumeError:
            return response.error('imageErr')
        diskelem = vmxml.Element('disk', type='file', device=vmDev)
        diskelem.appendChildWithArgs('source', file=path)

        target = {'dev': blockdev}
        if iface:
            target['bus'] = iface

        diskelem.appendChildWithArgs('target', **target)
        diskelem_xml = vmxml.format_xml(diskelem)

        changed = False
        if not force:
            try:
                self._dom.updateDeviceFlags(diskelem_xml)
            except libvirt.libvirtError:
                self.log.info("regular updateDeviceFlags failed")
            else:
                changed = True

        if not changed:
            try:
                self._dom.updateDeviceFlags(
                    diskelem_xml, libvirt.VIR_DOMAIN_DEVICE_MODIFY_FORCE
                )
            except libvirt.libvirtError:
                self.log.exception("forceful updateDeviceFlags failed")
                self.cif.teardownVolumePath(drivespec)
                return response.error('changeDisk')
        if vmDev in self.conf:
            self.cif.teardownVolumePath(self.conf[vmDev])

        self.conf[vmDev] = path
        return {'status': doneCode, 'vmList': self.status()}

    @api.logged(on='vdsm.api')
    def setTicket(self, otp, seconds, connAct, params):
        """
        setTicket defaults to the first graphic device.
        use updateDevice to select the device.
        """
        try:
            graphics = next(self._domain.get_device_elements('graphics'))
        except StopIteration:
            return response.error('ticketErr',
                                  'no graphics devices configured')
        return self._setTicketForGraphicDev(
            graphics, otp, seconds, connAct, None, params)

    def _setTicketForGraphicDev(self, graphics, otp, seconds, connAct,
                                disconnectAction, params):
        vmxml.set_attr(graphics, 'passwd', otp.value)
        if int(seconds) > 0:
            validto = time.strftime('%Y-%m-%dT%H:%M:%S',
                                    time.gmtime(time.time() + float(seconds)))
            vmxml.set_attr(graphics, 'passwdValidTo', validto)
        if connAct is not None and vmxml.attr(graphics, 'type') == 'spice':
            vmxml.set_attr(graphics, 'connected', connAct)
        hooks.before_vm_set_ticket(self._domain.xml, self._custom, params)
        try:
            self._dom.updateDeviceFlags(vmxml.format_xml(graphics), 0)
            self._consoleDisconnectAction = disconnectAction or \
                ConsoleDisconnectAction.LOCK_SCREEN
        except virdomain.TimeoutError as tmo:
            res = response.error('ticketErr', unicode(tmo))
        else:
            hooks.after_vm_set_ticket(self._domain.xml, self._custom, params)
            res = {'status': doneCode}
        return res

    def _reviveTicket(self, newlife):
        """
        Revive an existing ticket, if it has expired or about to expire.
        Needs to be called only if Vm.hasSpice == True
        """
        graphics = self._findGraphicsDeviceXMLByType('spice')  # cannot fail
        validto = max(time.strptime(vmxml.attr(graphics, 'passwdValidTo'),
                                    '%Y-%m-%dT%H:%M:%S'),
                      time.gmtime(time.time() + newlife))
        vmxml.set_attr(graphics, 'passwdValidTo',
                       time.strftime('%Y-%m-%dT%H:%M:%S', validto))
        vmxml.set_attr(graphics, 'connected', 'keep')
        self._dom.updateDeviceFlags(vmxml.format_xml(graphics), 0)

    def _findGraphicsDeviceXMLByType(self, deviceType):
        """
        libvirt (as in 1.2.3) supports only one graphic device per type
        """
        desc = self._dom.XMLDesc(libvirt.VIR_DOMAIN_XML_SECURE)
        for graphics in DomainDescriptor(desc).get_device_elements('graphics'):
            if vmxml.attr(graphics, 'type') == deviceType:
                return graphics
        # no graphics device configured
        return None

    def onIOError(self, blockDevAlias, err, action):
        """
        Called back by IO_ERROR_REASON event

        Old -rhev versions of QEMU provided detailed reason ('eperm', 'eio',
        'enospc', 'eother'), but they are been obsoleted and patches moved
        upstream.
        Newer QEMUs distinguish only between 'enospc' and 'anything else',
        and modern libvirts follow through reporting only two reasons:
        'enospc' or '' (empty string) for 'anything else'.
        """
        reason = err.upper() if err else "EOTHER"

        if action == libvirt.VIR_DOMAIN_EVENT_IO_ERROR_PAUSE:
            self.log.info('abnormal vm stop device %s error %s',
                          blockDevAlias, err)
            with self._confLock:
                self.conf['pauseCode'] = reason
            self._setGuestCpuRunning(False)
            self._logGuestCpuStatus('onIOError')
            if reason == 'ENOSPC':
                if not self.extendDrivesIfNeeded():
                    self.log.info("No VM drives were extended")

            self._send_ioerror_status_event(reason, blockDevAlias)

        elif action == libvirt.VIR_DOMAIN_EVENT_IO_ERROR_REPORT:
            self.log.info('I/O error %s device %s reported to guest OS',
                          reason, blockDevAlias)
        else:
            # we do not support and do not expect other values
            self.log.warning('unexpected action %i on device %s error %s',
                             action, blockDevAlias, reason)

    def _send_ioerror_status_event(self, reason, alias):
        io_error_info = {'alias': alias}
        try:
            drive = vmdevices.common.lookup_device_by_alias(
                self._devices, hwclass.DISK, alias)
        except LookupError:
            self.log.warning('unknown disk alias: %s', alias)
        else:
            io_error_info['name'] = drive.name
            io_error_info['path'] = drive.path

        self.send_status_event(pauseCode=reason, ioerror=io_error_info)

    @property
    def hasSpice(self):
        return (self.conf.get('display') == 'qxl' or
                any(dev['device'] == 'spice'
                    for dev in self.conf.get('devices', [])
                    if dev['type'] == hwclass.GRAPHICS))

    @property
    def name(self):
        return self._domain.name

    def _updateDomainDescriptor(self):
        domainXML = self._dom.XMLDesc(0)
        self._domain = DomainDescriptor(domainXML)

    def _update_metadata(self):
        with metadata.domain(self._dom, xmlconstants.METADATA_VM_VDSM_ELEMENT,
                             namespace=xmlconstants.METADATA_VM_VDSM_PREFIX,
                             namespace_uri=xmlconstants.METADATA_VM_VDSM_URI) \
                as vm:
            vm['startTime'] = self.start_time
            vm['agentChannelName'] = self._agent_channel_name
            if self._guest_agent_api_version is not None:
                vm['guestAgentAPIVersion'] = self._guest_agent_api_version
            vm['destroy_on_reboot'] = self._destroy_on_reboot

    def _ejectFloppy(self):
        if 'volatileFloppy' in self.conf:
            fileutils.rm_file(self.conf['floppy'])
        self._changeBlockDev('floppy', 'fda', '')

    def releaseVm(self, gracefulAttempts=1):
        """
        Stop VM and release all resources
        """

        # delete the payload devices
        for drive in self._devices[hwclass.DISK]:
            if (hasattr(drive, 'specParams') and
                    'vmPayload' in drive.specParams):
                supervdsm.getProxy().removeFs(drive.path)

        with self._releaseLock:
            if self._released.is_set():
                return response.success()

            # unsetting mirror network will clear both mirroring
            # (on the same network).
            for nic in self._devices[hwclass.NIC]:
                if hasattr(nic, 'portMirroring') and hasattr(nic, 'name'):
                    for network in nic.portMirroring[:]:
                        supervdsm.getProxy().unsetPortMirroring(network,
                                                                nic.name)
                        nic.portMirroring.remove(network)

            self.log.info('Release VM resources')
            # this must be done *before* self._cleanupStatsCache() to preserve
            # the invariant: if a VM is monitorable, it has a stats cache
            # entry, to avoid false positives when reporting stats too old.
            self._monitorable = False
            self.lastStatus = vmstatus.POWERING_DOWN
            # Terminate the VM's creation thread.
            self._incoming_migration_vm_running.set()
            self.guestAgent.stop()
            if self._dom.connected:
                result = self._destroyVm(gracefulAttempts)
                if response.is_error(result):
                    return result

            # Wait for any Live Merge cleanup threads.  This will only block in
            # the extremely rare case where a VM is being powered off at the
            # same time as a live merge is being finalized.  These threads
            # finish quickly unless there are storage connection issues.
            for t in self._liveMergeCleanupThreads.values():
                t.join()

            self._cleanup()

            self.cif.irs.inappropriateDevices(self.id)

            hooks.after_vm_destroy(self._domain.xml, self._custom)
            for dev in self._customDevices():
                hooks.after_device_destroy(dev._deviceXML, self._custom,
                                           dev.custom)

            self._released.set()

        return response.success()

    def _destroyVm(self, gracefulAttempts=1):
        for idx in range(gracefulAttempts):
            self.log.info("_destroyVmGraceful attempt #%i", idx)
            res, safe_to_force = self._destroyVmGraceful()
            if not response.is_error(res):
                return res

        if safe_to_force:
            res = self._destroyVmForceful()
        return res

    def _destroyVmGraceful(self):
        safe_to_force = False
        try:
            self._dom.destroyFlags(libvirt.VIR_DOMAIN_DESTROY_GRACEFUL)
        except libvirt.libvirtError as e:
            # after successful migrations
            if (self.lastStatus == vmstatus.DOWN and
                    e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN):
                self.log.info("VM '%s' already down and destroyed", self.id)
            elif (self.lastStatus == vmstatus.DOWN and
                  e.get_error_code() == libvirt.VIR_ERR_OPERATION_INVALID):
                self.log.warning(
                    "VM '%s' couldn't be destroyed in libvirt: %s", self.id, e)
            else:
                self.log.warning(
                    "Failed to destroy VM '%s' gracefully (error=%i)",
                    self.id, e.get_error_code())
                if e.get_error_code() in (libvirt.VIR_ERR_OPERATION_FAILED,
                                          libvirt.VIR_ERR_SYSTEM_ERROR,):
                    safe_to_force = True
                return response.error('destroyErr'), safe_to_force
        return response.success(), safe_to_force

    def _destroyVmForceful(self):
        try:
            self._dom.destroy()
        except libvirt.libvirtError as e:
            self.log.warning(
                "Failed to destroy VM '%s' forcefully (error=%i)",
                self.id, e.get_error_code())
            return response.error('destroyErr')
        return response.success()

    def _deleteVm(self):
        """
        Clean VM from the system
        """
        try:
            del self.cif.vmContainer[self.id]
        except KeyError:
            self.log.exception("Failed to delete VM %s", self.id)
        else:
            self._cleanupRecoveryFile()
            self.log.debug("Total desktops after destroy of %s is %d",
                           self.id, len(self.cif.vmContainer))

    @api.logged(on='vdsm.api')
    def destroy(self, gracefulAttempts=1):
        self.log.debug('destroy Called')

        result = self.doDestroy(gracefulAttempts)
        if response.is_error(result):
            return result
        # Clean VM from the system
        self._deleteVm()

        return response.success()

    def doDestroy(self, gracefulAttempts=1,
                  reason=vmexitreason.ADMIN_SHUTDOWN):
        for dev in self._customDevices():
            hooks.before_device_destroy(dev._deviceXML, self._custom,
                                        dev.custom)

        hooks.before_vm_destroy(self._domain.xml, self._custom)
        with self._shutdownLock:
            self._shutdownReason = reason
        self._destroy_requested.set()

        return self.releaseVm(gracefulAttempts)

    def acpi_enabled(self):
        return self._domain.acpi_enabled()

    def acpiShutdown(self):
        with self._shutdownLock:
            self._shutdownReason = vmexitreason.ADMIN_SHUTDOWN
        try:
            self._dom.shutdownFlags(libvirt.VIR_DOMAIN_SHUTDOWN_ACPI_POWER_BTN)
        except virdomain.NotConnectedError:
            # the VM was already shut off asynchronously,
            # so ignore error and quickly exit
            self.log.warning('failed to invoke acpiShutdown: '
                             'domain not connected')
            return response.error('down')
        else:
            return response.success()

    def acpiReboot(self):
        try:
            self._dom.reboot(libvirt.VIR_DOMAIN_REBOOT_ACPI_POWER_BTN)
        except virdomain.NotConnectedError:
            # the VM was already shut off asynchronously,
            # so ignore error and quickly exit
            self.log.warning('failed to invoke acpiReboot: '
                             'domain not connected')
            return response.error('down')
        else:
            return response.success()

    @api.logged(on='vdsm.api')
    def setBalloonTarget(self, target):

        if not self._dom.connected:
            raise exception.BalloonError()
        try:
            target = int(target)
            self._dom.setMemory(target)
        except ValueError:
            raise exception.BalloonError('an integer is required for target')
        except libvirt.libvirtError as e:
            if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                raise exception.NoSuchVM()
            raise exception.BalloonError(e.message)
        else:
            # TODO: update metadata once we build devices with engine XML

            for dev in self.conf['devices']:
                if dev['type'] == hwclass.BALLOON and \
                        dev['specParams']['model'] != 'none':
                    dev['target'] = target
            # persist the target value to make it consistent after recovery
            self.saveState()

            self._devices[hwclass.BALLOON][0].target = target

    def get_balloon_info(self):
        # we will always have exactly one memballoon device
        dev = self._devices[hwclass.BALLOON][0]
        return {
            'target': dev.target,
            'minimum': dev.minimum,
        }

    @api.logged(on='vdsm.api')
    def setCpuTuneQuota(self, quota):
        try:
            self._dom.setSchedulerParameters({'vcpu_quota': int(quota)})
        except ValueError:
            return response.error('cpuTuneErr',
                                  'an integer is required for period')
        except libvirt.libvirtError as e:
            return self._reportException(key='cpuTuneErr', msg=e.message)
        else:
            # libvirt may change the value we set, so we must get fresh data
            return self._updateVcpuTuneInfo()

    @api.logged(on='vdsm.api')
    def setCpuTunePeriod(self, period):
        try:
            self._dom.setSchedulerParameters({'vcpu_period': int(period)})
        except ValueError:
            return response.error('cpuTuneErr',
                                  'an integer is required for period')
        except libvirt.libvirtError as e:
            return self._reportException(key='cpuTuneErr', msg=e.message)
        else:
            # libvirt may change the value we set, so we must get fresh data
            return self._updateVcpuTuneInfo()

    def _updateVcpuTuneInfo(self):
        try:
            self._vcpuTuneInfo = self._dom.schedulerParameters()
        except libvirt.libvirtError as e:
            return self._reportException(key='cpuTuneErr', msg=e.message)
        else:
            return {'status': doneCode}

    def _reportException(self, key, msg=None):
        """
        Convert an exception to an error status.
        This method should be called only within exception-handling context.
        """
        self.log.exception("Operation failed")
        return response.error(key, msg)

    def _setWriteWatermarks(self):
        """
        Define when to receive an event about high write to guest image
        Currently unavailable by libvirt.
        """
        pass

    def handle_failed_post_copy(self, clean_vm=False):
        # After a failed post-copy migration, the VM remains in a paused state
        # on both the ends of the migration. There is currently no way to
        # recover it, since the VM is missing some memory pages on the
        # destination and the old snapshot at the source doesn't know about the
        # changes made to the external world (network, storage, ...) during the
        # post-copy phase. The best what we can do in such a situation is to
        # destroy the paused VM instances on both the ends before someone tries
        # to resume any of them, causing confusion at best or more damages in
        # the worse case. We must also inform Engine about the fatal state of
        # the failed migration, so we can't destroy the VM immediately on the
        # destination (but we can do it on the source). We report the VM as
        # down on the destination to Engine and wait for destroy request from
        # it.
        self.log.warning("Migration failed in post-copy, "
                         "the VM will be destroyed")
        self.setDownStatus(ERROR,
                           vmexitreason.POSTCOPY_MIGRATION_FAILED)
        if clean_vm:
            self.destroy()
        else:
            self.doDestroy(1)

    def onLibvirtLifecycleEvent(self, event, detail, opaque):
        self.log.debug('event %s detail %s opaque %s',
                       eventToString(event), detail, opaque)
        if event == libvirt.VIR_DOMAIN_EVENT_STOPPED:
            if (detail == libvirt.VIR_DOMAIN_EVENT_STOPPED_MIGRATED and
                    self.lastStatus == vmstatus.MIGRATION_SOURCE):
                try:
                    hooks.after_vm_migrate_source(
                        self._domain.xml, self._custom)
                    for dev in self._customDevices():
                        hooks.after_device_migrate_source(
                            dev._deviceXML, self._custom, dev.custom)
                finally:
                    self.stopped_migrated_event_processed.set()
            elif (detail == libvirt.VIR_DOMAIN_EVENT_STOPPED_SAVED and
                    self.lastStatus == vmstatus.SAVING_STATE):
                hooks.after_vm_hibernate(self._domain.xml, self._custom)
            else:
                exit_code, reason = self._getShutdownReason(
                    detail == libvirt.VIR_DOMAIN_EVENT_STOPPED_SHUTDOWN)
                self._onQemuDeath(exit_code, reason)
        elif event == libvirt.VIR_DOMAIN_EVENT_SUSPENDED:
            self._setGuestCpuRunning(False)
            self._logGuestCpuStatus('onSuspend')
            if detail == libvirt.VIR_DOMAIN_EVENT_SUSPENDED_PAUSED:
                # Libvirt sometimes send the SUSPENDED/SUSPENDED_PAUSED event
                # after RESUMED/RESUMED_MIGRATED (when VM status is PAUSED
                # when migration completes, see qemuMigrationFinish function).
                # In this case self._dom is disconnected because the function
                # _completeIncomingMigration didn't update it yet.
                try:
                    domxml = self._dom.XMLDesc(0)
                except virdomain.NotConnectedError:
                    pass
                else:
                    hooks.after_vm_pause(domxml, self._custom)
            elif detail == libvirt.VIR_DOMAIN_EVENT_SUSPENDED_POSTCOPY:
                self._post_copy = migration.PostCopyPhase.RUNNING
                self.log.debug("Migration entered post-copy mode")
                with self._confLock:
                    self.conf['pauseCode'] = 'POSTCOPY'
                self.send_status_event(pauseCode='POSTCOPY')
            elif detail == libvirt.VIR_DOMAIN_EVENT_SUSPENDED_POSTCOPY_FAILED:
                # This event may be received only on the destination.
                self.handle_failed_post_copy()

        elif event == libvirt.VIR_DOMAIN_EVENT_RESUMED:
            self._setGuestCpuRunning(True)
            self._logGuestCpuStatus('onResume')
            if detail == libvirt.VIR_DOMAIN_EVENT_RESUMED_UNPAUSED:
                # This is not a real solution however the safest way to handle
                # this for now. Ultimately we need to change the way how we are
                # creating self._dom.
                # The event handler delivers the domain instance in the
                # callback however we do not use it.
                try:
                    domxml = self._dom.XMLDesc(0)
                except virdomain.NotConnectedError:
                    pass
                else:
                    hooks.after_vm_cont(domxml, self._custom)
            elif detail == libvirt.VIR_DOMAIN_EVENT_RESUMED_MIGRATED:
                if self.lastStatus == vmstatus.MIGRATION_DESTINATION:
                    self._incoming_migration_vm_running.set()
                    self._incomingMigrationFinished.set()
                elif self.lastStatus == vmstatus.MIGRATION_SOURCE:
                    # Failed migration on the source.  This is normally handled
                    # within the source thread after the migrateToURI3 call
                    # finishes.  But if the VM was migrating during recovery,
                    # there is source thread running and there is no
                    # migrateToURI3 call to wait for migration completion.
                    # So we must tell the source thread to check for this
                    # situation and perform migration cleanup if necessary
                    # (most notably setting the VM status to UP).
                    self._migrationSourceThread.recovery_cleanup()
            elif (self.lastStatus == vmstatus.MIGRATION_DESTINATION and
                  detail == libvirt.VIR_DOMAIN_EVENT_RESUMED_POSTCOPY):
                # When we enter post-copy mode, the VM starts actually
                # running on the destination, so we should unblock the
                # start up processing here.  The only exception is status,
                # which must still signal incoming migration to not confuse
                # Engine.
                self._incoming_migration_vm_running.set()
                self.log.info("Migration switched to post-copy mode")

    def _updateDevicesDomxmlCache(self, xml):
        """
            Devices cache their device's XML, which is used for per-device
            hooks. The cache is lost when a VM migrates because that info
            isn't sent, and so the cache needs to be updated at the
            destination.
            We update the cache by finding each device in the dom xml.
        """

        aliasToDevice = {}
        for devType in self._devices:
            for dev in self._devices[devType]:
                if hasattr(dev, 'alias'):
                    aliasToDevice[dev.alias] = dev
                elif devType == hwclass.WITHOUT_ALIAS:
                    # we expect these failures, we don't log
                    # to not confuse the user
                    pass
                else:
                    self.log.error("Alias not found for device type %s "
                                   "during migration at destination host" %
                                   devType)

        for deviceXML in vmxml.children(DomainDescriptor(xml).devices):
            alias = vmdevices.core.find_device_alias(deviceXML)
            if alias in aliasToDevice:
                aliasToDevice[alias]._deviceXML = vmxml.format_xml(deviceXML)
            elif vmxml.tag(deviceXML) == hwclass.GRAPHICS:
                # graphics device do not have aliases, must match by type
                graphicsType = vmxml.attr(deviceXML, 'type')
                for devObj in self._devices[hwclass.GRAPHICS]:
                    if devObj.device == graphicsType:
                        devObj._deviceXML = vmxml.format_xml(deviceXML)

    def waitForMigrationDestinationPrepare(self):
        """Wait until paths are prepared for migration destination"""
        # Wait for the VM to start its creation. There is no reason to start
        # the timed waiting for path preparation before the work has started.
        self.log.debug('migration destination: waiting for VM creation')
        self._vmCreationEvent.wait()
        prepareTimeout = self._loadCorrectedTimeout(
            config.getint('vars', 'migration_listener_timeout'), doubler=5)
        self.log.debug('migration destination: waiting %ss '
                       'for path preparation', prepareTimeout)
        self._pathsPreparedEvent.wait(prepareTimeout)
        if not self._pathsPreparedEvent.isSet():
            self.log.debug('Timeout while waiting for path preparation')
            return False
        srcDomXML = self._src_domain_xml
        self._updateDevicesDomxmlCache(srcDomXML)

        for dev in self._customDevices():
            hooks.before_device_migrate_destination(
                dev._deviceXML, self._custom, dev.custom)

        if self.hugepages:
            self._prepare_hugepages()

        hooks.before_vm_migrate_destination(srcDomXML, self._custom)
        return True

    def getBlockJob(self, drive):
        for job in self.conf['_blockJobs'].values():
            if all([bool(drive[x] == job['disk'][x])
                    for x in ('imageID', 'domainID', 'volumeID')]):
                return job
        raise LookupError("No block job found for drive '%s'", drive.name)

    def trackBlockJob(self, jobID, drive, base, top, strategy):
        driveSpec = dict((k, drive[k]) for k in
                         ('poolID', 'domainID', 'imageID', 'volumeID'))
        with self._confLock:
            try:
                job = self.getBlockJob(drive)
            except LookupError:
                newJob = {'jobID': jobID, 'disk': driveSpec,
                          'baseVolume': base, 'topVolume': top,
                          'strategy': strategy, 'blockJobType': 'commit'}
                self.conf['_blockJobs'][jobID] = newJob
            else:
                self.log.error("Cannot add block job %s.  A block job with id "
                               "%s already exists for image %s", jobID,
                               job['jobID'], drive['imageID'])
                raise BlockJobExistsError()
        self.saveState()

    def untrackBlockJob(self, jobID):
        with self._confLock:
            try:
                del self.conf['_blockJobs'][jobID]
            except KeyError:
                # If there was contention on the confLock, this may have
                # already been removed
                return False
        self.saveState()
        return True

    def _activeLayerCommitReady(self, jobInfo, drive):
        try:
            pivot = libvirt.VIR_DOMAIN_BLOCK_JOB_TYPE_ACTIVE_COMMIT
        except AttributeError:
            return False
        if (jobInfo['cur'] == jobInfo['end'] and jobInfo['type'] == pivot):

            # Check the job state in the xml to make sure the job is
            # ready. We know about two interesting corner cases:
            #
            # - cur == 0 and end == 0 when a job starts. Trying to pivot
            #   succeeds, but the xml never updates after that.
            #   See https://bugzilla.redhat.com/1442266.
            #
            # - cur == end and cur != 0, but the job is not ready yet, and
            #   blockJobAbort raises an error.
            #   See https://bugzilla.redhat.com/1376580

            self.log.debug("Checking xml for drive %r", drive.name)
            root = ET.fromstring(self._dom.XMLDesc(0))
            disk_xpath = "./devices/disk/target[@dev='%s'].." % drive.name
            disk = root.find(disk_xpath)
            if disk is None:
                self.log.warning("Unable to find %r in vm xml", drive)
                return False
            return disk.find("./mirror[@ready='yes']") is not None
        return False

    @property
    def hasVmJobs(self):
        """
        Return True if there are VM jobs to monitor
        """
        with self._jobsLock:
            # we always do a full check the first time we run.
            # This may be wasteful on normal flow,
            # but covers pretty nicely the recovering flow.
            return self._vmJobs is None or bool(self.conf['_blockJobs'])

    def updateVmJobs(self):
        self._vmJobs = self.queryBlockJobs()

    def queryBlockJobs(self):
        def startCleanup(job, drive, needPivot):
            t = LiveMergeCleanupThread(self, job, drive, needPivot)
            t.start()
            self._liveMergeCleanupThreads[job['jobID']] = t

        jobsRet = {}
        # We need to take the jobs lock here to ensure that we don't race with
        # another call to merge() where the job has been recorded but not yet
        # started.
        with self._jobsLock:
            for storedJob in self.conf['_blockJobs'].values():
                jobID = storedJob['jobID']
                self.log.debug("Checking job %s", jobID)
                cleanThread = self._liveMergeCleanupThreads.get(jobID)
                if cleanThread and cleanThread.isSuccessful():
                    # Handle successful jobs early because the job just needs
                    # to be untracked and the stored disk info might be stale
                    # anyway (ie. after active layer commit).
                    self.log.info("Cleanup thread %s successfully completed, "
                                  "untracking job %s (base=%s, top=%s)",
                                  cleanThread, jobID,
                                  storedJob["baseVolume"],
                                  storedJob["topVolume"])
                    self.untrackBlockJob(jobID)
                    continue

                drive = self._findDriveByUUIDs(storedJob['disk'])
                entry = {'id': jobID, 'jobType': 'block',
                         'blockJobType': storedJob['blockJobType'],
                         'bandwidth': 0, 'cur': '0', 'end': '0',
                         'imgUUID': storedJob['disk']['imageID']}

                liveInfo = None
                if 'gone' not in storedJob:
                    try:
                        liveInfo = self._dom.blockJobInfo(drive.name, 0)
                    except libvirt.libvirtError:
                        self.log.exception("Error getting block job info")
                        jobsRet[jobID] = entry
                        continue

                if liveInfo:
                    self.log.debug("Job %s live info: %s", jobID, liveInfo)
                    entry['bandwidth'] = liveInfo['bandwidth']
                    entry['cur'] = str(liveInfo['cur'])
                    entry['end'] = str(liveInfo['end'])
                    doPivot = self._activeLayerCommitReady(liveInfo, drive)
                else:
                    # Libvirt has stopped reporting this job so we know it will
                    # never report it again.
                    if 'gone' not in storedJob:
                        self.log.info("Libvirt job %s was terminated", jobID)
                    storedJob['gone'] = True
                    doPivot = False
                if not liveInfo or doPivot:
                    if not cleanThread:
                        # There is no cleanup thread so the job must have just
                        # ended.  Spawn an async cleanup.
                        self.log.info("Starting cleanup thread for job: %s",
                                      jobID)
                        startCleanup(storedJob, drive, doPivot)
                    elif cleanThread.isAlive():
                        # Let previously started cleanup thread continue
                        self.log.debug("Still waiting for block job %s to be "
                                       "synchronized", jobID)
                    elif not cleanThread.isSuccessful():
                        # At this point we know the thread is not alive and the
                        # cleanup failed.  Retry it with a new thread.
                        self.log.info("Previous job %s cleanup thread failed, "
                                      "retrying", jobID)
                        startCleanup(storedJob, drive, doPivot)
                jobsRet[jobID] = entry
        return jobsRet

    @api.logged(on='vdsm.api')
    def merge(self, driveSpec, baseVolUUID, topVolUUID, bandwidth, jobUUID):
        if not caps.getLiveMergeSupport():
            self.log.error("Live merge is not supported on this host")
            return response.error('mergeErr')

        bandwidth = int(bandwidth)
        if jobUUID is None:
            jobUUID = str(uuid.uuid4())

        try:
            drive = self._findDriveByUUIDs(driveSpec)
        except LookupError:
            return response.error('imageErr')

        # Check that libvirt exposes full volume chain information
        chains = self._driveGetActualVolumeChain([drive])
        if drive['alias'] not in chains:
            self.log.error("merge: libvirt does not support volume chain "
                           "monitoring.  Unable to perform live merge.")
            return response.error('mergeErr')

        actual_chain = chains[drive['alias']]

        try:
            base_target = drive.volume_target(baseVolUUID, actual_chain)
            top_target = drive.volume_target(topVolUUID, actual_chain)
        except VolumeNotFound as e:
            self.log.error("merge: %s", e)
            return response.error('mergeErr')

        try:
            baseInfo = self._getVolumeInfo(drive.domainID, drive.poolID,
                                           drive.imageID, baseVolUUID)
            topInfo = self._getVolumeInfo(drive.domainID, drive.poolID,
                                          drive.imageID, topVolUUID)
        except StorageUnavailableError:
            self.log.error("Unable to get volume information")
            return errCode['mergeErr']

        # If base is a shared volume then we cannot allow a merge.  Otherwise
        # We'd corrupt the shared volume for other users.
        if baseInfo['voltype'] == 'SHARED':
            self.log.error("Refusing to merge into a shared volume")
            return errCode['mergeErr']

        # Indicate that we expect libvirt to maintain the relative paths of
        # backing files.  This is necessary to ensure that a volume chain is
        # visible from any host even if the mountpoint is different.
        flags = libvirt.VIR_DOMAIN_BLOCK_COMMIT_RELATIVE

        if topVolUUID == drive.volumeID:
            # Pass a flag to libvirt to indicate that we expect a two phase
            # block job.  In the first phase, data is copied to base.  Once
            # completed, an event is raised to indicate that the job has
            # transitioned to the second phase.  We must then tell libvirt to
            # pivot to the new active layer (baseVolUUID).
            flags |= libvirt.VIR_DOMAIN_BLOCK_COMMIT_ACTIVE

        # Make sure we can merge into the base in case the drive was enlarged.
        if not self._can_merge_into(drive, baseInfo, topInfo):
            return errCode['destVolumeTooSmall']

        # If the base volume format is RAW and its size is smaller than its
        # capacity (this could happen because the engine extended the base
        # volume), we have to refresh the volume to cause lvm to get current lv
        # size from storage, and update the kernel so the lv reflects the real
        # size on storage. Not refreshing the volume may fail live merge.
        # This could happen if disk extended after taking a snapshot but before
        # performing the live merge.  See https://bugzilla.redhat.com/1367281
        if (drive.chunked and
                baseInfo['format'] == 'RAW' and
                int(baseInfo['apparentsize']) < int(baseInfo['capacity'])):
            self.log.info("Refreshing raw volume %r (apparentsize=%s, "
                          "capacity=%s)",
                          baseVolUUID, baseInfo['apparentsize'],
                          baseInfo['capacity'])
            self.__refreshDriveVolume({
                'domainID': drive.domainID, 'poolID': drive.poolID,
                'imageID': drive.imageID, 'volumeID': baseVolUUID,
            })

        # Take the jobs lock here to protect the new job we are tracking from
        # being cleaned up by queryBlockJobs() since it won't exist right away
        with self._jobsLock:
            try:
                self.trackBlockJob(jobUUID, drive, baseVolUUID, topVolUUID,
                                   'commit')
            except BlockJobExistsError:
                self.log.error("A block job is already active on this disk")
                return response.error('mergeErr')

            orig_chain = [entry.uuid for entry in chains[drive['alias']]]
            chain_str = volume_chain_to_str(orig_chain)
            self.log.info("Starting merge with jobUUID=%r, original chain=%s, "
                          "disk=%r, base=%r, top=%r, bandwidth=%d, flags=%d",
                          jobUUID, chain_str, drive.name, base_target,
                          top_target, bandwidth, flags)

            try:
                self._dom.blockCommit(drive.name, base_target, top_target,
                                      bandwidth, flags)
            except libvirt.libvirtError:
                self.log.exception("Live merge failed (job: %s)", jobUUID)
                self.untrackBlockJob(jobUUID)
                return response.error('mergeErr')

        # blockCommit will cause data to be written into the base volume.
        # Perform an initial extension to ensure there is enough space to
        # copy all the required data.  Normally we'd use monitoring to extend
        # the volume on-demand but internal watermark information is not being
        # reported by libvirt so we must do the full extension up front.  In
        # the worst case, the allocated size of 'base' should be increased by
        # the allocated size of 'top' plus one additional chunk to accomodate
        # additional writes to 'top' during the live merge operation.
        if drive.chunked and baseInfo['format'] == 'COW':
            capacity, alloc, physical = self._getExtendInfo(drive)
            baseSize = int(baseInfo['apparentsize'])
            topSize = int(topInfo['apparentsize'])
            maxAlloc = baseSize + topSize
            self.extendDriveVolume(drive, baseVolUUID, maxAlloc, capacity)

        # Trigger the collection of stats before returning so that callers
        # of getVmStats after this returns will see the new job
        self.updateVmJobs()

        return {'status': doneCode}

    def _can_merge_into(self, drive, base_info, top_info):
        # If the drive was resized the top volume could be larger than the
        # base volume.  Libvirt can handle this situation for file-based
        # volumes and block qcow volumes (where extension happens dynamically).
        # Raw block volumes cannot be extended by libvirt so we require ovirt
        # engine to extend them before calling merge.  Check here.
        if not drive.blockDev or base_info['format'] != 'RAW':
            return True

        if int(base_info['capacity']) < int(top_info['capacity']):
            self.log.warning("The base volume is undersized and cannot be "
                             "extended (base capacity: %s, top capacity: %s)",
                             base_info['capacity'], top_info['capacity'])
            return False
        return True

    def _driveGetActualVolumeChain(self, drives):
        def lookupDeviceXMLByAlias(domXML, targetAlias):
            for deviceXML in vmxml.children(DomainDescriptor(domXML).devices):
                alias = vmdevices.core.find_device_alias(deviceXML)
                if alias and alias == targetAlias:
                    return deviceXML
            raise LookupError("Unable to find matching XML for device %s",
                              targetAlias)

        ret = {}
        self._updateDomainDescriptor()
        for drive in drives:
            alias = drive['alias']
            diskXML = lookupDeviceXMLByAlias(self._domain.xml, alias)
            volChain = drive.parse_volume_chain(diskXML)
            if volChain:
                ret[alias] = volChain
        return ret

    def _syncVolumeChain(self, drive):
        def getVolumeInfo(device, volumeID):
            for info in device['volumeChain']:
                if info['volumeID'] == volumeID:
                    return utils.picklecopy(info)

        if not isVdsmImage(drive):
            self.log.debug("Skipping drive '%s' which is not a vdsm image",
                           drive.name)
            return

        curVols = [x['volumeID'] for x in drive.volumeChain]
        chains = self._driveGetActualVolumeChain([drive])
        try:
            chain = chains[drive['alias']]
        except KeyError:
            self.log.debug("Unable to determine volume chain. Skipping volume "
                           "chain synchronization for drive %s", drive.name)
            return

        volumes = [entry.uuid for entry in chain]
        activePath = chain[-1].path
        self.log.debug("vdsm chain: %s, libvirt chain: %s", curVols, volumes)

        # Ask the storage to sync metadata according to the new chain
        res = self.cif.irs.imageSyncVolumeChain(drive.domainID, drive.imageID,
                                                drive['volumeID'], volumes)
        if res['status']['code'] != 0:
            self.log.error("Unable to synchronize volume chain to storage")
            raise StorageUnavailableError()

        if (set(curVols) == set(volumes)):
            return

        volumeID = volumes[-1]
        res = self.cif.irs.getVolumeInfo(drive.domainID, drive.poolID,
                                         drive.imageID, volumeID)
        if res['status']['code'] != 0:
            self.log.error("Unable to get info of volume %s (domain: %s image:"
                           " %s)", volumeID, drive.domainID, drive.imageID)
            raise RuntimeError("Unable to get volume info")
        driveFormat = res['info']['format'].lower()

        # Sync this VM's data strctures.  Ugh, we're storing the same info in
        # two places so we need to change it twice.
        device = self._lookupConfByPath(drive['path'])
        if drive.volumeID != volumeID:
            # If the active layer changed:
            #  Update the disk path, volumeID, volumeInfo, and format members
            volInfo = getVolumeInfo(device, volumeID)

            # Path must be set with the value being used by libvirt
            device['path'] = drive.path = volInfo['path'] = activePath
            device['format'] = drive.format = driveFormat
            device['volumeID'] = drive.volumeID = volumeID
            device['volumeInfo'] = drive.volumeInfo = volInfo
            for v in device['volumeChain']:
                if v['volumeID'] == volumeID:
                    v['path'] = activePath

        # Remove any components of the volumeChain which are no longer present
        newChain = [x for x in device['volumeChain']
                    if x['volumeID'] in volumes]
        device['volumeChain'] = drive.volumeChain = newChain

    def _fixLegacyRngConf(self):
        def _is_legacy_rng_device_conf(dev):
            """
            Returns True if dev is a legacy (3.5) RNG device conf,
            False otherwise.
            """
            return dev['type'] == hwclass.RNG and (
                'specParams' not in dev or
                'source' not in dev['specParams']
            )

        with self._confLock:
            self._devices[hwclass.RNG] = [dev for dev
                                          in self._devices[hwclass.RNG]
                                          if 'source' in dev.specParams]
            self.conf['devices'] = [dev for dev
                                    in self.conf['devices']
                                    if not _is_legacy_rng_device_conf(dev)]

    def getDiskDevices(self):
        return self._devices[hwclass.DISK]

    def getNicDevices(self):
        return self._devices[hwclass.NIC]

    @property
    def sdIds(self):
        """
        Returns a list of the ids of the storage domains in use by the VM.
        """
        return set(device.domainID
                   for device in self._devices[hwclass.DISK]
                   if device['device'] == 'disk' and isVdsmImage(device))

    def _logGuestCpuStatus(self, reason):
        self.log.info('CPU %s: %s',
                      'running' if self._guestCpuRunning else 'stopped',
                      reason)

    def _setUnresponsiveIfTimeout(self, stats, stats_age):
        if self.isMigrating():
            return
        # we don't care about decimals here
        if stats_age < config.getint('vars', 'vm_command_timeout'):
            return
        if stats['monitorResponse'] == '-1':
            return
        # TODO: remove once we have real monitoring for containers
        if not is_kvm(self._custom):
            return

        self.log.warning('monitor became unresponsive'
                         ' (command timeout, age=%s)',
                         stats_age)
        stats['monitorResponse'] = '-1'

    def onDeviceRemoved(self, device_alias):
        self.log.info("Device removal reported: %s", device_alias)

        # We currently hotunplug all devices synchronously, except for memory.
        device_hwclass = hwclass.MEMORY

        try:
            conf = vmdevices.common.lookup_conf_by_alias(
                self.conf['devices'], device_hwclass, device_alias)
        except LookupError:
            self.log.warning("Removed device not found in conf: %s",
                             device_alias)
        else:
            with self._confLock:
                self.conf['devices'].remove(conf)

        try:
            device = vmdevices.common.lookup_device_by_alias(
                self._devices, device_hwclass, device_alias)
        except LookupError:
            self.log.warning("Removed device not found in devices: %s",
                             device_alias)
            return
        self._devices[device_hwclass].remove(device)

        self.saveState()
        device.teardown()

        # TODO: Remove the following domain descriptor update once
        # https://bugzilla.redhat.com/1414393 is fixed. Domain descriptor is
        # already updated in saveState call above. But due to the bug the
        # device may still be present in the domain XML and get removed only
        # shortly afterwards. So let's try once again if needed. If the device
        # is still present in the domain XML after the additional update (it's
        # probably not that much likely) we don't care much. This callback
        # currently handles only memory devices and the main purpose of the
        # update is to get the current memory size. But Engine doesn't use
        # that value, we just log it and expose it in the stats.
        xpath = ".//alias[@name='%s']" % (device_alias,)
        if self._domain.devices.find(xpath) is not None:
            self._updateDomainDescriptor()

    # Accessing storage

    def _getVolumeSize(self, domainID, poolID, imageID, volumeID):
        """ Return volume size info by accessing storage """
        res = self.cif.irs.getVolumeSize(domainID, poolID, imageID, volumeID)
        if res['status']['code'] != 0:
            raise StorageUnavailableError(
                "Unable to get volume size for domain %s volume %s" %
                (domainID, volumeID))
        return VolumeSize(int(res['apparentsize']), int(res['truesize']))

    def _getVolumeInfo(self, domainID, poolID, imageID, volumeID):
        res = self.cif.irs.getVolumeInfo(domainID, poolID, imageID, volumeID)
        if res['status']['code'] != 0:
            raise StorageUnavailableError(
                "Unable to get volume info for domain %s volume %s" %
                (domainID, volumeID))
        return res['info']

    def _setVolumeSize(self, domainID, poolID, imageID, volumeID, size):
        res = self.cif.irs.setVolumeSize(domainID, poolID, imageID, volumeID,
                                         size)
        if res['status']['code'] != 0:
            raise StorageUnavailableError(
                "Unable to set volume size to %s for domain %s volume %s" %
                (size, domainID, volumeID))


class LiveMergeCleanupThread(object):
    def __init__(self, vm, job, drive, doPivot):
        self.vm = vm
        self.job = job
        self.drive = drive
        self.doPivot = doPivot
        self.success = False
        self._thread = concurrent.thread(self.run, name="merge/" + vm.id[:8])

    def start(self):
        self._thread.start()

    def join(self):
        self._thread.join()

    def isAlive(self):
        return self._thread.is_alive()

    def tryPivot(self):
        # We call imageSyncVolumeChain which will mark the current leaf
        # ILLEGAL.  We do this before requesting a pivot so that we can
        # properly recover the VM in case we crash.  At this point the
        # active layer contains the same data as its parent so the ILLEGAL
        # flag indicates that the VM should be restarted using the parent.
        newVols = [vol['volumeID'] for vol in self.drive.volumeChain
                   if vol['volumeID'] != self.drive.volumeID]
        self.vm.cif.irs.imageSyncVolumeChain(self.drive.domainID,
                                             self.drive.imageID,
                                             self.drive['volumeID'], newVols)

        # A pivot changes the top volume being used for the VM Disk.  Until
        # we can correct our metadata following the pivot we should not
        # attempt to monitor drives.
        # TODO: Stop monitoring only for the live merge disk
        self.vm.disableDriveMonitor()

        self.vm.log.info("Requesting pivot to complete active layer commit "
                         "(job %s)", self.job['jobID'])
        try:
            flags = libvirt.VIR_DOMAIN_BLOCK_JOB_ABORT_PIVOT
            self.vm._dom.blockJobAbort(self.drive.name, flags)
        except libvirt.libvirtError as e:
            self.vm.enableDriveMonitor()
            if e.get_error_code() != libvirt.VIR_ERR_BLOCK_COPY_ACTIVE:
                raise
            raise BlockCopyActiveError(self.job['jobID'])
        except:
            self.vm.enableDriveMonitor()
            raise

        self._waitForXMLUpdate()
        self.vm.log.info("Pivot completed (job %s)", self.job['jobID'])

    def update_base_size(self):
        # If the drive size was extended just after creating the snapshot which
        # we are removing, the size of the top volume might be larger than the
        # size of the base volume.  In that case libvirt has enlarged the base
        # volume automatically as part of the blockCommit operation.  Update
        # our metadata to reflect this change.
        topVolUUID = self.job['topVolume']
        baseVolUUID = self.job['baseVolume']
        topVolInfo = self.vm._getVolumeInfo(self.drive.domainID,
                                            self.drive.poolID,
                                            self.drive.imageID, topVolUUID)
        self.vm._setVolumeSize(self.drive.domainID, self.drive.poolID,
                               self.drive.imageID, baseVolUUID,
                               topVolInfo['capacity'])

    def teardown_top_volume(self):
        # TODO move this method to storage public API
        sd_manifest = sdc.sdCache.produce_manifest(self.drive.domainID)
        sd_manifest.teardownVolume(self.drive.imageID,
                                   self.job['topVolume'])

    @logutils.traceback()
    def run(self):
        self.update_base_size()
        if self.doPivot:
            try:
                self.tryPivot()
            except BlockCopyActiveError as e:
                self.vm.log.warning("Pivot failed: %s", e)
                return

        self.vm.log.info("Synchronizing volume chain after live merge "
                         "(job %s)", self.job['jobID'])
        self.vm._syncVolumeChain(self.drive)
        if self.doPivot:
            self.vm.enableDriveMonitor()
        self.teardown_top_volume()
        self.success = True
        self.vm.log.info("Synchronization completed (job %s)",
                         self.job['jobID'])

    def isSuccessful(self):
        """
        Returns True if this phase completed successfully.
        """
        return self.success

    def _waitForXMLUpdate(self):
        # Libvirt version 1.2.8-16.el7_1.2 introduced a bug where the
        # synchronous call to blockJobAbort will return before the domain XML
        # has been updated.  This makes it look like the pivot failed when it
        # actually succeeded.  This means that vdsm state will not be properly
        # synchronized and we may start the vm with a stale volume in the
        # future.  See https://bugzilla.redhat.com/show_bug.cgi?id=1202719 for
        # more details.
        # TODO: Remove once we depend on a libvirt with this bug fixed.

        # We expect libvirt to show that the original leaf has been removed
        # from the active volume chain.
        origVols = sorted([x['volumeID'] for x in self.drive.volumeChain])
        expectedVols = origVols[:]
        expectedVols.remove(self.drive.volumeID)

        alias = self.drive['alias']
        self.vm.log.info("Waiting for libvirt to update the XML after pivot "
                         "of drive %s completed", alias)
        while True:
            # This operation should complete in either one or two iterations of
            # this loop.  Until libvirt updates the XML there is nothing to do
            # but wait.  While we wait we continue to tell engine that the job
            # is ongoing.  If we are still in this loop when the VM is powered
            # off, the merge will be resolved manually by engine using the
            # reconcileVolumeChain verb.
            chains = self.vm._driveGetActualVolumeChain([self.drive])
            if alias not in chains.keys():
                raise RuntimeError("Failed to retrieve volume chain for "
                                   "drive %s.  Pivot failed.", alias)
            curVols = sorted([entry.uuid for entry in chains[alias]])

            if curVols == origVols:
                time.sleep(1)
            elif curVols == expectedVols:
                self.vm.log.info("The XML update has been completed")
                break
            else:
                self.vm.log.error("Bad volume chain found for drive %s. "
                                  "Previous chain: %s, Expected chain: %s, "
                                  "Actual chain: %s", alias, origVols,
                                  expectedVols, curVols)
                raise RuntimeError("Bad volume chain found")
