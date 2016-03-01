# Copyright 2014 Red Hat, Inc.
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
import errno
import os

from vdsm.constants import EXT_TC
from vdsm.utils import execCmd

_TC_ERR_PREFIX = 'RTNETLINK answers: '
_errno_trans = dict(((os.strerror(code), code) for code in errno.errorcode))


def process_request(command):
    command.insert(0, EXT_TC)
    retcode, out, err = execCmd(command, raw=True)
    if retcode != 0:
        if retcode == 2 and err:
            for err_line in err.splitlines():
                if err_line.startswith(_TC_ERR_PREFIX):
                    err = err_line
                    retcode = _errno_trans.get(
                        err[len(_TC_ERR_PREFIX):].strip())
                    break
        raise TrafficControlException(retcode, err, command)
    return out


class TrafficControlException(Exception):
    def __init__(self, errCode, message, command):
        self.errCode = errCode
        self.message = message
        self.command = command
        Exception.__init__(self, self.errCode, self.message, self.command)
