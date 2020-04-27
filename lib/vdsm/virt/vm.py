#
# Copyright 2008-2020 Red Hat, Inc.
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

from __future__ import absolute_import
from __future__ import division

# stdlib imports
from collections import defaultdict, namedtuple
from contextlib import contextmanager
import json
import logging
import os
import tempfile
import threading
import time
import uuid
import xml.etree.ElementTree as ET

# 3rd party libs imports
import libvirt
#
# As [1] says:
#
#   Libvirt does not guarantee any support of direct use of the guest agent. If
#   you don't mind using libvirt-qemu.so, you can use the
#   virDomainQemuAgentCommand API (exposed by virsh qemu-agent-command); but be
#   aware that this is unsupported, and any changes you make to the agent that
#   change state behind libvirt's back may cause libvirt to misbehave.
#
# So let's be careful and use the interface only to gather information and not
# to change state of the guest.
#
# [1] https://wiki.libvirt.org/page/Qemu_guest_agent
import libvirt_qemu
import six

# vdsm imports
from vdsm.common import api
from vdsm.common import cpuarch
from vdsm.common import exception
from vdsm.common import libvirtconnection
from vdsm.common import logutils
from vdsm.common import response
import vdsm.common.time
import vdsm.virt.jobs
from vdsm import constants
from vdsm import hugepages
from vdsm import jobs
from vdsm import utils
from vdsm.config import config
from vdsm.common import concurrent
from vdsm.common import conv
from vdsm.common import hooks
from vdsm.common import supervdsm
from vdsm.common import xmlutils
from vdsm.common.define import ERROR, NORMAL, doneCode, errCode
from vdsm.common.logutils import SimpleLogAdapter, volume_chain_to_str
from vdsm.network import api as net_api

# TODO: remove these imports, code using this should use storage apis.
from vdsm.storage import qemuimg
from vdsm.storage import sdc

from vdsm.virt import backup
from vdsm.virt import blockjob
from vdsm.virt import domxml_preprocess
from vdsm.virt import drivemonitor
from vdsm.virt import guestagent
from vdsm.virt import libvirtxml
from vdsm.virt import metadata
from vdsm.virt import migration
from vdsm.virt import sampling
from vdsm.virt import saslpasswd2
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
from vdsm.virt.jobs import snapshot
from vdsm.virt import vmdevices
from vdsm.virt.vmdevices import drivename
from vdsm.virt.vmdevices import lookup
from vdsm.virt.vmdevices import hwclass
from vdsm.virt.vmdevices import storagexml
from vdsm.virt.vmdevices.common import get_metadata
from vdsm.virt.vmdevices.common import identify_from_xml_elem
from vdsm.virt.vmdevices.storage import DISK_TYPE, VolumeNotFound
from vdsm.virt.vmdevices.storage import BLOCK_THRESHOLD
from vdsm.virt.vmdevices.storagexml import change_disk
from vdsm.virt.vmpowerdown import VmShutdown, VmReboot
from vdsm.virt.utils import isVdsmImage, cleanup_guest_socket
from vdsm.virt.utils import extract_cluster_version
from vdsm.virt.utils import TimedAcquireLock
from six.moves import range


# A libvirt constant for undefined cpu quota
_NO_CPU_QUOTA = 0

# A libvirt constant for undefined cpu period
_NO_CPU_PERIOD = 0


class VolumeError(RuntimeError):
    def __str__(self):
        return "Bad volume specification " + RuntimeError.__str__(self)


class DoubleDownError(RuntimeError):
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


class ResumeBehavior:
    AUTO_RESUME = 'auto_resume'
    LEAVE_PAUSED = 'leave_paused'
    KILL = 'kill'


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


