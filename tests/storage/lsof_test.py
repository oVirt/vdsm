# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

import os
import pwd

import pytest

from vdsm.storage import lsof

from .marks import requires_root


@requires_root
@pytest.mark.root
def test_proc_info_used(tmpdir):
    path = str(tmpdir.join("file"))
    with open(path, "w") as f:
        assert list(lsof.proc_info(path)) == [{
            "command": "pytest",
            "fd": f.fileno(),
            "pid": os.getpid(),
            "user": pwd.getpwuid(os.getuid())[0]
        }]


@requires_root
@pytest.mark.root
def test_proc_info_unused(tmpdir):
    path = tmpdir.join("file")
    path.write("")
    assert list(lsof.proc_info(str(path))) == []


@requires_root
@pytest.mark.root
def test_proc_info_no_such_file():
    assert list(lsof.proc_info("/no/such/file")) == []
