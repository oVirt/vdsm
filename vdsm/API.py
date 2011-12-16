#
# Copyright (C) 2012 Adam Litke, IBM Corporation
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

import constants
import storage.volume
import storage.safelease

class Task(object):
    def __init__(self, cif, UUID):
        self._irs = cif.irs
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

class VM(object):
    def __init__(self, cif, UUID):
        self._cif = cif
        self._UUID = UUID

    def changeCD(self, driveSpec):
        return self._cif.changeCD(self._UUID, driveSpec)

    def changeFloppy(self, driveSpec):
        return self._cif.changeFloppy(self._UUID, driveSpec)

    def cont(self):
        return self._cif.cont(self._UUID)

    def create(self, createParameters):
        createParameters['vmId'] = self._UUID
        return self._cif.create(createParameters)

    def desktopLock(self):
        return self._cif.desktopLock(self._UUID)

    def desktopLogin(self, domain, username, password):
        return self._cif.desktopLogin(self._UUID, domain, username,
                                      password)

    def desktopLogoff(self, force):
        return self._cif.desktopLogoff(self._UUID, force)

    def desktopSendHcCommand(self, message):
        return self._cif.sendHcCmdToDesktop(self._UUID, message)

    def destroy(self):
        return self._cif.destroy(self._UUID)

    def getMigrationStatus(self):
        return self._cif.migrateStatus(self._UUID)

    def getStats(self):
        return self._cif.getVmStats(self._UUID)

    def hibernate(self, hibernationVolHandle):
        return self._cif.hibernate(self._UUID, hibernationVolHandle)

    def hotplugDisk(self, params):
        params['vmId'] = self._UUID
        return self._cif.hotplugDisk(params)

    def hotunplugDisk(self, params):
        params['vmId'] = self._UUID
        return self._cif.hotunplugDisk(params)

    def migrate(self, params):
        params['vmId'] = self._UUID
        return self._cif.migrate(params)

    def migrationCancel(self):
        return self._cif.migrateCancel(self._UUID)

    def migrationCreate(self, params):
        params['vmId'] = self._UUID
        return self._cif.migrationCreate(params)

    def monitorCommand(self, command):
        return self._cif.monitorCommand(self._UUID, command)

    def pause(self):
        return self._cif.pause(self._UUID)

    def reset(self):
        return self._cif.sysReset(self._UUID)

    def sendKeys(self, keySequence):
        return self._cif.sendKeys(self._UUID, keySequence)

    def setTicket(self, password, ttl, existingConnAction):
        return self._cif.setVmTicket(self._UUID, password, ttl,
                                     existingConnAction)

    def shutdown(self, delay, message):
        return self._cif.shutdown(self._UUID, delay, message)

    def snapshot(self, snapDrives):
        return self._cif.snapshot(self._UUID, snapDrives)

class Volume(object):
    def __init__(self, cif, UUID, spUUID, sdUUID, imgUUID):
        self._irs = cif.irs
        self._UUID = UUID
        self._spUUID = spUUID
        self._sdUUID = sdUUID
        self._imgUUID = imgUUID

    def copy(self, dstSdUUID, dstImgUUID, dstVolUUID, desc, volType,
             volFormat, preallocate, postZero, force):
        vmUUID = '' # vmUUID is never used
        return self._irs.copyImage(self._sdUUID, self._spUUID, vmUUID,
                self._imgUUID, self._UUID, dstImgUUID, dstVolUUID, desc,
                dstSdUUID, volType, volFormat, preallocate, postZero,
                force)

    def create(self, size, volFormat, preallocate, diskType, desc,
               srcImgUUID, srcVolUUID):
        return self._irs.createVolume(self._sdUUID, self._spUUID,
                self._imgUUID, size, volFormat, preallocate, diskType,
                self._UUID, desc, srcImgUUID, srcVolUUID)

    def delete(self, postZero, force):
        return self._irs.deleteVolume(self._sdUUID, self._spUUID,
                self._imgUUID, [self._UUID], postZero, force)

    def extend(self, size, isShuttingDown):
        return self._irs.extendVolume(self._sdUUID, self._spUUID,
                self._imgUUID, self._UUID, size, isShuttingDown)

    def getInfo(self):
        return self._irs.getVolumeInfo(self._sdUUID, self._spUUID,
                self._imgUUID, self._UUID)

    def getPath(self):
        return self._irs.getVolumePath(self._sdUUID, self._spUUID,
                self._imgUUID, self._UUID)

    def getSize(self):
        return self._irs.getVolumeSize(self._sdUUID, self._spUUID,
                self._imgUUID, self._UUID)

    def prepare(self, rw):
        return self._irs.prepareVolume(self._sdUUID, self._spUUID,
                self._imgUUID, self._UUID, rw)

    def refresh(self):
        return self._irs.refreshVolume(self._sdUUID, self._spUUID,
                self._imgUUID, self._UUID)

    def setDescription(self, description):
        return self._irs.setVolumeDescription(self._sdUUID,
                self._spUUID, self._imgUUID, self._UUID, description)

    def setLegality(self, legality):
        return self._irs.setVolumeLegality(self._sdUUID,
                self._spUUID, self._imgUUID, self._UUID, legality)

    def tearDown(self):
        return self._irs.tearDownVolume(self._sdUUID, self._spUUID,
                self._imgUUID, self._UUID)

