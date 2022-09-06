# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division
from vdsm.tool import UsageError


class InvalidConfig(UsageError):
    """ raise when invalid configuration passed """
    pass


class InvalidRun(UsageError):
    """ raise when the environment is not valid to run the command """
    pass


# Declare state of configuration
#
# YES   = Module configured.
#
# NO    = Module not configured before.
#
# MAYBE = Module configured before,
#         configuration validity could not be determined.
#
# See also --force at configurators.py.
YES, NO, MAYBE = tuple(range(3))
