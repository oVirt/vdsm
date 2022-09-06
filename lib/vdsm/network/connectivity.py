# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import errno
import os
import time
import logging

from vdsm.common import constants
from vdsm.common.conv import tobool
from vdsm.common import fileutils

from . import errors as ne
from .errors import ConfigNetworkError

CONNECTIVITY_TIMEOUT_DEFAULT = 4
P_VDSM_CLIENT_LOG = constants.P_VDSM_RUN + 'client.log'


def _get_connectivity_timeout(options):
    return int(
        options.get('connectivityTimeout', CONNECTIVITY_TIMEOUT_DEFAULT)
    )


def check(options):
    if tobool(options.get('connectivityCheck', True)):
        logging.debug('Checking connectivity...')
        if not _client_seen(_get_connectivity_timeout(options)):
            logging.info('Connectivity check failed, rolling back')
            raise ConfigNetworkError(
                ne.ERR_LOST_CONNECTION, 'connectivity check failed'
            )


def confirm():
    fileutils.touch_file(P_VDSM_CLIENT_LOG)


def _client_seen(timeout):
    start = time.time()
    while timeout >= 0:
        try:
            if os.stat(P_VDSM_CLIENT_LOG).st_mtime > start:
                return True
        except OSError as e:
            if e.errno == errno.ENOENT:
                pass  # P_VDSM_CLIENT_LOG is not yet there
            else:
                raise
        time.sleep(1)
        timeout -= 1
    return False
