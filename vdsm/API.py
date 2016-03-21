#
# Copyright (C) 2012 Adam Litke, IBM Corporation
# Copyright (C) 2012-2014 Red Hat, Inc.
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
# pylint: disable=R0904

import os
import time
import logging
import errno

from vdsm.network.errors import ConfigNetworkError

from vdsm import commands
from vdsm import cpuarch
from vdsm import utils
from clientIF import clientIF
from vdsm import constants
from vdsm import exception
from vdsm import hooks
from vdsm import hostdev
from vdsm import response
from vdsm import supervdsm
from vdsm import jobs
from vdsm import v2v
from vdsm.virt import hoststats
from vdsm.virt import vmstatus
from vdsm.virt import sampling
from vdsm.virt import secret
import storage.misc
import storage.clusterlock
import storage.volume
import storage.sd
import storage.image
from virt.vmdevices import graphics
from virt.vmdevices import hwclass
from vdsm.compat import pickle
from vdsm.define import doneCode, errCode, Kbytes, Mbytes
import caps
from vdsm.config import config


haClient = None  # Define here to work around pyflakes issue #13
try:
    import ovirt_hosted_engine_ha.client.client as haClient
except ImportError:
    pass

# default message for system shutdown, will be displayed in guest
USER_SHUTDOWN_MESSAGE = 'System going down'


def updateTimestamp():
    # The setup API uses this log file to determine if this host is still
    # accessible.  We use a file (rather than an event) because setup is
    # performed by a separate, root process.
    utils.touchFile(constants.P_VDSM_CLIENT_LOG)


class APIBase(object):
    ctorArgs = []

    def __init__(self):
        self._cif = clientIF.getInstance()
        self._irs = self._cif.irs
        self.log = self._cif.log


class ConnectionRefs(APIBase):
    ctorArgs = []

    def __init__(self):
        APIBase.__init__(self)

    def acquire(self, conRefArgs):
        return self._irs.storageServer_ConnectionRefs_acquire(conRefArgs)

    def release(self, refIDs):
        return self._irs.storageServer_ConnectionRefs_release(refIDs)

    def statuses(self):
        return self._irs.storageServer_ConnectionRefs_statuses()


class Task(APIBase):
    ctorArgs = ['taskID']

    def __init__(self, UUID):
        APIBase.__init__(self)
        self._UUID = UUID

    def clear(self):
        return self._irs.clearTask(self._UUID)

    def getInfo(self):
        return self._irs.getTaskInfo(self._UUID)

    def getStatus(self):
        return self._irs.getTaskStatus(self._UUID)

    def revert(self):
        return self._irs.revertTask(self._UUID)

    def stop(self):
        return self._irs.stopTask(self._UUID)


