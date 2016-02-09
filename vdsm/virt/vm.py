#
# Copyright 2008-2014 Red Hat, Inc.
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
from collections import namedtuple
from contextlib import contextmanager
from xml.dom import Node
from xml.dom.minidom import parseString as _domParseStr
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
from vdsm import constants
from vdsm import libvirtconnection
from vdsm import netinfo
from vdsm import qemuimg
from vdsm import response
from vdsm import utils
from vdsm.compat import pickle
from vdsm.config import config
from vdsm.define import ERROR, NORMAL, doneCode, errCode
from vdsm.netinfo import DUMMY_BRIDGE
from storage import outOfProcess as oop
from storage import sd
from storage import fileUtils

# local imports
# In future those should be imported via ..
from logUtils import SimpleLogAdapter
import caps
import hooks
import hostdev
import numaUtils
import supervdsm

# local package imports
from .domain_descriptor import DomainDescriptor
from . import guestagent
from . import migration
from . import sampling
from . import virdomain
from . import vmdevices
from . import vmexitreason
from . import vmstats
from . import vmstatus
from .vmdevices import hwclass
from .vmdevices.storage import DISK_TYPE
from .vmtune import update_io_tune_dom, collect_inner_elements
from .vmtune import io_tune_values_to_dom, io_tune_dom_to_values
from . import vmxml
from .vmxml import METADATA_VM_TUNE_URI, METADATA_VM_TUNE_ELEMENT
from .vmxml import METADATA_VM_TUNE_PREFIX

from .utils import isVdsmImage, cleanup_guest_socket
from vmpowerdown import VmShutdown, VmReboot

_VMCHANNEL_DEVICE_NAME = 'com.redhat.rhevm.vdsm'
# This device name is used as default both in the qemu-guest-agent
# service/daemon and in libvirtd (to be used with the quiesce flag).
_QEMU_GA_DEVICE_NAME = 'org.qemu.guest_agent.0'
_AGENT_CHANNEL_DEVICES = (_VMCHANNEL_DEVICE_NAME, _QEMU_GA_DEVICE_NAME)

DEFAULT_BRIDGE = config.get("vars", "default_bridge")

# A libvirt constant for undefined cpu quota
_NO_CPU_QUOTA = 0

# A libvirt constant for undefined cpu period
_NO_CPU_PERIOD = 0


def _listDomains():
    libvirtCon = libvirtconnection.get()
    for domId in libvirtCon.listDomainsID():
        try:
            vm = libvirtCon.lookupByID(domId)
            xmlDom = vm.XMLDesc(0)
        except libvirt.libvirtError as e:
            if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                logging.exception("domId: %s is dead", domId)
            else:
                raise
        else:
            yield vm, xmlDom


def getVDSMDomains():
    """
    Return a list of Domains created by VDSM.
    """
    return [vm for vm, xmlDom in _listDomains()
            if vmxml.has_channel(xmlDom, _VMCHANNEL_DEVICE_NAME)]


def _filterSnappableDiskDevices(diskDeviceXmlElements):
        return filter(lambda x: not(x.getAttribute('device')) or
                      x.getAttribute('device') in ['disk', 'lun'],
                      diskDeviceXmlElements)


class VolumeError(RuntimeError):
    def __str__(self):
        return "Bad volume specification " + RuntimeError.__str__(self)


class DoubleDownError(RuntimeError):
    pass


class ImprobableResizeRequestError(RuntimeError):
    pass


class BlockJobExistsError(Exception):
    pass


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


def eventToString(event):
    try:
        return _EVENT_STRINGS[event]
    except IndexError:
        return "Unknown (%i)" % event


class SetLinkAndNetworkError(Exception):
    pass


class UpdatePortMirroringError(Exception):
    pass


