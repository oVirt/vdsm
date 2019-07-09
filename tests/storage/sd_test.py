#
# Copyright 2019 Red Hat, Inc.
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

from __future__ import absolute_import

import pytest

from vdsm.storage import sd
from vdsm.storage import exception as se


def test_validate_domain_version_invaid():
    with pytest.raises(se.UnsupportedDomainVersion):
        sd.StorageDomain.validate_version(-1)


@pytest.mark.parametrize("domain_version", [0, 2, 3, 4, 5])
def test_validate_domain_version_supported(domain_version):
    sd.StorageDomain.validate_version(domain_version)
