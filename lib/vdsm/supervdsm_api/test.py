# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division
from . import expose


@expose
def ping(*args, **kwargs):
    # This method exists for testing purposes
    return True
