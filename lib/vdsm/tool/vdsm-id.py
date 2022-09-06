# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division
from .. import host
from . import expose, ExtraArgsError
import sys


@expose("vdsm-id")
def getUUID(*args):
    """
    vdsm-id
    Printing host uuid
    """
    if len(args) > 1:
        raise ExtraArgsError()
    hostUUID = host.uuid()
    if hostUUID is None:
        raise EnvironmentError('Cannot retrieve host UUID')
    sys.stdout.write(hostUUID + '\n')
    return 0
