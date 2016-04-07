#
# Copyright 2013-2016 Red Hat, Inc.
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

import logging
import weakref

from vdsm.storage import exception as se
from vdsm.storage.persistent import unicodeDecoder
from vdsm.storage.persistent import unicodeEncoder
from vdsm.storage.securable import secured
from vdsm.storage.securable import unsecured

import blockSD
import sd

from sp import LVER_INVALID
from sp import SPM_ID_FREE
from vdsm.config import config


MAX_POOL_DESCRIPTION_SIZE = 50

PMDK_DOMAINS = "POOL_DOMAINS"
PMDK_POOL_DESCRIPTION = "POOL_DESCRIPTION"
PMDK_LVER = "POOL_SPM_LVER"
PMDK_SPM_ID = "POOL_SPM_ID"
PMDK_MASTER_VER = "MASTER_VERSION"


# Calculate how many domains can be in the pool before overflowing the Metadata
MAX_DOMAINS = blockSD.SD_METADATA_SIZE - blockSD.METADATA_BASE_SIZE
MAX_DOMAINS -= MAX_POOL_DESCRIPTION_SIZE + sd.MAX_DOMAIN_DESCRIPTION_SIZE
MAX_DOMAINS -= blockSD.PVS_METADATA_SIZE
MAX_DOMAINS /= 48


def _domainListEncoder(domDict):
    domains = ','.join(['%s:%s' % (k, v) for k, v in domDict.iteritems()])
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
class StoragePoolMemoryBackend(StoragePoolBackendInterface):

    __slots__ = ('pool', 'masterVersion', 'domainsMap')

    log = logging.getLogger('Storage.StoragePoolMemoryBackend')

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
        lVer, spmId = self.masterDomain.inquireClusterLock()
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
            ((k, v.capitalize()) for k, v in domainsMap.iteritems()))
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
        self.setDomainsMap(domainsMap, __securityOverride=True)
