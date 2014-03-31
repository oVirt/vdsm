#
# Copyright 2008-2013 Red Hat, Inc.
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
from contextlib import contextmanager
from copy import deepcopy
from xml.dom import Node
from xml.dom.minidom import parseString as _domParseStr
import logging
import os
import pickle
import tempfile
import threading
import time
import xml.dom.minidom

# 3rd party libs imports
import libvirt

# vdsm imports
from vdsm import constants
from vdsm import libvirtconnection
from vdsm import netinfo
from vdsm import qemuImg
from vdsm import utils
from vdsm import vdscli
from vdsm.config import config
from vdsm.define import ERROR, NORMAL, doneCode, errCode
from vdsm.netinfo import DUMMY_BRIDGE
from storage import outOfProcess as oop
from storage import sd
from storage import fileUtils

# local imports
from logUtils import SimpleLogAdapter
import caps
import guestIF
import hooks
import kaxmlrpclib
import sampling
import supervdsm

_VMCHANNEL_DEVICE_NAME = 'com.redhat.rhevm.vdsm'
# This device name is used as default both in the qemu-guest-agent
# service/daemon and in libvirtd (to be used with the quiesce flag).
_QEMU_GA_DEVICE_NAME = 'org.qemu.guest_agent.0'
_AGENT_CHANNEL_DEVICES = (_VMCHANNEL_DEVICE_NAME, _QEMU_GA_DEVICE_NAME)

DEFAULT_BRIDGE = config.get("vars", "default_bridge")

DISK_DEVICES = 'disk'
NIC_DEVICES = 'interface'
VIDEO_DEVICES = 'video'
SOUND_DEVICES = 'sound'
CONTROLLER_DEVICES = 'controller'
GENERAL_DEVICES = 'general'
BALLOON_DEVICES = 'balloon'
REDIR_DEVICES = 'redir'
RNG_DEVICES = 'rng'
WATCHDOG_DEVICES = 'watchdog'
CONSOLE_DEVICES = 'console'
SMARTCARD_DEVICES = 'smartcard'


def isVdsmImage(drive):
    """
    Tell if drive looks like a vdsm image

    :param drive: drive to check
    :type drive: dict or vm.Drive
    :return: bool
    """
    required = ('domainID', 'imageID', 'poolID', 'volumeID')
    return all(k in drive for k in required)


def _filterSnappableDiskDevices(diskDeviceXmlElements):
        return filter(lambda(x): not(x.getAttribute('device')) or
                      x.getAttribute('device') in ['disk', 'lun'],
                      diskDeviceXmlElements)


class _MigrationError(RuntimeError):
    pass


class MigrationSourceThread(threading.Thread):
    """
    A thread that takes care of migration on the source vdsm.
    """
    _ongoingMigrations = threading.BoundedSemaphore(1)

    @classmethod
    def setMaxOutgoingMigrations(cls, n):
        """Set the initial value of the _ongoingMigrations semaphore.

        must not be called after any vm has been run."""
        cls._ongoingMigrations = threading.BoundedSemaphore(n)

    def __init__(self, vm, dst='', dstparams='',
                 mode='remote', method='online',
                 tunneled=False, dstqemu='', abortOnError=False, **kwargs):
        self.log = vm.log
        self._vm = vm
        self._dst = dst
        self._mode = mode
        self._method = method
        self._dstparams = dstparams
        self._machineParams = {}
        self._tunneled = utils.tobool(tunneled)
        self._abortOnError = utils.tobool(abortOnError)
        self._dstqemu = dstqemu
        self._downtime = kwargs.get('downtime') or \
            config.get('vars', 'migration_downtime')
        self.status = {
            'status': {
                'code': 0,
                'message': 'Migration in progress'},
            'progress': 0}
        threading.Thread.__init__(self)
        self._preparingMigrationEvt = True
        self._migrationCanceledEvt = False
        self._monitorThread = None

    def getStat(self):
        """
        Get the status of the migration.
        """
        if self._monitorThread is not None:
            # fetch migration status from the monitor thread
            self.status['progress'] = int(
                float(self._monitorThread.data_progress +
                      self._monitorThread.mem_progress) / 2)
        return self.status

    def _setupVdsConnection(self):
        if self._mode == 'file':
            return

        # FIXME: The port will depend on the binding being used.
        # This assumes xmlrpc
        hostPort = vdscli.cannonizeHostPort(
            self._dst, self._vm.cif.bindings['xmlrpc'].serverPort)
        self.remoteHost, self.remotePort = hostPort.rsplit(':', 1)

        if config.getboolean('vars', 'ssl'):
            self.destServer = vdscli.connect(
                hostPort,
                useSSL=True,
                TransportClass=kaxmlrpclib.TcpkeepSafeTransport)
        else:
            self.destServer = kaxmlrpclib.Server('http://' + hostPort)
        self.log.debug('Destination server is: ' + hostPort)
        try:
            self.log.debug('Initiating connection with destination')
            status = self.destServer.getVmStats(self._vm.id)
            if not status['status']['code']:
                self.log.error("Machine already exists on the destination")
                self.status = errCode['exist']
        except Exception:
            self.log.error("Error initiating connection", exc_info=True)
            self.status = errCode['noConPeer']

    def _setupRemoteMachineParams(self):
        self._machineParams.update(self._vm.status())
        # patch VM config for targets < 3.1
        self._patchConfigForLegacy()
        self._machineParams['elapsedTimeOffset'] = \
            time.time() - self._vm._startTime
        vmStats = self._vm.getStats()
        if 'username' in vmStats:
            self._machineParams['username'] = vmStats['username']
        if 'guestIPs' in vmStats:
            self._machineParams['guestIPs'] = vmStats['guestIPs']
        if 'guestFQDN' in vmStats:
            self._machineParams['guestFQDN'] = vmStats['guestFQDN']
        for k in ('_migrationParams', 'pid'):
            if k in self._machineParams:
                del self._machineParams[k]
        if self._mode != 'file':
            self._machineParams['migrationDest'] = 'libvirt'
        self._machineParams['_srcDomXML'] = self._vm._dom.XMLDesc(0)

    def _prepareGuest(self):
        if self._mode == 'file':
            self.log.debug("Save State begins")
            if self._vm.guestAgent.isResponsive():
                lockTimeout = 30
            else:
                lockTimeout = 0
            self._vm.guestAgent.desktopLock()
            #wait for lock or timeout
            while lockTimeout:
                if self._vm.getStats()['session'] in ["Locked", "LoggedOff"]:
                    break
                time.sleep(1)
                lockTimeout -= 1
                if lockTimeout == 0:
                    self.log.warning('Agent ' + self._vm.id +
                                     ' unresponsive. Hiberanting without '
                                     'desktopLock.')
                    break
            self._vm.pause('Saving State')
        else:
            self.log.debug("Migration started")
            self._vm.lastStatus = 'Migration Source'

    def _recover(self, message):
        if not self.status['status']['code']:
            self.status = errCode['migrateErr']
        self.log.error(message)
        if self._mode != 'file':
            try:
                self.destServer.destroy(self._vm.id)
            except Exception:
                self.log.error("Failed to destroy remote VM", exc_info=True)
        # if the guest was stopped before migration, we need to cont it
        if self._mode == 'file' or self._method != 'online':
            self._vm.cont()
        # either way, migration has finished
        self._vm.lastStatus = 'Up'

    def _finishSuccessfully(self):
        self.status['progress'] = 100
        if self._mode != 'file':
            self._vm.setDownStatus(NORMAL, "Migration succeeded")
            self.status['status']['message'] = 'Migration done'
        else:
            # don't pickle transient params
            for ignoreParam in ('displayIp', 'display', 'pid'):
                if ignoreParam in self._machineParams:
                    del self._machineParams[ignoreParam]

            fname = self._vm.cif.prepareVolumePath(self._dstparams)
            try:
                with open(fname, "w") as f:
                    pickle.dump(self._machineParams, f)
            finally:
                self._vm.cif.teardownVolumePath(self._dstparams)

            self._vm.setDownStatus(NORMAL, "SaveState succeeded")
            self.status['status']['message'] = 'SaveState done'

    def _patchConfigForLegacy(self):
        """
        Remove from the VM config drives list "cdrom" and "floppy"
        items and set them up as full paths
        """
        # care only about "drives" list, since
        # "devices" doesn't cause errors
        if 'drives' in self._machineParams:
            for item in ("cdrom", "floppy"):
                new_drives = []
                for drive in self._machineParams['drives']:
                    if drive['device'] == item:
                        self._machineParams[item] = drive['path']
                    else:
                        new_drives.append(drive)
                self._machineParams['drives'] = new_drives

        # vdsm < 4.13 expect this to exist
        self._machineParams['afterMigrationStatus'] = ''

    @staticmethod
    def _raiseAbortError():
        e = libvirt.libvirtError(defmsg='')
        # we have to override the value to get what we want
        # err might be None
        e.err = (libvirt.VIR_ERR_OPERATION_ABORTED,  # error code
                 libvirt.VIR_FROM_QEMU,              # error domain
                 'operation aborted',                # error message
                 libvirt.VIR_ERR_WARNING,            # error level
                 '', '', '',                         # str1, str2, str3,
                 -1, -1)                             # int1, int2
        raise e

    def run(self):
        try:
            startTime = time.time()
            self._setupVdsConnection()
            self._setupRemoteMachineParams()
            self._prepareGuest()
            MigrationSourceThread._ongoingMigrations.acquire()
            try:
                if self._migrationCanceledEvt:
                    self._raiseAbortError()
                self.log.debug("migration semaphore acquired")
                self._vm.conf['_migrationParams'] = {
                    'dst': self._dst,
                    'mode': self._mode,
                    'method': self._method,
                    'dstparams': self._dstparams,
                    'dstqemu': self._dstqemu}
                self._vm.saveState()
                self._startUnderlyingMigration(startTime)
                self._finishSuccessfully()
            except libvirt.libvirtError as e:
                if e.get_error_code() == libvirt.VIR_ERR_OPERATION_ABORTED:
                    self.status['status']['code'] = \
                        errCode['migCancelErr']['status']['code']
                    self.status['status']['message'] = 'Migration canceled'
                raise
            finally:
                if '_migrationParams' in self._vm.conf:
                    del self._vm.conf['_migrationParams']
                MigrationSourceThread._ongoingMigrations.release()
        except Exception as e:
            self._recover(str(e))
            self.log.error("Failed to migrate", exc_info=True)

    def _startUnderlyingMigration(self, startTime):
        if self._mode == 'file':
            hooks.before_vm_hibernate(self._vm._dom.XMLDesc(0), self._vm.conf)
            try:
                self._vm._vmStats.pause()
                fname = self._vm.cif.prepareVolumePath(self._dst)
                try:
                    self._vm._dom.save(fname)
                finally:
                    self._vm.cif.teardownVolumePath(self._dst)
            except Exception:
                self._vm._vmStats.cont()
                raise
        else:
            for dev in self._vm._customDevices():
                hooks.before_device_migrate_source(
                    dev._deviceXML, self._vm.conf, dev.custom)
            hooks.before_vm_migrate_source(self._vm._dom.XMLDesc(0),
                                           self._vm.conf)
            response = self.destServer.migrationCreate(self._machineParams)
            if response['status']['code']:
                self.status = response
                raise RuntimeError('migration destination error: ' +
                                   response['status']['message'])
            if config.getboolean('vars', 'ssl'):
                transport = 'tls'
            else:
                transport = 'tcp'
            duri = 'qemu+%s://%s/system' % (transport, self.remoteHost)
            if self._vm.conf['_migrationParams']['dstqemu']:
                muri = 'tcp://%s' % \
                       self._vm.conf['_migrationParams']['dstqemu']
            else:
                muri = 'tcp://%s' % self.remoteHost

            self._vm.log.debug('starting migration to %s '
                               'with miguri %s', duri, muri)

            t = MigrationDowntimeThread(self._vm, int(self._downtime))

            if MigrationMonitorThread._MIGRATION_MONITOR_INTERVAL:
                self._monitorThread = MigrationMonitorThread(self._vm,
                                                             startTime)
                self._monitorThread.start()

            try:
                if ('qxl' in self._vm.conf['display'] and
                        self._vm.conf.get('clientIp')):
                    SPICE_MIGRATION_HANDOVER_TIME = 120
                    self._vm._reviveTicket(SPICE_MIGRATION_HANDOVER_TIME)

                maxBandwidth = config.getint('vars', 'migration_max_bandwidth')
                #FIXME: there still a race here with libvirt,
                # if we call stop() and libvirt migrateToURI2 didn't start
                # we may return migration stop but it will start at libvirt
                # side
                self._preparingMigrationEvt = False
                if not self._migrationCanceledEvt:
                    self._vm._dom.migrateToURI2(
                        duri, muri, None,
                        libvirt.VIR_MIGRATE_LIVE |
                        libvirt.VIR_MIGRATE_PEER2PEER |
                        (libvirt.VIR_MIGRATE_TUNNELLED if
                            self._tunneled else 0) |
                        (libvirt.VIR_MIGRATE_ABORT_ON_ERROR if
                            self._abortOnError else 0),
                        None, maxBandwidth)
                else:
                    self._raiseAbortError()

            finally:
                t.cancel()
                if MigrationMonitorThread._MIGRATION_MONITOR_INTERVAL:
                    self._monitorThread.stop()

    def stop(self):
        # if its locks we are before the migrateToURI2()
        # call so no need to abortJob()
        try:
            self._migrationCanceledEvt = True
            self._vm._dom.abortJob()
        except libvirt.libvirtError:
            if not self._preparingMigrationEvt:
                    raise


class VolumeError(RuntimeError):
    def __str__(self):
        return "Bad volume specification " + RuntimeError.__str__(self)


class DoubleDownError(RuntimeError):
    pass

VALID_STATES = ('Down', 'Migration Destination', 'Migration Source',
                'Paused', 'Powering down', 'RebootInProgress',
                'Restoring state', 'Saving State',
                'Up', 'WaitForLaunch')


class MERGESTATUS:
    NOT_STARTED = "Not Started"
    IN_PROGRESS = "In Progress"
    FAILED = "Failed"
    COMPLETED = "Completed"
    UNKNOWN = "Unknown"
    DRIVE_NOT_FOUND = "Drive Not Found"
    BASE_NOT_FOUND = "Base Not Found"
    DRIVE_NOT_SUPPORTED = "Drive Not Supported"


class DRIVE_SHARED_TYPE:
    NONE = "none"
    EXCLUSIVE = "exclusive"
    SHARED = "shared"
    TRANSIENT = "transient"

    @classmethod
    def getAllValues(cls):
        # TODO: use introspection
        return (cls.NONE, cls.EXCLUSIVE, cls.SHARED, cls.TRANSIENT)


# These strings are representing libvirt virDomainEventType values
# http://libvirt.org/html/libvirt-libvirt.html#virDomainEventType
_EVENT_STRINGS = ("Defined",
                  "Undefined",
                  "Started",
                  "Suspended",
                  "Resumed",
                  "Stopped",
                  "Shutdown",
                  "PM-Suspended")


def eventToString(event):
    return _EVENT_STRINGS[event]


class SetLinkAndNetworkError(Exception):
    pass


class UpdatePortMirroringError(Exception):
    pass


class VmStatsThread(sampling.AdvancedStatsThread):
    MBPS_TO_BPS = 10 ** 6 / 8

    def __init__(self, vm):
        sampling.AdvancedStatsThread.__init__(self, log=vm.log, daemon=True)
        self._vm = vm

        self.highWrite = (
            sampling.AdvancedStatsFunction(
                self._highWrite,
                config.getint('vars', 'vm_watermark_interval')))
        self.updateVolumes = (
            sampling.AdvancedStatsFunction(
                self._updateVolumes,
                config.getint('irs', 'vol_size_sample_interval')))

        self.sampleCpu = (
            sampling.AdvancedStatsFunction(
                self._sampleCpu,
                config.getint('vars', 'vm_sample_cpu_interval'),
                config.getint('vars', 'vm_sample_cpu_window')))
        self.sampleDisk = (
            sampling.AdvancedStatsFunction(
                self._sampleDisk,
                config.getint('vars', 'vm_sample_disk_interval'),
                config.getint('vars', 'vm_sample_disk_window')))
        self.sampleDiskLatency = (
            sampling.AdvancedStatsFunction(
                self._sampleDiskLatency,
                config.getint('vars', 'vm_sample_disk_latency_interval'),
                config.getint('vars', 'vm_sample_disk_latency_window')))
        self.sampleNet = (
            sampling.AdvancedStatsFunction(
                self._sampleNet,
                config.getint('vars', 'vm_sample_net_interval'),
                config.getint('vars', 'vm_sample_net_window')))

        self.addStatsFunction(
            self.highWrite, self.updateVolumes, self.sampleCpu,
            self.sampleDisk, self.sampleDiskLatency, self.sampleNet)

    def _highWrite(self):
        if not self._vm.isDisksStatsCollectionEnabled():
            # Avoid queries from storage during recovery process
            return
        self._vm.extendDrivesIfNeeded()

    def _updateVolumes(self):
        if not self._vm.isDisksStatsCollectionEnabled():
            # Avoid queries from storage during recovery process
            return

        for vmDrive in self._vm._devices[DISK_DEVICES]:
            self._vm.updateDriveVolume(vmDrive)

    def _sampleCpu(self):
        cpuStats = self._vm._dom.getCPUStats(True, 0)
        return cpuStats[0]

    def _sampleDisk(self):
        if not self._vm.isDisksStatsCollectionEnabled():
            # Avoid queries from storage during recovery process
            return

        diskSamples = {}
        for vmDrive in self._vm._devices[DISK_DEVICES]:
            diskSamples[vmDrive.name] = self._vm._dom.blockStats(vmDrive.name)

        return diskSamples

    def _sampleDiskLatency(self):
        if not self._vm.isDisksStatsCollectionEnabled():
            # Avoid queries from storage during recovery process
            return
        #{'wr_total_times': 0L, 'rd_operations': 9638L,
        # 'flush_total_times': 0L,'rd_total_times': 7622718001L,
        # 'rd_bytes': 85172430L, 'flush_operations': 0L,
        # 'wr_operations': 0L, 'wr_bytes': 0L}
        diskLatency = {}
        for vmDrive in self._vm._devices[DISK_DEVICES]:
            diskLatency[vmDrive.name] = self._vm._dom.blockStatsFlags(
                vmDrive.name, flags=libvirt.VIR_TYPED_PARAM_STRING_OKAY)
        return diskLatency

    def _sampleNet(self):
        netSamples = {}
        for nic in self._vm._devices[NIC_DEVICES]:
            netSamples[nic.name] = self._vm._dom.interfaceStats(nic.name)
        return netSamples

    def _diff(self, prev, curr, val):
        return prev[val] - curr[val]

    def _usagePercentage(self, val, sampleInterval):
        return 100 * val / sampleInterval / 1000 ** 3

    def _getCpuStats(self, stats):
        sInfo, eInfo, sampleInterval = self.sampleCpu.getStats()

        try:
            stats['cpuSys'] = self._usagePercentage(
                self._diff(eInfo, sInfo, 'user_time') +
                self._diff(eInfo, sInfo, 'system_time'),
                sampleInterval)
            stats['cpuUser'] = self._usagePercentage(
                self._diff(eInfo, sInfo, 'cpu_time')
                - self._diff(eInfo, sInfo, 'user_time')
                - self._diff(eInfo, sInfo, 'system_time'),
                sampleInterval)

        except (TypeError, ZeroDivisionError) as e:
            self._log.debug("CPU stats not available: %s", e)
            stats['cpuUser'] = 0.0
            stats['cpuSys'] = 0.0

    def _getNetworkStats(self, stats):
        stats['network'] = {}
        sInfo, eInfo, sampleInterval = self.sampleNet.getStats()

        for nic in self._vm._devices[NIC_DEVICES]:
            if nic.name.startswith('hostdev'):
                continue
            ifSpeed = [100, 1000][nic.nicModel in ('e1000', 'virtio')]

            ifStats = {'macAddr': nic.macAddr,
                       'name': nic.name,
                       'speed': str(ifSpeed),
                       'state': 'unknown'}

            try:
                ifStats['rxErrors'] = str(eInfo[nic.name][2])
                ifStats['rxDropped'] = str(eInfo[nic.name][3])
                ifStats['txErrors'] = str(eInfo[nic.name][6])
                ifStats['txDropped'] = str(eInfo[nic.name][7])

                ifRxBytes = (100.0 *
                             ((eInfo[nic.name][0] - sInfo[nic.name][0]) %
                              2 ** 32) /
                             sampleInterval / ifSpeed / self.MBPS_TO_BPS)
                ifTxBytes = (100.0 *
                             ((eInfo[nic.name][4] - sInfo[nic.name][4]) %
                              2 ** 32) /
                             sampleInterval / ifSpeed / self.MBPS_TO_BPS)

                ifStats['rxRate'] = '%.1f' % ifRxBytes
                ifStats['txRate'] = '%.1f' % ifTxBytes
            except (KeyError, TypeError, ZeroDivisionError):
                self._log.debug("Network stats not available")

            stats['network'][nic.name] = ifStats

    def _getDiskStats(self, stats):
        sInfo, eInfo, sampleInterval = self.sampleDisk.getStats()

        for vmDrive in self._vm._devices[DISK_DEVICES]:
            dName = vmDrive.name
            dStats = {}
            try:
                dStats = {'truesize': str(vmDrive.truesize),
                          'apparentsize': str(vmDrive.apparentsize)}
                if isVdsmImage(vmDrive):
                    dStats['imageID'] = vmDrive.imageID
                elif "GUID" in vmDrive:
                    dStats['lunGUID'] = vmDrive.GUID
                dStats['readRate'] = ((eInfo[dName][1] - sInfo[dName][1]) /
                                      sampleInterval)
                dStats['writeRate'] = ((eInfo[dName][3] - sInfo[dName][3]) /
                                       sampleInterval)
            except (AttributeError, KeyError, TypeError, ZeroDivisionError):
                self._log.debug("Disk %s stats not available", dName)

            stats[dName] = dStats

    def _getDiskLatency(self, stats):
        sInfo, eInfo, sampleInterval = self.sampleDiskLatency.getStats()

        def _avgLatencyCalc(sData, eData):
            readLatency = (0 if not (eData['rd_operations'] -
                                     sData['rd_operations'])
                           else (eData['rd_total_times'] -
                                 sData['rd_total_times']) /
                                (eData['rd_operations'] -
                                 sData['rd_operations']))
            writeLatency = (0 if not (eData['wr_operations'] -
                                      sData['wr_operations'])
                            else (eData['wr_total_times'] -
                                  sData['wr_total_times']) /
                                 (eData['wr_operations'] -
                                  sData['wr_operations']))
            flushLatency = (0 if not (eData['flush_operations'] -
                                      sData['flush_operations'])
                            else (eData['flush_total_times'] -
                                  sData['flush_total_times']) /
                                 (eData['flush_operations'] -
                                  sData['flush_operations']))
            return str(readLatency), str(writeLatency), str(flushLatency)

        for vmDrive in self._vm._devices[DISK_DEVICES]:
            dName = vmDrive.name
            dLatency = {'readLatency': '0',
                        'writeLatency': '0',
                        'flushLatency': '0'}
            try:
                (dLatency['readLatency'],
                 dLatency['writeLatency'],
                 dLatency['flushLatency']) = _avgLatencyCalc(sInfo[dName],
                                                             eInfo[dName])
            except (KeyError, TypeError):
                self._log.debug("Disk %s latency not available", dName)
            else:
                stats[dName].update(dLatency)

    def get(self):
        stats = {}

        try:
            stats['statsAge'] = time.time() - self.getLastSampleTime()
        except TypeError:
            self._log.debug("Stats age not available")
            stats['statsAge'] = -1.0

        self._getCpuStats(stats)
        self._getNetworkStats(stats)
        self._getDiskStats(stats)
        self._getDiskLatency(stats)

        return stats

    def handleStatsException(self, ex):
        # We currently handle only libvirt exceptions
        if not hasattr(ex, "get_error_code"):
            return False

        # We currently handle only the missing domain exception
        if ex.get_error_code() != libvirt.VIR_ERR_NO_DOMAIN:
            return False

        return True


