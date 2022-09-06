# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

"""
errors - vdsm internal errors

This module provide internal errors which are not part of vdsm api, helpers for
error handling. For public vdsm errors see vdsm.common.exception.
"""

from __future__ import absolute_import
from __future__ import division


class Base(Exception):
    msg = "Base class for vdsm errors"

    def __str__(self):
        return self.msg.format(self=self)
