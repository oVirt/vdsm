#
# Copyright 2009 Red Hat, Inc. and/or its affiliates.
#
# Licensed to you under the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.  See the files README and
# LICENSE_GPL_v2 which accompany this distribution.
#

import os
from glob import glob
import logging
import time
import threading
import errno
import uuid
import codecs

import constants
import storage_mailbox
import blockSD
import sd
from blockSD import SD_METADATA_SIZE
import misc
from misc import Event
import fileUtils
from config import config
from sdf import StorageDomainFactory as SDF
import storage_exception as se
from persistentDict import DictValidator
from processPool import Timeout

BLANK_POOL_UUID = '00000000-0000-0000-0000-000000000000'

POOL_MASTER_DOMAIN = 'mastersd'

MAX_POOL_DESCRIPTION_SIZE = 50

PMDK_DOMAINS = "POOL_DOMAINS"
PMDK_POOL_DESCRIPTION = "POOL_DESCRIPTION"
PMDK_LVER = "POOL_SPM_LVER"
PMDK_SPM_ID = "POOL_SPM_ID"
PMDK_MASTER_VER = "MASTER_VERSION"

def domainListEncoder(domDict):
    domains = ','.join([ '%s:%s' % (k, v) for k, v in domDict.iteritems()])
    return domains

def domainListDecoder(s):
    domList = {}
    if not s:
        return domList
    for domDecl in s.split(","):
        k, v = domDecl.split(':')
        domList[k.strip("'")] = v.strip("'").capitalize()
    return domList

SP_MD_FIELDS = {
        # Key          dec,  enc
        PMDK_DOMAINS : (domainListDecoder, domainListEncoder),
        PMDK_POOL_DESCRIPTION : (str, str), # should be decode\encode utf8
        PMDK_LVER : (int, str),
        PMDK_SPM_ID : (int, str),
        PMDK_MASTER_VER : (int, str)
    }

# Calculate how many domains can be in the pool before overflowing the Metadata
MAX_DOMAINS = SD_METADATA_SIZE - blockSD.METADATA_BASE_SIZE
MAX_DOMAINS -= MAX_POOL_DESCRIPTION_SIZE + sd.MAX_DOMAIN_DESCRIPTION_SIZE
MAX_DOMAINS -= blockSD.PVS_METADATA_SIZE
MAX_DOMAINS /= 48

class StatsThread(threading.Thread):
    log = logging.getLogger('Storage.StatsThread')
    onDomainConnectivityStateChange = Event("StatsThread.onDomainConnectivityStateChange")
    def __init__(self, func, sdUUID):
        """
        StatsThread gets two arguments on instatiation:
        func - function to call
        dom - argument to pass to func()
        """
        threading.Thread.__init__(self)
        self._statscache = dict(result=
            dict(code=200, lastCheck=0.0, delay='0', valid=True))
        self._statsdelay = config.getint('irs', 'sd_health_check_delay')
        self._statsletrun = True
        self._statsfunc = func
        self._sdUUID = sdUUID
        self._domain = None


    def run(self):
        while self._statsletrun:
            try:
                if self._domain is None:
                    self._domain = SDF.produce(self._sdUUID)
                stats, code = self._statsfunc(self._domain)
            except se.StorageException, e:
                self.log.error("Unexpected error", exc_info=True)
                code = e.code
            except Exception, e:
                self.log.error("Unexpected error", exc_info=True)
                code = 200

            delay = 0
            try:
                # This is handled seperatly because in case of this kind
                # of failure we don't want to print stack trace
                delay = self._domain.getReadDelay()
            except Exception, e:
                self.log.error("Could not figure out delay for domain `%s` (%s)", self._sdUUID, e)
                code = 200

            if code != 0:
                self._domain = None

            finish = time.time()

            stats['finish'] = finish
            stats['result'] = dict(code=code, lastCheck=finish,
                delay=str(delay), valid=(code == 0))

            try:
                if self._statscache["result"]["valid"] != stats["result"]["valid"]:
                    self.onDomainConnectivityStateChange.emit(self._sdUUID, stats["result"]["valid"])
            except:
                self.log.error("Could not emit domain state event", exc_info=True)

            self._statscache.update(stats)

            count = 0
            while self._statsletrun and count < self._statsdelay:
                count += 1
                time.sleep(1)

        self._statsfunc = None


    def stop(self):
        self._statsletrun = False


    def getStatsResults(self):
        return self._statscache.copy()


