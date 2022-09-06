# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division


def deprecated(thing):
    """
    Mark functions, methods, and classes as deprecated.

    Marked items will be remove in future version. Do not used them in new
    code.
    """
    return thing
