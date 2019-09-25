#
# Copyright 2013-2017 Red Hat, Inc.
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

import logging
import weakref

import six

from vdsm.common import exception
from vdsm.storage import clusterlock
from vdsm.storage import exception as se
from vdsm.storage import misc
from vdsm.storage import sd
from vdsm.storage.persistent import DictValidator
from vdsm.storage.persistent import unicodeDecoder
from vdsm.storage.persistent import unicodeEncoder
from vdsm.storage.securable import secured
from vdsm.storage.securable import unsecured
from vdsm.storage.sp import LVER_INVALID
from vdsm.storage.sp import SPM_ACQUIRED
from vdsm.storage.sp import SPM_FREE
from vdsm.storage.sp import SPM_ID_FREE
from vdsm.config import config


MAX_POOL_DESCRIPTION_SIZE = 50

PMDK_DOMAINS = "POOL_DOMAINS"
PMDK_POOL_DESCRIPTION = "POOL_DESCRIPTION"
PMDK_LVER = "POOL_SPM_LVER"
PMDK_SPM_ID = "POOL_SPM_ID"
PMDK_MASTER_VER = "MASTER_VERSION"


def _domainListEncoder(domDict):
    domains = ','.join(['%s:%s' % (k, v) for k, v in six.iteritems(domDict)])
    return domains


def _domainListDecoder(s):
    domList = {}
    if not s:
        return domList
    for domDecl in s.split(","):
        k, v = domDecl.split(':')
        domList[k.strip("'")] = v.strip("'").capitalize()
    return domList


# metadata_key: (metadata_decoder, metadata_encoder)
SP_MD_FIELDS = {
    PMDK_DOMAINS: (_domainListDecoder, _domainListEncoder),
    PMDK_POOL_DESCRIPTION: (unicodeDecoder, unicodeEncoder),
    PMDK_LVER: (int, str),
    PMDK_SPM_ID: (int, str),
    PMDK_MASTER_VER: (int, str)
}


@secured
class StoragePoolBackendInterface(object):
    """StoragePool Backend Interface Definition"""

    def __is_secure__(self):
        return False

    @unsecured
    def getSpmStatus(self):
        """Return the current SPM information with the tuple (lVer, spmId)

        This method is used from the StoragePool to get information about
        the current SPM in the Pool. The special values LVER_INVALID and
        SPM_ID_FREE are used when the values are either missing or just
        initialized.
        """
        raise NotImplementedError()

    def setSpmStatus(self, lVer=None, spmId=None):
        """Set the current SPM information using the lVer and spmId values

        This method is used from the StoragePool to set the information
        about the current SPM in the Pool. The special value None is used
        to mark any parameter that shouldn't be updated.
        This request can be ignored from the backend.
        """
        raise NotImplementedError()

    @unsecured
    def getDomainsMap(self):
        """Return a dictionary of domains in the pool

        This method must return a dictionary representing the storage domains
        statuses. The key represents the domain uuid and the status is one
        of [DOM_ATTACHED_STATUS, DOM_ACTIVE_STATUS].
        The dictionary must contain an entry for all the storage domains that
        are currently attached to the pool.
        """
        raise NotImplementedError()

    def setDomainsMap(self, domains):
        """Set a dictionary of domains in the pool

        This method is used from the StoragePool to set the map of domains in
        the pool. For more information on the format see getDomainsMap.
        """
        raise NotImplementedError()

    @unsecured
    def getMaximumSupportedDomains(self):
        """Return the maximum number of domains that can be attached

        This method is used from the StoragePool to check how many domains
        can be attached to the pool.
        """
        raise NotImplementedError()

    @unsecured
    def getMasterVersion(self):
        """Return the master domain version

        This method is used from the StoragePool to get the current version
        of the information returned by the backend. It is used in particular
        to check if the domains map is up to date.
        """
        raise NotImplementedError()

    @unsecured
    def validateMasterDomainVersion(self, masterDomain, masterVersion):
        """Valideate the master domain and version

        This method is used from the StoragePool to ensure that the backend
        is using the correct master and version. In case of a mismatch the
        method should raise a StoragePoolWrongMaster exception.
        """
        raise NotImplementedError()

    def setDomainRegularRole(self, domain):
        """Set the domain role to regular

        This method is used from the StoragePool to notify that a master
        storage domain has been demoted to regular.
        This request can be ignored from the backend.
        """
        raise NotImplementedError()

    @unsecured
    def initParameters(self, poolName, domain, masterVersion):
        """Init the storage pool parameters

        This method is used from the StoragePool generally upon creation
        to set the initial storage pool parameters.
        """
        raise NotImplementedError()

    def switchMasterDomain(self, curMasterDomain, newMasterDomain,
                           newMasterVersion):
        """Switch the master domain to a new domain

        This method is used from the StoragePool to request the switch
        of the master domain to a different domain with a new version.
        """
        raise NotImplementedError()

    @unsecured
    def getInfo(self):
        """Return a dictionary of pool information

        This method is used from the StoragePool to get the information
        about the pool, the dictionary should include: {'name': ...,
        'domains': ..., 'master_ver': ..., 'lver': ..., 'spm_id': ...}
        """
        raise NotImplementedError()


