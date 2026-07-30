# SPDX-FileCopyrightText: oVirt Developers
# SPDX-License-Identifier: GPL-2.0-or-later

import logging
import threading

from collections import namedtuple

import sanlock

from vdsm.common import concurrent
from vdsm.common.panic import panic

from . import clusterlock

log = logging.getLogger("storage.spwd")

Lease = namedtuple("Lease", "lockspace,resource,disk")


class Watchdog:
    """
    Watchdog for master storage domain cluster lock.
    """

    def __init__(
        self, sd, check_interval, host_id, max_errors=3, callback=lambda: None
    ):
        """
        Arguments:
            sd (StorageDomain): storage domain to check. We watch the
                cluster lease on this storage domain.
            check_interval (float): Number of second to wait between
                checks.
            host_id (int): sanlock host id of this host. Used to verify that
                the cluster lease is still owned by us on disk. If the lease
                is free or owned by another host, the watchdog panics to
                stop a stale in-memory SPM role from corrupting pool
                metadata (split brain).
            max_errors (int): Number of consecutive temporary errors
                allowed. If checking the cluster lease fails more than
                max_errors, the watchdog will panic.
            callback (callable): If set, called after every monitor cycle.
        """
        self._sd = sd
        self._check_interval = check_interval
        self._host_id = host_id
        self._max_errors = max_errors
        self._callback = callback

        # The cluster lock must not change while we monitor. This is the
        # state that must be valid while we monitor.
        lease = sd.getClusterLease()
        self._lease = Lease(
            lockspace=sd.sdUUID,
            resource=lease.name,
            disk=(lease.path, lease.offset),
        )

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
            version, owner = self._sd.inspectClusterLock()
        except clusterlock.TemporaryFailure as e:
            # add_lockspace has not completed yet.
            self._handle_transient_error(e)
            return
        except sanlock.SanlockException as e:
            # Storage I/O error reading the lease. Transient - wdmd/softdog
            # fences the host if storage does not recover.
            self._handle_transient_error(e)
            return
        except Exception:
            panic(
                "Unexpected error checking cluster lock {}".format(self._lease)
            )

        # Reset errors on a successful inspect.
        self._errors = 0

        if owner is None:
            # Free on disk - e.g. generation mismatch after reconnecting
            # following a blip. We no longer hold it despite our in-memory
            # SPM role.
            panic(
                "Cluster lock {} was lost (no valid owner on disk)".format(
                    self._lease
                )
            )

        if owner != self._host_id:
            # Held by another host - split brain: our in-memory SPM role
            # is stale.
            panic(
                "Cluster lock {} is held by host {} (expected {})".format(
                    self._lease, owner, self._host_id
                )
            )

        log.debug(
            "Cluster lock %s held by host %s (version %s)",
            self._lease,
            owner,
            version,
        )

    def _handle_transient_error(self, e):
        if self._errors < self._max_errors:
            self._errors += 1
            log.warning(
                "Error (%s/%s) checking cluster lock %s: %s",
                self._errors,
                self._max_errors,
                self._lease,
                e,
            )
            return

        panic("Error checking cluster lock {}".format(self._lease))
