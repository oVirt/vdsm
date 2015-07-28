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

    @permutations([
        ['sdUUID', 'a6ecac0a-5c6b-46d7-9ba5-df8b34df2d01'],
        ['domaindir', '/a/b/c'],
        ['_metadata', {}],
        ['mountpoint', '/a/b'],
    ])
    def test_property(self, prop, val):
        self.assertEqual(getattr(self.dom, prop), val)

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
        ])
    def test_common_functions(self, fn, nargs):
        self.check_call(fn, nargs)


class BlockTests(DomainTestMixin, VdsmTestCase):
    fakeDomClass = FakeBlockStorageDomain

    def test_block_properties(self):
        self.assertEqual(512, self.dom.logBlkSize)
        self.assertEqual(512, self.dom.phyBlkSize)


class FileTests(DomainTestMixin, VdsmTestCase):
    fakeDomClass = FakeFileStorageDomain

    def test_getremotepath(self):
        self.assertEqual('b', self.dom.getRemotePath())
