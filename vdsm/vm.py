#
# Copyright 2008-2011 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301  USA
#
# Refer to the README and COPYING files for full details of the license
#

import os, traceback
import time
import threading, logging
import constants
import utils
from define import NORMAL, ERROR, doneCode, errCode
from config import config
import kaxmlrpclib
import pickle
from logUtils import SimpleLogAdapter
from copy import deepcopy
import tempfile
import libvirt
import vdscli

"""
A module containing classes needed for VM communication.
"""

def isVdsmImage(drive):
    return all(k in drive.keys() for k in ('volumeID', 'domainID', 'imageID',
                'poolID'))


class Drive:
    def __init__(self, poolID, domainID, imageID, volumeID, path, truesize,
            apparentsize, blockDev, index='', unit='', serial='',
            format='raw', boot=None, propagateErrors='off', reqsize=0,
            alias='', **kwargs):
        self.poolID = poolID
        self.domainID = domainID
        self.imageID = imageID
        self.volumeID = volumeID
        self.path = path
        self.truesize = int(truesize)
        self.apparentsize = int(apparentsize)
        self.blockDev = blockDev
        self.needExtend = False
        self.reqsize = int(reqsize)
        self.iface = kwargs.get('if')
        self.index = index
        self.unit = unit
        self.serial = serial
        self.format = format
        self.propagateErrors = propagateErrors
        self.boot = boot
        self.alias = alias
        self.name = self._libvirtName()

    def _libvirtName(self):
        devname = 'vd' if self.iface == 'virtio' else 'hd'
        devindex = ''

        i = int(self.index)
        while i > 0:
            devindex = chr(ord('a') + (i % 26)) + devindex
            i /= 26

        return devname + (devindex or 'a')


class _MigrationError(RuntimeError): pass

