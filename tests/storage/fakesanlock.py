#
# Copyright 2016 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from __future__ import absolute_import
from __future__ import division

import errno
import threading
from testlib import maybefail

from vdsm.storage.compat import sanlock


class FakeSanlock(object):
    """
    With fake sanlock, you can use code depending on sanlock, without
    running a sanlock daemon, and waiting for slow sanlock operations
    such as adding a lockspace.

    You can also simulate any error from sanlock by setting an error in
    the errors dictionary before calling a method.

    To test code importing sanlock, monkeypatch sanlock module::

        from fakesanlock import FakeSanlock

        @MonkeyPatch(module, "sanlock", FakeSanlock())
        def test_module(self):
            ...
    """

    # Copied from sanlock src/sanlock_rv.h
    SANLK_LEADER_MAGIC = -223

    class SanlockException(Exception):
        @property
        def errno(self):
            return self.args[0]

    def __init__(self):
        self.spaces = {}
        self.resources = {}
        self.errors = {}
        self.hosts = {}

    def check_lockspace_initialized(self, lockspace):
        # TODO: check that sanlock was initialized may need to be added also
        # into other places beside add_lockspace. Find all relevant places.
        if lockspace not in self.spaces:
            raise self.SanlockException(
                self.SANLK_LEADER_MAGIC, "Sanlock lockspace add failure",
                "Lease does not exist on storage")

    def check_lockspace_location(self, lockspace, path, offset):
        if lockspace["path"] != path or lockspace["offset"] != offset:
            raise self.SanlockException(
                errno.EINVAL, "Sanlock lockspace add failure",
                "Invalid argument")

    @maybefail
    def add_lockspace(self, lockspace, host_id, path, offset=0, iotimeout=0,
                      **kwargs):
        """
        Add a lockspace, acquiring a host_id in it. If async is True the
        function will return immediatly and the status can be checked
        using inq_lockspace.  The iotimeout option configures the io
        timeout for the specific lockspace, overriding the default value
        (see the sanlock daemon parameter -o).
        """

        self.check_lockspace_initialized(lockspace)
        ls = self.spaces[lockspace]
        self.check_lockspace_location(ls, path, offset)

        if "host_id" in ls:
            raise self.SanlockException(
                errno.EEXIST, "Sanlock lockspace add failure", "File exists")

        wait = not kwargs.get('async', False)

        generation = 0
        host = self.hosts.get(host_id)
        if host:
            generation = host["generation"] + 1

        host = {"id": host_id,
                "generation": generation,
                "flags": sanlock.HOST_LIVE}
        self.hosts[host_id] = host

        # Mark the locksapce as not ready, so callers of inq_lockspace will
        # wait until it is added.
        ls["host_id"] = host_id
        # TODO: check the real sanlock semantics if iotimeout is different from
        # iotimeout provided during initialization.
        ls["iotimeout"] = iotimeout
        ls["ready"] = threading.Event()

        def complete():
            # Wake up threads waiting on inq_lockspace()
            ls["ready"].set()

        if wait:
            complete()
        else:
            # The test must call complete_async().
            ls["complete"] = complete

    @maybefail
    def rem_lockspace(self, lockspace, host_id, path, offset=0,
                      unused=False, **kwargs):
        """
        Remove a lockspace, releasing the acquired host_id. If async is
        True the function will return immediately and the status can be
        checked using inq_lockspace. If unused is True the command will
        fail (EBUSY) if there is at least one acquired resource in the
        lockspace (instead of automatically release it).
        """
        wait = not kwargs.get('async', False)
        ls = self.spaces[lockspace]

        # Mark the locksapce as not ready, so callers of inq_lockspace will
        # wait until it is removed.
        ls["ready"].clear()

        def complete():
            # Delete the lockspace and wake up threads waiting on
            # inq_lockspace().
            # Lockspace shouldn't be removed completely, as this would mean
            # that sanlock on given path wasn't initialized. Instead just
            # remove host_id from lockspace.
            del ls["host_id"]
            ls["ready"].set()

        if wait:
            complete()
        else:
            # The test must call complete_async().
            ls["complete"] = complete

    def complete_async(self, lockspace):
        """
        This is a special method for testing, simulating completion of an async
        operation started by add_lockspace() or rem_lockspace().
        """
        ls = self.spaces[lockspace]
        complete = ls.pop("complete")
        complete()

    @maybefail
    def inq_lockspace(self, lockspace, host_id, path, offset=0, wait=False):
        """
        Return True if the sanlock daemon currently owns the host_id in
        lockspace, False otherwise. The special value None is returned
        when the daemon is still in the process of acquiring or
        releasing the host_id.  If the wait flag is set to True the
        function will block until the host_id is either acquired or
        released.
        """
        try:
            ls = self.spaces[lockspace]
        except KeyError:
            return False

        if wait:
            ls["ready"].wait()
        elif not ls["ready"].is_set():
            return None

        return "host_id" in ls

    @maybefail
    def write_resource(self, lockspace, resource, disks, max_hosts=0,
                       num_hosts=0):
        # We never use more then one disk, not sure why sanlock supports more
        # then one. Fail if called with multiple disks.
        assert len(disks) == 1

        path, offset = disks[0]
        self.resources[(path, offset)] = {"lockspace": lockspace,
                                          "resource": resource,
                                          "version": 0,
                                          "acquired": False}

    @maybefail
    def read_resource(self, path, offset=0):
        key = (path, offset)
        if key not in self.resources:
            raise self.SanlockException(self.SANLK_LEADER_MAGIC,
                                        "Sanlock resource read failure",
                                        "Sanlock excpetion")
        return self.resources[key]

    def register(self):
        """
        Register to sanlock daemon and return the connection fd.
        """
        return 42

    def acquire(self, lockspace, resource, disks, slkfd=None, pid=None,
                shared=False, version=None):
        """
        Acquire a resource lease for the current process (using the
        slkfd argument to specify the sanlock file descriptor) or for an
        other process (using the pid argument). If shared is True the
        resource will be acquired in the shared mode. The version is the
        version of the lease that must be acquired or fail.  The disks
        must be in the format: [(path, offset), ... ].
        """
        # Do we have a lockspace?
        try:
            ls = self.spaces[lockspace]
        except KeyError:
            raise self.SanlockException(
                errno.ENOSPC, "No such lockspace %r" % lockspace)

        # Is it ready?
        if not ls["ready"].is_set():
            raise self.SanlockException(
                errno.ENOSPC, "No such lockspace %r" % lockspace)

        key = disks[0]
        res = self.resources[key]
        if res["acquired"]:
            raise self.SanlockException(
                errno.EEXIST, 'Sanlock resource not acquired', 'File exists')

        res["acquired"] = True
        host_id = ls["host_id"]
        res["host_id"] = host_id
        res["generation"] = self.hosts[host_id]["generation"]
        # The actual sanlock uses a timestamp field as well, but for current
        # testing purposes it is not needed since it is not used by the tested
        # code

    def release(self, lockspace, resource, disks, slkfd=None, pid=None):
        """
        Release a resource lease for the current process.  The disks
        must be in the format: [(path, offset), ... ].
        """
        # Do we have a lockspace?
        try:
            self.spaces[lockspace]
        except KeyError:
            raise self.SanlockException(
                errno.ENOSPC, "No such lockspace %r" % lockspace)

        key = disks[0]
        res = self.resources[key]
        if not res["acquired"]:
            raise self.SanlockException(
                errno.EPERM, 'Sanlock resource not released',
                'Operation not permitted')

        res["acquired"] = False
        res["host_id"] = 0
        res["generation"] = 0

    def read_resource_owners(self, lockspace, resource, disks):
        try:
            self.spaces[lockspace]
        except KeyError:
            # Sanlock can return two kind of error:
            #  - EINVAL in case device wasn't initialized
            #  - EMSGSIZE in case device doesn't exists
            # As we don't do checks that device exists (or have an option to
            # simulate it (doesn't) exists), assume here that device exists and
            # raise EINVAL error.
            raise self.SanlockException(
                errno.EINVAL, "Unable to read resource owners",
                "Invalid argument")

        key = disks[0]
        res = self.resources[key]

        # The lease is not owned, return empty list
        if res.get("host_id", 0) == 0:
            return []

        # The actual sanlock also returns timestamp and host flags but we do
        # not use these fields in the tested code so they are not added
        return [{
            "host_id": res["host_id"],
            "generation": res["generation"]
        }]

    def get_hosts(self, lockspace, host_id=0):
        try:
            self.spaces[lockspace]
        except KeyError:
            raise self.SanlockException(
                errno.ENOSPC, "No such lockspace %r" % lockspace)

        return [self.hosts[host_id]]

    def init_lockspace(self, lockspace, path, offset=0, max_hosts=0,
                       num_hosts=0, use_aio=False):
        """
        Initialize a device to be used as sanlock lock space.
        In our case, we just create empty dictionary for a lockspace.
        """
        ls = {
            "path": path,
            "offset": offset,
            "max_hosts": max_hosts,
            "num_hosts": num_hosts,
            "use_aio": use_aio,
        }

        # Real sanlock just overwrites lockspace if it was already initialized.
        self.spaces[lockspace] = ls

    def init_resource(self, lockspace, resource, disks, max_hosts=0,
                      num_hosts=0, use_aio=True):
        self.write_resource(lockspace, resource, disks, max_hosts=max_hosts,
                            num_hosts=num_hosts)
