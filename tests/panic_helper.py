# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division
import os

from vdsm.common import panic

# Create new process group so panic will not kill the test runner.
os.setpgid(0, 0)

panic.panic("panic test")
