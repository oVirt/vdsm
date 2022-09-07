# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

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


def test_hookRead(hookSetup):
    path = os.path.join(os.path.dirname(__file__), "results/hook_read.json")
    with open(path) as f:
        expected_out = json.load(f)
    ret = hooks.hookRead("add-brick", "PRE", "11test1.sh")
    assert expected_out == ret
