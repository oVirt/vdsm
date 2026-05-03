# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

"""
This module provides exceptions for the profiling package.
"""


class UsageError(Exception):
    """ Raised when profiler is used incorrectly """
