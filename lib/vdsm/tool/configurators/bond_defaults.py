# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import os

from vdsm import constants
from vdsm.network.link.bond import sysfs_options_mapper

from . import YES, NO


BONDING_DEFAULTS = constants.P_VDSM_RUN + 'bonding-defaults.json'


def isconfigured():
    return YES if os.path.exists(BONDING_DEFAULTS) else NO


def configure():
    sysfs_options_mapper.dump_bonding_options()
