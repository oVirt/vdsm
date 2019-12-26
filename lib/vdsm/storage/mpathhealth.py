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

import collections
import logging
import threading

import six

from vdsm.storage import devicemapper
from vdsm.storage import udev

log = logging.getLogger("storage.mpathhealth")


class MultipathStatus(object):

    def __init__(self, failed_paths=(), valid_paths=None, dm_seqnum=-1):
        self.failed_paths = set(failed_paths)
        self.valid_paths = valid_paths
        self.dm_seqnum = dm_seqnum

    def info(self):
        res = {"failed_paths": sorted(self.failed_paths)}
        if self.valid_paths is not None:
            res["valid_paths"] = self.valid_paths
        return res


class Monitor(udev.MultipathMonitor):

    def __init__(self):
        self._lock = threading.Lock()
        self._status = collections.defaultdict(MultipathStatus)

    def status(self):
        """
        Returns a dictionary containing the faulty paths and the number of
        valid paths for each device, with the mpath device UUID as the key.
        For example:
        {
            "uuid-2": {
                "valid_paths": 1,
                "failed_paths": [
                    "8:112",
                    "8:113"
                ]
            }
        }

        """
        res = {}
        with self._lock:
            for uuid, status in six.iteritems(self._status):
                res[uuid] = status.info()
        return res

    def start(self):
        """
        Implementation of the interface udev.MultipathMonitor.start()
        This method is called by the udev.MultipathListener and should not
        be called by others.

        The initial status of the mpath devices is built here.
        The data is updated through callbacks received in the handle method.
        """
        for guid, paths in devicemapper.multipath_status().items():
            failed_paths = [p.name for p in paths if p.status == "F"]
            if failed_paths:
                valid_paths = len(paths) - len(failed_paths)
                mpath_status = MultipathStatus(failed_paths, valid_paths)
                self._status[guid] = mpath_status
                if valid_paths == 0:
                    log.warn("Multipath device %r has failed paths %r,"
                             " no valid paths",
                             guid, failed_paths)
                else:
                    log.info("Multipath device %r has failed paths %r,"
                             " %r valid paths",
                             guid, failed_paths, valid_paths)

    def handle(self, event):
        """
        Implementation of the interface udev.MultipathMonitor.handle()
        This method is called by the udev.MultipathListener and should not
        be called by others.

        This method receives a udev.MultipathEvent and updates the internal
        data structure according to the event.
        """
        if event.type == udev.MPATH_REMOVED:
            self._mpath_removed(event)
        elif event.type == udev.PATH_FAILED:
            self._path_failed(event)
        elif event.type == udev.PATH_REINSTATED:
            self._path_reinstated(event)

    def _path_reinstated(self, event):
        with self._lock:
            mpath = self._status[event.mpath_uuid]
            mpath.failed_paths.discard(event.dm_path)
            if event.dm_seqnum > mpath.dm_seqnum:
                mpath.valid_paths = event.valid_paths
                mpath.dm_seqnum = event.dm_seqnum
            if not mpath.failed_paths:
                self._status.pop(event.mpath_uuid)
                log.info("Path %r reinstated for multipath device %r,"
                         " all paths are valid",
                         event.dm_path, event.mpath_uuid)
            else:
                log.info("Path %r reinstated for multipath device %r,"
                         " %d valid paths left",
                         event.dm_path, event.mpath_uuid, event.valid_paths)

    def _path_failed(self, event):
        with self._lock:
            mpath = self._status[event.mpath_uuid]
            mpath.failed_paths.add(event.dm_path)
            if event.dm_seqnum > mpath.dm_seqnum:
                mpath.valid_paths = event.valid_paths
                mpath.dm_seqnum = event.dm_seqnum
            if event.valid_paths == 0:
                log.warn("Path %r failed for multipath device %r,"
                         " no valid paths left",
                         event.dm_path, event.mpath_uuid)
            else:
                log.info("Path %r failed for multipath device %r,"
                         " %d valid paths left",
                         event.dm_path, event.mpath_uuid, event.valid_paths)

    def _mpath_removed(self, event):
        with self._lock:
            log.info("Multipath device %r was removed", event.mpath_uuid)
            self._status.pop(event.mpath_uuid, None)
