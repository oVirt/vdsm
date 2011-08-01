#
# Copyright 2009 Red Hat, Inc. and/or its affiliates.
#
# Licensed to you under the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.  See the files README and
# LICENSE_GPL_v2 which accompany this distribution.
#

import os
from config import config
import logging
import tempfile

import iscsi
import fileUtils
import sd
import storage_exception as se
import outOfProcess as oop
from processPool import Timeout
import supervdsm
import constants

def validateDirAccess(dirPath):
    oop.fileUtils.validateAccess(dirPath)
    supervdsm.getProxy().validateAccess(constants.QEMU_PROCESS_USER,
            (constants.DISKIMAGE_GROUP, constants.METADATA_GROUP), dirPath,
            (os.R_OK | os.X_OK))
    oop.fileUtils.validatePermissions(dirPath)


class StorageServerConnection:
    log = logging.getLogger('Storage.ServerConnection')
    storage_repository = config.get('irs', 'repository')

    @staticmethod
    def loggableConList(conList):
        cons = []
        for con in conList:
            conCopy = con.copy()
            for key in conCopy:
                if key.upper() == 'PASSWORD':
                    conCopy[key] = '******'
            cons.append(conCopy)
        return cons

    def __validateConnectionParams(self, domType, conList):
        """
        Validate connection parameters
        """
        cons = self.loggableConList(conList=conList)
        self.log.info("conList=%s", cons)
        conParamsList = []

        if domType in sd.FILE_DOMAIN_TYPES:
            for con in conList:
                conParams = {}
                try:
                    conParams['cid'] = con['id']
                    conParams['rp'] = con['connection']
                except KeyError:
                    raise se.InvalidParameterException("connection", self.loggableConList([con]))

                conParamsList.append(conParams)
        elif domType in sd.BLOCK_DOMAIN_TYPES:
            for con in conList:
                conParams = {}
                try:
                    conParams['cid'] = con['id']
                    conParams['ip'] = con['connection']
                    conParams['port'] = con['port']
                    conParams['iqn'] = con['iqn']
                    conParams['tpgt'] = con['portal']
                    conParams['user'] = con['user']
                    conParams['password'] = con['password']
                    # For the sake of backward compatibility we do not insist
                    # on presense of 'initiatorName'. We just assume it is
                    # None if it is absent. All the layers below can cope
                    # with initiatorName being None.
                    conParams['initiatorName'] = con.get('initiatorName')
                except KeyError:
                    raise se.InvalidParameterException("connection", self.loggableConList([con]))

                conParamsList.append(conParams)
        else:
            raise se.InvalidParameterException("type", domType)

        return conParamsList

    def connect(self, domType, conList):
        """
        Connect to a storage low level entity (server).
        """
        self.log.info("Request to connect %s storage server", sd.type2name(domType))
        conParams = self.__validateConnectionParams(domType, conList)

        if domType == sd.NFS_DOMAIN:
            return self.__connectFileServer(conParams, fileUtils.FSTYPE_NFS)
        elif domType == sd.LOCALFS_DOMAIN:
            return self.__connectLocalConnection(conParams)
        elif domType in sd.BLOCK_DOMAIN_TYPES:
            return self.__connectiSCSIServer(conParams)
        else:
            raise se.InvalidParameterException("type", domType)

    def disconnect(self, domType, conList):
        """
        Disconnect from a storage low level entity (server).
        """
        self.log.info("Request to disconnect %s storage server", sd.type2name(domType))
        conParams = self.__validateConnectionParams(domType, conList)

        if domType == sd.NFS_DOMAIN:
            return self.__disconnectFileServer(conParams, fileUtils.FSTYPE_NFS)
        elif domType == sd.LOCALFS_DOMAIN:
            return self.__disconnectLocalConnection(conParams)
        elif domType in sd.BLOCK_DOMAIN_TYPES:
            return self.__disconnectiSCSIServer(conParams)
        else:
            raise se.InvalidParameterException("type", domType)

    def validate(self, domType, conList):
        """
        Validate that we can connect to a storage server.
        """
        self.log.info("Request to validate %s storage server", sd.type2name(domType))
        conParams = self.__validateConnectionParams(domType, conList)

        if domType == sd.NFS_DOMAIN:
            return self.__validateFileServer(conParams, fileUtils.FSTYPE_NFS)
        elif domType == sd.LOCALFS_DOMAIN:
            return self.__validateLocalConnection(conParams)
        elif domType in sd.BLOCK_DOMAIN_TYPES:
            return self.__validateiSCSIConnection(conParams)
        else:
            raise se.InvalidParameterException("type", domType)

    def __connectFileServer(self, conParams, fsType):
        """
        Connect to a storage low level entity.
        """
        conStatus = []
        localPath = os.path.join(self.storage_repository, sd.DOMAIN_MNT_POINT)
        fileUtils.createdir(localPath)

        for con in conParams:
            try:
                mntPoint = fileUtils.transformPath(con['rp'])
                mntPath = os.path.join(localPath, mntPoint)

                if fsType == fileUtils.FSTYPE_NFS:
                    # Stale handle usually resolves itself when doing directory lookups
                    # BUT if someone deletes the export on the servers side. We will keep
                    # getting stale handles and this is unresolvable unless you umount and
                    # remount.
                    if oop.fileUtils.isStaleHandle(mntPath):
                        # A VM might be holding a stale handle, we have to umount
                        # but we can't umount as long as someone is holding a handle
                        # even if it's stale. We use lazy so we can at least recover.
                        # Processes having an open file handle will not recover until
                        # they reopen the files.
                        oop.fileUtils.umount(con['rp'], mntPath, lazy=True)

                fileUtils.createdir(mntPath)

                rc = oop.fileUtils.mount(con['rp'], mntPath, fsType)
                if rc == 0:
                    try:
                        validateDirAccess(mntPath)
                    except se.StorageServerAccessPermissionError, ex:
                        self.log.debug("Unmounting file system %s "
                            "(not enough access permissions)" % con['rp'])
                        oop.fileUtils.umount(con['rp'], mntPath, fsType)
                        raise
                else:
                    self.log.error("Error during storage connection: rc=%s", rc, exc_info=True)
                    rc = se.StorageServerConnectionError.code

            # We should return error status instead of exception itself
            except se.StorageException, ex:
                rc = ex.code
                self.log.error("Error during storage connection: %s", str(ex), exc_info=True)
            except Exception, ex:
                rc = se.StorageServerConnectionError.code
                self.log.error("Error during storage connection: %s", str(ex), exc_info=True)

            conStatus.append({'id':con['cid'], 'status': rc})
        return conStatus

    def __connectLocalConnection(self, conParams):
        """
        Connect to a storage low level entity.
        """
        conStatus = []
        localPath = os.path.join(self.storage_repository, sd.DOMAIN_MNT_POINT)
        fileUtils.createdir(localPath)

        for con in conParams:
            rc = 0
            try:
                if os.path.exists(con['rp']):
                    lnPoint = fileUtils.transformPath(con['rp'])
                    lnPath = os.path.join(localPath, lnPoint)
                    if not os.path.lexists(lnPath):
                        os.symlink(con['rp'], lnPath)
                else:
                    self.log.error("Path %s does not exists.", con['rp'])
                    rc = se.StorageServerConnectionError.code
            except se.StorageException, ex:
                rc = ex.code
                self.log.error("Error during storage connection: %s", str(ex), exc_info=True)
            except Exception, ex:
                rc = se.StorageServerConnectionError.code
                self.log.error("Error during storage connection: %s", str(ex), exc_info=True)

            conStatus.append({'id':con['cid'], 'status': rc})
        return conStatus

    def __connectiSCSIServer(self, conParams):
        """
        Connect to a storage low level entity (server).
        """
        conStatus = []

        for con in conParams:
            try:
                if not con['iqn']:
                    iscsi.addiSCSIPortal(con['ip'], con['port'],
                        con['initiatorName'], con['user'], con['password'])[0]
                else:
                    iscsi.addiSCSINode(con['ip'], con['port'], con['iqn'],
                        con['tpgt'], con['initiatorName'],
                        con['user'], con['password'])

                rc = 0
            # We should return error status instead of exception itself
            except se.StorageException, ex:
                rc = ex.code
                self.log.error("Error during storage connection: %s", str(ex), exc_info=True)
            except Exception, ex:
                rc = se.StorageServerConnectionError.code
                self.log.error("Error during storage connection: %s", str(ex), exc_info=True)

            conStatus.append({'id':con['cid'], 'status':rc})

        return conStatus

    def __validateFileServer(self, conParams, fsType):
        """
        Validate that we can connect to a storage server.
        """
        conStatus = []

        for con in conParams:
            try:
                mountpoint = tempfile.mkdtemp()
                try:
                    rc = oop.fileUtils.mount(con['rp'], mountpoint, fsType)
                    if rc == 0:
                        try:
                            validateDirAccess(mountpoint)
                        except se.StorageServerAccessPermissionError, ex:
                            rc = ex.code
                    else:
                        self.log.error("Error during storage connection validation: rc=%s", rc, exc_info=True)
                        rc = se.StorageServerValidationError.code
                finally:
                    try:
                        oop.fileUtils.umount(con['rp'], mountpoint, fsType)
                    finally:
                        oop.os.rmdir(mountpoint)

            except se.StorageException, ex:
                rc = ex.code
                self.log.error("Error during storage connection validation: %s", str(ex), exc_info=True)
            except Exception, ex:
                rc = se.StorageServerValidationError.code
                self.log.error("Error during storage connection validation: %s", str(ex), exc_info=True)

            conStatus.append({'id':con['cid'], 'status': rc})
        return conStatus

    def __validateLocalConnection(self, conParams):
        """
        Validate that we can connect to a storage server.
        """
        conStatus = []

        for con in conParams:
            rc = 0
            try:
                if os.path.exists(con['rp']):
                    validateDirAccess(con['rp'])
                else:
                    self.log.error("Path %s does not exists.", con['rp'])
                    rc = se.StorageServerValidationError.code
            except se.StorageException, ex:
                rc = ex.code
                self.log.error("Error during storage connection validation: %s", str(ex), exc_info=True)
            except Exception, ex:
                rc = se.StorageServerValidationError.code
                self.log.error("Error during storage connection validation: %s", str(ex), exc_info=True)

            conStatus.append({'id':con['cid'], 'status': rc})

        return conStatus

    def __validateiSCSIConnection(self, conParams):
        """
        Validate that we can connect to a storage server.
        """
        conStatus = []

        for con in conParams:
            #iscsi.addiSCSIPortal('localhost')  ##FIXME
            conStatus.append({'id':con['cid'], 'status':0})

        return conStatus

    def __disconnectFileServer(self, conParams, fsType):
        """
        Disconnect from a storage low level entity (server).
        """
        conStatus = []

        for con in conParams:
            try:
                localPath = os.path.join(self.storage_repository, sd.DOMAIN_MNT_POINT)
                mntPoint = fileUtils.transformPath(con['rp'])
                mntPath = os.path.join(localPath, mntPoint)

                rc = oop.fileUtils.umount(con['rp'], mntPath, fsType)
                if rc == 0:
                    try:
                        oop.os.rmdir(mntPath)
                    except (OSError, Timeout):
                        # Report the error to the log, but keep going,
                        # afterall we succeeded to disconnect the FS server
                        msg = ("Cannot remove mountpoint after umount()")
                        self.log.warning(msg, exc_info=True)

                else:
                    self.log.error("Error during storage disconnection: rc=%s", rc, exc_info=True)
                    rc = se.StorageServerDisconnectionError.code

            # We should return error status instead of exception itself
            except se.StorageException, ex:
                rc = ex.code
                self.log.error("Error during storage disconnection: %s", str(ex), exc_info=True)
            except Exception, ex:
                rc = se.StorageException.code
                self.log.error("Error during storage disconnection: %s", str(ex), exc_info=True)

            conStatus.append({'id':con['cid'], 'status': rc})
        return conStatus

    def __disconnectLocalConnection(self, conParams):
        """
        Disconnect from a storage low level entity (server).
        """
        conStatus = []
        localPath = os.path.join(self.storage_repository, sd.DOMAIN_MNT_POINT)

        for con in conParams:
            rc = 0
            try:
                lnPoint = fileUtils.transformPath(con['rp'])
                lnPath = os.path.join(localPath, lnPoint)
                if os.path.lexists(lnPath):
                    os.unlink(lnPath)
            except se.StorageException, ex:
                rc = ex.code
                self.log.error("Error during storage disconnection: %s", str(ex), exc_info=True)
            except Exception, ex:
                rc = se.StorageServerDisconnectionError.code
                self.log.error("Error during storage disconnection: %s", str(ex), exc_info=True)

            conStatus.append({'id':con['cid'], 'status': rc})
        return conStatus

    def __disconnectiSCSIServer(self, conParams):
        """
        Disconnect from a storage low level entity (server).
        """
        conStatus = []

        for con in conParams:
            try:
                if not con['iqn']:
                    iscsi.remiSCSIPortal(con['ip'], con['port'])
                else:
                    iscsi.remiSCSINode(con['ip'], con['port'], con['iqn'], con['tpgt'])

                rc = 0
            # We should return error status instead of exception itself
            except se.StorageException, ex:
                rc = ex.code
                self.log.error("Error during storage disconnection: %s", str(ex), exc_info=True)
            except Exception, ex:
                rc = se.StorageException.code
                self.log.error("Error during storage disconnection: %s", str(ex), exc_info=True)

            conStatus.append({'id':con['cid'], 'status':rc})

        return conStatus
