# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import

from vdsm import constants

from vdsm.storage import misc


def fuser(path, mountPoint=False):
    cmd = [constants.EXT_FUSER]
    if mountPoint:
        cmd.append("-m")

    cmd.append(path)
    (rc, out, err) = misc.execCmd(cmd, raw=True)
    if rc != 0:
        return []

    return [int(pid) for pid in out.split()]
