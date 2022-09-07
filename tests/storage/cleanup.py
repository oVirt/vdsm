# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import pprint


class CleanupError(Exception):
    def __init__(self, msg, errors):
        self.msg = msg
        self.errors = errors

    def __str__(self):
        return "%s: %s" % (self.msg, pprint.pformat(self.errors))

    def __repr__(self):
        return str(self)