class VM(APIBase):
    BLANK_UUID = '00000000-0000-0000-0000-000000000000'
    ctorArgs = ['vmID']

    def __init__(self, UUID):
        APIBase.__init__(self)
        self._UUID = UUID

    def changeCD(self, driveSpec):
        """
        Change the CD in the specified VM.

        :param vmId: uuid of specific VM.
        :type vmId: UUID
        :param driveSpec: specification of the new CD image. Either an
                image path or a `storage`-centric quartet.
        """
        v = self._cif.vmContainer.get(self._UUID)
        if not v:
            return errCode['noVM']
        return v.changeCD(driveSpec)

    def changeFloppy(self, driveSpec):
        """
        Change the floppy disk in the specified VM.

        :param vmId: uuid of specific VM.
        :type vmId: UUID
        :param driveSpec: specification of the new CD image. Either an
                image path or a `storage`-centric quartet.
        """
        v = self._cif.vmContainer.get(self._UUID)
        if not v:
            return errCode['noVM']
        return v.changeFloppy(driveSpec)

    def cont(self):
        v = self._cif.vmContainer.get(self._UUID)
        if not v:
            return errCode['noVM']
        return v.cont()

    def create(self, vmParams):
        """
        Start up a virtual machine.

        :param vmParams: required and optional VM parameters.
        :type vmParams: dict
        """
        vmParams['vmId'] = self._UUID
        try:
            if vmParams.get('vmId') in self._cif.vmContainer:
                self.log.warning('vm %s already exists' % vmParams['vmId'])
                return errCode['exist']

            if 'hiberVolHandle' in vmParams:
                vmParams['restoreState'], paramFilespec = \
                    self._getHibernationPaths(vmParams.pop('hiberVolHandle'))
                try:   # restore saved vm parameters
                    # NOTE: pickled params override command-line params. this
                    # might cause problems if an upgrade took place since the
                    # parmas were stored.
                    fname = self._cif.prepareVolumePath(paramFilespec)
                    try:
                        with open(fname) as f:
                            pickledMachineParams = pickle.load(f)

                        if type(pickledMachineParams) == dict:
                            self.log.debug('loaded pickledMachineParams ' +
                                           str(pickledMachineParams))
                            self.log.debug('former conf ' + str(vmParams))
                            vmParams.update(pickledMachineParams)
                    finally:
                        self._cif.teardownVolumePath(paramFilespec)
                except:
                    self.log.error("Error restoring VM parameters",
                                   exc_info=True)

            requiredParams = ['vmId', 'memSize']
            for param in requiredParams:
                if param not in vmParams:
                    self.log.error('Missing required parameter %s' % (param))
                    return {'status': {'code': errCode['MissParam']
                                                      ['status']['code'],
                                       'message': 'Missing required '
                                       'parameter %s' % (param)}}
            try:
                storage.misc.validateUUID(vmParams['vmId'])
            except:
                return {'status': {'code': errCode['MissParam']
                                                  ['status']['code'],
                                   'message': 'vmId must be a valid UUID'}}
            if vmParams['memSize'] == 0:
                return {'status': {'code': errCode['MissParam']
                                                  ['status']['code'],
                                   'message': 'Must specify nonzero memSize'}}

            if vmParams.get('boot') == 'c' and 'hda' not in vmParams \
                    and not vmParams.get('drives'):
                return {'status': {'code': errCode['MissParam']
                                                  ['status']['code'],
                                   'message': 'missing boot disk'}}

            if 'vmType' not in vmParams:
                vmParams['vmType'] = 'kvm'
            elif vmParams['vmType'] == 'kvm':
                if 'kvmEnable' not in vmParams:
                    vmParams['kvmEnable'] = 'true'

            if 'sysprepInf' in vmParams:
                if not vmParams.get('floppy'):
                    vmParams['floppy'] = '%s%s.vfd' % (constants.P_VDSM_RUN,
                                                       vmParams['vmId'])
                vmParams['volatileFloppy'] = True

            if 'sysprepInf' in vmParams:
                if not self._createSysprepFloppyFromInf(vmParams['sysprepInf'],
                                                        vmParams['floppy']):
                    return {'status': {'code': errCode['createErr']
                                                      ['status']['code'],
                                       'message': 'Failed to create '
                                                  'sysprep floppy image. '
                                                  'No space on /tmp?'}}

            if not graphics.isSupportedDisplayType(vmParams):
                return {'status': {'code': errCode['createErr']
                                                  ['status']['code'],
                                   'message': 'Unknown display type %s' %
                                              vmParams.get('display')}}
            if 'nicModel' not in vmParams:
                vmParams['nicModel'] = config.get('vars', 'nic_model')
            return self._cif.createVm(vmParams)

        except OSError as e:
            self.log.debug("OS Error creating VM", exc_info=True)
            return {'status': {'code': errCode['createErr']['status']['code'],
                               'message': 'Failed to create VM. '
                                          'No space on /tmp? ' + e.message}}
        except:
            self.log.debug("Error creating VM", exc_info=True)
            return errCode['unexpected']

    def desktopLock(self):
        """
        Lock user session in guest operating system using guest agent.
        """
        try:
            v = self._cif.vmContainer[self._UUID]
        except KeyError:
            return errCode['noVM']
        v.guestAgent.desktopLock()
        if v.guestAgent.isResponsive():
            return {'status': doneCode}
        else:
            return errCode['nonresp']

    def desktopLogin(self, domain, username, password):
        """
        Log into guest operating system using guest agent.
        """
        try:
            v = self._cif.vmContainer[self._UUID]
        except KeyError:
            return errCode['noVM']
        v.guestAgent.desktopLogin(domain, username, password)
        if v.guestAgent.isResponsive():
            return {'status': doneCode}
        else:
            return errCode['nonresp']

    def desktopLogoff(self, force):
        """
        Log out of guest operating system using guest agent.
        """
        try:
            v = self._cif.vmContainer[self._UUID]
        except KeyError:
            return errCode['noVM']
        v.guestAgent.desktopLogoff(force)
        if v.guestAgent.isResponsive():
            return {'status': doneCode}
        else:
            return errCode['nonresp']

    def desktopSendHcCommand(self, message):
        """
        Send a command to the guest agent (depricated).
        """
        try:
            v = self._cif.vmContainer[self._UUID]
        except KeyError:
            return errCode['noVM']
        v.guestAgent.sendHcCmdToDesktop(message)
        if v.guestAgent.isResponsive():
            return {'status': doneCode}
        else:
            return errCode['nonresp']

    def destroy(self):
        """
        Destroy the specified VM.
        """
        self.log.debug('About to destroy VM %s', self._UUID)

        with self._cif.vmContainerLock:
            v = self._cif.vmContainer.get(self._UUID)
            if not v:
                return errCode['noVM']
            res = v.destroy()
            status = utils.picklecopy(res)
            if status['status']['code'] == 0:
                status['status']['message'] = "Machine destroyed"
            return status

    def getMigrationStatus(self):
        """
        Report status of a currently outgoing migration.
        """
        try:
            v = self._cif.vmContainer[self._UUID]
        except KeyError:
            return errCode['noVM']
        return {'status': doneCode, 'migrationStats': v.migrateStatus()}

    def getStats(self):
        """
        Obtain statistics of the specified VM
        """
        return self._getStats()

    def _getStats(self, runHooks=True):
        v = self._cif.vmContainer.get(self._UUID)
        if not v:
            return errCode['noVM']

        if runHooks:
            try:
                hooks.before_get_vm_stats()
            except exception.HookError as e:
                return response.error('hookError',
                                      'Hook error: ' + str(e))

        stats = v.getStats().copy()
        if runHooks:
            stats = hooks.after_get_vm_stats([stats])[0]
        return {'status': doneCode, 'statsList': [stats]}

    def hibernate(self, hibernationVolHandle):
        """
        Hibernate a VM.

        :param hiberVolHandle: opaque string, indicating the location of
                               hibernation images.
        """
        params = {'vmId': self._UUID, 'mode': 'file',
                  'hiberVolHandle': hibernationVolHandle}
        response = self.migrate(params)
        if not response['status']['code']:
            response['status']['message'] = 'Hibernation process starting'
        return response

    def updateDevice(self, params):
        if 'deviceType' not in params:
            self.log.error('Missing a required parameters: deviceType')
            return {'status': {'code': errCode['MissParam']['status']['code'],
                               'message': 'Missing one of required '
                                          'parameters: deviceType'}}
        try:
            v = self._cif.vmContainer[self._UUID]
        except KeyError:
            self.log.warning("vm %s doesn't exist", self._UUID)
            return errCode['noVM']

        if params['deviceType'] == hwclass.NIC:
            if 'alias' not in params:
                self.log.error('Missing the required alias parameters.')
                return {'status':
                        {'code': errCode['MissParam']['status']['code'],
                         'message': 'Missing the required alias parameter'}}
        return v.updateDevice(params)

    def hotplugNic(self, params):
        try:
            utils.validateMinimalKeySet(params, ('vmId', 'nic'))
        except ValueError:
            self.log.error('Missing one of required parameters: vmId, nic')
            return {'status': {'code': errCode['MissParam']['status']['code'],
                               'message': 'Missing one of required '
                                          'parameters: vmId, nic'}}
        try:
            curVm = self._cif.vmContainer[self._UUID]
        except KeyError:
            self.log.warning("vm %s doesn't exist", self._UUID)
            return errCode['noVM']

        return curVm.hotplugNic(params)

    def hotunplugNic(self, params):
        try:
            utils.validateMinimalKeySet(params, ('vmId', 'nic'))
        except ValueError:
            self.log.error('Missing one of required parameters: vmId, nic')
            return {'status': {'code': errCode['MissParam']['status']['code'],
                               'message': 'Missing one of required '
                                          'parameters: vmId, nic'}}
        try:
            curVm = self._cif.vmContainer[self._UUID]
        except KeyError:
            self.log.warning("vm %s doesn't exist", self._UUID)
            return errCode['noVM']

        return curVm.hotunplugNic(params)

    def hotplugDisk(self, params):
        try:
            utils.validateMinimalKeySet(params, ('vmId', 'drive'))
        except ValueError:
            self.log.error('Missing one of required parameters: vmId, drive')
            return {'status': {'code': errCode['MissParam']['status']['code'],
                               'message': 'Missing one of required '
                                          'parameters: vmId, drive'}}
        try:
            curVm = self._cif.vmContainer[self._UUID]
        except KeyError:
            self.log.warning("vm %s doesn't exist", self._UUID)
            return errCode['noVM']

        return curVm.hotplugDisk(params)

    def hotunplugDisk(self, params):
        try:
            utils.validateMinimalKeySet(params, ('vmId', 'drive'))
        except ValueError:
            self.log.error('Missing one of required parameters: vmId, drive')
            return {'status': {'code': errCode['MissParam']['status']['code'],
                               'message': 'Missing one of required '
                                          'parameters: vmId, drive'}}
        try:
            curVm = self._cif.vmContainer[self._UUID]
        except KeyError:
            self.log.warning("vm %s doesn't exist", self._UUID)
            return errCode['noVM']

        return curVm.hotunplugDisk(params)

    def hotplugMemory(self, params):
        try:
            utils.validateMinimalKeySet(params, ('vmId', 'memory'))
        except ValueError:
            self.log.error('Missing one of required parameters: vmId, memory')
            return {'status': {'code': errCode['MissParam']['status']['code'],
                               'message': 'Missing one of required '
                                          'parameters: vmId, memory'}}

        try:
            curVm = self._cif.vmContainer[self._UUID]
        except KeyError:
            self.log.warning("vm %s doesn't exist", self._UUID)
            return errCode['noVM']

        return curVm.hotplugMemory(params)

    def setNumberOfCpus(self, numberOfCpus):

        if self._UUID is None or numberOfCpus is None:
            self.log.error('Missing one of required parameters: \
            vmId: (%s), numberOfCpus: (%s)', self._UUID, numberOfCpus)
            return {'status': {'code': errCode['MissParam']['status']['code'],
                               'message': 'Missing one of required '
                                          'parameters: vmId, numberOfCpus'}}
        try:
            curVm = self._cif.vmContainer[self._UUID]
        except KeyError:
            self.log.warning("vm %s doesn't exist", self._UUID)
            return errCode['noVM']

        return curVm.setNumberOfCpus(int(numberOfCpus))

    def updateVmPolicy(self, params):
        try:
            curVm = self._cif.vmContainer[self._UUID]
        except KeyError:
            self.log.warning("vm %s doesn't exist", self._UUID)
            return errCode['noVM']

        # Remove the vmId parameter from params we do not need it anymore
        del params["vmId"]

        return curVm.updateVmPolicy(params)

    def migrate(self, params):
        """
        Migrate a VM to a remote host.

        :param params: a dictionary containing:
            *dst* - remote host or hibernation image filename
            *dstparams* - hibernation image filename for vdsm parameters
            *mode* - ``remote``/``file``
            *method* - ``online``
            *downtime* - allowed down time during online migration
            *consoleAddress* - remote host graphics address
            *dstqemu* - remote host address dedicated for migration
            *compressed* - compress repeated pages during live migration
            *autoConverge* - force convergence during live migration
            *maxBandwidth* - max bandwidth used by this specific migration
            *convergenceSchedule* - actions to perform when stalling
        """
        params['vmId'] = self._UUID
        self.log.debug(params)
        try:
            v = self._cif.vmContainer[self._UUID]
        except KeyError:
            return errCode['noVM']

        vmParams = v.status()
        if vmParams['status'] in (vmstatus.WAIT_FOR_LAUNCH, vmstatus.DOWN):
            return errCode['noVM']
        if params.get('mode') == 'file':
            if 'dst' not in params:
                params['dst'], params['dstparams'] = \
                    self._getHibernationPaths(params['hiberVolHandle'])
        else:
            params['mode'] = 'remote'
        return v.migrate(params)

    def migrateChangeParams(self, params):
        """
        Change parameters of an ongoing migration

        :param params: a dictionary containing:
            *maxBandwidth* - new max bandwidth
        """
        try:
            v = self._cif.vmContainer[self._UUID]
        except KeyError:
            return response.error('noVM')
        return v.migrateChangeParams(params)

    def migrateCancel(self):
        """
        Cancel a currently outgoing migration process.
        """
        try:
            v = self._cif.vmContainer[self._UUID]
        except KeyError:
            return errCode['noVM']
        return v.migrateCancel()

    def migrationCreate(self, params):
        """
        Start a migration-destination VM.

        :param params: parameters of new VM, to be passed to
            *:meth:* - `~clientIF.create`.
        :type params: dict
        """
        self.log.debug('Migration create')

        params['vmId'] = self._UUID
        result = self.create(params)
        if result['status']['code']:
            self.log.debug('Migration create - Failed')
            return result

        v = self._cif.vmContainer.get(self._UUID)

        try:
            if not v.waitForMigrationDestinationPrepare():
                return errCode['createErr']
        except exception.HookError as e:
            self.log.debug('Destination VM creation failed due to hook' +
                           ' error:' + str(e))
            return response.error('hookError', 'Destination hook failed: ' +
                                  str(e))
        self.log.debug('Destination VM creation succeeded')
        return {'status': doneCode, 'migrationPort': 0,
                'params': result['vmList']}

    def diskReplicateStart(self, srcDisk, dstDisk):
        v = self._cif.vmContainer.get(self._UUID)
        if not v:
            return errCode['noVM']
        return v.diskReplicateStart(srcDisk, dstDisk)

    def diskReplicateFinish(self, srcDisk, dstDisk):
        v = self._cif.vmContainer.get(self._UUID)
        if not v:
            return errCode['noVM']
        return v.diskReplicateFinish(srcDisk, dstDisk)

    def diskSizeExtend(self, driveSpecs, newSize):
        if self._UUID == VM.BLANK_UUID:
            try:
                volume = Volume(
                    driveSpecs['volumeID'], driveSpecs['poolID'],
                    driveSpecs['domainID'], driveSpecs['imageID'])
            except KeyError:
                return errCode['imageErr']
            return volume.updateSize(newSize)
        else:
            v = self._cif.vmContainer.get(self._UUID)
            if not v:
                return errCode['noVM']
            return v.diskSizeExtend(driveSpecs, newSize)

    def pause(self):
        v = self._cif.vmContainer.get(self._UUID)
        if not v:
            return errCode['noVM']
        return v.pause()

    def reset(self):
        """
        Press the virtual reset button for the specified VM.
        """
        return errCode['noimpl']

    def setTicket(self, password, ttl, existingConnAction, params):
        """
        Set the ticket (password) to be used to connect to a VM display

        :param vmId: specify the VM whos ticket is to be changed.
        :param password: new password
        :type password: string
        :param ttl: ticket lifetime (seconds)
        :param existingConnAction: what to do with a currently-connected
                client (SPICE only):
                ``disconnect`` - disconnect old client when a new client
                                 connects.
                ``keep``       - allow existing client to remain
                                 connected.
                ``fail``       - abort command without disconnecting
                                 the current client.
        :param additional parameters in dict format
        """
        try:
            v = self._cif.vmContainer[self._UUID]
        except KeyError:
            return errCode['noVM']
        return v.setTicket(password, ttl, existingConnAction, params)

    def shutdown(self, delay=None, message=None, reboot=False, timeout=None,
                 force=False):
        """
        Shut a VM down politely.

        :param message: message to be shown to guest user before shutting down
                        his machine.
        :param delay: grace period (seconds) to let guest user close his
                      applications.
        :param reboot: True if reboot is desired, False for shutdown
        :param timeout: number of seconds to wait before trying next
                        shutdown/reboot method
        :param force: True if shutdown/reboot desired by any means necessary
                      (forceful reboot/shutdown if all graceful methods fail)
        """
        try:
            v = self._cif.vmContainer[self._UUID]
        except KeyError:
            return errCode['noVM']
        if not delay:
            delay = config.get('vars', 'user_shutdown_timeout')
        if not message:
            message = USER_SHUTDOWN_MESSAGE
        if not timeout:
            timeout = config.getint('vars', 'sys_shutdown_timeout')
        return v.shutdown(delay, message, reboot, timeout, force)

    def _createSysprepFloppyFromInf(self, infFileBinary, floppyImage):
        try:
            rc, out, err = commands.execCmd([constants.EXT_MK_SYSPREP_FLOPPY,
                                             floppyImage],
                                            sudo=True,
                                            data=infFileBinary.data)
            if rc:
                return False
            else:
                return True
        except:
            self.log.error("Error creating sysprep floppy", exc_info=True)
            return False

    def _getHibernationPaths(self, hiberVolHandle):
        """
        Break *hiberVolHandle* into the "quartets" of hibernation images.
        """
        domainID, poolID, stateImageID, stateVolumeID, \
            paramImageID, paramVolumeID = hiberVolHandle.split(',')

        return dict(domainID=domainID, poolID=poolID, imageID=stateImageID,
                    volumeID=stateVolumeID, device='disk'), \
            dict(domainID=domainID, poolID=poolID,
                 imageID=paramImageID, volumeID=paramVolumeID,
                 device='disk')

    def freeze(self):
        v = self._cif.vmContainer.get(self._UUID)
        if not v:
            return errCode['noVM']
        return v.freeze()

    def thaw(self):
        v = self._cif.vmContainer.get(self._UUID)
        if not v:
            return errCode['noVM']
        return v.thaw()

    def snapshot(self, snapDrives, snapMemory=None, frozen=False):
        v = self._cif.vmContainer.get(self._UUID)
        if not v:
            return errCode['noVM']
        memoryParams = {}
        if snapMemory:
            memoryParams['dst'], memoryParams['dstparams'] = \
                self._getHibernationPaths(snapMemory)
        return v.snapshot(snapDrives, memoryParams, frozen=frozen)

    def setBalloonTarget(self, target):
        v = self._cif.vmContainer.get(self._UUID)
        if not v:
            return errCode['noVM']
        return v.setBalloonTarget(target)

    def setCpuTuneQuota(self, quota):
        v = self._cif.vmContainer.get(self._UUID)
        if not v:
            return errCode['noVM']
        return v.setCpuTuneQuota(quota)

    def setIoTune(self, tunables):
        v = self._cif.vmContainer.get(self._UUID)
        if not v:
            return errCode['noVM']
        return v.setIoTune(tunables)

    def getIoTunePolicy(self):
        v = self._cif.vmContainer.get(self._UUID)
        if not v:
            return errCode['noVM']
        return v.getIoTunePolicy()

    def setCpuTunePeriod(self, period):
        v = self._cif.vmContainer.get(self._UUID)
        if not v:
            return errCode['noVM']
        return v.setCpuTunePeriod(period)

    def getDiskAlignment(self, disk):
        if self._UUID != VM.BLANK_UUID:
            return errCode['noimpl']
        return self._cif.getDiskAlignment(disk)

    def merge(self, drive, baseVolUUID, topVolUUID, bandwidth=0, jobUUID=None):
        v = self._cif.vmContainer.get(self._UUID)
        if not v:
            return errCode['noVM']
        return v.merge(drive, baseVolUUID, topVolUUID, bandwidth, jobUUID)