class StoragePool:
    '''
    StoragePool object should be relatively cheap to construct. It should defer
    any heavy lifting activities until the time it is really needed.
    '''
    log = logging.getLogger('Storage.StoragePool')
    storage_repository = config.get('irs', 'repository')
    _poolsTmpDir = config.get('irs', 'pools_data_dir')
    lvExtendPolicy = config.get('irs', 'vol_extend_policy')

    def __init__(self, spUUID):
        self.spUUID = str(spUUID)
        self.poolPath = os.path.join(self.storage_repository, self.spUUID)
        self.id = None
        self.scsiKey = None
        self._poolFile = os.path.join(self._poolsTmpDir, self.spUUID)
        self.hsmMailer = None
        self.spmMailer = None
        self.masterDomain = None
        self.repostats = {}

    def __del__(self):
        if len(self.repostats) > 0:
            threading.Thread(target=self.disconnectDomains).start()

    def __createMailboxMonitor(self):
        # Currently mailbox is not needed for non block device sd's
        if self.hsmMailer:
            return

        if isinstance(self.masterDomain, blockSD.BlockStorageDomain) and self.lvExtendPolicy == "ON":
            self.hsmMailer = storage_mailbox.HSM_Mailbox(self.id, self.spUUID)

    def __cleanupDomains(self, domlist, msdUUID, masterVersion):
        """
        Clean up domains after failed Storage Pool creation
        domlist - comma separated list of sdUUIDs
        """
        # Go through all the domains and detach them from the pool
        # Since something went wrong (otherwise why would we be cleaning
        # the mess up?) do not expect all the domains to exist
        domains = [SDF.produce(d) for d in domlist]
        for d in domains:
            try:
                self.detachSD(d, msdUUID, masterVersion)
            except Exception:
                self.log.error("Unexpected error", exc_info=True)
        self.refresh()

    def getMasterVersion(self):
        return self.getMetaParam(PMDK_MASTER_VER)

    def acquireClusterLock(self):
        msd = self.getMasterDomain()
        msd.acquireClusterLock(self.id)

    def releaseClusterLock(self):
        self.getMasterDomain().releaseClusterLock()

    def validateAttachedDomain(self, sdUUID):
        domList = self.getDomains()
        if sdUUID not in domList:
            raise se.StorageDomainNotInPool(self.spUUID, sdUUID)
        # Avoid handle domains if not owned by pool
        dom = SDF.produce(sdUUID)
        pools = dom.getPools()
        if self.spUUID not in pools:
            raise se.StorageDomainNotInPool(self.spUUID, sdUUID)


    def validatePoolMVerHigher(self, masterVersion):
        """
        Make sure the masterVersion higher than that of the pool.

        :param masterVersion: the master version you want to validate
        :type masterVersion: int

        :raises: :exc:`storage_exception.StoragePoolWrongMasterVersion`
            exception if masterVersion doesn't follow the rules

        """
        d = self.getMasterDomain()
        mver = self.getMasterVersion()
        if not int(masterVersion) > mver:
            raise se.StoragePoolWrongMaster(self.spUUID, d.sdUUID)


    def getMaximumSupportedDomains(self):
        msdType = sd.name2type(self.getMasterDomain().getInfo()["type"])
        msdVersion = int(self.getMasterDomain().getInfo()["version"])
        if msdType in sd.BLOCK_DOMAIN_TYPES and msdVersion in blockSD.VERS_METADATA_LV:
            return MAX_DOMAINS
        else:
            return config.getint("irs", "maximum_domains_in_pool")

    def create(self, poolName, msdUUID, domList, masterVersion, safeLease):
        """
        Create new storage pool with single/multiple image data domain.
        The command will create new storage pool meta-data attach each
        storage domain to that storage pool.
        At least one data (images) domain must be provided
         'poolName' - storage pool name
         'msdUUID' - master domain of this pool (one of domList)
         'domList' - list of domains (i.e sdUUID,sdUUID,...,sdUUID)
        """
        self.log.info("spUUID=%s poolName=%s master_sd=%s "
                      "domList=%s masterVersion=%s %s",
                      self.spUUID, poolName, msdUUID,
                      domList, masterVersion, str(safeLease))

        if msdUUID not in domList:
            raise se.InvalidParameterException("masterDomain", msdUUID)

        # Check the domains before pool creation
        for dom in domList:
            try:
                domain = SDF.produce(dom)
                domain.validate()
            except se.StorageException:
                self.log.error("Unexpected error", exc_info=True)
                raise se.StorageDomainAccessError(dom)

            # Validate unattached domains
            if not domain.isISO():
                domain.invalidateMetadata()
                spUUIDs = domain.getPools()
                # Non ISO domains have only 1 pool
                if len(spUUIDs) > 0:
                    raise se.StorageDomainAlreadyAttached(spUUIDs[0], dom)

        fileUtils.createdir(self.poolPath)

        try:
            # Seeing as we are just creating the pool then the host doesn't
            # have an assigned Id for this pool.  When locking the domain we must use an Id
            self.id = 1000
            # Master domain is unattached and all changes to unattached domains
            # must be performed under storage lock
            msd = SDF.produce(msdUUID)
            msd.changeLeaseParams(safeLease)
            msd.acquireClusterLock(self.id)
        except:
            self.id = None
            raise
        try:
            try:
                # Mark 'master' domain
                # We should do it before actually attaching this domain to the pool.
                # During 'master' marking we create pool metadata and each attached
                # domain should register there
                self.createMaster(poolName, msd, masterVersion, safeLease)
                self.__rebuild(msdUUID=msdUUID, masterVersion=masterVersion)
                # Attach storage domains to the storage pool
                # Since we are creating the pool then attach is done from the hsm and not the spm
                # therefore we must manually take the master domain lock
                # TBD: create will receive only master domain and further attaches should be done
                # under SPM

                # Master domain was already attached (in createMaster),
                # no need to reattach
                for sdUUID in domList:
                    # No need to attach the master
                    if sdUUID == msdUUID:
                        continue

                    self.attachSD(sdUUID)
            except Exception:
                self.log.error("Create domain canceled due to an unexpected error", exc_info=True)

                try:
                    fileUtils.cleanupdir(self.poolPath)
                    self.__cleanupDomains(domList, msdUUID, masterVersion)
                except:
                    self.log.error("Cleanup failed due to an unexpected error", exc_info=True)

                raise
        finally:
            msd.releaseClusterLock()
            self.id = None

        self.disconnectDomains()
        return True

    def _saveReconnectInformation(self, hostID, scsiKey, msdUUID, masterVersion):
        pers = ["id=%d\n" % hostID]
        pers.append("scsiKey=%s\n" % scsiKey)
        pers.append("sdUUID=%s\n" % msdUUID)
        pers.append("version=%s\n" % masterVersion)
        with open(self._poolFile, "w") as f:
            f.writelines(pers)


    def connect(self, hostID, scsiKey, msdUUID, masterVersion):
        """
        Connect a Host to a specific storage pool.

        Caller must acquire resource Storage.spUUID so that this method would never be called twice concurrently.
        """
        self.log.info("Connect host #%s to the storage pool %s with master domain: %s (ver = %s)" %
            (hostID, self.spUUID, msdUUID, masterVersion))

        if not os.path.exists(self._poolsTmpDir):
            msg = ("StoragePoolConnectionError for hostId: %s, on poolId: %s," +
                   " Pools temp data dir: %s does not exist" %
                    (hostID, self.spUUID, self._poolsTmpDir))
            self.log.error(msg)
            msg = "Pools temp data dir: %s does not exist" % (self._poolsTmpDir)
            raise se.StoragePoolConnectionError(msg)

        if os.path.exists(self._poolFile):
            os.unlink(self._poolFile)

        self._saveReconnectInformation(hostID, scsiKey, msdUUID, masterVersion)
        self.id = hostID
        self.scsiKey = scsiKey
        # Make sure SDCache doesn't have stale data (it can be in case of FC)
        SDF.refresh()
        # Rebuild whole Pool
        self.__rebuild(msdUUID=msdUUID, masterVersion=masterVersion)
        self.__createMailboxMonitor()

        return True


    def disconnectDomains(self):
        for sdUUID in self.repostats.keys():
            self.stopRepoStats(sdUUID)
        return True


    def disconnect(self):
        """
        Disconnect a Host from specific storage pool.

        Caller must acquire resource Storage.spUUID so that this method would never be called twice concurrently.
        """
        self.log.info("Disconnect from the storage pool %s", self.spUUID)

        self.id = None
        self.scsiKey = None
        if os.path.exists(self._poolFile):
            os.unlink(self._poolFile)

        if self.hsmMailer:
            self.hsmMailer.stop()
            self.hsmMailer = None

        # Remove all links
        if os.path.exists(self.poolPath):
            fileUtils.cleanupdir(self.poolPath)

        self.disconnectDomains()

        return True


    def reconnect(self):
        self.log.info("Trying to reconnect to pool: %s" % self.spUUID)
        try:
            file = open(self._poolFile, "r")
            for line in file:
                pair = line.strip().split("=")
                if len(pair) == 2:
                    if pair[0] == "id":
                        hostId = int(pair[1])
                    elif pair[0] == "scsiKey":
                        scsiKey = pair[1]
                    elif pair[0] == "sdUUID":
                        msdUUID = pair[1]
                    elif pair[0] == "version":
                        masterVersion = pair[1]
            file.close()
            if not (hostId and scsiKey and msdUUID and masterVersion):
                os.unlink(self._poolFile)
                return False
            if self.connect(hostId, scsiKey, msdUUID, masterVersion):
                return True
        except:
            self.log.error("RECONNECT: Failed: %s", self.spUUID, exc_info=True)
            os.unlink(self._poolFile)
            raise


    def createMaster(self, poolName, domain, masterVersion, leaseParams):
        """
        Create a fresh master file system directory tree
        """
        # THIS METHOD MUST BE RUN UNDER DOMAIN STORAGE LOCK
        self.log.info("setting master domain for spUUID %s on sdUUID=%s", self.spUUID, domain.sdUUID)
        futurePoolMD = self._getPoolMD(domain)
        with futurePoolMD.transaction():
            domain.changeLeaseParams(leaseParams)
            for spUUID in domain.getPools():
                if spUUID != self.spUUID:
                    self.log.warn("Force detaching from pool `%s` because of reconstruct master", spUUID)
                    domain.detach(spUUID)
            domain.attach(self.spUUID)
            domain.changeRole(sd.MASTER_DOMAIN)

            futurePoolMD.update({
            PMDK_SPM_ID: -1,
            PMDK_LVER: -1,
            PMDK_MASTER_VER: masterVersion,
            PMDK_POOL_DESCRIPTION: poolName,
            PMDK_DOMAINS: {domain.sdUUID: sd.DOM_ACTIVE_STATUS}})

    def reconstructMaster(self, poolName, msdUUID, domDict, masterVersion, safeLease):
        self.log.info("spUUID=%s poolName=%s"
                      " master_sd=%s domDict=%s masterVersion=%s "
                      "leaseparams=(%s)",
                      self.spUUID, poolName, msdUUID, domDict, masterVersion,
                      str(safeLease))

        if msdUUID not in domDict:
            raise se.InvalidParameterException("masterDomain", msdUUID)

        try:
            # Seeing as we are just creating the pool then the host doesn't
            # have an assigned Id for this pool.  When locking the domain we must use an Id
            self.id = 1000
            # Master domain is unattached and all changes to unattached domains
            # must be performed under storage lock
            futureMaster = SDF.produce(msdUUID)
            futureMaster.changeLeaseParams(safeLease)
            futureMaster.acquireClusterLock(self.id)
            try:
                self.createMaster(poolName, futureMaster, masterVersion, safeLease)
                self.refresh(msdUUID=msdUUID, masterVersion=masterVersion)

                # TBD: Run full attachSD?
                domains = self.getDomains()
                for sdUUID in domDict:
                    domains[sdUUID] = domDict[sdUUID].capitalize()
                # Add domain to domain list in pool metadata
                self.setMetaParam(PMDK_DOMAINS, domains)
                self.log.info("Set storage pool domains: %s", domains)
            finally:
                futureMaster.releaseClusterLock()
        finally:
            self.id = None


    def copyPoolMD(self, prevMd, newMD):
        prevPoolMD = self._getPoolMD(prevMd)
        domains = prevPoolMD[PMDK_DOMAINS]
        pool_descr = prevPoolMD[PMDK_POOL_DESCRIPTION]
        lver = prevPoolMD[PMDK_LVER]
        spmId = prevPoolMD[PMDK_SPM_ID]
        # This is actually domain metadata, But I can't change this because of
        # backward compatibility
        leaseParams = prevMd.getLeaseParams()

        # Now insert pool metadata into new mastersd metadata

        newPoolMD = self._getPoolMD(newMD)
        with newPoolMD.transaction():
            newPoolMD.update({PMDK_DOMAINS: domains,
                PMDK_POOL_DESCRIPTION: pool_descr,
                PMDK_LVER: lver,
                PMDK_SPM_ID: spmId})
            newMD.changeLeaseParams(leaseParams)

    def __masterMigrate(self, sdUUID, msdUUID, masterVersion):
        curmsd = SDF.produce(sdUUID)
        newmsd = SDF.produce(msdUUID)
        curmsd.invalidateMetadata()
        newmsd.upgrade(curmsd.getVersion())

        # new 'master' should be in 'active' status
        domList = self.getDomains()
        if msdUUID not in domList:
            raise se.StorageDomainNotInPool(self.spUUID, msdUUID)
        if domList[msdUUID] != sd.DOM_ACTIVE_STATUS:
            raise se.StorageDomainNotActive(msdUUID)
        if newmsd.isISO():
            raise se.IsoCannotBeMasterDomain(msdUUID)
        if newmsd.isBackup():
            raise se.BackupCannotBeMasterDomain(msdUUID)

        # Copy master file system content to the new master
        src = os.path.join(curmsd.domaindir, sd.MASTER_FS_DIR)
        dst = os.path.join(newmsd.domaindir, sd.MASTER_FS_DIR)

        # Mount new master file system
        newmsd.mountMaster()
        # Make sure there is no cruft left over
        for dir in [newmsd.getVMsDir(), newmsd.getTasksDir()]:
            fileUtils.cleanupdir(dir)

        cmd = ["%s cf - --exclude=lost+found -C %s . | %s xf - -C %s" % (constants.EXT_TAR, src, constants.EXT_TAR, dst)]
        rc = misc.execCmd(cmd, sudo=False, shell=True)[0]

        if rc:
            # Failed to copy the master data
            try:
                newmsd.unmountMaster()
            except Exception:
                self.log.error("Unexpected error", exc_info=True)
            raise se.StorageDomainMasterCopyError(msdUUID)

        self.copyPoolMD(curmsd, newmsd)

        path = newmsd.getMDPath()
        if not path:
            newmsd.unmountMaster()
            raise se.StorageDomainLayoutError("domain", msdUUID)

        # Acquire safelease lock on new master
        try:
            # Reset SPM lock because of the host still SPM
            # It will speedup new lock acquiring
            newmsd.initSPMlease()
            newmsd.acquireClusterLock(self.id)
        except Exception:
            self.log.error("Unexpected error", exc_info=True)
            newmsd.releaseClusterLock()
            newmsd.unmountMaster()
            raise
        self.log.debug("masterMigrate - lease acquired successfully")

        try:
            # Now mark new domain as 'master'
            # if things break down here move the master back pronto
            newPoolMD = self._getPoolMD(newmsd)
            with newPoolMD.transaction():
                newPoolMD[PMDK_MASTER_VER] = masterVersion
                newmsd.changeRole(sd.MASTER_DOMAIN)
            self._saveReconnectInformation(self.id, self.scsiKey, newmsd.sdUUID, masterVersion)
        except Exception:
            self.log.error("Unexpected error", exc_info=True)
            newmsd.releaseClusterLock()
            newmsd.unmountMaster()
            raise

        # From this point on we have a new master and should not fail
        try:
            # Now recreate 'mastersd' link
            # we can use refresh() to do the job
            self.refresh(msdUUID, masterVersion)

            # From this point on there is a new master domain in the pool
            # Now that we are beyond the criticial point we can clean up things
            curmsd.changeRole(sd.REGULAR_DOMAIN)

            # Clean up the old data from previous master fs
            for dir in [curmsd.getVMsDir(), curmsd.getTasksDir()]:
                fileUtils.cleanupdir(dir)

            # NOTE: here we unmount the *previous* master file system !!!
            curmsd.unmountMaster()
        except Exception:
            self.log.error("Unexpected error", exc_info=True)

        try:
            # Release old lease
            curmsd.releaseClusterLock()
        except Exception:
            self.log.error("Unexpected error", exc_info=True)


    def __unmountLastMaster(self, sdUUID):
        curmsd = SDF.produce(sdUUID)
        # Check if it's last domain and allow it detaching
        dl = self.getDomains(activeOnly=True)
        domList = dl.keys()
        if curmsd.sdUUID in domList:
            domList.remove(curmsd.sdUUID)
        for item in domList:
            domain = SDF.produce(item)
            if domain.isData():
                # Failure, we have at least one more data domain
                # in the pool and one which can become 'master'
                raise se.StoragePoolHasPotentialMaster(item)
        curmsd.unmountMaster()

    def masterMigrate(self, sdUUID, msdUUID, masterVersion):
        self.log.info("sdUUID=%s spUUID=%s msdUUID=%s", sdUUID,  self.spUUID, msdUUID)

        # Check if we are migrating to or just unmounting last master
        if msdUUID != sd.BLANK_UUID:
            self.__masterMigrate(sdUUID, msdUUID, masterVersion)
            return False    # not last master

        self.__unmountLastMaster(sdUUID)
        return True     # last master

    def attachSD(self, sdUUID):
        """
        Attach a storage domain to the storage pool.
        This marks the storage domain as "attached" and links it
        to the storage pool
         'sdUUID' - storage domain UUID
        """
        self.log.info("sdUUID=%s spUUID=%s", sdUUID, self.spUUID)

        domains = self.getDomains()
        if sdUUID in domains:
            return True

        if len(domains) >= self.getMaximumSupportedDomains():
            raise se.TooManyDomainsInStoragePoolError()

        dom = SDF.produce(sdUUID)
        dom.acquireClusterLock(self.id)
        try:
            #If you remove this condition, remove it from public_createStoragePool too.
            if dom.isData() and (dom.getVersion() != self.masterDomain.getVersion()):
                raise se.MixedSDVersionError(dom.sdUUID, dom.getVersion(), self.masterDomain.sdUUID, self.masterDomain.getVersion())
            dom.attach(self.spUUID)
            domains[sdUUID] = sd.DOM_ATTACHED_STATUS
            self.setMetaParam(PMDK_DOMAINS, domains)
            self._refreshDomainLinks(dom)
        finally:
            dom.releaseClusterLock()
        self.updateMonitoringThreads()


    def forcedDetachSD(self, sdUUID):
        self.log.warn("Force detaching domain `%s`", sdUUID)
        domains = self.getDomains()
        if sdUUID not in domains:
            return True
        del domains[sdUUID]
        self.setMetaParam(PMDK_DOMAINS, domains)
        self._cleanupDomainLinks(sdUUID)
        self.updateMonitoringThreads()
        self.log.debug("Force detach for domain `%s` is done", sdUUID)

    def detachSD(self, sdUUID, msdUUID, masterVersion):
        """
        Detach a storage domain from a storage pool.
        This removes the storage domain entry in the storage pool meta-data
        and leaves the storage domain in 'unattached' status.
         'sdUUID' - storage domain UUID
         'msdUUID' - master storage domain UUID
         'masterVersion' - new master storage domain version
        """
        self.log.info("sdUUID=%s spUUID=%s msdUUID=%s", sdUUID,  self.spUUID, msdUUID)

        dom = SDF.produce(sdUUID)
        if dom.isISO():
            dom.acquireClusterLock(self.id)
        try:
            dom.invalidateMetadata()
            try:
                # Avoid detach domains if not owned by pool
                self.validateAttachedDomain(sdUUID)
                domList = self.getDomains()
                sd.validateSDStateTransition(sdUUID, domList[sdUUID], sd.DOM_UNATTACHED_STATUS)

                # If the domain being detached is the 'master', move all pool
                # metadata to the new 'master' domain (msdUUID)
                if sdUUID == self.getMasterDomain().sdUUID:
                    self.masterMigrate(sdUUID, msdUUID, masterVersion)

                # Remove pool info from domain metadata
                dom.detach(self.spUUID)

                # Remove domain from pool metadata
                del domList[sdUUID]
                self.setMetaParam(PMDK_DOMAINS, domList)
                self._cleanupDomainLinks(sdUUID)

                self.updateMonitoringThreads()
            except Exception:
                self.log.error("Unexpected error", exc_info=True)
        finally:
            if dom.isISO():
                dom.releaseClusterLock()


    def activateSD(self, sdUUID):
        """
        Activate a storage domain that is already a member in a storage pool.
        Validate that the storage domain is owned by the storage pool.
         'sdUUID' - storage domain UUID
        """
        self.log.info("sdUUID=%s spUUID=%s", sdUUID,  self.spUUID)

        # Avoid domain activation if not owned by pool
        self.validateAttachedDomain(sdUUID)
        domList = self.getDomains()
        dom = SDF.produce(sdUUID)
        sd.validateSDStateTransition(sdUUID, domList[sdUUID], sd.DOM_ACTIVE_STATUS)

        # Do nothing if already active
        if domList[sdUUID] == sd.DOM_ACTIVE_STATUS:
            return True

        if dom.getDomainClass() == sd.DATA_DOMAIN:
            dom.upgrade(self.getVersion())

        dom.activate()
        # set domains also do rebuild
        domList[sdUUID] = sd.DOM_ACTIVE_STATUS
        self.setMetaParam(PMDK_DOMAINS, domList)
        self._refreshDomainLinks(dom)
        self.updateMonitoringThreads()
        return True


    def deactivateSD(self, sdUUID, new_msdUUID, masterVersion):
        """
        Deactivate a storage domain.
        Validate that the storage domain is owned by the storage pool.
        Change storage domain status to "Attached" in the storage pool meta-data.

        :param sdUUID: The UUID of the storage domain you want to deactivate.
        :param new_msdUUID: The UUID of the new master storage domain.
        :param masterVersion: new master storage domain version
        """
        self.log.info("sdUUID=%s spUUID=%s new_msdUUID=%s", sdUUID,  self.spUUID, new_msdUUID)
        domList = self.getDomains()
        if sdUUID not in domList:
            raise se.StorageDomainNotInPool(self.spUUID, sdUUID)
        try:
            dom = SDF.produce(sdUUID)
            #Check that dom is really reachable and not a cached value.
            dom.validate(False)
        except se.StorageException:
            self.log.warn("deactivaing MIA domain `%s`", sdUUID, exc_info=True)
            if new_msdUUID != BLANK_POOL_UUID:
                #Trying to migrate master failed to reach actual msd.
                raise se.StorageDomainAccessError(sdUUID)
        else:
            if dom.isMaster():
                #Maybe there should be information in the exception that the UUID is
                #not invalid because of its format but because it is equal to the SD. Will be less confusing.
                #TODO: verify in masterMigrate().
                if sdUUID == new_msdUUID:
                    raise se.InvalidParameterException("new_msdUUID", new_msdUUID)
                self.masterMigrate(sdUUID, new_msdUUID, masterVersion)
            elif dom.isBackup():
                dom.unmountMaster()

        domList[sdUUID] = sd.DOM_ATTACHED_STATUS
        self.setMetaParam(PMDK_DOMAINS, domList)
        self.updateMonitoringThreads()

    def _linkStorageDomain(self, src, linkName):
        self.log.info("Linking %s to %s", src, linkName)
        try:
            current = os.readlink(linkName)
        except OSError, e:
            if e.errno != errno.ENOENT:
                self.log.error("Can't link SD %s to %s", src, linkName, exc_info=True)
                return
        else:
            if current == linkName:
                return #Nothing to do
        #Rebuid the link
        tmp_link_name = os.path.join(self.storage_repository, str(uuid.uuid4()))
        os.symlink(src, tmp_link_name)     #make tmp_link
        os.rename(tmp_link_name, linkName)


    def _cleanupDomainLinks(self, domain):
        linkPath = os.path.join(self.poolPath, domain)
        try:
            os.remove(linkPath)
        except (OSError, IOError):
            pass

    def _refreshDomainLinks(self, domain):
        domain.refreshDirTree()
        linkName = os.path.join(self.poolPath, domain.sdUUID)
        self._linkStorageDomain(domain.domaindir, linkName)
        if self.masterDomain.sdUUID == domain.sdUUID:
            masterName = os.path.join(self.poolPath, POOL_MASTER_DOMAIN)
            self._linkStorageDomain(domain.domaindir, masterName)
        else:
            domPoolMD = self._getPoolMD(domain)
            with domPoolMD.transaction():
                domain.changeRole(sd.REGULAR_DOMAIN)
                domPoolMD[PMDK_MASTER_VER] = 0


    def __rebuild(self, msdUUID, masterVersion):
        """
        Rebuild storage pool.
        """
        # master domain must be refreshed first
        msd = self.getMasterDomain(msdUUID=msdUUID, masterVersion=masterVersion)
        self.updateMonitoringThreads()

        fileUtils.createdir(self.poolPath)

        # Find out all domains for future cleanup
        domainpat = os.path.join(self.poolPath, constants.UUID_GLOB_PATTERN)
        cleanupdomains = glob(domainpat)

        # We should not rebuild non-active domains, because
        # they are probably disconnected from the host
        domUUIDs = self.getDomains(activeOnly=True).keys()

        #msdUUID should be present and active in getDomains result.
        #TODO: Consider remove if clause.
        if msdUUID in domUUIDs:
            domUUIDs.remove(msdUUID)

        for domUUID in domUUIDs:
            try:
                d = SDF.produce(domUUID)
            except se.StorageDomainDoesNotExist:
                # We should not rebuild a non-master active domain
                # if it is disconnected. Log the error and continue
                self.log.error("pool %s metadata contains an unknown domain %s", self.spUUID, domUUID, exc_info=True)
                continue

            try:
                self._refreshDomainLinks(d)
            except (se.StorageException, OSError):
                self.log.error("Can't refresh domain links", exc_info=True)
                continue
            # Remove domain from potential cleanup
            linkName = os.path.join(self.poolPath, domUUID)
            if linkName in cleanupdomains:
                cleanupdomains.remove(linkName)
        # Always try to build master links
        try:
            self._refreshDomainLinks(msd)
        except (se.StorageException, OSError):
            self.log.error("_refreshDomainLinks failed for master domain %s", msd.sdUUID, exc_info=True)
        linkName = os.path.join(self.poolPath, msd.sdUUID)
        if linkName in cleanupdomains:
            cleanupdomains.remove(linkName)

        # Clenup old trash from the pool
        for i in cleanupdomains:
            try:
                os.remove(i)
            except OSError as e:
                if e.errno != errno.ENOENT:
                    self.log.warn("Could not clean all trash from the pool dom `%s` (%s)", i, e)
            except Exception as e:
                    self.log.warn("Could not clean all trash from the pool dom `%s` (%s)", i, e)


    def refresh(self, msdUUID=None, masterVersion=None):
        """
        Refresh storage pool.
         'msdUUID' - master storage domain UUID
        """
        # Make sure the StorageDomainFactory has its internal cache refreshed
        SDF.refresh()
        self.__rebuild(msdUUID=msdUUID, masterVersion=masterVersion)


    def updateVM(self, vmList, sdUUID=None):
        """
        Update VMs.
         'vmList' - [{'vm':vmUUID,'ovf','imglist':'imgUUID1,imgUUID2,...'},...]
         'sdUUID' - target domain UUID, if not None, VM Images and the master tree
                    must be located on this domain.
                    If sdUUID is None, the update is on the pool, and therefore the
                    master domain will be updated.
        """
        if sdUUID is None:
            sdUUID = self.getMasterDomain().sdUUID

        self.log.info("spUUID=%s sdUUID=%s", self.spUUID, sdUUID)
        vms = self._getVMsPath(sdUUID)
        # We should exclude 'masterd' link from IMG_METAPATTERN globing
        vmUUID = ovf = imgList = ''
        for vm in vmList:
            if not vm:
                continue
            try:
                vmUUID = vm['vm']
                ovf = vm['ovf']
                imgList = vm['imglist'].split(',')
                self.log.info("vmUUID=%s imgList=%s", vmUUID, str(imgList))
            except KeyError:
                raise se.InvalidParameterException("vmList", str(vmList))

            vmPath = os.path.join(vms, vmUUID)
            if fileUtils.pathExists(vmPath):
                fileUtils.cleanupdir(vmPath, ignoreErrors = False)

            try:
                os.mkdir(vmPath)
                codecs.open(os.path.join(vmPath, vmUUID + '.ovf'), 'w',
                            encoding='utf8').write(ovf)
            except OSError, ex:
                if ex.errno == errno.ENOSPC:
                    raise se.NoSpaceLeftOnDomain(sdUUID)

                raise


    def removeVM(self, vmList, sdUUID=None):
        """
        Remove VMs.
         'vmList' - vmUUID1,vmUUID2,...
        """
        self.log.info("spUUID=%s vmList=%s sdUUID=%s", self.spUUID, str(vmList), sdUUID)
        vms = self._getVMsPath(sdUUID)
        vmUUIDs = vmList.split(',')
        for vm in vmUUIDs:
            if os.path.exists(os.path.join(vms, vm)):
                fileUtils.cleanupdir(os.path.join(vms, vm))


    def setDescription(self, descr):
        """
        Set storage pool description.
         'descr' - pool description
        """
        if len(descr) > MAX_POOL_DESCRIPTION_SIZE:
            raise se.StoragePoolDescriptionTooLongError()

        self.log.info("spUUID=%s descr=%s", self.spUUID, descr)
        self.setMetaParam(PMDK_POOL_DESCRIPTION, descr)


    def extendVolume(self, sdUUID, volumeUUID, size, isShuttingDown=None):
        SDF.produce(sdUUID).extendVolume(volumeUUID, size, isShuttingDown)

    @classmethod
    def _getPoolMD(cls, domain):
        # This might look disgusting but this makes it so that
        # This is the only intrusion needed to satisfy the
        # unholy union between pool and SD metadata
        return DictValidator(domain._metadata._dict, SP_MD_FIELDS)

    @property
    def _metadata(self):
        master = self.getMasterDomain()
        return self._getPoolMD(master)

    def getDescription(self):
        try:
            return self.getMetaParam(PMDK_POOL_DESCRIPTION)
            # There was a bug that cause pool description to
            # disappear. Returning "" might be ugly but it keeps
            # everyone happy.
        except KeyError:
            return ""

    def getVersion(self):
        return self.getMasterDomain().getVersion()

    def getInfo(self):
        """
        Get storage pool info.
        """
        ##self.log.info("Get info of the storage pool %s",
        ##    self.spUUID)
        if not self.spUUID:
            raise se.StoragePoolInternalError

        info = {'type': '', 'name': '', 'domains': '', 'master_uuid': '', 'master_ver': 0,
                'lver': -1, 'spm_id': -1, 'isoprefix': '', 'pool_status': 'uninitialized', 'version': -1}
        list_and_stats = {}

        msdUUID = None
        try:
            master = self.getMasterDomain()
            msdUUID = master.sdUUID
            msdInfo = master.getInfo()
        except Exception:
            self.log.error("Couldn't read from master domain", exc_info=True)
            raise se.StoragePoolMasterNotFound(self.spUUID, msdUUID)

        try:
            info['type'] = msdInfo['type']
            info['domains'] = domainListEncoder(self.getDomains())
            info['name'] = self.getDescription()
            info['lver'] = self.getMetaParam(PMDK_LVER)
            info['spm_id'] = self.getMetaParam(PMDK_SPM_ID)
            info['master_uuid'] = msdInfo['uuid']
            info['master_ver'] = self.getMasterVersion()
            info['version'] = str(self.getVersion())
        except Exception:
            self.log.error("Pool metadata error", exc_info=True)
            raise se.StoragePoolActionError(self.spUUID)


        # Get info of all pool's domains
        domDict = self.getDomains()
        repoStats = self.getRepoStats()
        for item in domDict:
            # Return statistics for active domains only
            stats = {}
            alerts = []
            if domDict[item] == sd.DOM_ACTIVE_STATUS:
                try:
                    dom = SDF.produce(item)
                    if dom.isISO():
                        info['isoprefix'] = os.path.join(self.poolPath, item,
                                              sd.DOMAIN_IMAGES, sd.ISO_IMAGE_UUID)
                except:
                    self.log.warn("Could not get full domain information, it is probably unavailable", exc_info=True)

                if item in repoStats:
                    try:
                        stats['disktotal'] = repoStats[item]['disktotal']
                        stats['diskfree'] = repoStats[item]['diskfree']
                        if not repoStats[item]['mdavalid']:
                            alerts.append({'code':se.SmallVgMetadata.code,
                                           'message':se.SmallVgMetadata.message})
                            self.log.warning("VG %s's metadata size too small %s",
                                              dom.sdUUID, repoStats[item]['mdasize'])

                        if not repoStats[item]['mdathreshold']:
                            alerts.append({'code':se.VgMetadataCriticallyFull.code,
                                           'message':se.VgMetadataCriticallyFull.message})
                            self.log.warning("VG %s's metadata size exceeded critical size: \
                                             mdasize=%s mdafree=%s", dom.sdUUID,
                                             repoStats[item]['mdasize'], repoStats[item]['mdafree'])
                    except KeyError:
                        # We might have been asked to run before the first repoStats cycle was run
                        if item not in self.repostats:
                            self.log.warn("RepoStats is not active for active domain `%s`", item)

                        try:
                            stats.update(SDF.produce(item).getStats())
                        except:
                            self.log.error("Could get information for domain `%s`", item, exc_info=True)
                            # Domain is unavailable and we have nothing in the cache
                            # Return defaults
                            stats['disktotal'] = ""
                            stats['diskfree'] = ""
                    stats['alerts'] = alerts

            stats['status'] = domDict[item]
            list_and_stats[item] = stats

        info["pool_status"] = "connected"
        return dict(info=info, dominfo=list_and_stats)


    def getIsoDomain(self):
        """
        Get pool's ISO domain if active
        """
        domDict = self.getDomains(activeOnly=True)
        for item in domDict:
            try:
                dom = SDF.produce(item)
            except se.StorageDomainDoesNotExist :
               self.log.warn("Storage domain %s does not exist", item)
               continue

            if dom.isISO():
                return dom
        return None

    def setMetaParams(self, params):
        self._metadata.update(params)

    def setMetaParam(self, key, value):
        """
        Set key:value in pool metadata file
        """
        self._metadata[key] = value

    def getMetaParam(self, key):
        """
        Get parameter from pool metadata file
        """
        return self._metadata[key]

    def getMasterDomain(self, msdUUID=None, masterVersion=None):
        # Either we have in cache or we got non blank msdUUID,
        # no other option should be supported
        if msdUUID and msdUUID != sd.BLANK_UUID:
            self.masterDomain = self.verifyMasterDomain(msdUUID=msdUUID, masterVersion=masterVersion)
            self.log.debug("Master domain '%s' verified", self.masterDomain)

        if not self.masterDomain:
            self.log.error("Couldn't find master domain for pool %s", self.spUUID, exc_info=True)
            raise se.StoragePoolMasterNotFound(self.spUUID, str(msdUUID))

        return self.masterDomain

    def verifyMasterDomain(self, msdUUID, masterVersion=-1):
        """
        Get master domain of this pool
         'spUUID' - storage pool UUID
        """
        # Validate params, if given
        try:
            # Make sure we did not receive a version without a domain
            if not msdUUID:
                masterVersion = -1
            # Make sure that version is an integer
            masterVersion = int(masterVersion)
        except:
            msdUUID = None
            masterVersion = -1

        domain = SDF.produce(msdUUID)
        if not domain.isMaster():
            self.log.error("Requested master domain '%s' is not a master domain at all", msdUUID)
            raise se.StoragePoolWrongMaster(self.spUUID, msdUUID)

        pools = domain.getPools()
        if (self.spUUID not in pools):
            self.log.error("Requested master domain '%s' does not belong to pool '%s'", msdUUID, self.spUUID)
            raise se.StoragePoolWrongMaster(self.spUUID, msdUUID)

        ver = self._getPoolMD(domain)[PMDK_MASTER_VER]
        if masterVersion != -1 and ver != masterVersion:
            self.log.error("Requested master domain '%s' does not have expected version '%d' it is version '%s'",
                        msdUUID, masterVersion, ver)
            raise se.StoragePoolWrongMaster(self.spUUID, msdUUID)

        self.log.debug("Master domain '%s' verified", msdUUID)
        return domain


    def invalidateMetadata(self):
        self._metadata.invalidate()

    @misc.samplingmethod
    def updateMonitoringThreads(self):
        # domain list it's list of sdUUID:status
        # sdUUID1:status1,sdUUID2:status2,...
        self.invalidateMetadata()
        activeDomains = self.getDomains(activeOnly=True)
        monitoredDomains = self.repostats.keys()

        for sdUUID in monitoredDomains:
            if sdUUID not in activeDomains:
                try:
                    self.stopRepoStats(sdUUID)
                    self.log.debug("sp `%s` stopped monitoring domain `%s`" % (self.spUUID, sdUUID))
                except se.StorageException:
                    self.log.error("Unexpected error while trying to stop monitoring domain `%s`", sdUUID, exc_info=True)

        for sdUUID in activeDomains:
            if sdUUID not in monitoredDomains:
                try:
                    self.startRepoStats(sdUUID)
                    self.log.debug("sp `%s` started monitoring domain `%s`" % (self.spUUID, sdUUID))
                except se.StorageException:
                    self.log.error("Unexpected error while trying to monitor domain `%s`", sdUUID, exc_info=True)

    def getDomains(self, activeOnly=False):
        return dict((sdUUID, status) \
               for sdUUID, status in self.getMetaParam(PMDK_DOMAINS).iteritems() \
               if not activeOnly or status == sd.DOM_ACTIVE_STATUS)

    def checkBackupDomain(self):
        domDict = self.getDomains(activeOnly=True)
        for sdUUID in domDict:
            dom = SDF.produce(sdUUID)
            if dom.isBackup():
                dom.mountMaster()
                # Master tree should be exist in this point
                # Recreate it if not.
                dom.createMasterTree()


    def getImageDomainsList(self, imgUUID, datadomains=True):
        """
        Get list of all domains in the pool that contain imgUUID
         'imgUUID' - image UUID
        """
        # TODO: get rid of this verb and let management query each domain separately
        #  the problem with current implementation is that when a domain is not accesible
        #  the error must be ignored and management can reach wrong conclusions.
        domainsdict = self.getDomains(activeOnly=True)
        domainslist = []

        for dom in domainsdict:
            try:
                d = SDF.produce(dom)
            except Exception:
                # Pass over invisible active domains
                self.log.error("Unexpected error", exc_info=True)
                continue

            if datadomains and not d.isData():
                continue

            imageslist = d.getAllImages()
            if imgUUID in imageslist:
                domainslist.append(dom)

        return domainslist

    def isMember(self, sdUUID, checkActive=False):
        """
        Check if domain is memeber in the pool.
        """
        return sdUUID in self.getDomains(activeOnly=checkActive)

    def isActive(self, sdUUID):
        return sdUUID in self.getDomains(activeOnly=True)

    # TODO : move to sd.py
    def _getVMsPath(self, sdUUID):
        """
        Return general path of VMs from the pool.
        If 'sdUUID' is given then return VMs dir within it.
        """
        if sdUUID and sdUUID != sd.BLANK_UUID:
            if not self.isActive(sdUUID):
                raise se.StorageDomainNotActive(sdUUID)
            vmPath = SDF.produce(sdUUID).getVMsDir()
        # Get VMs path from the pool (from the master domain)
        else:
            vmPath = self.getMasterDomain().getVMsDir()

        if not os.path.exists(vmPath):
            raise se.VMPathNotExists(vmPath)
        return vmPath

    def check(self):
        poolstatus = 0
        baddomains = {}
        message = "Pool OK"
        try:
            masterdomain = self.getMasterDomain()
            self.invalidateMetadata()
            spmId = self.getMetaParam(PMDK_SPM_ID)
            domains = self.getDomains(activeOnly=True)

            for dom in domains:
                d = SDF.produce(dom)
                domstatus = d.checkDomain(spUUID=self.spUUID)
                if domstatus["domainstatus"] != 0:
                    baddomains[dom] = domstatus
                    poolstatus = se.StoragePoolCheckError.code
                    message = "Pool has bad domains"
        except se.StorageException, e:
            poolstatus = e.code
            message = str(e)
        except:
            poolstatus = se.StorageException.code
            message = "Pool is bad"

        return dict(poolstatus = poolstatus, baddomains = baddomains,
                    masterdomain = masterdomain.sdUUID, spmhost=spmId,
                    message = message)


    def _repostats(self, domain):
        # self.selftest() should return True if things are looking good
        # and False otherwise
        stats = { 'disktotal' : '0',
                  'diskfree' : '0',
                  'masterValidate' : { 'mount' : False, 'valid' : False }
                }
        code = 0
        try:
            if not domain.selftest():
                code = 200

            res = domain.getStats()
            stats.update(res)
            # Add here more selftests if needed
            # Fill stats to get it back to the caller
            # Keys 'finish' and 'result' are reserved and may not be used
            stats['masterValidate'] = domain.validateMaster()
        except se.StorageException, e:
            code = e.code
        except (OSError, Timeout):
            code = se.StorageDomainAccessError.code

        return stats, code


    def startRepoStats(self, sdUUID):
        statthread = self.repostats.get(sdUUID)
        if not statthread:
            statthread = StatsThread(self._repostats, sdUUID)
            statthread.start()
            self.repostats[sdUUID] = statthread
        self.log.debug("%s stat %s", sdUUID, statthread)


    def stopRepoStats(self, domain):
        statthread = self.repostats.pop(domain, None)
        if statthread:
            statthread.stop()
        self.log.debug("%s stat %s", domain, statthread)


    def getRepoStats(self):
        repostats = self.repostats.copy()
        result = {}
        for d in repostats:
            result[d] = repostats[d].getStatsResults()

        return result
