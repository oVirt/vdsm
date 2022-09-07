# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

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