class Volume(APIBase):
    ctorArgs = ['volumeID', 'storagepoolID', 'storagedomainID', 'imageID']

    class Types:
        UNKNOWN = storage.volume.UNKNOWN_VOL
        PREALLOCATED = storage.volume.PREALLOCATED_VOL
        SPARSE = storage.volume.SPARSE_VOL

    class Formats:
        UNKNOWN = storage.volume.UNKNOWN_FORMAT
        COW = storage.volume.COW_FORMAT
        RAW = storage.volume.RAW_FORMAT

    class Roles:
        SHARED = storage.volume.SHARED_VOL
        LEAF = storage.volume.LEAF_VOL

    BLANK_UUID = storage.volume.BLANK_UUID

    def __init__(self, UUID, spUUID, sdUUID, imgUUID):
        APIBase.__init__(self)
        self._UUID = UUID
        self._spUUID = spUUID
        self._sdUUID = sdUUID
        self._imgUUID = imgUUID

    def copy(self, dstSdUUID, dstImgUUID, dstVolUUID, desc, volType,
             volFormat, preallocate, postZero, force):
        vmUUID = ''   # vmUUID is never used
        return self._irs.copyImage(self._sdUUID, self._spUUID, vmUUID,
                                   self._imgUUID, self._UUID, dstImgUUID,
                                   dstVolUUID, desc, dstSdUUID, volType,
                                   volFormat, preallocate, postZero, force)

    def create(self, size, volFormat, preallocate, diskType, desc,
               srcImgUUID, srcVolUUID, initialSize=None):
        return self._irs.createVolume(self._sdUUID, self._spUUID,
                                      self._imgUUID, size, volFormat,
                                      preallocate, diskType, self._UUID, desc,
                                      srcImgUUID, srcVolUUID,
                                      initialSize=initialSize)

    def delete(self, postZero, force):
        return self._irs.deleteVolume(self._sdUUID, self._spUUID,
                                      self._imgUUID, [self._UUID], postZero,
                                      force)

    def extendSize(self, newSize):
        return self._irs.extendVolumeSize(
            self._spUUID, self._sdUUID, self._imgUUID, self._UUID, newSize)

    def updateSize(self, newSize):
        return self._irs.updateVolumeSize(
            self._spUUID, self._sdUUID, self._imgUUID, self._UUID, newSize)

    def getInfo(self):
        return self._irs.getVolumeInfo(self._sdUUID, self._spUUID,
                                       self._imgUUID, self._UUID)

    def getSize(self):
        return self._irs.getVolumeSize(self._sdUUID, self._spUUID,
                                       self._imgUUID, self._UUID)

    def setSize(self, newSize):
        return self._irs.setVolumeSize(self._sdUUID, self._spUUID,
                                       self._imgUUID, self._UUID, newSize)

    def refresh(self):
        return self._irs.refreshVolume(self._sdUUID, self._spUUID,
                                       self._imgUUID, self._UUID)

    def setDescription(self, description):
        return self._irs.setVolumeDescription(self._sdUUID, self._spUUID,
                                              self._imgUUID, self._UUID,
                                              description)

    def setLegality(self, legality):
        return self._irs.setVolumeLegality(self._sdUUID, self._spUUID,
                                           self._imgUUID, self._UUID, legality)


