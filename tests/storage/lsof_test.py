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