class MigrationSourceThread(threading.Thread):
    """
    A thread that takes care of migration on the source vdsm.
    """
    _ongoingMigrations = threading.BoundedSemaphore(1)
    @classmethod
    def setMaxOutgoingMigrations(klass, n):
        """Set the initial value of the _ongoingMigrations semaphore.

        must not be called after any vm has been run."""
        klass._ongoingMigrations = threading.BoundedSemaphore(n)

    def __init__ (self, vm, dst='', dstparams='',
                  mode='remote', method='online', **kwargs):
        self.log = vm.log
        self._vm = vm
        self._dst = dst
        self._mode = mode
        self._method = method
        self._dstparams = dstparams
        self._machineParams = {}
        self._downtime = kwargs.get('downtime') or \
                            config.get('vars', 'migration_downtime')
        self.status = {'status': {'code': 0, 'message': 'Migration in process'}, 'progress': 0}
        threading.Thread.__init__(self)

    def getStat (self):
        """
        Get the status of the migration.
        """
        return self.status

    def _setupVdsConnection(self):
        if self._mode == 'file': return
        self.remoteHost = self._dst.split(':')[0]
        self.remotePort = self._vm.cif.serverPort
        try:
            self.remotePort = self._dst.split(':')[1]
        except:
            pass
        serverAddress = self.remoteHost + ':' + self.remotePort
        if config.getboolean('vars', 'ssl'):
            self.destServer = vdscli.connect(serverAddress, useSSL=True,
                    TransportClass=kaxmlrpclib.TcpkeepSafeTransport)
        else:
            self.destServer = kaxmlrpclib.Server('http://' + serverAddress)
        self.log.debug('Destination server is: ' + serverAddress)
        try:
            self.log.debug('Initiating connection with destination')
            status = self.destServer.getVmStats(self._vm.id)
            if not status['status']['code']:
                self.log.error("Machine already exists on the destination")
                self.status = errCode['exist']
        except:
            self.log.error(traceback.format_exc())
            self.status = errCode['noConPeer']

    def _setupRemoteMachineParams(self):
        self._machineParams.update(self._vm.status())
        if self._vm._guestCpuRunning:
            self._machineParams['afterMigrationStatus'] = 'Up'
        else:
            self._machineParams['afterMigrationStatus'] = 'Pause'
        self._machineParams['elapsedTimeOffset'] = \
                                time.time() - self._vm._startTime
        vmStats = self._vm.getStats()
        if 'username' in vmStats:
            self._machineParams['username'] = vmStats['username']
        if 'guestIPs' in vmStats:
            self._machineParams['guestIPs'] = vmStats['guestIPs']
        for k in ('_migrationParams', 'pid'):
            if k in self._machineParams:
                del self._machineParams[k]

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
                            ' unresponsive. Hiberanting without desktopLock.')
                    break
            self._vm.pause('Saving State')
        else:
            self.log.debug("migration Process begins")
            self._vm.lastStatus = 'Migration Source'

    def _recover(self, message):
        if not self.status['status']['code']:
            self.status = errCode['migrateErr']
        self.log.error(message)
        if self._mode != 'file':
            try:
                self.destServer.destroy(self._vm.id)
            except:
                self.log.error(traceback.format_exc())
        # if the guest was stopped before migration, we need to cont it
        if self._mode == 'file' or self._method != 'online':
            self._vm.cont()
        # either way, migration has finished
        self._vm.lastStatus = 'Up'

    def _finishSuccessfully(self):
        if self._mode != 'file':
            self._vm.setDownStatus(NORMAL, "Migration succeeded")
            self.status = {'status': {'code': 0, 'message': 'Migration done'}, 'progress': 100}
        else:
            # don't pickle transient params
            for ignoreParam in ('displayIp', 'display', 'pid'):
                if ignoreParam in self._machineParams:
                    del self._machineParams[ignoreParam]

            fname = self._vm.cif._prepareVolumePath(self._dstparams)
            try:
                with file(fname, "w") as f:
                    pickle.dump(self._machineParams, f)
            finally:
                self._vm.cif._teardownVolumePath(self._dstparams)

            self._vm.setDownStatus(NORMAL, "SaveState succeeded")
            self.status = {'status': {'code': 0, 'message': 'SaveState done'}, 'progress': 100}

    def run(self):
        try:
            mstate = ''
            self._setupVdsConnection()
            self._setupRemoteMachineParams()
            self._prepareGuest()
            self.status['progress'] = 10
            MigrationSourceThread._ongoingMigrations.acquire()
            try:
                self.log.debug("migration semaphore acquired")
                if not mstate:
                    self._vm.conf['_migrationParams'] = {'dst': self._dst,
                                'mode': self._mode, 'method': self._method,
                                'dstparams': self._dstparams}
                    self._vm.saveState()
                    self._startUnderlyingMigration()
                self._finishSuccessfully()
            finally:
                if '_migrationParams' in self._vm.conf:
                    del self._vm.conf['_migrationParams']
                MigrationSourceThread._ongoingMigrations.release()
        except Exception, e:
            self._recover(str(e))
            self.log.error(traceback.format_exc())


class VolumeError(RuntimeError):
    def __str__(self):
        return "Bad volume specification " + RuntimeError.__str__(self)

class DoubleDownError(RuntimeError): pass

VALID_STATES = ('Down', 'Migration Destination', 'Migration Source',
                'Paused', 'Powering down', 'RebootInProgress',
                'Restoring state', 'Saving State',
                'Up', 'WaitForLaunch')