class Image(object):
    def __init__(self, cif, UUID, spUUID, sdUUID):
        self._irs = cif.irs
        self._UUID = UUID
        self._spUUID = spUUID
        self._sdUUID = sdUUID

    def delete(self, postZero, force):
        return self._irs.deleteImage(self._sdUUID, self._spUUID,
                self._UUID, postZero, force)

    def deleteVolumes(self, volumeList, postZero=False, force=False):
        return self._irs.deleteVolume(self._sdUUID, self._spUUID,
                self._UUID, volumeList, postZero, force)

    def getVolumes(self):
        return self._irs.getVolumesList(self._sdUUID, self._spUUID,
                self._UUID)

    def mergeSnapshots(self, ancestor, successor, postZero):
        vmUUID = '' # Not used
        # XXX: On success, self._sdUUID needs to be updated
        return self._irs.mergeSnapshots(self._sdUUID, self._spUUID,
                vmUUID, self._UUID, ancestor, successor, postZero)

    def move(self, dstSdUUID, operation, postZero, force):
        vmUUID = '' # Not used
        # XXX: On success, self._sdUUID needs to be updated
        return self._irs.moveImage(self._spUUID, self._sdUUID,
                dstSdUUID, self._UUID, vmUUID, operation, postZero, force)

class LVMVolumeGroup(object):
    def __init__(self, cif, UUID=None):
        self._irs = cif.irs
        self._UUID = UUID

    def create(self, name, devlist):
        return self._irs.createVG(name, devlist)

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

class ISCSIConnection(object):
    def __init__(self, cif, host, port, user="", password=""):
        self._irs = cif.irs
        self._host = host
        self._port = port
        self._user = user
        self._pass = password

    def discoverSendTargets(self):
        params = { 'connection': self._host, 'port': self._port,
                   'user': self._user, 'password': self._pass }
        return self._irs.discoverSendTargets(params)


