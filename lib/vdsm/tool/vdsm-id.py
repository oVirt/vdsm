# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import logging

from .. import host
from . import LOGGER_NAME
from . import expose, ExtraArgsError


log = logging.getLogger(LOGGER_NAME)


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
    log.info("%s", hostUUID)
    return 0