class Image(APIBase):
    ctorArgs = ['imageID', 'storagepoolID', 'storagedomainID']

    BLANK_UUID = storage.volume.BLANK_UUID

    class DiskTypes:
        UNKNOWN = storage.image.UNKNOWN_DISK_TYPE
        SYSTEM = storage.image.SYSTEM_DISK_TYPE
        DATA = storage.image.DATA_DISK_TYPE
        SHARED = storage.image.SHARED_DISK_TYPE
        SWAP = storage.image.SWAP_DISK_TYPE
        TEMP = storage.image.TEMP_DISK_TYPE

    def __init__(self, UUID, spUUID, sdUUID):
        APIBase.__init__(self)
        self._UUID = UUID
        self._spUUID = spUUID
        self._sdUUID = sdUUID

    def delete(self, postZero, force):
        return self._irs.deleteImage(self._sdUUID, self._spUUID, self._UUID,
                                     postZero, force)

    def deleteVolumes(self, volumeList, postZero=False, force=False):
        return self._irs.deleteVolume(self._sdUUID, self._spUUID, self._UUID,
                                      volumeList, postZero, force)

    def getVolumes(self):
        return self._irs.getVolumesList(self._sdUUID, self._spUUID, self._UUID)

    def mergeSnapshots(self, ancestor, successor, postZero):
        vmUUID = ''   # Not used
        # XXX: On success, self._sdUUID needs to be updated
        return self._irs.mergeSnapshots(self._sdUUID, self._spUUID, vmUUID,
                                        self._UUID, ancestor, successor,
                                        postZero)

    def move(self, dstSdUUID, operation, postZero, force):
        vmUUID = ''   # Not used
        # XXX: On success, self._sdUUID needs to be updated
        return self._irs.moveImage(self._spUUID, self._sdUUID, dstSdUUID,
                                   self._UUID, vmUUID, operation, postZero,
                                   force)

    def sparsify(self, tmpVolUUID, dstSdUUID, dstImgUUID, dstVolUUID):
        return self._irs.sparsifyImage(self._spUUID, self._sdUUID, self._UUID,
                                       tmpVolUUID, dstSdUUID, dstImgUUID,
                                       dstVolUUID)

    def cloneStructure(self, dstSdUUID):
        return self._irs.cloneImageStructure(self._spUUID, self._sdUUID,
                                             self._UUID, dstSdUUID)

    def syncData(self, dstSdUUID, syncType):
        return self._irs.syncImageData(self._spUUID, self._sdUUID, self._UUID,
                                       dstSdUUID, syncType)

    def upload(self, methodArgs, volumeID=None):
        return self._irs.uploadImage(
            methodArgs, self._spUUID, self._sdUUID, self._UUID, volumeID)

    def download(self, methodArgs, volumeID=None):
        return self._irs.downloadImage(
            methodArgs, self._spUUID, self._sdUUID, self._UUID, volumeID)

    def prepare(self, volumeID):
        return self._irs.prepareImage(
            self._sdUUID, self._spUUID, self._UUID, volumeID)

    def teardown(self, volumeID=None):
        return self._irs.teardownImage(
            self._sdUUID, self._spUUID, self._UUID, volumeID)

    def uploadToStream(self, methodArgs, callback, startEvent, volUUID=None):
        return self._irs.uploadImageToStream(
            methodArgs, callback, startEvent, self._spUUID, self._sdUUID,
            self._UUID, volUUID)

    def downloadFromStream(self, methodArgs, callback, volUUID=None):
        return self._irs.downloadImageFromStream(
            methodArgs, callback, self._spUUID, self._sdUUID, self._UUID,
            volUUID)

    def reconcileVolumeChain(self, leafVolID):
        return self._irs.reconcileVolumeChain(self._spUUID, self._sdUUID,
                                              self._UUID, leafVolID)


