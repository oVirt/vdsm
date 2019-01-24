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

"""
Common fixtures that can be used without importing anything.
"""

from __future__ import absolute_import
from __future__ import division

import uuid

import pytest

from vdsm.storage import constants as sc
from vdsm.storage import fileSD
from vdsm.storage import outOfProcess as oop


@pytest.fixture
def tmp_repo(tmpdir, monkeypatch):
    """
    Provide a temporary repo directory and patch vsdm to use it instead of
    /rhev/data-center.
    """
    # Create data-center directory in the tmpdir, so we don't mix temporary
    # files from other tests in the data-center.
    data_center = tmpdir.mkdir("data-center")
    mnt_dir = data_center.mkdir(sc.DOMAIN_MNT_POINT)

    # Patch repo directory.
    monkeypatch.setattr(sc, "REPO_DATA_CENTER", str(data_center))
    monkeypatch.setattr(sc, "REPO_MOUNT_DIR", str(mnt_dir))

    class tmp_repo:
        path = str(data_center)
        pool_id = str(uuid.uuid4())
        pool_path = str(data_center.join(pool_id))

    try:
        yield tmp_repo
    finally:
        # ioprocess is typically invoked from tests using tmp_repo. This
        # terminate ioprocess instances, avoiding thread and process leaks in
        # tests, and errors in __del__ during test shutdown.
        oop.stop()


@pytest.fixture
def fake_access(monkeypatch):
    """
    Fake access checks used in file based storage using supervdsm.

    Returned object has a "allowed" attribute set to True to make access check
    succeed. To make access check fail, set it to False.
    """
    class fake_access:
        allowed = True

    fa = fake_access()
    monkeypatch.setattr(fileSD, "validateDirAccess", lambda path: fa.allowed)
    return fa
