# Copyright 2015 Red Hat, Inc.
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

from contextlib import contextmanager

from testlib import VdsmTestCase
from testlib import permutations, expandPermutations
from testlib import recorded

from storage import blockSD, fileSD, fileVolume, blockVolume


class FakeDomainManifest(object):
    def __init__(self):
        self.sdUUID = 'a6ecac0a-5c6b-46d7-9ba5-df8b34df2d01'
        self.domaindir = '/a/b/c'
        self.mountpoint = '/a/b'
        self._metadata = {}
        self.__class__.__class_calls__ = []

    @recorded
    def replaceMetadata(self, md):
        pass

    @recorded
    def getIsoDomainImagesDir(self):
        pass

    @recorded
    def getMDPath(self):
        pass

    @recorded
    def getMetaParam(self, key):
        pass

    @recorded
    def qcow2_compat(self):
        pass

    @recorded
    def getVersion(self):
        pass

    @recorded
    def getMetadata(self):
        pass

    @recorded
    def getFormat(self):
        pass

    @recorded
    def getPools(self):
        pass

    @recorded
    def getRepoPath(self):
        pass

    @recorded
    def getStorageType(self):
        pass

    @recorded
    def getDomainRole(self):
        pass

    @recorded
    def getDomainClass(self):
        pass

    @recorded
    def isISO(self):
        pass

    @recorded
    def isBackup(self):
        pass

    @recorded
    def isData(self):
        pass

    @recorded
    def deleteImage(self, sdUUID, imgUUID, volsImgs):
        pass

    @recorded
    def purgeImage(self, sdUUID, imgUUID, volsImgs, discard):
        pass

    @recorded
    def getAllImages(self):
        pass

    @recorded
    def getAllVolumes(self):
        pass

    @recorded
    def getReservedId(self):
        pass

    @recorded
    def acquireHostId(self, hostId, async=False):
        pass

    @recorded
    def releaseHostId(self, hostId, async=False, unused=False):
        pass

    @recorded
    def hasHostId(self, hostId):
        pass

    @recorded
    def getHostStatus(self, hostId):
        pass

    @recorded
    def getDomainLease(self):
        pass

    @recorded
    def acquireDomainLock(self, hostID):
        pass

    @recorded
    def releaseDomainLock(self):
        pass

    @recorded
    def inquireDomainLock(self):
        pass

    @recorded
    def hasVolumeLeases(self):
        pass

    @recorded
    def _makeDomainLock(self, domVersion):
        pass

    @recorded
    def refreshDirTree(self):
        pass

    @recorded
    def refresh(self):
        pass

    @recorded
    def validateCreateVolumeParams(self, volFormat, srcVolUUID,
                                   preallocate=None):
        pass

    @recorded
    def external_leases_path(self):
        pass


class FakeBlockDomainManifest(FakeDomainManifest):
    def __init__(self):
        FakeDomainManifest.__init__(self)
        self.logBlkSize = 512
        self.phyBlkSize = 512

    @recorded
    def getMonitoringPath(self):
        pass

    @recorded
    def getVSize(self, imgUUID, volUUID):
        pass

    @recorded
    def getVAllocSize(self, imgUUID, volUUID):
        pass

    @recorded
    def getLeasesFilePath(self):
        pass

    @recorded
    def getIdsFilePath(self):
        pass

    @recorded
    def readMetadataMapping(self):
        pass

    @classmethod
    @recorded
    def metaSize(cls, *args):
        pass

    @classmethod
    @recorded
    def getMetaDataMapping(cls, *args):
        pass

    @recorded
    def resizePV(self, guid):
        pass

    @recorded
    def extend(self, devlist, force):
        pass

    @recorded
    def extendVolume(self, volumeUUID, size, isShuttingDown=None):
        pass

    @recorded
    def rmDCImgDir(self, imgUUID, volsImgs):
        pass

    @recorded
    def _getImgExclusiveVols(self, imgUUID, volsImgs):
        pass

    @recorded
    @contextmanager
    def acquireVolumeMetadataSlot(self, vol_name, slotSize):
        yield

    @recorded
    def getVolumeLease(self, imgUUID, volUUID):
        pass

    @classmethod
    @recorded
    def supports_external_leases(cls, version):
        pass


