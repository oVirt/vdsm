#
# Copyright 2012 Red Hat, Inc.
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

from vdsm import utils
from vdsm import constants


class LsBlkException(Exception):
    def __init__(self, rc):
        self.rc = rc
        self.message = 'lsblk execution failed with error code %s' % self.rc

    def __str__(self):
        return self.message


def _parseLsBlk(out):
    blkDict = {}
    for l in out:
        d = {}
        for t in l.split():
            k, v = t.split('=', 1)
            d[k] = v[1:-1]
        blkDict[d['KNAME']] = d
    return blkDict


def getLsBlk():
    rc, out, err = utils.execCmd([constants.EXT_LSBLK, '--all', '--bytes',
                                  '--pairs', '--output', 'KNAME,FSTYPE,UUID'])
    if rc:
        raise LsBlkException(rc)
    return _parseLsBlk(out)
