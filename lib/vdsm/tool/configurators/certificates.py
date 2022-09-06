# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import os

from . import YES, NO
from vdsm import constants
from vdsm.common import cmdutils
from vdsm.common import commands
from vdsm.common import pki
from vdsm.config import config


def validate():
    return _certsExist()


def _exec_vdsm_gencerts():
    try:
        commands.run([
            os.path.join(constants.P_VDSM_EXEC, 'vdsm-gencerts.sh'),
            pki.CA_FILE,
            pki.KEY_FILE,
            pki.CERT_FILE
        ])
    except cmdutils.Error as e:
        msg = "Failed to perform vdsm-gencerts action: {}".format(e)
        raise RuntimeError(msg)


def configure():
    _exec_vdsm_gencerts()


def isconfigured():
    return YES if _certsExist() else NO


def _certsExist():
    return not config.getboolean('vars', 'ssl') or\
        os.path.isfile(pki.CERT_FILE)
