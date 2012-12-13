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

from vdsm import constants
from vdsm import utils


class BlockIdException(Exception):
    def __init__(self, rc, attrib):
        self.rc = rc
        self.attrib = attrib
        if self.rc == 2:
            self.message = \
                'device could not be identified for %s' % self.attrib
        elif self.rc == 4:
            self.message = 'blkid usage or other errors for %s' % self.attrib
        elif self.rc == 8:
            self.message = 'ambivalent low-level probing result was ' + \
                'detected for %s' % self.attrib
        else:
            self.message = 'blkid execution failed with error ' + \
                'code %s for %s' % (self.rc, self.attrib)

    def __str__(self):
        return self.message


def getDeviceByUuid(uuid):
    rc, out, err = utils.execCmd([constants.EXT_BLKID, '-U', uuid])
    if rc:
        raise BlockIdException(rc, uuid)
    return out[0]
