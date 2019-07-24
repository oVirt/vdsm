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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA
# 02110-1301  USA
#
# Refer to the README and COPYING files for full details of the license
#
from __future__ import absolute_import
from __future__ import division

import json
import operator
import os

import pytest

from vdsm.gluster import hooks


@pytest.fixture
def hookSetup(tmpdir):
    hooks._glusterHooksPath = str(tmpdir)
    addDir = tmpdir.mkdir("add-brick").mkdir("pre")
    createDir = tmpdir.mkdir("create").mkdir("post")
    # create files in temp dir
    brickHook = addDir.join("S11test1.sh")
    brickHook.write("test brick hook")
    createHook = createDir.join("K12test2.sh")
    createHook.write("test create hook")


def test_hooksList(hookSetup):
    path = os.path.join(os.path.dirname(__file__), "results/hooks_list.json")
    with open(path) as f:
        expected_out = json.load(f)
    ret = hooks.hooksList()
    by_name = operator.itemgetter("name")
    assert sorted(expected_out, key=by_name) == sorted(ret, key=by_name)
