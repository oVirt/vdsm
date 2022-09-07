# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

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