class StorageDomain(object):
    def __init__(self, cif, UUID, spUUID=None):
        self._irs = cif.irs
        self._UUID = UUID
        self._spUUID = spUUID

    def activate(self):
        return self._irs.activateStorageDomain(self._UUID, self._spUUID)

    def attach(self, spUUID):
        # XXX: on success, self._spUUID should be set
        return self._irs.attachStorageDomain(self._UUID, spUUID)

    def create(self, type, typeArgs, name, domainClass, version=None):
        if version is None:
            version = constants.SUPPORTED_DOMAIN_VERSIONS[0]
        return self._irs.createStorageDomain(type, self._UUID, name,
                typeArgs, domainClass, version)

    def deactivate(self, masterSdUUID, masterVersion):
        return self._irs.deactivateStorageDomain(self._UUID,
                self._spUUID, masterSdUUID, masterVersion)

    def detach(self, masterSdUUID, masterVersion, force):
        # XXX: on success, self._spUUID should be set to None
        if force:
            return self._irs.forcedDetachStorageDomain(self._UUID,
                    self._spUUID)
        else:
            return self._irs.detachStorageDomain(self._UUID,
                    self._spUUID, masterSdUUID, masterVersion)

    def extend(self, devlist):
        return self._irs.extendStorageDomain(self._UUID,
                self._spUUID, devlist)

    def format(self, autoDetach):
        return self._irs.formatStorageDomain(self._UUID, autoDetach)

    def getFileList(self, pattern):
        return self._irs.getFileList(self._UUID, pattern)

    def getImages(self):
        return self._irs.getImagesList(self._UUID)

    def getInfo(self):
        return self._irs.getStorageDomainInfo(self._UUID)

    def getStats(self):
        return self._irs.getStorageDomainStats(self._UUID)

    def getVolumes(self, imgUUID=storage.volume.BLANK_UUID):
        return self._irs.getVolumesList(self._UUID, self._spUUID, imgUUID)

    def setDescription(self, description):
        return self._irs.setStorageDomainDescription(self._UUID,
                description)

    def uploadVolume(self, imgUUID, volUUID, srcPath, size, method):
        return self._irs.uploadVolume(self._UUID, self._spUUID,
                imgUUID, volUUID, srcPath, size, method)

    def validate(self):
        return self._irs.validateStorageDomain(self._UUID)

class StoragePool(object):
    def __init__(self, cif, UUID):
        self._irs = cif.irs
        self._UUID = UUID

    def connect(self, hostID, scsiKey, masterSdUUID, masterVersion):
        return self._irs.connectStoragePool(self._UUID, hostID, scsiKey,
                masterSdUUID, masterVersion)

    def connectStorageServer(self, domainType, connectionParams):
        return self._irs.connectStorageServer(domainType,
                self._UUID, connectionParams)

    def create(self, name, masterSdUUID, masterVersion, domainList,
               lockRenewalIntervalSec, leaseTimeSec, ioOpTimeoutSec,
               leaseRetries):
        poolType = None # Not used
        lockPolicy = None # Not used
        return self._irs.createStoragePool(poolType, self._UUID,
                name, masterSdUUID, domainList, masterVersion,
                lockPolicy, lockRenewalIntervalSec, leaseTimeSec,
                ioOpTimeoutSec, leaseRetries)

    def destroy(self, hostID, scsiKey):
        return self._irs.destroyStoragePool(self._UUID, hostID, scsiKey)

    def disconnect(self, hostID, scsiKey, remove):
        return self._irs.disconnectStoragePool(self._UUID, hostID,
                scsiKey, remove)

    def disconnectStorageServer(self, domainType, connectionParams):
        return self._irs.disconnectStorageServer(domainType, self._UUID,
                connectionParams)

    def fence(self):
        lastOwner = None # Unused
        lastLver = None # Unused
        return self._irs.fenceSpmStorage(self._UUID, lastOwner,
                lastLver)

    def getBackedUpVmsInfo(self, sdUUID, vmList):
        return self._irs.getVmsInfo(self._UUID, sdUUID, vmList)

    def getBackedUpVmsList(self, sdUUID):
        return self._irs.getVmsList(self._UUID, sdUUID)

    def getFloppyList(self):
        return self._irs.getFloppyList(self._UUID)

    def getDomainsContainingImage(self, imgUUID, onlyDataDomains=True):
        return self._irs.getImageDomainsList(self._UUID, imgUUID,
                onlyDataDomains)

    def getIsoList(self, filenameExtension='iso'):
        return self._irs.getIsoList(self._UUID, filenameExtension)

    def getSpmStatus(self):
        return self._irs.getSpmStatus(self._UUID)

    def getStorageConnections(self):
        return self._irs.getStorageConnectionsList(self._UUID)

    def getInfo(self):
        return self._irs.getStoragePoolInfo(self._UUID)

    def moveMultipleImages(self, srcSdUUID, dstSdUUID, imgDict,
                           force=False):
        vmUUID = None # Unused parameter
        return self._irs.moveMultipleImages(self._UUID, srcSdUUID,
                dstSdUUID, imgDict, vmUUID, force)

    def reconstructMaster(self, name, masterSdUUID, masterVersion,
                          domainDict, lockRenewalIntervalSec,
                          leaseTimeSec, ioOpTimeoutSec, leaseRetries):
        lockPolicy = None # Not used
        return self._irs.reconstructMaster(self._UUID, name,
                masterSdUUID, domainDict, masterVersion, lockPolicy,
                lockRenewalIntervalSec, leaseTimeSec,
                ioOpTimeoutSec, leaseRetries)

    def refresh(self, masterSdUUID, masterVersion):
        return self._irs.refreshStoragePool(self._UUID,
                masterSdUUID, masterVersion)

    def setDescription(self, description):
        return self._irs.setStoragePoolDescription(self._UUID,
                description)

    def spmStart(self, prevID, prevLver, enableScsiFencing,
                 maxHostID=None, domVersion=None):
        if maxHostID is None:
            maxHostID=storage.safelease.MAX_HOST_ID
        recoveryMode = None # unused
        return self._irs.spmStart(self._UUID, prevID, prevLver,
                recoveryMode, enableScsiFencing, maxHostID, domVersion)

    def spmStop(self):
        return self._irs.spmStop(self._UUID)

    def upgrade(self, targetDomVersion):
        return self._irs.upgradeStoragePool(self._UUID,
                targetDomVersion)

    def validateStorageServerConnection(self, domainType,
                                        connectionParams):
        return self._irs.validateStorageServerConnection(domainType,
                self._UUID, [connectionParams])

    def updateVMs(self, vmList, sdUUID):
        return self._irs.updateVM(self._UUID, vmList, sdUUID)

    def removeVMs(self, vmList, sdUUID):
        # This internal API deviates horribly in that it takes a
        # comma-separated list of VMs rather than a proper list
        _vmString = ','.join(vmList)
        return self._irs.removeVM(self._UUID, _vmString, sdUUID)

