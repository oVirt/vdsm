# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
import itertools
import logging
import operator

log = logging.getLogger('storage.guarded')


class ReleaseError(Exception):
    pass


class Deadlock(Exception):
    msg = "Attempt to lock will deadlock: {self.locks}"

    def __init__(self, locks):
        self.locks = locks

    def __str__(self):
        return self.msg.format(self=self)


class context(object):
    """
    A context manager to lock groups of storage entities for an operation.

    When performing an operation on storage (eg. copying data from one volume
    to another volume), the entities (volumes) must be locked to protect them
    from conflicting access by other threads of this application and (in the
    future) from simultaneous access by other hosts.  This requires the use of
    multiple layers of locks and rigid lock ordering rules to prevent deadlock.

    This class receives a variable number of lock lists corresponding to each
    entity involved in an operation.  The locks from all entities are grouped
    together and any duplicate locks removed.  Next, the locks are sorted by
    namespace and then by name.  When entering the context the locks are
    acquired in sorted order.  When exiting the context the locks are released
    in reverse order.  Errors are handled as gracefully as possible with any
    acquired locks being released in the proper order.

    Attemping to lock the same lock twice with differnt mode is not supported
    and will raise a Deadlock exception with the conflicting locks.
    """

    def __init__(self, locks):
        """
        Receives a variable number of locks which must descend from
        AbstractLock.  The locks are deduplicated and sorted.
        """
        self._locks = self._validate(locks)
        self._held_locks = []

    def _validate(self, locks):
        """
        Remove duplicate locks and sort the locks.

        Raises Deadlock if trying to take the same lock with different modes.
        """
        locks = sorted(set(locks))
        by_ns_name = operator.attrgetter("ns", "name")
        for _, group in itertools.groupby(locks, by_ns_name):
            group = list(group)
            if len(group) > 1:
                raise Deadlock(group)
        return locks

    def __enter__(self):
        for lock in self._locks:
            try:
                lock.acquire()
            except Exception as exc:
                log.error("Error acquiring lock %r", lock)
                try:
                    self._release()
                except ReleaseError:
                    log.exception("Error releasing locks")
                raise exc

            self._held_locks.append(lock)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            self._release()
        except ReleaseError:
            if exc_type is None:
                raise
            # Don't hide the original error
            log.exception("Error releasing locks")
            return False

    def _release(self):
        errors = []
        while self._held_locks:
            lock = self._held_locks.pop()
            try:
                lock.release()
            except Exception as e:
                errors.append(e)
        if errors:
            raise ReleaseError(errors)


class AbstractLock(object):
    @property
    def ns(self):
        raise NotImplementedError

    @property
    def name(self):
        raise NotImplementedError

    @property
    def mode(self):
        raise NotImplementedError

    def acquire(self):
        raise NotImplementedError

    def release(self):
        raise NotImplementedError

    def __eq__(self, other):
        return self._key() == other._key()

    def __ne__(self, other):
        return not self == other

    def __lt__(self, other):
        return (self.ns, self.name) < (other.ns, other.name)

    def __hash__(self):
        return hash(self._key())

    def _key(self):
        return type(self), self.ns, self.name, self.mode

    def __repr__(self):
        return "<%s ns=%s, name=%s, mode=%s at 0x%x>" % (
            self.__class__.__name__, self.ns, self.name, self.mode, id(self))
