# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import pytest

from vdsm.storage import iscsiadm

from . marks import requires_root


@requires_root
@pytest.mark.root
def test_run_cmd():
    out = iscsiadm.run_cmd(["--version"])
    assert isinstance(out, str)
    assert "iscsiadm version" in out