@secured
class StoragePoolDiskBackend(StoragePoolBackendInterface):

    __slots__ = ('pool',)

    log = logging.getLogger('storage.StoragePoolDiskBackend')

    def __init__(self, pool):
        self.pool = weakref.proxy(pool)

    # Read-Only StoragePool Object Accessors ###

    def __is_secure__(self):
        return self.pool.isSecure()

    @property
    def id(self):
        return self.pool.id

    @property
    def spmRole(self):
        return self.pool.spmRole

    @property
    def spUUID(self):
        return self.pool.spUUID

    @property
    def masterDomain(self):
        return self.pool.masterDomain

    # StoragePool Backend Interface Implementation ###

    @unsecured
    def getSpmStatus(self):
        poolMeta = self._getPoolMD(self.masterDomain)

        # if we claim that we were the SPM (but we're currently not) we
        # have to make sure that we're not returning stale data
        if (poolMeta[PMDK_SPM_ID] == self.id and
                not self.spmRole == SPM_ACQUIRED):
            self.invalidateMetadata()
            poolMeta = self._getPoolMD(self.masterDomain)

        return poolMeta[PMDK_LVER], poolMeta[PMDK_SPM_ID]

    def setSpmStatus(self, lVer=None, spmId=None):
        self.invalidateMetadata()
        metaParams = {}
        if lVer is not None:
            metaParams[PMDK_LVER] = lVer
        if spmId is not None:
            metaParams[PMDK_SPM_ID] = spmId
        self._metadata.update(metaParams)

    @unsecured
    def getDomainsMap(self):
        # The assumption is that whenever the storage pool metadata changes
        # the HSM hosts will receive refreshStoragePool (and the metadata will
        # be invalidated). So the invalidation in this method may be redundant
        # or it was introduced to handle negative flows (missed refresh call).
        # Anyway I think that we could get rid of this in the future, provided
        # that the engine handles/resends failed refreshStoragePool calls.
        self.invalidateMetadata()
        return self.getMetaParam(PMDK_DOMAINS)

    def setDomainsMap(self, domains):
        self.setMetaParam(PMDK_DOMAINS, domains)

    @unsecured
    def getMaximumSupportedDomains(self):
        return config.getint("irs", "maximum_domains_in_pool")

    @unsecured
    def getMasterVersion(self):
        return self.getMetaParam(PMDK_MASTER_VER)

    @unsecured
    def validateMasterDomainVersion(self, masterDomain, masterVersion):
        version = self._getPoolMD(masterDomain)[PMDK_MASTER_VER]
        if version != int(masterVersion):
            self.log.error("Requested master domain %s does not have expected "
                           "version %s it is version %s",
                           masterDomain.sdUUID, masterVersion, version)
            raise se.StoragePoolWrongMaster(self.spUUID, masterDomain.sdUUID)

    # TODO: evaluate if it is possible to remove this from the backends and
    # just use domain.changeRole(...) in the StoragePool class
    def setDomainRegularRole(self, domain):
        poolMetadata = self._getPoolMD(domain)
        # TODO: consider to remove the transaction (and this method as well)
        # since setting the version to 0 may be useless.
        with poolMetadata.transaction():
            poolMetadata[PMDK_MASTER_VER] = 0
            domain.changeRole(sd.REGULAR_DOMAIN)

    @unsecured
    def initParameters(self, poolName, domain, masterVersion):
        self._getPoolMD(domain).update({
            PMDK_SPM_ID: SPM_ID_FREE,
            PMDK_LVER: LVER_INVALID,
            PMDK_MASTER_VER: masterVersion,
            PMDK_POOL_DESCRIPTION: poolName,
            PMDK_DOMAINS: {domain.sdUUID: sd.DOM_ACTIVE_STATUS},
        })

    def switchMasterDomain(self, curMasterDomain, newMasterDomain,
                           newMasterVersion):
        curPoolMD = self._getPoolMD(curMasterDomain)
        newPoolMD = self._getPoolMD(newMasterDomain)

        newPoolMD.update({
            PMDK_DOMAINS: curPoolMD[PMDK_DOMAINS],
            PMDK_POOL_DESCRIPTION: curPoolMD[PMDK_POOL_DESCRIPTION],
            PMDK_LVER: curPoolMD[PMDK_LVER],
            PMDK_SPM_ID: curPoolMD[PMDK_SPM_ID],
            PMDK_MASTER_VER: newMasterVersion,
        })

    @unsecured
    def getInfo(self):
        try:
            pmd = self._getPoolMD(self.masterDomain)
        except Exception:
            self.log.error("Pool metadata error", exc_info=True)
            raise se.StoragePoolActionError(self.spUUID)

        return {
            'name': pmd[PMDK_POOL_DESCRIPTION],
            'domains': _domainListEncoder(pmd[PMDK_DOMAINS]),
            'master_ver': pmd[PMDK_MASTER_VER],
            'lver': pmd[PMDK_LVER],
            'spm_id': pmd[PMDK_SPM_ID],
        }

    # Backend Specific Methods

    @unsecured
    def forceFreeSpm(self):
        # DO NOT USE, STUPID, HERE ONLY FOR BC
        # TODO: SCSI Fence the 'lastOwner'
        # pylint: disable=unexpected-keyword-arg
        self.setSpmStatus(LVER_INVALID, SPM_ID_FREE, __securityOverride=True)
        self.pool.spmRole = SPM_FREE

    @classmethod
    def _getPoolMD(cls, domain):
        # This might look disgusting but this makes it so that
        # This is the only intrusion needed to satisfy the
        # unholy union between pool and SD metadata
        return DictValidator(domain._metadata._dict, SP_MD_FIELDS)

    @property
    def _metadata(self):
        return self._getPoolMD(self.masterDomain)

    @unsecured
    def getMetaParam(self, key):
        """
        Get parameter from pool metadata file
        """
        return self._metadata[key]

    def setMetaParam(self, key, value):
        """
        Set key:value in pool metadata file
        """
        self._metadata[key] = value

    @unsecured
    def getDescription(self):
        try:
            return self.getMetaParam(PMDK_POOL_DESCRIPTION)
            # There was a bug that cause pool description to
            # disappear. Returning "" might be ugly but it keeps
            # everyone happy.
        except KeyError:
            return ""

    def setDescription(self, descr):
        """
        Set storage pool description.
         'descr' - pool description
        """
        if len(descr) > MAX_POOL_DESCRIPTION_SIZE:
            raise se.StoragePoolDescriptionTooLongError()

        self.log.info("spUUID=%s descr=%s", self.spUUID, descr)

        if not misc.isAscii(descr) and not self.masterDomain.supportsUnicode():
            raise se.UnicodeArgumentException()

        self.setMetaParam(PMDK_POOL_DESCRIPTION, descr)

    @unsecured
    def invalidateMetadata(self):
        if not self.spmRole == SPM_ACQUIRED:
            self._metadata.invalidate()


