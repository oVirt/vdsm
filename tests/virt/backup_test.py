#
# Copyright 2020 Red Hat, Inc.
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
from __future__ import division
from __future__ import print_function

import collections

from vdsm.common import nbdutils
from vdsm.common.xmlutils import indented

from vdsm.virt import backup


class FakeDrive(object):
    def __init__(self, name, imageID):
        self.name = name
        self.imageID = imageID
        self.diskType = 'file'


def test_backup_xml():
    # drives must be sorted for the disks to appear
    # each time in the same order in the backup XML
    drives = collections.OrderedDict()
    drives["img-id-1"] = FakeDrive("sda", "img-id-1")
    drives["img-id-2"] = FakeDrive("vda", "img-id-2")
    drives_scratch_disks = {
        "sda": "/path/to/scratch_sda",
        "vda": "/path/to/scratch_vda",
    }
    addr = nbdutils.UnixAddress("/path/to/sock")
    backup_xml = backup.create_backup_xml(
        addr, drives, drives_scratch_disks)

    expected_xml = """
        <domainbackup mode='pull'>
            <server transport='unix' socket='/path/to/sock'/>
            <disks>
                <disk name='sda' type='file'>
                    <scratch file='/path/to/scratch_sda'>
                        <seclabel model="dac" relabel="no"/>
                    </scratch>
                </disk>
                <disk name='vda' type='file'>
                    <scratch file='/path/to/scratch_vda'>
                        <seclabel model="dac" relabel="no"/>
                    </scratch>
                </disk>
            </disks>
        </domainbackup>
        """
    assert indented(expected_xml) == indented(backup_xml)
