# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

import logging
import threading

from collections import namedtuple

from vdsm.common import concurrent
from vdsm.common.panic import panic

from . import exception as se

log = logging.getLogger("storage.spwd")

Lease = namedtuple("Lease", "lockspace,resource,disk")


class Watchdog:
    """
    Watchdog for master storage domain cluster lock.
    """

    def __init__(self, sd, check_interval, max_errors=3,
                 callback=lambda: None):
        """
        Arguments:
            sd (StorageDomain): storage domain to check. We watch the
                cluster lease on this storage domain.
            check_interval (float): Number of second to wait between
                checks.
            max_errors (int): Number of consecutive temporary errors
                allowed. If checking the cluster lease fails more than
                max_errors, the watchdog will panic.
            callback (callable): If set, called after every monitor cycle.
        """
        self._sd = sd
        self._check_interval = check_interval
        self._max_errors = max_errors
        self._callback = callback

        # The cluster lock must not change while we monitor. This is the
        # state that must be valid while we monitor.
        lease = sd.getClusterLease()
        self._lease = Lease(
            lockspace=sd.sdUUID,
            resource=lease.name,
            disk=(lease.path, lease.offset))

        # Number of temporary errors. Reset on every successful check.
        self._errors = 0

        # Condition protecting internal state. Locked in start(), stop()
        # and during check, so stopping the monitor requires waiting
        # until the currnet check is complete.
        self._cond = threading.Condition(threading.Lock())

        self._thread = concurrent.thread(self._run, name="spwd")
        self._running = False

    def start(self):
        log.info("Start watching cluster lock %s", self._lease)
        with self._cond:
            if self._running:
                raise RuntimeError("Watchdog already started")

            self._thread.start()
            self._running = True

    def stop(self):
        log.info("Stop watching cluster lock %s", self._lease)
        with self._cond:
            if not self._running:
                return

            self._running = False
            self._cond.notify()

        self._thread.join()

    def _run(self):
        while True:
            with self._cond:
                if not self._running:
                    break

                self._cond.wait(self._check_interval)
                if not self._running:
                    break

                try:
                    self._check()
                finally:
                    self._callback()

    def _check(self):
        try:
            resources = self._sd.inquireClusterLock()
        except se.SanlockInquireError as e:
            if e.is_temporary():
                if self._errors < self._max_errors:
                    # We will check again later in the next montioring
                    # cycle.
                    self._errors += 1
                    log.warning(
                        "Error (%s/%s) checking cluster lock %s",
                        self._errors, self._max_errors, self._lease)
                    return

            panic("Error checking cluster lock {}".format(self._lease))
        except Exception:
            panic("Unexpected error checking cluster lock {}"
                  .format(self._lease))

        # Reset errors on succesful inquire.
        self._errors = 0

        for r in resources:
            if r["lockspace"] != self._lease.lockspace:
                continue

            if r["resource"] != self._lease.resource:
                continue

            # Validate the cluster lease.

            if r["disks"] != [self._lease.disk]:
                panic("Invalid cluster lock disk exepcted={} actual={}"
                      .format(self._lease, r))

            log.debug("Found cluster lock %s", r)
            return

        panic("Cluster lock {} was lost".format(self._lease))