class LVMVolumeGroup(APIBase):
    ctorArgs = ['lvmvolumegroupID']

    def __init__(self, lvmvolumegroupID=None):
        APIBase.__init__(self)
        self._UUID = lvmvolumegroupID

    def create(self, name, devlist, force=False):
        return self._irs.createVG(name, devlist, force)

    def getInfo(self):
        if self._UUID is not None:
            return self._irs.getVGInfo(self._UUID)
        else:
            # FIXME: Add proper error return
            return None

    def remove(self):
        if self._UUID is not None:
            return self._irs.removeVG(self._UUID)
        else:
            # FIXME: Add proper error return
            return None


class ISCSIConnection(APIBase):
    ctorArgs = ['host', 'port', 'user', 'password']

    def __init__(self, host, port, user="", password=""):
        APIBase.__init__(self)
        self._host = host
        self._port = port
        self._user = user
        self._pass = password

    def discoverSendTargets(self):
        params = {'connection': self._host, 'port': self._port,
                  'user': self._user, 'password': self._pass}
        return self._irs.discoverSendTargets(params)


class StorageDomain(APIBase):
    ctorArgs = ['storagedomainID']

    class Types:
        UNKNOWN = storage.sd.UNKNOWN_DOMAIN
        NFS = storage.sd.NFS_DOMAIN
        FCP = storage.sd.FCP_DOMAIN
        ISCSI = storage.sd.ISCSI_DOMAIN
        LOCALFS = storage.sd.LOCALFS_DOMAIN
        CIFS = storage.sd.CIFS_DOMAIN
        POSIXFS = storage.sd.POSIXFS_DOMAIN

    class Classes:
        DATA = storage.sd.DATA_DOMAIN
        ISO = storage.sd.ISO_DOMAIN
        BACKUP = storage.sd.BACKUP_DOMAIN

    BLANK_UUID = storage.sd.BLANK_UUID

    def __init__(self, UUID):
        APIBase.__init__(self)
        self._UUID = UUID

    def activate(self, storagepoolID):
        return self._irs.activateStorageDomain(self._UUID, storagepoolID)

    def attach(self, storagepoolID):
        return self._irs.attachStorageDomain(self._UUID, storagepoolID)

    def create(self, domainType, typeArgs, name, domainClass, version=None):
        if version is None:
            version = constants.SUPPORTED_DOMAIN_VERSIONS[0]
        return self._irs.createStorageDomain(domainType, self._UUID, name,
                                             typeArgs, domainClass, version)

    def deactivate(self, storagepoolID, masterSdUUID, masterVersion):
        return self._irs.deactivateStorageDomain(self._UUID, storagepoolID,
                                                 masterSdUUID, masterVersion)

    def detach(self, storagepoolID, masterSdUUID=None, masterVersion=0,
               force=False):
        if force:
            return self._irs.forcedDetachStorageDomain(self._UUID,
                                                       storagepoolID)
        else:
            return self._irs.detachStorageDomain(self._UUID, storagepoolID,
                                                 masterSdUUID, masterVersion)

    def extend(self, storagepoolID, devlist, force=False):
        return self._irs.extendStorageDomain(self._UUID, storagepoolID,
                                             devlist, force)

    def resizePV(self, storagepoolID, guid):
        return self._irs.resizePV(self._UUID, storagepoolID, guid)

    def format(self, autoDetach):
        return self._irs.formatStorageDomain(self._UUID, autoDetach)

    def getFileStats(self, pattern, caseSensitive):
        return self._irs.getFileStats(self._UUID, pattern, caseSensitive)

    def getImages(self):
        return self._irs.getImagesList(self._UUID)

    def getInfo(self):
        return self._irs.getStorageDomainInfo(self._UUID)

    def getStats(self):
        return self._irs.getStorageDomainStats(self._UUID)

    def getVolumes(self, storagepoolID, imageID=Image.BLANK_UUID):
        return self._irs.getVolumesList(self._UUID, storagepoolID, imageID)

    def setDescription(self, description):
        return self._irs.setStorageDomainDescription(self._UUID, description)

    def validate(self):
        return self._irs.validateStorageDomain(self._UUID)