class FakeFileDomainManifest(FakeDomainManifest):
    def __init__(self):
        FakeDomainManifest.__init__(self)
        self.remotePath = 'b'

    @recorded
    def getMonitoringPath(self):
        pass

    @recorded
    def getVSize(self, imgUUID, volUUID):
        pass

    @recorded
    def getVAllocSize(self, imgUUID, volUUID):
        pass

    @recorded
    def getLeasesFilePath(self):
        pass

    @recorded
    def getIdsFilePath(self):
        pass

    @recorded
    def getVolumeLease(self, imgUUID, volUUID):
        pass

    @classmethod
    @recorded
    def supports_external_leases(cls, version):
        pass


class FakeBlockStorageDomain(blockSD.BlockStorageDomain):
    manifestClass = FakeBlockDomainManifest

    def __init__(self):
        self._manifest = self.manifestClass()


class FakeFileStorageDomain(fileSD.FileStorageDomain):
    manifestClass = FakeFileDomainManifest

    def __init__(self):
        self._manifest = self.manifestClass()


class FakeVolumeManifest(object):
    def __init__(self):
        self.sdUUID = 'b4502284-2101-4c5c-ada0-6a196fb30315'
        self.imgUUID = 'e2a325e4-62be-4939-8145-72277c270e8e'
        self.volUUID = '6aab5eb4-2a8b-4cb7-a0b7-bc6f61de3e18'
        self.repoPath = '/rhev/data-center'
        self.voltype = None
        self.__class__.__class_calls__ = []

    @property
    def imagePath(self):
        return '/a/b'

    @property
    def volumePath(self):
        return '/a/b/c'

    @classmethod
    @recorded
    def formatMetadata(cls, *args):
        pass

    @classmethod
    @recorded
    def _putMetadata(cls, *args):
        pass

    @classmethod
    @recorded
    def createMetadata(cls, metaId, meta):
        pass

    @classmethod
    @recorded
    def newMetadata(cls, metaId, sdUUID, imgUUID, puuid, size, format, type,
                    voltype, disktype, desc="", legality=None):
        pass

    @recorded
    def getVolumePath(self):
        pass

    @recorded
    def getMetadataId(self):
        pass

    @recorded
    def getMetadata(self, metaId=None):
        pass

    @recorded
    def getMetaParam(self, key):
        pass

    @recorded
    def setMetadata(self, meta, metaId=None):
        pass

    @recorded
    def getParent(self):
        pass

    @recorded
    def setLeaf(self):
        pass

    @recorded
    def isLeaf(self):
        pass

    @recorded
    def getVolType(self):
        pass

    @recorded
    def getChildren(self):
        pass

    @recorded
    def isShared(self):
        pass

    @recorded
    def setInternal(self):
        pass

    @recorded
    def recheckIfLeaf(self):
        pass

    @recorded
    def getImage(self):
        pass

    @recorded
    def setDescription(self, descr):
        pass

    @recorded
    def getDescription(self):
        pass

    @recorded
    def getLegality(self):
        pass

    @recorded
    def setLegality(self, legality):
        pass

    @recorded
    def setDomain(self, sdUUID):
        pass

    @recorded
    def setShared(self):
        pass

    @recorded
    def getSize(self):
        pass

    @recorded
    def optimal_size(self):
        pass

    @recorded
    def setSize(self, size):
        pass

    @recorded
    def updateInvalidatedSize(self):
        pass

    @recorded
    def getType(self):
        pass

    @recorded
    def setType(self, prealloc):
        pass

    @recorded
    def getDiskType(self):
        pass

    @recorded
    def getFormat(self):
        pass

    @recorded
    def setFormat(self, volFormat):
        pass

    @recorded
    def isLegal(self):
        pass

    @recorded
    def isFake(self):
        pass

    @recorded
    def isInternal(self):
        pass

    @recorded
    def isSparse(self):
        pass

    @recorded
    def getVolumeSize(self, bs=0):
        pass

    @recorded
    def getVolumeTrueSize(self, bs=0):
        pass

    @recorded
    def metadata2info(self, meta):
        pass

    @recorded
    def getInfo(self):
        pass

    @recorded
    def getVmVolumeInfo(self):
        pass

    @recorded
    def getVolumeParams(self, bs=0):
        pass

    @recorded
    def validateDelete(self):
        pass

    @classmethod
    @recorded
    def newVolumeLease(cls, metaId, sdUUID, volUUID):
        pass

    @recorded
    def refreshVolume(self):
        pass

    @recorded
    def _share(self, dstImgPath):
        pass

    @recorded
    def _shareLease(self, dstImgPath):
        pass

    @classmethod
    @recorded
    def getImageVolumes(cls, repoPath, sdUUID, imgUUID):
        pass

    @recorded
    def prepare(self, rw=True, justme=False,
                chainrw=False, setrw=False, force=False):
        pass

    @classmethod
    @recorded
    def teardown(cls, sdUUID, volUUID, justme=False):
        pass

    @classmethod
    @recorded
    def max_size(cls, virtual_size, format):
        pass


