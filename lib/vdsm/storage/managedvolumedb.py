#
# Copyright 2019 Red Hat, Inc.
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

import logging
import threading

from vdsm import utils

log = logging.getLogger("storage.managedvolumedb")

_lock = threading.Lock()
_db = {}


class Error(Exception):
    """ Base class for managed volume db errors """


class NotFound(Error):
    """ Managed volume not found """


class VolumeAlreadyExists(Error):
    """ Managed volume already exists """


def get(vol_id):
    with _lock:
        try:
            log.debug("Getting Managed volume %s ", vol_id)
            return utils.picklecopy(_db[vol_id])
        except KeyError:
            raise NotFound("Managed volume {} not found".format(vol_id))


def add(vol_id, vol_info):
    with _lock:
        if vol_id in _db:
            raise VolumeAlreadyExists(vol_id)
        log.info("Adding Managed volume %s to DB.", vol_id)
        _db[vol_id] = vol_info


def remove(vol_id):
    with _lock:
        _db.pop(vol_id, None)
        log.info("Removing Managed volume %s from DB.", vol_id)


def update(vol_id, path=None, attachment=None, multipath_id=None):
    with _lock:
        try:
            vol_info = _db[vol_id]
        except KeyError:
            raise NotFound("Managed volume {} not found".format(vol_id))

        log.debug("Updating Managed volume %s path %s attachment %s "
                  "multipath_id %s.",
                  vol_id, path, attachment, multipath_id)
        if attachment:
            vol_info['attachment'] = attachment
        if path:
            vol_info['path'] = path
        if multipath_id:
            vol_info['multipath_id'] = multipath_id


# This should only be used by test code!
def _clear():
    with _lock:
        _db.clear()