class MigrationDowntimeThread(threading.Thread):
    def __init__(self, vm, downtime):
        super(MigrationDowntimeThread, self).__init__()
        self.DOWNTIME_STEPS = config.getint('vars', 'migration_downtime_steps')

        self._vm = vm
        self._downtime = downtime
        self._stop = threading.Event()

        delay_per_gib = config.getint('vars', 'migration_downtime_delay')
        memSize = int(vm.conf['memSize'])
        self._wait = (delay_per_gib * max(memSize, 2048) + 1023) / 1024

        self.daemon = True
        self.start()

    def run(self):
        self._vm.log.debug('migration downtime thread started')

        for i in range(self.DOWNTIME_STEPS):
            self._stop.wait(self._wait / self.DOWNTIME_STEPS)

            if self._stop.isSet():
                break

            downtime = self._downtime * (i + 1) / self.DOWNTIME_STEPS
            self._vm.log.debug('setting migration downtime to %d', downtime)
            self._vm._dom.migrateSetMaxDowntime(downtime, 0)

        self._vm.log.debug('migration downtime thread exiting')

    def cancel(self):
        self._vm.log.debug('canceling migration downtime thread')
        self._stop.set()


class MigrationMonitorThread(threading.Thread):
    _MIGRATION_MONITOR_INTERVAL = config.getint(
        'vars', 'migration_monitor_interval')  # seconds

    def __init__(self, vm, startTime):
        super(MigrationMonitorThread, self).__init__()
        self._stop = threading.Event()
        self._vm = vm
        self._startTime = startTime
        self.daemon = True
        self.data_progress = 0
        self.mem_progress = 0

    def run(self):
        def calculateProgress(remaining, total):
            if remaining == 0:
                return 100
            progress = 100 - 100 * remaining / total if total else 0
            return progress if (progress < 100) else 99

        self._vm.log.debug('starting migration monitor thread')

        memSize = int(self._vm.conf['memSize'])
        maxTimePerGiB = config.getint('vars',
                                      'migration_max_time_per_gib_mem')
        migrationMaxTime = (maxTimePerGiB * memSize + 1023) / 1024
        lastProgressTime = time.time()
        lowmark = None
        progress_timeout = config.getint('vars', 'migration_progress_timeout')

        while not self._stop.isSet():
            self._stop.wait(self._MIGRATION_MONITOR_INTERVAL)
            (jobType, timeElapsed, _,
             dataTotal, dataProcessed, dataRemaining,
             memTotal, memProcessed, memRemaining,
             fileTotal, fileProcessed, _) = self._vm._dom.jobInfo()

            remaining = dataRemaining + memRemaining
            abort = False
            now = time.time()
            if 0 < migrationMaxTime < now - self._startTime:
                self._vm.log.warn('The migration took %d seconds which is '
                                  'exceeding the configured maximum time '
                                  'for migrations of %d seconds. The '
                                  'migration will be aborted.',
                                  now - self._startTime,
                                  migrationMaxTime)
                abort = True
            elif (lowmark is None) or (lowmark > remaining):
                lowmark = remaining
                lastProgressTime = now
            elif (now - lastProgressTime) > progress_timeout:
                # Migration is stuck, abort
                self._vm.log.warn(
                    'Migration is stuck: Hasn\'t progressed in %s seconds. '
                    'Aborting.' % (now - lastProgressTime))
                abort = True

            if abort:
                self._vm._dom.abortJob()
                self.stop()
                break

            if remaining > lowmark:
                MiB = 1024 * 1024
                self._vm.log.warn(
                    'Migration stalling: remaining (%sMiB)'
                    ' > lowmark (%sMiB).'
                    ' Refer to RHBZ#919201.',
                    remaining / MiB, lowmark / MiB)

            if jobType == 0:
                continue

            self.data_progress = calculateProgress(dataRemaining, dataTotal)
            self.mem_progress = calculateProgress(memRemaining, memTotal)

            self._vm.log.info('Migration Progress: %s seconds elapsed, %s%% of'
                              ' data processed, %s%% of mem processed' %
                              (timeElapsed / 1000,
                                  self.data_progress,
                                  self.mem_progress))

    def stop(self):
        self._vm.log.debug('stopping migration monitor thread')
        self._stop.set()


class TimeoutError(libvirt.libvirtError):
    pass


class NotifyingVirDomain:
    # virDomain wrapper that notifies vm when a method raises an exception with
    # get_error_code() = VIR_ERR_OPERATION_TIMEOUT

    def __init__(self, dom, tocb):
        self._dom = dom
        self._cb = tocb

    def __getattr__(self, name):
        attr = getattr(self._dom, name)
        if not callable(attr):
            return attr

        def f(*args, **kwargs):
            try:
                ret = attr(*args, **kwargs)
                self._cb(False)
                return ret
            except libvirt.libvirtError as e:
                if e.get_error_code() == libvirt.VIR_ERR_OPERATION_TIMEOUT:
                    self._cb(True)
                    toe = TimeoutError(e.get_error_message())
                    toe.err = e.err
                    raise toe
                raise
        return f


class XMLElement(object):

    def __init__(self, tagName, text=None, **attrs):
        self._elem = xml.dom.minidom.Document().createElement(tagName)
        self.setAttrs(**attrs)
        if text is not None:
            self.appendTextNode(text)

    def __getattr__(self, name):
        return getattr(self._elem, name)

    def setAttrs(self, **attrs):
        for attrName, attrValue in attrs.iteritems():
            self._elem.setAttribute(attrName, attrValue)

    def appendTextNode(self, text):
        textNode = xml.dom.minidom.Document().createTextNode(text)
        self._elem.appendChild(textNode)

    def appendChild(self, element):
        self._elem.appendChild(element)

    def appendChildWithArgs(self, childName, text=None, **attrs):
        child = XMLElement(childName, text, **attrs)
        self._elem.appendChild(child)
        return child


class _DomXML:
    def __init__(self, conf, log, arch):
        """
        Create the skeleton of a libvirt domain xml

        <domain type="kvm">
            <name>vmName</name>
            <uuid>9ffe28b6-6134-4b1e-8804-1185f49c436f</uuid>
            <memory>262144</memory>
            <currentMemory>262144</currentMemory>
            <vcpu current='smp'>160</vcpu>
            <devices>
            </devices>
            <memtune>
                <min_guarantee>0</min_guarantee>
            </memtune>
        </domain>

        """
        self.conf = conf
        self.log = log

        self.arch = arch

        self.doc = xml.dom.minidom.Document()

        if utils.tobool(self.conf.get('kvmEnable', 'true')):
            domainType = 'kvm'
        else:
            domainType = 'qemu'

        domainAttrs = {'type': domainType}

        # Hack around libvirt issue BZ#988070, this is going to be removed as
        # soon as the domain XML format supports the specification of USB
        # keyboards

        if self.arch == caps.Architecture.PPC64:
            domainAttrs['xmlns:qemu'] = \
                'http://libvirt.org/schemas/domain/qemu/1.0'

        self.dom = XMLElement('domain', **domainAttrs)
        self.doc.appendChild(self.dom)

        self.dom.appendChildWithArgs('name', text=self.conf['vmName'])
        self.dom.appendChildWithArgs('uuid', text=self.conf['vmId'])
        memSizeKB = str(int(self.conf.get('memSize', '256')) * 1024)
        self.dom.appendChildWithArgs('memory', text=memSizeKB)
        self.dom.appendChildWithArgs('currentMemory', text=memSizeKB)
        vcpu = self.dom.appendChildWithArgs('vcpu', text=self._getMaxVCpus())
        vcpu.setAttrs(**{'current': self._getSmp()})

        memSizeGuaranteedKB = str(1024 * int(
            self.conf.get('memGuaranteedSize', '0')
        ))

        memtune = XMLElement('memtune')
        self.dom.appendChild(memtune)

        memtune.appendChildWithArgs('min_guarantee',
                                    text=memSizeGuaranteedKB)

        self._devices = XMLElement('devices')
        self.dom.appendChild(self._devices)

    def appendClock(self):
        """
        Add <clock> element to domain:

        <clock offset="variable" adjustment="-3600">
            <timer name="rtc" tickpolicy="catchup">
        </clock>
        """

        m = XMLElement('clock', offset='variable',
                       adjustment=str(self.conf.get('timeOffset', 0)))
        if utils.tobool(self.conf.get('tdf', True)):
            m.appendChildWithArgs('timer', name='rtc', tickpolicy='catchup')

        self.dom.appendChild(m)

    def appendOs(self):
        """
        Add <os> element to domain:

        <os>
            <type arch="x86_64" machine="pc">hvm</type>
            <boot dev="cdrom"/>
            <kernel>/tmp/vmlinuz-2.6.18</kernel>
            <initrd>/tmp/initrd-2.6.18.img</initrd>
            <cmdline>ARGs 1</cmdline>
            <smbios mode="sysinfo"/>
        </os>
        """

        oselem = XMLElement('os')
        self.dom.appendChild(oselem)

        DEFAULT_MACHINES = {caps.Architecture.X86_64: 'pc',
                            caps.Architecture.PPC64: 'pseries'}

        machine = self.conf.get('emulatedMachine', DEFAULT_MACHINES[self.arch])

        oselem.appendChildWithArgs('type', text='hvm', arch=self.arch,
                                   machine=machine)

        qemu2libvirtBoot = {'a': 'fd', 'c': 'hd', 'd': 'cdrom', 'n': 'network'}
        for c in self.conf.get('boot', ''):
            oselem.appendChildWithArgs('boot', dev=qemu2libvirtBoot[c])

        if self.conf.get('initrd'):
            oselem.appendChildWithArgs('initrd', text=self.conf['initrd'])

        if self.conf.get('kernel'):
            oselem.appendChildWithArgs('kernel', text=self.conf['kernel'])

        if self.conf.get('kernelArgs'):
            oselem.appendChildWithArgs('cmdline', text=self.conf['kernelArgs'])

        if self.arch == caps.Architecture.X86_64:
            oselem.appendChildWithArgs('smbios', mode='sysinfo')

    def appendSysinfo(self, osname, osversion, hostUUID):
        """
        Add <sysinfo> element to domain:

        <sysinfo type="smbios">
          <bios>
            <entry name="vendor">QEmu/KVM</entry>
            <entry name="version">0.13</entry>
          </bios>
          <system>
            <entry name="manufacturer">Fedora</entry>
            <entry name="product">Virt-Manager</entry>
            <entry name="version">0.8.2-3.fc14</entry>
            <entry name="serial">32dfcb37-5af1-552b-357c-be8c3aa38310</entry>
            <entry name="uuid">c7a5fdbd-edaf-9455-926a-d65c16db1809</entry>
          </system>
        </sysinfo>
        """

        sysinfoelem = XMLElement('sysinfo', type='smbios')
        self.dom.appendChild(sysinfoelem)

        syselem = XMLElement('system')
        sysinfoelem.appendChild(syselem)

        def appendEntry(k, v):
            syselem.appendChildWithArgs('entry', text=v, name=k)

        appendEntry('manufacturer', constants.SMBIOS_MANUFACTURER)
        appendEntry('product', osname)
        appendEntry('version', osversion)
        appendEntry('serial', hostUUID)
        appendEntry('uuid', self.conf['vmId'])

    def appendFeatures(self):
        """
        Add machine features to domain xml.

        Currently only
        <features>
            <acpi/>
        <features/>
        """

        if utils.tobool(self.conf.get('acpiEnable', 'true')):
            features = self.dom.appendChildWithArgs('features')
            features.appendChildWithArgs('acpi')

    def appendCpu(self):
        """
        Add guest CPU definition.

        <cpu match="exact">
            <model>qemu64</model>
            <topology sockets="S" cores="C" threads="T"/>
            <feature policy="require" name="sse2"/>
            <feature policy="disable" name="svm"/>
        </cpu>
        """

        cpu = XMLElement('cpu')

        if self.arch in (caps.Architecture.X86_64):
            cpu.setAttrs(match='exact')

            features = self.conf.get('cpuType', 'qemu64').split(',')
            model = features[0]

            if model == 'hostPassthrough':
                cpu.setAttrs(mode='host-passthrough')
            elif model == 'hostModel':
                cpu.setAttrs(mode='host-model')
            else:
                cpu.appendChildWithArgs('model', text=model)

                # This hack is for backward compatibility as the libvirt
                # does not allow 'qemu64' guest on intel hardware
                if model == 'qemu64' and not '+svm' in features:
                    features += ['-svm']

                for feature in features[1:]:
                    # convert Linux name of feature to libvirt
                    if feature[1:6] == 'sse4_':
                        feature = feature[0] + 'sse4.' + feature[6:]

                    featureAttrs = {'name': feature[1:]}
                    if feature[0] == '+':
                        featureAttrs['policy'] = 'require'
                    elif feature[0] == '-':
                        featureAttrs['policy'] = 'disable'
                    cpu.appendChildWithArgs('feature', **featureAttrs)

        if ('smpCoresPerSocket' in self.conf or
                'smpThreadsPerCore' in self.conf):
            maxVCpus = int(self._getMaxVCpus())
            cores = int(self.conf.get('smpCoresPerSocket', '1'))
            threads = int(self.conf.get('smpThreadsPerCore', '1'))
            cpu.appendChildWithArgs('topology',
                                    sockets=str(maxVCpus / cores / threads),
                                    cores=str(cores), threads=str(threads))

        #CPU-pinning support
        # see http://www.ovirt.org/wiki/Features/Design/cpu-pinning
        if 'cpuPinning' in self.conf:
            cputune = XMLElement('cputune')
            cpuPinning = self.conf.get('cpuPinning')
            for cpuPin in cpuPinning.keys():
                cputune.appendChildWithArgs('vcpupin', vcpu=cpuPin,
                                            cpuset=cpuPinning[cpuPin])
            self.dom.appendChild(cputune)

        self.dom.appendChild(cpu)

    def _appendAgentDevice(self, path, name):
        """
          <channel type='unix'>
             <target type='virtio' name='org.linux-kvm.port.0'/>
             <source mode='bind' path='/tmp/socket'/>
          </channel>
        """
        channel = XMLElement('channel', type='unix')
        channel.appendChildWithArgs('target', type='virtio', name=name)
        channel.appendChildWithArgs('source', mode='bind', path=path)
        self._devices.appendChild(channel)

    def appendInput(self):
        """
        Add input device.

        <input bus="ps2" type="mouse"/>
        """
        if utils.tobool(self.conf.get('tabletEnable')):
            inputAttrs = {'type': 'tablet', 'bus': 'usb'}
        else:
            if self.arch == caps.Architecture.PPC64:
                mouseBus = 'usb'
            else:
                mouseBus = 'ps2'

            inputAttrs = {'type': 'mouse', 'bus': mouseBus}
        self._devices.appendChildWithArgs('input', **inputAttrs)

    def appendKeyboardDevice(self):
        """
        Add keyboard device for ppc64 using a QEMU argument directly.
        This is a workaround to the issue BZ#988070 in libvirt

            <qemu:commandline>
                <qemu:arg value='-usbdevice'/>
                <qemu:arg value='keyboard'/>
            </qemu:commandline>
        """
        commandLine = XMLElement('qemu:commandline')
        commandLine.appendChildWithArgs('qemu:arg', value='-usbdevice')
        commandLine.appendChildWithArgs('qemu:arg', value='keyboard')
        self.dom.appendChild(commandLine)

    def appendGraphics(self):
        """
        Add graphics section to domain xml.

        <graphics autoport="yes" listen="0" type="vnc"/>

        or

        <graphics autoport="yes" keymap="en-us" listen="0" port="5910"
                  tlsPort="5890" type="spice" passwd="foo"
                  passwdValidTo="2010-04-09T15:51:00"/>
        <channel type='spicevmc'>
           <target type='virtio' name='com.redhat.spice.0'/>
        </channel>
        """
        graphicsAttrs = {'port': self.conf['displayPort'], 'autoport': 'yes'}
        if self.conf['display'] == 'vnc':
            graphicsAttrs['type'] = 'vnc'
        elif 'qxl' in self.conf['display']:
            graphicsAttrs['type'] = 'spice'
            graphicsAttrs['tlsPort'] = self.conf['displaySecurePort']

        if self.conf.get('keyboardLayout'):
            graphicsAttrs['keymap'] = self.conf['keyboardLayout']
        if not 'spiceDisableTicketing' in self.conf:
            graphicsAttrs['passwd'] = '*****'
            graphicsAttrs['passwdValidTo'] = '1970-01-01T00:00:01'

        graphics = XMLElement('graphics', **graphicsAttrs)

        if 'qxl' in self.conf['display']:
            if self.conf.get('spiceSecureChannels'):
                for channel in self.conf['spiceSecureChannels'].split(','):
                    graphics.appendChildWithArgs('channel', name=channel[1:],
                                                 mode='secure')

            vmc = XMLElement('channel', type='spicevmc')
            vmc.appendChildWithArgs('target', type='virtio',
                                    name='com.redhat.spice.0')
            self._devices.appendChild(vmc)

        if self.conf.get('displayNetwork'):
            graphics.appendChildWithArgs('listen', type='network',
                                         network=netinfo.LIBVIRT_NET_PREFIX +
                                         self.conf.get('displayNetwork'))
        else:
            graphics.setAttrs(listen='0')

        self._devices.appendChild(graphics)

    def appendEmulator(self):
        emulatorPath = '/usr/bin/qemu-system-' + self.arch

        emulator = XMLElement('emulator', text=emulatorPath)

        self._devices.appendChild(emulator)

    def toxml(self):
        return self.doc.toprettyxml(encoding='utf-8')

    def _getSmp(self):
        return self.conf.get('smp', '1')

    def _getMaxVCpus(self):
        return self.conf.get('maxVCpus', self._getSmp())


