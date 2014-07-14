#
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA
# 02110-1301  USA
#
# Refer to the README and COPYING files for full details of the license
#


import os
from testlib import VdsmTestCase as TestCaseBase

import storage.blockSD
import storage.fileSD

testDir = os.path.dirname(__file__)


class TestBlockGetAllVolumes(TestCaseBase):
    def mGetLV(self, vgName):
        """ This function returns lvs output in lvm.getLV() format.

        Input file name: lvs_<sdName>.out
        Input file should be the output of:
        lvs --noheadings --units b --nosuffix --separator '|' \
            -o uuid,name,vg_name,attr,size,seg_start_pe,devices,tags <sdName>
        """
        lvs = []
        lvs_out = open(os.path.join(testDir, 'lvs_%s.out' % vgName),
                       "r").read()
        for line in lvs_out.split():
            fields = [field.strip() for field in
                      line.split(storage.lvm.SEPARATOR)]
            lvs.append(storage.lvm.makeLV(*fields))
        return lvs

    def test_getAllVolumes(self):
        storage.blockSD.lvm.getLV = self.mGetLV
        sdName = "3386c6f2-926f-42c4-839c-38287fac8998"
        allVols = storage.blockSD.getAllVolumes(sdName)
        self.assertEqual(len(allVols), 23)


class Moop(object):
    class Modulito(object):
        def glob(self, volMetaPattern):
            """ This function returns glob output from a file.

            Input file name: 'glob_<sdName>.out
            Input file format: str(glob.glob(<imgsDir>))
            When
            <imgsDir> = /rhev/data-center/mnt/<mntPoint>/<sdName>/images/*.meta
            """
            sdPath, globExp = volMetaPattern.split('/images')
            sdHead, sdName = os.path.split(sdPath)
            inp = open(os.path.join(sdHead, "glob_%s.out" % sdName),
                       "r").read()
            # Danger Will Robinson! Danger!
            return eval(inp)

    class FileUselessUtils(object):
        def pathExists(metafile):
            return True

    @property
    def glob(cls):
        return cls.Modulito()

    @property
    def fileutils(cls):
        return cls.FileUselessUtils()


class TestFileGetAllVolumes(TestCaseBase):
    class MStorageDomain(storage.fileSD.FileStorageDomain):
        def __init__(self, sdUUID):
                self.sdUUID = sdUUID
                self.mountpoint = testDir
                self.stat = None

        @property
        def oop(self):
            return Moop()

    def test_getAllVolumes(self):
        sdName = "1c60971a-8647-44ac-ae33-6520887f8843"
        dom = self.MStorageDomain(sdName)
        allVols = dom.getAllVolumes()
        self.assertEqual(len(allVols), 11)