class Global(object):
    def __init__(self, cif):
        self._cif = cif
        self._irs = cif.irs

    # General Host functions
    def fenceNode(self, addr, port, agent, username, password, action,
                  secure=False):
        return self._cif.fenceNode(addr, port, agent, username,
                password, action, secure)

    def ping(self):
        return self._cif.ping()

    def getCapabilities(self):
        return self._cif.getVdsCapabilities()

    def getStats(self):
        return self._cif.getVdsStats()

    def setLogLevel(self, level):
        return self._cif.setLogLevel(level)

    # VM-related functions
    def getVMList(self, fullStatus=False, vmList=[]):
        return self._cif.list(fullStatus, vmList)

    # Networking-related functions
    def setupNetworks(self, networks={}, bondings={}, options={}):
        return self._cif.setupNetworks(networks, bondings, options)

    def editNetwork(self, oldBridge, newBridge, vlan=None, bond=None,
                    nics=None, options={}):
        return self._cif.editNetwork(oldBridge, newBridge, vlan, bond,
                    nics, options)

    def setSafeNetworkConfig(self):
        return self._cif.setSafeNetworkConfig()

    # Top-level storage functions
    def getStorageDomains(self, spUUID=None, domainClass=None,
                          storageType=None, remotePath=None):
        return self._irs.getStorageDomainsList(spUUID, domainClass,
                storageType, remotePath)

    def getConnectedStoragePools(self):
        return self._irs.getConnectedStoragePoolsList()

    def getStorageRepoStats(self):
        return self._irs.repoStats()

    def getLVMVolumeGroups(self, storageType=None):
        return self._irs.getVGList(storageType)

    def getDeviceList(self, storageType=None):
        return self._irs.getStorageDeviceList(storageType)

    def getDeviceInfo(self, guid):
        return self._irs.getDeviceInfo(guid)

    def getDevicesVisibility(self, guidList):
        return self._irs.getDevicesVisibility(guidList)

    def getAllTasksInfo(self):
        return self._irs.getAllTasksInfo()

    def getAllTasksStatuses(self):
        return self._irs.getAllTasksStatuses()
