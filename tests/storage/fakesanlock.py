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

from vdsm.storage import constants as sc
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

    # Tuples with supported alignment and sector size.
    # Copied from python/sanlock.c
    ALIGN_SIZE = (1048576, 2097152, 4194304, 8388608)
    SECTOR_SIZE = (512, 4096)

    class SanlockException(Exception):
        @property
        def errno(self):
            return self.args[0]

    def __init__(self, sector_size=sc.BLOCK_SIZE_512):
        self.spaces = {}
        self.resources = {}
        self.errors = {}
        self.hosts = {}
        # As fake sanlock keeps everything only in memory, this mimics
        # sector size of underlying storage.
        self.sector_size = sector_size

    def check_align_and_sector(
            self, align, sector, resource=None, check_sector=True):
        """
        Check that alignment and sector size contain valid values.
        This means that the values are in ALIGN_SIZE/SECTOR_SIZE tuple
        and if the resource dict is provided, that these values match
        the values in the dict. This dict can represent either lockspace
        or resource. In any case, it has to contain "align" and "sector"
        keys. We also check, that sector size is same as sector size of
        underlying storage. This check can be skipped if check_sector is set to
        False. This is a workaround which mimics real sanlock behavior: sanlock
        write_resource() doesn't fail even if it's called with different sector
        size than underlying sector size.
        """
        # Check that alignment and sector size are among values supported by
        # sanlock

        if align not in self.ALIGN_SIZE:
            raise ValueError("Invalid align value: %d" % align)

        if sector not in self.SECTOR_SIZE:
            raise ValueError("Invalid sector value: %d" % sector)

        # Check that sector size is same underlying storage sector size
        if check_sector and self.sector_size != sector:
            raise self.SanlockException(
                errno.EINVAL, "Invalid sector size", "Invalid argument")

        # Check that alignment and sector size is same as alignment and sector
        # size of previously written resource
        if resource:
            if align != resource["align"]:
                raise self.SanlockException(
                    errno.EINVAL, "Invalid alignment", "Invalid argument")

            if sector != resource["sector"]:
                raise self.SanlockException(
                    errno.EINVAL, "Invalid sector size", "Invalid argument")

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
        self._validate_bytes(lockspace)
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
        self._validate_bytes(lockspace)
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
        self._validate_bytes(lockspace)
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
                       num_hosts=0, align=ALIGN_SIZE[0],
                       sector=SECTOR_SIZE[0]):
        # Validate lockspace and resource names are given as bytes.
        self._validate_bytes(lockspace)
        self._validate_bytes(resource)
        # We never use more then one disk, not sure why sanlock supports more
        # then one. Fail if called with multiple disks.
        assert len(disks) == 1

        # Here we skip check underlying sector size is same as one we use in
        # this call, as real sanlock always succeeds.
        self.check_align_and_sector(align, sector, check_sector=False)

        path, offset = disks[0]
        self.resources[(path, offset)] = {"lockspace": lockspace,
                                          "resource": resource,
                                          "version": 0,
                                          "acquired": False,
                                          "align": align,
                                          "sector": sector,
                                          }

    @maybefail
    def read_resource(
            self, path, offset=0, align=ALIGN_SIZE[0], sector=SECTOR_SIZE[0]):
        key = (path, offset)
        if key not in self.resources:
            raise self.SanlockException(self.SANLK_LEADER_MAGIC,
                                        "Sanlock resource read failure",
                                        "Sanlock excpetion")

        self.check_align_and_sector(
            align, sector, resource=self.resources[key])

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
        # Validate lockspace and resource names are given as bytes.
        self._validate_bytes(lockspace)
        self._validate_bytes(resource)
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
        # Validate lockspace and resource names are given as bytes.
        self._validate_bytes(lockspace)
        self._validate_bytes(resource)
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

    def read_resource_owners(
            self, lockspace, resource, disks, align=ALIGN_SIZE[0],
            sector=SECTOR_SIZE[0]):
        # Validate lockspace and resource name are given as bytes.
        self._validate_bytes(lockspace)
        self._validate_bytes(resource)
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

        self.check_align_and_sector(align, sector, resource=res)

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
        self._validate_bytes(lockspace)
        try:
            self.spaces[lockspace]
        except KeyError:
            raise self.SanlockException(
                errno.ENOSPC, "No such lockspace %r" % lockspace)

        return [self.hosts[host_id]]

    def write_lockspace(self, lockspace, path, offset=0, max_hosts=0,
                        iotimeout=0, align=ALIGN_SIZE[0],
                        sector=SECTOR_SIZE[0]):
        """
        Initialize a device to be used as sanlock lock space.
        In our case, we just create empty dictionary for a lockspace.
        """
        self._validate_bytes(lockspace)
        self.check_align_and_sector(align, sector)

        ls = {
            "path": path,
            "offset": offset,
            "max_hosts": max_hosts,
            "iotimeout": 0,
            "align": align,
            "sector": sector,
        }

        # Real sanlock just overwrites lockspace if it was already initialized.
        self.spaces[lockspace] = ls

    def _validate_bytes(self, arg):
        if not isinstance(arg, bytes):
            raise TypeError("Argument type is not bytes: %r" % arg)
