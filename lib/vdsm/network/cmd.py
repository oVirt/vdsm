# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

from vdsm.common.cmdutils import exec_cmd as exec_sync_bytes
from vdsm.network.common import conversion_util


def exec_sync(cmds):
    """Execute a command and convert returned values to native string.

    Note that this function should not be used if output data could be
    undecodable bytes.
    """
    retcode, out, err = exec_sync_bytes(cmds)
    return retcode, conversion_util.to_str(out), conversion_util.to_str(err)
