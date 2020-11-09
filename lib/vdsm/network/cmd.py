# Copyright 2017-2020 Red Hat, Inc.
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

from __future__ import absolute_import
from __future__ import division

import uuid

from vdsm.common import systemd
from vdsm.common.cmdutils import exec_cmd as exec_sync_bytes
from vdsm.network.common import conversion_util


def exec_sync(cmds):
    """Execute a command and convert returned values to native string.

    Note that this function should not be used if output data could be
    undecodable bytes.
    """
    retcode, out, err = exec_sync_bytes(cmds)
    return retcode, conversion_util.to_str(out), conversion_util.to_str(err)


def exec_systemd_new_unit(cmds, slice_name):
    # TODO: We set unique uuid for every run to not use the same unit twice
    # and prevent systemd-run race (BZ#1259468). This uuid could be dropped
    # when BZ#1272368 will be solved or when we use systemd >= v220.
    unit = uuid.uuid4()
    cmds = systemd.wrap(cmds, scope=True, unit=unit, slice=slice_name)

    return exec_sync(cmds)