VolumeChainEntry = namedtuple('VolumeChainEntry',
                              ['uuid', 'path', 'allocation'])

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
    DeviceMapping = ((hwclass.DISK, vmdevices.storage.Drive),
                     (hwclass.NIC, vmdevices.network.Interface),
                     (hwclass.SOUND, vmdevices.core.Sound),
                     (hwclass.VIDEO, vmdevices.core.Video),
                     (hwclass.GRAPHICS, vmdevices.graphics.Graphics),
                     (hwclass.CONTROLLER, vmdevices.core.Controller),
                     (hwclass.GENERAL, vmdevices.core.Generic),
                     (hwclass.BALLOON, vmdevices.core.Balloon),
                     (hwclass.WATCHDOG, vmdevices.core.Watchdog),
                     (hwclass.CONSOLE, vmdevices.core.Console),
                     (hwclass.REDIR, vmdevices.core.Redir),
                     (hwclass.RNG, vmdevices.core.Rng),
                     (hwclass.SMARTCARD, vmdevices.core.Smartcard),
                     (hwclass.TPM, vmdevices.core.Tpm),
                     (hwclass.HOSTDEV, vmdevices.hostdevice.HostDevice),
                     (hwclass.MEMORY, vmdevices.core.Memory))

    def _emptyDevMap(self):
        return dict((dev, []) for dev, _ in self.DeviceMapping)

    def _makeChannelPath(self, deviceName):
        return constants.P_LIBVIRT_VMCHANNELS + self.id + '.' + deviceName

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
        self._dom = virdomain.Disconnected(params["vmId"])
        self.recovering = recover
        self.conf = {'pid': '0', '_blockJobs': {}, 'clientIp': ''}
        self.conf.update(params)
        if 'smp' not in self .conf:
            self.conf['smp'] = '1'
        # restore placeholders for BC sake
        vmdevices.graphics.initLegacyConf(self.conf)
        self.cif = cif
        self.log = SimpleLogAdapter(self.log, {"vmId": self.conf['vmId']})
        self._destroyed = False
        self._recoveryLock = threading.Lock()
        self._recoveryFile = constants.P_VDSM_RUN + \
            str(self.conf['vmId']) + '.recovery'
        self._monitorResponse = 0
        self.memCommitted = 0
        self._consoleDisconnectAction = ConsoleDisconnectAction.LOCK_SCREEN
        self._confLock = threading.Lock()
        self._jobsLock = threading.Lock()
        self._statusLock = threading.Lock()
        self._creationThread = threading.Thread(target=self._startUnderlyingVm)
        if 'migrationDest' in self.conf:
            self._lastStatus = vmstatus.MIGRATION_DESTINATION
        elif 'restoreState' in self.conf:
            self._lastStatus = vmstatus.RESTORING_STATE
        else:
            self._lastStatus = vmstatus.WAIT_FOR_LAUNCH
        self._migrationSourceThread = migration.SourceThread(self)
        self._kvmEnable = self.conf.get('kvmEnable', 'true')
        self._incomingMigrationFinished = threading.Event()
        self.id = self.conf['vmId']
        self._volPrepareLock = threading.Lock()
        self._initTimePauseCode = None
        self._initTimeRTC = int(self.conf.get('timeOffset', 0))
        self._guestEvent = vmstatus.POWERING_UP
        self._guestEventTime = 0
        self._guestCpuRunning = False
        self._guestCpuLock = threading.Lock()
        self._startTime = time.time() - \
            float(self.conf.pop('elapsedTimeOffset', 0))

        self._usedIndices = {}  # {'ide': [], 'virtio' = []}
        self.stopDisksStatsCollection()
        self._vmCreationEvent = threading.Event()
        self._pathsPreparedEvent = threading.Event()
        self._devices = self._emptyDevMap()

        self._connection = libvirtconnection.get(cif)
        if 'vmName' not in self.conf:
            self.conf['vmName'] = 'n%s' % self.id
        self._guestSocketFile = self._makeChannelPath(_VMCHANNEL_DEVICE_NAME)
        self._qemuguestSocketFile = self._makeChannelPath(_QEMU_GA_DEVICE_NAME)
        self.guestAgent = guestagent.GuestAgent(
            self._guestSocketFile, self.cif.channelListener, self.log,
            self._onGuestStatusChange)
        self._domain = DomainDescriptor.from_id(self.id)
        self._released = False
        self._releaseLock = threading.Lock()
        self.saveState()
        self._watchdogEvent = {}
        self.arch = caps.getTargetArch()

        if self.arch not in caps.Architecture.POWER and \
                self.arch != caps.Architecture.X86_64:
            raise RuntimeError('Unsupported architecture: %s' % self.arch)

        self._powerDownEvent = threading.Event()
        self._liveMergeCleanupThreads = {}
        self._shutdownLock = threading.Lock()
        self._shutdownReason = None
        self._vcpuLimit = None
        self._vcpuTuneInfo = {}
        self._numaInfo = {}
        self._vmJobs = None
        self._clientPort = ''

    def _get_lastStatus(self):
        # note that we don't use _statusLock here. One of the reasons is the
        # non-obvious recursive locking in the following flow:
        # _set_lastStatus() -> saveState() -> status() -> _get_lastStatus().
        status = self._lastStatus
        if not self._guestCpuRunning and status in vmstatus.PAUSED_STATES:
            return vmstatus.PAUSED
        return status

    def _set_lastStatus(self, value):
        with self._statusLock:
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

    def _notify(self, operation, params):
        sub_id = '|virt|%s|%s' % (operation, self.id)
        self.cif.notify(sub_id, **{self.id: params})

    def _onGuestStatusChange(self):
        self.send_status_event(**self._getGuestStats())

    def _get_status_time(self):
        """
        Value provided by this method is used to order messages
        containing changed status on the engine side.
        """
        return str(int(utils.monotonic_time() * 1000))

    lastStatus = property(_get_lastStatus, _set_lastStatus)

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

    def __legacyDrives(self):
        """
        Backward compatibility for qa scripts that specify direct paths.
        """
        legacies = []
        DEVICE_SPEC = ((0, 'hda'), (1, 'hdb'), (2, 'hdc'), (3, 'hdd'))
        for index, linuxName in DEVICE_SPEC:
            path = self.conf.get(linuxName)
            if path:
                legacies.append({'type': hwclass.DISK,
                                 'device': 'disk', 'path': path,
                                 'iface': 'ide', 'index': index,
                                 'truesize': 0})
        return legacies

    def __removableDrives(self):
        removables = [{
            'type': hwclass.DISK,
            'device': 'cdrom',
            'iface': vmdevices.storage.DEFAULT_INTERFACE_FOR_ARCH[self.arch],
            'path': self.conf.get('cdrom', ''),
            'index': 2,
            'truesize': 0}]
        floppyPath = self.conf.get('floppy')
        if floppyPath:
            removables.append({
                'type': hwclass.DISK,
                'device': 'floppy',
                'path': floppyPath,
                'iface': 'fdc',
                'index': 0,
                'truesize': 0})
        return removables

    def devMapFromDevSpecMap(self, dev_spec_map):
        dev_map = self._emptyDevMap()

        for dev_type, dev_class in self.DeviceMapping:
            for dev in dev_spec_map[dev_type]:
                dev_map[dev_type].append(dev_class(self.conf, self.log, **dev))

        return dev_map

    def devSpecMapFromConf(self):
        """
        Return the "devices" section of this Vm's conf.
        If missing, create it according to old API.
        """
        devices = self._emptyDevMap()

        # For BC we need to save previous behaviour for old type parameters.
        # The new/old type parameter will be distinguished
        # by existence/absence of the 'devices' key

        try:
            # while this code is running, Vm is queryable for status(),
            # thus we must fix devices in an atomic way, hence the deep copy
            with self._confLock:
                devConf = utils.picklecopy(self.conf['devices'])
        except KeyError:
            # (very) old Engines do not send device configuration
            devices[hwclass.DISK] = self.getConfDrives()
            devices[hwclass.NIC] = self.getConfNetworkInterfaces()
            devices[hwclass.SOUND] = self.getConfSound()
            devices[hwclass.VIDEO] = self.getConfVideo()
            devices[hwclass.GRAPHICS] = self.getConfGraphics()
            devices[hwclass.CONTROLLER] = self.getConfController()
        else:
            for dev in devConf:
                try:
                    devices[dev['type']].append(dev)
                except KeyError:
                    if 'type' not in dev or dev['type'] != 'channel':
                        self.log.warn("Unknown type found, device: '%s' "
                                      "found", dev)
                    devices[hwclass.GENERAL].append(dev)

            if not devices[hwclass.GRAPHICS]:
                devices[hwclass.GRAPHICS] = self.getConfGraphics()

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

        # Avoid overriding the saved balloon target value on recovery.
        if not self.recovering:
            for dev in balloonDevices:
                dev['target'] = int(self.conf.get('memSize')) * 1024

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

    def getConfController(self):
        """
        Normalize controller device.
        """
        controllers = []
        # For now we create by default only 'virtio-serial' controller
        controllers.append({'type': hwclass.CONTROLLER,
                            'device': 'virtio-serial'})
        return controllers

    def getConfVideo(self):
        """
        Normalize video device provided by conf.
        """

        DEFAULT_VIDEOS = {caps.Architecture.X86_64: 'cirrus',
                          caps.Architecture.PPC64: 'vga',
                          caps.Architecture.PPC64LE: 'vga'}

        vcards = []
        if self.conf.get('display') == 'vnc':
            devType = DEFAULT_VIDEOS[self.arch]
        elif self.hasSpice:
            devType = 'qxl'
        else:
            devType = None

        if devType:
            # this method is called only in the ancient Engines compatibility
            # path. But ancient Engines do not support headless VMs, as they
            # will not stop sending display data all of sudden.
            monitors = int(self.conf.get('spiceMonitors', '1'))
            vram = '65536' if (monitors <= 2) else '32768'
            for idx in range(monitors):
                vcards.append({'type': hwclass.VIDEO,
                               'specParams': {'vram': vram},
                               'device': devType})

        return vcards

    def getConfGraphics(self):
        """
        Normalize graphics device provided by conf.
        """

        # this method needs to cope both with ancient Engines and with
        # recent Engines unaware of graphic devices.
        if 'display' not in self.conf:
            return []

        return [{
            'type': hwclass.GRAPHICS,
            'device': (
                'spice'
                if self.conf['display'] in ('qxl', 'qxlnc')
                else 'vnc'),
            'specParams': vmdevices.graphics.makeSpecParams(self.conf)}]

    def getConfSound(self):
        """
        Normalize sound device provided by conf.
        """
        scards = []
        if self.conf.get('soundDevice'):
            scards.append({'type': hwclass.SOUND,
                           'device': self.conf.get('soundDevice')})

        return scards

    def getConfNetworkInterfaces(self):
        """
        Normalize networks interfaces provided by conf.
        """
        nics = []
        macs = self.conf.get('macAddr', '').split(',')
        models = self.conf.get('nicModel', '').split(',')
        bridges = self.conf.get('bridge', DEFAULT_BRIDGE).split(',')
        if macs == ['']:
            macs = []
        if models == ['']:
            models = []
        if bridges == ['']:
            bridges = []
        if len(models) < len(macs) or len(models) < len(bridges):
            raise ValueError('Bad nic specification')
        if models and not (macs or bridges):
            raise ValueError('Bad nic specification')
        if not macs or not models or not bridges:
            return ''
        macs = macs + [macs[-1]] * (len(models) - len(macs))
        bridges = bridges + [bridges[-1]] * (len(models) - len(bridges))

        for mac, model, bridge in zip(macs, models, bridges):
            if model == 'pv':
                model = 'virtio'
            nics.append({'type': hwclass.NIC,
                         'macAddr': mac,
                         'nicModel': model, 'network': bridge,
                         'device': 'bridge'})
        return nics

    def getConfDrives(self):
        """
        Normalize drives provided by conf.
        """
        # FIXME
        # Will be better to change the self.conf but this implies an API change
        # Remove this when the API parameters will be consistent.
        confDrives = self.conf.get('drives', [])
        if not confDrives:
            confDrives.extend(self.__legacyDrives())
        confDrives.extend(self.__removableDrives())

        for drv in confDrives:
            drv['type'] = hwclass.DISK
            drv['format'] = drv.get('format') or 'raw'
            drv['propagateErrors'] = drv.get('propagateErrors') or 'off'
            drv['readonly'] = False
            drv['shared'] = False
            # FIXME: For BC we have now two identical keys: iface = if
            # Till the day that conf will not returned as a status anymore.
            drv['iface'] = drv.get('iface') or \
                drv.get(
                    'if',
                    vmdevices.storage.DEFAULT_INTERFACE_FOR_ARCH[self.arch])

        return confDrives

    def updateDriveIndex(self, drv):
        if not drv['iface'] in self._usedIndices:
            self._usedIndices[drv['iface']] = []
        drv['index'] = self.__getNextIndex(self._usedIndices[drv['iface']])
        self._usedIndices[drv['iface']].append(int(drv['index']))

    def normalizeDrivesIndices(self, confDrives):
        drives = [(order, drv) for order, drv in enumerate(confDrives)]
        indexed = []
        for order, drv in drives:
            if drv['iface'] not in self._usedIndices:
                self._usedIndices[drv['iface']] = []
            idx = drv.get('index')
            if idx is not None:
                self._usedIndices[drv['iface']].append(int(idx))
                indexed.append(order)

        for order, drv in drives:
            if order not in indexed:
                self.updateDriveIndex(drv)

        return [drv for order, drv in drives]

    def run(self):
        self._creationThread.start()

    def memCommit(self):
        """
        Reserve the required memory for this VM.
        """
        memory = int(self.conf['memSize'])
        memory += config.getint('vars', 'guest_ram_overhead')
        self.memCommitted = 2 ** 20 * memory

    def _startUnderlyingVm(self):
        self.log.debug("Start")
        try:
            self.memCommit()
            with self._ongoingCreations:
                self._vmCreationEvent.set()
                try:
                    self._run()
                except MissingLibvirtDomainError:
                    # we cannot continue without a libvirt domain object,
                    # not even on recovery, to avoid state desync or worse
                    # split-brain scenarios.
                    raise
                except Exception as e:
                    # as above
                    if isinstance(e, libvirt.libvirtError) and \
                            e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                        raise MissingLibvirtDomainError()
                    elif not self.recovering:
                        raise
                    else:
                        self.log.info("Skipping errors on recovery",
                                      exc_info=True)

            if ('migrationDest' in self.conf or 'restoreState' in self.conf) \
                    and self.lastStatus != vmstatus.DOWN:
                self._completeIncomingMigration()

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
                self.conf.get(
                    'exitCode', ERROR),
                self.conf.get(
                    'exitReason', e.reason),
                self.conf.get(
                    'exitMessage', ''))
            self.recovering = False
        except MigrationError:
            self.log.exception("Failed to start a migration destination vm")
            self.setDownStatus(ERROR, vmexitreason.MIGRATION_FAILED)
        except Exception as e:
            if self.recovering:
                self.log.info("Skipping errors on recovery", exc_info=True)
            else:
                self.log.exception("The vm start process failed")
                self.setDownStatus(ERROR, vmexitreason.GENERIC_ERROR, str(e))

    def _incomingMigrationPending(self):
        return 'migrationDest' in self.conf or 'restoreState' in self.conf

    def stopDisksStatsCollection(self):
        self._volumesPrepared = False

    def startDisksStatsCollection(self):
        self._volumesPrepared = True

    def isDisksStatsCollectionEnabled(self):
        return self._volumesPrepared

    def preparePaths(self, drives):
        for drive in drives:
            with self._volPrepareLock:
                if self._destroyed:
                    # A destroy request has been issued, exit early
                    break
                drive['path'] = self.cif.prepareVolumePath(drive, self.id)

        else:
            # Now we got all the resources we needed
            self.startDisksStatsCollection()

    def _prepareTransientDisks(self, drives):
        for drive in drives:
            self._createTransientDisk(drive)

    def _onQemuDeath(self):
        self.log.info('underlying process disconnected')
        self._dom = virdomain.Disconnected(self.id)
        # Try release VM resources first, if failed stuck in 'Powering Down'
        # state
        result = self.releaseVm()
        if not result['status']['code']:
            with self._shutdownLock:
                reason = self._shutdownReason
            if reason is None:
                self.setDownStatus(ERROR, vmexitreason.LOST_QEMU_CONNECTION)
            else:
                self.setDownStatus(NORMAL, reason)
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
        with self._recoveryLock:
            if self._recoveryFile is not None:
                with tempfile.NamedTemporaryFile(dir=constants.P_VDSM_RUN,
                                                 delete=False) as f:
                    pickle.dump(self._getVmState(), f)

                os.rename(f.name, self._recoveryFile)
            else:
                self.log.debug(
                    'saveState after cleanup for status=%s destroyed=%s',
                    self.lastStatus, self._destroyed)

        try:
            self._updateDomainDescriptor()
        except Exception:
            # we do not care if _dom suddenly died now
            pass

    def _getVmState(self):
        toSave = self.status()
        toSave['startTime'] = self._startTime
        if self.lastStatus != vmstatus.DOWN:
            guestInfo = self.guestAgent.getGuestInfo()
            toSave['username'] = guestInfo['username']
            toSave['guestIPs'] = guestInfo['guestIPs']
            toSave['guestFQDN'] = guestInfo['guestFQDN']
        else:
            toSave['username'] = ""
            toSave['guestIPs'] = ""
            toSave['guestFQDN'] = ""
        if 'sysprepInf' in toSave:
            del toSave['sysprepInf']
            if 'floppy' in toSave:
                del toSave['floppy']
        for drive in toSave.get('drives', []):
            for d in self._devices[hwclass.DISK]:
                if isVdsmImage(d) and drive.get('volumeID') == d.volumeID:
                    drive['truesize'] = str(d.truesize)
                    drive['apparentsize'] = str(d.apparentsize)

        toSave['_blockJobs'] = utils.picklecopy(
            self.conf.get('_blockJobs', {}))

        return toSave

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
        except Exception:
            self.log.exception("Reboot event failed")

    def onConnect(self, clientIp='', clientPort=''):
        if clientIp:
            self.conf['clientIp'] = clientIp
            self._clientPort = clientPort

    def _timedDesktopLock(self):
        # This is not a definite fix, we're aware that there is still the
        # possibility of a race condition, however this covers more cases
        # than before and a quick gain
        if not self.conf.get('clientIp', '') and not self._destroyed:
            delay = config.get('vars', 'user_shutdown_timeout')
            timeout = config.getint('vars', 'sys_shutdown_timeout')
            CDA = ConsoleDisconnectAction
            if self._consoleDisconnectAction == CDA.LOCK_SCREEN:
                self.guestAgent.desktopLock()
            elif self._consoleDisconnectAction == CDA.LOGOUT:
                self.guestAgent.desktopLogoff()
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

        for drive in self._devices[hwclass.DISK]:
            if not (drive.chunked or drive.replicaChunked):
                continue

            try:
                capacity, alloc, physical = self._getExtendInfo(drive)
            except libvirt.libvirtError as e:
                self.log.error("Unable to get watermarks for drive %s: %s",
                               drive.name, e)
                continue

            ret.append((drive, drive.volumeID, capacity, alloc, physical))

        return ret

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
                raise RuntimeError('waiting more that %ss for _guestCpuLock' %
                                   timeout)

    def cont(self, afterState=vmstatus.UP, guestCpuLocked=False):
        if not guestCpuLocked:
            self._acquireCpuLockWithTimeout()
        try:
            if self.lastStatus in (vmstatus.MIGRATION_SOURCE,
                                   vmstatus.SAVING_STATE, vmstatus.DOWN):
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
        return {'status': doneCode, 'output': ['']}

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
        return {'status': doneCode, 'output': ['']}

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
            return response.error('noVM')

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
                utils.rmFile(self.conf['floppy'])
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

    def _reattachHostDevices(self):
        # reattach host devices
        for dev_name, _ in self._host_devices():
            self.log.debug('Reattaching device %s to host.' % dev_name)
            try:
                hostdev.reattach_detachable(dev_name)
            except hostdev.NoIOMMUSupportException:
                self.log.exception('Could not reattach device %s back to host '
                                   'due to missing IOMMU support.')

    def _host_devices(self):
        for device in self._devices[hwclass.HOSTDEV][:]:
            yield device.device, device
        for device in self._devices[hwclass.NIC][:]:
            if device.is_hostdevice:
                yield device.hostdev, device

    def setDownStatus(self, code, exitReasonCode, exitMessage=''):
        if not exitMessage:
            exitMessage = vmexitreason.exitReasons.get(exitReasonCode,
                                                       'VM terminated')
        event_data = {}
        try:
            self.lastStatus = vmstatus.DOWN
            with self._confLock:
                self.conf['exitCode'] = code
                if 'restoreState' in self.conf:
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

        self.conf['status'] = self.lastStatus
        with self._confLock:
            # Filter out any internal keys
            status = dict((k, v) for k, v in self.conf.iteritems()
                          if not k.startswith("_"))
            status['guestDiskMapping'] = self.guestAgent.guestDiskMapping
            status['statusTime'] = self._get_status_time()
            return utils.picklecopy(status)

    def getStats(self):
        """
        used by API.Vm.getStats

        WARNING: this method should only gather statistics by copying data.
        Especially avoid costly and dangerous ditrect calls to the _dom
        attribute. Use the periodic operations instead!
        """

        stats = {'statusTime': self._get_status_time()}
        if self.lastStatus == vmstatus.DOWN:
            stats.update(self._getDownVmStats())
        else:
            stats.update(self._getConfigVmStats())
            stats.update(self._getRunningVmStats())
            stats['status'] = self._getVmStatus()
            stats.update(self._getGuestStats())
        return stats

    def _getDownVmStats(self):
        stats = {
            'vmId': self.conf['vmId'],
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
        but can change as result as interaction with libvirt (display*)
        """
        stats = {
            'vmId': self.conf['vmId'],
            'vmName': self.name,
            'pid': self.conf['pid'],
            'vmType': self.conf['vmType'],
            'kvmEnable': self._kvmEnable,
            'acpiEnable': self.conf.get('acpiEnable', 'true')}
        if 'cdrom' in self.conf:
            stats['cdrom'] = self.conf['cdrom']
        if 'boot' in self.conf:
            stats['boot'] = self.conf['boot']
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

        with self._confLock:
            if 'pauseCode' in self.conf:
                stats['pauseCode'] = self.conf['pauseCode']
        if self.isMigrating():
            stats['migrationProgress'] = self.migrateStatus()['progress']

        try:
            vm_sample = sampling.stats_cache.get(self.id)
            decStats = vmstats.produce(self,
                                       vm_sample.first_value,
                                       vm_sample.last_value,
                                       vm_sample.interval)
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
        if self._numaInfo:
            stats['vNodeRuntimeInfo'] = self._numaInfo
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

    def _getGraphicsStats(self):
        def getInfo(dev):
            return {
                'type': dev.device,
                'port': dev.port,
                'tlsPort': dev.tlsPort,
                'ipAddress': dev.specParams.get('displayIp', '0')}

        stats = {
            'displayInfo': [getInfo(dev)
                            for dev in self._devices[hwclass.GRAPHICS]]}
        if 'display' in self.conf:
            stats['displayType'] = self.conf['display']
            stats['displayPort'] = self.conf['displayPort']
            stats['displaySecurePort'] = self.conf['displaySecurePort']
            stats['displayIp'] = self.conf['displayIp']
        # else headless VM
        return stats

    def _getGuestStats(self):
        stats = self.guestAgent.getGuestInfo()
        realMemUsage = int(stats['memUsage'])
        if realMemUsage != 0:
            memUsage = (100 - float(realMemUsage) /
                        int(self.conf['memSize']) * 100)
        else:
            memUsage = 0
        stats['memUsage'] = utils.convertToStr(int(memUsage))
        return stats

    def isMigrating(self):
        return self._migrationSourceThread.isAlive()

    def hasTransientDisks(self):
        for drive in self._devices[hwclass.DISK]:
            if drive.transientDisk:
                return True
        return False

    def migrate(self, params):
        self._acquireCpuLockWithTimeout()
        try:
            if self.isMigrating():
                self.log.warning('vm already migrating')
                return response.error('exist')
            if self.hasTransientDisks():
                return response.error('transientErr')
            # while we were blocking, another migrationSourceThread could have
            # taken self Down
            if self._lastStatus == vmstatus.DOWN:
                return response.error('noVM')
            self._migrationSourceThread = migration.SourceThread(
                self, **params)
            self._migrationSourceThread.start()
            self._migrationSourceThread.getStat()
            self.send_status_event()
            return self._migrationSourceThread.status
        finally:
            self._guestCpuLock.release()

    def migrateStatus(self):
        return self._migrationSourceThread.getStat()

    def migrateCancel(self):
        self._acquireCpuLockWithTimeout()
        try:
            self._migrationSourceThread.stop()
            self._migrationSourceThread.status['status']['message'] = \
                'Migration process cancelled'
            return self._migrationSourceThread.status
        except libvirt.libvirtError as e:
            if e.get_error_code() == libvirt.VIR_ERR_OPERATION_INVALID:
                return response.error('migCancelErr')
            raise
        except virdomain.NotConnectedError:
            return response.error('migCancelErr')
        finally:
            self._guestCpuLock.release()

    def _customDevices(self):
        """
            Get all devices that have custom properties
        """

        for devType in self._devices:
            for dev in self._devices[devType]:
                if dev.custom:
                    yield dev

    def _appendDevices(self, domxml):
        """
        Create all devices and run before_device_create hook script for devices
        with custom properties

        The resulting device xml is cached in dev._deviceXML.
        """

        for devType in self._devices:
            for dev in self._devices[devType]:
                try:
                    deviceXML = dev.getXML().toxml(encoding='utf-8')
                except vmdevices.core.SkipDevice:
                    self.log.info('Skipping device %s.', dev.device)
                    continue

                if getattr(dev, "custom", {}):
                    deviceXML = hooks.before_device_create(
                        deviceXML, self.conf, dev.custom)

                dev._deviceXML = deviceXML
                domxml.appendDeviceXML(deviceXML)

    def _buildDomainXML(self):
        domxml = vmxml.Domain(self.conf, self.log, self.arch)
        domxml.appendOs()

        if self.arch == caps.Architecture.X86_64:
            osd = caps.osversion()

            osVersion = osd.get('version', '') + '-' + osd.get('release', '')
            serialNumber = self.conf.get('serial', utils.getHostUUID())

            domxml.appendSysinfo(
                osname=constants.SMBIOS_OSNAME,
                osversion=osVersion,
                serialNumber=serialNumber)

        domxml.appendClock()

        if self.arch == caps.Architecture.X86_64:
            domxml.appendFeatures()

        domxml.appendCpu()

        domxml.appendNumaTune()

        domxml._appendAgentDevice(self._guestSocketFile.decode('utf-8'),
                                  _VMCHANNEL_DEVICE_NAME)
        domxml._appendAgentDevice(self._qemuguestSocketFile.decode('utf-8'),
                                  _QEMU_GA_DEVICE_NAME)
        domxml.appendInput()

        if self.arch == caps.Architecture.PPC64:
            domxml.appendEmulator()

        self._appendDevices(domxml)

        for graphDev in self._devices[hwclass.GRAPHICS]:
            if graphDev.device == 'spice':
                domxml._devices.appendChild(graphDev.getSpiceVmcChannelsXML())
                break

        for consoleDev in self._devices[hwclass.CONSOLE]:
            if consoleDev.isSerial:
                domxml._devices.appendChild(consoleDev.getSerialDeviceXML())
                break

        for drive in self._devices[hwclass.DISK][:]:
            for leaseElement in drive.getLeasesXML():
                domxml._devices.appendChild(leaseElement)

        return domxml.toxml()

    def _cleanup(self):
        """
        General clean up routine
        """
        self._cleanupDrives()
        self._cleanupFloppy()
        self._cleanupGuestAgent()
        cleanup_guest_socket(self._qemuguestSocketFile)
        self._reattachHostDevices()
        self._cleanupStatsCache()
        numaUtils.invalidateNumaCache(self)
        for con in self._devices[hwclass.CONSOLE]:
            con.cleanup()

    def _cleanupRecoveryFile(self):
        with self._recoveryLock:
            utils.rmFile(self._recoveryFile)
            self._recoveryFile = None

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
        self._getUnderlyingNetworkInterfaceInfo()
        self._getUnderlyingDriveInfo()
        self._getUnderlyingSoundDeviceInfo()
        self._getUnderlyingVideoDeviceInfo()
        self._getUnderlyingGraphicsDeviceInfo()
        self._getUnderlyingControllerDeviceInfo()
        self._getUnderlyingBalloonDeviceInfo()
        self._getUnderlyingWatchdogDeviceInfo()
        self._getUnderlyingSmartcardDeviceInfo()
        self._getUnderlyingRngDeviceInfo()
        self._getUnderlyingConsoleDeviceInfo()
        self._getUnderlyingHostDeviceInfo()
        self._getUnderlyingMemoryDeviceInfo()
        # Obtain info of all unknown devices. Must be last!
        self._getUnderlyingUnknownDeviceInfo()

    def _updateAgentChannels(self):
        """
        We moved the naming of guest agent channel sockets. To keep backwards
        compatability we need to make symlinks from the old channel sockets, to
        the new naming scheme.
        This is necessary to prevent incoming migrations, restoring of VMs and
        the upgrade of VDSM with running VMs to fail on this.
        """
        for name, path in self._domain.all_channels():
            if name not in _AGENT_CHANNEL_DEVICES:
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
        if self._destroyed:
            # reaching here means that Vm.destroy() was called before we could
            # handle it. We must handle it now
            try:
                self._dom.destroy()
            except Exception:
                pass
            raise Exception('destroy() called before Vm started')

        if not self._dom.connected:
            raise MissingLibvirtDomainError(vmexitreason.LIBVIRT_START_FAILED)

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

        self._guestEventTime = self._startTime
        sampling.stats_cache.add(self.id)
        try:
            self.guestAgent.connect()
        except Exception:
            self.log.exception("Failed to connect to guest agent channel")

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

        if not self.recovering or 'pid' not in self.conf:
            self.conf['pid'] = str(self._getPid())

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

    def _run(self):
        self.log.info("VM wrapper has started")
        dev_spec_map = self.devSpecMapFromConf()

        # recovery flow note:
        # we do not start disk stats collection here since
        # in the recovery flow irs may not be ready yet.
        # Disk stats collection is started from clientIF at the end
        # of the recovery process.
        if not self.recovering:
            self.preparePaths(dev_spec_map[hwclass.DISK])
            self._prepareTransientDisks(dev_spec_map[hwclass.DISK])
            self._updateDevices(dev_spec_map)
            # We need to save conf here before we actually run VM.
            # It's not enough to save conf only on status changes as we did
            # before, because if vdsm will restarted between VM run and conf
            # saving we will fail in inconsistent state during recovery.
            # So, to get proper device objects during VM recovery flow
            # we must to have updated conf before VM run
            self.saveState()
        else:
            # we need to fix the graphics device configuration in the
            # case VDSM is upgraded from 3.4 to 3.5 on the host without
            # rebooting it. Evident on, but not limited to, the HE case.
            self._fixLegacyGraphicsConf()

        self._devices = self.devMapFromDevSpecMap(dev_spec_map)

        # We should set this event as a last part of drives initialization
        self._pathsPreparedEvent.set()

        initDomain = 'migrationDest' not in self.conf
        # we need to complete the initialization, including
        # domDependentInit, after the migration is completed.

        if not self.recovering:
            for dev_name, dev in self._host_devices():
                self.log.debug('Detaching device %s from the host.' % dev_name)
                dev.detach()

        if self.recovering:
            self._dom = virdomain.Notifying(
                self._connection.lookupByUUIDString(self.id),
                self._timeoutExperienced)
        elif 'migrationDest' in self.conf:
            pass  # self._dom will be disconnected until migration ends.
        elif 'restoreState' in self.conf:
            # TODO: for unknown historical reasons, we call this hook also
            # on this flow. Issues:
            # - we will also call the more specific before_vm_dehibernate
            # - we feed the hook with wrong XML
            # - we ignore the output of the hook
            hooks.before_vm_start(self._buildDomainXML(), self.conf)

            fromSnapshot = self.conf.get('restoreFromSnapshot', False)
            with self._confLock:
                srcDomXML = self.conf.pop('_srcDomXML')
            if fromSnapshot:
                srcDomXML = self._correctDiskVolumes(srcDomXML)
                srcDomXML = self._correctGraphicsConfiguration(srcDomXML)
            hooks.before_vm_dehibernate(srcDomXML, self.conf,
                                        {'FROM_SNAPSHOT': str(fromSnapshot)})

            # TODO: this is debug information. For 3.6.x we still need to
            # see the XML even with 'info' as default level.
            self.log.info(srcDomXML)

            fname = self.cif.prepareVolumePath(self.conf['restoreState'])
            try:
                if fromSnapshot:
                    self._connection.restoreFlags(fname, srcDomXML, 0)
                else:
                    self._connection.restore(fname)
            finally:
                self.cif.teardownVolumePath(self.conf['restoreState'])

            self._dom = virdomain.Notifying(
                self._connection.lookupByUUIDString(self.id),
                self._timeoutExperienced)
        else:
            domxml = hooks.before_vm_start(self._buildDomainXML(), self.conf)
            # TODO: this is debug information. For 3.6.x we still need to
            # see the XML even with 'info' as default level.
            self.log.info(domxml)

            flags = libvirt.VIR_DOMAIN_NONE
            with self._confLock:
                if 'launchPaused' in self.conf:
                    flags |= libvirt.VIR_DOMAIN_START_PAUSED
                    self.conf['pauseCode'] = 'NOERR'
                    del self.conf['launchPaused']
            self._dom = virdomain.Notifying(
                self._connection.createXML(domxml, flags),
                self._timeoutExperienced)
            hooks.after_vm_start(self._dom.XMLDesc(0), self.conf)
            for dev in self._customDevices():
                hooks.after_device_create(dev._deviceXML, self.conf,
                                          dev.custom)

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
        parsedSrcDomXML = _domParseStr(srcDomXML)

        allDiskDeviceXmlElements = parsedSrcDomXML.childNodes[0]. \
            getElementsByTagName('devices')[0].getElementsByTagName('disk')

        snappableDiskDeviceXmlElements = \
            _filterSnappableDiskDevices(allDiskDeviceXmlElements)

        for snappableDiskDeviceXmlElement in snappableDiskDeviceXmlElements:
            self._changeDisk(snappableDiskDeviceXmlElement)

        return parsedSrcDomXML.toxml()

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

    def _changeDisk(self, diskDeviceXmlElement):
        diskType = diskDeviceXmlElement.getAttribute('type')

        if diskType not in ['file', 'block']:
            return

        diskSerial = diskDeviceXmlElement. \
            getElementsByTagName('serial')[0].childNodes[0].nodeValue

        for vmDrive in self._devices[hwclass.DISK]:
            if vmDrive.serial == diskSerial:
                # update the type
                diskDeviceXmlElement.setAttribute(
                    'type', 'block' if vmDrive.blockDev else 'file')

                # update the path
                diskDeviceXmlElement.getElementsByTagName('source')[0]. \
                    setAttribute('dev' if vmDrive.blockDev else 'file',
                                 vmDrive.path)

                # update the format (the disk might have been collapsed)
                diskDeviceXmlElement.getElementsByTagName('driver')[0]. \
                    setAttribute('type',
                                 'qcow2' if vmDrive.format == 'cow' else 'raw')

                break

    def hotplugNic(self, params):
        if self.isMigrating():
            return response.error('migInProgress')

        nicParams = params['nic']
        nic = vmdevices.network.Interface(self.conf, self.log, **nicParams)
        nicXml = nic.getXML().toprettyxml(encoding='utf-8')
        nicXml = hooks.before_nic_hotplug(nicXml, self.conf,
                                          params=nic.custom)
        nic._deviceXML = nicXml
        # TODO: this is debug information. For 3.6.x we still need to
        # see the XML even with 'info' as default level.
        self.log.info("Hotplug NIC xml: %s", nicXml)

        try:
            self._dom.attachDevice(nicXml)
        except libvirt.libvirtError as e:
            self.log.exception("Hotplug failed")
            nicXml = hooks.after_nic_hotplug_fail(
                nicXml, self.conf, params=nic.custom)
            if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                return response.error('noVM')
            return response.error('hotplugNic', e.message)
        else:
            # FIXME!  We may have a problem here if vdsm dies right after
            # we sent command to libvirt and before save conf. In this case
            # we will gather almost all needed info about this NIC from
            # the libvirt during recovery process.
            self._devices[hwclass.NIC].append(nic)
            with self._confLock:
                self.conf['devices'].append(nicParams)
            self.saveState()
            self._getUnderlyingNetworkInterfaceInfo()
            hooks.after_nic_hotplug(nicXml, self.conf,
                                    params=nic.custom)

        if hasattr(nic, 'portMirroring'):
            mirroredNetworks = []
            try:
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

    def _lookupDeviceByAlias(self, devType, alias):
        for dev in self._devices[devType][:]:
            try:
                if dev.alias == alias:
                    return dev
            except AttributeError:
                continue
        raise LookupError('Device instance for device identified by alias %s '
                          'not found' % alias)

    def _lookupConfByAlias(self, alias):
        for devConf in self.conf['devices'][:]:
            if devConf['type'] == hwclass.NIC and \
                    devConf['alias'] == alias:
                return devConf
        raise LookupError('Configuration of device identified by alias %s not'
                          'found' % alias)

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
            netDev = self._lookupDeviceByAlias(hwclass.NIC,
                                               params['alias'])
            netConf = self._lookupConfByAlias(params['alias'])

            linkValue = 'up' if utils.tobool(params.get('linkActive',
                                             netDev.linkActive)) else 'down'
            network = params.get('network', netDev.network)
            if network == '':
                network = DUMMY_BRIDGE
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
        source = vnicXML.getElementsByTagName('source')[0]
        source.setAttribute('bridge', networkValue)
        try:
            link = vnicXML.getElementsByTagName('link')[0]
        except IndexError:
            vnicXML.appendChildWithArgs('link')
        link.setAttribute('state', linkValue)
        if (specParams and
                ('inbound' in specParams or 'outbound' in specParams)):
            oldBandwidths = vnicXML.getElementsByTagName('bandwidth')
            oldBandwidth = oldBandwidths[0] if len(oldBandwidths) else None
            newBandwidth = dev.paramsToBandwidthXML(specParams, oldBandwidth)
            if oldBandwidth is None:
                vnicXML.appendChild(newBandwidth)
            else:
                vnicXML.replaceChild(newBandwidth, oldBandwidth)
        vnicStrXML = vnicXML.toprettyxml(encoding='utf-8')
        try:
            try:
                vnicStrXML = hooks.before_update_device(vnicStrXML, self.conf,
                                                        custom)
                self._dom.updateDeviceFlags(vnicStrXML,
                                            libvirt.VIR_DOMAIN_AFFECT_LIVE)
                dev._deviceXML = vnicStrXML
                self.log.info("Nic has been updated:\n %s" % vnicStrXML)
                hooks.after_update_device(vnicStrXML, self.conf, custom)
            except Exception as e:
                self.log.warn('Request failed: %s', vnicStrXML, exc_info=True)
                hooks.after_update_device_fail(vnicStrXML, self.conf, custom)
                raise SetLinkAndNetworkError(e.message)
            yield
        except Exception:
            # Rollback link and network.
            self.log.warn('Rolling back link and net for: %s', dev.alias,
                          exc_info=True)
            self._dom.updateDeviceFlags(vnicXML.toxml(encoding='utf-8'),
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
            raise UpdatePortMirroringError(e.message)
        else:
            # Update the conf with the new mirroring.
            conf['portMirroring'] = networks

    def _updateGraphicsDevice(self, params):
        graphics = self._findGraphicsDeviceXMLByType(params['graphicsType'])
        if graphics:
            result = self._setTicketForGraphicDev(
                graphics, params['password'], params['ttl'],
                params['existingConnAction'], params['params'])
            if result['status']['code'] == 0:
                result['vmList'] = self.status()
            return result
        else:
            return response.error('updateDevice')

    def updateDevice(self, params):
        if params.get('deviceType') == hwclass.NIC:
            return self._updateInterfaceDevice(params)
        elif params.get('deviceType') == hwclass.GRAPHICS:
            return self._updateGraphicsDevice(params)
        else:
            return response.error('noimpl')

    def hotunplugNic(self, params):
        if self.isMigrating():
            return response.error('migInProgress')

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

            nicXml = nic.getXML().toprettyxml(encoding='utf-8')
            hooks.before_nic_hotunplug(nicXml, self.conf,
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
        except HotunplugTimeout as e:
            self.log.error("%s", e)
            self._rollback_nic_hotunplug(nicDev, nic)
            hooks.after_nic_hotunplug_fail(nicXml, self.conf,
                                           params=nic.custom)
            return response.error('hotunplugNic', "%s" % e)
        except libvirt.libvirtError as e:
            self.log.exception("Hotunplug failed")
            if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                return response.error('noVM')
            self._rollback_nic_hotunplug(nicDev, nic)
            hooks.after_nic_hotunplug_fail(nicXml, self.conf,
                                           params=nic.custom)
            return response.error('hotunplugNic', e.message)

        hooks.after_nic_hotunplug(nicXml, self.conf,
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

    def hotplugMemory(self, params):

        if self.isMigrating():
            return errCode['migInProgress']

        memParams = params.get('memory', {})
        device = vmdevices.core.Memory(self.conf, self.log, **memParams)

        deviceXml = device.getXML().toprettyxml(encoding='utf-8')
        deviceXml = hooks.before_memory_hotplug(deviceXml)
        device._deviceXML = deviceXml
        self.log.debug("Hotplug memory xml: %s", deviceXml)

        try:
            self._dom.attachDevice(deviceXml)
        except libvirt.libvirtError as e:
            self.log.exception("hotplugMemory failed")
            if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                return errCode['noVM']
            return response.error('hotplugMem', e.message)

        self._devices[hwclass.MEMORY].append(device)
        with self._confLock:
            self.conf['devices'].append(memParams)
        self._updateDomainDescriptor()
        self._getUnderlyingMemoryDeviceInfo()
        # TODO: this is raceful (as the similar code of hotplugDisk
        # and hotplugNic, as a concurrent call of hotplug can change
        # vm.conf before we return.
        self.saveState()

        hooks.after_memory_hotplug(deviceXml)

        return {'status': doneCode, 'vmList': self.status()}

    def setNumberOfCpus(self, numberOfCpus):

        if self.isMigrating():
            return response.error('migInProgress')

        self.log.debug("Setting number of cpus to : %s", numberOfCpus)
        hooks.before_set_num_of_cpus()
        try:
            self._dom.setVcpusFlags(numberOfCpus,
                                    libvirt.VIR_DOMAIN_AFFECT_CURRENT)
        except libvirt.libvirtError as e:
            self.log.exception("setNumberOfCpus failed")
            if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                return response.error('noVM')
            return response.error('setNumberOfCpusErr', e.message)

        self.conf['smp'] = str(numberOfCpus)
        self.saveState()
        hooks.after_set_num_of_cpus()
        return {'status': doneCode, 'vmList': self.status()}

    def _updateVcpuLimit(self):
        qos = self._getVmPolicy()
        if qos is not None:
            try:
                vcpuLimit = qos.getElementsByTagName("vcpuLimit")
                self._vcpuLimit = vcpuLimit[0].childNodes[0].data
            except IndexError:
                # missing vcpuLimit node
                self._vcpuLimit = None

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

        if self.isMigrating():
            return response.error('migInProgress')

        if not params:
            self.log.error("updateVmPolicy got an empty policy.")
            return self._reportError(key='MissParam',
                                     msg="updateVmPolicy got an empty policy.")

        #
        # Get the current QoS block
        metadata_modified = False
        qos = self._getVmPolicy()
        if qos is None:
            return self._reportError(key='updateVmPolicyErr')

        #
        # Process provided properties, remove property after it is processed

        if 'vcpuLimit' in params:
            # Remove old value
            vcpuLimit = qos.getElementsByTagName("vcpuLimit")
            if vcpuLimit:
                qos.removeChild(vcpuLimit[0])

            vcpuLimit = vmxml.Element("vcpuLimit")
            vcpuLimit.appendTextNode(str(params["vcpuLimit"]))
            qos.appendChild(vcpuLimit)

            metadata_modified = True
            self._vcpuLimit = params.pop('vcpuLimit')

        if 'ioTune' in params:
            # Make sure the top level element exists
            ioTuneList = qos.getElementsByTagName("ioTune")
            if not ioTuneList:
                ioTuneElement = vmxml.Element("ioTune")
                ioTuneList.append(ioTuneElement)
                qos.appendChild(ioTuneElement)
                metadata_modified = True

            if update_io_tune_dom(ioTuneList[0], params["ioTune"]) > 0:
                metadata_modified = True

            del params['ioTune']

        # Check remaining fields in params and report the list of unsupported
        # params to the log

        if params:
            self.log.warn("updateVmPolicy got unknown parameters: %s",
                          ", ".join(params.iterkeys()))

        #
        # Save modified metadata

        if metadata_modified:
            metadata_xml = qos.toprettyxml()

            try:
                self._dom.setMetadata(libvirt.VIR_DOMAIN_METADATA_ELEMENT,
                                      metadata_xml, METADATA_VM_TUNE_PREFIX,
                                      METADATA_VM_TUNE_URI, 0)
            except libvirt.libvirtError as e:
                self.log.exception("updateVmPolicy failed")
                if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                    return response.error('noVM')
                else:
                    return self._reportError(key='updateVmPolicyErr',
                                             msg=e.message)

        return {'status': doneCode}

    def _getVmPolicy(self):
        """
        This method gets the current qos block from the libvirt metadata.
        If there is not any, it will create a new empty DOM tree with
        the <qos> root element.

        :return: XML DOM object representing the root qos element
        """

        metadata_xml = "<%s></%s>" % (METADATA_VM_TUNE_ELEMENT,
                                      METADATA_VM_TUNE_ELEMENT)

        try:
            metadata_xml = self._dom.metadata(
                libvirt.VIR_DOMAIN_METADATA_ELEMENT,
                METADATA_VM_TUNE_URI, 0)
        except libvirt.libvirtError as e:
            if e.get_error_code() != libvirt.VIR_ERR_NO_DOMAIN_METADATA:
                self.log.exception("getVmPolicy failed")
                return None

        metadata = _domParseStr(metadata_xml)
        return metadata.getElementsByTagName(METADATA_VM_TUNE_ELEMENT)[0]

    def _findDeviceByNameOrPath(self, device_name, device_path):
        for device in self._devices[hwclass.DISK]:
            if ((device.name == device_name
                or ("path" in device and device["path"] == device_path))
                    and isVdsmImage(device)):
                return device
        else:
            return None

    def getIoTunePolicy(self):
        tunables = []
        qos = self._getVmPolicy()
        ioTuneList = qos.getElementsByTagName("ioTune")
        if not ioTuneList or not ioTuneList[0].hasChildNodes():
            return []

        for device in ioTuneList[0].getElementsByTagName("device"):
            tunables.append(io_tune_dom_to_values(device))

        return tunables

    def setIoTune(self, tunables):
        for io_tune_change in tunables:
            device_name = io_tune_change.get('name', None)
            device_path = io_tune_change.get('path', None)
            io_tune = io_tune_change['ioTune']

            # Find the proper device object
            found_device = self._findDeviceByNameOrPath(device_name,
                                                        device_path)
            if found_device is None:
                return self._reportError(
                    key='updateIoTuneErr',
                    msg="Device {} not found".format(device_name))

            # Merge the update with current values
            dom = found_device.getXML()
            io_dom_list = dom.getElementsByTagName("iotune")
            old_io_tune = {}
            if io_dom_list:
                collect_inner_elements(io_dom_list[0], old_io_tune)
                old_io_tune.update(io_tune)
                io_tune = old_io_tune

            # Verify the ioTune params
            try:
                found_device._validateIoTuneParams(io_tune)
            except ValueError:
                return self._reportException(key='updateIoTuneErr',
                                             msg='Invalid ioTune value')

            try:
                self._dom.setBlockIoTune(found_device.name, io_tune,
                                         libvirt.VIR_DOMAIN_AFFECT_LIVE)
            except libvirt.libvirtError as e:
                self.log.exception("setVmIoTune failed")
                if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                    return response.error('noVM')
                else:
                    return self._reportError(key='updateIoTuneErr',
                                             msg=e.message)

            # Update both the ioTune arguments and device xml DOM
            # so we are still up-to-date
            # TODO: improve once libvirt gets support for iotune events
            #       see https://bugzilla.redhat.com/show_bug.cgi?id=1114492
            if io_dom_list:
                dom.removeChild(io_dom_list[0])
            io_dom = vmxml.Element("iotune")
            io_tune_values_to_dom(io_tune, io_dom)
            dom.appendChild(io_dom)
            found_device.specParams['ioTune'] = io_tune

            # Make sure the cached XML representation is valid as well
            xml = found_device.getXML().toprettyxml(encoding='utf-8')
            # TODO: this is debug information. For 3.6.x we still need to
            # see the XML even with 'info' as default level.
            self.log.info("New device XML for %s: %s",
                          found_device.name, xml)
            found_device._deviceXML = xml

        return {'status': doneCode}

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
            qemuimg.create(transientPath, format=qemuimg.FORMAT.QCOW2,
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

    def hotplugDisk(self, params):
        if self.isMigrating():
            return response.error('migInProgress')

        diskParams = params.get('drive', {})
        diskParams['path'] = self.cif.prepareVolumePath(diskParams)

        if isVdsmImage(diskParams):
            self._normalizeVdsmImg(diskParams)
            self._createTransientDisk(diskParams)

        self.updateDriveIndex(diskParams)
        drive = vmdevices.storage.Drive(self.conf, self.log, **diskParams)

        if drive.hasVolumeLeases:
            return response.error('noimpl')

        driveXml = drive.getXML().toprettyxml(encoding='utf-8')
        # TODO: this is debug information. For 3.6.x we still need to
        # see the XML even with 'info' as default level.
        self.log.info("Hotplug disk xml: %s" % (driveXml))

        driveXml = hooks.before_disk_hotplug(driveXml, self.conf,
                                             params=drive.custom)
        drive._deviceXML = driveXml
        try:
            self._dom.attachDevice(driveXml)
        except libvirt.libvirtError as e:
            self.log.exception("Hotplug failed")
            self.cif.teardownVolumePath(diskParams)
            if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                return response.error('noVM')
            return response.error('hotplugDisk', e.message)
        else:
            # FIXME!  We may have a problem here if vdsm dies right after
            # we sent command to libvirt and before save conf. In this case
            # we will gather almost all needed info about this drive from
            # the libvirt during recovery process.
            self._devices[hwclass.DISK].append(drive)

            with self._confLock:
                self.conf['devices'].append(diskParams)
            self.saveState()
            self._getUnderlyingDriveInfo()
            hooks.after_disk_hotplug(driveXml, self.conf,
                                     params=drive.custom)

        return {'status': doneCode, 'vmList': self.status()}

    def hotunplugDisk(self, params):
        if self.isMigrating():
            return response.error('migInProgress')

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

        driveXml = drive.getXML().toprettyxml(encoding='utf-8')
        # TODO: this is debug information. For 3.6.x we still need to
        # see the XML even with 'info' as default level.
        self.log.info("Hotunplug disk xml: %s", driveXml)

        hooks.before_disk_hotunplug(driveXml, self.conf,
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
                return response.error('noVM')
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
            hooks.after_disk_hotunplug(driveXml, self.conf,
                                       params=drive.custom)
            self._cleanupDrives(drive)

        return {'status': doneCode, 'vmList': self.status()}

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
        with utils.stopwatch("Hotunplug device %s" % device.name):
            deadline = (utils.monotonic_time() +
                        config.getfloat('vars', 'hotunplug_timeout'))
            sleep_time = config.getfloat('vars', 'hotunplug_check_interval')
            while device.is_attached_to(self._dom.XMLDesc(0)):
                time.sleep(sleep_time)
                if utils.monotonic_time() > deadline:
                    raise HotunplugTimeout("Timeout detaching device %s"
                                           % device.name)

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
            if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                # same as NotConnectedError above: possible race on shutdown
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
        if 'restoreState' in self.conf:
            self.cont()
            with self._confLock:
                del self.conf['restoreState']
                fromSnapshot = self.conf.pop('restoreFromSnapshot', False)
            hooks.after_vm_dehibernate(self._dom.XMLDesc(0), self.conf,
                                       {'FROM_SNAPSHOT': fromSnapshot})
            self._syncGuestTime()
        elif 'migrationDest' in self.conf:
            waitMigration = True
            if self.recovering:
                try:
                    if self._isDomainRunning():
                        waitMigration = False
                        self.log.info('migration completed while recovering!')
                except libvirt.libvirtError:
                    self.log.exception('migration failed while recovering!')
                    raise MigrationError()
            if waitMigration:
                usedTimeout = self._waitForUnderlyingMigration()
                self._attachLibvirtDomainAfterMigration(
                    self._incomingMigrationFinished.isSet(), usedTimeout)
            # else domain connection already established earlier
            self._domDependentInit()
            del self.conf['migrationDest']
            hooks.after_vm_migrate_destination(self._dom.XMLDesc(0), self.conf)

            for dev in self._customDevices():
                hooks.after_device_migrate_destination(
                    dev._deviceXML, self.conf, dev.custom)

            # TODO: _syncGuestTime() should be called here as it is in
            # restore_state path.  But there may be some issues with the call
            # such as blocking for some time when qemu-guest-agent is not
            # running in the guest.  We'd like to discuss them more before
            # touching migration.

        if 'guestIPs' in self.conf:
            del self.conf['guestIPs']
        if 'guestFQDN' in self.conf:
            del self.conf['guestFQDN']
        if 'username' in self.conf:
            del self.conf['username']
        self.saveState()
        self.log.info("End of migration")

    def _waitForUnderlyingMigration(self):
        timeout = config.getint('vars', 'migration_destination_timeout')
        self.log.debug("Waiting %s seconds for end of migration", timeout)
        self._incomingMigrationFinished.wait(timeout)
        return timeout

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
                self.log.debug("NOTE: incomingMigrationFinished event has "
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
        hooks.before_vm_cont(self._dom.XMLDesc(0), self.conf)
        self._dom.resume()

    def _underlyingPause(self):
        hooks.before_vm_pause(self._dom.XMLDesc(0), self.conf)
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

    def snapshot(self, snapDrives, memoryParams, frozen=False):
        """Live snapshot command"""

        def _diskSnapshot(vmDev, newPath, sourceType):
            """Libvirt snapshot XML"""

            disk = vmxml.Element('disk', name=vmDev, snapshot='external',
                                 type=sourceType)
            # Libvirt versions before 1.2.2 do not understand 'type' and treat
            # all snapshots as if they are type='file'.  In order to ensure
            # proper handling of block snapshots in modern libvirt versions,
            # we specify type='block' and dev=path for block volumes but we
            # always speficy the file=path for backwards compatibility.
            args = {'type': sourceType, 'file': newPath}
            if sourceType == 'block':
                args['dev'] = newPath
            disk.appendChildWithArgs('source', **args)
            return disk

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

        if self.isMigrating():
            return response.error('migInProgress')

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

            snapType = 'block' if vmDrives[vmDevName].blockDev else 'file'
            snapelem = _diskSnapshot(vmDevName, newDrives[vmDevName]["path"],
                                     snapType)
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

        snapxml = snap.toprettyxml()
        # TODO: this is debug information. For 3.6.x we still need to
        # see the XML even with 'info' as default level.
        self.log.info(snapxml)

        # We need to stop the collection of the stats for two reasons, one
        # is to prevent spurious libvirt errors about missing drive paths
        # (since we're changing them), and also to prevent to trigger a drive
        # extension for the new volume with the apparent size of the old one
        # (the apparentsize is updated as last step in updateDriveParameters)
        self.stopDisksStatsCollection()

        try:
            if should_freeze:
                freezed = self.freeze()
            try:
                self._dom.snapshotCreateXML(snapxml, snapFlags)
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
            self.startDisksStatsCollection()
            if memoryParams:
                self.cif.teardownVolumePath(memoryVol)

        # Returning quiesce to notify the manager whether the guest agent
        # froze and flushed the filesystems or not.
        quiesce = should_freeze and freezed["status"]["code"] == 0
        return {'status': doneCode, 'quiesce': quiesce}

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

    def diskReplicateFinish(self, srcDisk, dstDisk):
        try:
            drive = self._findDriveByUUIDs(srcDisk)
        except LookupError:
            return response.error('imageErr')

        if drive.hasVolumeLeases:
            return response.error('noimpl')

        if drive.transientDisk:
            return response.error('transientErr')

        if not drive.isDiskReplicationInProgress():
            return response.error('replicaErr')

        # Looking for the replication blockJob info (checking its presence)
        blkJobInfo = self._dom.blockJobInfo(drive.name, 0)

        if (not isinstance(blkJobInfo, dict)
                or 'cur' not in blkJobInfo or 'end' not in blkJobInfo):
            self.log.error("Replication job not found for disk %s (%s)",
                           drive.name, srcDisk)

            # Making sure that we don't have any stale information
            self._delDiskReplica(drive)
            return response.error('replicaErr')

        # Checking if we reached the replication mode ("mirroring" in libvirt
        # and qemu terms)
        if blkJobInfo['cur'] != blkJobInfo['end']:
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

            # We need to stop the stats collection in order to avoid spurious
            # errors from the stats threads during the switch from the old
            # drive to the new one. This applies only to the case where we
            # actually switch to the destination.
            self.stopDisksStatsCollection()
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
            self.startDisksStatsCollection()

        return {'status': doneCode}

    def _startDriveReplication(self, drive):
        destxml = drive.getReplicaXML().toprettyxml()
        self.log.debug("Replicating drive %s to %s", drive.name, destxml)

        flags = (libvirt.VIR_DOMAIN_BLOCK_COPY_SHALLOW |
                 libvirt.VIR_DOMAIN_BLOCK_COPY_REUSE_EXT)

        # TODO: Remove fallback when using libvirt >= 1.2.9.
        try:
            self._dom.blockCopy(drive.name, destxml, flags=flags)
        except libvirt.libvirtError as e:
            if e.get_error_code() != libvirt.VIR_ERR_NO_SUPPORT:
                raise

            self.log.warning("blockCopy not supported, using blockRebase")

            base = drive.diskReplicate["path"]
            self.log.debug("Replicating drive %s to %s", drive.name, base)

            flags = (libvirt.VIR_DOMAIN_BLOCK_REBASE_COPY |
                     libvirt.VIR_DOMAIN_BLOCK_REBASE_REUSE_EXT |
                     libvirt.VIR_DOMAIN_BLOCK_REBASE_SHALLOW)

            if drive.diskReplicate["diskType"] == DISK_TYPE.BLOCK:
                flags |= libvirt.VIR_DOMAIN_BLOCK_REBASE_COPY_DEV

            self._dom.blockRebase(drive.name, base, flags=flags)

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
        # Apparently this is what libvirt would do anyway, except that
        # it would fail on NFS when root_squash is enabled, see BZ#963881
        # Patches have been submitted to avoid this behavior, the virtual
        # and apparent sizes will be returned by the qemu process and
        # through the libvirt blockInfo call.
        curVirtualSize = qemuimg.info(drive.path, "qcow2")['virtualsize']

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
            # In all cases we want to try and fix the size in the metadata.
            # Same as above, this is what libvirt would do, see BZ#963881
            # Note that newVirtualSize may be larger than the requested size
            # because of rounding in qemu.
            newVirtualSize = qemuimg.info(drive.path, "qcow2")['virtualsize']
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

    def changeCD(self, drivespec):
        if self.arch in caps.Architecture.POWER:
            blockdev = 'sda'
        else:
            blockdev = 'hdc'

        return self._changeBlockDev('cdrom', blockdev, drivespec)

    def changeFloppy(self, drivespec):
        return self._changeBlockDev('floppy', 'fda', drivespec)

    def _changeBlockDev(self, vmDev, blockdev, drivespec):
        try:
            path = self.cif.prepareVolumePath(drivespec)
        except VolumeError:
            return response.error('imageErr')
        diskelem = vmxml.Element('disk', type='file', device=vmDev)
        diskelem.appendChildWithArgs('source', file=path)
        diskelem.appendChildWithArgs('target', dev=blockdev)

        try:
            self._dom.updateDeviceFlags(
                diskelem.toxml(), libvirt.VIR_DOMAIN_DEVICE_MODIFY_FORCE)
        except Exception:
            self.log.debug("updateDeviceFlags failed", exc_info=True)
            self.cif.teardownVolumePath(drivespec)
            return response.error('changeDisk')
        if vmDev in self.conf:
            self.cif.teardownVolumePath(self.conf[vmDev])

        self.conf[vmDev] = path
        return {'status': doneCode, 'vmList': self.status()}

    def setTicket(self, otp, seconds, connAct, params):
        """
        setTicket defaults to the first graphic device.
        use updateDevice to select the device.
        """
        try:
            graphics = self._domain.get_device_elements('graphics')[0]
        except IndexError:
            return response.error('ticketErr',
                                  'no graphics devices configured')
        return self._setTicketForGraphicDev(
            graphics, otp, seconds, connAct, params)

    def _setTicketForGraphicDev(self, graphics, otp, seconds, connAct, params):
        graphics.setAttribute('passwd', otp.value)
        if int(seconds) > 0:
            validto = time.strftime('%Y-%m-%dT%H:%M:%S',
                                    time.gmtime(time.time() + float(seconds)))
            graphics.setAttribute('passwdValidTo', validto)
        if graphics.getAttribute('type') == 'spice':
            graphics.setAttribute('connected', connAct)
        hooks.before_vm_set_ticket(self._domain.xml, self.conf, params)
        try:
            self._dom.updateDeviceFlags(graphics.toxml(), 0)
            disconnectAction = params.get('disconnectAction',
                                          ConsoleDisconnectAction.LOCK_SCREEN)
            self._consoleDisconnectAction = disconnectAction
        except virdomain.TimeoutError as tmo:
            res = response.error('ticketErr', unicode(tmo))
        else:
            hooks.after_vm_set_ticket(self._domain.xml, self.conf, params)
            res = {'status': doneCode}
        return res

    def _reviveTicket(self, newlife):
        """
        Revive an existing ticket, if it has expired or about to expire.
        Needs to be called only if Vm.hasSpice == True
        """
        graphics = self._findGraphicsDeviceXMLByType('spice')  # cannot fail
        validto = max(time.strptime(graphics.getAttribute('passwdValidTo'),
                                    '%Y-%m-%dT%H:%M:%S'),
                      time.gmtime(time.time() + newlife))
        graphics.setAttribute(
            'passwdValidTo', time.strftime('%Y-%m-%dT%H:%M:%S', validto))
        graphics.setAttribute('connected', 'keep')
        self._dom.updateDeviceFlags(graphics.toxml(), 0)

    def _findGraphicsDeviceXMLByType(self, deviceType):
        """
        libvirt (as in 1.2.3) supports only one graphic device per type
        """
        for graphics in _domParseStr(
            self._dom.XMLDesc(libvirt.VIR_DOMAIN_XML_SECURE)). \
                childNodes[0].getElementsByTagName('graphics'):
            if graphics.getAttribute('type') == deviceType:
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
            drive = self._lookupDeviceByAlias(hwclass.DISK, alias)
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
        return self.conf['vmName']

    def _getPid(self):
        try:
            pid = supervdsm.getProxy().getVmPid(
                self.name.encode('utf-8'))
        except (IOError, ValueError):
            self.log.error('cannot read pid')
            raise
        else:
            if pid <= 0:
                raise ValueError('read invalid pid: %i' % pid)
            return pid

    def _updateDomainDescriptor(self):
        domainXML = self._dom.XMLDesc(0)
        self._domain = DomainDescriptor(domainXML)

    def _ejectFloppy(self):
        if 'volatileFloppy' in self.conf:
            utils.rmFile(self.conf['floppy'])
        self._changeBlockDev('floppy', 'fda', '')

    def releaseVm(self):
        """
        Stop VM and release all resources
        """

        # delete the payload devices
        for drive in self._devices[hwclass.DISK]:
            if (hasattr(drive, 'specParams') and
                    'vmPayload' in drive.specParams):
                supervdsm.getProxy().removeFs(drive.path)

        with self._releaseLock:
            if self._released:
                return {'status': doneCode}

            # unsetting mirror network will clear both mirroring
            # (on the same network).
            for nic in self._devices[hwclass.NIC]:
                if hasattr(nic, 'portMirroring'):
                    for network in nic.portMirroring[:]:
                        supervdsm.getProxy().unsetPortMirroring(network,
                                                                nic.name)
                        nic.portMirroring.remove(network)

            self.log.info('Release VM resources')
            self.lastStatus = vmstatus.POWERING_DOWN
            # Terminate the VM's creation thread.
            self._incomingMigrationFinished.set()
            self.guestAgent.stop()
            if self._dom.connected:
                result = self._destroyVmGraceful()
                if result['status']['code']:
                    return result

            # Wait for any Live Merge cleanup threads.  This will only block in
            # the extremely rare case where a VM is being powered off at the
            # same time as a live merge is being finalized.  These threads
            # finish quickly unless there are storage connection issues.
            for t in self._liveMergeCleanupThreads.values():
                t.join()

            self._cleanup()

            self.cif.irs.inappropriateDevices(self.id)

            hooks.after_vm_destroy(self._domain.xml, self.conf)
            for dev in self._customDevices():
                hooks.after_device_destroy(dev._deviceXML, self.conf,
                                           dev.custom)

            self._released = True

        return {'status': doneCode}

    def _destroyVmGraceful(self):
        try:
            self._dom.destroyFlags(libvirt.VIR_DOMAIN_DESTROY_GRACEFUL)
        except libvirt.libvirtError as e:
            # after succesfull migraions
            if (self.lastStatus == vmstatus.DOWN and
                    e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN):
                self.log.info("VM '%s' already down and destroyed",
                              self.conf['vmId'])
            else:
                self.log.warning("Failed to destroy VM '%s' gracefully",
                                 self.conf['vmId'], exc_info=True)
                if e.get_error_code() == libvirt.VIR_ERR_OPERATION_FAILED:
                    return self._destroyVmForceful()
                return response.error('destroyErr')
        return {'status': doneCode}

    def _destroyVmForceful(self):
        try:
            self._dom.destroy()
        except libvirt.libvirtError:
            self.log.warning("Failed to destroy VM '%s'",
                             self.conf['vmId'], exc_info=True)
            return response.error('destroyErr')
        return {'status': doneCode}

    def deleteVm(self):
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
                           self.conf['vmId'], len(self.cif.vmContainer))

    def destroy(self):
        self.log.debug('destroy Called')

        result = self.doDestroy()
        if result['status']['code']:
            return result
        # Clean VM from the system
        self.deleteVm()

        return {'status': doneCode}

    def doDestroy(self):
        for dev in self._customDevices():
            hooks.before_device_destroy(dev._deviceXML, self.conf,
                                        dev.custom)

        hooks.before_vm_destroy(self._domain.xml, self.conf)
        with self._shutdownLock:
            self._shutdownReason = vmexitreason.ADMIN_SHUTDOWN
        self._destroyed = True

        return self.releaseVm()

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

    def setBalloonTarget(self, target):

        if not self._dom.connected:
            return self._reportError(key='balloonErr')
        try:
            target = int(target)
            self._dom.setMemory(target)
        except ValueError:
            return self._reportException(
                key='balloonErr', msg='an integer is required for target')
        except libvirt.libvirtError as e:
            if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                return self._reportException(key='noVM')
            return self._reportException(key='balloonErr', msg=e.message)
        else:
            for dev in self.conf['devices']:
                if dev['type'] == hwclass.BALLOON and \
                        dev['specParams']['model'] != 'none':
                    dev['target'] = target
            # persist the target value to make it consistent after recovery
            self.saveState()
            return {'status': doneCode}

    def setCpuTuneQuota(self, quota):
        try:
            self._dom.setSchedulerParameters({'vcpu_quota': int(quota)})
        except ValueError:
            return self._reportError(key='cpuTuneErr',
                                     msg='an integer is required for period')
        except libvirt.libvirtError as e:
            return self._reportException(key='cpuTuneErr', msg=e.message)
        else:
            # libvirt may change the value we set, so we must get fresh data
            return self._updateVcpuTuneInfo()

    def setCpuTunePeriod(self, period):
        try:
            self._dom.setSchedulerParameters({'vcpu_period': int(period)})
        except ValueError:
            return self._reportError(key='cpuTuneErr',
                                     msg='an integer is required for period')
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

    def _reportError(self, key, msg=None):
        """
        Produce an error status.
        """
        if msg is None:
            error = errCode[key]
        else:
            error = response.error(key, msg)
        return error

    def _reportException(self, key, msg=None):
        """
        Convert an exception to an error status.
        This method should be called only within exception-handling context.
        """
        self.log.exception("Operation failed")
        return self._reportError(key, msg)

    def _getUnderlyingDeviceAddress(self, devXml, index=0):
        """
        Obtain device's address from libvirt
        """
        address = {}
        adrXml = devXml.getElementsByTagName('address')[index]
        # Parse address to create proper dictionary.
        # Libvirt device's address definition is:
        # PCI = {'type':'pci', 'domain':'0x0000', 'bus':'0x00',
        #        'slot':'0x0c', 'function':'0x0'}
        # IDE = {'type':'drive', 'controller':'0', 'bus':'0', 'unit':'0'}
        for key in adrXml.attributes.keys():
            address[key.strip()] = adrXml.getAttribute(key).strip()

        return address

    def _getUnderlyingUnknownDeviceInfo(self):
        """
        Obtain unknown devices info from libvirt.

        Unknown device is a device that has an address but wasn't
        passed during VM creation request.
        """
        def isKnownDevice(alias):
            for dev in self.conf['devices']:
                if dev.get('alias') == alias:
                    return True
            return False

        for x in self._domain.devices.childNodes:
            # Ignore empty nodes and devices without address
            if (x.nodeName == '#text' or
                    not x.getElementsByTagName('address')):
                continue

            alias = x.getElementsByTagName('alias')[0].getAttribute('name')
            if not isKnownDevice(alias):
                address = self._getUnderlyingDeviceAddress(x)
                # I general case we assume that device has attribute 'type',
                # if it hasn't getAttribute returns ''.
                device = x.getAttribute('type')
                newDev = {'type': x.nodeName,
                          'alias': alias,
                          'device': device,
                          'address': address}
                self.conf['devices'].append(newDev)

    def _getUnderlyingControllerDeviceInfo(self):
        """
        Obtain controller devices info from libvirt.
        """
        for x in self._domain.get_device_elements('controller'):
            # Ignore controller devices without address
            if not x.getElementsByTagName('address'):
                continue
            alias = x.getElementsByTagName('alias')[0].getAttribute('name')
            device = x.getAttribute('type')
            # Get model and index. Relevant for USB controllers.
            model = x.getAttribute('model')
            index = x.getAttribute('index')

            # Get controller address
            address = self._getUnderlyingDeviceAddress(x)

            # In case the controller has index and/or model, they
            # are compared. Currently relevant for USB controllers.
            for ctrl in self._devices[hwclass.CONTROLLER]:
                if ((ctrl.device == device) and
                        (not hasattr(ctrl, 'index') or ctrl.index == index) and
                        (not hasattr(ctrl, 'model') or ctrl.model == model)):
                    ctrl.alias = alias
                    ctrl.address = address
            # Update vm's conf with address for known controller devices
            # In case the controller has index and/or model, they
            # are compared. Currently relevant for USB controllers.
            knownDev = False
            for dev in self.conf['devices']:
                if ((dev['type'] == hwclass.CONTROLLER) and
                        (dev['device'] == device) and
                        ('index' not in dev or dev['index'] == index) and
                        ('model' not in dev or dev['model'] == model)):
                    dev['address'] = address
                    dev['alias'] = alias
                    knownDev = True
            # Add unknown controller device to vm's conf
            if not knownDev:
                self.conf['devices'].append(
                    {'type': hwclass.CONTROLLER,
                     'device': device,
                     'address': address,
                     'alias': alias})

    def _getUnderlyingBalloonDeviceInfo(self):
        """
        Obtain balloon device info from libvirt.
        """
        for x in self._domain.get_device_elements('memballoon'):
            # Ignore balloon devices without address.
            if not x.getElementsByTagName('address'):
                address = None
            else:
                address = self._getUnderlyingDeviceAddress(x)
            alias = x.getElementsByTagName('alias')[0].getAttribute('name')

            for dev in self._devices[hwclass.BALLOON]:
                if address and not hasattr(dev, 'address'):
                    dev.address = address
                if not hasattr(dev, 'alias'):
                    dev.alias = alias

            for dev in self.conf['devices']:
                if dev['type'] == hwclass.BALLOON:
                    if address and not dev.get('address'):
                        dev['address'] = address
                    if not dev.get('alias'):
                        dev['alias'] = alias

    def _getUnderlyingConsoleDeviceInfo(self):
        """
        Obtain the alias for the console device from libvirt
        """
        for x in self._domain.get_device_elements('console'):
            # All we care about is the alias
            alias = x.getElementsByTagName('alias')[0].getAttribute('name')
            for dev in self._devices[hwclass.CONSOLE]:
                if not hasattr(dev, 'alias'):
                    dev.alias = alias

            for dev in self.conf['devices']:
                if dev['device'] == hwclass.CONSOLE and \
                        not dev.get('alias'):
                    dev['alias'] = alias

    def _getUnderlyingSmartcardDeviceInfo(self):
        """
        Obtain smartcard device info from libvirt.
        """
        for x in self._domain.get_device_elements('smartcard'):
            if not x.getElementsByTagName('address'):
                continue

            address = self._getUnderlyingDeviceAddress(x)
            alias = x.getElementsByTagName('alias')[0].getAttribute('name')

            for dev in self._devices[hwclass.SMARTCARD]:
                if not hasattr(dev, 'address'):
                    dev.address = address
                    dev.alias = alias

            for dev in self.conf['devices']:
                if dev['type'] == hwclass.SMARTCARD and \
                        not dev.get('address'):
                    dev['address'] = address
                    dev['alias'] = alias

    def _getUnderlyingRngDeviceInfo(self):
        """
        Obtain rng device info from libvirt.
        """
        for rng in self._domain.get_device_elements('rng'):
            address = self._getUnderlyingDeviceAddress(rng)
            alias = rng.getElementsByTagName('alias')[0].getAttribute('name')
            source = rng.getElementsByTagName('backend')[0].firstChild.\
                nodeValue

            for dev in self._devices[hwclass.RNG]:
                if caps.RNG_SOURCES[dev.specParams['source']] == source and \
                        not hasattr(dev, 'alias'):
                    dev.address = address
                    dev.alias = alias
                    break

            for dev in self.conf['devices']:
                if dev['type'] == hwclass.RNG and \
                        caps.RNG_SOURCES[dev['specParams']['source']] == \
                        source and 'alias' not in dev:
                    dev['address'] = address
                    dev['alias'] = alias
                    break

    def _getUnderlyingHostDeviceUSBInfo(self, x):
        alias = x.getElementsByTagName('alias')[0].getAttribute('name')
        address = self._getUnderlyingDeviceAddress(x)

        # The routine is quite unusual because we cannot directly reconstruct
        # the unique name. Therefore, we first look up correspondoing device
        # object address,
        for dev in self._devices[hwclass.HOSTDEV]:
            if address == dev.hostAddress:
                dev.alias = alias
                device = dev.device

        # and use that to identify the device in self.conf.
        for dev in self.conf['devices']:
            if dev['device'] == device:
                dev['alias'] = alias

        # This has an unfortunate effect that we will not be able to look up
        # any new USB passthrough devices, because there is no easy
        # way of reconstructing the udev name we use as unique id.

    def _getUnderlyingHostDeviceInfo(self):
        """
        Obtain host device info from libvirt
        """
        for x in self._domain.get_device_elements('hostdev'):
            device_type = x.getAttribute('type')
            if device_type == 'usb':
                self._getUnderlyingHostDeviceUSBInfo(x)
                continue
            alias = x.getElementsByTagName('alias')[0].getAttribute('name')
            address = self._getUnderlyingDeviceAddress(x)
            source = x.getElementsByTagName('source')[0]
            device = hostdev.pci_address_to_name(
                **self._getUnderlyingDeviceAddress(source))

            # We can assume the device name to be correct since we're
            # inspecting source element. For the address, we may look at
            # both addresses and determine the correct one.
            if (hostdev.pci_address_to_name(**address) == device):
                address = self._getUnderlyingDeviceAddress(x, 1)

            known_device = False
            for dev in self.conf['devices']:
                if dev['device'] == device:
                    dev['alias'] = alias
                    dev['address'] = address

            for dev in self._devices[hwclass.HOSTDEV]:
                if dev.device == device:
                    dev.alias = alias
                    dev.address = address
                    known_device = True

            if not known_device:
                device = hostdev.pci_address_to_name(
                    **self._getUnderlyingDeviceAddress(source))

                hostdevice = {'type': hwclass.HOSTDEV,
                              'device': device,
                              'alias': alias,
                              'address': address}
                self.conf['devices'].append(hostdevice)

    def _getUnderlyingWatchdogDeviceInfo(self):
        """
        Obtain watchdog device info from libvirt.
        """
        for x in self._domain.get_device_elements('watchdog'):

            # PCI watchdog has "address" different from ISA watchdog
            if x.getElementsByTagName('address'):
                address = self._getUnderlyingDeviceAddress(x)
                alias = x.getElementsByTagName('alias')[0].getAttribute('name')

                for wd in self._devices[hwclass.WATCHDOG]:
                    if not hasattr(wd, 'address') or not hasattr(wd, 'alias'):
                        wd.address = address
                        wd.alias = alias

                for dev in self.conf['devices']:
                    if ((dev['type'] == hwclass.WATCHDOG) and
                            (not dev.get('address') or not dev.get('alias'))):
                        dev['address'] = address
                        dev['alias'] = alias

    def _getUnderlyingVideoDeviceInfo(self):
        """
        Obtain video devices info from libvirt.
        """
        for x in self._domain.get_device_elements('video'):
            alias = x.getElementsByTagName('alias')[0].getAttribute('name')
            # Get video card address
            address = self._getUnderlyingDeviceAddress(x)

            # FIXME. We have an identification problem here.
            # Video card device has not unique identifier, except the alias
            # (but backend not aware to device's aliases). So, for now
            # we can only assign the address according to devices order.
            for vc in self._devices[hwclass.VIDEO]:
                if not hasattr(vc, 'address') or not hasattr(vc, 'alias'):
                    vc.alias = alias
                    vc.address = address
                    break
            # Update vm's conf with address
            for dev in self.conf['devices']:
                if ((dev['type'] == hwclass.VIDEO) and
                        (not dev.get('address') or not dev.get('alias'))):
                    dev['address'] = address
                    dev['alias'] = alias
                    break

    def _getUnderlyingSoundDeviceInfo(self):
        """
        Obtain sound devices info from libvirt.
        """
        for x in self._domain.get_device_elements('sound'):
            alias = x.getElementsByTagName('alias')[0].getAttribute('name')
            # Get sound card address
            address = self._getUnderlyingDeviceAddress(x)

            # FIXME. We have an identification problem here.
            # Sound device has not unique identifier, except the alias
            # (but backend not aware to device's aliases). So, for now
            # we can only assign the address according to devices order.
            for sc in self._devices[hwclass.SOUND]:
                if not hasattr(sc, 'address') or not hasattr(sc, 'alias'):
                    sc.alias = alias
                    sc.address = address
                    break
            # Update vm's conf with address
            for dev in self.conf['devices']:
                if ((dev['type'] == hwclass.SOUND) and
                        (not dev.get('address') or not dev.get('alias'))):
                    dev['address'] = address
                    dev['alias'] = alias
                    break

    def _getDriveIdentification(self, dom):
        sources = dom.getElementsByTagName('source')
        if sources:
            devPath = (sources[0].getAttribute('file') or
                       sources[0].getAttribute('dev') or
                       sources[0].getAttribute('name'))
        else:
            devPath = ''
        target = dom.getElementsByTagName('target')
        name = target[0].getAttribute('dev') if target else ''
        alias = dom.getElementsByTagName('alias')[0].getAttribute('name')
        return alias, devPath, name

    def _getUnderlyingDriveInfo(self):
        """
        Obtain block devices info from libvirt.
        """
        # FIXME!  We need to gather as much info as possible from the libvirt.
        # In the future we can return this real data to management instead of
        # vm's conf
        for x in self._domain.get_device_elements('disk'):
            alias, devPath, name = self._getDriveIdentification(x)
            readonly = bool(x.getElementsByTagName('readonly'))
            boot = x.getElementsByTagName('boot')
            bootOrder = boot[0].getAttribute('order') if boot else ''

            devType = x.getAttribute('device')
            if devType == 'disk':
                # raw/qcow2
                drv = x.getElementsByTagName('driver')[0].getAttribute('type')
            else:
                drv = 'raw'
            # Get disk address
            address = self._getUnderlyingDeviceAddress(x)

            # Keep data as dict for easier debugging
            deviceDict = {'path': devPath, 'name': name,
                          'readonly': readonly, 'bootOrder': bootOrder,
                          'address': address, 'type': devType, 'boot': boot}

            # display indexed pairs of ordered values from 2 dicts
            # such as {key_1: (valueA_1, valueB_1), ...}
            def mergeDicts(deviceDef, dev):
                return dict((k, (deviceDef[k], getattr(dev, k, None)))
                            for k in deviceDef.iterkeys())

            self.log.debug('Looking for drive with attributes %s', deviceDict)
            for d in self._devices[hwclass.DISK]:
                # When we analyze a disk device that was already discovered in
                # the past (generally as soon as the VM is created) we should
                # verify that the cached path is the one used in libvirt.
                # We already hit few times the problem that after a live
                # migration the paths were not in sync anymore (BZ#1059482).
                if (hasattr(d, 'alias') and d.alias == alias
                        and d.path != devPath):
                    self.log.warning('updating drive %s path from %s to %s',
                                     d.alias, d.path, devPath)
                    d.path = devPath
                if d.path == devPath:
                    d.name = name
                    d.type = devType
                    d.drv = drv
                    d.alias = alias
                    d.address = address
                    d.readonly = readonly
                    if bootOrder:
                        d.bootOrder = bootOrder
                    self.log.debug('Matched %s', mergeDicts(deviceDict, d))
            # Update vm's conf with address for known disk devices
            knownDev = False
            for dev in self.conf['devices']:
                # See comment in previous loop. This part is used to update
                # the vm configuration as well.
                if ('alias' in dev and dev['alias'] == alias
                        and dev['path'] != devPath):
                    self.log.warning('updating drive %s config path from %s '
                                     'to %s', dev['alias'], dev['path'],
                                     devPath)
                    dev['path'] = devPath
                if (dev['type'] == hwclass.DISK and
                        dev['path'] == devPath):
                    dev['name'] = name
                    dev['address'] = address
                    dev['alias'] = alias
                    dev['readonly'] = str(readonly)
                    if bootOrder:
                        dev['bootOrder'] = bootOrder
                    self.log.debug('Matched %s', mergeDicts(deviceDict, dev))
                    knownDev = True
            # Add unknown disk device to vm's conf
            if not knownDev:
                archIface = vmdevices.storage.\
                    DEFAULT_INTERFACE_FOR_ARCH[self.arch]
                iface = archIface if address['type'] == 'drive' else 'pci'
                diskDev = {'type': hwclass.DISK, 'device': devType,
                           'iface': iface, 'path': devPath, 'name': name,
                           'address': address, 'alias': alias,
                           'readonly': str(readonly)}
                if bootOrder:
                    diskDev['bootOrder'] = bootOrder
                self.log.warn('Found unknown drive: %s', diskDev)
                self.conf['devices'].append(diskDev)

    def _getUnderlyingGraphicsDeviceInfo(self):
        """
        Obtain graphic framebuffer devices info from libvirt.
        Libvirt only allows at most one device of each type, so mapping between
        _devices and conf.devices is based on this fact.
        The libvirt docs are a bit sparse on this topic, but as of libvirt
        1.2.3 if one tries to create a domain with 2+ SPICE graphics devices,
        virsh responds: error: unsupported configuration: only 1 graphics
        device of each type (sdl, vnc, spice) is supported
        """

        for gxml in self._domain.get_device_elements('graphics'):
            port = gxml.getAttribute('port')
            tlsPort = gxml.getAttribute('tlsPort')
            graphicsType = gxml.getAttribute('type')

            for d in self._devices[hwclass.GRAPHICS]:
                if d.device == graphicsType:
                    if port:
                        d.port = port
                    if tlsPort:
                        d.tlsPort = tlsPort
                    break

            for dev in self.conf['devices']:
                if (dev.get('type') == hwclass.GRAPHICS and
                        dev.get('device') == graphicsType):
                    if port:
                        dev['port'] = port
                    if tlsPort:
                        dev['tlsPort'] = tlsPort
                    break

        # the first graphic device is duplicated in the legacy conf
        vmdevices.graphics.updateLegacyConf(self.conf)

    def _getUnderlyingNetworkInterfaceInfo(self):
        """
        Obtain network interface info from libvirt.
        """
        for x in self._domain.get_device_elements('interface'):
            devType = x.getAttribute('type')
            mac = x.getElementsByTagName('mac')[0].getAttribute('address')
            alias = x.getElementsByTagName('alias')[0].getAttribute('name')
            xdrivers = x.getElementsByTagName('driver')
            driver = ({'name': xdrivers[0].getAttribute('name'),
                       'queues': xdrivers[0].getAttribute('queues')}
                      if xdrivers else {})
            if devType == 'hostdev':
                name = alias
                model = 'passthrough'
            else:
                name = x.getElementsByTagName('target')[0].getAttribute('dev')
                model = x.getElementsByTagName('model')[0].getAttribute('type')

            network = None
            try:
                if x.getElementsByTagName('link')[0].getAttribute('state') == \
                        'down':
                    linkActive = False
                else:
                    linkActive = True
            except IndexError:
                linkActive = True
            source = x.getElementsByTagName('source')
            if source:
                network = source[0].getAttribute('bridge')
                if not network:
                    network = source[0].getAttribute('network')
                    network = network[len(netinfo.LIBVIRT_NET_PREFIX):]

            # Get nic address
            address = {}
            # TODO: fix _getUnderlyingDeviceAddress and its users to have this
            # TODO: code.
            for child in x.childNodes:
                if (child.nodeType != Node.TEXT_NODE and
                        child.tagName == 'address'):
                    address = dict((k.strip(), child.getAttribute(k).strip())
                                   for k in child.attributes.keys())
                    break

            for nic in self._devices[hwclass.NIC]:
                if nic.macAddr.lower() == mac.lower():
                    nic.name = name
                    nic.alias = alias
                    nic.address = address
                    nic.linkActive = linkActive
                    if driver:
                        # If a driver was reported, pass it back to libvirt.
                        # Engine (vm's conf) is not interested in this value.
                        nic.driver = driver
            # Update vm's conf with address for known nic devices
            knownDev = False
            for dev in self.conf['devices']:
                if (dev['type'] == hwclass.NIC and
                        dev['macAddr'].lower() == mac.lower()):
                    dev['address'] = address
                    dev['alias'] = alias
                    dev['name'] = name
                    dev['linkActive'] = linkActive
                    knownDev = True
            # Add unknown nic device to vm's conf
            if not knownDev:
                nicDev = {'type': hwclass.NIC,
                          'device': devType,
                          'macAddr': mac,
                          'nicModel': model,
                          'address': address,
                          'alias': alias,
                          'name': name,
                          'linkActive': linkActive}
                if network:
                    nicDev['network'] = network
                self.conf['devices'].append(nicDev)

    def _getUnderlyingMemoryDeviceInfo(self):
        """
        Obtain memory device info from libvirt.
        """
        for x in self._domain.get_device_elements('memory'):
            alias = x.getElementsByTagName('alias')[0].getAttribute('name')
            # Get device address
            address = self._getUnderlyingDeviceAddress(x)

            for mem in self._devices[hwclass.MEMORY]:
                if not hasattr(mem, 'address') or not hasattr(mem, 'alias'):
                    mem.alias = alias
                    mem.address = address
                    break
            # Update vm's conf with address
            for dev in self.conf['devices']:
                if ((dev['type'] == hwclass.MEMORY) and
                        (not dev.get('address') or not dev.get('alias'))):
                    dev['address'] = address
                    dev['alias'] = alias
                    break
        self.conf['memSize'] = self._domain.get_memory_size()

    def _setWriteWatermarks(self):
        """
        Define when to receive an event about high write to guest image
        Currently unavailable by libvirt.
        """
        pass

    def onLibvirtLifecycleEvent(self, event, detail, opaque):
        self.log.debug('event %s detail %s opaque %s',
                       eventToString(event), detail, opaque)
        if event == libvirt.VIR_DOMAIN_EVENT_STOPPED:
            if (detail == libvirt.VIR_DOMAIN_EVENT_STOPPED_MIGRATED and
                    self.lastStatus == vmstatus.MIGRATION_SOURCE):
                hooks.after_vm_migrate_source(self._domain.xml, self.conf)
                for dev in self._customDevices():
                    hooks.after_device_migrate_source(
                        dev._deviceXML, self.conf, dev.custom)
            elif (detail == libvirt.VIR_DOMAIN_EVENT_STOPPED_SAVED and
                    self.lastStatus == vmstatus.SAVING_STATE):
                hooks.after_vm_hibernate(self._domain.xml, self.conf)
            else:
                if detail == libvirt.VIR_DOMAIN_EVENT_STOPPED_SHUTDOWN:
                    with self._shutdownLock:
                        if self._shutdownReason is None:
                            # do not overwrite admin shutdown, if present
                            self._shutdownReason = vmexitreason.USER_SHUTDOWN
                self._onQemuDeath()
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
                    hooks.after_vm_pause(domxml, self.conf)

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
                    hooks.after_vm_cont(domxml, self.conf)
            elif (detail == libvirt.VIR_DOMAIN_EVENT_RESUMED_MIGRATED and
                  self.lastStatus == vmstatus.MIGRATION_DESTINATION):
                self._incomingMigrationFinished.set()

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

        for deviceXML in vmxml.all_devices(xml):
            aliasElement = deviceXML.getElementsByTagName('alias')
            if aliasElement:
                alias = aliasElement[0].getAttribute('name')

                if alias in aliasToDevice:
                    aliasToDevice[alias]._deviceXML = deviceXML.toxml()
            elif deviceXML.tagName == hwclass.GRAPHICS:
                # graphics device do not have aliases, must match by type
                graphicsType = deviceXML.getAttribute('type')
                for devObj in self._devices[hwclass.GRAPHICS]:
                    if devObj.device == graphicsType:
                        devObj._deviceXML = deviceXML.toxml()

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
        with self._confLock:
            srcDomXML = self.conf.pop('_srcDomXML').encode('utf-8')
        self._updateDevicesDomxmlCache(srcDomXML)

        for dev in self._customDevices():
            hooks.before_device_migrate_destination(
                dev._deviceXML, self.conf, dev.custom)

        hooks.before_vm_migrate_destination(srcDomXML, self.conf)
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

    def _activeLayerCommitReady(self, jobInfo):
        try:
            pivot = libvirt.VIR_DOMAIN_BLOCK_JOB_TYPE_ACTIVE_COMMIT
        except AttributeError:
            return False
        if (jobInfo['cur'] == jobInfo['end'] and jobInfo['type'] == pivot):
            return True
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
                cleanThread = self._liveMergeCleanupThreads.get(jobID)
                if cleanThread and cleanThread.isSuccessful():
                    # Handle successful jobs early because the job just needs
                    # to be untracked and the stored disk info might be stale
                    # anyway (ie. after active layer commit).
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
                    entry['bandwidth'] = liveInfo['bandwidth']
                    entry['cur'] = str(liveInfo['cur'])
                    entry['end'] = str(liveInfo['end'])
                    doPivot = self._activeLayerCommitReady(liveInfo)
                else:
                    # Libvirt has stopped reporting this job so we know it will
                    # never report it again.
                    doPivot = False
                    storedJob['gone'] = True
                if not liveInfo or doPivot:
                    if not cleanThread:
                        # There is no cleanup thread so the job must have just
                        # ended.  Spawn an async cleanup.
                        startCleanup(storedJob, drive, doPivot)
                    elif cleanThread.isAlive():
                        # Let previously started cleanup thread continue
                        self.log.debug("Still waiting for block job %s to be "
                                       "synchronized", jobID)
                    elif not cleanThread.isSuccessful():
                        # At this point we know the thread is not alive and the
                        # cleanup failed.  Retry it with a new thread.
                        startCleanup(storedJob, drive, doPivot)
                jobsRet[jobID] = entry
        return jobsRet

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

        base = top = None
        for v in drive.volumeChain:
            if v['volumeID'] == baseVolUUID:
                base = v['path']
            if v['volumeID'] == topVolUUID:
                top = v['path']
        if base is None:
            self.log.error("merge: base volume '%s' not found", baseVolUUID)
            return response.error('mergeErr')
        if top is None:
            self.log.error("merge: top volume '%s' not found", topVolUUID)
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

        # Take the jobs lock here to protect the new job we are tracking from
        # being cleaned up by queryBlockJobs() since it won't exist right away
        with self._jobsLock:
            try:
                self.trackBlockJob(jobUUID, drive, baseVolUUID, topVolUUID,
                                   'commit')
            except BlockJobExistsError:
                self.log.error("A block job is already active on this disk")
                return response.error('mergeErr')
            self.log.info("Starting merge with jobUUID='%s'", jobUUID)

            try:
                ret = self._dom.blockCommit(drive.path, base, top, bandwidth,
                                            flags)
                if ret != 0:
                    raise RuntimeError("blockCommit failed rc:%i", ret)
            except (RuntimeError, libvirt.libvirtError):
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

    def _diskXMLGetVolumeChainInfo(self, diskXML, drive):
        def find_element_by_name(doc, name):
            for child in doc.childNodes:
                if child.nodeName == name:
                    return child
            return None

        def pathToVolID(drive, path):
            for vol in drive.volumeChain:
                if os.path.realpath(vol['path']) == os.path.realpath(path):
                    return vol['volumeID']
            raise LookupError("Unable to find VolumeID for path '%s'", path)

        volChain = []
        while True:
            sourceXML = find_element_by_name(diskXML, 'source')
            if not sourceXML:
                break
            sourceAttr = ('file', 'dev')[drive.blockDev]
            path = sourceXML.getAttribute(sourceAttr)

            # TODO: Allocation information is not available in the XML.  Switch
            # to the new interface once it becomes available in libvirt.
            alloc = None
            bsXML = find_element_by_name(diskXML, 'backingStore')
            if not bsXML:
                self.log.warning("<backingStore/> missing from backing "
                                 "chain for drive %s", drive.name)
                break
            diskXML = bsXML
            entry = VolumeChainEntry(pathToVolID(drive, path), path, alloc)
            volChain.insert(0, entry)
        return volChain or None

    def _driveGetActualVolumeChain(self, drives):
        def lookupDeviceXMLByAlias(domXML, targetAlias):
            for deviceXML, alias in _devicesWithAlias(domXML):
                if alias == targetAlias:
                    return deviceXML
            raise LookupError("Unable to find matching XML for device %s",
                              targetAlias)

        ret = {}
        self._updateDomainDescriptor()
        for drive in drives:
            alias = drive['alias']
            diskXML = lookupDeviceXMLByAlias(self._domain.xml, alias)
            volChain = self._diskXMLGetVolumeChainInfo(diskXML, drive)
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
        self.cif.irs.imageSyncVolumeChain(drive.domainID, drive.imageID,
                                          drive['volumeID'], volumes)

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

    def _fixLegacyGraphicsConf(self):
        with self._confLock:
            if not vmdevices.graphics.getFirstGraphics(self.conf):
                self.conf['devices'].extend(self.getConfGraphics())

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

    def getBalloonDevicesConf(self):
        for dev in self.conf['devices']:
            if dev['type'] == hwclass.BALLOON:
                yield dev

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

    def _setUnresponsiveIfTimeout(self, stats, statsAge):
        if (not self.isMigrating()
                and statsAge > config.getint('vars', 'vm_command_timeout')
                and stats['monitorResponse'] != '-1'):
            self.log.warning('monitor become unresponsive'
                             ' (command timeout, age=%s)',
                             statsAge)
            stats['monitorResponse'] = '-1'

    def updateNumaInfo(self):
        self._numaInfo = numaUtils.getVmNumaNodeRuntimeInfo(self)

    @property
    def hasGuestNumaNode(self):
        return 'guestNumaNodes' in self.conf

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


class LiveMergeCleanupThread(threading.Thread):
    def __init__(self, vm, job, drive, doPivot):
        threading.Thread.__init__(self)
        self.setDaemon(True)
        self.vm = vm
        self.job = job
        self.drive = drive
        self.doPivot = doPivot
        self.success = False

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
        # attempt to collect disk stats.
        # TODO: Stop collection only for the live merge disk
        self.vm.stopDisksStatsCollection()

        self.vm.log.info("Requesting pivot to complete active layer commit "
                         "(job %s)", self.job['jobID'])
        try:
            flags = libvirt.VIR_DOMAIN_BLOCK_JOB_ABORT_PIVOT
            ret = self.vm._dom.blockJobAbort(self.drive.name, flags)
        except:
            self.vm.startDisksStatsCollection()
            raise
        else:
            if ret != 0:
                self.vm.log.error("Pivot failed for job %s (rc=%i)",
                                  self.job['jobID'], ret)
                raise RuntimeError("pivot failed")
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

    @utils.traceback()
    def run(self):
        self.update_base_size()
        if self.doPivot:
            self.tryPivot()
        self.vm.log.info("Synchronizing volume chain after live merge "
                         "(job %s)", self.job['jobID'])
        self.vm._syncVolumeChain(self.drive)
        if self.doPivot:
            self.vm.startDisksStatsCollection()
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


def _devicesWithAlias(domXML):
    return vmxml.filter_devices_with_alias(vmxml.all_devices(domXML))
