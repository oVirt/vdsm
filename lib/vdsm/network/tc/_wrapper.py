# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division
import errno
import os

from vdsm.network import cmd

EXT_TC = '/sbin/tc'
_TC_ERR_PREFIX = 'RTNETLINK answers: '
_errno_trans = dict(((os.strerror(code), code) for code in errno.errorcode))


def process_request(command):
    command.insert(0, EXT_TC)
    retcode, out, err = cmd.exec_sync(command)
    if retcode != 0:
        if retcode == 2 and err:
            for err_line in err.splitlines():
                if err_line.startswith(_TC_ERR_PREFIX):
                    err = err_line
                    retcode = _errno_trans.get(
                        err[len(_TC_ERR_PREFIX) :].strip()  # noqa: E203
                    )
                    break
        raise TrafficControlException(retcode, err, command)
    return out


class TrafficControlException(Exception):
    def __init__(self, errCode, message, command):
        self.errCode = errCode
        self.msg = message
        self.command = command
        Exception.__init__(self, self.errCode, self.msg, self.command)