class VmDevice(object):
    def __init__(self, conf, log, **kwargs):
        for attr, value in kwargs.iteritems():
            try:
                setattr(self, attr, value)
            except AttributeError:  # skip read-only properties
                pass
        self.conf = conf
        self.log = log
        self._deviceXML = None

    def __str__(self):
        attrs = [':'.join((a, str(getattr(self, a)))) for a in dir(self)
                 if not a.startswith('__')]
        return ' '.join(attrs)

    def createXmlElem(self, elemType, deviceType, attributes=()):
        """
        Create domxml device element according to passed in params
        """
        elemAttrs = {}
        element = XMLElement(elemType)

        if deviceType:
            elemAttrs['type'] = deviceType

        for attrName in attributes:
            if not hasattr(self, attrName):
                continue

            attr = getattr(self, attrName)
            if isinstance(attr, dict):
                element.appendChildWithArgs(attrName, **attr)
            else:
                elemAttrs[attrName] = attr

        element.setAttrs(**elemAttrs)
        return element


class GeneralDevice(VmDevice):

    def getXML(self):
        """
        Create domxml for general device
        """
        return self.createXmlElem(self.type, self.device, ['address'])


class ControllerDevice(VmDevice):

    def getXML(self):
        """
        Create domxml for controller device
        """
        ctrl = self.createXmlElem('controller', self.device,
                                  ['index', 'model', 'master', 'address'])
        if self.device == 'virtio-serial':
            ctrl.setAttrs(index='0', ports='16')

        return ctrl


class VideoDevice(VmDevice):

    def getXML(self):
        """
        Create domxml for video device
        """
        video = self.createXmlElem('video', None, ['address'])
        sourceAttrs = {'vram': self.specParams.get('vram', '32768'),
                       'heads': self.specParams.get('heads', '1')}
        if 'ram' in self.specParams:
            sourceAttrs['ram'] = self.specParams['ram']

        video.appendChildWithArgs('model', type=self.device, **sourceAttrs)
        return video


class SoundDevice(VmDevice):

    def getXML(self):
        """
        Create domxml for sound device
        """
        sound = self.createXmlElem('sound', None, ['address'])
        sound.setAttrs(model=self.device)
        return sound


class NetworkInterfaceDevice(VmDevice):

    def __init__(self, conf, log, **kwargs):
        # pyLint can't tell that the Device.__init__() will
        # set a nicModel attribute, so modify the kwarg list
        # prior to device init.
        for attr, value in kwargs.iteritems():
            if attr == 'nicModel' and value == 'pv':
                kwargs[attr] = 'virtio'
            elif attr == 'network' and value == '':
                kwargs[attr] = DUMMY_BRIDGE
        VmDevice.__init__(self, conf, log, **kwargs)
        self.sndbufParam = False
        self._customize()

    def _customize(self):
        # Customize network device
        vhosts = self._getVHostSettings()
        self.driver = vhosts.get(self.network, False)
        try:
            self.sndbufParam = self.conf['custom']['sndbuf']
        except KeyError:
            pass    # custom_sndbuf not specified

    def _getVHostSettings(self):
        VHOST_MAP = {'true': 'vhost', 'false': 'qemu'}
        vhosts = {}
        vhostProp = self.conf.get('custom', {}).get('vhost', '')

        if vhostProp != '':
            for vhost in vhostProp.split(','):
                try:
                    vbridge, vstatus = vhost.split(':', 1)
                    vhosts[vbridge] = VHOST_MAP[vstatus.lower()]
                except (ValueError, KeyError):
                    self.log.warning("Unknown vhost format: %s", vhost)

        return vhosts

    def getXML(self):
        """
        Create domxml for network interface.

        <interface type="bridge">
            <mac address="aa:bb:dd:dd:aa:bb"/>
            <model type="virtio"/>
            <source bridge="engine"/>
            [<filterref filter='filter name'/>]
            [<tune><sndbuf>0</sndbuf></tune>]
            [<link state='up|down'/>]
            [<bandwidth>
              [<inbound average="int" [burst="int"]  [peak="int"]/>]
              [<outbound average="int" [burst="int"]  [peak="int"]/>]
             </bandwidth>]
        </interface>
        """
        iface = self.createXmlElem('interface', self.device, ['address'])
        iface.appendChildWithArgs('mac', address=self.macAddr)
        iface.appendChildWithArgs('model', type=self.nicModel)
        iface.appendChildWithArgs('source', bridge=self.network)
        if hasattr(self, 'filter'):
            iface.appendChildWithArgs('filterref', filter=self.filter)

        if hasattr(self, 'linkActive'):
            iface.appendChildWithArgs('link', state='up'
                                      if utils.tobool(self.linkActive)
                                      else 'down')

        if hasattr(self, 'bootOrder'):
            iface.appendChildWithArgs('boot', order=self.bootOrder)

        if self.driver:
            iface.appendChildWithArgs('driver', name=self.driver)

        if self.sndbufParam:
            tune = iface.appendChildWithArgs('tune')
            tune.appendChildWithArgs('sndbuf', text=self.sndbufParam)

        if hasattr(self, 'specParams'):
            if 'inbound' in self.specParams or 'outbound' in self.specParams:
                iface.appendChild(self.getXMLBandwidth(self.specParams))
        return iface

    def getXMLBandwidth(self, specParams, oldBandwidth=None):
        bandwidth = self.createXmlElem('bandwidth', None)
        # Inbound and Outbound traffic can be independently shaped.
        for attr in ('inbound', 'outbound'):
            newSetting = specParams.get(attr)
            # if newSetting is specified, replace current settings
            if newSetting:
                bandwidth.appendChildWithArgs(attr, **newSetting)
            # if newSetting is not specified, keep current settings
            elif newSetting is None:
                if oldBandwidth is not None:
                    attrXMLs = oldBandwidth.getElementsByTagName
                    attrXML = attrXMLs[0] if len(attrXMLs) else None
                else:
                    attrXML = None
                if attrXML is not None:
                    bandwidth.appendChild(attrXML)
            # if newSetting is {} do nothing = remove current settings
        return bandwidth


class Drive(VmDevice):
    VOLWM_CHUNK_MB = config.getint('irs', 'volume_utilization_chunk_mb')
    VOLWM_FREE_PCT = 100 - config.getint('irs', 'volume_utilization_percent')
    VOLWM_CHUNK_REPLICATE_MULT = 2  # Chunk multiplier during replication

    def __init__(self, conf, log, **kwargs):
        if not kwargs.get('serial'):
            self.serial = kwargs.get('imageID'[-20:]) or ''
        VmDevice.__init__(self, conf, log, **kwargs)
        # Keep sizes as int
        self.reqsize = int(kwargs.get('reqsize', '0'))  # Backward compatible
        self.truesize = int(kwargs.get('truesize', '0'))
        self.apparentsize = int(kwargs.get('apparentsize', '0'))
        self.name = self._makeName()
        self.cache = config.get('vars', 'qemu_drive_cache')

        if self.device in ("cdrom", "floppy"):
            self._blockDev = False
        else:
            self._blockDev = None

        self._customize()
        self._setExtSharedState()

    def _setExtSharedState(self):
        # We cannot use tobool here as shared can take several values
        # (e.g. none, exclusive) that would be all mapped to False.
        shared = str(getattr(self, "shared", "false")).lower()

        # Backward compatibility with the old values (true, false)
        if shared == 'true':
            self.extSharedState = DRIVE_SHARED_TYPE.SHARED
        elif shared == 'false':
            if config.getboolean('irs', 'use_volume_leases'):
                self.extSharedState = DRIVE_SHARED_TYPE.EXCLUSIVE
            else:
                self.extSharedState = DRIVE_SHARED_TYPE.NONE
        elif shared in DRIVE_SHARED_TYPE.getAllValues():
            self.extSharedState = shared
        else:
            raise ValueError("Unknown shared value %s" % shared)

    @property
    def hasVolumeLeases(self):
        if self.extSharedState != DRIVE_SHARED_TYPE.EXCLUSIVE:
            return False

        for volInfo in getattr(self, "volumeChain", []):
            if "leasePath" in volInfo and "leaseOffset" in volInfo:
                return True

        return False

    def __getitem__(self, key):
        try:
            value = getattr(self, str(key))
        except AttributeError:
            raise KeyError(key)
        else:
            return value

    def __contains__(self, attr):
        return hasattr(self, attr)

    def isDiskReplicationInProgress(self):
        return hasattr(self, "diskReplicate")

    @property
    def volExtensionChunk(self):
        """
        Returns the volume extension chunks (used for the thin provisioning
        on block devices). The value is based on the vdsm configuration but
        can also dynamically change according to the VM needs (e.g. increase
        during a live storage migration).
        """
        if self.isDiskReplicationInProgress():
            return self.VOLWM_CHUNK_MB * self.VOLWM_CHUNK_REPLICATE_MULT
        return self.VOLWM_CHUNK_MB

    @property
    def watermarkLimit(self):
        """
        Returns the watermark limit, when the LV usage reaches this limit an
        extension is in order (thin provisioning on block devices).
        """
        return (self.VOLWM_FREE_PCT * self.volExtensionChunk *
                constants.MEGAB / 100)

    def getNextVolumeSize(self):
        """
        Returns the next volume size in megabytes. This value is based on the
        volExtensionChunk property and it's the size that should be requested
        for the next LV extension.
        """
        return (self.volExtensionChunk +
                ((self.apparentsize + constants.MEGAB - 1) / constants.MEGAB))

    @property
    def networkDev(self):
        try:
            return self.volumeInfo['volType'] == "network"
        except AttributeError:
            # To handle legacy and removable drives.
            return False

    @property
    def blockDev(self):
        if self.networkDev:
            return False

        if self._blockDev is None:
            try:
                self._blockDev = utils.isBlockDevice(self.path)
            except Exception:
                self.log.debug("Unable to determine if the path '%s' is a "
                               "block device", self.path, exc_info=True)
        return self._blockDev

    @property
    def transientDisk(self):
        # Using getattr to handle legacy and removable drives.
        return getattr(self, 'shared', None) == DRIVE_SHARED_TYPE.TRANSIENT

    def _customize(self):
        if self.transientDisk:
            # Force the cache to be writethrough, which is qemu's default.
            # This is done to ensure that we don't ever use cache=none for
            # transient disks, since we create them in /var/run/vdsm which
            # may end up on tmpfs and don't support O_DIRECT, and qemu uses
            # O_DIRECT when cache=none and hence hotplug might fail with
            # error that one can take eternity to debug the reason behind it!
            self.cache = "writethrough"
        elif self.iface == 'virtio':
            try:
                self.cache = self.conf['custom']['viodiskcache']
            except KeyError:
                pass  # Ignore if custom disk cache is missing

    def _makeName(self):
        devname = {'ide': 'hd', 'scsi': 'sd', 'virtio': 'vd', 'fdc': 'fd'}
        devindex = ''

        i = int(self.index)
        while i > 0:
            devindex = chr(ord('a') + (i % 26)) + devindex
            i /= 26

        return devname.get(self.iface, 'hd') + (devindex or 'a')

    def _checkIoTuneCategories(self):
        categories = ("bytes", "iops")
        ioTuneParamsInfo = self.specParams['ioTune']
        for category in categories:
            if ioTuneParamsInfo.get('total_' + category + '_sec', 0) and \
                    (ioTuneParamsInfo.get('read_' + category + '_sec', 0) or
                     ioTuneParamsInfo.get('write_' + category + '_sec', 0)):
                raise ValueError('A non-zero total value and non-zero'
                                 ' read/write value for %s_sec can not be'
                                 ' set at the same time' % category)

    def _validateIoTuneParams(self):
        ioTuneParams = ('total_bytes_sec', 'read_bytes_sec',
                        'write_bytes_sec', 'total_iops_sec',
                        'write_iops_sec', 'read_iops_sec')
        for key, value in self.specParams['ioTune'].iteritems():
            try:
                if key in ioTuneParams:
                    self.specParams['ioTune'][key] = int(value)
                    if self.specParams['ioTune'][key] >= 0:
                        continue
                else:
                    raise Exception('parameter %s name is invalid' % key)
            except ValueError as e:
                e.args = ('an integer is required for ioTune'
                          ' parameter %s' % key,) + e.args[1:]
                raise
            else:
                raise ValueError('parameter %s value should be'
                                 ' equal or greater than zero' % key)

        self._checkIoTuneCategories()

    def getLeasesXML(self):
        """
        Create domxml for the drive lease.

        <lease>
            <key>volumeID</key>
            <lockspace>domainID</lockspace>
            <target offset="0" path="/path/to/lease"/>
        </lease>
        """
        if not self.hasVolumeLeases:
            return  # empty items generator

        # NOTE: at the moment we are generating the lease only for the leaf,
        # when libvirt will support shared leases this will loop over all the
        # volumes
        for volInfo in self.volumeChain[-1:]:
            lease = XMLElement('lease')
            lease.appendChildWithArgs('key', text=volInfo['volumeID'])
            lease.appendChildWithArgs('lockspace',
                                      text=volInfo['domainID'])
            lease.appendChildWithArgs('target', path=volInfo['leasePath'],
                                      offset=str(volInfo['leaseOffset']))
            yield lease

    def getXML(self):
        """
        Create domxml for disk/cdrom/floppy.

        <disk type='file' device='disk' snapshot='no'>
          <driver name='qemu' type='qcow2' cache='none'/>
          <source file='/path/to/image'/>
          <target dev='hda' bus='ide'/>
          <serial>54-a672-23e5b495a9ea</serial>
        </disk>
        """
        self.device = getattr(self, 'device', 'disk')

        source = XMLElement('source')
        if self.blockDev:
            deviceType = 'block'
            source.setAttrs(dev=self.path)
        elif self.networkDev:
            deviceType = 'network'
            source.setAttrs(protocol=self.volumeInfo['protocol'],
                            name=self.volumeInfo['path'])
            hostAttrs = {'name': self.volumeInfo['volfileServer'],
                         'port': self.volumeInfo['volPort'],
                         'transport': self.volumeInfo['volTransport']}
            source.appendChildWithArgs('host', **hostAttrs)
        else:
            deviceType = 'file'
            sourceAttrs = {'file': self.path}
            if self.device == 'cdrom' or self.device == 'floppy':
                sourceAttrs['startupPolicy'] = 'optional'
            source.setAttrs(**sourceAttrs)
        diskelem = self.createXmlElem('disk', deviceType,
                                      ['device', 'address', 'sgio'])
        diskelem.setAttrs(snapshot='no')
        diskelem.appendChild(source)

        targetAttrs = {'dev': self.name}
        if self.iface:
            targetAttrs['bus'] = self.iface
        diskelem.appendChildWithArgs('target', **targetAttrs)

        if self.extSharedState == DRIVE_SHARED_TYPE.SHARED:
            diskelem.appendChildWithArgs('shareable')
        if hasattr(self, 'readonly') and utils.tobool(self.readonly):
            diskelem.appendChildWithArgs('readonly')
        if hasattr(self, 'serial'):
            diskelem.appendChildWithArgs('serial', text=self.serial)
        if hasattr(self, 'bootOrder'):
            diskelem.appendChildWithArgs('boot', order=self.bootOrder)

        if self.device != 'lun' and hasattr(self, 'sgio'):
            raise ValueError("sgio attribute can be set only for LUN devices")

        if self.device == 'lun' and self.format == 'cow':
            raise ValueError("cow format is not supported for LUN devices")

        if self.device == 'disk' or self.device == 'lun':
            driverAttrs = {'name': 'qemu'}
            if self.blockDev:
                driverAttrs['io'] = 'native'
            else:
                driverAttrs['io'] = 'threads'
            if self.format == 'cow':
                driverAttrs['type'] = 'qcow2'
            elif self.format:
                driverAttrs['type'] = 'raw'

            driverAttrs['cache'] = self.cache

            if (self.propagateErrors == 'on' or
                    utils.tobool(self.propagateErrors)):
                driverAttrs['error_policy'] = 'enospace'
            else:
                driverAttrs['error_policy'] = 'stop'
            diskelem.appendChildWithArgs('driver', **driverAttrs)
        elif self.device == 'floppy':
            if (self.path and
                not utils.getUserPermissions(constants.QEMU_PROCESS_USER,
                                             self.path)['write']):
                diskelem.appendChildWithArgs('readonly')

        if hasattr(self, 'specParams') and 'ioTune' in self.specParams:
            self._validateIoTuneParams()
            iotune = XMLElement('iotune')
            for key, value in self.specParams['ioTune'].iteritems():
                iotune.appendChildWithArgs(key, text=str(value))
            diskelem.appendChild(iotune)

        return diskelem


class BalloonDevice(VmDevice):

    def getXML(self):
        """
        Create domxml for a memory balloon device.

        <memballoon model='virtio'>
          <address type='pci' domain='0x0000' bus='0x00' slot='0x04'
           function='0x0'/>
        </memballoon>
        """
        m = self.createXmlElem(self.device, None, ['address'])
        m.setAttrs(model=self.specParams['model'])
        return m


class WatchdogDevice(VmDevice):
    def __init__(self, *args, **kwargs):
        super(WatchdogDevice, self).__init__(*args, **kwargs)

        if not hasattr(self, 'specParams'):
            self.specParams = {}

    def getXML(self):
        """
        Create domxml for a watchdog device.

        <watchdog model='i6300esb' action='reset'>
          <address type='pci' domain='0x0000' bus='0x00' slot='0x05'
           function='0x0'/>
        </watchdog>
        """
        m = self.createXmlElem(self.type, None, ['address'])
        m.setAttrs(model=self.specParams.get('model', 'i6300esb'),
                   action=self.specParams.get('action', 'none'))
        return m


class SmartCardDevice(VmDevice):
    def getXML(self):
        """
        Add smartcard section to domain xml

        <smartcard mode='passthrough' type='spicevmc'>
          <address ... />
        </smartcard>
        """
        card = self.createXmlElem(self.device, None, ['address'])
        sourceAttrs = {'mode': self.specParams['mode']}
        if sourceAttrs['mode'] != 'host':
            sourceAttrs['type'] = self.specParams['type']
        card.setAttrs(**sourceAttrs)
        return card


