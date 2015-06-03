# Copyright 2012 Red Hat, Inc.
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

import uuid

from testlib import VdsmTestCase as TestCaseBase

from storage import blockSD

SDBLKSZ = 512


class FakeBlockStorageDomain(blockSD.BlockStorageDomain):
    DOMAIN_VERSION = 3

    def __init__(self, sdUUID, occupiedMetadataSlots=None):
        self._sdUUID = sdUUID
        self._logBlkSize = SDBLKSZ
        self.occupiedMetadataSlots = occupiedMetadataSlots

    @property
    def sdUUID(self):
        return self._sdUUID

    @property
    def logBlkSize(self):
        return self._logBlkSize

    @property
    def stat(self):
        return None

    def getVersion(self):
        return self.DOMAIN_VERSION

    def _getOccupiedMetadataSlots(self):
        return self.occupiedMetadataSlots


class BlockDomainMetadataSlotTests(TestCaseBase):
    OCCUPIED_METADATA_SLOTS = [(4, 1), (7, 1)]
    EXPECTED_METADATA_SLOT = 5

    def setUp(self):
        self.blksd = FakeBlockStorageDomain(str(uuid.uuid4()),
                                            self.OCCUPIED_METADATA_SLOTS)

    def testMetaSlotSelection(self):
        with self.blksd.acquireVolumeMetadataSlot(None, 1) as mdSlot:
            self.assertEqual(mdSlot, self.EXPECTED_METADATA_SLOT)

    def testMetaSlotLock(self):
        with self.blksd.acquireVolumeMetadataSlot(None, 1):
            acquired = self.blksd._lvTagMetaSlotLock.acquire(False)
            self.assertEqual(acquired, False)