class Vm(object):
    """
    Used for abstracting cummunication between various parts of the
    system and Qemu.

    Runs Qemu in a subprocess and communicates with it, and monitors
    its behaviour.
    """
    log = logging.getLogger("vm.Vm")
    _ongoingCreations = threading.BoundedSemaphore(1)
    MigrationSourceThreadClass = MigrationSourceThread
    def __init__(self, cif, params):
        """
        Initialize a new VM instance.

        :param cif: The client interface that creates this VM.
        :type cif: :class:`clientIF.clientIF`
        :param params: The VM parameters.
        :type params: dict
        """
        self.conf = {'pid': '0'}
        self.conf.update(params)
        self.cif = cif
        self.log = SimpleLogAdapter(self.log, {"vmId" : self.conf['vmId']})
        self.destroyed = False
        self._recoveryFile = constants.P_VDSM_RUN + str(
                                    self.conf['vmId']) + '.recovery'
        self.user_destroy = False
        self._monitorResponse = 0
        self.conf['clientIp'] = ''
        self.memCommitted = 0
        self._creationThread = threading.Thread(target=self._startUnderlyingVm)
        if 'migrationDest' in self.conf:
            self._lastStatus = 'Migration Destination'
        elif 'restoreState' in self.conf:
            self._lastStatus = 'Restoring state'
        else:
            self._lastStatus = 'WaitForLaunch'
        self._nice = ''
        self._migrationSourceThread = self.MigrationSourceThreadClass(self)
        self._kvmEnable = self.conf.get('kvmEnable', 'true')
        self._guestSocektFile = constants.P_VDSM_RUN + self.conf['vmId'] + \
                                '.guest.socket'
        self._drives = []
        self._incomingMigrationFinished = threading.Event()
        self.id = self.conf['vmId']
        self._volPrepareLock = threading.Lock()
        self._preparedDrives = {}
        self._initTimePauseCode = None
        self.guestAgent = None
        self._guestEvent = 'Powering up'
        self._guestEventTime = 0
        self._vmStats = None
        self._guestCpuRunning = False
        self._guestCpuLock = threading.Lock()
        self._startTime = time.time() - float(
                                self.conf.pop('elapsedTimeOffset', 0))
        self._cdromPreparedPath = ''
        self._floppyPreparedPath = ''
        self._volumesPrepared = False
        self._pathsPreparedEvent = threading.Event()
        self.saveState()

    def _get_lastStatus(self):
        SHOW_PAUSED_STATES = ('Powering down', 'RebootInProgress', 'Up')
        if not self._guestCpuRunning and self._lastStatus in SHOW_PAUSED_STATES:
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

    def run(self):
        self._creationThread.start()

    def memCommit(self):
        """
        Reserve the required memory for this VM.
        """
        self.memCommitted = 2**20 * (int(self.conf['memSize']) +
                                config.getint('vars', 'guest_ram_overhead'))

    def _startUnderlyingVm(self):
        self.log.debug("Start")
        try:
            self.memCommit()
            self._ongoingCreations.acquire()
            self.log.debug("_ongoingCreations acquired")
            try:
                self._run()
                if self.lastStatus != 'Down' and 'recover' not in self.conf:
                    self.cif.ksmMonitor.adjust()
            except Exception:
                if 'recover' not in self.conf:
                    raise
                else:
                    self.log.info("Skipping errors on recovery", exc_info=True)
            finally:
                self._ongoingCreations.release()
                self.log.debug("_ongoingCreations released")

            if ('migrationDest' in self.conf or 'restoreState' in self.conf
                                               ) and self.lastStatus != 'Down':
                self._waitForIncomingMigrationFinish()

            self.lastStatus = 'Up'
            if self._initTimePauseCode:
                self.conf['pauseCode'] = self._initTimePauseCode
                if self._initTimePauseCode == 'ENOSPC':
                    self.cont()
            else:
                try:
                    del self.conf['pauseCode']
                except:
                    pass

            if 'recover' in self.conf:
                del self.conf['recover']
            self.saveState()
        except Exception, e:
            if 'recover' in self.conf:
                self.log.info("Skipping errors on recovery", exc_info=True)
            else:
                self.log.error("The vm start process failed", exc_info=True)
                self.setDownStatus(ERROR, str(e))

    def _incomingMigrationPending(self):
        return 'migrationDest' in self.conf or 'restoreState' in self.conf

    def _prepareVolumePath(self, drive):
        volPath = ''
        if not self.destroyed:
            with self._volPrepareLock:
                if not self.destroyed:
                    volPath = self.cif._prepareVolumePath(drive)
                    self._preparedDrives[volPath] = drive

        return volPath

    def _initDriveList(self, drives):
        vindex = 0
        for d in drives:
            if d.get('if') == 'virtio' and not 'index' in d:
                d['index'] = str(vindex)
                vindex += 1

        for index, drive in zip(range(len(drives)), drives):
            if not drive.get('if'):
                drive['if'] = 'ide'
                drive['index'] = index
            if not drive.get('serial') and drive.get('imageID'):
                drive['serial'] = drive['imageID'][-20:]

            try:
                res = self.cif.irs.getVolumeSize(drive['domainID'],
                                     drive['poolID'], drive['imageID'],
                                     drive['volumeID'])
            except KeyError:
                self.log.info("Ignoring drive %s", str(drive))
            else:
                drive['truesize'] = res['truesize']
                drive['apparentsize'] = res['apparentsize']
                drive['blockDev'] = not self.cif.irs.getStorageDomainInfo(
                            drive['domainID'])['info']['type'] in ('NFS', 'LOCALFS')
                self._drives.append(Drive(**drive))

    def preparePaths(self):
        for drive in self.conf.get('drives', []):
            drive['path'] = self._prepareVolumePath(drive)
        # Now we got all needed resources
        self._volumesPrepared = True

        try:
            self._cdromPreparedPath = self._prepareVolumePath(
                                            self.conf.get('cdrom'))
        except VolumeError:
            self.log.warning(traceback.format_exc())
            if self.conf.get('cdrom'):
                del self.conf['cdrom']
        if 'floppy' in self.conf:
            self._floppyPreparedPath = self._prepareVolumePath(
                                            self.conf['floppy'])

    def releaseVm(self):
        """
        Stop VM and release all resources (implemented for libvirt VMs)
        """
        pass

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
        return base * (20 + load) / 20

    def saveState (self):
        if self.destroyed:
            return
        toSave = deepcopy(self.status())
        toSave['startTime'] = self._startTime
        if self.lastStatus != 'Down' and self._vmStats and self.guestAgent:
            toSave['username'] = self.guestAgent.guestInfo['username']
            toSave['guestIPs'] = self.guestAgent.guestInfo['guestIPs']
        else:
            toSave['username'] = ""
            toSave['guestIPs'] = ""
        if 'sysprepInf' in toSave:
            del toSave['sysprepInf']
            if 'floppy' in toSave: del toSave['floppy']
        for drive in toSave.get('drives', []):
            for d in self._drives:
                if drive.get('volumeID') == d.volumeID:
                    drive['truesize'] = str(d.truesize)
                    drive['apparentsize'] = str(d.apparentsize)

        with tempfile.NamedTemporaryFile(dir=constants.P_VDSM_RUN,
                                         delete=False) as f:
             pickle.dump(toSave, f)

        os.rename(f.name, self._recoveryFile)

    def onReboot (self, withRelaunch):
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
            if withRelaunch:
                self.cif.relaunch(self.status())
        except:
            self.log.error(traceback.format_exc())

    def onShutdown (self):
        self.log.debug('onShutdown() event')
        self.user_destroy = True

    def onConnect(self, clientIp=''):
        if clientIp:
            self.conf['clientIp'] = clientIp

    def onDisconnect(self, detail=None):
        self.guestAgent.desktopLock()
        self.conf['clientIp'] = ''

    def _rtcUpdate(self, timeOffset):
        self.log.debug('new rtc offset %s', timeOffset)
        self.conf['timeOffset'] = timeOffset

    def _onHighWrite(self, block_dev, offset):
        self.log.info('_onHighWrite: write above watermark on %s offset %s',
                      block_dev, offset)
        self._lvExtend(block_dev)

    def _lvExtend(self, block_dev, newsize=None):
        for d in self._drives:
            if not d.blockDev: continue
            if d.name != block_dev: continue
            if newsize is None:
                newsize = config.getint('irs',
                    'volume_utilization_chunk_mb') + (d.apparentsize + 2**20
                                                     - 1) / 2**20
            # TODO cap newsize by max volume size
            volDict = {'poolID': d.poolID, 'domainID': d.domainID,
                       'imageID': d.imageID, 'volumeID': d.volumeID}
            d.needExtend = True
            d.reqsize = newsize
            # sendExtendMsg expects size in bytes
            self.cif.irs.hsm.sendExtendMsg(d.poolID, volDict, newsize * 2**20,
                                           self._afterLvExtend)
            self.log.debug('%s/%s (%s): apparentsize %s req %s', d.domainID,
                           d.volumeID, d.name, d.apparentsize / constants.MEGAB,
                           newsize) #in MiB

            # store most recently requested size in conf, to be re-requested on
            # migration destination
            for drive in self.conf.get('drives', []):
                if drive.get('volumeID') == d.volumeID:
                    drive['reqsize'] = str(d.reqsize)

    def _refreshLV(self, domainID, poolID, imageID, volumeID):
        """ Stop vm before refreshing LV. """

        self._guestCpuLock.acquire()
        try:
            wasRunning = self._guestCpuRunning
            if wasRunning:
                self.pause(guestCpuLocked=True)
            self.cif.irs.refreshVolume(domainID, poolID, imageID, volumeID)
            if wasRunning:
                self.cont(guestCpuLocked=True)
        finally:
            self._guestCpuLock.release()

    def _afterLvExtend(self, drive):
        self.log.debug('_afterLvExtend %s' % drive)
        for d in self._drives:
            if (d.poolID, d.domainID,
                d.imageID, d.volumeID) != (
                                 drive['poolID'], drive['domainID'],
                                 drive['imageID'], drive['volumeID']):
                continue
            self._refreshLV(drive['domainID'], drive['poolID'],
                            drive['imageID'], drive['volumeID'])
            res = self.cif.irs.getVolumeSize(d.domainID, d.poolID, d.imageID,
                                             d.volumeID)
            if res['status']['code']:
                self.log.debug("Get size failed for %s %s %s %s. Skipping.",
                                d.domainID, d.poolID, d.imageID, d.volumeID)
                continue

            apparentsize = int(res['apparentsize'])
            truesize = int(res['truesize'])
            self.log.debug('_afterLvExtend apparentsize %s req size %s',
                            apparentsize / constants.MEGAB, d.reqsize) # MiB
            if apparentsize >= d.reqsize * constants.MEGAB: #in Bytes
                d.needExtend = False
                try:
                    self.cont()
                except libvirt.libvirtError:
                    self.log.debug("vm %s can't be resumed", self.id,
                                   exc_info=True)

            # TODO report failure to VDC
            d.truesize = truesize
            d.apparentsize = apparentsize
            self._setWriteWatermarks()
            return {'status': doneCode}

    def changeCD(self, drivespec):
        return self._changeBlockDev('cdrom', 'ide1-cd0', drivespec)

    def changeFloppy(self, drivespec):
        return self._changeBlockDev('floppy', 'floppy0', drivespec)

    def _migrationTimeout(self):
        timeout = config.getint('vars', 'migration_timeout')
        mem = int(self.conf['memSize'])
        if mem > 2048:
            timeout = timeout * mem / 2048
        return timeout

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
                del self.conf['pauseCode']
            except:
                pass
            return {'status': doneCode, 'output': ['']}
        finally:
            if not guestCpuLocked:
                self._guestCpuLock.release()

    def pause(self, afterState='Paused', guestCpuLocked=False):
        if not guestCpuLocked:
            self._acquireCpuLockWithTimeout()
        try:
            self.conf['pauseCode'] = 'NOERR'
            self._underlyingPause()
            if hasattr(self, 'updateGuestCpuRunning'):
                self.updateGuestCpuRunning()
            self._lastStatus = afterState
            return {'status': doneCode, 'output': ['']}
        finally:
            if not guestCpuLocked:
                self._guestCpuLock.release()

    def shutdown(self, timeout, message):
        try:
            now = time.time()
            if self.lastStatus == 'Down':
                return
            if self.guestAgent and self.guestAgent.isResponsive():
                self._guestEventTime = now
                self._guestEvent = 'Powering down'
                self.log.debug('guestAgent shutdown called')
                self.guestAgent.desktopShutdown(timeout, message)
                agent_timeout = int(timeout) + config.getint('vars', 'sys_shutdown_timeout')
                timer = threading.Timer(agent_timeout, self._timedShutdown)
                timer.start()
            elif self.conf['acpiEnable'].lower() == "true":
                self._guestEventTime = now
                self._guestEvent = 'Powering down'
                self._acpiShutdown()
            # No tools, no ACPI
            else:
                return {'status': {'code': errCode['exist']['status']['code'],
                        'message': 'VM without ACPI or active SolidICE tools. Try Forced Shutdown.'}}
        except:
            self.log.error(traceback.format_exc())
        return {'status': {'code': doneCode['code'],
                'message': 'Machine shut down'}}

    def _timedShutdown(self):
        self.log.debug('_timedShutdown Called')
        try:
            if self.lastStatus == 'Down':
                return
            if self.conf['acpiEnable'].lower() != "true":
                self.destroy()
            else:
                self._acpiShutdown()
        except:
            self.log.error(traceback.format_exc())

    def _teardownVolumePath(self, drive):
        try:
            if self._preparedDrives.has_key(drive):
                resCode = self.cif._teardownVolumePath(self._preparedDrives[drive])
                # If teardown failed leave drive in _preparedDrives for next try.
                if not resCode:
                    del self._preparedDrives[drive]
            else:
                self.log.warn("Volume %s missing from preparedDrives", str(drive))
        except:
            self.log.error(traceback.format_exc())

    def _cleanup(self):
        with self._volPrepareLock:
            for drive in self._preparedDrives.keys():
                self.log.debug("Drive %s cleanup" % drive)
                self._teardownVolumePath(drive)

        if self.conf.get('volatileFloppy'):
            try:
                self.log.debug("Floppy %s cleanup" % self.conf['floppy'])
                utils.rmFile(self.conf['floppy'])
            except:
                pass
        try:
            self.guestAgent.stop()
        except:
            pass
        utils.rmFile(self._guestSocektFile)
        utils.rmFile(self._recoveryFile)

    def setDownStatus (self, code, reason):
        try:
            self.lastStatus = 'Down'
            self.conf['exitCode'] = code
            if 'restoreState' in self.conf:
                self.conf['exitMessage'] = "Wake up from hibernation failed"
            else:
                self.conf['exitMessage'] = reason
            self.log.debug("Changed state to Down: " + reason)
        except DoubleDownError:
            pass
        try:
            self.guestAgent.stop()
        except:
            pass
        try:
            self._vmStats.stop()
        except:
            pass
        self.saveState()

    def status(self):
        # used by clientIF.list
        self.conf['status'] = self.lastStatus
        return self.conf

    def getStats(self):
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

        # used by clientIF.getVmStats
        if self.lastStatus == 'Down':
            stats = {}
            stats['exitCode'] = self.conf['exitCode']
            stats['status'] = self.lastStatus
            stats['exitMessage'] = self.conf['exitMessage']
            if 'timeOffset' in self.conf:
                stats['timeOffset'] = self.conf['timeOffset']
            return stats

        stats = {'displayPort': self.conf['displayPort'],
                 'displaySecurePort': self.conf['displaySecurePort'],
                 'displayType': self.conf['display'],
                 'displayIp': self.conf['displayIp'],
                 'pid': self.conf['pid'],
                 'vmType': self.conf['vmType'],
                 'kvmEnable': self._kvmEnable,
                 'network': {}, 'disks': {},
                 'monitorResponse': str(self._monitorResponse),
                 'nice': self._nice,
                 'elapsedTime' : str(int(time.time() - self._startTime)),
                 }
        if 'cdrom' in self.conf:
            stats['cdrom'] = self.conf['cdrom']
        if 'boot' in self.conf:
            stats['boot'] = self.conf['boot']

        decStats = {}
        try:
            if self._vmStats:
                decStats = self._vmStats.get()
                if (not self._migrationSourceThread.isAlive()
                    and decStats['statsAge'] > config.getint('vars',
                                                       'vm_command_timeout')):
                    stats['monitorResponse'] = '-1'
        except:
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
                        stats['disks'][var][value] = utils.convertToStr(decStats[var][value])
                except:
                    self.log.error("Error setting vm disk stats", exc_info=True)


        if self.lastStatus in ('Saving State', 'Restoring state', 'Migration Source', 'Migration Destination', 'Paused'):
            stats['status'] = self.lastStatus
        elif self._migrationSourceThread.isAlive():
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
        except:
            return stats
        memUsage = 0
        realMemUsage = int(stats['memUsage'])
        if realMemUsage != 0:
            memUsage = 100 - float(realMemUsage) / int(self.conf['memSize']) * 100
        stats['memUsage'] = utils.convertToStr(int(memUsage))
        return stats

    def migrate(self, params):
        self._acquireCpuLockWithTimeout()
        try:
            if self._migrationSourceThread.isAlive():
                self.log.warning('vm already migrating')
                return errCode['exist']
            # while we were blocking, another migrationSourceThread could have
            # taken self Down
            if self._lastStatus == 'Down':
                return errCode['noVM']
            self._migrationSourceThread = self.MigrationSourceThreadClass(self,
                                                                     **params)
            self._migrationSourceThread.start()
            check = self._migrationSourceThread.getStat()
            if check['status']['code']:
                return check
            return {'status': {'code': 0,
                               'message': 'Migration process starting'}}
        finally:
            self._guestCpuLock.release()

    def migrateStatus(self):
        return self._migrationSourceThread.getStat()