class FakeBlockVolumeManifest(FakeVolumeManifest):

    @recorded
    def getMetaOffset(self):
        pass

    @recorded
    def getParentMeta(self):
        pass

    @recorded
    def getParentTag(self):
        pass

    @recorded
    def getVolumeTag(self, tagPrefix):
        pass

    @recorded
    def changeVolumeTag(self, tagPrefix, uuid):
        pass

    @recorded
    def setParentMeta(self, puuid):
        pass

    @recorded
    def setParentTag(self, puuid):
        pass

    @recorded
    def _setrw(self, rw):
        pass

    @recorded
    def getDevPath(self):
        pass

    @recorded
    def removeMetadata(self, metaId):
        pass

    @classmethod
    @recorded
    def calculate_volume_alloc_size(cls, preallocate, capacity, initial_size):
        pass


class FakeFileVolumeManifest(FakeVolumeManifest):
    def __init__(self):
        super(FakeFileVolumeManifest, self).__init__()
        self.oop = 'oop'

    @recorded
    def _getMetaVolumePath(self, vol_path=None):
        pass

    @classmethod
    @recorded
    def file_setrw(cls, *args):
        pass

    @recorded
    def _setrw(self, rw):
        pass

    @recorded
    def _getLeaseVolumePath(self, vol_path):
        pass

    @recorded
    def removeMetadata(self, metaId=None):
        pass


class FakeFileVolume(fileVolume.FileVolume):
    manifestClass = FakeFileVolumeManifest

    def __init__(self):
        self._manifest = self.manifestClass()


class FakeBlockVolume(blockVolume.BlockVolume):
    manifestClass = FakeBlockVolumeManifest

    def __init__(self):
        self._manifest = self.manifestClass()


class RedirectionChecker(object):
    """
    Checks whether a source class redirects method calls to a target class
    instance accessible via the 'target_name" attribute.  The target class
    methods must use the @recorded decorator.
    """
    def __init__(self, source_instance, target_name):
        self.source_instance = source_instance
        self.target_name = target_name

    def check_method(self, fn, args, result):
        target = getattr(self.source_instance, self.target_name)
        getattr(self.source_instance, fn)(*args)
        self.assertEqual(result, target.__calls__)

    def check_method_call(self, fn, nr_args=0):
        args = tuple(range(nr_args))
        self.check_method(fn, args, [(fn, args, {})])

    def check_classmethod_call(self, fn, nr_args=0):
        args = tuple(range(nr_args))
        target = getattr(self.source_instance, self.target_name)
        getattr(self.source_instance, fn)(*args)
        self.assertEqual([(fn, args, {})], target.__class_calls__)

    def assertEqual(self, expected, actual):
        assert actual == expected, "expected: %r got: %r" % (expected, actual)