class RedirDevice(VmDevice):
    def getXML(self):
        """
        Create domxml for a redir device.

        <redirdev bus='usb' type='spicevmc'>
          <address type='usb' bus='0' port='1'/>
        </redirdev>
        """
        return self.createXmlElem('redirdev', self.device, ['bus', 'address'])


class RngDevice(VmDevice):
    def getXML(self):
        """
        <rng model='virtio'>
            <rate period="2000" bytes="1234"/>
            <backend model='random'>/dev/random</backend>
        </rng>
        """
        rng = self.createXmlElem('rng', None, ['model'])

        # <rate... /> element
        if 'bytes' in self.specParams:
            rateAttrs = {'bytes': self.specParams['bytes']}
            if 'period' in self.specParams:
                rateAttrs['period'] = self.specParams['period']

            rng.appendChildWithArgs('rate', None, **rateAttrs)

        # <backend... /> element
        rng.appendChildWithArgs('backend',
                                caps.RNG_SOURCES[self.specParams['source']],
                                model='random')

        return rng


class ConsoleDevice(VmDevice):
    def getXML(self):
        """
        Create domxml for a console device.

        <console type='pty'>
          <target type='virtio' port='0'/>
        </console>
        """
        m = self.createXmlElem('console', 'pty')
        m.appendChildWithArgs('target', type='virtio', port='0')
        return m


class MigrationError(Exception):
    pass


class StorageUnavailableError(Exception):
    pass