class StoragePool(APIBase):
    ctorArgs = ['storagepoolID']

    def __init__(self, UUID):
        APIBase.__init__(self)
        self._UUID = UUID

    # scsiKey not used
    def connect(self, hostID, scsiKey, masterSdUUID, masterVersion,
                domainDict=None):
        return self._irs.connectStoragePool(
            self._UUID, hostID, masterSdUUID, masterVersion, domainDict)

    def connectStorageServer(self, domainType, connectionParams):
        return self._irs.connectStorageServer(domainType, self._UUID,
                                              connectionParams)

    def create(self, name, masterSdUUID, masterVersion, domainList,
               lockRenewalIntervalSec, leaseTimeSec, ioOpTimeoutSec,
               leaseRetries):
        poolType = None   # Not used
        lockPolicy = None   # Not used
        return self._irs.createStoragePool(
            poolType, self._UUID, name, masterSdUUID, domainList,
            masterVersion, lockPolicy, lockRenewalIntervalSec, leaseTimeSec,
            ioOpTimeoutSec, leaseRetries)

    # scsiKey not used
    def destroy(self, hostID, scsiKey):
        return self._irs.destroyStoragePool(self._UUID, hostID)

    # scsiKey not used
    def disconnect(self, hostID, scsiKey, remove=False):
        return self._irs.disconnectStoragePool(self._UUID, hostID, remove)

    def disconnectStorageServer(self, domainType, connectionParams):
        return self._irs.disconnectStorageServer(domainType, self._UUID,
                                                 connectionParams)

    def fence(self):
        lastOwner = None   # Unused
        lastLver = None   # Unused
        return self._irs.fenceSpmStorage(self._UUID, lastOwner, lastLver)

    def getBackedUpVmsInfo(self, storagedomainID, vmList):
        return self._irs.getVmsInfo(self._UUID, storagedomainID, vmList)

    def getBackedUpVmsList(self, storagedomainID):
        return self._irs.getVmsList(self._UUID, storagedomainID)

    def getDomainsContainingImage(self, imageID):
        return self._irs.getImageDomainsList(self._UUID, imageID)

    def getSpmStatus(self):
        return self._irs.getSpmStatus(self._UUID)

    def getInfo(self):
        return self._irs.getStoragePoolInfo(self._UUID)

    def moveMultipleImages(self, srcSdUUID, dstSdUUID, imgDict,
                           force=False):
        vmUUID = None   # Unused parameter
        return self._irs.moveMultipleImages(self._UUID, srcSdUUID, dstSdUUID,
                                            imgDict, vmUUID, force)

    def reconstructMaster(self, hostId, name, masterSdUUID, masterVersion,
                          domainDict, lockRenewalIntervalSec, leaseTimeSec,
                          ioOpTimeoutSec, leaseRetries):
        lockPolicy = None   # Not used
        return self._irs.reconstructMaster(
            self._UUID, name, masterSdUUID, domainDict, masterVersion,
            lockPolicy, lockRenewalIntervalSec, leaseTimeSec, ioOpTimeoutSec,
            leaseRetries, hostId)

    def refresh(self, masterSdUUID, masterVersion):
        return self._irs.refreshStoragePool(self._UUID, masterSdUUID,
                                            masterVersion)

    def setDescription(self, description):
        return self._irs.setStoragePoolDescription(self._UUID, description)

    def spmStart(self, prevID, prevLver, enableScsiFencing,
                 maxHostID=None, domVersion=None):
        if maxHostID is None:
            maxHostID = storage.clusterlock.MAX_HOST_ID
        return self._irs.spmStart(self._UUID, prevID, prevLver, maxHostID,
                                  domVersion)

    def spmStop(self):
        return self._irs.spmStop(self._UUID)

    def upgrade(self, targetDomVersion):
        return self._irs.upgradeStoragePool(self._UUID, targetDomVersion)

    def updateVMs(self, vmList, storagedomainID=None):
        return self._irs.updateVM(self._UUID, vmList, storagedomainID)

    def removeVM(self, vmUUID, storagedomainID=None):
        return self._irs.removeVM(self._UUID, vmUUID, storagedomainID)


