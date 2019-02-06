#
# Copyright 2014-2018 Red Hat, Inc.
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

from __future__ import absolute_import
from __future__ import division

import pytest
from vdsm.storage import nfsSD
from vdsm.storage import constants as sc
from vdsm.storage import exception as se
from vdsm.storage import sd


def test_incorrect_block_rejected():
    with pytest.raises(se.InvalidParameterException):
        nfsSD.NfsStorageDomain.create(
            sc.BLANK_UUID, "test", sd.DATA_DOMAIN,
            sc.BLANK_UUID, sd.ISCSI_DOMAIN, 4, sc.BLOCK_SIZE_4K,
            sc.ALIGNMENT_1M)


def test_incorrect_alignment_rejected():
    with pytest.raises(se.InvalidParameterException):
        nfsSD.NfsStorageDomain.create(
            sc.BLANK_UUID, "test", sd.DATA_DOMAIN,
            sc.BLANK_UUID, sd.ISCSI_DOMAIN, 4, sc.BLOCK_SIZE_512,
            sc.ALIGNMENT_2M)
