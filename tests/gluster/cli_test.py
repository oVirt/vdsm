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

import os
import pytest

from vdsm.gluster import cli
from vdsm.gluster import exception as ge


def test_exec_gluster_empty_cmd():
    with pytest.raises(ge.GlusterCmdFailedException):
        cli._execGluster([])


@pytest.mark.parametrize("relative_filepath, expected", [
    ("results/getTreeTestData-1.xml", "d6b27c29-dce0-420e-9982-42f855bca9cd"),
])
def test_getTree(relative_filepath, expected):

    def read_from_file(relative_filepath):
        path = os.path.join(os.path.dirname(__file__), relative_filepath)
        with open(path) as f:
            out = f.read()
        return out

    xml = read_from_file(relative_filepath)
    assert cli._getTree(xml).find('uuidGenerate/uuid').text == expected


def test_get_tree_empty_input():
    with pytest.raises(ge.GlusterXmlErrorException):
        cli._getTree("")