@expandPermutations
class DomainTestMixin(object):

    @permutations([
        ['supports_external_leases', 1],
    ])
    def test_class_methods(self, fn, nargs):
        self.checker.check_classmethod_call(fn, nargs)

    @permutations([
        ['sdUUID', 'a6ecac0a-5c6b-46d7-9ba5-df8b34df2d01'],
        ['domaindir', '/a/b/c'],
        ['_metadata', {}],
        ['mountpoint', '/a/b'],
    ])
    def test_property(self, prop, val):
        self.assertEqual(getattr(self.domain, prop), val)

    def test_getrepopath(self):
        # The private method _getRepoPath in StorageDomain calls the public
        # method getRepoPath in the StorageDomainManifest.
        self.checker.check_method('_getRepoPath', (),
                                  [('getRepoPath', (), {})])

    def test_nonexisting_function(self):
        self.assertRaises(AttributeError,
                          self.checker.check_method_call, 'foo')

    @permutations([
        # dom method, manifest method, nargs
        ['getClusterLease', 'getDomainLease', 0],
        ['acquireClusterLock', 'acquireDomainLock', 1],
        ['releaseClusterLock', 'releaseDomainLock', 0],
        ['inquireClusterLock', 'inquireDomainLock', 0],
        ['_makeClusterLock', '_makeDomainLock', 1],
    ])
    def test_clusterlock(self, dom_method, manifest_method, nr_args):
        args = tuple(range(nr_args))
        self.checker.check_method(dom_method, args,
                                  [(manifest_method, args, {})])

    @permutations([
        ['getMonitoringPath', 0],
        ['replaceMetadata', 1],
        ['getVSize', 2],
        ['getVAllocSize', 2],
        ['getLeasesFilePath', 0],
        ['getIdsFilePath', 0],
        ['getIsoDomainImagesDir', 0],
        ['getMDPath', 0],
        ['getMetaParam', 1],
        ['getVersion', 0],
        ['qcow2_compat', 0],
        ['getMetadata', 0],
        ['getFormat', 0],
        ['getPools', 0],
        ['getStorageType', 0],
        ['getDomainRole', 0],
        ['getDomainClass', 0],
        ['isISO', 0],
        ['isBackup', 0],
        ['isData', 0],
        ['deleteImage', 3],
        ['purgeImage', 4],
        ['getAllImages', 0],
        ['getAllVolumes', 0],
        ['getReservedId', 0],
        ['acquireHostId', 2],
        ['releaseHostId', 3],
        ['hasHostId', 1],
        ['getHostStatus', 1],
        ['hasVolumeLeases', 0],
        ['refreshDirTree', 0],
        ['refresh', 0],
        ['validateCreateVolumeParams', 3],
        ['getVolumeLease', 2],
        ['external_leases_path', 0],
    ])
    def test_common_functions(self, fn, nargs):
        self.checker.check_method_call(fn, nargs)


@expandPermutations
class BlockDomainTests(DomainTestMixin, VdsmTestCase):

    def setUp(self):
        self.domain = FakeBlockStorageDomain()
        self.checker = RedirectionChecker(self.domain, '_manifest')

    def test_block_properties(self):
        self.assertEqual(512, self.domain.logBlkSize)
        self.assertEqual(512, self.domain.phyBlkSize)

    def test_acquirevolumemetadataslot(self):
        with self.domain.acquireVolumeMetadataSlot(0, 1):
            result = [('acquireVolumeMetadataSlot', (0, 1), {})]
            self.assertEqual(self.domain._manifest.__calls__, result)

    @permutations([
        ['extend', 2],
        ['resizePV', 1],
        ['readMetadataMapping', 0],
        ['extendVolume', 3],
        ['rmDCImgDir', 2],
    ])
    def test_block_functions(self, fn, nargs=0):
        self.checker.check_method_call(fn, nargs)

    @permutations([
        ['metaSize', 1],
        ['getMetaDataMapping', 2],
    ])
    def test_block_classmethod(self, fn, nargs=0):
        self.checker.check_classmethod_call(fn, nargs)


class FileDomainTests(DomainTestMixin, VdsmTestCase):

    def setUp(self):
        self.domain = FakeFileStorageDomain()
        self.checker = RedirectionChecker(self.domain, '_manifest')

    def test_getremotepath(self):
        self.assertEqual('b', self.domain.getRemotePath())