class DestroyedOnResumeError(DestroyedOnStartupError):
    """
    The VM was destroyed while it was resumed.
    This happens when the VM is in paused state for too long and it is
    instructed to destroy itself in such a case.
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

    def __bool__(self):
        return self.origin is not None

    # TODO: drop when py2 is no longer needed
    __nonzero__ = __bool__


def _undefine_vm_flags():
    flags = libvirt.VIR_DOMAIN_UNDEFINE_NVRAM

    # If incremental backup is supported by libvirt we should add
    # VIR_DOMAIN_UNDEFINE_CHECKPOINTS_METADATA flag to make sure
    # all checkpoint metadata will be removed also. If the VM doesn't
    # have any backups with checkpoint this flag shouldn't have
    # any effect.
    #
    # TODO: Remove check when we require libvirt 6.0 on all distros.
    if hasattr(libvirt, "VIR_DOMAIN_UNDEFINE_CHECKPOINTS_METADATA"):
        flags |= libvirt.VIR_DOMAIN_UNDEFINE_CHECKPOINTS_METADATA

    return flags


def _undefine_stale_domain(vm, connection):
    doms_to_remove = []
    try:
        dom = connection.lookupByUUIDString(vm.id)
    except libvirt.libvirtError as e:
        if e.get_error_code() != libvirt.VIR_ERR_NO_DOMAIN:
            raise
    else:
        doms_to_remove.append(dom)
    try:
        dom = connection.lookupByName(vm.name)
    except libvirt.libvirtError as e:
        if e.get_error_code() != libvirt.VIR_ERR_NO_DOMAIN:
            raise
    else:
        doms_to_remove.append(dom)
    for dom in doms_to_remove:
        try:
            state, reason = dom.state(0)
            if state in vmstatus.LIBVIRT_DOWN_STATES:
                flags = _undefine_vm_flags()
                dom.undefineFlags(flags)
                vm.log.debug("Stale domain removed: %s", (vm.id,))
            else:
                raise exception.VMExists("VM %s is already running: %s" %
                                         (vm.id, state,))
        except libvirt.libvirtError as e:
            if e.get_error_code() != libvirt.VIR_ERR_NO_DOMAIN:
                raise


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

    def _makeChannelPath(self, device_name):
        for name, path in self._domain.all_channels():
            if name == device_name:
                return path
        return constants.P_LIBVIRT_VMCHANNELS + self.id + '.' + device_name

    def __init__(self, cif, params, recover=False):
        """
        Initialize a new VM instance.

        :param cif: The client interface that creates this VM.
        :type cif: :class:`vdsm.clientIF.clientIF`
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
        elapsedTimeOffset = float(params.pop('elapsedTimeOffset', 0))
        # we need to make sure the 'devices' key exists in vm.conf regardless
        # how the Vm is initialized, either through XML or from conf.
        self.conf = {'devices': []}
        self.conf.update(params)
        self._external = params.get('external', False)
        self.arch = cpuarch.effective()
        self._src_domain_xml = params.get('_srcDomXML')
        if self._src_domain_xml is not None:
            self._domain = DomainDescriptor(self._src_domain_xml)
        else:
            self._domain = DomainDescriptor(params['xml'])
        self.id = self._domain.id
        if self._src_domain_xml is not None:
            if self._altered_state.from_snapshot:
                self._src_domain_xml = \
                    self._correct_disk_volumes_from_xml(
                        self._src_domain_xml, params['xml'])
                self._domain = DomainDescriptor(self._src_domain_xml)
            self.conf['xml'] = self._src_domain_xml
        self.log = SimpleLogAdapter(self.log, {"vmId": self.id})
        self._dom = virdomain.Disconnected(self.id)
        self.cif = cif
        self._custom = {'vmId': self.id}
        self._exit_info = {}
        self._cluster_version = None
        self._pause_time = None
        self._guest_agent_api_version = None
        self._balloon_minimum = None
        self._balloon_target = None
        self._blockJobs = {}
        self._md_desc = metadata.Descriptor.from_xml(self.conf['xml'])
        self._init_from_metadata()
        self._destroy_requested = threading.Event()
        self._monitorResponse = 0
        self._post_copy = migration.PostCopyPhase.NONE
        self._consoleDisconnectAction = ConsoleDisconnectAction.LOCK_SCREEN
        self._confLock = threading.Lock()
        self._jobsLock = threading.Lock()
        self._statusLock = threading.Lock()
        self._creationThread = concurrent.thread(self._startUnderlyingVm,
                                                 name="vm/" + self.id[:8])
        self._incoming_migration_finished = threading.Event()
        self._incoming_migration_vm_running = threading.Event()
        self._volPrepareLock = threading.Lock()
        self._initTimePauseCode = None
        self._timeOffset = params.get('timeOffset')
        self._initTimeRTC = int(
            0 if self._timeOffset is None else self._timeOffset
        )
        self._guestEvent = vmstatus.POWERING_UP
        self._guestEventTime = 0
        self._guestCpuRunning = False
        self._guestCpuLock = TimedAcquireLock(self.id)
        if recover:
            with self._md_desc.values() as md:
                if 'startTime' in md:
                    self._startTime = md['startTime']
                else:
                    self._startTime = time.time()
        else:
            self._startTime = time.time() - elapsedTimeOffset

        self._usedIndices = defaultdict(list)  # {'ide': [], 'virtio' = []}

        self._vmStartEvent = threading.Event()
        self._vmAsyncStartError = None
        self._vmCreationEvent = threading.Event()
        self.stopped_migrated_event_processed = threading.Event()
        self._incoming_migration_prepared = threading.Event()
        self._devices = vmdevices.common.empty_dev_map()
        self._hotunplugged_devices = {}  # { alias: device_object }

        self.drive_monitor = drivemonitor.DriveMonitor(
            self, self.log, enabled=False)
        self._connection = libvirtconnection.get(cif)
        if (recover and
            # status retrieved from the recovery file (legacy style)
            (params.get('status') == vmstatus.MIGRATION_SOURCE or
             # no status from recovery file available (new style)
             params.get('status') is None and self._recovering_migration())):
            self.log.info("Recovering possibly last_migrating VM")
            last_migrating = True
        else:
            last_migrating = False
        self._migrationSourceThread = migration.SourceThread(
            self, recovery=last_migrating)
        self._guestSocketFile = self._makeChannelPath(self._agent_channel_name)
        self._qemuguestSocketFile = self._makeChannelPath(
            vmchannels.QEMU_GA_DEVICE_NAME)
        self.guestAgent = guestagent.GuestAgent(
            self._guestSocketFile, self.cif.channelListener, self.log,
            self._onGuestStatusChange,
            lambda: self.cif.qga_poller.get_caps(self.id),
            lambda: self.cif.qga_poller.get_guest_info(self.id),
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
        self._clientIp = ''
        self._clientPort = ''
        self._monitorable = False
        self._migration_downtime = None
        self._pause_code = None
        self._last_disk_mapping_hash = None

    @property
    def _hugepages_shared(self):
        custom = self._custom['custom']
        return conv.tobool(custom.get('hugepages_shared', False))

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
        if not self.hugepages:
            return 0

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
        if not self.hugepages:
            return 0

        # Integer ceiling (m + n - 1) // n.
        return (
            (self.mem_size_mb() * 1024 + self.hugepagesz - 1) //
            self.hugepagesz
        )

    @property
    def _agent_channel_name(self):
        name = vmchannels.LEGACY_DEVICE_NAME
        for channel_name, _path in self._domain.all_channels():
            if channel_name == 'ovirt-guest-agent.0':
                name = channel_name
        return name

    def _init_from_metadata(self):
        self._custom['custom'] = self._md_desc.custom
        with self._md_desc.values() as md:
            self._destroy_on_reboot = (
                md.get('destroy_on_reboot', False) or
                self._domain.on_reboot_config() == 'destroy'
            )
            # can be None, and it is fine.
            self._guest_agent_api_version = md.get('guestAgentAPIVersion')
            exit_info = {}
            for key in ('exitCode', 'exitMessage', 'exitReason',):
                value = md.get(key)
                if value is not None:
                    exit_info[key] = value
            self._exit_info.update(exit_info)
            # start with sane defaults:
            self._mem_guaranteed_size_mb = 0
            mem_guaranteed_size = md.get('minGuaranteedMemoryMb')
            if mem_guaranteed_size is not None:
                # data from Engine prevails:
                self._mem_guaranteed_size_mb = mem_guaranteed_size
            else:
                # if this is missing, let's try using what we may have saved
                self._mem_guaranteed_size_mb = md.get('memGuaranteedSize', 0)
            self._blockJobs = json.loads(md.get('block_jobs', '{}'))
            self._cluster_version = extract_cluster_version(md)
            self._launch_paused = conv.tobool(md.get('launchPaused', False))
            self._resume_behavior = md.get('resumeBehavior',
                                           ResumeBehavior.AUTO_RESUME)
            self._snapshot_job = json.loads(md.get('snapshot_job', '{}'))
            self._pause_time = md.get('pauseTime')
            self._balloon_target = md.get('balloonTarget')

    def min_cluster_version(self, major, minor):
        """
        Check that cluster version is at least major.minor.

        :param int major: Required major version.
        :param int minor: Required minor version.

        :returns: True iff VM cluster version is known and is at least
          `major`.`minor`.
        :rtype: bool
        """
        if self._cluster_version is None:
            return False
        cluster_major, cluster_minor = self._cluster_version
        return (cluster_major > major or
                cluster_major == major and cluster_minor >= minor)

    def _get_lastStatus(self):
        # Note that we don't use _statusLock here due to potential risk of
        # recursive locking.
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
                self._lastStatus = value

    def send_status_event(self, **kwargs):
        stats = {'status': self._getVmStatus()}
        if stats['status'] == vmstatus.DOWN:
            stats.update(self._getDownVmStats())
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
        return str(vdsm.common.time.event_time())

    lastStatus = property(_get_lastStatus, set_last_status)

    def __getNextIndex(self, used):
        for n in range(max(used or [0]) + 2):
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
        if dev['type'] in (hwclass.DISK, hwclass.NIC):
            vm_custom = self._custom['custom']
            self.log.debug('device %s: adding VM custom properties %s',
                           dev['type'], vm_custom)
            dev['vm_custom'] = vm_custom
        return dev

    def _normalize_storage_params(self, disk_params):
        # Normalize vdsm images
        for drv in disk_params:
            if isVdsmImage(drv):
                try:
                    self._normalizeVdsmImg(drv)
                except StorageUnavailableError:
                    # storage unavailable is not fatal on recovery;
                    # the storage subsystem monitors the devices
                    # and will notify when they come up later.
                    if not self.recovering:
                        raise

        self.normalizeDrivesIndices(disk_params)

    def _initialize_balloon(self, balloon_devs):
        if len(balloon_devs) < 1:
            self.log.warning("No balloon device present")
            return
        elif len(balloon_devs) > 1:
            self.log.warning("Multiple balloon devices present")
            return
        if self._balloon_target is None:
            self._balloon_target = \
                self.mem_size_mb(current=self.recovering) * 1024
        self._balloon_minimum = self._mem_guaranteed_size_mb * 1024

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

        status = self.status()
        status['xml'] = self._domain.xml
        return response.success(vmList=status)

    def mem_size_mb(self, current=False):
        mem_size_mb = self._domain.get_memory_size(current=current)
        if mem_size_mb is None:
            self._updateDomainDescriptor()
            mem_size_mb = self._domain.get_memory_size(current=current)
        return mem_size_mb

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
                self._wait_for_incoming_postcopy_migration()

            if self.recovering and \
               self._lastStatus == vmstatus.WAIT_FOR_LAUNCH:
                if self._exit_info:
                    self.set_last_status(vmstatus.DOWN,
                                         vmstatus.WAIT_FOR_LAUNCH)
                else:
                    self._recover_status()
                    if self._lastStatus == vmstatus.MIGRATION_DESTINATION:
                        self._wait_for_incoming_postcopy_migration()
                        self.lastStatus = vmstatus.UP
                    if self._snapshot_job:
                        self.snapshot(None, None, None,
                                      job_uuid=self._snapshot_job['jobUUID'],
                                      recovery=True)
            else:
                self.lastStatus = vmstatus.UP
            if self._initTimePauseCode:
                self._pause_code = self._initTimePauseCode
                if self._pause_code != 'NOERR' and \
                   self._pause_time is None:
                    self._pause_time = vdsm.common.time.monotonic_time()
                if self._initTimePauseCode == 'ENOSPC':
                    self.cont()
            else:
                self._pause_code = None
                self._pause_time = None

            self.recovering = False
            if self._dom.connected:
                self._updateDomainDescriptor()

            self.send_status_event(**self._getRunningVmStats())

        except MissingLibvirtDomainError as e:
            # we cannot ever deal with this error, not even on recovery.
            exit_info = self._exit_info.copy()
            self.setDownStatus(
                exit_info.get('exitCode', ERROR),
                exit_info.get('exitReason', e.reason),
                exit_info.get('exitMessage', ''))
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
        except (libvirt.libvirtError, virdomain.NotConnectedError,):
            # We proceed with the best effort setting in case of error.
            # (NotConnectedError can appear in case of error in _run.)
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
            if reason == libvirt.VIR_DOMAIN_RUNNING_POSTCOPY:
                # post-copy migration on the destination
                self.log.info("Post-copy incoming migration detected "
                              "in recovery")
                self.set_last_status(vmstatus.MIGRATION_DESTINATION,
                                     vmstatus.WAIT_FOR_LAUNCH)
            elif self._recovering_migration(self._dom):
                # pre-copy migration on the source
                self.set_last_status(vmstatus.MIGRATION_SOURCE,
                                     vmstatus.WAIT_FOR_LAUNCH)
                self._migrationSourceThread.start()
            else:
                self.set_last_status(vmstatus.UP, vmstatus.WAIT_FOR_LAUNCH)
        elif state == libvirt.VIR_DOMAIN_SHUTOFF and not self._exit_info:
            if reason == libvirt.VIR_DOMAIN_SHUTDOWN:
                self.setDownStatus(NORMAL, vmexitreason.USER_SHUTDOWN)
            elif reason == libvirt.VIR_DOMAIN_SHUTOFF_DESTROYED:
                self.setDownStatus(NORMAL, vmexitreason.ADMIN_SHUTDOWN)
            elif reason == libvirt.VIR_DOMAIN_SHUTOFF_MIGRATED:
                self.setDownStatus(NORMAL, vmexitreason.MIGRATION_SUCCEEDED)
            elif reason == libvirt.VIR_DOMAIN_SHUTOFF_SAVED:
                self.setDownStatus(NORMAL, vmexitreason.SAVE_STATE_SUCCEEDED)
            elif reason == libvirt.VIR_DOMAIN_SHUTOFF_FAILED:
                self.setDownStatus(ERROR, vmexitreason.LIBVIRT_START_FAILED)
            else:
                self.setDownStatus(ERROR, vmexitreason.GENERIC_ERROR)
        else:
            self.log.error("Unexpected VM state: %s (reason %s)",
                           state, reason)
            # We must unset WAIT_FOR_LAUNCH status otherwise clientIF will wait
            # for status change forever. Setting UP in such a case is
            # consistent with the libvirtError fallback above.
            self.set_last_status(vmstatus.UP, vmstatus.WAIT_FOR_LAUNCH)

    def _wait_for_incoming_postcopy_migration(self):
        # We must wait for a contingent post-copy migration to finish
        # in VM initialization before we can run libvirt jobs such as
        # write metadata or run periodic operations.
        self._incoming_migration_finished.wait(
            config.getint('vars', 'migration_destination_timeout'))
        # Wait a bit to increase the chance that downtime is reported
        # from the source before we report that the VM is UP on the
        # destination.  This makes migration completion handling in
        # Engine easier.
        time.sleep(1)

    def preparePaths(self):
        drives = vmdevices.common.storage_device_params_from_domain_xml(
            self.id, self.domain, self._md_desc, self.log)
        self._preparePathsForDrives(drives)

    def _preparePathsForDrives(self, drives):
        for drive in drives:
            with self._volPrepareLock:
                if self._destroy_requested.is_set():
                    # A destroy request has been issued, exit early
                    break
                if self._altered_state.origin is not None:
                    # We must use the original payload path in
                    # incoming migrations, otherwise the generated
                    # payload path may not match the one from the
                    # domain XML (when migrating from Vdsm versions
                    # using different payload paths).
                    path = drive.get('path')
                else:
                    path = None
                drive['path'] = self.cif.prepareVolumePath(
                    drive, self.id, path=path
                )
                if isVdsmImage(drive):
                    # This is the only place we support manipulation of a
                    # prepared image, required for the localdisk hook. The hook
                    # may change drive's diskType, path and format.
                    modified = hooks.after_disk_prepare(drive, self._custom)
                    drive.update(modified)
        else:
            # Now we got all the resources we needed
            self.drive_monitor.enable()

    def _prepareTransientDisks(self, drives):
        for drive in drives:
            self._createTransientDisk(drive)

    def payload_drives(self):
        return [drive for drive in self._devices[hwclass.DISK]
                if vmdevices.storage.is_payload_drive(drive)]

    def _getShutdownReason(self):
        exit_code = NORMAL
        with self._shutdownLock:
            reason = self._shutdownReason
        # There are more shutdown reasons that are errors,
        # but in those cases the code should not reach this method,
        # so only two of them are handled here
        if reason in (vmexitreason.DESTROYED_ON_PAUSE_TIMEOUT,
                      vmexitreason.HOST_SHUTDOWN):
            exit_code = ERROR
        self.log.debug('shutdown reason: %s', reason)
        return exit_code, reason

    def _onQemuDeath(self, exit_code, reason):
        self.log.info('underlying process disconnected')
        self._dom = virdomain.Defined(self.id, self._dom)
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

    def onReboot(self):
        try:
            self.log.info('reboot event')
            self._startTime = time.time()
            self._guestEventTime = self._startTime
            self._guestEvent = vmstatus.REBOOT_IN_PROGRESS
            self._powerDownEvent.set()
            self._update_metadata()
            # this always triggers onStatusChange event, which
            # also sends back status event to Engine.
            self.guestAgent.onReboot()
            if self._destroy_on_reboot:
                self.doDestroy(reason=vmexitreason.DESTROYED_ON_REBOOT)
        except Exception:
            self.log.exception("Reboot event failed")

    def onConnect(self, clientIp='', clientPort=''):
        if clientIp:
            self._clientIp = clientIp
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
        if not self._clientIp and not self._destroy_requested.is_set():
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
        if self._clientIp != clientIp:
            self.log.debug('Ignoring disconnect event because ip differs')
            return
        if self._clientPort and self._clientPort != clientPort:
            self.log.debug('Ignoring disconnect event because ports differ')
            return

        self._clientIp = ''
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
        self._timeOffset = newTimeOffset

    def getChunkedDrives(self):
        """
        Return list of writable chunked drives, or writable non-chunked
        drives replicating to chunked replica drive.
        """
        return [drive for drive in self._devices[hwclass.DISK]
                if (drive.chunked or drive.replicaChunked) and not
                drive.readonly]

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

        blockinfo = vmdevices.storage.BlockInfo(capacity, alloc, physical)

        if blockinfo != drive.blockinfo:
            drive.blockinfo = blockinfo
            self.log.debug("Extension info for drive %s volume %s: %s",
                           drive.name, drive.volumeID, blockinfo)

        return blockinfo

    def monitor_drives(self):
        """
        Return True if at least one drive is being extended, False otherwise.
        """
        extended = False

        try:
            for drive in self.drive_monitor.monitored_drives():
                if self.extend_drive_if_needed(drive):
                    extended = True
        except drivemonitor.ImprobableResizeRequestError:
            return False

        return extended

    def extend_drive_if_needed(self, drive):
        """
        Check if a drive should be extended, and start extension flow if
        needed.

        When libvirt BLOCK_THRESHOLD event handling is enabled (
        irs.enable_block_threshold_event == True), this method acts according
        the drive.threshold_state:

        - UNSET: the drive needs to register for a new block threshold,
                 so try to set it. We set the threshold both for chunked
                 drives and non-chunked drives replicating to chunked
                 drives.
        - EXCEEDED: the drive needs extension, try to extend it.
        - SET: this method should never receive a drive in this state,
               emit warning and exit.

        Return True if started an extension flow, False otherwise.
        """

        if drive.threshold_state == BLOCK_THRESHOLD.SET:
            self.log.warning(
                "Unexpected state for drive %s: threshold_state SET",
                drive.name)
            return

        try:
            capacity, alloc, physical = self._getExtendInfo(drive)
        except libvirt.libvirtError as e:
            self.log.error("Unable to get watermarks for drive %s: %s",
                           drive.name, e)
            return False

        if drive.threshold_state == BLOCK_THRESHOLD.UNSET:
            self.drive_monitor.set_threshold(drive, physical)

        if not self.drive_monitor.should_extend_volume(
                drive, drive.volumeID, capacity, alloc, physical):
            return False

        # TODO: if the threshold is wrongly set below the current allocation,
        # for example because of delays in handling the event, or if the VM
        # writes too fast, we will never receive an event.
        # We need to set the drive threshold to EXCEEDED both if we receive
        # one event or if we found that the threshold was exceeded during
        # the drivemonitor.should_extend_volume check.
        self.drive_monitor.update_threshold_state_exceeded(drive)

        self.log.info(
            "Requesting extension for volume %s on domain %s (apparent: "
            "%s, capacity: %s, allocated: %s, physical: %s "
            "threshold_state: %s)",
            drive.volumeID, drive.domainID, drive.apparentsize, capacity,
            alloc, physical, drive.threshold_state)

        self.extendDriveVolume(drive, drive.volumeID, physical, capacity)
        return True

    def extendDriveVolume(self, vmDrive, volumeID, curSize, capacity):
        """
        Extend drive volume and its replica volume during replication.

        Must be called only when the drive or its replica are chunked.
        """
        newSize = vmDrive.getNextVolumeSize(curSize, capacity)

        # If drive is replicated to a block device, we extend first the
        # replica, and handle drive later in __afterReplicaExtension.

        # Used to measure the total extend time for the drive and the replica.
        # Note that the volume is extended after the replica is extended, so
        # the total extend time includes the time to extend the replica.
        clock = vdsm.common.time.Clock()
        clock.start("total")

        if vmDrive.replicaChunked:
            self.__extendDriveReplica(vmDrive, newSize, clock)
        else:
            self.__extendDriveVolume(vmDrive, volumeID, newSize, clock)

    def __refreshDriveVolume(self, volInfo):
        self.log.debug("Refreshing drive volume for %s (domainID: %s, "
                       "volumeID: %s)", volInfo['name'], volInfo['domainID'],
                       volInfo['volumeID'])
        self.cif.irs.refreshVolume(volInfo['domainID'], volInfo['poolID'],
                                   volInfo['imageID'], volInfo['volumeID'])

    def __verifyVolumeExtension(self, volInfo):
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
        clock = volInfo["clock"]
        clock.stop("extend-replica")

        with clock.run("refresh-replica"):
            self.__refreshDriveVolume(volInfo)

        self.__verifyVolumeExtension(volInfo)
        vmDrive = lookup.drive_by_name(
            self.getDiskDevices()[:], volInfo['name'])
        if not vmDrive.chunked:
            # This was a replica only extension, we are done.
            clock.stop("total")
            self.log.info("Extend replica %s completed %s",
                          volInfo["volumeID"], clock)
            return

        self.log.debug("Requesting extension for the original drive: %s "
                       "(domainID: %s, volumeID: %s)",
                       vmDrive.name, vmDrive.domainID, vmDrive.volumeID)
        self.__extendDriveVolume(vmDrive, vmDrive.volumeID,
                                 volInfo['newSize'], clock)

    def __extendDriveVolume(self, vmDrive, volumeID, newSize, clock):
        clock.start("extend-volume")
        volInfo = {
            'domainID': vmDrive.domainID,
            'imageID': vmDrive.imageID,
            'internal': vmDrive.volumeID != volumeID,
            'name': vmDrive.name,
            'newSize': newSize,
            'poolID': vmDrive.poolID,
            'volumeID': volumeID,
            'clock': clock,
        }
        self.log.debug("Requesting an extension for the volume: %s", volInfo)
        self.cif.irs.sendExtendMsg(
            vmDrive.poolID,
            volInfo,
            newSize,
            self.__afterVolumeExtension)

    def __extendDriveReplica(self, drive, newSize, clock):
        clock.start("extend-replica")
        volInfo = {
            'domainID': drive.diskReplicate['domainID'],
            'imageID': drive.diskReplicate['imageID'],
            'name': drive.name,
            'newSize': newSize,
            'poolID': drive.diskReplicate['poolID'],
            'volumeID': drive.diskReplicate['volumeID'],
            'clock': clock,
        }
        self.log.debug("Requesting an extension for the volume "
                       "replication: %s", volInfo)
        self.cif.irs.sendExtendMsg(drive.poolID,
                                   volInfo,
                                   newSize,
                                   self.__afterReplicaExtension)

    def __afterVolumeExtension(self, volInfo):
        clock = volInfo["clock"]
        clock.stop("extend-volume")

        with clock.run("refresh-volume"):
            self.__refreshDriveVolume(volInfo)

        # Check if the extension succeeded.  On failure an exception is raised
        # TODO: Report failure to the engine.
        volSize = self.__verifyVolumeExtension(volInfo)

        # This was a volume extension or replica and volume extension.
        clock.stop("total")
        self.log.info("Extend volume %s completed %s",
                      volInfo["volumeID"], clock)

        # Only update apparentsize and truesize if we've resized the leaf
        if not volInfo['internal']:
            drive = lookup.drive_by_name(
                self.getDiskDevices()[:], volInfo['name'])
            self._update_drive_volume_size(drive, volSize)

        self._resume_if_needed()

    def _update_drive_volume_size(self, drive, volsize):
        """
        Updates drive's apparentsize and truesize, and set a new block
        threshold based on the new size.

        Arguments:
            drive (virt.vmdevices.storage.Drive): The drive object using the
                resized volume.
            volsize (virt.vm.VolumeSize): new volume size tuple
        """
        drive.apparentsize = volsize.apparentsize
        drive.truesize = volsize.truesize
        self.drive_monitor.set_threshold(drive, volsize.apparentsize)

    def _resume_if_needed(self):
        try:
            self.cont()
        except libvirt.libvirtError as e:
            current_status = self.lastStatus
            if (current_status == vmstatus.UP and
                    e.get_error_domain() == libvirt.VIR_FROM_QEMU and
                    e.get_error_code() == libvirt.VIR_ERR_OPERATION_INVALID):
                # Safe to skip: the VM is already running when the
                # operation was attempted.
                self.log.debug("Cannot resume VM in state %s", current_status)
            else:
                self.log.exception("Cannot resume VM")
        except DestroyedOnResumeError:
            self.log.debug("Cannot resume VM: paused for too long, destroyed")

    def maybe_resume(self):
        """
        Handle resume request according to auto-resume value of the VM.

        The VM may be resumed, left paused, or destroyed, according to its
        auto-resume setting.

        :raises: `DestroyedOnResumeError` if the VM is destroyed.
        """
        resume_behavior = self._resume_behavior
        if resume_behavior == ResumeBehavior.AUTO_RESUME:
            self.cont()
            self.log.info("VM resumed")
        elif resume_behavior == ResumeBehavior.LEAVE_PAUSED:
            self.log.info("Auto-resume disabled for the VM")
        elif resume_behavior == ResumeBehavior.KILL:
            if self.maybe_kill_paused():
                raise DestroyedOnResumeError()
            else:
                self.cont()
                self.log.info("VM resumed")
        else:
            raise Exception("Unsupported resume behavior value: %s",
                            (resume_behavior,))

    def maybe_kill_paused(self):
        self.log.debug("Considering to kill a paused VM")
        if self._resume_behavior != ResumeBehavior.KILL:
            self.log.debug("VM not permitted to be killed")
            return False
        # TODO: The following should prevent other threads from running actions
        # on the VM until VM status is set to Down.  Engine and libvirt are
        # unaware about what's happening and may trigger concurrent operations
        # (such as resume, migration, destroy, ...) on the VM while we check
        # its status and possibly destroy it.
        pause_time = self._pause_time
        now = vdsm.common.time.monotonic_time()
        if pause_time is not None and \
           now - pause_time > \
           config.getint('vars', 'vm_kill_paused_time'):
            self.log.info("VM paused for too long, will be destroyed")
            self.destroy(gracefulAttempts=0,
                         reason=vmexitreason.DESTROYED_ON_PAUSE_TIMEOUT)
            return True
        else:
            self.log.debug("VM not paused long enough, not killing it")
            return False

    def _acquireCpuLockWithTimeout(self, flow):
        timeout = self._loadCorrectedTimeout(
            config.getint('vars', 'vm_command_timeout'))
        self._guestCpuLock.acquire(timeout, flow)

    def cont(self, afterState=vmstatus.UP, guestCpuLocked=False,
             ignoreStatus=False, guestTimeSync=False):
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
            self._acquireCpuLockWithTimeout(flow='cont')
        try:
            if (not ignoreStatus and
                    self.lastStatus in (vmstatus.MIGRATION_SOURCE,
                                        vmstatus.SAVING_STATE,
                                        vmstatus.DOWN)):
                self.log.error('cannot cont while %s', self.lastStatus)
                return response.error('unexpected')
            self._underlyingCont()
            self._setGuestCpuRunning(self.isDomainRunning(),
                                     guestCpuLocked=True)
            self._logGuestCpuStatus('continue')
            self._lastStatus = afterState
            self._pause_code = None
            self._pause_time = None
            if guestTimeSync or \
               config.getboolean('vars', 'time_sync_cont_enable'):
                self.syncGuestTime()
        finally:
            if not guestCpuLocked:
                self._guestCpuLock.release()

        self.send_status_event()
        self._update_metadata()
        return response.success()

    def pause(self, afterState=vmstatus.PAUSED, guestCpuLocked=False,
              pauseCode='NOERR'):
        if not guestCpuLocked:
            self._acquireCpuLockWithTimeout(flow='pause')
        self._pause_code = pauseCode
        try:
            self._underlyingPause()
            self._setGuestCpuRunning(self.isDomainRunning(),
                                     guestCpuLocked=True)
            self._logGuestCpuStatus('pause')
            self._lastStatus = afterState
        finally:
            if not guestCpuLocked:
                self._guestCpuLock.release()

        self.send_status_event()
        if pauseCode != 'NOERR' and self._pause_time is None:
            self._pause_time = vdsm.common.time.monotonic_time()
            self._update_metadata()
        return response.success()

    @property
    def pause_code(self):
        return self._pause_code

    def _setGuestCpuRunning(self, isRunning, guestCpuLocked=False, flow=None):
        """
        here we want to synchronize the access to guestCpuRunning
        made by callback with the pause/cont methods.
        To do so we reuse guestCpuLocked.
        """
        if not guestCpuLocked:
            self._acquireCpuLockWithTimeout(flow=flow)
        try:
            self._guestCpuRunning = isRunning
        finally:
            if not guestCpuLocked:
                self._guestCpuLock.release()

    def syncGuestTime(self):
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
                self.log.warning(
                    template,
                    "QEMU agent unresponsive during "
                    "guest time synchronization")
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
        exit_info = {
            'exitCode': code,
            'exitMessage': exitMessage,
            'exitReason': exitReasonCode,
        }
        event_data = {}
        try:
            self.lastStatus = vmstatus.DOWN
            if self._altered_state.origin == _FILE_ORIGIN:
                exit_info['exitMessage'] = (
                    "Wake up from hibernation failed" +
                    ((":" + exitMessage) if exitMessage else ''))
            self._exit_info = exit_info
            self.log.info("Changed state to Down: %s (code=%i)",
                          exitMessage, exitReasonCode)
            try:
                self._update_metadata()
            except virdomain.NotConnectedError:
                # The VM got down before proper self._dom initialization.
                pass
            except libvirt.libvirtError as e:
                # The domain may no longer exist if it is transient
                # (i.e. legacy).
                if e.get_error_code() != libvirt.VIR_ERR_NO_DOMAIN:
                    raise
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
        if event_data:
            self.send_status_event(**event_data)

    def status(self):
        return {'vmId': self.id, 'status': self.lastStatus,
                'statusTime': self._get_status_time()}

    def getStats(self):
        """
        Used by vdsm.API.Vm.getStats.

        WARNING: This method should only gather statistics by copying data.
        Especially avoid costly and dangerous direct calls to the _dom
        attribute. Use the periodic operations instead!
        """
        stats = {'statusTime': self._get_status_time()}
        stats['status'] = self._getVmStatus()
        if stats['status'] == vmstatus.DOWN:
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
                oga_stats = self._getGuestStats()
                if 'memoryStats' in stats:
                    # prefer balloon stats over OGA stats
                    if 'memoryStats' not in oga_stats:
                        oga_stats['memoryStats'] = stats['memoryStats']
                    else:
                        oga_stats['memoryStats'].update(stats['memoryStats'])
                    if oga_stats['memUsage'] == '0':
                        # Compute memUsage from balloon stats
                        oga_stats['memUsage'] = str(int(
                            100 - float(
                                int(stats['memoryStats']['mem_free']) / 1024) /
                            self.mem_size_mb() * 100))
                stats.update(oga_stats)
        return stats

    def _getDownVmStats(self):
        stats = {
            'vmId': self.id,
            'status': self.lastStatus
        }
        stats.update(self._getExitedVmStats())
        return stats

    def _getExitedVmStats(self):
        stats = self._exit_info.copy()
        if self._timeOffset is not None:
            stats['timeOffset'] = self._timeOffset
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
            'vmType': self._domain.vm_type(),
            'kvmEnable': 'true',
            'acpiEnable': 'true' if self.acpi_enabled() else 'false'}
        return stats

    def _getRunningVmStats(self):
        """
        gathers all the stats which can change while a VM is running.
        """
        stats = {
            'elapsedTime': str(int(time.time() - self._startTime)),
            'monitorResponse': str(self._monitorResponse),
            'clientIp': self._clientIp,
            'timeOffset': str(
                '0' if self._timeOffset is None else self._timeOffset
            ),
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
        if self._pause_code is not None:
            stats['pauseCode'] = self._pause_code
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

    def migration_parameters(self):
        return {
            '_srcDomXML': self._dom.XMLDesc(0),
            'vmId': self.id,
            'xml': self._domain.xml,
            'elapsedTimeOffset': (
                time.time() - self._startTime
            ),
        }

    def migratable_domain_xml(self):
        """
        Return domain XML suitable for migration destinations.

        Unlike a normal domain XML, this domain XML may be slightly
        modified by libvirt and will not be rejected by the migration
        end.
        """
        return self._dom.XMLDesc(libvirt.VIR_DOMAIN_XML_MIGRATABLE)

    def _get_vm_migration_progress(self):
        return self.migrateStatus()['progress']

    def _getGraphicsStats(self):
        return {'displayInfo': vmdevices.graphics.display_info(self.domain)}

    def _getGuestStats(self):
        stats = self.guestAgent.getGuestInfo()
        realMemUsage = int(stats['memUsage'])
        if realMemUsage != 0:
            memUsage = (100 - float(realMemUsage) /
                        self.mem_size_mb() * 100)
        else:
            memUsage = 0
        stats['memUsage'] = utils.convertToStr(int(memUsage))
        if self.lastStatus == vmstatus.UP:
            self._update_guest_disk_mapping()
        return stats

    def _update_guest_disk_mapping(self):
        disk_mapping_hash = self.guestAgent.diskMappingHash
        if disk_mapping_hash == self._last_disk_mapping_hash:
            return
        guest_disk_mapping = list(six.iteritems(
            self.guestAgent.guestDiskMapping))
        with self._confLock:
            disk_devices = list(self.getDiskDevices())
            vmdevices.common.update_guest_disk_mapping(
                self._md_desc, disk_devices, guest_disk_mapping, self.log
            )
        try:
            self._sync_metadata()
            self._updateDomainDescriptor()
        except (libvirt.libvirtError, virdomain.NotConnectedError) as e:
            self.log.warning("Couldn't update metadata: %s", e)
            return
        self._last_disk_mapping_hash = disk_mapping_hash

    def isMigrating(self):
        return self._migrationSourceThread.migrating()

    def _recovering_migration(self, dom=None):
        try:
            if dom is None:
                dom = self._connection.lookupByUUIDString(self.id)
            job_stats = dom.jobStats()
        except libvirt.libvirtError:
            return False
        return migration.ongoing(job_stats)

    def hasTransientDisks(self):
        for drive in self._devices[hwclass.DISK]:
            if drive.transientDisk:
                return True
        return False

    @api.guard(_not_migrating)
    def migrate(self, params):
        self._acquireCpuLockWithTimeout(flow='migrate')
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
            self._post_copy = migration.PostCopyPhase.NONE
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

    def migrateCancel(self):
        self._acquireCpuLockWithTimeout(flow='migrate.cancel')
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

    def migrateChangeParams(self, params):
        self._acquireCpuLockWithTimeout(flow='migrate.change_params')

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
        # Do DOM-dependent xml transformations
        dom = xmlutils.fromstring(self.conf['xml'])

        on_reboot = vmxml.find_first(dom, 'on_reboot', None)
        if on_reboot is not None:
            vmxml.remove_child(dom, on_reboot)

        domxml_preprocess.replace_placeholders(
            dom, self.arch, self.conf.get('serial'))

        if config.getboolean('devel', 'xml_minimal_changes'):
            domxml_preprocess.update_disks_xml_from_objs(
                self, dom, self._devices[hwclass.DISK])
        else:
            domxml_preprocess.replace_disks_xml(
                dom, self._devices[hwclass.DISK])

        domxml_preprocess.update_leases_xml_from_disk_objs(
            self, dom, self._devices[hwclass.DISK])
        domxml_preprocess.replace_device_xml_with_hooks_xml(
            dom, self.id, self._custom)

        return xmlutils.tostring(dom, pretty=True)

    def _cleanup(self):
        """
        General clean up routine
        """
        self._cleanupDrives()
        self._cleanupGuestAgent()
        self._teardown_devices()
        cleanup_guest_socket(self._qemuguestSocketFile)
        self._cleanupStatsCache()
        for con in self._domain.get_device_elements('console'):
            vmdevices.core.cleanup_console(con, self.id)
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
            devices = list(self._tracked_devices())

        for device in devices:
            try:
                device.teardown()
            except Exception:
                self.log.exception('Failed to tear down device %s, device in '
                                   'inconsistent state', device.device)

    def _undefine_domain(self):
        if self._external:
            # This is a grey area, see rhbz#1610917;
            # we never really decided what is the standard here, so we
            # restore the < 4.2 behaviour (do not mess with external VM
            # *definition*) that we changed by side effect when switching
            # to persistent domains.
            self.log.info(
                "Will not undefine external VM %s", self.id)
            return

        try:
            flags = _undefine_vm_flags()
            self._dom.undefineFlags(flags)
        except libvirt.libvirtError as e:
            self.log.warning("Failed to undefine VM '%s' (error=%i)",
                             self.id, e.get_error_code())
        except virdomain.NotConnectedError:
            self.log.info("Can't undefine disconnected VM '%s'", self.id)

    def _cleanupStatsCache(self):
        try:
            sampling.stats_cache.remove(self.id)
        except KeyError:
            self.log.warning('timestamp already removed from stats cache')

    def isDomainRunning(self):
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

        self._vmDependentInit()

    def _vmDependentInit(self):
        """
        Perform the final initialization of the VM object once the
        libvirt.Domain object is available - e.g. after the VM was actually
        started by libvirt.
        This method is called on all the four initialization flows.
        """
        self._guestEventTime = self._startTime

        self._updateDomainDescriptor()
        self._updateMetadataDescriptor()

        self._getUnderlyingVmDevicesInfo()

        # Currently there is no protection agains mirroring a network twice,
        if not self.recovering:
            for nic in self._devices[hwclass.NIC]:
                for network in nic.portMirroring:
                    supervdsm.getProxy().setPortMirroring(network, nic.name)

            vmdevices.common.save_device_metadata(
                self._md_desc, self._devices, self.log)
            self.save_custom_properties()
            self._sync_metadata()

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

        for con in self._domain.get_device_elements('console'):
            vmdevices.core.prepare_console(con, self.id)

        self._guestCpuRunning = self.isDomainRunning()
        self._logGuestCpuStatus('domain initialization')
        if self.lastStatus not in (vmstatus.MIGRATION_DESTINATION,
                                   vmstatus.RESTORING_STATE):
            try:
                self._initTimePauseCode = self._readPauseCode()
            except libvirt.libvirtError as e:
                if self.recovering:
                    self.log.warning("Couldn't retrieve initial pause code "
                                     "from libvirt: %s", e)
                else:
                    raise
        if not self.recovering and self._initTimePauseCode:
            self._pause_code = self._initTimePauseCode
            if self._pause_code != 'NOERR' and self._pause_time is None:
                self._pause_time = vdsm.common.time.monotonic_time()
            if self._initTimePauseCode == 'ENOSPC':
                self.cont()

        self._dom_vcpu_setup()
        self._updateIoTuneInfo()

    def _hotplug_device_metadata(self, dev_class, dev_obj):
        attrs, data = get_metadata(dev_class, dev_obj)
        if not attrs:
            self.log.error(
                "No attrs trying to save metadata for "
                "hotplugged device %s", dev_obj)
        else:
            self._set_device_metadata(attrs, data)
            self._sync_metadata()

    def _hotunplug_device_metadata(self, dev_class, dev_obj):
        attrs, _ = get_metadata(dev_class, dev_obj)
        if not attrs:
            self.log.error(
                "No attrs trying to save metadata for "
                "hotunplugged device %s", dev_obj)
        else:
            self._clear_device_metadata(attrs)
            self._sync_metadata()

    def _set_device_metadata(self, attrs, dev_conf):
        """
        Set the metadata (dev_conf) for device identified by `attrs'.
        `dev_conf' is a python dict whose keys are strings.
        Overwrites any existing metadata.
        """
        data = utils.picklecopy(dev_conf)
        with self._md_desc.device(**attrs) as dev:
            dev.clear()
            dev.update(data)

    def _clear_device_metadata(self, attrs):
        """
        Clear the metadata for device identified by `attrs'.
        """
        with self._md_desc.device(**attrs) as dev:
            dev.clear()

    def _dom_vcpu_setup(self):
        self._updateVcpuTuneInfo()
        self._updateVcpuLimit()

    def _tracked_devices(self):
        for dom in self._domain.get_device_elements('graphics'):
            yield vmdevices.graphics.Graphics(dom, self.id)
        for dom in self._domain.get_device_elements('hostdev'):
            meta = vmdevices.common.dev_meta_from_elem(
                dom, self.id, self._md_desc
            )
            yield vmdevices.hostdevice.HostDevice(dom, meta, self.log)
        for dev_objects in self._devices.values():
            for dev_object in dev_objects[:]:
                yield dev_object

    def _setup_devices(self):
        """
        Runs before the underlying libvirt domain is created.

        Handle setup of all devices. If some device cannot be setup,
        go through the devices that were successfully setup and tear
        them down, logging all exceptions we encounter. Exception is then
        raised as we cannot continue the VM creation due to device failures.
        """
        done = []
        for dev_object in self._tracked_devices():
            try:
                dev_object.setup()
            except Exception:
                self.log.exception("Failed to setup device %s",
                                   dev_object.device)
                self._teardown_devices(done)
                raise
            else:
                done.append(dev_object)

    def _make_devices(self):
        disk_objs = self._perform_host_local_adjustment()
        return self._make_devices_from_xml(disk_objs)

    def _perform_host_local_adjustment(self):
        """
        Perform the per-host adjustments we need to make to the XML
        configuration received by Engine. Needed in the transitional phase
        on which the VM run flow is adjusting from vm.conf to xml.

        Starting a VM using XML is not just about sending the configuration
        in a different format. We need to address all the steps we need
        to set up the host and secure the resources needed by VM.

        This method collects those steps. It is expected to be dissolved
        once the flow is fully migrated to the XML.
        """
        # First, we need to set up storage. Storage still needs per-host
        # customizations/preparation. We will need new verbs and to integrate
        # into the Engine flow to get rid of this code.
        disk_objs = []
        disk_params = vmdevices.common.storage_device_params_from_domain_xml(
            self.id, self._domain, self._md_desc, self.log)

        if not disk_params:
            # unlikely but possible. Nothing else to do.
            return disk_objs

        self._normalize_storage_params(disk_params)

        # We need to prepare the images we are given. This includes
        # learning about the paths, getting drive leases and run the
        # drive-specific device hooks. In the future all of this should
        # be done explicitely by Engine before to invoke VM.create, so
        # before this flow.
        #
        # When recovering, we trust the path were already prepared in the
        # other flows.
        if not self.recovering:
            self._preparePathsForDrives(disk_params)
            self._prepareTransientDisks(disk_params)

        # we need Drive objects to correctly handle the lease placeholders.
        # so we make them in advance.
        disk_objs = [
            vmdevices.storage.Drive(self.log, **params)
            for params in disk_params
        ]

        # To properly support live merge, we still need to have meaningful
        # data in vm.conf['devices'] about the drives. We will need to fix
        # live merge (and possibly other storage flows, like snapshot) to
        # NOT use it and use only Drive objects instead. Once that is done,
        # we can get rid of this code.
        self._override_disk_device_config(disk_params)
        return disk_objs

    def _make_devices_from_xml(self, disk_objs=None):
        # Engine XML flow note:
        # we expect only storage devices to be sent in vm.conf format,
        # everything else should be taken from the XML.
        # We don't expect any storage device to be sent in the XML.
        dev_objs_from_xml = vmdevices.common.dev_map_from_domain_xml(
            self.id, self.domain, self._md_desc, self.log,
            noerror=self.recovering
        )

        if disk_objs:
            # overridden because of per-host adjustement
            dev_objs_from_xml[hwclass.DISK] = disk_objs

        self.log.debug('Built %d devices', len(dev_objs_from_xml))
        return dev_objs_from_xml

    def _override_disk_device_config(self, disk_params):
        disk_devs = []
        for params in disk_params:
            dev = {}
            dev.update(params)
            dev['type'] = hwclass.DISK
            dev = self._dev_spec_update_with_vm_conf(dev)

            self.log.debug("Overridden legacy device configuration: %s", dev)
            disk_devs.append(dev)
        self.conf['devices'] = disk_devs

        self.log.debug("Overridden %d legacy drive configurations",
                       len(disk_params))

    # TODO: Remove this method.
    def _build_device_conf_from_objects(self, dev_map):
        devices_conf = []
        for dev_class, dev_objs in dev_map.items():
            if dev_class == hwclass.DISK:
                # disk conf is stored when VM starts
                continue

            for dev in dev_objs:
                dev_params = dev.config()
                if dev_params is None:
                    self.log.debug('No parameters for device %s', dev)
                    continue

                devices_conf.append(dev_params)

        return devices_conf

    def _run(self):
        self.log.info("VM wrapper has started")
        if not self.recovering and \
           self._altered_state.origin != _MIGRATION_ORIGIN:
            self._remove_domain_artifacts()

        if not self.recovering and not self._altered_state.origin:
            # We need to define the domain in order to save device metadata in
            # _make_devices().  It'll get redefined with the final version
            # later.
            domxml = libvirtxml.make_placeholder_domain_xml(self)
            dom = self._connection.defineXML(domxml)
            self._dom = virdomain.Defined(self.id, dom)

        self._devices = self._make_devices()
        # We (re)initialize the balloon values in all the flows.
        self._initialize_balloon(
            list(self._domain.get_device_elements('memballoon'))
        )

        initDomain = self._altered_state.origin != _MIGRATION_ORIGIN
        # we need to complete the initialization, including
        # domDependentInit, after the migration is completed.

        if not self.recovering:
            self._setup_devices()

        if self.recovering:
            dom = self._connection.lookupByUUIDString(self.id)
            state, reason = dom.state(0)
            if state in vmstatus.LIBVIRT_DOWN_STATES:
                self._dom = virdomain.Defined(self.id, dom)
                return
            self._dom = virdomain.Notifying(dom, self._timeoutExperienced)
            for dev in self._devices[hwclass.NIC]:
                dev.recover()
        elif self._altered_state.origin == _MIGRATION_ORIGIN:
            self._incoming_migration_prepared.set()
            # self._dom will be disconnected until migration ends.
        elif self._altered_state.origin == _FILE_ORIGIN:
            if self.hugepages:
                self._prepare_hugepages()

            # TODO: for unknown historical reasons, we call this hook also
            # on this flow. Issues:
            # - we will also call the more specific before_vm_dehibernate
            # - we feed the hook with wrong XML
            # - we ignore the output of the hook
            hooks.before_vm_start(self._buildDomainXML(), self._custom)

            fromSnapshot = self._altered_state.from_snapshot
            srcDomXML = self._src_domain_xml
            if fromSnapshot:
                # If XML was provided by Engine, disk paths have already been
                # corrected in __init__.  If legacy configuration
                # (e.g. 'vmName') is present, we use the legacy path here.
                # Otherwise we leave srcDomXML untouched, since we don't have
                # anything from Engine (4.2.0) to update it with.
                if 'vmName' in self.conf:
                    srcDomXML = self._correct_disk_volumes_from_conf(srcDomXML)
                srcDomXML = self._correctGraphicsConfiguration(srcDomXML)
            hooks.before_vm_dehibernate(srcDomXML, self._custom,
                                        {'FROM_SNAPSHOT': str(fromSnapshot)})

            # TODO: this is debug information. For 3.6.x we still need to
            # see the XML even with 'info' as default level.
            self.log.info("%s", srcDomXML)

            self._connection.defineXML(srcDomXML)
            restore_path = self._altered_state.path
            fname = self.cif.prepareVolumePath(restore_path)
            try:
                if fromSnapshot:
                    self._connection.restoreFlags(
                        fname, srcDomXML, libvirt.VIR_DOMAIN_SAVE_PAUSED)
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
                # We use this flag only when starting VM, and we need to
                # make sure not to pass or use it on migration creation.
                if self._launch_paused:
                    flags |= libvirt.VIR_DOMAIN_START_PAUSED
                    self._pause_code = 'NOERR'
            hooks.dump_vm_launch_flags_to_file(self.id, flags)

            if self.hugepages:
                self._prepare_hugepages()

            try:
                hooks.before_vm_start(
                    self._buildDomainXML(),
                    self._custom,
                    final_callback=self._updateDomainDescriptor)

                flags = hooks.load_vm_launch_flags_from_file(self.id)

                # TODO: this is debug information. For 3.6.x we still need to
                # see the XML even with 'info' as default level.
                self.log.info("%s", self._domain.xml)

                dom = self._connection.defineXML(self._domain.xml)
                self._dom = virdomain.Defined(self.id, dom)
                self._update_metadata()
                dom.createWithFlags(flags)
                self._dom = virdomain.Notifying(dom, self._timeoutExperienced)
                hooks.after_vm_start(self._dom.XMLDesc(0), self._custom)
                for dev in self._customDevices():
                    hooks.after_device_create(dev._deviceXML, self._custom,
                                              dev.custom)
            finally:
                hooks.remove_vm_launch_flags_file(self.id)

        if initDomain:
            self._domDependentInit()

    def _remove_domain_artifacts(self):
        _undefine_stale_domain(self, self._connection)

    def _correct_disk_volumes_from_conf(self, srcDomXML):
        """
        Replace each volume in the given XML with the latest volume
        that the image has.
        Each image has a newer volume than the one that appears in the
        XML, which was the latest volume of the image at the time the
        snapshot was taken, since we create new volume when we preview
        or revert to snapshot.
        """
        domain = MutableDomainDescriptor(srcDomXML)
        devices = self._devices[hwclass.DISK]

        for element in domain.get_device_elements('disk'):
            if vmxml.attr(element, 'device') == 'disk':
                change_disk(element, devices, self.log)

        return domain.xml

    def _correct_disk_volumes_from_xml(self, srcDomXML, engine_xml):
        """
        Replace each volume in the given XML with the latest volume
        that the image has.
        Each image has a newer volume than the one that appears in the
        XML, which was the latest volume of the image at the time the
        snapshot was taken, since we create new volume when we preview
        or revert to snapshot.
        """
        domain = MutableDomainDescriptor(srcDomXML)
        engine_domain = DomainDescriptor(engine_xml)
        engine_md = metadata.Descriptor.from_xml(engine_xml)
        params = vmdevices.common.storage_device_params_from_domain_xml(
            self.id, engine_domain, engine_md, self.log)
        devices = [vmdevices.storage.Drive(self.log, **p) for p in params]

        with domain.metadata_descriptor() as domain_md:
            for element in domain.get_device_elements('disk'):
                if vmxml.attr(element, 'device') == 'disk':
                    change_disk(element, devices, self.log)

                    _, dev_class = identify_from_xml_elem(element)
                    attrs = dev_class.get_identifying_attrs(element)
                    if not attrs:
                        self.log.warning(
                            'cannot update metadata for disk %s: '
                            'missing attributes', xmlutils.tostring(element))
                        continue

                    metadata.replace_device(domain_md, engine_md, attrs)

        return domain.xml

    def _correctGraphicsConfiguration(self, domXML):
        """
        Fix the configuration of graphics device after resume.
        Make sure the ticketing settings are right
        """

        domObj = ET.fromstring(domXML)
        for devXml in domObj.findall('.//devices/graphics'):
            vmdevices.graphics.reset_password(devXml)
        return xmlutils.tostring(domObj)

    @api.guard(_not_migrating)
    def hotplugNic(self, params):
        xml = params['xml']
        nic = vmdevices.common.dev_from_xml(self, xml)
        dom = xmlutils.fromstring(xml)
        dom_devices = vmxml.find_first(dom, 'devices')
        nic_dom = next(iter(dom_devices))
        nicXml = xmlutils.tostring(nic_dom)
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
            return response.error('hotplugNic', str(e))
        else:
            # FIXME!  We may have a problem here if vdsm dies right after
            # we sent command to libvirt and before save conf. In this case
            # we will gather almost all needed info about this NIC from
            # the libvirt during recovery process.
            device_conf = self._devices[hwclass.NIC]
            device_conf.append(nic)
            self._hotplug_device_metadata(hwclass.NIC, nic)
            self._updateDomainDescriptor()
            vmdevices.network.Interface.update_device_info(self, device_conf)
            hooks.after_nic_hotplug(nicXml, self._custom,
                                    params=nic.custom)

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
            nic_element = xmlutils.fromstring(nicXml)
            vmxml.replace_first_child(dom_devices, nic_element)
            hotunplug_params = {'xml': xmlutils.tostring(dom)}
            self.hotunplugNic(hotunplug_params,
                              port_mirroring=mirroredNetworks)
            return response.error('hotplugNic', str(e))

        device_info = {'devices': [{'macAddr': nic.macAddr,
                                    'alias': nic.alias,
                                    }],
                       'xml': self._domain.xml,
                       }
        return {'status': doneCode, 'vmList': device_info}

    def _hotunplug_device(self, device_xml, device, device_hwclass,
                          update_metadata=False):
        try:
            self._dom.detachDevice(device_xml)
            self._waitForDeviceRemoval(device)
        except HotunplugTimeout as e:
            self.log.error('%s', e)
            raise
        except libvirt.libvirtError as e:
            self.log.exception('Hotunplug failed: %s', device_xml)
            if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                raise exception.NoSuchVM(vmId=self.id)
            raise
        if update_metadata:
            self._hotunplug_device_metadata(device_hwclass, device)
            self._updateDomainDescriptor()

    @api.guard(_not_migrating)
    # This hot plug must be able to take multiple devices so that
    # IOMMU placeholders and/or different devices in shared groups can
    # be added.
    def hotplugHostdev(self, device_xmls):
        for xml in device_xmls:
            _cls, dom, meta = vmdevices.common.dev_elems_from_xml(self, xml)
            try:
                vmdevices.hostdevice.setup_device(dom, meta, self.log)
            except libvirt.libvirtError:
                # We couldn't detach one of the devices. Halt.
                # No cleanup needed, detaching a detached device is noop.
                self.log.exception('Could not detach a device from a host: %s',
                                   xml)
                return response.error('hostdevDetachErr')
        assigned_devices = []
        for xml in device_xmls:
            self.log.info("Hotplug hostdev xml: %s", xml)
            _cls, dom, _meta = vmdevices.common.dev_elems_from_xml(self, xml)
            dev_xml = xmlutils.tostring(dom)
            try:
                self._dom.attachDevice(dev_xml)
            except libvirt.libvirtError:
                self.log.exception('Skipping device %s.', dev_xml)
                continue
            self._updateDomainDescriptor()
            assigned_devices.append(xml)
        return response.success(assignedDevices=assigned_devices)

    @api.guard(_not_migrating)
    def hotunplugHostdev(self, device_xmls):
        unplugged_devices = []
        # TODO: Hot unplug the devices concurrently?
        for xml in device_xmls:
            cls, dom, meta = vmdevices.common.dev_elems_from_xml(self, xml)
            alias = vmdevices.core.find_device_alias(dom)
            dev_object = cls(dom, meta, self.log)
            if alias:
                self._hotunplugged_devices[alias] = dev_object
            dev_xml = xmlutils.tostring(dom)
            try:
                self._hotunplug_device(dev_xml, dev_object, hwclass.HOSTDEV)
            except (HotunplugTimeout, libvirt.libvirtError):
                continue
            unplugged_devices.append(xml)
        return response.success(unpluggedDevices=unplugged_devices)

    def _lookupDeviceByPath(self, path):
        for dev in self._devices[hwclass.DISK][:]:
            try:
                if dev.path == path:
                    return dev
            except AttributeError:
                continue
        raise LookupError('Device instance for device with path {0} not found'
                          ''.format(path))

    def _updateInterfaceDevice(self, params):
        try:
            netDev = vmdevices.lookup.device_by_alias(
                self._devices[hwclass.NIC][:], params['alias'])

            linkValue = 'up' if conv.tobool(
                params.get('linkActive', netDev.linkActive)) else 'down'
            network = params.get('network', netDev.network)
            if network == '':
                network = net_api.DUMMY_BRIDGE
                linkValue = 'down'
            custom = params.get('custom', {})
            specParams = params.get('specParams')
            MTU = params.get('mtu', netDev.mtu)
            netsToMirror = params.get('portMirroring', netDev.portMirroring)

            with self.setLinkAndNetwork(netDev, linkValue, network,
                                        custom, specParams, MTU):
                with self.updatePortMirroring(netDev, netsToMirror):
                    self._hotplug_device_metadata(hwclass.NIC, netDev)
                    return {'vmList': {}}
        except (LookupError,
                SetLinkAndNetworkError,
                UpdatePortMirroringError) as e:
            raise exception.UpdateDeviceFailed(str(e))

    @contextmanager
    def setLinkAndNetwork(self, dev, linkValue, networkValue, custom,
                          specParams=None, MTU=None):
        vnicXML = dev.getXML()
        source = vmxml.find_first(vnicXML, 'source')
        vmxml.set_attr(source, 'bridge', networkValue)
        try:
            link = vmxml.find_first(vnicXML, 'link')
        except vmxml.NotFound:
            link = vnicXML.appendChildWithArgs('link')
        vmxml.set_attr(link, 'state', linkValue)
        if MTU is not None:
            try:
                mtu = vmxml.find_first(vnicXML, 'mtu')
            except vmxml.NotFound:
                mtu = vnicXML.appendChildWithArgs('mtu')
            vmxml.set_attr(mtu, 'size', str(MTU))
        vmdevices.network.update_bandwidth_xml(dev, vnicXML, specParams)
        vnicStrXML = xmlutils.tostring(vnicXML, pretty=True)
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
                self.log.warning(
                    'Request failed: %s', vnicStrXML, exc_info=True)
                hooks.after_update_device_fail(
                    vnicStrXML, self._custom, custom
                )
                raise SetLinkAndNetworkError(str(e))
            yield
        except Exception:
            # Rollback link and network.
            self.log.warning('Rolling back link and net for: %s', dev.alias,
                             exc_info=True)
            self._dom.updateDeviceFlags(xmlutils.tostring(vnicXML),
                                        libvirt.VIR_DOMAIN_AFFECT_LIVE)
            raise
        else:
            # Update the device and the configuration.
            dev.network = networkValue
            dev.linkActive = linkValue == 'up'
            dev.custom = custom
            dev.mtu = MTU

    @contextmanager
    def updatePortMirroring(self, nic, networks):
        devName = nic.name
        netsToDrop = [net for net in nic.portMirroring
                      if net not in networks]
        netsToAdd = [net for net in networks
                     if net not in nic.portMirroring]
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
            nic.portMirroring = networks

    def _updateGraphicsDevice(self, params):
        graphics = self._findGraphicsDeviceXMLByType(params['graphicsType'])
        if graphics is None:
            raise exception.UpdateDeviceFailed()

        result = self._setTicketForGraphicDev(
            graphics,
            params['password'],
            params['ttl'],
            params.get('existingConnAction'),
            params.get('disconnectAction'),
            params['params']
        )

        result['vmList'] = {}
        return result

    def updateDevice(self, params):
        # this is not optimal, but to properly support XML, we would need
        # to rewrite the flow from the ground up.
        desired_xml = params.get('xml', None)
        dev_params = params

        if params.get('deviceType') == hwclass.NIC:
            if desired_xml is not None:
                nic = vmdevices.common.dev_from_xml(self, desired_xml)
                dev_params = nic.update_params()
            return self._updateInterfaceDevice(dev_params)

        if params.get('deviceType') == hwclass.GRAPHICS:
            if desired_xml is not None:
                # problem here is `params', which can be anything.
                # to support this with XML, we need to figure out
                # how to pass them.
                raise exception.MethodNotImplemented()
            return self._updateGraphicsDevice(params)

        raise exception.MethodNotImplemented()

    @api.guard(_not_migrating)
    def hotunplugNic(self, params, port_mirroring=None):
        xml = params.get('xml')
        try:
            nic = lookup.device_from_xml_alias(
                self._devices[hwclass.NIC][:], xml)
        except LookupError:
            nic = vmdevices.common.dev_from_xml(self, xml)

        nicParams = {'macAddr': nic.macAddr}
        if port_mirroring is None:
            port_mirroring = nic.portMirroring

        if nic:
            if port_mirroring is not None:
                for network in port_mirroring:
                    supervdsm.getProxy().unsetPortMirroring(network, nic.name)

            nicXml = xmlutils.tostring(nic.getXML(), pretty=True)
            hooks.before_nic_hotunplug(nicXml, self._custom,
                                       params=nic.custom)
            # TODO: this is debug information. For 3.6.x we still need to
            # see the XML even with 'info' as default level.
            self.log.info("Hotunplug NIC xml: %s", nicXml)
        else:
            self.log.error("Hotunplug NIC failed - NIC not found: %s",
                           nicParams)
            return response.error('hotunplugNic', "NIC not found")

        try:
            self._hotunplug_device(nicXml, nic, hwclass.NIC,
                                   update_metadata=True)
        except (HotunplugTimeout, libvirt.libvirtError) as e:
            hooks.after_nic_hotunplug_fail(nicXml, self._custom,
                                           params=nic.custom)
            return response.error('hotunplugNic', str(e))

        hooks.after_nic_hotunplug(nicXml, self._custom,
                                  params=nic.custom)
        return {'status': doneCode, 'vmList': {}}

    def _update_mem_guaranteed_size(self, params):
        if 'memGuaranteedSize' in params:
            self._mem_guaranteed_size_mb = params["memGuaranteedSize"]
            self._balloon_minimum = self._mem_guaranteed_size_mb * 1024
            self._update_metadata()

    def update_guest_agent_api_version(self):
        self._guest_agent_api_version = self.guestAgent.effectiveApiVersion
        self._update_metadata()
        return self._guest_agent_api_version

    @api.guard(_not_migrating)
    def hotplugMemory(self, params):
        device_xml = params.get('xml')
        if device_xml is None:
            mem_params = params.get('memory', {})
            device_xml = vmdevices.core.memory_xml(mem_params)
        device_xml = hooks.before_memory_hotplug(device_xml, self._custom)
        self.log.debug("Hotplug memory xml: %s", device_xml)

        try:
            self._dom.attachDevice(device_xml)
        except libvirt.libvirtError as e:
            self.log.exception("hotplugMemory failed")
            if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                raise exception.NoSuchVM()
            return response.error('hotplugMem', str(e))

        self._updateDomainDescriptor()
        self._update_mem_guaranteed_size(params)
        hooks.after_memory_hotplug(device_xml, self._custom)

        return {'status': doneCode, 'vmList': {}}

    @api.guard(_not_migrating)
    def hotunplugMemory(self, params):
        device_xml = params.get('xml')
        if device_xml is None:
            mem_params = params['memory']
            device_xml = vmdevices.core.memory_xml(mem_params)
        self.log.info("Hotunplug memory xml: %s", device_xml)

        try:
            self._dom.detachDevice(device_xml)
        except libvirt.libvirtError as e:
            if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                raise exception.NoSuchVM(vmId=self.id)
            raise exception.HotunplugMemFailed(str(e), vmId=self.id)

        self._update_mem_guaranteed_size(params)

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
            return response.error('setNumberOfCpusErr', str(e))

        self._updateDomainDescriptor()
        hooks.after_set_num_of_cpus()
        return {'status': doneCode, 'vmList': {}}

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

                    disk = self.findDriveByUUIDs({
                        'domainID': ioTune["domainID"],
                        'poolID': ioTune["poolID"],
                        'imageID': ioTune["imageID"],
                        'volumeID': ioTune["volumeID"]})

                    self.log.debug("Device path: %s", disk.path)
                    ioTune["name"] = disk.name
                    ioTune["path"] = disk.path

                except LookupError as e:
                    return response.error('updateVmPolicyErr', str(e))

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
            self.log.warning("updateVmPolicy got unknown parameters: %s",
                             ", ".join(six.iterkeys(params)))

        #
        # Save modified metadata

        if metadata_modified:
            metadata_xml = xmlutils.tostring(qos)

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
                    return response.error('updateVmPolicyErr', str(e))

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

        metadata = xmlutils.fromstring(metadata_xml)
        return vmxml.find_first(
            metadata,
            xmlconstants.METADATA_VM_TUNE_ELEMENT,
            None)

    def find_device_by_name_or_path(self, device_name=None, device_path=None):
        for device in self._devices[hwclass.DISK]:
            if not isVdsmImage(device):
                continue
            if device_name is not None and device.name == device_name:
                return device
            if (device_path is not None and
                    (device.get("path") == device_path)):
                return device

        raise LookupError(
            "No such disk {} with path {}".format(
                device_name, device_path))

    def io_tune_policy_values(self):
        try:
            return {
                'policy': self.io_tune_policy(),
                'current_values': self.io_tune_values(),
            }
        except virdomain.NotConnectedError:
            # race on shutdown
            return {}
        except exception.UpdateIOTuneError:
            return {}

    def io_tune_policy(self):
        return utils.picklecopy(self._ioTuneInfo)

    def io_tune_values(self):
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
                    self.log.error('updateIoTuneErr', str(e))
                    raise exception.UpdateIOTuneError(str(e))

        return resultList

    def setIoTune(self, tunables):
        for io_tune_change in tunables:
            device_name = io_tune_change.get('name', None)
            device_path = io_tune_change.get('path', None)
            io_tune = io_tune_change['ioTune']

            try:
                # Find the proper device object
                found_device = self.find_device_by_name_or_path(
                    device_name, device_path)
            except LookupError:
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
                    raise exception.UpdateIOTuneError(str(e))

            with self._ioTuneLock:
                self._ioTuneValues[found_device.name] = io_tune

            # TODO: improve once libvirt gets support for iotune events
            #       see https://bugzilla.redhat.com/show_bug.cgi?id=1114492
            found_device.iotune = io_tune

            # Make sure the cached XML representation is valid as well
            xml = xmlutils.tostring(found_device.getXML())
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
            operation = qemuimg.create(transientPath,
                                       format=qemuimg.FORMAT.QCOW2,
                                       qcow2Compat=sdDom.qcow2_compat(),
                                       backing=diskParams['path'],
                                       backingFormat=driveFormat)
            operation.run()
            os.fchmod(transientHandle, 0o660)
        except Exception:
            self.log.info("Unlinking transient disk volume %r", transientPath)
            os.unlink(transientPath)  # Closing after deletion is correct
            self.log.exception("Failed to create the transient disk for "
                               "volume %s", diskParams['volumeID'])
        finally:
            os.close(transientHandle)

        diskParams['diskType'] = DISK_TYPE.FILE
        diskParams['path'] = transientPath
        diskParams['format'] = 'cow'

    def _removeTransientDisk(self, drive):
        if drive.transientDisk:
            self.log.info("Unlinking transient disk %r", drive.path)
            os.unlink(drive.path)

    @api.guard(_not_migrating)
    def hotplugDisk(self, params):
        xml = params.get('xml')
        _cls, elem, meta = vmdevices.common.dev_elems_from_xml(self, xml)
        diskParams = storagexml.parse(elem, meta)
        diskParams['path'] = self.cif.prepareVolumePath(diskParams)

        if isVdsmImage(diskParams):
            self._normalizeVdsmImg(diskParams)
            self._createTransientDisk(diskParams)

        self.updateDriveIndex(diskParams)
        drive = vmdevices.storage.Drive(self.log, **diskParams)

        if drive.hasVolumeLeases:
            return response.error('noimpl')

        driveXml = xmlutils.tostring(drive.getXML(), pretty=True)
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
            return response.error('hotplugDisk', str(e))
        else:
            device_conf = self._devices[hwclass.DISK]
            device_conf.append(drive)

            with self._confLock:
                self.conf['devices'].append(diskParams)

            self._hotplug_device_metadata(hwclass.DISK, drive)

            self._updateDomainDescriptor()
            vmdevices.storage.Drive.update_device_info(self, device_conf)
            hooks.after_disk_hotplug(driveXml, self._custom,
                                     params=drive.custom)

        return {'status': doneCode, 'vmList': {}}

    @api.guard(_not_migrating)
    def hotunplugDisk(self, params):
        diskParams = {}
        drive = None
        xml = params.get('xml')
        try:
            drive = lookup.device_from_xml_alias(
                self._devices[hwclass.DISK][:], xml)
        except LookupError:
            _, elem, meta = vmdevices.common.dev_elems_from_xml(self, xml)
            diskParams = storagexml.parse(elem, meta)

        if drive is None:
            # needed to find network drives
            diskParams['path'] = self.cif.prepareVolumePath(diskParams)
            try:
                drive = self.findDriveByUUIDs(diskParams)
            except LookupError:
                self.log.error("Hotunplug disk failed - Disk not found: %s",
                               diskParams)
                return response.error('hotunplugDisk', "Disk not found")

        if drive.hasVolumeLeases:
            return response.error('noimpl')

        driveXml = xmlutils.tostring(drive.getXML(), pretty=True)
        # TODO: this is debug information. For 3.6.x we still need to
        # see the XML even with 'info' as default level.
        self.log.info("Hotunplug disk xml: %s", driveXml)

        hooks.before_disk_hotunplug(driveXml, self._custom,
                                    params=drive.custom)
        try:
            self._hotunplug_device(driveXml, drive, hwclass.DISK,
                                   update_metadata=True)
        except HotunplugTimeout as e:
            return response.error('hotunplugDisk', "%s" % e)
        except libvirt.libvirtError as e:
            return response.error('hotunplugDisk', str(e))
        else:
            # Find and remove disk device from vm's conf
            for dev in self.conf['devices'][:]:
                if dev['type'] == hwclass.DISK and dev['path'] == drive.path:
                    with self._confLock:
                        self.conf['devices'].remove(dev)
                    break

            hooks.after_disk_hotunplug(driveXml, self._custom,
                                       params=drive.custom)
            self._cleanupDrives(drive)

        return {'status': doneCode, 'vmList': {}}

    @api.guard(_not_migrating)
    def hotplugLease(self, params):
        # REQUIRED_FOR: Vdsm and Engine < 4.2, for the migration flow.
        # Why it breaks: on destination side we call
        # vmdevices.common.update_device_info
        # Vdsm looks for the key, Engine doesn't send it - but according to
        # the schema, Engine is not required to do so.
        # See rhbz#1590063 for more details.
        if 'device' not in params:
            params['device'] = hwclass.LEASE

        vmdevices.lease.prepare(self.cif.irs, [params])
        lease = vmdevices.lease.Device(self.log, **params)

        leaseXml = xmlutils.tostring(lease.getXML(), pretty=True)
        self.log.info("Hotplug lease xml: %s", leaseXml)

        try:
            self._dom.attachDevice(leaseXml)
        except libvirt.libvirtError as e:
            # TODO: repeated in many places, move to domain wrapper?
            if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                raise exception.NoSuchVM(vmId=self.id)
            raise exception.HotplugLeaseFailed(reason=str(e), lease=lease)

        self._devices[hwclass.LEASE].append(lease)
        self._updateDomainDescriptor()
        return response.success(vmList={})

    @api.guard(_not_migrating)
    def hotunplugLease(self, params):
        try:
            lease = vmdevices.lease.find_device(self._devices, params)
        except LookupError:
            raise exception.HotunplugLeaseFailed(reason="No such lease",
                                                 lease=params)

        leaseXml = xmlutils.tostring(lease.getXML(), pretty=True)
        self.log.info("Hotunplug lease xml: %s", leaseXml)

        try:
            self._hotunplug_device(leaseXml, lease, hwclass.LEASE)
        except HotunplugTimeout as e:
            raise exception.HotunplugLeaseFailed(reason=str(e), lease=lease)
        except libvirt.libvirtError as e:
            raise exception.HotunplugLeaseFailed(reason=str(e), lease=lease)

        # libvirt doesn't generate a device removal event on lease hot
        # unplug, so we must update domain descriptor here.
        # See https://bugzilla.redhat.com/1639228.
        self._updateDomainDescriptor()

        return response.success(vmList={})

    def _device_removed(self, device, sleep_time):
        if isinstance(device, vmdevices.lease.Device):
            # libvirt doesn't generate a device removal event on lease hot
            # unplug.  See https://bugzilla.redhat.com/1639228.
            time.sleep(sleep_time)
            return not vmdevices.lease.is_attached_to(device,
                                                      self._dom.XMLDesc(0))
        else:
            return device.hotunplug_event.wait(sleep_time)

    def _waitForDeviceRemoval(self, device):
        self.log.debug("Waiting for hotunplug to finish")
        with utils.stopwatch("Hotunplug %r" % device):
            deadline = (vdsm.common.time.monotonic_time() +
                        config.getfloat('vars', 'hotunplug_timeout'))
            sleep_time = config.getfloat('vars', 'hotunplug_check_interval')
            while not self._device_removed(device, sleep_time):
                if vdsm.common.time.monotonic_time() > deadline:
                    raise HotunplugTimeout("Timeout detaching %r" % device)

    def _readPauseCode(self):
        state, reason = self._dom.state(0)

        if (state == libvirt.VIR_DOMAIN_PAUSED and
           reason == libvirt.VIR_DOMAIN_PAUSED_IOERROR):

            diskErrors = self._dom.diskErrors()
            for device, error in six.viewitems(diskErrors):
                if error == libvirt.VIR_DOMAIN_DISK_ERROR_NO_SPACE:
                    self.log.warning('device %s out of space', device)
                    return 'ENOSPC'
                elif error == libvirt.VIR_DOMAIN_DISK_ERROR_UNSPEC:
                    self.log.warning('device %s reported I/O error',
                                     device)
                    return 'EIO'
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
        elif self._monitorable:
            self._monitorResponse = 0

    def _completeIncomingMigration(self):
        if self._altered_state.origin == _FILE_ORIGIN:
            self.cont(guestTimeSync=True)
            fromSnapshot = self._altered_state.from_snapshot
            self._altered_state = _AlteredState()
            hooks.after_vm_dehibernate(self._dom.XMLDesc(0), self._custom,
                                       {'FROM_SNAPSHOT': fromSnapshot})
        elif self._altered_state.origin == _MIGRATION_ORIGIN:
            finished, timeout = self._waitForUnderlyingMigration()
            if self._destroy_requested.is_set():
                try:
                    dom = self._connection.lookupByUUIDString(self.id)
                    dom.destroyFlags()
                except libvirt.libvirtError as e:
                    self.log.warning("Couldn't destroy incoming VM: %s", e)
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
        self._update_metadata()   # to store agent API version
        self._updateDomainDescriptor()
        self.log.info("End of migration")

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
            self._sync_metadata()

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

        if not self._dom.isPersistent():
            # The domain may not be persistent if it was migrated from old
            # Vdsm or if the migration couldn't be certain that persistent
            # domains are universally available in the cluster.  Then we
            # need to make the domain persistent.  If that fails (due to
            # unexpected circumstances or when a block job is running), we
            # don't want to block further operations and we can live with
            # the transient domain.
            self.log.debug("Switching transient VM to persistent")
            try:
                self._connection.defineXML(self._dom.XMLDesc(0))
            except libvirt.libvirtError as e:
                self.log.info("Failed to make VM persistent: %s'", e)

    def _underlyingCont(self):
        hooks.before_vm_cont(self._dom.XMLDesc(0), self._custom)
        self._dom.resume()

    def _underlyingPause(self):
        hooks.before_vm_pause(self._dom.XMLDesc(0), self._custom)
        self._dom.suspend()

    def findDriveByUUIDs(self, drive):
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

        volSize = self._getVolumeSize(
            vmDrive.domainID, vmDrive.poolID, vmDrive.imageID,
            vmDrive.volumeID)

        vmDrive.truesize = volSize.truesize
        vmDrive.apparentsize = volSize.apparentsize

    def updateDriveParameters(self, driveParams):
        """Update the drive with the new volume information"""

        # Updating the vmDrive object
        for vmDrive in self._devices[hwclass.DISK][:]:
            if vmDrive.name == driveParams["name"]:
                with self._md_desc.device(
                        devtype=vmDrive.type, name=vmDrive.name
                ) as dev:
                    for k, v in six.iteritems(driveParams):
                        setattr(vmDrive, k, v)
                        # only a subset of driveParams is relevant to
                        # metadata (e.g. drive IDs). Skip fields that
                        # don't belong to metadata.
                        if k in dev:
                            dev[k] = v
                self._sync_metadata()
                break
        else:
            self.log.error("Unable to update the drive object for: %s",
                           driveParams["name"])

        # Updating the VM configuration
        try:
            conf = self._findDriveConfigByName(driveParams["name"])
        except LookupError:
            self.log.error("Unable to update the device configuration "
                           "for disk %s", driveParams["name"])
        else:
            with self._confLock:
                conf.update(driveParams)

    def clear_drive_threshold(self, drive, old_volume_id):
        # Check that libvirt exposes full volume chain information
        chains = self._driveGetActualVolumeChain([drive])
        if drive['alias'] not in chains:
            self.log.error(
                "libvirt does not support volume chain "
                "monitoring.  Unable to update threshold for %s.",
                drive.name)
            return

        actual_chain = chains[drive['alias']]

        try:
            target_index = drive.volume_target_index(
                old_volume_id, actual_chain)
        except VolumeNotFound as e:
            self.log.error(
                "Unable to find the target index for %s: %s", old_volume_id, e)
            return

        try:
            self.drive_monitor.clear_threshold(drive, index=target_index)
        except libvirt.libvirtError as e:
            self.log.error(
                "Unable to clear the drive threshold: %s", e)

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

    @backup.requires_libvirt_support()
    @api.guard(_not_migrating)
    def start_backup(self, config):
        dom = backup.DomainAdapter(self)
        return backup.start_backup(self, dom, config)

    @backup.requires_libvirt_support()
    @api.guard(_not_migrating)
    def stop_backup(self, backup_id):
        dom = backup.DomainAdapter(self)
        return backup.stop_backup(self, dom, backup_id=backup_id)

    @backup.requires_libvirt_support()
    @api.guard(_not_migrating)
    def backup_info(self, backup_id, checkpoint_id=None):
        dom = backup.DomainAdapter(self)
        return backup.backup_info(
            self, dom, backup_id=backup_id, checkpoint_id=checkpoint_id)

    @backup.requires_libvirt_support()
    @api.guard(_not_migrating)
    def delete_checkpoints(self, checkpoint_ids):
        dom = backup.DomainAdapter(self)
        return backup.delete_checkpoints(
            self, dom, checkpoint_ids=checkpoint_ids)

    @backup.requires_libvirt_support()
    @api.guard(_not_migrating)
    def redefine_checkpoints(self, checkpoints):
        dom = backup.DomainAdapter(self)
        return backup.redefine_checkpoints(
            self, dom, checkpoints=checkpoints)

    @api.guard(_not_migrating)
    def snapshot(self, snap_drives, memory_params, frozen,
                 job_uuid, recovery=False, timeout=30):
        job_id = job_uuid or str(uuid.uuid4())
        job = snapshot.Job(self, snap_drives, memory_params,
                           frozen, job_id, recovery, timeout)
        jobs.add(job)
        vdsm.virt.jobs.schedule(job)
        return {'status': doneCode}

    def diskReplicateStart(self, srcDisk, dstDisk):
        try:
            drive = self.findDriveByUUIDs(srcDisk)
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
            diskType = replica.get("diskType")
            replica['path'] = self.cif.prepareVolumePath(replica)
            if diskType != replica["diskType"]:
                # Disk type was detected or modified when preparing the volume.
                # Persist it so migration can continue after vdsm crash.
                self._updateDiskReplica(drive)
            try:
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

    def diskReplicateFinish(self, srcDisk, dstDisk):
        try:
            drive = self.findDriveByUUIDs(srcDisk)
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
            raise exception.ReplicationNotInProgress(vmId=self.id,
                                                     driveName=drive.name,
                                                     srcDisk=srcDisk)

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
        dstDiskCopy.update({'device': drive.device, 'name': drive.name,
                            'type': drive.type})
        dstDiskCopy['path'] = self.cif.prepareVolumePath(dstDiskCopy)

        if "diskType" not in dstDiskCopy:
            dstDiskCopy["diskType"] = drive.diskReplicate["diskType"]

        if srcDisk != dstDisk:
            self.log.debug("Stopping the disk replication switching to the "
                           "destination drive: %s", dstDisk)
            blockJobFlags = libvirt.VIR_DOMAIN_BLOCK_JOB_ABORT_PIVOT
            diskToTeardown = srcDisk

            # We need to stop monitoring drives in order to avoid spurious
            # errors from the stats threads during the switch from the old
            # drive to the new one. This applies only to the case where we
            # actually switch to the destination.
            self.drive_monitor.disable()
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
            return response.error('changeDisk')  # Finally is evaluated
        else:
            try:
                try:
                    self.cif.teardownVolumePath(diskToTeardown)
                except Exception:
                    # There is nothing we can do at this point other
                    # than logging
                    self.log.exception("Unable to teardown the previous chain:"
                                       " %s", diskToTeardown)
                self.updateDriveParameters(dstDiskCopy)
                try:
                    self.updateDriveVolume(drive)
                except StorageUnavailableError as e:
                    # Will be recovered on the next monitoring cycle
                    self.log.error("Unable to update drive %r volume size: "
                                   "%s", drive.name, e)
            finally:
                self._delDiskReplica(drive)

        finally:
            self.drive_monitor.enable()

        return response.success()

    def _startDriveReplication(self, drive):
        destxml = xmlutils.tostring(drive.getReplicaXML())
        self.log.debug("Replicating drive %s to %s", drive.name, destxml)

        flags = (libvirt.VIR_DOMAIN_BLOCK_COPY_SHALLOW |
                 libvirt.VIR_DOMAIN_BLOCK_COPY_REUSE_EXT |
                 libvirt.VIR_DOMAIN_BLOCK_COPY_TRANSIENT_JOB)

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

        self._persist_drive_replica(drive, replica)
        drive.diskReplicate = replica

    def _updateDiskReplica(self, drive):
        """
        Update the persisted copy of drive replica.
        """
        if not drive.isDiskReplicationInProgress():
            raise RuntimeError("Disk '%s' does not have an ongoing "
                               "replication" % drive.name)

        self._persist_drive_replica(drive, drive.diskReplicate)

    def _delDiskReplica(self, drive):
        """
        This utility method is the inverse of _setDiskReplica, look at the
        _setDiskReplica description for more information.
        """
        del drive.diskReplicate

        with self._confLock:
            with self._md_desc.device(
                    devtype=drive.type, name=drive.name
            ) as dev:
                del dev['diskReplicate']

        self._sync_metadata()

    def _persist_drive_replica(self, drive, replica):
        with self._confLock:
            with self._md_desc.device(
                    devtype=drive.type, name=drive.name
            ) as dev:
                dev['diskReplicate'] = replica

        self._sync_metadata()

    def _diskSizeExtendCow(self, drive, newSizeBytes):
        try:
            # Due to an old bug in libvirt (BZ#963881) this call used to be
            # broken for NFS domains when root_squash was enabled.  This has
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
            'name': drive.name,
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
            self.log.warning(
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
            drive = self.findDriveByUUIDs(driveSpecs)
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

    def changeCD(self, cdromspec):
        drivespec = cdromspec['path']
        blockdev = drivename.make(
            cdromspec['iface'], cdromspec['index'])
        iface = cdromspec['iface']
        return self._changeBlockDev('cdrom', blockdev, drivespec, iface,
                                    force=bool(drivespec))

    def changeFloppy(self, drivespec):
        return self._changeBlockDev('floppy', 'fda', drivespec)

    def _changeBlockDev(self, vmDev, blockdev, drivespec, iface=None,
                        force=True):
        try:
            path = self.cif.prepareVolumePath(drivespec)
        except VolumeError:
            raise exception.ImageFileNotFound()

        diskelem = vmxml.Element('disk', type='file', device=vmDev)
        diskelem.appendChildWithArgs('source', file=path)

        target = {'dev': blockdev}
        if iface:
            target['bus'] = iface

        diskelem.appendChildWithArgs('target', **target)
        diskelem_xml = xmlutils.tostring(diskelem)

        self.log.info("changeBlockDev: using disk XML: %s", diskelem_xml)

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
                raise exception.ChangeDiskFailed()

        if vmDev in self.conf:
            self.cif.teardownVolumePath(self.conf[vmDev])

        return {'vmList': {}}

    def setTicket(self, otp, seconds, connAct, params):
        """
        setTicket defaults to the first graphic device.
        use updateDevice to select the device.
        """
        try:
            graphics = next(self._domain.get_device_elements('graphics'))
        except StopIteration:
            raise exception.SpiceTicketError(
                'no graphics devices configured')
        return self._setTicketForGraphicDev(
            graphics, otp, seconds, connAct, None, params)

    def _check_fips_params_valid(self, params):
            if 'fips' in params and \
               params.get('fips') not in ['true', 'false']:
                raise exception.MissingParameter(
                    'fips param should either be "true", '
                    '"false" or non-existent')

            fips = conv.tobool(params.get('fips'))
            if fips and params.get('vncUsername') is None:
                raise exception.GeneralException(
                    'FIPS mode requires vncUsername')

    def _setTicketForGraphicDev(self, graphics, otp, seconds, connAct,
                                disconnectAction, params):
        if vmxml.attr(graphics, 'type') == 'vnc':
            self._check_fips_params_valid(params)

            vnc_username = params.get('vncUsername')
            fips = conv.tobool(params.get('fips'))

            if fips:
                saslpasswd2.set_vnc_password(vnc_username, otp.value)
            elif vnc_username is not None:
                saslpasswd2.remove_vnc_password(vnc_username)

        vmxml.set_attr(graphics, 'passwd', otp.value)
        if int(seconds) > 0:
            validto = time.strftime('%Y-%m-%dT%H:%M:%S',
                                    time.gmtime(time.time() + float(seconds)))
            vmxml.set_attr(graphics, 'passwdValidTo', validto)
        if connAct is not None and vmxml.attr(graphics, 'type') == 'spice':
            vmxml.set_attr(graphics, 'connected', connAct)
        hooks.before_vm_set_ticket(self._domain.xml, self._custom, params)
        try:
            self._dom.updateDeviceFlags(xmlutils.tostring(graphics), 0)
            self._consoleDisconnectAction = disconnectAction or \
                ConsoleDisconnectAction.LOCK_SCREEN
        except virdomain.TimeoutError as tmo:
            raise exception.SpiceTicketError(six.text_type(tmo))

        else:
            hooks.after_vm_set_ticket(self._domain.xml, self._custom, params)
            return {}

    def reviveTicket(self, newlife):
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
        self._dom.updateDeviceFlags(xmlutils.tostring(graphics), 0)

    def _findGraphicsDeviceXMLByType(self, deviceType):
        """
        libvirt (as in 1.2.3) supports only one graphic device per type
        """
        dom_desc = DomainDescriptor(
            self._dom.XMLDesc(libvirt.VIR_DOMAIN_XML_SECURE))
        try:
            return next(dom_desc.get_device_elements_with_attrs(
                hwclass.GRAPHICS, type=deviceType))
        except StopIteration:
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
            self._pause_code = reason
            self._pause_time = vdsm.common.time.monotonic_time()
            self._setGuestCpuRunning(False, flow='IOError')
            self._logGuestCpuStatus('onIOError')
            if reason == 'ENOSPC':
                if not self.monitor_drives():
                    self.log.info("No VM drives were extended")

            self._send_ioerror_status_event(reason, blockDevAlias)
            self._update_metadata()

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
            drive = vmdevices.lookup.device_by_alias(
                self._devices[hwclass.DISK][:], alias)
        except LookupError:
            self.log.warning('unknown disk alias: %s', alias)
        else:
            io_error_info['name'] = drive.name
            io_error_info['path'] = drive.path

        self.send_status_event(pauseCode=reason, ioerror=io_error_info)

    @property
    def hasSpice(self):
        return bool(list(self._domain.get_device_elements_with_attrs(
            hwclass.GRAPHICS, type='spice')))

    @property
    def name(self):
        return self._domain.name

    def _updateDomainDescriptor(self, xml=None):
        domxml = self._dom.XMLDesc(0) if xml is None else xml
        self._domain = DomainDescriptor(domxml)

    def _updateMetadataDescriptor(self):
        # load will overwrite any existing content, as per doc.
        self._md_desc.load(self._dom)

    def _update_metadata(self):
        with self._md_desc.values() as vm:
            vm['startTime'] = self.start_time
            if self._guest_agent_api_version is not None:
                vm['guestAgentAPIVersion'] = self._guest_agent_api_version
            vm['destroy_on_reboot'] = self._destroy_on_reboot
            vm['memGuaranteedSize'] = self._mem_guaranteed_size_mb
            vm['balloonTarget'] = self._balloon_target
            if self._pause_time is None:
                try:
                    del vm['pauseTime']
                except KeyError:
                    pass
            else:
                vm['pauseTime'] = self._pause_time
            vm.update(self._exit_info)
            try:
                if not self._snapshot_job or \
                        not jobs.get(self._snapshot_job['jobUUID']).active:
                    try:
                        del vm['snapshot_job']
                    except KeyError:
                        pass
                else:
                    vm['snapshot_job'] = json.dumps(self._snapshot_job)
            except jobs.NoSuchJob:
                try:
                    del vm['snapshot_job']
                except KeyError:
                    # It been cleared by a different flow on the metadata.
                    pass
        self._sync_metadata()

    def save_custom_properties(self):
        if self.min_cluster_version(4, 2):
            return
        # In cluster versions 4.1 and before, we stored the
        # custom variables in the recovery file. Moving them
        # in the XML metadata.
        self._md_desc.add_custom(self._custom['custom'])

    def _sync_metadata(self):
        if self._external:
            return
        self._md_desc.dump(self._dom)

    def releaseVm(self, gracefulAttempts=1):
        """
        Stop VM and release all resources
        """

        # delete the payload devices
        for drive in self.payload_drives():
            try:
                supervdsm.getProxy().removeFs(drive.path)
            except:
                self.log.exception("Failed to remove a payload file")

        with self._releaseLock:
            if self._released.is_set():
                return response.success()

            # unsetting mirror network will clear both mirroring
            # (on the same network).
            for nic in self._devices[hwclass.NIC]:
                if hasattr(nic, 'name'):
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
        safe_to_force = True
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
            with self.cif.vm_container_lock:
                del self.cif.vmContainer[self.id]
        except KeyError:
            self.log.exception("Failed to delete VM %s", self.id)
        else:
            self._undefine_domain()
            self.log.debug("Total desktops after destroy of %s is %d",
                           self.id, len(self.cif.vmContainer))

    def destroy(self, gracefulAttempts=1,
                reason=vmexitreason.ADMIN_SHUTDOWN):
        self.log.debug('destroy Called')

        result = self.doDestroy(gracefulAttempts, reason)
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

    def qemuGuestAgentShutdown(self):
        with self._shutdownLock:
            self._shutdownReason = vmexitreason.ADMIN_SHUTDOWN
        try:
            self._dom.shutdownFlags(libvirt.VIR_DOMAIN_SHUTDOWN_GUEST_AGENT)
        except virdomain.NotConnectedError:
            # the VM was already shut off asynchronously,
            # so ignore error and quickly exit
            self.log.warning('failed to invoke qemuGuestAgentShutdown: '
                             'domain not connected')
            raise exception.VMIsDown()
        except libvirt.libvirtError:
            # it's likely QEMU GA is not installed or not responding
            logging.exception("Shutdown by QEMU Guest Agent failed")
            raise exception.NonResponsiveGuestAgent()

    def qemuGuestAgentReboot(self):
        try:
            self._dom.reboot(libvirt.VIR_DOMAIN_REBOOT_GUEST_AGENT)
        except virdomain.NotConnectedError:
            # the VM was already shut off asynchronously,
            # so ignore error and quickly exit
            self.log.warning('failed to invoke qemuGuestAgentReboot: '
                             'domain not connected')
            raise exception.VMIsDown()
        except libvirt.libvirtError:
            # it's likely QEMU GA is not installed or not responding
            logging.exception("Reboot by QEMU Guest Agent failed")
            raise exception.NonResponsiveGuestAgent()

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

    def setBalloonTarget(self, target):
        dev = next(self._domain.get_device_elements('memballoon'))
        if dev.attrib.get('model') == 'none':
            return

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
            raise exception.BalloonError(str(e))
        else:
            self._balloon_target = target
            self._update_metadata()

    def get_balloon_info(self):
        if self._balloon_minimum is None or self._balloon_target is None:
            # getStats() is called concurrently when the VM is being
            # created, in this case no balloon device is available
            return {}
        return {
            'target': self._balloon_target,
            'minimum': self._balloon_minimum,
        }

    def setCpuTuneQuota(self, quota):
        try:
            self._dom.setSchedulerParameters({'vcpu_quota': int(quota)})
        except ValueError:
            return response.error('cpuTuneErr',
                                  'an integer is required for period')
        except libvirt.libvirtError as e:
            return self._reportException(key='cpuTuneErr', msg=str(e))
        else:
            # libvirt may change the value we set, so we must get fresh data
            return self._updateVcpuTuneInfo()

    def setCpuTunePeriod(self, period):
        try:
            self._dom.setSchedulerParameters({'vcpu_period': int(period)})
        except ValueError:
            return response.error('cpuTuneErr',
                                  'an integer is required for period')
        except libvirt.libvirtError as e:
            return self._reportException(key='cpuTuneErr', msg=str(e))
        else:
            # libvirt may change the value we set, so we must get fresh data
            return self._updateVcpuTuneInfo()

    def _updateVcpuTuneInfo(self):
        try:
            self._vcpuTuneInfo = self._dom.schedulerParameters()
        except libvirt.libvirtError as e:
            return self._reportException(key='cpuTuneErr', msg=str(e))
        else:
            return {'status': doneCode}

    def _reportException(self, key, msg=None):
        """
        Convert an exception to an error status.
        This method should be called only within exception-handling context.
        """
        self.log.exception("Operation failed")
        return response.error(key, msg)

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
            self._handle_libvirt_domain_stopped(detail)
        elif event == libvirt.VIR_DOMAIN_EVENT_SUSPENDED:
            self._handle_libvirt_domain_suspended(detail)
        elif event == libvirt.VIR_DOMAIN_EVENT_RESUMED:
            self._handle_libvirt_domain_resumed(detail)
        elif event == libvirt.VIR_DOMAIN_EVENT_SHUTDOWN:
            self._handle_libvirt_domain_shutdown(detail)

    def _handle_libvirt_domain_stopped(self, detail):
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
            exit_code, reason = self._getShutdownReason()
            self._onQemuDeath(exit_code, reason)

    def _handle_libvirt_domain_suspended(self, detail):
        self._setGuestCpuRunning(False, flow='event.suspend')
        self._logGuestCpuStatus('onSuspend')
        if self.lastStatus == vmstatus.MIGRATION_DESTINATION and \
           detail == libvirt.VIR_DOMAIN_EVENT_SUSPENDED_PAUSED:
            self._incoming_migration_completed()
        elif detail in (
                libvirt.VIR_DOMAIN_EVENT_SUSPENDED_PAUSED,
                libvirt.VIR_DOMAIN_EVENT_SUSPENDED_IOERROR,
        ):
            if detail == libvirt.VIR_DOMAIN_EVENT_SUSPENDED_IOERROR:
                try:
                    self._pause_code = self._readPauseCode()
                except libvirt.libvirtError as e:
                    self.log.warning(
                        "Couldn't retrieve pause code from libvirt: %s", e)
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
            self._pause_code = 'POSTCOPY'
            self.send_status_event(pauseCode='POSTCOPY')
        elif detail == libvirt.VIR_DOMAIN_EVENT_SUSPENDED_POSTCOPY_FAILED:
            # This event may be received only on the destination.
            self.handle_failed_post_copy()

    def _handle_libvirt_domain_resumed(self, detail):
        self._setGuestCpuRunning(True, flow='event.resume')
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
                self._incoming_migration_completed()
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
            # running on the destination.  But libvirt doesn't
            # like when we operate the VM as running, since the
            # migration job is still running.  That prevents us
            # from running other libvirt jobs, such as metadata
            # updates, which can time out, fail, and cause (at
            # least) migration failure.
            # So let's just log the event here and keep waiting
            # for migration completion.  Let's also keep the VM
            # status as incoming migration, which is what Engine
            # expects while the VM is still migrating.
            self.log.info("Migration switched to post-copy mode")

    def _handle_libvirt_domain_shutdown(self, detail):
        # Do not overwrite existing shutdown reason
        if self._shutdownReason is None:
            with self._shutdownLock:
                if self._shutdownReason is None:
                    if detail == libvirt.VIR_DOMAIN_EVENT_SHUTDOWN_HOST:
                        self._shutdownReason = vmexitreason.HOST_SHUTDOWN
                    elif detail == libvirt.VIR_DOMAIN_EVENT_SHUTDOWN_GUEST:
                        self._shutdownReason = vmexitreason.USER_SHUTDOWN
                    else:
                        # If an unexpected 'detail' was received,
                        # warn the user and set the default value.
                        self.log.warning(
                            "Unexpected host/user shutdown detail from"
                            " libvirt: %s. Assuming user shutdown.", detail
                        )
                        self._shutdownReason = vmexitreason.USER_SHUTDOWN

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
                aliasToDevice[alias]._deviceXML = xmlutils.tostring(deviceXML)

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
        self._incoming_migration_prepared.wait(prepareTimeout)
        if not self._incoming_migration_prepared.isSet():
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

    def _incoming_migration_completed(self):
        self._incoming_migration_vm_running.set()
        self._incoming_migration_finished.set()

    def getBlockJob(self, drive):
        for job in self._blockJobs.values():
            if all([bool(drive[x] == job['disk'][x])
                    for x in ('imageID', 'domainID', 'volumeID')]):
                return job
        raise LookupError("No block job found for drive %r" % drive.name)

    def trackBlockJob(self, jobID, drive, base, top, strategy):
        driveSpec = dict((k, drive[k]) for k in
                         ('poolID', 'domainID', 'imageID', 'volumeID'))
        with self._confLock:
            try:
                job = self.getBlockJob(drive)
            except LookupError:
                newJob = {'jobID': jobID, 'disk': driveSpec,
                          'baseVolume': base, 'topVolume': top,
                          'strategy': strategy, 'blockJobType': 'commit',
                          'drive': drive.name}
                self._blockJobs[jobID] = newJob
            else:
                self.log.error("Cannot add block job %s.  A block job with id "
                               "%s already exists for image %s", jobID,
                               job['jobID'], drive['imageID'])
                raise BlockJobExistsError()
        self._sync_block_job_info()
        self._sync_metadata()
        self._updateDomainDescriptor()

    def untrackBlockJob(self, jobID):
        with self._confLock:
            try:
                del self._blockJobs[jobID]
            except KeyError:
                # If there was contention on the confLock, this may have
                # already been removed
                return False

        self._sync_disk_metadata()
        self._sync_block_job_info()
        self._sync_metadata()
        self._updateDomainDescriptor()
        return True

    def _sync_block_job_info(self):
        with self._md_desc.values() as vm:
            vm['block_jobs'] = json.dumps(self._blockJobs)

    def _sync_disk_metadata(self):
        for drive in self._devices[hwclass.DISK]:
            info = {}
            for key in ('volumeID', 'volumeChain', 'volumeInfo'):
                value = getattr(drive, key, None)
                if value is not None:
                    info[key] = utils.picklecopy(value)

            if not info:
                continue

            with self._confLock:
                with self._md_desc.device(
                    devtype=drive.type, name=drive.name
                ) as dev:
                    dev.update(info)

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
            return self._vmJobs is None or bool(self._blockJobs)

    def updateVmJobs(self):
        try:
            self._vmJobs = self.queryBlockJobs()
        except Exception:
            self.log.exception("Error updating VM jobs")

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
            for storedJob in list(self._blockJobs.values()):
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

                try:
                    drive = self.findDriveByUUIDs(storedJob['disk'])
                except LookupError:
                    # Drive loopkup may fail only in case of active layer
                    # merge, and pivot completed.
                    disk = storedJob['disk']
                    if disk["volumeID"] != storedJob["topVolume"]:
                        self.log.error("Cannot find drive for job %s "
                                       "(disk=%s)",
                                       jobID, storedJob['disk'])
                        continue
                    # Active layer merge, check if pivot completed.
                    pivoted_drive = dict(disk)
                    pivoted_drive["volumeID"] = storedJob["baseVolume"]
                    try:
                        drive = self.findDriveByUUIDs(pivoted_drive)
                    except LookupError:
                        self.log.error("Pivot completed but cannot find drive "
                                       "for job %s (disk=%s)",
                                       jobID, pivoted_drive)
                        continue
                entry = {'id': jobID, 'jobType': 'block',
                         'blockJobType': storedJob['blockJobType'],
                         'bandwidth': 0, 'cur': '0', 'end': '0',
                         'imgUUID': storedJob['disk']['imageID'],
                         'drive': storedJob['drive']}

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

    def on_block_job_event(self, drive, job_type, job_status):
        """
        Implement virConnectDomainEventBlockJobCallback.

        Currently we only log the event; we may want to use this to optimize
        waiting for completion.

        For more info see:
        https://libvirt.org/html/libvirt-libvirt-domain.html#virConnectDomainEventBlockJobCallback
        """  # NOQA: E501 (long line)

        job_id = "(untracked)"

        # Only COMMIT and ACTIVE_COMMIT jobs are tracked.
        if job_type in (libvirt.VIR_DOMAIN_BLOCK_JOB_TYPE_COMMIT,
                        libvirt.VIR_DOMAIN_BLOCK_JOB_TYPE_ACTIVE_COMMIT):
            with self._jobsLock:
                for job in self._blockJobs.values():
                    if job['drive'] == drive:
                        job_id = job['jobID']

        type_name = blockjob.type_name(job_type)

        if job_status == libvirt.VIR_DOMAIN_BLOCK_JOB_COMPLETED:
            self.log.info("Block job %s type %s for drive %s has completed",
                          job_id, type_name, drive)
        elif job_status == libvirt.VIR_DOMAIN_BLOCK_JOB_FAILED:
            self.log.error("Block job %s type %s for drive %s has failed",
                           job_id, type_name, drive)
        elif job_status == libvirt.VIR_DOMAIN_BLOCK_JOB_CANCELED:
            self.log.error("Block job %s type %s for drive %s was canceled",
                           job_id, type_name, drive)
        elif job_status == libvirt.VIR_DOMAIN_BLOCK_JOB_READY:
            self.log.info("Block job %s type %s for drive %s is ready",
                          job_id, type_name, drive)
        else:
            self.log.error(
                "Block job %s type %s for drive %s: unexpected status %s",
                job_id, type_name, drive, job_status)

    def merge(self, driveSpec, baseVolUUID, topVolUUID, bandwidth, jobUUID):
        bandwidth = int(bandwidth)
        if jobUUID is None:
            jobUUID = str(uuid.uuid4())

        try:
            drive = self.findDriveByUUIDs(driveSpec)
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
                'name': drive.name,
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
        if drive.diskType != DISK_TYPE.BLOCK or base_info['format'] != 'RAW':
            return True

        if int(base_info['capacity']) < int(top_info['capacity']):
            self.log.warning("The base volume is undersized and cannot be "
                             "extended (base capacity: %s, top capacity: %s)",
                             base_info['capacity'], top_info['capacity'])
            return False
        return True

    def _driveGetActualVolumeChain(self, drives):
        ret = {}
        self._updateDomainDescriptor()
        for drive in drives:
            alias = drive['alias']
            diskXML = vmdevices.lookup.xml_device_by_alias(
                self._domain.devices, alias
            )
            volChain = drive.parse_volume_chain(diskXML)
            if volChain:
                ret[alias] = volChain
        return ret

    def _syncVolumeChain(self, drive):
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

        old_path = drive.path
        must_update_drive = drive.volumeID != volumeID
        # drive object fields must always be updated
        if must_update_drive:
            # If the active layer changed:
            #  Update the disk path, volumeID, volumeInfo, and format members
            # Path must be set with the value being used by libvirt
            volInfo = find_chain_node(drive.volumeChain, volumeID)
            volInfo['path'] = activePath
            drive.path = activePath
            drive.format = driveFormat
            drive.volumeID = volumeID
            drive.volumeInfo = volInfo
            update_active_path(drive.volumeChain, volumeID, activePath)

        # Remove any components of the volumeChain which are no longer present
        drive.volumeChain = clean_volume_chain(drive.volumeChain, volumes)

        # we store disk infos in self.conf for backward compatibility.
        # We need to fix that data too.
        dev_conf = vmdevices.lookup.conf_by_path(
            self.conf['devices'], old_path)
        if must_update_drive:
            sync_drive_conf(dev_conf, drive)
        dev_conf['volumeChain'] = clean_volume_chain(
            dev_conf['volumeChain'], volumes)

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

        self.log.warning('monitor became unresponsive'
                         ' (command timeout, age=%s)',
                         stats_age)
        stats['monitorResponse'] = '-1'

    def onDeviceRemoved(self, device_alias):
        self.log.info("Device removal reported: %s", device_alias)
        try:
            device = self._hotunplugged_devices.pop(device_alias)
        except KeyError:
            try:
                device, device_hwclass = \
                    vmdevices.lookup.hotpluggable_device_by_alias(
                        self._devices, device_alias)
            except LookupError:
                # This may also happen if Vdsm is restarted between hot unplug
                # initiation and this event; device cleanup is not performed in
                # such a case.
                self.log.warning("Removed device not found in devices: %s",
                                 device_alias)
                return
            else:
                self._devices[device_hwclass].remove(device)
        try:
            device.teardown()
        finally:
            device.hotunplug_event.set()
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

    def run_dom_snapshot(self, snapxml, snap_flags):
        self._dom.snapshotCreateXML(snapxml, snap_flags)

    def job_stats(self):
        return self._dom.jobStats()

    def update_snapshot_metadata(self, data):
        self._snapshot_job = data
        self._update_metadata()

    def snapshot_metadata(self):
        return self._snapshot_job

    def abort_domjob(self):
        self._dom.abortJob()

    def qemu_agent_command(self, command, timeout, flags):
        # BEWARE: This interface has to be used only to gather information and
        # not to change state of the guest! Always prefer libvirt API if
        # it is available. See libvirt_qemu import above for further
        # explanation.
        return libvirt_qemu.qemuAgentCommand(
            self._dom, command, timeout, flags)


class LiveMergeCleanupThread(object):
    def __init__(self, vm, job, drive, doPivot):
        self.vm = vm
        self.job = job
        self.drive = drive
        self.doPivot = doPivot
        self.success = False
        self._thread = concurrent.thread(
            self.run, name="merge/" + job["jobID"][:8])

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
        self.vm.drive_monitor.disable()

        self.vm.log.info("Requesting pivot to complete active layer commit "
                         "(job %s)", self.job['jobID'])
        try:
            flags = libvirt.VIR_DOMAIN_BLOCK_JOB_ABORT_PIVOT
            self.vm._dom.blockJobAbort(self.drive.name, flags)
        except libvirt.libvirtError as e:
            self.vm.drive_monitor.enable()
            if e.get_error_code() != libvirt.VIR_ERR_BLOCK_COPY_ACTIVE:
                raise
            raise BlockCopyActiveError(self.job['jobID'])
        except:
            self.vm.drive_monitor.enable()
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
            self.vm.drive_monitor.enable()
        chain_after_merge = [vol['volumeID'] for vol in self.drive.volumeChain]
        if self.job['topVolume'] not in chain_after_merge:
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


def update_active_path(volume_chain, volumeID, activePath):
    for v in volume_chain:
        if v['volumeID'] == volumeID:
            v['path'] = activePath


def clean_volume_chain(volume_chain, volumes):
    # Remove any components of the volumeChain which are no longer present
    return [x for x in volume_chain if x['volumeID'] in volumes]


def find_chain_node(volume_chain, volumeID):
    for info in volume_chain:
        if info['volumeID'] == volumeID:
            return utils.picklecopy(info)
    return None


def sync_drive_conf(dev_conf, drive):
    conf_vol_info = find_chain_node(dev_conf['volumeChain'], drive.volumeID)
    conf_vol_info['path'] = drive.path
    dev_conf['path'] = drive.path
    dev_conf['format'] = drive.format
    dev_conf['volumeID'] = drive.volumeID
    dev_conf['volumeInfo'] = conf_vol_info
    update_active_path(
        dev_conf['volumeChain'], drive.volumeID, drive.path)
