#
# Copyright 2009-2011 Red Hat, Inc.
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

import os
from config import config
import logging
import tempfile

import iscsi
import fileUtils
import sd
import storage_exception as se
import outOfProcess as oop
import supervdsm
import constants
import mount

CON_TIMEOUT = config.getint("irs", "process_pool_timeout")

getProcPool = oop.getGlobalProcPool

def validateDirAccess(dirPath):
    getProcPool().fileUtils.validateAccess(dirPath)
    supervdsm.getProxy().validateAccess(constants.QEMU_PROCESS_USER,
            (constants.DISKIMAGE_GROUP, constants.METADATA_GROUP), dirPath,
            (os.R_OK | os.X_OK))

PARAMS_FILE_DOMAIN = (('cid', 'id'), ('rp', 'connection'))
PARAMS_BLOCK_DOMAIN = (
        ('cid', 'id'),
        ('ip', 'connection'),
        ('iqn', 'iqn'),
        ('tpgt', 'portal'),
        ('user', 'user'),
        ('password', 'password'),
        ('port', 'port'),
        ('initiatorName', 'initiatorName', None))

class StorageServerConnection:
    log = logging.getLogger('Storage.ServerConnection')
    storage_repository = config.get('irs', 'repository')

    def __validateConnectionParams(self, domType, conList):
        """
        Validate connection parameters
        """
        conParamsList = []

        if domType in sd.FILE_DOMAIN_TYPES:
            paramInfos = PARAMS_FILE_DOMAIN
        elif domType in sd.BLOCK_DOMAIN_TYPES:
            paramInfos = PARAMS_BLOCK_DOMAIN
        else:
            raise se.InvalidParameterException("type", domType)

        for con in conList:
            conParams = {}
            for paramInfo in paramInfos:
                conParamName, paramName = paramInfo[:2]
                hasDefault = len(paramInfo) > 2
                try:
                    if hasDefault:
                        value = con.get(paramName, paramInfo[2])
                    else:
                        value = con[paramName]

                    conParams[conParamName] = value
                except KeyError:
                    raise se.InvalidParameterException(paramName, 'parameter is missing from connection info %s' % (con.get('id', "")))

            conParamsList.append(conParams)

        return conParamsList

    def connect(self, domType, conList):
        """
        Connect to a storage low level entity (server).
        """
        self.log.info("Request to connect %s storage server", sd.type2name(domType))
        conParams = self.__validateConnectionParams(domType, conList)

        if domType == sd.NFS_DOMAIN:
            func = self.__connectFileServer
        elif domType == sd.LOCALFS_DOMAIN:
            func = self.__connectLocalConnection
        elif domType in sd.BLOCK_DOMAIN_TYPES:
            func = self.__connectiSCSIServer
        else:
            raise se.InvalidParameterException("type", domType)

        return self.__processConnections(func, conParams,
                se.StorageServerConnectionError.code)


    def __processConnections(self, func, conParams,
            defaultErrCode=se.StorageServerConnectionError.code):
        conStatus = []
        for con in conParams:
            try:
                rc = func(con)
            # We should return error status instead of exception itself
            except se.StorageException, ex:
                rc = ex.code
                self.log.error("Error during storage connection operation:", exc_info=True)
            except Exception, ex:
                rc = defaultErrCode
                self.log.error("Error during storage connection operation:", exc_info=True)

            conStatus.append({'id':con['cid'], 'status': rc})

        return conStatus



    def disconnect(self, domType, conList):
        """
        Disconnect from a storage low level entity (server).
        """
        self.log.info("Request to disconnect %s storage server", sd.type2name(domType))
        conParams = self.__validateConnectionParams(domType, conList)

        if domType == sd.NFS_DOMAIN:
            func = self.__disconnectFileServer
        elif domType == sd.LOCALFS_DOMAIN:
            func = self.__disconnectLocalConnection
        elif domType in sd.BLOCK_DOMAIN_TYPES:
            func = self.__disconnectiSCSIServer
        else:
            raise se.InvalidParameterException("type", domType)

        return self.__processConnections(func, conParams,
                se.StorageServerDisconnectionError.code)

    def validate(self, domType, conList):
        """
        Validate that we can connect to a storage server.
        """
        self.log.info("Request to validate %s storage server", sd.type2name(domType))
        conParams = self.__validateConnectionParams(domType, conList)

        if domType == sd.NFS_DOMAIN:
            func = self.__validateFileServer
        elif domType == sd.LOCALFS_DOMAIN:
            func = self.__validateLocalConnection
        elif domType in sd.BLOCK_DOMAIN_TYPES:
            func = self.__validateiSCSIConnection
        else:
            raise se.InvalidParameterException("type", domType)

        return self.__processConnections(func, conParams,
                se.StorageServerValidationError.code)

    def __connectFileServer(self, con):
        """
        Connect to a storage low level entity.
        """
        localPath = os.path.join(self.storage_repository, sd.DOMAIN_MNT_POINT)
        fileUtils.createdir(localPath)

        mntPoint = fileUtils.transformPath(con['rp'])
        mntPath = os.path.join(localPath, mntPoint)

        rc = 0
        mnt = mount.Mount(con['rp'], mntPath)

        # Stale handle usually resolves itself when doing directory lookups
        # BUT if someone deletes the export on the servers side. We will keep
        # getting stale handles and this is unresolvable unless you umount and
        # remount.
        if getProcPool().fileUtils.isStaleHandle(mnt.fs_file):
            # A VM might be holding a stale handle, we have to umount
            # but we can't umount as long as someone is holding a handle
            # even if it's stale. We use lazy so we can at least recover.
            # Processes having an open file handle will not recover until
            # they reopen the files.
            mnt.umount(lazy=True)

        fileUtils.createdir(mnt.fs_file)

        try:
            if not mnt.isMounted():
                mnt.mount(fileUtils.NFS_OPTIONS, mount.VFS_NFS, timeout=CON_TIMEOUT)
        except mnt.MountError:
            self.log.error("Error during storage connection", exc_info=True)
            rc = se.StorageServerConnectionError.code

        try:
            validateDirAccess(mnt.fs_file)
        except se.StorageServerAccessPermissionError:
            self.log.debug("Unmounting file system %s "
                "(not enough access permissions)", con['rp'])
            mnt.umount(timeout=CON_TIMEOUT)
            raise

        return rc

    def __connectLocalConnection(self, con):
        """
        Connect to a storage low level entity.
        """
        localPath = os.path.join(self.storage_repository, sd.DOMAIN_MNT_POINT)
        fileUtils.createdir(localPath)

        rc = 0
        if os.path.exists(con['rp']):
            lnPoint = fileUtils.transformPath(con['rp'])
            lnPath = os.path.join(localPath, lnPoint)
            if not os.path.lexists(lnPath):
                os.symlink(con['rp'], lnPath)
        else:
            self.log.error("Path %s does not exists.", con['rp'])
            rc = se.StorageServerConnectionError.code

        return rc

    def __connectiSCSIServer(self, con):
        """
        Connect to a storage low level entity (server).
        """
        if not con['iqn']:
            iscsi.addiSCSIPortal(con['ip'], con['port'],
                con['initiatorName'], con['user'], con['password'])[0]
        else:
            iscsi.addiSCSINode(con['ip'], con['port'], con['iqn'],
                con['tpgt'], con['initiatorName'],
                con['user'], con['password'])
        return 0

    def __validateFileServer(self, con):
        """
        Validate that we can connect to a storage server.
        """
        rc = 0
        mountpoint = tempfile.mkdtemp()
        mnt = mount.Mount(con['rp'], mountpoint)
        try:
            try:
                mnt.mount(fileUtils.NFS_OPTIONS, mount.VFS_NFS)
            except mount.MountError:
                self.log.error("Error during storage connection validation", exc_info=True)
                return se.StorageServerValidationError.code

            try:
                validateDirAccess(mountpoint)
            except se.StorageServerAccessPermissionError, ex:
                rc = ex.code
        finally:
            try:
                if mnt.isMounted():
                    mnt.umount(timeout=CON_TIMEOUT)
            except mount.MountError:
                self.log.error("Error cleaning mount %s", mnt)
            finally:
                getProcPool().os.rmdir(mnt.fs_file)

        return rc

    def __validateLocalConnection(self, con):
        """
        Validate that we can connect to a storage server.
        """
        rc = 0
        if os.path.exists(con['rp']):
            validateDirAccess(con['rp'])
        else:
            self.log.error("Path %s does not exists.", con['rp'])
            rc = se.StorageServerValidationError.code

        return rc

    def __validateiSCSIConnection(self, con):
        """
        Validate that we can connect to a storage server.
        """
        return 0

    def __getConnectionDictMount(self, con):
        localPath = os.path.join(self.storage_repository, sd.DOMAIN_MNT_POINT)
        mntPoint = fileUtils.transformPath(con['rp'])
        mntPath = os.path.join(localPath, mntPoint)

        return mount.Mount(con['rp'], mntPath)

    def __disconnectFileServer(self, con):
        """
        Disconnect from a storage low level entity (server).
        """
        try:
            rc = 0
            mnt = self.__getConnectionDictMount(con)
            if mnt.isMounted():
                mnt.umount(timeout=CON_TIMEOUT)
        except mount.MountError:
            self.log.error("Error during storage disconnection", exc_info=True)
            return se.StorageServerDisconnectionError.code

        try:
            os.rmdir(mnt.fs_file)
        except OSError:
            # Report the error to the log, but keep going,
            # afterall we succeeded in disconnecting the NFS server
            self.log.warning("Cannot remove mountpoint after umount()", exc_info=True)

        return rc


    def __disconnectLocalConnection(self, con):
        """
        Disconnect from a storage low level entity (server).
        """
        localPath = os.path.join(self.storage_repository, sd.DOMAIN_MNT_POINT)

        lnPoint = fileUtils.transformPath(con['rp'])
        lnPath = os.path.join(localPath, lnPoint)
        if os.path.lexists(lnPath):
            os.unlink(lnPath)

        return 0

    def __disconnectiSCSIServer(self, con):
        """
        Disconnect from a storage low level entity (server).
        """
        if not con['iqn']:
            iscsi.remiSCSIPortal(con['ip'], con['port'])
        else:
            iscsi.remiSCSINode(con['ip'], con['port'], con['iqn'], con['tpgt'])
        return 0