class Global(APIBase):
    ctorArgs = []

    def __init__(self):
        APIBase.__init__(self)

    # General Host functions
    def fenceNode(self, addr, port, agent, username, password, action,
                  secure=False, options='', policy=None):
        """Send a fencing command to a remote node.

           agent is one of (rsa, ilo, drac5, ipmilan, etc)
           action can be one of (status, on, off, reboot)."""

        def fence(script, inp):
            rc, out, err = commands.execCmd([script], data=inp)
            self.log.debug('rc %s inp %s out %s err %s', rc,
                           hidePasswd(inp), out, err)
            return rc, out, err

        def hidePasswd(text):
            cleantext = ''
            for line in text.splitlines(True):
                if line.startswith('passwd='):
                    line = 'passwd=XXXX\n'
                cleantext += line
            return cleantext

        def should_fence(policy):
            # skip fence execution if map of storage domains with host id is
            # entered and at least one storage domain connection from host is
            # alive
            if policy is None:
                self.log.debug('No policy specified')
                return True

            hostIdMap = policy.get('storageDomainHostIdMap')
            if not hostIdMap:
                self.log.warning('Invalid policy specified')
                return True

            result = self._irs.getHostLeaseStatus(hostIdMap)
            if result['status']['code'] != 0:
                self.log.error(
                    "Error getting host lease status, error code '%s'",
                    result['status']['code'])
                return True

            # HOST_STATUS_LIVE means that host renewed its lease in last 80
            # seconds. If so, we consider the host Up and we won't execute
            # fencing, even when it's unreachable from engine
            for sd, status in result['domains'].iteritems():
                if status == storage.clusterlock.HOST_STATUS_LIVE:
                    self.log.debug("Host has live lease on '%s'", sd)
                    return False

            self.log.debug("Host doesn't have any live lease")
            return True

        self.log.debug('fenceNode(addr=%s,port=%s,agent=%s,user=%s,passwd=%s,'
                       'action=%s,secure=%s,options=%s,policy=%s)',
                       addr, port, agent, username, password, action, secure,
                       options, policy)

        if action not in ('status', 'on', 'off', 'reboot'):
            raise ValueError('illegal action ' + action)

        if action != 'status' and not should_fence(policy):
            self.log.debug("Skipping execution of action '%s'", action)
            return {'status': doneCode, 'operationStatus': 'skipped'}

        script = constants.EXT_FENCE_PREFIX + agent

        inp = ('agent=fence_%s\nipaddr=%s\nlogin=%s\naction=%s\n'
               'passwd=%s\n') % (agent, addr, username, action, password.value)
        if port != '':
            inp += 'port=%s\n' % (port,)
        if utils.tobool(secure):
            inp += 'secure=yes\n'
        inp += options

        try:
            rc, out, err = fence(script, inp)
        except OSError as e:
            if e.errno == os.errno.ENOENT:
                return errCode['fenceAgent']
            raise
        self.log.debug('rc %s in %s out %s err %s', rc,
                       hidePasswd(inp), out, err)
        if not 0 <= rc <= 2:
            return {'status': {'code': 1,
                               'message': out + err}}
        message = doneCode['message']
        if action == 'status':
            if rc == 0:
                power = 'on'
            elif rc == 2:
                power = 'off'
            else:
                power = 'unknown'
                message = out + err
            return {'status': {'code': 0, 'message': message},
                    'power': power}
        if rc != 0:
            message = out + err
        return {'status': {'code': rc, 'message': message},
                'power': 'unknown', 'operationStatus': 'initiated'}

    def ping(self):
        "Ping the server. Useful for tests"
        updateTimestamp()
        return {'status': doneCode}

    def getCapabilities(self):
        """
        Report host capabilities.
        """
        hooks.before_get_caps()
        updateTimestamp()  # required for some ovirt-3.0.z Engines
        c = caps.get()
        c['netConfigDirty'] = str(self._cif._netConfigDirty)
        c = hooks.after_get_caps(c)

        return {'status': doneCode, 'info': c}

    def getHardwareInfo(self):
        """
        Report host hardware information
        """
        try:
            hw = supervdsm.getProxy().getHardwareInfo()
            return {'status': doneCode, 'info': hw}
        except:
            self.log.error("failed to retrieve hardware info", exc_info=True)
            return errCode['hwInfoErr']

    def getAllVmStats(self):
        """
        Get statistics of all running VMs.
        """
        hooks.before_get_all_vm_stats()
        statsList = self._cif.getAllVmStats()
        statsList = hooks.after_get_all_vm_stats(statsList)
        return {'status': doneCode, 'statsList': statsList}

    def hostdevListByCaps(self, caps=None):
        devices = hostdev.list_by_caps(caps)
        return {'status': doneCode, 'deviceList': devices}

    def hostdevChangeNumvfs(self, deviceName, numvfs):
        self._cif._netConfigDirty = True
        hostdev.change_numvfs(deviceName, numvfs)
        return {'status': doneCode}

    def getStats(self):
        """
        Report host statistics.
        """

        def _readSwapTotalFree():
            meminfo = utils.readMemInfo()
            return meminfo['SwapTotal'] / 1024, meminfo['SwapFree'] / 1024

        hooks.before_get_stats()
        stats = {}

        first_sample, last_sample, _ = sampling.host_samples.stats()
        decStats = hoststats.produce(first_sample, last_sample)

        if self._irs:
            decStats['storageDomains'] = self._irs.repoStats()
            del decStats['storageDomains']['status']
        else:
            decStats['storageDomains'] = {}

        for var in decStats:
            stats[var] = utils.convertToStr(decStats[var])

        stats['memAvailable'] = self._memAvailable() / Mbytes
        stats['memCommitted'] = self._memCommitted() / Mbytes
        stats['memFree'] = self._memFree() / Mbytes
        stats['swapTotal'], stats['swapFree'] = _readSwapTotalFree()
        (stats['vmCount'], stats['vmActive'], stats['vmMigrating'],
         stats['incomingVmMigrations'], stats['outgoingVmMigrations']) = \
            self._countVms()
        (tm_year, tm_mon, tm_day, tm_hour, tm_min, tm_sec,
         dummy, dummy, dummy) = time.gmtime(time.time())
        stats['dateTime'] = '%02d-%02d-%02dT%02d:%02d:%02d GMT' % (
            tm_year, tm_mon, tm_day, tm_hour, tm_min, tm_sec)
        stats['momStatus'] = self._cif.mom.getStatus()
        stats.update(self._cif.mom.getKsmStats())

        stats['netConfigDirty'] = str(self._cif._netConfigDirty)
        stats['generationID'] = self._cif._generationID
        stats['haStats'] = self._getHaInfo()
        if stats['haStats']['configured']:
            # For backwards compatibility, will be removed in the future
            stats['haScore'] = stats['haStats']['score']

        stats = hooks.after_get_stats(stats)
        return {'status': doneCode, 'info': stats}

    def setLogLevel(self, level):
        """
        Set verbosity level of vdsm's log.

        params
            level: requested logging level. `logging.DEBUG` `logging.ERROR`

        Doesn't survive a restart
        """
        logging.warning('Setting loglevel to %s', level)
        handlers = logging.getLogger().handlers
        [fileHandler] = [h for h in handlers if
                         isinstance(h, logging.FileHandler)]
        fileHandler.setLevel(int(level))

        return dict(status=doneCode)

    # VM-related functions
    def getVMList(self, fullStatus=False, vmList=(), onlyUUID=False):
        """ return a list of known VMs with full (or partial) config each """
        # To improve complexity, convert 'vms' to set(vms)
        vmSet = set(vmList)
        vmlist = [v.status(fullStatus)
                  for v in self._cif.vmContainer.values()
                  if not vmSet or v.id in vmSet]
        if not fullStatus and onlyUUID:
            # BZ 1196735: api backward compatibility issue
            # REQUIRED_FOR: engine-3.5.0 only
            vmlist = [v['vmId'] for v in vmlist]
        return {'status': doneCode, 'vmList': vmlist}

    def getExternalVMs(self, uri, username, password):
        """
        Return information about the not-KVM virtual machines:
        getExternalVMs returns list of VMs with subsection of  properties
        that returns from getVmsList (with the same keys ie vmName for name)
        currently v2v returns the following information:
            vm: vmName, vmId, state, memSize, smp, disks and network list,
            disk: dev, alias
            network: type, macAddr, bridge, dev
        """
        return v2v.get_external_vms(uri, username, password)

    def getExternalVmFromOva(self, ova_path):
        """
        Return information regarding a VM that is a part of the ova:
        getExternalVmFromOva return information on a VM that is a part
        of the provided ova file.
        The return value is a VM with the following information:
            vm: vmName, state, memSize, smp, disks and network list,
            disk: type, capacity, alias, allocation
            network: dev, model, type, bridge
        """
        return v2v.get_ova_info(ova_path)

    def convertExternalVm(self, uri, username, password, vminfo, jobid):
        return v2v.convert_external_vm(uri, username, password, vminfo, jobid,
                                       self._irs)

    def convertExternalVmFromOva(self, ova_path, vminfo, jobid):
        return v2v.convert_ova(ova_path, vminfo, jobid, self._cif.irs)

    def getJobs(self, job_type=None, job_ids=()):
        found = jobs.info(job_type=job_type, job_ids=job_ids)
        return response.success(jobs=found)

    def getConvertedVm(self, jobid):
        return v2v.get_converted_vm(jobid)

    def deleteV2VJob(self, jobid):
        return v2v.delete_job(jobid)

    def abortV2VJob(self, jobid):
        return v2v.abort_job(jobid)

    def registerSecrets(self, secrets, clear=False):
        return secret.register(secrets, clear=clear)

    def unregisterSecrets(self, uuids):
        return secret.unregister(uuids)

    # Networking-related functions
    def setupNetworks(self, networks, bondings, options):
        """Add a new network to this vds, replacing an old one."""

        if not self._cif._networkSemaphore.acquire(blocking=False):
            self.log.warn('concurrent network verb already executing')
            return errCode['unavail']

        try:
            self._cif._netConfigDirty = True
            supervdsm.getProxy().setupNetworks(networks, bondings, options)
            return {'status': doneCode}
        except ConfigNetworkError as e:
            self.log.error(e.message, exc_info=True)
            return {'status': {'code': e.errCode, 'message': e.message}}
        except exception.HookError as e:
            return response.error('hookError', 'Hook error: ' + str(e))
        except:
            raise
        finally:
            self._cif._networkSemaphore.release()

    def setSafeNetworkConfig(self):
        """Declare current network configuration as 'safe'"""
        if not self._cif._networkSemaphore.acquire(blocking=False):
            self.log.warn('concurrent network verb already executing')
            return errCode['unavail']
        try:
            self._cif._netConfigDirty = False
            supervdsm.getProxy().setSafeNetworkConfig()
            return {'status': doneCode}
        finally:
            self._cif._networkSemaphore.release()

    # Top-level storage functions
    def getStorageDomains(self, storagepoolID=None, domainClass=None,
                          storageType=None, remotePath=None):
        return self._irs.getStorageDomainsList(storagepoolID, domainClass,
                                               storageType, remotePath)

    def getConnectedStoragePools(self):
        return self._irs.getConnectedStoragePoolsList()

    def getStorageRepoStats(self):
        return self._irs.repoStats()

    def startMonitoringDomain(self, sdUUID, hostID):
        return self._irs.startMonitoringDomain(sdUUID, hostID)

    def stopMonitoringDomain(self, sdUUID):
        return self._irs.stopMonitoringDomain(sdUUID)

    def getLVMVolumeGroups(self, storageType=None):
        return self._irs.getVGList(storageType)

    def getDeviceList(self, storageType=None, guids=(), checkStatus=True):
        return self._irs.getDeviceList(storageType, guids, checkStatus)

    def getDevicesVisibility(self, guidList):
        return self._irs.getDevicesVisibility(guidList)

    def getAllTasksInfo(self):
        return self._irs.getAllTasksInfo()

    def getAllTasksStatuses(self):
        return self._irs.getAllTasksStatuses()

    def getAllTasks(self):
        return self._irs.getAllTasks()

    def setMOMPolicy(self, policy):
        try:
            self._cif.mom.setPolicy(policy)
            return dict(status=doneCode)
        except:
            return errCode['momErr']

    def setMOMPolicyParameters(self, key_value_store):
        try:
            self._cif.mom.setPolicyParameters(key_value_store)
            return dict(status=doneCode)
        except:
            return errCode['momErr']

    def setKsmTune(self, tuningParams):
        try:
            supervdsm.getProxy().ksmTune(tuningParams)
            return dict(status=doneCode)
        except:
            self.log.exception('setKsmTune API call failed.')
            return errCode['ksmErr']

    def setHaMaintenanceMode(self, mode, enabled):
        """
        Sets Hosted Engine HA maintenance mode ('global' or 'local') to
        enabled (True) or disabled (False).
        """
        if not haClient:
            return errCode['unavail']

        self.log.info("Setting Hosted Engine HA %s maintenance to %s",
                      mode.lower(), enabled)
        if mode.lower() == 'global':
            mm = haClient.HAClient.MaintenanceMode.GLOBAL
        elif mode.lower() == 'local':
            mm = haClient.HAClient.MaintenanceMode.LOCAL
        else:
            return errCode['haErr']

        try:
            haClient.HAClient().set_maintenance_mode(mm, enabled)
        except Exception:
            self.log.exception("error setting HA maintenance mode")
            return errCode['haErr']
        return {'status': doneCode}

    def add_image_ticket(self, ticket):
        return self._irs.add_image_ticket(ticket)

    def remove_image_ticket(self, uuid):
        return self._irs.remove_image_ticket(uuid)

    def extend_image_ticket(self, uuid, timeout):
        return self._irs.extend_image_ticket(uuid, timeout)

    # take a rough estimate on how much free mem is available for new vm
    # memTotal = memFree + memCached + mem_used_by_non_qemu + resident  .
    # simply returning (memFree + memCached) is not good enough, as the
    # resident set size of qemu processes may grow - up to  memCommitted.
    # Thus, we deduct the growth potential of qemu processes, which is
    # (memCommitted - resident)

    def _memAvailable(self):
        """
        Return an approximation of available memory for new VMs.
        """
        memCommitted = self._memCommitted()
        resident = 0
        for v in self._cif.vmContainer.values():
            if v.conf['pid'] == '0':
                continue
            try:
                with open('/proc/' + v.conf['pid'] + '/statm') as statmfile:
                    resident += int(statmfile.read().split()[1])
            except:
                pass
        resident *= cpuarch.PAGE_SIZE_BYTES
        meminfo = utils.readMemInfo()
        freeOrCached = (meminfo['MemFree'] +
                        meminfo['Cached'] + meminfo['Buffers']) * Kbytes
        return freeOrCached + resident - memCommitted - \
            config.getint('vars', 'host_mem_reserve') * Mbytes

    def _memFree(self):
        """
        Return the actual free mem on host.
        """
        meminfo = utils.readMemInfo()
        return (meminfo['MemFree'] +
                meminfo['Cached'] + meminfo['Buffers']) * Kbytes

    def _memCommitted(self):
        """
        Return the amount of memory (Mb) committed for VMs
        """
        committed = 0
        for v in self._cif.vmContainer.values():
            committed += v.memCommitted
        return committed

    def _countVms(self):
        count = active = incoming = outgoing = 0
        for vmId, v in self._cif.vmContainer.items():
            try:
                count += 1
                status = v.lastStatus
                if status == vmstatus.UP:
                    active += 1
                elif status == vmstatus.MIGRATION_DESTINATION:
                    incoming += 1
                elif status == vmstatus.MIGRATION_SOURCE:
                    outgoing += 1
            except:
                self.log.error(vmId + ': Lost connection to VM')
        return count, active, incoming + outgoing, incoming, outgoing

    def _getHaInfo(self):
        """
        Return Hosted Engine HA information for this host.
        """
        i = {
            'configured': False,
            'active': False,
            'score': 0,
            'globalMaintenance': False,
            'localMaintenance': False,
        }
        if haClient:
            try:
                instance = haClient.HAClient()
                host_id = instance.get_local_host_id()

                # If a host id is available, consider HA configured
                if host_id:
                    i['configured'] = True
                else:
                    return i

                stats = instance.get_all_stats()
                if 0 in stats:
                    i['globalMaintenance'] = stats[0].get(
                        haClient.HAClient.GlobalMdFlags.MAINTENANCE, False)
                if host_id in stats:
                    i['active'] = stats[host_id]['live-data']
                    i['score'] = stats[host_id]['score']
                    i['localMaintenance'] = stats[host_id]['maintenance']
            except IOError as ex:
                if ex.errno == errno.ENOENT:
                    self.log.error(
                        ("failed to retrieve Hosted Engine HA score '{0}'"
                         "Is the Hosted Engine setup finished?")
                        .format(str(ex))
                    )
                else:
                    self.log.exception(
                        "failed to retrieve Hosted Engine HA score"
                    )
            except Exception:
                self.log.exception("failed to retrieve Hosted Engine HA info")
        return i


class SDM(APIBase):
    ctorArgs = []

    def create_volume(self, job_id, vol_info):
        return self._irs.sdm_create_volume(job_id, vol_info)
