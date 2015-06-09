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

from testlib import VdsmTestCase
from testlib import permutations, expandPermutations
from testlib import recorded

from storage import sd, blockSD, fileSD, glusterSD


class FakeDomainManifest(sd.StorageDomainManifest):
    def __init__(self):
        self.sdUUID = 'a6ecac0a-5c6b-46d7-9ba5-df8b34df2d01'
        self.domaindir = '/a/b/c'
        self.mountpoint = '/a/b'
        self._metadata = {}
        self.__class__._classmethod_calls = []

    @classmethod
    def record_classmethod_call(cls, fn, args):
        cls._classmethod_calls.append((fn, args))

    @classmethod
    def get_classmethod_calls(cls):
        return cls._classmethod_calls

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
    def getAllImages(self):
        pass

    @recorded
    def getAllVolumes(self):
        pass


class FakeBlockDomainManifest(FakeDomainManifest):
    def __init__(self):
        FakeDomainManifest.__init__(self)
        self.logBlkSize = 512
        self.phyBlkSize = 512

    @recorded
    def getReadDelay(self):
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
    def metaSize(cls, *args):
        cls.record_classmethod_call('metaSize', args)

    @classmethod
    def getMetaDataMapping(cls, *args):
        cls.record_classmethod_call('getMetaDataMapping', args)

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
    def getVolumeClass(self):
        pass

    @recorded
    def rmDCImgDir(self, imgUUID, volsImgs):
        pass

    @recorded
    def _getImgExclusiveVols(self, imgUUID, volsImgs):
        pass


class FakeFileDomainManifest(FakeDomainManifest):
    def __init__(self):
        FakeDomainManifest.__init__(self)
        self.remotePath = 'b'

    @recorded
    def getReadDelay(self):
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
    def getVolumeClass(self):
        pass


class FakeBlockStorageDomain(blockSD.BlockStorageDomain):
    manifestClass = FakeBlockDomainManifest

    def __init__(self):
        self.stat = None
        self._manifest = self.manifestClass()


class FakeFileStorageDomain(fileSD.FileStorageDomain):
    manifestClass = FakeFileDomainManifest

    def __init__(self):
        self.stat = None
        self._manifest = self.manifestClass()


@expandPermutations
class DomainTestMixin(object):
    # Must be implemented by the sub class
    fakeDomClass = None

    def setUp(self):
        self.dom = self.fakeDomClass()

    def _check(self, fn, args, result):
        getattr(self.dom, fn)(*args)
        self.assertEqual(self.dom._manifest.__recording__, result)

    def check_call(self, fn, nr_args=0):
        args = tuple(range(nr_args))
        self._check(fn, args, [(fn, args, {})])

    def check_classmethod_call(self, fn, nr_args=0):
        args = tuple(range(nr_args))
        getattr(self.dom, fn)(*args)
        self.assertEquals(self.dom._manifest.get_classmethod_calls(),
                          [(fn, args)])

    @permutations([
        ['sdUUID', 'a6ecac0a-5c6b-46d7-9ba5-df8b34df2d01'],
        ['domaindir', '/a/b/c'],
        ['_metadata', {}],
        ['mountpoint', '/a/b'],
    ])
    def test_property(self, prop, val):
        self.assertEqual(getattr(self.dom, prop), val)

    def test_getrepopath(self):
        # The private method _getRepoPath in StorageDomain calls the public
        # method getRepoPath in the StorageDomainManifest.
        self._check('_getRepoPath', (), [('getRepoPath', (), {})])

    def test_nonexisting_function(self):
        self.assertRaises(AttributeError, self.check_call, 'foo')

    @permutations([
        ['getReadDelay', 0],
        ['replaceMetadata', 1],
        ['getVSize', 2],
        ['getVAllocSize', 2],
        ['getLeasesFilePath', 0],
        ['getIdsFilePath', 0],
        ['getIsoDomainImagesDir', 0],
        ['getMDPath', 0],
        ['getMetaParam', 1],
        ['getVersion', 0],
        ['getMetadata', 0],
        ['getVolumeClass', 0],
        ['getFormat', 0],
        ['getPools', 0],
        ['getStorageType', 0],
        ['getDomainRole', 0],
        ['getDomainClass', 0],
        ['isISO', 0],
        ['isBackup', 0],
        ['isData', 0],
        ['deleteImage', 3],
        ['getAllImages', 0],
        ['getAllVolumes', 0],
        ])
    def test_common_functions(self, fn, nargs):
        self.check_call(fn, nargs)


@expandPermutations
class BlockTests(DomainTestMixin, VdsmTestCase):
    fakeDomClass = FakeBlockStorageDomain

    def test_block_properties(self):
        self.assertEqual(512, self.dom.logBlkSize)
        self.assertEqual(512, self.dom.phyBlkSize)

    @permutations([
        ['extend', 2],
        ['resizePV', 1],
        ['readMetadataMapping', 0],
        ['extendVolume', 3],
        ['rmDCImgDir', 2],
    ])
    def test_block_functions(self, fn, nargs=0):
        self.check_call(fn, nargs)

    @permutations([
        ['metaSize', 1],
        ['getMetaDataMapping', 2],
    ])
    def test_block_classmethod(self, fn, nargs=0):
        self.check_classmethod_call(fn, nargs)


class FileTests(DomainTestMixin, VdsmTestCase):
    fakeDomClass = FakeFileStorageDomain

    def test_getremotepath(self):
        self.assertEqual('b', self.dom.getRemotePath())
