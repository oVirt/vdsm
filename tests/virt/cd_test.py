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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA
# 02110-1301  USA
#
# Refer to the README and COPYING files for full details of the license
#

import logging
import uuid

import libvirt
import pytest

from vdsm import clientIF
from vdsm.common import exception

from . import vmfakelib as fake


def test_change_cd_eject():
    with fake.VM(cif=ClientIF()) as fakevm:
        fakevm._dom = fake.Domain()
        cdromspec = {
            'path': '',
            'iface': 'ide',
            'index': '2',
        }
        fakevm.changeCD(cdromspec)


def test_change_cd_failure():
    with fake.VM(cif=ClientIF()) as fakevm:
        # no specific meaning, actually any error != None is good
        fakevm._dom = fake.Domain(virtError=libvirt.VIR_ERR_GET_FAILED)

        cdromspec = {
            'path': '/path/to/image',
            'iface': 'ide',
            'index': '2',
        }

        with pytest.raises(exception.ImageFileNotFound):
            fakevm.changeCD(cdromspec)


def test_change_cd_pdiv():
    sd_id = uuid.uuid4()
    img_id = uuid.uuid4()
    vol_id = uuid.uuid4()
    drivespec = {
        "device": "cdrom",
        "domainID": sd_id,
        "poolID": uuid.uuid4(),
        "imageID": img_id,
        "volumeID": vol_id,
    }

    with fake.VM(cif=ClientIF()) as fakevm:
        fakevm._dom = fake.Domain()
        cdromspec = {
            'path': drivespec,
            'iface': 'ide',
            'index': '2',
        }
        fakevm.changeCD(cdromspec)
        assert (sd_id, img_id, vol_id) in fakevm.cif.irs.prepared_volumes


class ClientIF(clientIF.clientIF):
    log = logging.getLogger('cd_test.ClientIF')

    def __init__(self):
        self.irs = fake.IRS()
        self.channelListener = None
        self.vmContainer = {}