@secured
class StoragePoolMemoryBackend(StoragePoolBackendInterface):

    __slots__ = ('pool', 'masterVersion', 'domainsMap')

    log = logging.getLogger('storage.StoragePoolMemoryBackend')

    def __init__(self, pool, masterVersion, domainsMap):
        self.pool = weakref.proxy(pool)
        self.updateVersionAndDomains(masterVersion, domainsMap)

    # Read-Only StoragePool Object Accessors

    def __is_secure__(self):
        return self.pool.isSecure()

    @property
    def spUUID(self):
        return self.pool.spUUID

    @property
    def masterDomain(self):
        return self.pool.masterDomain

    # StoragePool Backend Interface Implementation

    @unsecured
    def getSpmStatus(self):
        # FIXME: unify with StoragePoolDiskBackend
        try:
            lVer, spmId = self.masterDomain.inquireClusterLock()
        except clusterlock.TemporaryFailure as e:
            raise exception.expected(e)
        return lVer or LVER_INVALID, spmId or SPM_ID_FREE

    def setSpmStatus(self, lVer, spmId):
        self.log.debug(
            'this storage pool implementation ignores the set spm '
            'status requests (lver=%s, spmid=%s)', lVer, spmId)

    @unsecured
    def getDomainsMap(self):
        return self.domainsMap

    def setDomainsMap(self, domainsMap):
        self.domainsMap = dict(
            ((k, v.capitalize()) for k, v in six.iteritems(domainsMap)))
        self.log.info(
            'new storage pool master version %s and domains map %s',
            self.masterVersion, self.domainsMap)

    @unsecured
    def getMaximumSupportedDomains(self):
        return config.getint("irs", "maximum_domains_in_pool")

    @unsecured
    def getMasterVersion(self):
        return self.masterVersion

    @unsecured
    def validateMasterDomainVersion(self, masterDomain, masterVersion):
        if self.masterVersion != int(masterVersion):
            self.log.error(
                'requested master version %s is not the expected one %s',
                masterVersion, self.masterVersion)
            raise se.StoragePoolWrongMaster(self.spUUID, masterDomain.sdUUID)

    def setDomainRegularRole(self, domain):
        domain.changeRole(sd.REGULAR_DOMAIN)

    @unsecured
    def initParameters(self, domain, poolName, masterVersion):
        self.log.debug(
            'this storage pool implementation ignores master '
            'domain initialization (sdUUID=%s, poolName="%s", '
            'masterVersion=%s)', domain.sdUUID, poolName, masterVersion)

    def switchMasterDomain(self, currentMasterDomain, newMasterDomain,
                           newMasterVersion):
        self.log.debug(
            'switching from master domain %s version %s to master domain '
            '%s version %s', currentMasterDomain.sdUUID, self.masterVersion,
            newMasterDomain.sdUUID, newMasterVersion)
        self.masterVersion = newMasterVersion

    @unsecured
    def getInfo(self):
        lVer, spmId = self.getSpmStatus()
        return {
            'name': 'No Description',
            'domains': _domainListEncoder(self.domainsMap),
            'master_ver': self.masterVersion,
            'lver': lVer,
            'spm_id': spmId,
        }

    # Backend Specific Methods

    @unsecured
    def updateVersionAndDomains(self, masterVersion, domainsMap):
        self.log.debug('updating domain version to %s and domains map '
                       'to %s', masterVersion, domainsMap)
        self.masterVersion = masterVersion
        # pylint: disable=unexpected-keyword-arg
        self.setDomainsMap(domainsMap, __securityOverride=True)
