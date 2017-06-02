# Copyright 2017 Red Hat, Inc.
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

import logging
import subprocess
import uuid

from vdsm.common import cmdutils
from vdsm.common.cmdutils import systemd_run
from vdsm.common.compat import CPopen as Popen


def exec_sync(cmds):
    """Execute a command and convert returned values to native string.

    Native string format is bytes for Python 2 and unicode for Python 3. It is
    important to keep data in a native format for seamless usage of output data
    in the rest of Python codebase.

    Note that this function should not be used if output data could be
    undecodable bytes.
    """
    retcode, out, err = exec_sync_bytes(cmds)
    return retcode, _to_str(out), _to_str(err)


def exec_systemd_new_unit(cmds, slice_name):
    # TODO: We set unique uuid for every run to not use the same unit twice
    # and prevent systemd_run race (BZ#1259468). This uuid could be dropped
    # when BZ#1272368 will be solved or when we use systemd >= v220.
    unit = uuid.uuid4()
    cmds = systemd_run(cmds, scope=True, unit=unit, slice=slice_name)

    return exec_sync(cmds)


def exec_sync_bytes(cmds):
    logging.debug(cmdutils.command_log_line(cmds))

    p = Popen(
        cmds, close_fds=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    out, err = p.communicate()

    logging.debug(cmdutils.retcode_log_line(p.returncode, err=err))

    return p.returncode, out, err


def _to_str(value):
    """Convert textual value to native string.

    Passed value (bytes output of Popen communicate) will be returned as
    a native str value (bytes in Python 2, unicode in Python 3).
    """
    if isinstance(value, str):
        return value
    elif isinstance(value, bytes):
        return value.decode('utf-8')
    else:
        raise ValueError(
            'Expected a textual value, given {} of type {}.'.format(
                value, type(value)))
