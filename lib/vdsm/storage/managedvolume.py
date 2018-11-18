#
# Copyright 2010-2019 Red Hat, Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

"""

This module provides needed interfaces to for attaching and detaching volumes:
    - connector_info() - returns connector info
    - attach_volume() - attach a volume according to the connection info
                        provided
    - detach_volume() - detach a volume according to the device info provided
"""

from __future__ import absolute_import
from __future__ import division

import json
import logging
import os

# TODO: Change to simple import when os_brick is available
#       and required
try:
    import os_brick
except ImportError:
    os_brick = None

from vdsm.common import cmdutils
from vdsm.common import commands
from vdsm.common import supervdsm

HELPER = '/usr/libexec/vdsm/managedvolume-helper'

log = logging.getLogger("storage.managedvolume")


class Error(Exception):
    """ managed volume operation failed """


class NotSupported(Error):
    """ managed volume operation not supported """


class HelperFailed(Error):
    """ managed volume operation helper failed """


def run_helper(sub_cmd):
    if os.geteuid() != 0:
        return supervdsm.getProxy().managedvolume_run_helper(sub_cmd)

    try:
        result = commands.run([HELPER, sub_cmd])
    except cmdutils.Error as e:
        raise HelperFailed("Error executing helper: %s" % e)
    try:
        return json.loads(result)
    except ValueError as e:
        raise HelperFailed("Error loading result: %s" % e)


def connector_info():
    """
    Get connector information from os-brick.
        If not running as root, use supervdsm to invoke this function as root.
    """
    if os_brick is None:
        raise NotSupported("Cannot import os_brick.initiator")

    log.debug("Starting get connector_info")
    return run_helper("connector_info")