class Vm(object):
    """
    Used for abstracting communication between various parts of the
    system and Qemu.

    Runs Qemu in a subprocess and communicates with it, and monitors
    its behaviour.
    """
    log = logging.getLogger("vm.Vm")
    # limit threads number until the libvirt lock will be fixed
    _ongoingCreations = threading.BoundedSemaphore(4)
    DeviceMapping = ((DISK_DEVICES, Drive),
                     (NIC_DEVICES, NetworkInterfaceDevice),
                     (SOUND_DEVICES, SoundDevice),
                     (VIDEO_DEVICES, VideoDevice),
                     (CONTROLLER_DEVICES, ControllerDevice),
                     (GENERAL_DEVICES, GeneralDevice),
                     (BALLOON_DEVICES, BalloonDevice),
                     (WATCHDOG_DEVICES, WatchdogDevice),
                     (CONSOLE_DEVICES, ConsoleDevice),
                     (REDIR_DEVICES, RedirDevice),
                     (RNG_DEVICES, RngDevice),
                     (SMARTCARD_DEVICES, SmartCardDevice))

    def _makeDeviceDict(self):
        return dict((dev, []) for dev, _ in self.DeviceMapping)

    def _makeChannelPath(self, deviceName):
        return constants.P_LIBVIRT_VMCHANNELS + self.id + '.' + deviceName

    def _getDefaultDiskInterface(self):
        DEFAULT_DISK_INTERFACES = {caps.Architecture.X86_64: 'ide',
                                   caps.Architecture.PPC64: 'scsi'}
        return DEFAULT_DISK_INTERFACES[self.arch]

    def __init__(self, cif, params):
        """
        Initialize a new VM instance.

        :param cif: The client interface that creates this VM.
        :type cif: :class:`clientIF.clientIF`
        :param params: The VM parameters.
        :type params: dict
        """
        self._dom = None
        self.conf = {'pid': '0'}
        self.conf.update(params)
        self.cif = cif
        self.log = SimpleLogAdapter(self.log, {"vmId": self.conf['vmId']})
        self.destroyed = False
        self._recoveryFile = constants.P_VDSM_RUN + \
            str(self.conf['vmId']) + '.recovery'
        self.user_destroy = False
        self._monitorResponse = 0
        self.conf['clientIp'] = ''
        self.memCommitted = 0
        self._confLock = threading.Lock()
        self._creationThread = threading.Thread(target=self._startUnderlyingVm)
        if 'migrationDest' in self.conf:
            self._lastStatus = 'Migration Destination'
        elif 'restoreState' in self.conf:
            self._lastStatus = 'Restoring state'
        else:
            self._lastStatus = 'WaitForLaunch'
        self._migrationSourceThread = MigrationSourceThread(self)
        self._kvmEnable = self.conf.get('kvmEnable', 'true')
        self._guestSocketFile = constants.P_VDSM_RUN + self.conf['vmId'] + \
            '.guest.socket'
        self._incomingMigrationFinished = threading.Event()
        self.id = self.conf['vmId']
        self._volPrepareLock = threading.Lock()
        self._initTimePauseCode = None
        self._initTimeRTC = long(self.conf.get('timeOffset', 0))
        self.guestAgent = None
        self._guestEvent = 'Powering up'
        self._guestEventTime = 0
        self._vmStats = None
        self._guestCpuRunning = False
        self._guestCpuLock = threading.Lock()
        self._startTime = time.time() - \
            float(self.conf.pop('elapsedTimeOffset', 0))

        self._usedIndices = {}  # {'ide': [], 'virtio' = []}
        self.stopDisksStatsCollection()
        self._vmCreationEvent = threading.Event()
        self._pathsPreparedEvent = threading.Event()
        self._devices = self._makeDeviceDict()

        self._connection = libvirtconnection.get(cif)
        if 'vmName' not in self.conf:
            self.conf['vmName'] = 'n%s' % self.id
        self._guestSocketFile = self._makeChannelPath(_VMCHANNEL_DEVICE_NAME)
        self._qemuguestSocketFile = self._makeChannelPath(_QEMU_GA_DEVICE_NAME)
        self._lastXMLDesc = '<domain><uuid>%s</uuid></domain>' % self.id
        self._devXmlHash = '0'
        self._released = False
        self._releaseLock = threading.Lock()
        self.saveState()
        self._watchdogEvent = {}
        self.sdIds = []
        self.arch = caps.getTargetArch()

        if (self.arch not in ['ppc64', 'x86_64']):
            raise RuntimeError('Unsupported architecture: %s' % self.arch)

    def _get_lastStatus(self):
        PAUSED_STATES = ('Powering down', 'RebootInProgress', 'Up')
        if not self._guestCpuRunning and self._lastStatus in PAUSED_STATES:
            return 'Paused'
        return self._lastStatus

    def _set_lastStatus(self, value):
        if self._lastStatus == 'Down':
            self.log.warning('trying to set state to %s when already Down',
                             value)
            if value == 'Down':
                raise DoubleDownError
            else:
                return
        if value not in VALID_STATES:
            self.log.error('setting state to %s', value)
        if self._lastStatus != value:
            self.saveState()
            self._lastStatus = value

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
            res = self.cif.irs.getVolumeSize(drv['domainID'], drv['poolID'],
                                             drv['imageID'], drv['volumeID'])
            if res['status']['code'] != 0:
                raise StorageUnavailableError("Failed to get size for"
                                              " volume %s",
                                              drv['volumeID'])

            # if a key is missing here, is hsm bug and we cannot handle it.
            drv['truesize'] = res['truesize']
            drv['apparentsize'] = res['apparentsize']
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
                legacies.append({'type': DISK_DEVICES, 'device': 'disk',
                                 'path': path, 'iface': 'ide', 'index': index,
                                 'truesize': 0})
        return legacies

    def __removableDrives(self):
        removables = [{
            'type': DISK_DEVICES,
            'device': 'cdrom',
            'iface': self._getDefaultDiskInterface(),
            'path': self.conf.get('cdrom', ''),
            'index': 2,
            'truesize': 0}]
        floppyPath = self.conf.get('floppy')
        if floppyPath:
            removables.append({
                'type': DISK_DEVICES,
                'device': 'floppy',
                'path': floppyPath,
                'iface': 'fdc',
                'index': 0,
                'truesize': 0})
        return removables

    def buildConfDevices(self):
        """
        Return the "devices" section of this Vm's conf.
        If missing, create it according to old API.
        """
        devices = self._makeDeviceDict()

        # For BC we need to save previous behaviour for old type parameters.
        # The new/old type parameter will be distinguished
        # by existence/absence of the 'devices' key
        if self.conf.get('devices') is None:
            devices[DISK_DEVICES] = self.getConfDrives()
            devices[NIC_DEVICES] = self.getConfNetworkInterfaces()
            devices[SOUND_DEVICES] = self.getConfSound()
            devices[VIDEO_DEVICES] = self.getConfVideo()
            devices[CONTROLLER_DEVICES] = self.getConfController()
        else:
            for dev in self.conf.get('devices'):
                try:
                    devices[dev['type']].append(dev)
                except KeyError:
                    self.log.warn("Unknown type found, device: '%s' "
                                  "found", dev)
                    devices[GENERAL_DEVICES].append(dev)

        self._checkDeviceLimits(devices)

        # Normalize vdsm images
        for drv in devices[DISK_DEVICES]:
            if isVdsmImage(drv):
                try:
                    self._normalizeVdsmImg(drv)
                except StorageUnavailableError:
                    # storage unavailable is not fatal on recovery;
                    # the storage subsystem monitors the devices
                    # and will notify when they come up later.
                    if 'recover' not in self.conf:
                        raise

        self.normalizeDrivesIndices(devices[DISK_DEVICES])

        # Preserve old behavior. Since libvirt add a memory balloon device
        # to all guests, we need to specifically request not to add it.
        self._normalizeBalloonDevice(devices[BALLOON_DEVICES])

        return devices

    def _normalizeBalloonDevice(self, balloonDevices):
        EMPTY_BALLOON = {'type': BALLOON_DEVICES,
                         'device': 'memballoon',
                         'specParams': {
                             'model': 'none'}}

        # Avoid overriding the saved balloon target value on recovery.
        if 'recover' not in self.conf:
            for dev in balloonDevices:
                dev['target'] = int(self.conf.get('memSize')) * 1024

        if not balloonDevices:
            balloonDevices.append(EMPTY_BALLOON)

    def _checkDeviceLimits(self, devices):
        # libvirt only support one watchdog and one console device
        for device in (WATCHDOG_DEVICES, CONSOLE_DEVICES):
            if len(devices[device]) > 1:
                raise ValueError("only a single %s device is "
                                 "supported" % device)

    def getConfController(self):
        """
        Normalize controller device.
        """
        controllers = []
        # For now we create by default only 'virtio-serial' controller
        controllers.append({'type': CONTROLLER_DEVICES,
                            'device': 'virtio-serial'})
        return controllers

    def getConfVideo(self):
        """
        Normalize video device provided by conf.
        """

        DEFAULT_VIDEOS = {caps.Architecture.X86_64: 'cirrus',
                          caps.Architecture.PPC64: 'vga'}

        vcards = []
        if self.conf.get('display') == 'vnc':
            devType = DEFAULT_VIDEOS[self.arch]
        elif self.conf.get('display') == 'qxl':
            devType = 'qxl'

        monitors = int(self.conf.get('spiceMonitors', '1'))
        vram = '65536' if (monitors <= 2) else '32768'
        for idx in range(monitors):
            vcards.append({'type': VIDEO_DEVICES, 'specParams': {'vram': vram},
                           'device': devType})

        return vcards

    def getConfSound(self):
        """
        Normalize sound device provided by conf.
        """
        scards = []
        if self.conf.get('soundDevice'):
            scards.append({'type': SOUND_DEVICES,
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
            nics.append({'type': NIC_DEVICES, 'macAddr': mac,
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
            drv['type'] = DISK_DEVICES
            drv['format'] = drv.get('format') or 'raw'
            drv['propagateErrors'] = drv.get('propagateErrors') or 'off'
            drv['readonly'] = False
            drv['shared'] = False
            # FIXME: For BC we have now two identical keys: iface = if
            # Till the day that conf will not returned as a status anymore.
            drv['iface'] = drv.get('iface') or \
                drv.get('if', self._getDefaultDiskInterface())

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
            self._ongoingCreations.acquire()
            self.log.debug("_ongoingCreations acquired")
            self._vmCreationEvent.set()
            try:
                self._run()
                if self.lastStatus != 'Down' and 'recover' not in self.conf \
                        and not self.cif.mom:
                    # If MOM is available, we needn't tell it to adjust KSM
                    # behaviors on VM start/destroy, because the tuning can be
                    # done automatically according to its statistical data.
                    self.cif.ksmMonitor.adjust()
            except Exception:
                if 'recover' not in self.conf:
                    raise
                else:
                    self.log.info("Skipping errors on recovery", exc_info=True)
            finally:
                self._ongoingCreations.release()
                self.log.debug("_ongoingCreations released")

            if ('migrationDest' in self.conf or 'restoreState' in self.conf) \
                    and self.lastStatus != 'Down':
                self._waitForIncomingMigrationFinish()

            self.lastStatus = 'Up'
            if self._initTimePauseCode:
                self.conf['pauseCode'] = self._initTimePauseCode
                if self._initTimePauseCode == 'ENOSPC':
                    self.cont()
            else:
                try:
                    with self._confLock:
                        del self.conf['pauseCode']
                except KeyError:
                    pass

            if 'recover' in self.conf:
                with self._confLock:
                    del self.conf['recover']
            self.saveState()
        except Exception as e:
            if 'recover' in self.conf:
                self.log.info("Skipping errors on recovery", exc_info=True)
            else:
                self.log.error("The vm start process failed", exc_info=True)
                self.setDownStatus(ERROR, str(e))

    def _incomingMigrationPending(self):
        return 'migrationDest' in self.conf or 'restoreState' in self.conf

    def stopDisksStatsCollection(self):
        self._volumesPrepared = False

    def startDisksStatsCollection(self):
        self._volumesPrepared = True

    def isDisksStatsCollectionEnabled(self):
        return self._volumesPrepared

    def preparePaths(self, drives):
        domains = []
        for drive in drives:
            with self._volPrepareLock:
                if self.destroyed:
                    # A destroy request has been issued, exit early
                    break
                drive['path'] = self.cif.prepareVolumePath(drive, self.id)
            if drive['device'] == 'disk' and isVdsmImage(drive):
                domains.append(drive['domainID'])
        else:
            self.sdIds.extend(domains)
            # Now we got all the resources we needed
            self.startDisksStatsCollection()

    def _prepareTransientDisks(self, drives):
        for drive in drives:
            self._createTransientDisk(drive)

    def _onQemuDeath(self):
        self.log.info('underlying process disconnected')
        # Try release VM resources first, if failed stuck in 'Powering Down'
        # state
        response = self.releaseVm()
        if not response['status']['code']:
            if self.destroyed:
                self.setDownStatus(NORMAL, 'Admin shut down')
            elif self.user_destroy:
                self.setDownStatus(NORMAL, 'User shut down')
            else:
                self.setDownStatus(ERROR, "Lost connection with qemu process")

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
        self._saveStateInternal()
        try:
            self._getUnderlyingVmInfo()
        except Exception:
            # we do not care if _dom suddenly died now
            pass

    def _saveStateInternal(self):
        if self.destroyed:
            return
        with self._confLock:
            toSave = deepcopy(self.status())
        toSave['startTime'] = self._startTime
        if self.lastStatus != 'Down' and self._vmStats and self.guestAgent:
            toSave['username'] = self.guestAgent.guestInfo['username']
            toSave['guestIPs'] = self.guestAgent.guestInfo['guestIPs']
            toSave['guestFQDN'] = self.guestAgent.guestInfo['guestFQDN']
        else:
            toSave['username'] = ""
            toSave['guestIPs'] = ""
            toSave['guestFQDN'] = ""
        if 'sysprepInf' in toSave:
            del toSave['sysprepInf']
            if 'floppy' in toSave:
                del toSave['floppy']
        for drive in toSave.get('drives', []):
            for d in self._devices[DISK_DEVICES]:
                if isVdsmImage(d) and drive.get('volumeID') == d.volumeID:
                    drive['truesize'] = str(d.truesize)
                    drive['apparentsize'] = str(d.apparentsize)

        with tempfile.NamedTemporaryFile(dir=constants.P_VDSM_RUN,
                                         delete=False) as f:
            pickle.dump(toSave, f)

        os.rename(f.name, self._recoveryFile)

    def onReboot(self):
        try:
            self.log.debug('reboot event')
            self._startTime = time.time()
            self._guestEventTime = self._startTime
            self._guestEvent = 'RebootInProgress'
            self.saveState()
            self.guestAgent.onReboot()
            if self.conf.get('volatileFloppy'):
                self._ejectFloppy()
                self.log.debug('ejected volatileFloppy')
        except Exception:
            self.log.error("Reboot event failed", exc_info=True)

    def onShutdown(self):
        self.log.debug('onShutdown() event')
        self.user_destroy = True

    def onConnect(self, clientIp=''):
        if clientIp:
            self.conf['clientIp'] = clientIp

    def _timedDesktopLock(self):
        if not self.conf.get('clientIp', ''):
            self.guestAgent.desktopLock()

    def onDisconnect(self, detail=None):
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

    def _rtcUpdate(self, timeOffset):
        newTimeOffset = str(self._initTimeRTC + long(timeOffset))
        self.log.debug('new rtc offset %s', newTimeOffset)
        with self._confLock:
            self.conf['timeOffset'] = newTimeOffset

    def extendDrivesIfNeeded(self):
        extend = []

        for drive in self._devices[DISK_DEVICES]:
            if not drive.blockDev or drive.format != 'cow':
                continue

            capacity, alloc, physical = self._dom.blockInfo(drive.path, 0)

            # Since the check based on nextPhysSize is extremly risky (it
            # may result in the VM being paused) we can't use the regular
            # getNextVolumeSize call as it relies on a cached value of the
            # drive apparentsize.
            nextPhysSize = physical + drive.VOLWM_CHUNK_MB * constants.MEGAB

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
                self.log.error(
                    "Improbable extension request for volume %s on domain "
                    "%s, pausing the VM to avoid corruptions (capacity: %s, "
                    "allocated: %s, physical: %s, next physical size: %s)",
                    drive.volumeID, drive.domainID, capacity, alloc,
                    physical, nextPhysSize)
                self.pause(pauseCode='EOTHER')
                return False

            if physical - alloc < drive.watermarkLimit:
                extend.append((drive, capacity, alloc, physical))

        for drive, capacity, alloc, physical in extend:
            self.log.info(
                "Requesting extension for volume %s on domain %s (apparent: "
                "%s, capacity: %s, allocated: %s, physical: %s)",
                drive.volumeID, drive.domainID, drive.apparentsize, capacity,
                alloc, physical)
            self.extendDriveVolume(drive)

        return len(extend) > 0

    def extendDriveVolume(self, vmDrive):
        if not vmDrive.blockDev:
            return

        newSize = vmDrive.getNextVolumeSize()  # newSize is in megabytes

        if getattr(vmDrive, 'diskReplicate', None):
            volInfo = {'poolID': vmDrive.diskReplicate['poolID'],
                       'domainID': vmDrive.diskReplicate['domainID'],
                       'imageID': vmDrive.diskReplicate['imageID'],
                       'volumeID': vmDrive.diskReplicate['volumeID'],
                       'name': vmDrive.name, 'newSize': newSize}
            self.log.debug("Requesting an extension for the volume "
                           "replication: %s", volInfo)
            self.cif.irs.sendExtendMsg(vmDrive.poolID, volInfo,
                                       newSize * constants.MEGAB,
                                       self.__afterReplicaExtension)
        else:
            self.__extendDriveVolume(vmDrive, newSize)

    def __refreshDriveVolume(self, volInfo):
        self.cif.irs.refreshVolume(volInfo['domainID'], volInfo['poolID'],
                                   volInfo['imageID'], volInfo['volumeID'])

    def __verifyVolumeExtension(self, volInfo):
        self.log.debug("Refreshing drive volume for %s (domainID: %s, "
                       "volumeID: %s)", volInfo['name'], volInfo['domainID'],
                       volInfo['volumeID'])

        self.__refreshDriveVolume(volInfo)
        volSizeRes = self.cif.irs.getVolumeSize(volInfo['domainID'],
                                                volInfo['poolID'],
                                                volInfo['imageID'],
                                                volInfo['volumeID'])

        if volSizeRes['status']['code']:
            raise RuntimeError(
                "Cannot get the volume size for %s "
                "(domainID: %s, volumeID: %s)" % (volInfo['name'],
                                                  volInfo['domainID'],
                                                  volInfo['volumeID']))

        apparentSize = int(volSizeRes['apparentsize'])
        trueSize = int(volSizeRes['truesize'])

        self.log.debug("Verifying extension for volume %s, requested size %s, "
                       "current size %s", volInfo['volumeID'],
                       volInfo['newSize'] * constants.MEGAB, apparentSize)

        if apparentSize < volInfo['newSize'] * constants.MEGAB:  # in bytes
            raise RuntimeError(
                "Volume extension failed for %s (domainID: %s, volumeID: %s)" %
                (volInfo['name'], volInfo['domainID'], volInfo['volumeID']))

        return apparentSize, trueSize

    def __afterReplicaExtension(self, volInfo):
        self.__verifyVolumeExtension(volInfo)
        vmDrive = self._findDriveByName(volInfo['name'])
        self.log.debug("Requesting extension for the original drive: %s "
                       "(domainID: %s, volumeID: %s)",
                       vmDrive.name, vmDrive.domainID, vmDrive.volumeID)
        self.__extendDriveVolume(vmDrive, volInfo['newSize'])

    def __extendDriveVolume(self, vmDrive, newSize):
        volInfo = {'poolID': vmDrive.poolID, 'domainID': vmDrive.domainID,
                   'imageID': vmDrive.imageID, 'volumeID': vmDrive.volumeID,
                   'name': vmDrive.name, 'newSize': newSize}
        self.log.debug("Requesting an extension for the volume: %s", volInfo)
        self.cif.irs.sendExtendMsg(
            vmDrive.poolID,
            volInfo,
            newSize * constants.MEGAB,
            self.__afterVolumeExtension)

    def __afterVolumeExtension(self, volInfo):
        # Either the extension succeeded and we're setting the new apparentSize
        # and trueSize, or it fails and we raise an exception.
        # TODO: Report failure to the engine.
        apparentSize, trueSize = self.__verifyVolumeExtension(volInfo)
        vmDrive = self._findDriveByName(volInfo['name'])
        vmDrive.apparentsize, vmDrive.truesize = apparentSize, trueSize
        try:
            self.cont()
        except libvirt.libvirtError:
            self.log.debug("VM %s can't be resumed", self.id, exc_info=True)
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

    def cont(self, afterState='Up', guestCpuLocked=False):
        if not guestCpuLocked:
            self._acquireCpuLockWithTimeout()
        try:
            if self.lastStatus in ('Migration Source', 'Saving State', 'Down'):
                self.log.error('cannot cont while %s', self.lastStatus)
                return errCode['unexpected']
            self._underlyingCont()
            if hasattr(self, 'updateGuestCpuRunning'):
                self.updateGuestCpuRunning()
            self._lastStatus = afterState
            try:
                with self._confLock:
                    del self.conf['pauseCode']
            except KeyError:
                pass
            return {'status': doneCode, 'output': ['']}
        finally:
            if not guestCpuLocked:
                self._guestCpuLock.release()

    def pause(self, afterState='Paused', guestCpuLocked=False,
              pauseCode='NOERR'):
        if not guestCpuLocked:
            self._acquireCpuLockWithTimeout()
        try:
            with self._confLock:
                self.conf['pauseCode'] = pauseCode
            self._underlyingPause()
            if hasattr(self, 'updateGuestCpuRunning'):
                self.updateGuestCpuRunning()
            self._lastStatus = afterState
            return {'status': doneCode, 'output': ['']}
        finally:
            if not guestCpuLocked:
                self._guestCpuLock.release()

    def shutdown(self, timeout, message, reboot):
        try:
            now = time.time()
            if self.lastStatus == 'Down':
                return errCode['noVM']
            if self.guestAgent and self.guestAgent.isResponsive():
                self._guestEventTime = now
                if reboot:
                    self._guestEvent = 'RebootInProgress'
                    self.log.debug('guestAgent reboot called')
                else:
                    self._guestEvent = 'Powering down'
                    self.log.debug('guestAgent shutdown called')
                    agent_timeout = (int(timeout) +
                                     config.getint('vars',
                                                   'sys_shutdown_timeout'))
                    timer = threading.Timer(agent_timeout, self._timedShutdown)
                    timer.start()
                self.guestAgent.desktopShutdown(timeout, message, reboot)
            elif utils.tobool(self.conf.get('acpiEnable', 'true')) and \
                    not reboot:
                self._guestEventTime = now
                self._guestEvent = 'Powering down'
                self._acpiShutdown()
            # No tools, no ACPI
            else:
                return {
                    'status': {
                        'code': errCode['exist']['status']['code'],
                        'message': 'VM without ACPI or active SolidICE tools. '
                                   'Try Forced Shutdown.'}}
        except Exception:
            self.log.error("Shutdown failed", exc_info=True)
            return {'status': {'code': errCode['exist']['status']['code'],
                    'message': 'Failed to shutdown VM. Try Forced Shutdown.'}}
        message = 'Machine rebooting' if reboot else 'Machine shut down'
        return {'status': {'code': doneCode['code'], 'message': message}}

    def _timedShutdown(self):
        self.log.debug('_timedShutdown Called')
        try:
            if self.lastStatus == 'Down':
                return
            if not utils.tobool(self.conf.get('acpiEnable', 'true')):
                self.destroy()
            else:
                self._acpiShutdown()
        except Exception:
            self.log.error("_timedShutdown failed", exc_info=True)

    def _cleanupDrives(self, *drives):
        """
        Clean up drives related stuff. Sample usage:

        self._cleanupDrives()
        self._cleanupDrives(drive)
        self._cleanupDrives(drive1, drive2, drive3)
        self._cleanupDrives(*drives_list)
        """
        drives = drives or self._devices[DISK_DEVICES]
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
                    self.log.error("Drive teardown failure for %s",
                                   drive, exc_info=True)

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

        self._guestSockCleanup(self._guestSocketFile)

    def setDownStatus(self, code, reason):
        try:
            self.lastStatus = 'Down'
            with self._confLock:
                self.conf['exitCode'] = code
                if 'restoreState' in self.conf:
                    self.conf['exitMessage'] = (
                        "Wake up from hibernation failed")
                else:
                    self.conf['exitMessage'] = reason
            self.log.debug("Changed state to Down: " + reason)
        except DoubleDownError:
            pass
        try:
            self.guestAgent.stop()
        except Exception:
            pass
        try:
            self._vmStats.stop()
        except Exception:
            pass
        self.saveState()

    def status(self):
        # used by API.Global.getVMList
        self.conf['status'] = self.lastStatus
        return self.conf

    def getStats(self):
        stats = self._getStatsInternal()
        stats['hash'] = self._devXmlHash
        if self._watchdogEvent:
            stats["watchdogEvent"] = self._watchdogEvent
        return stats

    def _getStatsInternal(self):
        # used by API.Vm.getStats

        def _getGuestStatus():
            GUEST_WAIT_TIMEOUT = 60
            now = time.time()
            if now - self._guestEventTime < 5 * GUEST_WAIT_TIMEOUT and \
                    self._guestEvent == 'Powering down':
                return self._guestEvent
            if self.guestAgent and self.guestAgent.isResponsive() and \
                    self.guestAgent.getStatus():
                return self.guestAgent.getStatus()
            if now - self._guestEventTime < GUEST_WAIT_TIMEOUT:
                return self._guestEvent
            return 'Up'

        if self.lastStatus == 'Down':
            stats = {}
            stats['exitCode'] = self.conf['exitCode']
            stats['status'] = self.lastStatus
            stats['exitMessage'] = self.conf['exitMessage']
            if 'timeOffset' in self.conf:
                stats['timeOffset'] = self.conf['timeOffset']
            return stats

        stats = {
            'displayPort': self.conf['displayPort'],
            'displaySecurePort': self.conf['displaySecurePort'],
            'displayType': self.conf['display'],
            'displayIp': self.conf['displayIp'],
            'pid': self.conf['pid'],
            'vmType': self.conf['vmType'],
            'kvmEnable': self._kvmEnable,
            'network': {}, 'disks': {},
            'monitorResponse': str(self._monitorResponse),
            'elapsedTime': str(int(time.time() - self._startTime)), }
        if 'cdrom' in self.conf:
            stats['cdrom'] = self.conf['cdrom']
        if 'boot' in self.conf:
            stats['boot'] = self.conf['boot']

        decStats = {}
        try:
            if self._vmStats:
                decStats = self._vmStats.get()
                if (not self.isMigrating()
                    and decStats['statsAge'] >
                        config.getint('vars', 'vm_command_timeout')):
                    stats['monitorResponse'] = '-1'
        except Exception:
            self.log.error("Error fetching vm stats", exc_info=True)
        for var in decStats:
            if type(decStats[var]) is not dict:
                stats[var] = utils.convertToStr(decStats[var])
            elif var == 'network':
                stats['network'] = decStats[var]
            else:
                try:
                    stats['disks'][var] = {}
                    for value in decStats[var]:
                        stats['disks'][var][value] = \
                            utils.convertToStr(decStats[var][value])
                except Exception:
                    self.log.error("Error setting vm disk stats",
                                   exc_info=True)

        statuses = ('Saving State', 'Restoring state', 'Migration Source',
                    'Migration Destination', 'Paused')
        if self.lastStatus in statuses:
            stats['status'] = self.lastStatus
        elif self.isMigrating():
            if self._migrationSourceThread._mode == 'file':
                stats['status'] = 'Saving State'
            else:
                stats['status'] = 'Migration Source'
        elif self.lastStatus == 'Up':
            stats['status'] = _getGuestStatus()
        else:
            stats['status'] = self.lastStatus
        stats['acpiEnable'] = self.conf.get('acpiEnable', 'true')
        stats['timeOffset'] = self.conf.get('timeOffset', '0')
        stats['clientIp'] = self.conf.get('clientIp', '')
        if 'pauseCode' in self.conf:
            stats['pauseCode'] = self.conf['pauseCode']
        try:
            stats.update(self.guestAgent.getGuestInfo())
        except Exception:
            return stats
        memUsage = 0
        realMemUsage = int(stats['memUsage'])
        if realMemUsage != 0:
            memUsage = (100 - float(realMemUsage) /
                        int(self.conf['memSize']) * 100)
        stats['memUsage'] = utils.convertToStr(int(memUsage))
        stats['balloonInfo'] = self._getBalloonInfo()
        return stats

    def isMigrating(self):
        return self._migrationSourceThread.isAlive()

    def hasTransientDisks(self):
        for drive in self._devices[DISK_DEVICES]:
            if drive.transientDisk:
                return True
        return False

    def migrate(self, params):
        self._acquireCpuLockWithTimeout()
        try:
            if self.isMigrating():
                self.log.warning('vm already migrating')
                return errCode['exist']
            if self.hasTransientDisks():
                return errCode['transientErr']
            # while we were blocking, another migrationSourceThread could have
            # taken self Down
            if self._lastStatus == 'Down':
                return errCode['noVM']
            self._migrationSourceThread = MigrationSourceThread(self, **params)
            self._migrationSourceThread.start()
            self._migrationSourceThread.getStat()
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
                return errCode['migCancelErr']
            raise
        except AttributeError:
            if self._dom is None:
                return errCode['migCancelErr']
            raise
        finally:
            self._guestCpuLock.release()

    def _customDevices(self):
        """
            Get all devices that have custom properties
        """

        for devType in self._devices:
            for dev in self._devices[devType]:
                if getattr(dev, 'custom', {}):
                    yield dev

    def _appendDevices(self, domxml):
        """
        Create all devices and run before_device_create hook script for devices
        with custom properties

        The resulting device xml is cached in dev._deviceXML.
        """

        for devType in self._devices:
            for dev in self._devices[devType]:
                deviceXML = dev.getXML().toxml(encoding='utf-8')

                if getattr(dev, "custom", {}):
                    deviceXML = hooks.before_device_create(
                        deviceXML, self.conf, dev.custom)

                dev._deviceXML = deviceXML
                domxml._devices.appendChild(
                    xml.dom.minidom.parseString(deviceXML).firstChild)

    def _buildCmdLine(self):
        domxml = _DomXML(self.conf, self.log, self.arch)
        domxml.appendOs()

        if self.arch == caps.Architecture.X86_64:
            osd = caps.osversion()

            osVersion = osd.get('version', '') + '-' + osd.get('release', '')

            domxml.appendSysinfo(
                osname=constants.SMBIOS_OSNAME,
                osversion=osVersion,
                hostUUID=utils.getHostUUID())

        domxml.appendClock()

        if self.arch == caps.Architecture.X86_64:
            domxml.appendFeatures()

        domxml.appendCpu()
        if utils.tobool(self.conf.get('vmchannel', 'true')):
            domxml._appendAgentDevice(self._guestSocketFile.decode('utf-8'),
                                      _VMCHANNEL_DEVICE_NAME)
        if utils.tobool(self.conf.get('qgaEnable', 'true')):
            domxml._appendAgentDevice(
                self._qemuguestSocketFile.decode('utf-8'),
                _QEMU_GA_DEVICE_NAME)
        domxml.appendInput()
        domxml.appendGraphics()

        if self.arch == caps.Architecture.PPC64:
            domxml.appendEmulator()

        self._appendDevices(domxml)

        for drive in self._devices[DISK_DEVICES][:]:
            for leaseElement in drive.getLeasesXML():
                domxml._devices.appendChild(leaseElement)

        if self.arch == caps.Architecture.PPC64:
            domxml.appendKeyboardDevice()

        return domxml.toxml()

    def _initVmStats(self):
        self._vmStats = VmStatsThread(self)
        self._vmStats.start()
        self._guestEventTime = self._startTime

    @staticmethod
    def _guestSockCleanup(sock):
        if os.path.islink(sock):
            utils.rmFile(os.path.realpath(sock))
        utils.rmFile(sock)

    def _cleanup(self):
        """
        General clean up routine
        """
        self._cleanupDrives()
        self._cleanupFloppy()
        self._cleanupGuestAgent()
        utils.rmFile(self._recoveryFile)
        self._guestSockCleanup(self._qemuguestSocketFile)

    def updateGuestCpuRunning(self):
        self._guestCpuRunning = (self._dom.info()[0] ==
                                 libvirt.VIR_DOMAIN_RUNNING)

    def _getUnderlyingVmDevicesInfo(self):
        """
        Obtain underlying vm's devices info from libvirt.
        """
        self._getUnderlyingNetworkInterfaceInfo()
        self._getUnderlyingDriveInfo()
        self._getUnderlyingDisplayPort()
        self._getUnderlyingSoundDeviceInfo()
        self._getUnderlyingVideoDeviceInfo()
        self._getUnderlyingControllerDeviceInfo()
        self._getUnderlyingBalloonDeviceInfo()
        self._getUnderlyingWatchdogDeviceInfo()
        self._getUnderlyingSmartcardDeviceInfo()
        self._getUnderlyingConsoleDeviceInfo()
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
        agentChannelXml = _domParseStr(self._lastXMLDesc).childNodes[0]. \
            getElementsByTagName('devices')[0]. \
            getElementsByTagName('channel')
        for channel in agentChannelXml:
            try:
                name = channel.getElementsByTagName('target')[0].\
                    getAttribute('name')
                path = channel.getElementsByTagName('source')[0].\
                    getAttribute('path')
            except IndexError:
                continue

            if name not in _AGENT_CHANNEL_DEVICES:
                continue

            if os.path.islink(path):
                os.unlink(path)

            socketPath = self._makeChannelPath(name)
            if path != socketPath:
                os.symlink(path, socketPath)

    def _domDependentInit(self):
        if self.destroyed:
            # reaching here means that Vm.destroy() was called before we could
            # handle it. We must handle it now
            try:
                self._dom.destroy()
            except Exception:
                pass
            raise Exception('destroy() called before Vm started')

        self._getUnderlyingVmInfo()
        self._getUnderlyingVmDevicesInfo()
        self._updateAgentChannels()

        #Currently there is no protection agains mirroring a network twice,
        if 'recover' not in self.conf:
            for nic in self._devices[NIC_DEVICES]:
                if hasattr(nic, 'portMirroring'):
                    for network in nic.portMirroring:
                        supervdsm.getProxy().setPortMirroring(network,
                                                              nic.name)

        # VmStatsThread may use block devices info from libvirt.
        # So, run it after you have this info
        self._initVmStats()
        self.guestAgent = guestIF.GuestAgent(
            self._guestSocketFile, self.cif.channelListener, self.log,
            connect=utils.tobool(self.conf.get('vmchannel', 'true')))

        self._guestCpuRunning = (self._dom.info()[0] ==
                                 libvirt.VIR_DOMAIN_RUNNING)
        if self.lastStatus not in ('Migration Destination',
                                   'Restoring state'):
            self._initTimePauseCode = self._readPauseCode(0)
        if 'recover' not in self.conf and self._initTimePauseCode:
            self.conf['pauseCode'] = self._initTimePauseCode
            if self._initTimePauseCode == 'ENOSPC':
                self.cont()
        self.conf['pid'] = self._getPid()

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

    def _run(self):
        self.log.info("VM wrapper has started")
        self.conf['smp'] = self.conf.get('smp', '1')
        devices = self.buildConfDevices()

        if not 'recover' in self.conf:
            self.preparePaths(devices[DISK_DEVICES])
            self._prepareTransientDisks(devices[DISK_DEVICES])
            # Update self.conf with updated devices
            # For old type vmParams, new 'devices' key will be
            # created with all devices info
            newDevices = []
            for dev in devices.values():
                newDevices.extend(dev)

            self.conf['devices'] = newDevices
            # We need to save conf here before we actually run VM.
            # It's not enough to save conf only on status changes as we did
            # before, because if vdsm will restarted between VM run and conf
            # saving we will fail in inconsistent state during recovery.
            # So, to get proper device objects during VM recovery flow
            # we must to have updated conf before VM run
            self.saveState()
        else:
            for drive in devices[DISK_DEVICES]:
                if drive['device'] == 'disk' and isVdsmImage(drive):
                    self.sdIds.append(drive['domainID'])

        for devType, devClass in self.DeviceMapping:
            for dev in devices[devType]:
                self._devices[devType].append(devClass(self.conf, self.log,
                                                       **dev))

        # We should set this event as a last part of drives initialization
        self._pathsPreparedEvent.set()

        if self.conf.get('migrationDest'):
            return
        if not 'recover' in self.conf:
            domxml = hooks.before_vm_start(self._buildCmdLine(), self.conf)
            self.log.debug(domxml)
        if 'recover' in self.conf:
            self._dom = NotifyingVirDomain(
                self._connection.lookupByUUIDString(self.id),
                self._timeoutExperienced)
            # Reinitialize the merge statuses
            self._checkMerge()
        elif 'restoreState' in self.conf:
            fromSnapshot = self.conf.get('restoreFromSnapshot', False)
            srcDomXML = self.conf.pop('_srcDomXML')
            if fromSnapshot:
                srcDomXML = self._correctDiskVolumes(srcDomXML)
            hooks.before_vm_dehibernate(srcDomXML, self.conf,
                                        {'FROM_SNAPSHOT': str(fromSnapshot)})

            fname = self.cif.prepareVolumePath(self.conf['restoreState'])
            try:
                if fromSnapshot:
                    self._connection.restoreFlags(fname, srcDomXML, 0)
                else:
                    self._connection.restore(fname)
            finally:
                self.cif.teardownVolumePath(self.conf['restoreState'])

            self._dom = NotifyingVirDomain(
                self._connection.lookupByUUIDString(self.id),
                self._timeoutExperienced)
        else:
            flags = libvirt.VIR_DOMAIN_NONE
            if 'launchPaused' in self.conf:
                flags |= libvirt.VIR_DOMAIN_START_PAUSED
                self.conf['pauseCode'] = 'NOERR'
                del self.conf['launchPaused']
            self._dom = NotifyingVirDomain(
                self._connection.createXML(domxml, flags),
                self._timeoutExperienced)
            hooks.after_vm_start(self._dom.XMLDesc(0), self.conf)
            for dev in self._customDevices():
                hooks.after_device_create(dev._deviceXML, self.conf,
                                          dev.custom)

        if not self._dom:
            self.setDownStatus(ERROR, 'failed to start libvirt vm')
            return
        self._domDependentInit()

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

    def _changeDisk(self, diskDeviceXmlElement):
        diskType = diskDeviceXmlElement.getAttribute('type')

        if diskType not in ['file', 'block']:
            return

        diskSerial = diskDeviceXmlElement. \
            getElementsByTagName('serial')[0].childNodes[0].nodeValue

        for vmDrive in self._devices[DISK_DEVICES]:
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
            return errCode['migInProgress']

        nicParams = params['nic']
        nic = NetworkInterfaceDevice(self.conf, self.log, **nicParams)
        customProps = getattr(nic, 'custom', {})
        nicXml = nic.getXML().toprettyxml(encoding='utf-8')
        nicXml = hooks.before_nic_hotplug(nicXml, self.conf,
                                          params=customProps)
        nic._deviceXML = nicXml
        self.log.debug("Hotplug NIC xml: %s", nicXml)

        try:
            self._dom.attachDevice(nicXml)
        except libvirt.libvirtError as e:
            self.log.error("Hotplug failed", exc_info=True)
            nicXml = hooks.after_nic_hotplug_fail(
                nicXml, self.conf, params=customProps)
            if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                return errCode['noVM']
            return {'status': {'code': errCode['hotplugNic']['status']['code'],
                               'message': e.message}}
        else:
            # FIXME!  We may have a problem here if vdsm dies right after
            # we sent command to libvirt and before save conf. In this case
            # we will gather almost all needed info about this NIC from
            # the libvirt during recovery process.
            self._devices[NIC_DEVICES].append(nic)
            with self._confLock:
                self.conf['devices'].append(nicParams)
            self.saveState()
            self._getUnderlyingNetworkInterfaceInfo()
            hooks.after_nic_hotplug(nicXml, self.conf,
                                    params=customProps)

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
                self.log.error("setPortMirroring for network %s failed",
                               network, exc_info=True)
                nicParams['portMirroring'] = mirroredNetworks
                self.hotunplugNic({'nic': nicParams})
                return {'status':
                        {'code': errCode['hotplugNic']['status']['code'],
                         'message': e.message}}

        return {'status': doneCode, 'vmList': self.status()}

    def _lookupDeviceByAlias(self, devType, alias):
        for dev in self._devices[devType][:]:
            if dev.alias == alias:
                return dev
        raise LookupError('Device instance for device identified by alias %s '
                          'not found' % alias)

    def _lookupConfByAlias(self, alias):
        for devConf in self.conf['devices'][:]:
            if devConf['type'] == NIC_DEVICES and \
                    devConf['alias'] == alias:
                return devConf
        raise LookupError('Configuration of device identified by alias %s not'
                          'found' % alias)

    def _updateInterfaceDevice(self, params):
        try:
            netDev = self._lookupDeviceByAlias(NIC_DEVICES, params['alias'])
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
            return {'status':
                    {'code': errCode['updateDevice']['status']['code'],
                     'message': e.message}}

    @contextmanager
    def setLinkAndNetwork(self, dev, conf, linkValue, networkValue, custom,
                          specParams=None):
        vnicXML = dev.getXML()
        source = vnicXML.getElementsByTagName('source')[0]
        source.setAttribute('bridge', networkValue)
        try:
            link = vnicXML.getElementsByTagName('link')[0]
        except IndexError:
            link = xml.dom.minidom.Element('link')
            vnicXML.appendChildWithArgs(link)
        link.setAttribute('state', linkValue)
        if (specParams and
                ('inbound' in specParams or 'outbound' in specParams)):
            oldBandwidths = vnicXML.getElementsByTagName('bandwidth')
            oldBandwidth = oldBandwidths[0] if len(oldBandwidths) else None
            newBandwidth = dev.getXMLBandwidth(specParams, oldBandwidth)
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
                self.log.debug("Nic has been updated:\n %s" % vnicStrXML)
                hooks.after_update_device(vnicStrXML, self.conf, custom)
            except Exception as e:
                self.log.debug('Request failed: %s', vnicStrXML, exc_info=True)
                hooks.after_update_device_fail(vnicStrXML, self.conf, custom)
                raise SetLinkAndNetworkError(e.message)
            yield
        except Exception:
            # Rollback link and network.
            self.log.debug('Rolling back link and net for: %s', dev.alias,
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
            self.log.error(
                "%s for network %s failed",
                'setPortMirroring' if network in netsToAdd else
                'unsetPortMirroring',
                network,
                exc_info=True)
            # In case we fail, we rollback the Network mirroring.
            for network in mirroredNetworks:
                supervdsm.getProxy().unsetPortMirroring(network, devName)
            for network in droppedNetworks:
                supervdsm.getProxy().setPortMirroring(network, devName)
            raise UpdatePortMirroringError(e.message)
        else:
            # Update the conf with the new mirroring.
            conf['portMirroring'] = networks

    def updateDevice(self, params):
        if params.get('deviceType') == NIC_DEVICES:
            return self._updateInterfaceDevice(params)
        else:
            return errCode['noimpl']

    def hotunplugNic(self, params):
        if self.isMigrating():
            return errCode['migInProgress']

        nicParams = params['nic']

        # Find NIC object in vm's NICs list
        nic = None
        for dev in self._devices[NIC_DEVICES][:]:
            if dev.macAddr.lower() == nicParams['macAddr'].lower():
                nic = dev
                break

        if nic:
            if 'portMirroring' in nicParams:
                for network in nicParams['portMirroring']:
                    supervdsm.getProxy().unsetPortMirroring(network, nic.name)

            customProps = getattr(nic, 'custom', {})
            nicXml = nic.getXML().toprettyxml(encoding='utf-8')
            hooks.before_nic_hotunplug(nicXml, self.conf,
                                       params=customProps)
            self.log.debug("Hotunplug NIC xml: %s", nicXml)
        else:
            self.log.error("Hotunplug NIC failed - NIC not found: %s",
                           nicParams)
            return {'status': {'code': errCode['hotunplugNic']
                                              ['status']['code'],
                               'message': "NIC not found"}}

        # Remove found NIC from vm's NICs list
        if nic:
            self._devices[NIC_DEVICES].remove(nic)
        # Find and remove NIC device from vm's conf
        nicDev = None
        for dev in self.conf['devices'][:]:
            if (dev['type'] == NIC_DEVICES and
                    dev['macAddr'].lower() == nicParams['macAddr'].lower()):
                with self._confLock:
                    self.conf['devices'].remove(dev)
                nicDev = dev
                break

        self.saveState()

        try:
            self._dom.detachDevice(nicXml)
        except libvirt.libvirtError as e:
            self.log.error("Hotunplug failed", exc_info=True)
            if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                return errCode['noVM']
            # Restore NIC device in vm's conf and _devices
            if nicDev:
                with self._confLock:
                    self.conf['devices'].append(nicDev)
            if nic:
                self._devices[NIC_DEVICES].append(nic)
            self.saveState()
            hooks.after_nic_hotunplug_fail(nicXml, self.conf,
                                           params=customProps)
            return {
                'status': {'code': errCode['hotunplugNic']['status']['code'],
                           'message': e.message}}

        hooks.after_nic_hotunplug(nicXml, self.conf,
                                  params=customProps)
        return {'status': doneCode, 'vmList': self.status()}

    def setNumberOfCpus(self, numberOfCpus):

        if self.isMigrating():
            return errCode['migInProgress']

        self.log.debug("Setting number of cpus to : %s", numberOfCpus)
        hooks.before_set_num_of_cpus()
        try:
            self._dom.setVcpusFlags(numberOfCpus,
                                    libvirt.VIR_DOMAIN_AFFECT_CURRENT)
        except libvirt.libvirtError as e:
            self.log.error("setNumberOfCpus failed", exc_info=True)
            if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                return errCode['noVM']
            return {'status': {'code': errCode['setNumberOfCpusErr']
                    ['status']['code'], 'message': e.message}}

        self.conf['smp'] = str(numberOfCpus)
        self.saveState()
        hooks.after_set_num_of_cpus()
        return {'status': doneCode, 'vmList': self.status()}

    def _createTransientDisk(self, diskParams):
        if diskParams.get('shared', None) != DRIVE_SHARED_TYPE.TRANSIENT:
            return

        # FIXME: This should be replaced in future the support for transient
        # disk in libvirt (BZ#832194)
        driveFormat = (
            qemuImg.FORMAT.QCOW2 if diskParams['format'] == 'cow' else
            qemuImg.FORMAT.RAW
        )

        transientHandle, transientPath = tempfile.mkstemp(
            dir=config.get('vars', 'transient_disks_repository'),
            prefix="%s-%s." % (diskParams['domainID'], diskParams['volumeID']))

        try:
            qemuImg.create(transientPath, format=qemuImg.FORMAT.QCOW2,
                           backing=diskParams['path'],
                           backingFormat=driveFormat)
            os.fchmod(transientHandle, 0o660)
        except Exception:
            os.unlink(transientPath)  # Closing after deletion is correct
            self.log.error("Failed to create the transient disk for "
                           "volume %s", diskParams['volumeID'], exc_info=True)
        finally:
            os.close(transientHandle)

        diskParams['path'] = transientPath
        diskParams['format'] = 'cow'

    def _removeTransientDisk(self, drive):
        if drive.transientDisk:
            os.unlink(drive.path)

    def hotplugDisk(self, params):
        if self.isMigrating():
            return errCode['migInProgress']

        diskParams = params.get('drive', {})
        diskParams['path'] = self.cif.prepareVolumePath(diskParams)
        vdsmImg = isVdsmImage(diskParams)

        if vdsmImg:
            self._normalizeVdsmImg(diskParams)
            self._createTransientDisk(diskParams)

        self.updateDriveIndex(diskParams)
        drive = Drive(self.conf, self.log, **diskParams)

        if drive.hasVolumeLeases:
            return errCode['noimpl']

        customProps = getattr(drive, 'custom', {})
        driveXml = drive.getXML().toprettyxml(encoding='utf-8')
        self.log.debug("Hotplug disk xml: %s" % (driveXml))

        driveXml = hooks.before_disk_hotplug(driveXml, self.conf,
                                             params=customProps)
        drive._deviceXML = driveXml
        try:
            self._dom.attachDevice(driveXml)
        except libvirt.libvirtError as e:
            self.log.error("Hotplug failed", exc_info=True)
            self.cif.teardownVolumePath(diskParams)
            if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                return errCode['noVM']
            return {'status': {'code': errCode['hotplugDisk']
                                              ['status']['code'],
                               'message': e.message}}
        else:
            # FIXME!  We may have a problem here if vdsm dies right after
            # we sent command to libvirt and before save conf. In this case
            # we will gather almost all needed info about this drive from
            # the libvirt during recovery process.
            self._devices[DISK_DEVICES].append(drive)
            if vdsmImg:
                self.sdIds.append(diskParams['domainID'])
            with self._confLock:
                self.conf['devices'].append(diskParams)
            self.saveState()
            self._getUnderlyingDriveInfo()
            hooks.after_disk_hotplug(driveXml, self.conf,
                                     params=customProps)

        return {'status': doneCode, 'vmList': self.status()}

    def hotunplugDisk(self, params):
        if self.isMigrating():
            return errCode['migInProgress']

        diskParams = params.get('drive', {})
        diskParams['path'] = self.cif.prepareVolumePath(diskParams)

        try:
            drive = self._findDriveByUUIDs(diskParams)
        except LookupError:
            self.log.error("Hotunplug disk failed - Disk not found: %s",
                           diskParams)
            return {'status': {
                'code': errCode['hotunplugDisk']['status']['code'],
                'message': "Disk not found"
            }}

        if drive.hasVolumeLeases:
            return errCode['noimpl']

        customProps = getattr(drive, 'custom', {})
        driveXml = drive.getXML().toprettyxml(encoding='utf-8')
        self.log.debug("Hotunplug disk xml: %s", driveXml)
        # Remove found disk from vm's drives list
        if isVdsmImage(drive):
            self.sdIds.remove(drive.domainID)
        self._devices[DISK_DEVICES].remove(drive)
        # Find and remove disk device from vm's conf
        diskDev = None
        for dev in self.conf['devices'][:]:
            if (dev['type'] == DISK_DEVICES and
                    dev['path'] == drive.path):
                with self._confLock:
                    self.conf['devices'].remove(dev)
                diskDev = dev
                break

        self.saveState()

        hooks.before_disk_hotunplug(driveXml, self.conf,
                                    params=customProps)
        try:
            self._dom.detachDevice(driveXml)
        except libvirt.libvirtError as e:
            self.log.error("Hotunplug failed", exc_info=True)
            if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                return errCode['noVM']
            self._devices[DISK_DEVICES].append(drive)
            # Restore disk device in vm's conf and _devices
            if diskDev:
                with self._confLock:
                    self.conf['devices'].append(diskDev)
            self.saveState()
            return {
                'status': {'code': errCode['hotunplugDisk']['status']['code'],
                           'message': e.message}}
        else:
            hooks.after_disk_hotunplug(driveXml, self.conf,
                                       params=customProps)
            self._cleanupDrives(drive)

        return {'status': doneCode, 'vmList': self.status()}

    def _readPauseCode(self, timeout):
        self.log.warning('_readPauseCode unsupported by libvirt vm')
        return 'NOERR'

    def _monitorDependentInit(self, timeout=None):
        self.log.warning('unsupported by libvirt vm')

    def _timeoutExperienced(self, timeout):
        if timeout:
            self._monitorResponse = -1
        else:
            self._monitorResponse = 0

    def _waitForIncomingMigrationFinish(self):
        if 'restoreState' in self.conf:
            self.cont()
            del self.conf['restoreState']
            fromSnapshot = self.conf.pop('restoreFromSnapshot', False)
            hooks.after_vm_dehibernate(self._dom.XMLDesc(0), self.conf,
                                       {'FROM_SNAPSHOT': fromSnapshot})
        elif 'migrationDest' in self.conf:
            timeout = config.getint('vars', 'migration_destination_timeout')
            self.log.debug("Waiting %s seconds for end of migration", timeout)
            self._incomingMigrationFinished.wait(timeout)

            try:
                # Would fail if migration isn't successful,
                # or restart vdsm if connection to libvirt was lost
                self._dom = NotifyingVirDomain(
                    self._connection.lookupByUUIDString(self.id),
                    self._timeoutExperienced)

                if not self._incomingMigrationFinished.isSet():
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
                    if not self._incomingMigrationFinished.isSet():
                        newMsg = ('%s - Timed out '
                                  '(did not receive success event)' %
                                  (e.args[0] if len(e.args) else
                                   'Migration Error'))
                        e.args = (newMsg,) + e.args[1:]
                raise

            self._domDependentInit()
            del self.conf['migrationDest']
            hooks.after_vm_migrate_destination(self._dom.XMLDesc(0), self.conf)

            for dev in self._customDevices():
                hooks.after_device_migrate_destination(
                    dev._deviceXML, self.conf, dev.custom)

        if 'guestIPs' in self.conf:
            del self.conf['guestIPs']
        if 'guestFQDN' in self.conf:
            del self.conf['guestFQDN']
        if 'username' in self.conf:
            del self.conf['username']
        self.saveState()
        self.log.debug("End of migration")

    def _underlyingCont(self):
        hooks.before_vm_cont(self._dom.XMLDesc(0), self.conf)
        self._dom.resume()

    def _underlyingPause(self):
        hooks.before_vm_pause(self._dom.XMLDesc(0), self.conf)
        self._dom.suspend()

    def _findDriveByName(self, name):
        for device in self._devices[DISK_DEVICES][:]:
            if device.name == name:
                return device
        raise LookupError("No such drive: '%s'" % name)

    def _findDriveByUUIDs(self, drive):
        """Find a drive given its definition"""

        if "domainID" in drive:
            tgetDrv = (drive["domainID"], drive["imageID"],
                       drive["volumeID"])

            for device in self._devices[DISK_DEVICES][:]:
                if not hasattr(device, "domainID"):
                    continue
                if (device.domainID, device.imageID,
                        device.volumeID) == tgetDrv:
                    return device

        elif "GUID" in drive:
            for device in self._devices[DISK_DEVICES][:]:
                if not hasattr(device, "GUID"):
                    continue
                if device.GUID == drive["GUID"]:
                    return device

        elif "UUID" in drive:
            for device in self._devices[DISK_DEVICES][:]:
                if not hasattr(device, "UUID"):
                    continue
                if device.UUID == drive["UUID"]:
                    return device

        raise LookupError("No such drive: '%s'" % drive)

    def updateDriveVolume(self, vmDrive):
        if not vmDrive.device == 'disk' or not isVdsmImage(vmDrive):
            return

        volSize = self.cif.irs.getVolumeSize(
            vmDrive.domainID, vmDrive.poolID, vmDrive.imageID,
            vmDrive.volumeID)

        if volSize['status']['code'] != 0:
            self.log.error(
                "Unable to update the volume %s (domain: %s image: %s) "
                "for the drive %s" % (vmDrive.volumeID, vmDrive.domainID,
                                      vmDrive.imageID, vmDrive.name))
            return

        vmDrive.truesize = int(volSize['truesize'])
        vmDrive.apparentsize = int(volSize['apparentsize'])

    def updateDriveParameters(self, driveParams):
        """Update the drive with the new volume information"""

        # Updating the vmDrive object
        for vmDrive in self._devices[DISK_DEVICES][:]:
            if vmDrive.name == driveParams["name"]:
                for k, v in driveParams.iteritems():
                    setattr(vmDrive, k, v)
                self.updateDriveVolume(vmDrive)
                break
        else:
            self.log.error("Unable to update the drive object for: %s",
                           driveParams["name"])

        # Updating the VM configuration
        for vmDriveConfig in self.conf["devices"][:]:
            if (vmDriveConfig['type'] == DISK_DEVICES and
                    vmDriveConfig.get("name") == driveParams["name"]):
                vmDriveConfig.update(driveParams)
                break
        else:
            self.log.error("Unable to update the device configuration ",
                           "for: %s", driveParams["name"])

        self.saveState()

    def snapshot(self, snapDrives, memoryParams):
        """Live snapshot command"""

        def _diskSnapshot(vmDev, newPath):
            """Libvirt snapshot XML"""

            disk = XMLElement('disk', name=vmDev, snapshot='external')
            disk.appendChildWithArgs('source', file=newPath)
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
                    self.log.error("Unable to teardown drive: %s", vmDevName,
                                   exc_info=True)

        def _memorySnapshot(memoryVolumePath):
            """Libvirt snapshot XML"""

            memory = xml.dom.minidom.Element('memory')
            memory.setAttribute('snapshot', 'external')
            memory.setAttribute('file', memoryVolumePath)
            return memory

        def _vmConfForMemorySnapshot():
            """Returns the needed vm configuration with the memory snapshot"""

            return {'restoreFromSnapshot':   True,
                    '_srcDomXML':            self._dom.XMLDesc(0),
                    'elapsedTimeOffset':     time.time() - self._startTime}

        def _padMemoryVolume(memoryVolPath, spType, sdUUId):
            if spType == sd.NFS_DOMAIN:
                oop.getProcessPool(sdUUID).fileUtils. \
                    padToBlockSize(memoryVolPath)
            else:
                fileUtils.padToBlockSize(memoryVolPath)

        snap = xml.dom.minidom.Element('domainsnapshot')
        disks = xml.dom.minidom.Element('disks')
        newDrives = {}

        if self.isMigrating():
            return errCode['migInProgress']

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
                return errCode['snapshotErr']

            if vmDrive.hasVolumeLeases:
                self.log.error('disk %s has volume leases', vmDrive.name)
                return errCode['noimpl']

            if vmDrive.transientDisk:
                self.log.error('disk %s is a transient disk', vmDrive.name)
                return errCode['transientErr']

            vmDevName = vmDrive.name

            newDrives[vmDevName] = tgetDrv.copy()
            newDrives[vmDevName]["poolID"] = vmDrive.poolID
            newDrives[vmDevName]["name"] = vmDevName
            newDrives[vmDevName]["format"] = "cow"

        # If all the drives are the current ones, return success
        if len(newDrives) == 0:
            self.log.debug('all the drives are already in use, success')
            return {'status': doneCode}

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
                return errCode['snapshotErr']

            snapelem = _diskSnapshot(vmDevName, newDrives[vmDevName]["path"])
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
                with open(vmConfVolPath, "w") as f:
                    pickle.dump(vmConf, f)
            finally:
                self.cif.teardownVolumePath(vmConfVol)

            # Adding the memory volume to the snapshot xml
            memoryVol = memoryParams['dst']
            memoryVolPath = self.cif.prepareVolumePath(memoryVol)
            snap.appendChild(_memorySnapshot(memoryVolPath))
        else:
            snapFlags |= libvirt.VIR_DOMAIN_SNAPSHOT_CREATE_DISK_ONLY

            if utils.tobool(self.conf.get('qgaEnable', 'true')):
                snapFlags |= libvirt.VIR_DOMAIN_SNAPSHOT_CREATE_QUIESCE

        snapxml = snap.toprettyxml()
        self.log.debug(snapxml)

        # We need to stop the collection of the stats for two reasons, one
        # is to prevent spurious libvirt errors about missing drive paths
        # (since we're changing them), and also to prevent to trigger a drive
        # extension for the new volume with the apparent size of the old one
        # (the apparentsize is updated as last step in updateDriveParameters)
        self.stopDisksStatsCollection()

        try:
            try:
                self._dom.snapshotCreateXML(snapxml, snapFlags)
            except Exception as e:
                # Trying again without VIR_DOMAIN_SNAPSHOT_CREATE_QUIESCE.
                # At the moment libvirt is returning two generic errors
                # (INTERNAL_ERROR, ARGUMENT_UNSUPPORTED) which are too broad
                # to be caught (BZ#845635).
                snapFlags &= (~libvirt.VIR_DOMAIN_SNAPSHOT_CREATE_QUIESCE)
                # Here we don't need a full stacktrace (exc_info) but it's
                # still interesting knowing what was the error
                self.log.debug("Snapshot failed using the quiesce flag, "
                               "trying again without it (%s)", e)
                try:
                    self._dom.snapshotCreateXML(snapxml, snapFlags)
                except Exception as e:
                    self.log.error("Unable to take snapshot", exc_info=True)
                    if memoryParams:
                        self.cif.teardownVolumePath(memoryVol)
                    return errCode['snapshotErr']

            # We are padding the memory volume with block size of zeroes
            # because qemu-img truncates files such that their size is
            # round down to the closest multiple of block size (bz 970559).
            # This code should be removed once qemu-img will handle files
            # with size that is not multiple of block size correctly.
            if memoryParams:
                sdUUID = memoryVol['domainID']
                spUUID = memoryVol['poolID']
                spType = sd.name2type(
                    self.cif.irs.getStoragePoolInfo(spUUID)['info']['type'])
                if spType in sd.FILE_DOMAIN_TYPES:
                    _padMemoryVolume(memoryVolPath, spType, sdUUID)
                self.cif.teardownVolumePath(memoryVol)

            for drive in newDrives.values():  # Update the drive information
                try:
                    self.updateDriveParameters(drive)
                except Exception:
                    # Here it's too late to fail, the switch already happened
                    # and there's nothing we can do, we must to proceed anyway
                    # to report the live snapshot success.
                    self.log.error("Failed to update drive information for "
                                   "'%s'", drive, exc_info=True)
        finally:
            self.startDisksStatsCollection()

        # Returning quiesce to notify the manager whether the guest agent
        # froze and flushed the filesystems or not.
        return {'status': doneCode, 'quiesce':
                (snapFlags & libvirt.VIR_DOMAIN_SNAPSHOT_CREATE_QUIESCE
                    == libvirt.VIR_DOMAIN_SNAPSHOT_CREATE_QUIESCE)}

    def _runMerge(self):
        for mergeStatus in self.conf.get('liveMerge', []):
            if mergeStatus['status'] != MERGESTATUS.NOT_STARTED:
                continue

            try:
                self._dom.blockRebase(mergeStatus['path'],
                                      mergeStatus['basePath'], 0, 0)
            except Exception:
                mergeStatus['status'] = MERGESTATUS.FAILED
                self.log.error("Live merge failed for %s",
                               mergeStatus['path'], exc_info=True)
            else:
                mergeStatus['status'] = MERGESTATUS.IN_PROGRESS

        self.saveState()

    def _checkMerge(self):
        for mergeStatus in self.conf.get('liveMerge', []):
            if mergeStatus['status'] != MERGESTATUS.IN_PROGRESS:
                continue

            try:
                jobInfo = self._dom.blockJobInfo(mergeStatus['path'], 0)
            except Exception:
                jobInfo = None

            if not jobInfo:
                mergeStatus['status'] = MERGESTATUS.UNKNOWN

        self.saveState()

    def merge(self, mergeDrives):
        """Live merge command"""

        # Check if there is a merge still in progress
        for mergeStatus in self.conf.get('liveMerge', []):
            if mergeStatus['status'] == MERGESTATUS.IN_PROGRESS:
                return errCode['mergeErr']

        self.conf['liveMerge'] = []

        # Preparing the merge statuses
        for drive in mergeDrives:
            try:
                mergeDrive = self._findDriveByUUIDs(drive)
            except LookupError:
                mergeDrive = None

            mergeStatus = drive.copy()
            mergeStatus['status'] = MERGESTATUS.NOT_STARTED

            if not mergeDrive or not hasattr(mergeDrive, 'volumeChain'):
                mergeStatus['status'] = MERGESTATUS.DRIVE_NOT_FOUND
            elif mergeDrive.hasVolumeLeases or mergeDrive.transientDisk:
                mergeStatus['status'] = MERGESTATUS.DRIVE_NOT_SUPPORTED
            else:
                for volume in mergeDrive.volumeChain:
                    # qemu-kvm looks up for the backing file path looking at
                    # the value sotred in the qcow2 header, therefore here
                    # we can't use the absolute path provided by prepareImage
                    if volume['volumeID'] == drive['baseVolumeID']:
                        mergeStatus['basePath'] = "../%s/%s" % (
                            volume['imageID'], volume['volumeID'])
                        break
                else:
                    mergeStatus['status'] = MERGESTATUS.BASE_NOT_FOUND

                mergeStatus['path'] = mergeDrive.path
                mergeStatus['disk'] = mergeDrive.name

            self.conf['liveMerge'].append(mergeStatus)

        self.saveState()
        self._runMerge()

        return {'status': doneCode}

    def mergeStatus(self):
        def _filterInternalInfo(mergeStatus):
            return dict((k, v) for k, v in mergeStatus.iteritems()
                        if k not in ("path", "basePath"))

        mergeStatus = [_filterInternalInfo(x)
                       for x in self.conf.get('liveMerge', [])]

        return {'status': doneCode, 'mergeStatus': mergeStatus}

    def _onBlockJobEvent(self, path, type, status):
        for mergeStatus in self.conf.get('liveMerge', []):
            if mergeStatus['path'] == path:
                break
        else:
            self.log.error("Live merge completed for an unexpected "
                           "path: %s", path)
            return

        if status == libvirt.VIR_DOMAIN_BLOCK_JOB_COMPLETED:
            mergeStatus['status'] = MERGESTATUS.COMPLETED
        else:
            mergeStatus['status'] = MERGESTATUS.FAILED

        self.saveState()

    def _setDiskReplica(self, srcDrive, dstDisk):
        """
        This utility method is used to set the disk replication information
        both in the live object used by vdsm and the vm configuration
        dictionary that is stored on disk (so that the information is not
        lost across restarts).
        """
        if srcDrive.isDiskReplicationInProgress():
            raise RuntimeError("Disk '%s' already has an ongoing "
                               "replication" % srcDrive.name)

        for device in self.conf["devices"]:
            if (device['type'] == DISK_DEVICES
                    and device.get("name") == srcDrive.name):
                with self._confLock:
                    device['diskReplicate'] = dstDisk
                self.saveState()
                break
        else:
            raise LookupError("No such drive: '%s'" % srcDrive.name)

        srcDrive.diskReplicate = dstDisk

    def _delDiskReplica(self, srcDrive):
        """
        This utility method is the inverse of _setDiskReplica, look at the
        _setDiskReplica description for more information.
        """
        for device in self.conf["devices"]:
            if (device['type'] == DISK_DEVICES
                    and device.get("name") == srcDrive.name):
                with self._confLock:
                    del device['diskReplicate']
                self.saveState()
                break
        else:
            raise LookupError("No such drive: '%s'" % srcDrive.name)

        del srcDrive.diskReplicate

    def diskReplicateStart(self, srcDisk, dstDisk):
        try:
            srcDrive = self._findDriveByUUIDs(srcDisk)
        except LookupError:
            self.log.error("Unable to find the disk for '%s'", srcDisk)
            return errCode['imageErr']

        if srcDrive.hasVolumeLeases:
            return errCode['noimpl']

        if srcDrive.transientDisk:
            return errCode['transientErr']

        try:
            self._setDiskReplica(srcDrive, dstDisk)
        except Exception:
            self.log.error("Unable to set the replication for disk '%s' with "
                           "destination '%s'" % srcDrive.name, dstDisk)
            return errCode['replicaErr']

        dstDiskCopy = dstDisk.copy()

        # The device entry is enforced because stricly required by
        # prepareVolumePath
        dstDiskCopy.update({'device': srcDrive.device})

        try:
            dstDiskCopy['path'] = self.cif.prepareVolumePath(dstDiskCopy)

            try:
                self._dom.blockRebase(srcDrive.name, dstDiskCopy['path'], 0, (
                    libvirt.VIR_DOMAIN_BLOCK_REBASE_COPY |
                    libvirt.VIR_DOMAIN_BLOCK_REBASE_REUSE_EXT |
                    libvirt.VIR_DOMAIN_BLOCK_REBASE_SHALLOW
                ))
            except Exception:
                self.log.error("Unable to start the replication for %s to %s",
                               srcDrive.name, dstDiskCopy, exc_info=True)
                self.cif.teardownVolumePath(dstDiskCopy)
                raise
        except Exception:
            self.log.error("Cannot complete the disk replication process",
                           exc_info=True)
            self._delDiskReplica(srcDrive)
            return errCode['replicaErr']

        try:
            self.extendDriveVolume(srcDrive)
        except Exception:
            self.log.error("Initial extension request failed for %s",
                           srcDrive.name, exc_info=True)

        return {'status': doneCode}

    def diskReplicateFinish(self, srcDisk, dstDisk):
        try:
            srcDrive = self._findDriveByUUIDs(srcDisk)
        except LookupError:
            return errCode['imageErr']

        if srcDrive.hasVolumeLeases:
            return errCode['noimpl']

        if srcDrive.transientDisk:
            return errCode['transientErr']

        if not srcDrive.isDiskReplicationInProgress():
            return errCode['replicaErr']

        # Looking for the replication blockJob info (checking its presence)
        blkJobInfo = self._dom.blockJobInfo(srcDrive.name, 0)

        if (not isinstance(blkJobInfo, dict)
                or 'cur' not in blkJobInfo or 'end' not in blkJobInfo):
            self.log.error("Replication job not found for disk %s (%s)",
                           srcDrive.name, srcDisk)

            # Making sure that we don't have any stale information
            self._delDiskReplica(srcDrive)
            return errCode['replicaErr']

        # Checking if we reached the replication mode ("mirroring" in libvirt
        # and qemu terms)
        if blkJobInfo['cur'] != blkJobInfo['end']:
            return errCode['unavail']

        dstDiskCopy = dstDisk.copy()

        # Updating the destination disk device and name, the device is used by
        # prepareVolumePath (required to fill the new information as the path)
        # and the name is used by updateDriveParameters.
        dstDiskCopy.update({'device': srcDrive.device, 'name': srcDrive.name})
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
            diskToTeardown = srcDrive.diskReplicate

        try:
            # Stopping the replication
            self._dom.blockJobAbort(srcDrive.name, blockJobFlags)
        except Exception:
            self.log.error("Unable to stop the replication for the drive: %s",
                           srcDrive.name, exc_info=True)
            try:
                self.cif.teardownVolumePath(srcDrive.diskReplicate)
            except Exception:
                # There is nothing we can do at this point other than logging
                self.log.error("Unable to teardown the replication "
                               "destination disk", exc_info=True)
            return errCode['changeDisk']  # Finally is evaluated
        else:
            try:
                self.cif.teardownVolumePath(diskToTeardown)
            except Exception:
                # There is nothing we can do at this point other than logging
                self.log.error("Unable to teardown the previous chain: %s",
                               diskToTeardown, exc_info=True)
            self.updateDriveParameters(dstDiskCopy)
            if "domainID" in srcDisk:
                self.sdIds.append(dstDiskCopy['domainID'])
                self.sdIds.remove(srcDisk['domainID'])
        finally:
            self._delDiskReplica(srcDrive)
            self.startDisksStatsCollection()

        return {'status': doneCode}

    def _diskSizeExtendCow(self, drive, newSizeBytes):
        # Apparently this is what libvirt would do anyway, except that
        # it would fail on NFS when root_squash is enabled, see BZ#963881
        # Patches have been submitted to avoid this behavior, the virtual
        # and apparent sizes will be returned by the qemu process and
        # through the libvirt blockInfo call.
        currentSize = qemuImg.info(drive.path, "qcow2")['virtualsize']

        if currentSize > newSizeBytes:
            self.log.error(
                "Requested extension size %s for disk %s is smaller "
                "than the current size %s", newSizeBytes, drive.name,
                currentSize)
            return errCode['resizeErr']

        # Uncommit the current volume size (mark as in transaction)
        self.cif.irs.setVolumeSize(drive.domainID, drive.poolID,
                                   drive.imageID, drive.volumeID, 0)

        try:
            self._dom.blockResize(drive.name, newSizeBytes,
                                  libvirt.VIR_DOMAIN_BLOCK_RESIZE_BYTES)
        except libvirt.libvirtError:
            self.log.error(
                "An error occurred while trying to extend the disk %s "
                "to size %s", drive.name, newSizeBytes, exc_info=True)
            return errCode['updateDevice']
        finally:
            # In all cases we want to try and fix the size in the metadata.
            # Same as above, this is what libvirt would do, see BZ#963881
            sizeRoundedBytes = qemuImg.info(drive.path, "qcow2")['virtualsize']
            self.cif.irs.setVolumeSize(
                drive.domainID, drive.poolID, drive.imageID, drive.volumeID,
                sizeRoundedBytes)

        return {'status': doneCode, 'size': str(sizeRoundedBytes)}

    def _diskSizeExtendRaw(self, drive, newSizeBytes):
        # Picking up the volume size extension
        self.__refreshDriveVolume({
            'domainID': drive.domainID, 'poolID': drive.poolID,
            'imageID': drive.imageID, 'volumeID': drive.volumeID,
        })

        volumeInfo = self.cif.irs.getVolumeSize(
            drive.domainID, drive.poolID, drive.imageID, drive.volumeID)

        sizeRoundedBytes = int(volumeInfo['apparentsize'])

        # For the RAW device we use the volumeInfo apparentsize rather
        # than the (possibly) wrong size provided in the request.
        if sizeRoundedBytes != newSizeBytes:
            self.log.info(
                "The requested extension size %s is different from "
                "the RAW device size %s", newSizeBytes, sizeRoundedBytes)

        # At the moment here there's no way to fetch the previous size
        # to compare it with the new one. In the future blockInfo will
        # be able to return the value (fetched from qemu).

        try:
            self._dom.blockResize(drive.name, sizeRoundedBytes,
                                  libvirt.VIR_DOMAIN_BLOCK_RESIZE_BYTES)
        except libvirt.libvirtError:
            self.log.warn(
                "Libvirt failed to notify the new size %s to the "
                "running VM, the change will be available at the ",
                "reboot", sizeRoundedBytes, exc_info=True)
            return errCode['updateDevice']

        return {'status': doneCode, 'size': str(sizeRoundedBytes)}

    def diskSizeExtend(self, driveSpecs, newSizeBytes):
        try:
            newSizeBytes = int(newSizeBytes)
        except ValueError:
            return errCode['resizeErr']

        try:
            drive = self._findDriveByUUIDs(driveSpecs)
        except LookupError:
            return errCode['imageErr']

        try:
            if drive.format == "cow":
                return self._diskSizeExtendCow(drive, newSizeBytes)
            else:
                return self._diskSizeExtendRaw(drive, newSizeBytes)
        except Exception:
            self.log.error("Unable to extend disk %s to size %s",
                           drive.name, newSizeBytes, exc_info=True)

        return errCode['updateDevice']

    def _onWatchdogEvent(self, action):
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
                      "Action: %s", self.conf['vmName'],
                      actionToString(action))

    def changeCD(self, drivespec):
        if self.arch == caps.Architecture.PPC64:
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
            return errCode['imageErr']
        diskelem = XMLElement('disk', type='file', device=vmDev)
        diskelem.appendChildWithArgs('source', file=path)
        diskelem.appendChildWithArgs('target', dev=blockdev)

        try:
            self._dom.updateDeviceFlags(
                diskelem.toxml(), libvirt.VIR_DOMAIN_DEVICE_MODIFY_FORCE)
        except Exception:
            self.log.debug("updateDeviceFlags failed", exc_info=True)
            self.cif.teardownVolumePath(drivespec)
            return {'status': {'code': errCode['changeDisk']['status']['code'],
                               'message': errCode['changeDisk']['status']
                                                 ['message']}}
        self.cif.teardownVolumePath(self.conf.get(vmDev))
        self.conf[vmDev] = path
        return {'status': doneCode, 'vmList': self.status()}

    def setTicket(self, otp, seconds, connAct, params):
        graphics = _domParseStr(self._dom.XMLDesc(0)).childNodes[0]. \
            getElementsByTagName('graphics')[0]
        graphics.setAttribute('passwd', otp)
        if int(seconds) > 0:
            validto = time.strftime('%Y-%m-%dT%H:%M:%S',
                                    time.gmtime(time.time() + float(seconds)))
            graphics.setAttribute('passwdValidTo', validto)
        if graphics.getAttribute('type') == 'spice':
            graphics.setAttribute('connected', connAct)
        hooks.before_vm_set_ticket(self._lastXMLDesc, self.conf, params)
        try:
            self._dom.updateDeviceFlags(graphics.toxml(), 0)
        except TimeoutError as tmo:
            res = {'status': {'code': errCode['ticketErr']['status']['code'],
                              'message': unicode(tmo)}}
        else:
            hooks.after_vm_set_ticket(self._lastXMLDesc, self.conf, params)
            res = {'status': doneCode}
        return res

    def _reviveTicket(self, newlife):
        """Revive an existing ticket, if it has expired or about to expire"""
        graphics = _domParseStr(
            self._dom.XMLDesc(libvirt.VIR_DOMAIN_XML_SECURE)). \
            childNodes[0].getElementsByTagName('graphics')[0]
        validto = max(time.strptime(graphics.getAttribute('passwdValidTo'),
                                    '%Y-%m-%dT%H:%M:%S'),
                      time.gmtime(time.time() + newlife))
        graphics.setAttribute(
            'passwdValidTo', time.strftime('%Y-%m-%dT%H:%M:%S', validto))
        graphics.setAttribute('connected', 'keep')
        self._dom.updateDeviceFlags(graphics.toxml(), 0)

    def _onAbnormalStop(self, blockDevAlias, err):
        """
        Called back by IO_ERROR_REASON event

        :param err: one of "eperm", "eio", "enospc" or "eother"
        Note the different API from that of Vm._onAbnormalStop
        """
        self.log.info('abnormal vm stop device %s error %s',
                      blockDevAlias, err)
        self.conf['pauseCode'] = err.upper()
        self._guestCpuRunning = False
        if err.upper() == 'ENOSPC':
            if not self.extendDrivesIfNeeded():
                self.log.info("No VM drives were extended")

    def _acpiShutdown(self):
        self._dom.shutdownFlags(libvirt.VIR_DOMAIN_SHUTDOWN_ACPI_POWER_BTN)

    def _getPid(self):
        pid = '0'
        try:
            vmName = self.conf['vmName'].encode('utf-8')
            pid = supervdsm.getProxy().getVmPid(vmName)
        except Exception:
            pass
        return pid

    def _getUnderlyingVmInfo(self):
        self._lastXMLDesc = self._dom.XMLDesc(0)
        devxml = _domParseStr(self._lastXMLDesc).childNodes[0]. \
            getElementsByTagName('devices')[0]
        self._devXmlHash = str(hash(devxml.toxml()))

        return self._lastXMLDesc

    def _ejectFloppy(self):
        if 'volatileFloppy' in self.conf:
            utils.rmFile(self.conf['floppy'])
        self._changeBlockDev('floppy', 'fda', '')

    def releaseVm(self):
        """
        Stop VM and release all resources
        """

        # unsetting mirror network will clear both mirroring
        # (on the same network).
        for nic in self._devices[NIC_DEVICES]:
            if hasattr(nic, 'portMirroring'):
                for network in nic.portMirroring:
                    supervdsm.getProxy().unsetPortMirroring(network, nic.name)

        # delete the payload devices
        for drive in self._devices[DISK_DEVICES]:
            if (hasattr(drive, 'specParams') and
                    'vmPayload' in drive.specParams):
                supervdsm.getProxy().removeFs(drive.path)

        with self._releaseLock:
            if self._released:
                return {'status': doneCode}

            self.log.info('Release VM resources')
            self.lastStatus = 'Powering down'
            try:
                # Terminate the VM's creation thread.
                self._incomingMigrationFinished.set()
                if self._vmStats:
                    self._vmStats.stop()
                if self.guestAgent:
                    self.guestAgent.stop()
                if self._dom:
                    try:
                        self._dom.destroyFlags(
                            libvirt.VIR_DOMAIN_DESTROY_GRACEFUL)
                    except libvirt.libvirtError as e:
                        if (e.get_error_code() ==
                                libvirt.VIR_ERR_OPERATION_FAILED):
                            self.log.warn("Failed to destroy VM '%s' "
                                          "gracefully", self.conf['vmId'])
                            time.sleep(30)
                            self._dom.destroy()
            except libvirt.libvirtError as e:
                if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                    self.log.warning("libvirt domain not found", exc_info=True)
                else:
                    self.log.warn("VM %s is not running", self.conf['vmId'])

            if not self.cif.mom:
                self.cif.ksmMonitor.adjust()
            self._cleanup()

            self.cif.irs.inappropriateDevices(self.id)

            hooks.after_vm_destroy(self._lastXMLDesc, self.conf)
            for dev in self._customDevices():
                hooks.after_device_destroy(dev._deviceXML, self.conf,
                                           dev.custom)

            self._released = True

        return {'status': doneCode}

    def deleteVm(self):
        """
        Clean VM from the system
        """
        try:
            del self.cif.vmContainer[self.conf['vmId']]
            self.log.debug("Total desktops after destroy of %s is %d",
                           self.conf['vmId'], len(self.cif.vmContainer))
        except Exception:
            self.log.error("Failed to delete VM %s", self.conf['vmId'],
                           exc_info=True)

    def destroy(self):
        self.log.debug('destroy Called')
        for dev in self._customDevices():
            hooks.before_device_destroy(dev._deviceXML, self.conf,
                                        dev.custom)

        hooks.before_vm_destroy(self._lastXMLDesc, self.conf)
        self.destroyed = True

        response = self.releaseVm()
        if response['status']['code']:
            return response
        # Clean VM from the system
        self.deleteVm()

        return {'status': doneCode}

    def _getBalloonInfo(self):
        for dev in self.conf['devices']:
            if dev['type'] == BALLOON_DEVICES and \
                    dev['specParams']['model'] != 'none':
                max_mem = int(self.conf.get('memSize')) * 1024
                min_mem = int(self.conf.get('memGuaranteedSize', '0')) * 1024
                target_mem = dev.get('target', max_mem)
                cur_mem = self._dom.info()[2]
                return {'balloon_max': str(max_mem),
                        'balloon_cur': str(cur_mem),
                        'balloon_min': str(min_mem),
                        'balloon_target': str(target_mem)}
        return {}

    def setBalloonTarget(self, target):

        def reportError(key='balloonErr', msg=None):
            self.log.error("Set new balloon target failed", exc_info=True)
            if msg is None:
                error = errCode[key]
            else:
                error = {'status': {'code': errCode[key]
                         ['status']['code'], 'message': msg}}
            return error

        if self._dom is None:
            return reportError()
        try:
            target = int(target)
            self._dom.setMemory(target)
        except ValueError:
            return reportError(msg='an integer is required for target')
        except libvirt.libvirtError as e:
            if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                return reportError(key='noVM')
            return reportError(msg=e.message)
        else:
            for dev in self.conf['devices']:
                if dev['type'] == BALLOON_DEVICES and \
                        dev['specParams']['model'] != 'none':
                    dev['target'] = target
            # persist the target value to make it consistent after recovery
            self.saveState()
            return {'status': doneCode}

    def _getUnderlyingDeviceAddress(self, devXml):
        """
        Obtain device's address from libvirt
        """
        address = {}
        adrXml = devXml.getElementsByTagName('address')[0]
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

        devsxml = _domParseStr(self._lastXMLDesc).childNodes[0]. \
            getElementsByTagName('devices')[0]

        for x in devsxml.childNodes:
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
        ctrlsxml = _domParseStr(self._lastXMLDesc).childNodes[0]. \
            getElementsByTagName('devices')[0]. \
            getElementsByTagName('controller')
        for x in ctrlsxml:
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
            for ctrl in self._devices[CONTROLLER_DEVICES]:
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
                if ((dev['type'] == CONTROLLER_DEVICES) and
                        (dev['device'] == device) and
                        (not 'index' in dev or dev['index'] == index) and
                        (not 'model' in dev or dev['model'] == model)):
                    dev['address'] = address
                    dev['alias'] = alias
                    knownDev = True
            # Add unknown controller device to vm's conf
            if not knownDev:
                self.conf['devices'].append({'type': CONTROLLER_DEVICES,
                                             'device': device,
                                             'address': address,
                                             'alias': alias})

    def _getUnderlyingBalloonDeviceInfo(self):
        """
        Obtain balloon device info from libvirt.
        """
        balloonxml = _domParseStr(self._lastXMLDesc).childNodes[0]. \
            getElementsByTagName('devices')[0]. \
            getElementsByTagName('memballoon')
        for x in balloonxml:
            # Ignore balloon devices without address.
            if not x.getElementsByTagName('address'):
                address = None
            else:
                address = self._getUnderlyingDeviceAddress(x)
            alias = x.getElementsByTagName('alias')[0].getAttribute('name')

            for dev in self._devices[BALLOON_DEVICES]:
                if address and not hasattr(dev, 'address'):
                    dev.address = address
                if not hasattr(dev, 'alias'):
                    dev.alias = alias

            for dev in self.conf['devices']:
                if dev['type'] == BALLOON_DEVICES:
                    if address and not dev.get('address'):
                        dev['address'] = address
                    if not dev.get('alias'):
                        dev['alias'] = alias

    def _getUnderlyingConsoleDeviceInfo(self):
        """
        Obtain the alias for the console device from libvirt
        """
        consolexml = _domParseStr(self._lastXMLDesc).childNodes[0].\
            getElementsByTagName('devices')[0].\
            getElementsByTagName('console')
        for x in consolexml:
            # All we care about is the alias
            alias = x.getElementsByTagName('alias')[0].getAttribute('name')
            for dev in self._devices[CONSOLE_DEVICES]:
                if not hasattr(dev, 'alias'):
                    dev.alias = alias

            for dev in self.conf['devices']:
                if dev['device'] == CONSOLE_DEVICES and \
                        not dev.get('alias'):
                    dev['alias'] = alias

    def _getUnderlyingSmartcardDeviceInfo(self):
        """
        Obtain smartcard device info from libvirt.
        """
        smartcardxml = _domParseStr(self._lastXMLDesc).childNodes[0].\
            getElementsByTagName('devices')[0].\
            getElementsByTagName('smartcard')
        for x in smartcardxml:
            if not x.getElementsByTagName('address'):
                continue

            address = self._getUnderlyingDeviceAddress(x)
            alias = x.getElementsByTagName('alias')[0].getAttribute('name')

            for dev in self._devices[SMARTCARD_DEVICES]:
                if not hasattr(dev, 'address'):
                    dev.address = address
                    dev.alias = alias

            for dev in self.conf['devices']:
                if dev['device'] == SMARTCARD_DEVICES and \
                        not dev.get('address'):
                    dev['address'] = address
                    dev['alias'] = alias

    def _getUnderlyingWatchdogDeviceInfo(self):
        """
        Obtain watchdog device info from libvirt.
        """
        watchdogxml = _domParseStr(self._lastXMLDesc).childNodes[0]. \
            getElementsByTagName('devices')[0]. \
            getElementsByTagName('watchdog')
        for x in watchdogxml:

            # PCI watchdog has "address" different from ISA watchdog
            if x.getElementsByTagName('address'):
                address = self._getUnderlyingDeviceAddress(x)
                alias = x.getElementsByTagName('alias')[0].getAttribute('name')

                for wd in self._devices[WATCHDOG_DEVICES]:
                    if not hasattr(wd, 'address') or not hasattr(wd, 'alias'):
                        wd.address = address
                        wd.alias = alias

                for dev in self.conf['devices']:
                    if ((dev['type'] == WATCHDOG_DEVICES) and
                            (not dev.get('address') or not dev.get('alias'))):
                        dev['address'] = address
                        dev['alias'] = alias

    def _getUnderlyingVideoDeviceInfo(self):
        """
        Obtain video devices info from libvirt.
        """
        videosxml = _domParseStr(self._lastXMLDesc).childNodes[0]. \
            getElementsByTagName('devices')[0].getElementsByTagName('video')
        for x in videosxml:
            alias = x.getElementsByTagName('alias')[0].getAttribute('name')
            # Get video card address
            address = self._getUnderlyingDeviceAddress(x)

            # FIXME. We have an identification problem here.
            # Video card device has not unique identifier, except the alias
            # (but backend not aware to device's aliases). So, for now
            # we can only assign the address according to devices order.
            for vc in self._devices[VIDEO_DEVICES]:
                if not hasattr(vc, 'address') or not hasattr(vc, 'alias'):
                    vc.alias = alias
                    vc.address = address
                    break
            # Update vm's conf with address
            for dev in self.conf['devices']:
                if ((dev['type'] == VIDEO_DEVICES) and
                        (not dev.get('address') or not dev.get('alias'))):
                    dev['address'] = address
                    dev['alias'] = alias
                    break

    def _getUnderlyingSoundDeviceInfo(self):
        """
        Obtain sound devices info from libvirt.
        """
        soundsxml = _domParseStr(self._lastXMLDesc).childNodes[0]. \
            getElementsByTagName('devices')[0].getElementsByTagName('sound')
        for x in soundsxml:
            alias = x.getElementsByTagName('alias')[0].getAttribute('name')
            # Get sound card address
            address = self._getUnderlyingDeviceAddress(x)

            # FIXME. We have an identification problem here.
            # Sound device has not unique identifier, except the alias
            # (but backend not aware to device's aliases). So, for now
            # we can only assign the address according to devices order.
            for sc in self._devices[SOUND_DEVICES]:
                if not hasattr(sc, 'address') or not hasattr(sc, 'alias'):
                    sc.alias = alias
                    sc.address = address
                    break
            # Update vm's conf with address
            for dev in self.conf['devices']:
                if ((dev['type'] == SOUND_DEVICES) and
                        (not dev.get('address') or not dev.get('alias'))):
                    dev['address'] = address
                    dev['alias'] = alias
                    break

    def _getUnderlyingDriveInfo(self):
        """
        Obtain block devices info from libvirt.
        """
        disksxml = _domParseStr(self._lastXMLDesc).childNodes[0]. \
            getElementsByTagName('devices')[0].getElementsByTagName('disk')
        # FIXME!  We need to gather as much info as possible from the libvirt.
        # In the future we can return this real data to management instead of
        # vm's conf
        for x in disksxml:
            sources = x.getElementsByTagName('source')
            if sources:
                devPath = (sources[0].getAttribute('file') or
                           sources[0].getAttribute('dev') or
                           sources[0].getAttribute('name'))
            else:
                devPath = ''

            target = x.getElementsByTagName('target')
            name = target[0].getAttribute('dev') if target else ''
            alias = x.getElementsByTagName('alias')[0].getAttribute('name')
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
                d = dev if not isinstance(dev, VmDevice) else dev.__dict__
                return dict((k, (deviceDef[k], d.get(k, None)))
                            for k in deviceDef.iterkeys())

            self.log.debug('Looking for drive with attributes %s', deviceDict)
            for d in self._devices[DISK_DEVICES]:
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
                if dev['type'] == DISK_DEVICES and dev['path'] == devPath:
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
                archIface = self._getDefaultDiskInterface()
                iface = archIface if address['type'] == 'drive' else 'pci'
                diskDev = {'type': DISK_DEVICES, 'device': devType,
                           'iface': iface, 'path': devPath, 'name': name,
                           'address': address, 'alias': alias,
                           'readonly': str(readonly)}
                if bootOrder:
                    diskDev['bootOrder'] = bootOrder
                self.log.debug('Found unknown drive: %s', diskDev)
                self.conf['devices'].append(diskDev)

    def _getUnderlyingDisplayPort(self):
        """
        Obtain display port info from libvirt.
        """
        graphics = _domParseStr(self._lastXMLDesc).childNodes[0]. \
            getElementsByTagName('graphics')[0]
        port = graphics.getAttribute('port')
        if port:
            self.conf['displayPort'] = port
        port = graphics.getAttribute('tlsPort')
        if port:
            self.conf['displaySecurePort'] = port

    def _getUnderlyingNetworkInterfaceInfo(self):
        """
        Obtain network interface info from libvirt.
        """
        # TODO use xpath instead of parseString (here and elsewhere)
        ifsxml = _domParseStr(self._lastXMLDesc).childNodes[0]. \
            getElementsByTagName('devices')[0]. \
            getElementsByTagName('interface')
        for x in ifsxml:
            devType = x.getAttribute('type')
            mac = x.getElementsByTagName('mac')[0].getAttribute('address')
            alias = x.getElementsByTagName('alias')[0].getAttribute('name')
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
            address = self._getUnderlyingDeviceAddress(x)
            for nic in self._devices[NIC_DEVICES]:
                if nic.macAddr.lower() == mac.lower():
                    nic.name = name
                    nic.alias = alias
                    nic.address = address
                    nic.linkActive = linkActive
            # Update vm's conf with address for known nic devices
            knownDev = False
            for dev in self.conf['devices']:
                if (dev['type'] == NIC_DEVICES and
                        dev['macAddr'].lower() == mac.lower()):
                    dev['address'] = address
                    dev['alias'] = alias
                    dev['name'] = name
                    dev['linkActive'] = linkActive
                    knownDev = True
            # Add unknown nic device to vm's conf
            if not knownDev:
                nicDev = {'type': NIC_DEVICES,
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

    def _setWriteWatermarks(self):
        """
        Define when to receive an event about high write to guest image
        Currently unavailable by libvirt.
        """
        pass

    def _onLibvirtLifecycleEvent(self, event, detail, opaque):
        self.log.debug('event %s detail %s opaque %s',
                       eventToString(event), detail, opaque)
        if event == libvirt.VIR_DOMAIN_EVENT_STOPPED:
            if (detail == libvirt.VIR_DOMAIN_EVENT_STOPPED_MIGRATED and
                    self.lastStatus == 'Migration Source'):
                hooks.after_vm_migrate_source(self._lastXMLDesc, self.conf)
                for dev in self._customDevices():
                    hooks.after_device_migrate_source(
                        dev._deviceXML, self.conf, dev.custom)
            elif (detail == libvirt.VIR_DOMAIN_EVENT_STOPPED_SAVED and
                    self.lastStatus == 'Saving State'):
                hooks.after_vm_hibernate(self._lastXMLDesc, self.conf)
            else:
                if detail == libvirt.VIR_DOMAIN_EVENT_STOPPED_SHUTDOWN:
                    self.user_destroy = True
                self._onQemuDeath()
        elif event == libvirt.VIR_DOMAIN_EVENT_SUSPENDED:
            self._guestCpuRunning = False
            if detail == libvirt.VIR_DOMAIN_EVENT_SUSPENDED_PAUSED:
                # Libvirt sometimes send the SUSPENDED/SUSPENDED_PAUSED event
                # after RESUMED/RESUMED_MIGRATED (when VM status is PAUSED
                # when migration completes, see qemuMigrationFinish function).
                # In this case self._dom is None because the function
                # _waitForIncomingMigrationFinish didn't update it yet.
                try:
                    domxml = self._dom.XMLDesc(0)
                except AttributeError:
                    pass
                else:
                    hooks.after_vm_pause(domxml, self.conf)

        elif event == libvirt.VIR_DOMAIN_EVENT_RESUMED:
            self._guestCpuRunning = True
            if detail == libvirt.VIR_DOMAIN_EVENT_RESUMED_UNPAUSED:
                # This is not a real solution however the safest way to handle
                # this for now. Ultimately we need to change the way how we are
                # creating self._dom.
                # The event handler delivers the domain instance in the
                # callback however we do not use it.
                try:
                    domxml = self._dom.XMLDesc(0)
                except AttributeError:
                    pass
                else:
                    hooks.after_vm_cont(domxml, self.conf)
            elif (detail == libvirt.VIR_DOMAIN_EVENT_RESUMED_MIGRATED and
                  self.lastStatus == 'Migration Destination'):
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
                else:
                    self.log.error("Alias not found for device type %s "
                                   "during migration at destination host" %
                                   devType)

        devices = _domParseStr(xml).childNodes[0]. \
            getElementsByTagName('devices')[0]

        for deviceXML in devices.childNodes:
            if deviceXML.nodeType != Node.ELEMENT_NODE:
                continue

            aliasElement = deviceXML.getElementsByTagName('alias')
            if aliasElement:
                alias = aliasElement[0].getAttribute('name')

                if alias in aliasToDevice:
                    aliasToDevice[alias]._deviceXML = deviceXML.toxml()

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
        srcDomXML = self.conf.pop('_srcDomXML').encode('utf-8')
        self._updateDevicesDomxmlCache(srcDomXML)

        for dev in self._customDevices():
            hooks.before_device_migrate_destination(
                dev._deviceXML, self.conf, dev.custom)

        hooks.before_vm_migrate_destination(srcDomXML, self.conf)
        return True


# A little unrelated hack to make xml.dom.minidom.Document.toprettyxml()
# not wrap Text node with whitespace.
# until http://bugs.python.org/issue4147 is accepted
def __hacked_writexml(self, writer, indent="", addindent="", newl=""):

    # copied from xml.dom.minidom.Element.writexml and hacked not to wrap Text
    # nodes with whitespace.

    # indent = current indentation
    # addindent = indentation to add to higher levels
    # newl = newline string
    writer.write(indent + "<" + self.tagName)

    attrs = self._get_attributes()
    a_names = attrs.keys()
    a_names.sort()

    for a_name in a_names:
        writer.write(" %s=\"" % a_name)
        #_write_data(writer, attrs[a_name].value) # replaced
        xml.dom.minidom._write_data(writer, attrs[a_name].value)
        writer.write("\"")
    if self.childNodes:
        # added special handling of Text nodes
        if (len(self.childNodes) == 1 and
                isinstance(self.childNodes[0], xml.dom.minidom.Text)):
            writer.write(">")
            self.childNodes[0].writexml(writer)
            writer.write("</%s>%s" % (self.tagName, newl))
        else:
            writer.write(">%s" % (newl))
            for node in self.childNodes:
                node.writexml(writer, indent + addindent, addindent, newl)
            writer.write("%s</%s>%s" % (indent, self.tagName, newl))
    else:
        writer.write("/>%s" % (newl))


xml.dom.minidom.Element.writexml = __hacked_writexml
