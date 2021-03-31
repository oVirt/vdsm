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
import threading

import six

from vdsm.common import concurrent
from vdsm.storage import devicemapper

log = logging.getLogger("storage.mpathhealth")


class MultipathStatus(object):

    def __init__(self, failed_paths, valid_paths):
        self.failed_paths = set(failed_paths)
        self.valid_paths = valid_paths

    def info(self):
        return {
            "failed_paths": sorted(self.failed_paths),
            "valid_paths": self.valid_paths,
        }


class Monitor(object):

    def __init__(self, interval=10):
        self._lock = threading.Lock()
        self._status = {}
        self._thread = None
        self._done = threading.Event()
        self._interval = interval
        self._thread = concurrent.thread(self._run,
                                         name="mpathhealth",
                                         log=log)
        # Used for synchronization during testing
        self.callback = _NULL_CALLBACK

    def start(self):
        self._done.clear()
        self._thread.start()

    def stop(self):
        self._done.set()

    def wait(self):
        self._thread.join()

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

    def _run(self):
        log.debug("starting multipath health monitoring")
        while True:
            try:
                self._update_status()
            except Exception:
                log.exception("multipath health update failed")
            finally:
                self.callback()
            if self._done.wait(self._interval):
                break
        log.debug("multipath health monitoring has stopped")

    def _update_status(self):
        """
        Implementation of the multipath health monitor thread.
        The status of the mpath devices is queried here.
        """
        status = {}
        for guid, paths in devicemapper.multipath_status().items():
            failed_paths = [p.name for p in paths if p.status == "F"]
            if failed_paths:
                valid_paths = len(paths) - len(failed_paths)
                mpath_status = MultipathStatus(failed_paths, valid_paths)
                status[guid] = mpath_status
                if valid_paths == 0:
                    log.warning(
                        "Multipath device %r has failed paths %r, no valid "
                        "paths",
                        guid, failed_paths)
                else:
                    log.info("Multipath device %r has failed paths %r,"
                             " %r valid paths",
                             guid, failed_paths, valid_paths)
        # Call to devicemapper.multipath_status() can block,
        # so we update the report status dictionary only when we are done.
        with self._lock:
            self._status = status


def _NULL_CALLBACK():
    pass