@expandPermutations
class VolumeTestMixin(object):

    @permutations([
        ['sdUUID', 'b4502284-2101-4c5c-ada0-6a196fb30315'],
        ['imgUUID', 'e2a325e4-62be-4939-8145-72277c270e8e'],
        ['volUUID', '6aab5eb4-2a8b-4cb7-a0b7-bc6f61de3e18'],
        ['repoPath', '/rhev/data-center'],
        ['imagePath', '/a/b'],
        ['volumePath', '/a/b/c'],
        ['voltype', None],
    ])
    def test_property(self, prop, val):
        self.assertEqual(getattr(self.volume, prop), val)

    @permutations([
        ['getVolumePath', 0],
        ['getMetadataId', 0],
        ['getMetadata', 1],
        ['getMetaParam', 1],
        ['setMetadata', 2],
        ['getParent', 0],
        ['setLeaf', 0],
        ['isLeaf', 0],
        ['getVolType', 0],
        ['getChildren', 0],
        ['isShared', 0],
        ['setInternal', 0],
        ['recheckIfLeaf', 0],
        ['getImage', 0],
        ['setDescription', 1],
        ['getDescription', 0],
        ['getLegality', 0],
        ['setLegality', 1],
        ['setDomain', 1],
        ['setShared', 0],
        ['getSize', 0],
        ['setSize', 1],
        ['updateInvalidatedSize', 0],
        ['getType', 0],
        ['setType', 1],
        ['getDiskType', 0],
        ['getFormat', 0],
        ['setFormat', 1],
        ['isLegal', 0],
        ['isFake', 0],
        ['isInternal', 0],
        ['isSparse', 0],
        ['getVolumeSize', 1],
        ['getVolumeTrueSize', 1],
        ['metadata2info', 1],
        ['getInfo', 0],
        ['getVmVolumeInfo', 0],
        ['getVolumeParams', 1],
        ['validateDelete', 0],
        ['refreshVolume', 0],
        ['_share', 1],
        ['_shareLease', 1],
        ['prepare', 5],
        ['optimal_size', 0],
    ])
    def test_functions(self, fn, nargs):
        self.checker.check_method_call(fn, nargs)

    @permutations([
        ['formatMetadata', 1],
        ['_putMetadata', 2],
        ['createMetadata', 2],
        ['newMetadata', 11],
        ['newVolumeLease', 3],
        ['getImageVolumes', 3],
        ['teardown', 3],
    ])
    def test_class_methods(self, fn, nargs):
        self.checker.check_classmethod_call(fn, nargs)


@expandPermutations
class BlockVolumeTests(VolumeTestMixin, VdsmTestCase):

    def setUp(self):
        self.volume = FakeBlockVolume()
        self.checker = RedirectionChecker(self.volume, '_manifest')

    @permutations([
        ['getMetaOffset', 0],
        ['getParentMeta', 0],
        ['getParentTag', 0],
        ['getVolumeTag', 1],
        ['changeVolumeTag', 2],
        ['setParentMeta', 1],
        ['setParentTag', 1],
        ['_setrw', 1],
        ['getDevPath', 0],
        ['removeMetadata', 1],
    ])
    def test_functions(self, fn, nargs):
        self.checker.check_method_call(fn, nargs)

    @permutations([
        ['calculate_volume_alloc_size', 3],
        ['max_size', 2],
    ])
    def test_block_classmethod(self, fn, nargs):
        self.checker.check_classmethod_call(fn, nargs)


@expandPermutations
class FileVolumeTests(VolumeTestMixin, VdsmTestCase):

    def setUp(self):
        self.volume = FakeFileVolume()
        self.checker = RedirectionChecker(self.volume, '_manifest')

    @permutations([
        ['oop', 'oop'],
    ])
    def test_file_property(self, prop, val):
        self.assertEqual(getattr(self.volume, prop), val)

    # TODO: Test _getLeaseVolumePath with no arguments
    @permutations([
        ['_getMetaVolumePath', 1],
        ['_getLeaseVolumePath', 1],
        ['_setrw', 1],
        ['removeMetadata', 0],
    ])
    def test_functions(self, fn, nargs):
        self.checker.check_method_call(fn, nargs)

    @permutations([
        ['file_setrw', 2],
    ])
    def test_class_methods(self, fn, nargs):
        self.checker.check_classmethod_call(fn, nargs)

    @permutations([
        ['max_size', 2],
    ])
    def test_file_classmethod(self, fn, nargs):
        self.checker.check_classmethod_call(fn, nargs)
