# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Common fixtures that can be used without importing anything.
"""

from __future__ import absolute_import
from __future__ import division

import pytest


@pytest.fixture
def fake_executeable(tmpdir):
    """
    Prepares shell script which can be used by another fixture to fake a binary
    that is called in the test. Typical usage is to fake the binary output in
    the script.
    """
    path = tmpdir.join("fake-executable")
    path.ensure()
    path.chmod(0o755)

    return path
