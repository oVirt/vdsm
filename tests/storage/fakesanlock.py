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
import os
import socket
import threading

from operator import itemgetter

from testlib import maybefail

from vdsm.storage import constants as sc
from vdsm.storage.compat import sanlock

LVB_POISON = b"x"


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

    # See sanlock_strerror for complete list.
    _ERRORS = {
        SANLK_LEADER_MAGIC: "Lease does not exist on storage",
    }

    # Tuples with supported alignment and sector size.
    # Copied from python/sanlock.c
    ALIGN_SIZE = (sc.ALIGNMENT_1M,
                  sc.ALIGNMENT_2M,
                  sc.ALIGNMENT_4M,
                  sc.ALIGNMENT_8M)
    SECTOR_SIZE = (sc.BLOCK_SIZE_512, sc.BLOCK_SIZE_4K)

    RES_LVER = 1
    RES_SHARED = 4

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
        self.process_socket = None

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
            raise self._error(errno.EINVAL, "Invalid sector size")

        # Check that alignment and sector size is same as alignment and sector
        # size of previously written resource
        if resource:
            if align != resource["align"]:
                raise self._error(errno.EINVAL, "Invalid alignment")

            if sector != resource["sector"]:
                raise self._error(errno.EINVAL, "Invalid sector size")

    def check_lockspace_initialized(self, lockspace, error):
        # TODO: check that sanlock was initialized may need to be added also
        # into other places beside add_lockspace. Find all relevant places.
        if lockspace not in self.spaces:
            raise self._error(self.SANLK_LEADER_MAGIC, error)

    def check_lockspace_location(self, lockspace, path, offset, error):
        if lockspace["path"] != path or lockspace["offset"] != offset:
            raise self._error(errno.EINVAL, error)

    @maybefail
    def add_lockspace(self, lockspace, host_id, path, offset=0, iotimeout=0,
                      wait=True):
        """
        Add a lockspace, acquiring a host_id in it. If wait is False the
        function will return immediatly and the status can be checked
        using inq_lockspace.  The iotimeout option configures the io
        timeout for the specific lockspace, overriding the default value
        (see the sanlock daemon parameter -o).
        """
        error = "Sanlock lockspace add failure"
        self._validate_bytes(lockspace)
        self.check_lockspace_initialized(lockspace, error)
        ls = self.spaces[lockspace]
        self.check_lockspace_location(ls, path, offset, error)

        if "host_id" in ls:
            raise self._error(errno.EEXIST, error)

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
                      unused=False, wait=True):
        """
        Remove a lockspace, releasing the acquired host_id. If wait is
        False the function will return immediately and the status can be
        checked using inq_lockspace. If unused is True the command will
        fail (EBUSY) if there is at least one acquired resource in the
        lockspace (instead of automatically release it).
        """
        self._validate_bytes(lockspace)
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
        self.resources[(path, offset)] = {
            "lockspace": lockspace,
            "resource": resource,
            "version": 0,
            "acquired": False,
            "align": align,
            "sector": sector,
            "lvb": False,
            "busy": False,
        }

    @maybefail
    def read_resource(
            self, path, offset=0, align=ALIGN_SIZE[0], sector=SECTOR_SIZE[0]):
        key = (path, offset)
        if key not in self.resources:
            raise self._error(
                self.SANLK_LEADER_MAGIC, "Sanlock resource read failure")

        self.check_align_and_sector(
            align, sector, resource=self.resources[key])

        res = self.resources[key].copy()

        # Omit keys not in real sanlock response.
        del res["lvb"]
        del res["busy"]

        return res

    def register(self):
        """
        Register to sanlock daemon and return the connection fd.
        """
        # This check is not done by real sanlock, but it is important to detect
        # wrong usage of the library.
        assert (self.process_socket is None or
                self.process_socket.fileno() == -1)

        self.process_socket = socket.socket(socket.AF_UNIX)
        return self.process_socket.fileno()

    def acquire(self, lockspace, resource, disks, slkfd=None, pid=None,
                shared=False, version=None, lvb=False):
        """
        Acquire a resource lease for the current process (using the
        slkfd argument to specify the sanlock file descriptor) or for an
        other process (using the pid argument). If shared is True the
        resource will be acquired in the shared mode. The version is the
        version of the lease that must be acquired or fail.  The disks
        must be in the format: [(path, offset), ... ].
        """
        error = "Sanlock resource not acquired"

        # Validate lockspace and resource names are given as bytes.
        self._validate_bytes(lockspace)
        self._validate_bytes(resource)

        # Validate slkfd.
        if slkfd is not None:
            if self.process_socket.fileno() == -1:
                raise self._error(errno.EPIPE, error)

            assert slkfd == self.process_socket.fileno()

        # Do we have a lockspace?
        try:
            ls = self.spaces[lockspace]
        except KeyError:
            raise self._error(errno.ENOSPC, error)

        # Is it ready?
        if not ls["ready"].is_set():
            raise self._error(errno.ENOSPC, error)

        key = disks[0]
        res = self.resources[key]
        if res["acquired"]:
            raise self._error(errno.EEXIST, error)

        res["acquired"] = True
        host_id = ls["host_id"]
        res["host_id"] = host_id
        res["generation"] = self.hosts[host_id]["generation"]
        res["lvb"] = lvb
        # The actual sanlock uses a timestamp field as well, but for current
        # testing purposes it is not needed since it is not used by the tested
        # code

    def release(self, lockspace, resource, disks, slkfd=None, pid=None):
        """
        Release a resource lease for the current process.  The disks
        must be in the format: [(path, offset), ... ].
        """
        error = "Sanlock resource not released"

        # Validate lockspace and resource names are given as bytes.
        self._validate_bytes(lockspace)
        self._validate_bytes(resource)

        # Validate slkfd.
        if slkfd is not None:
            if self.process_socket.fileno() == -1:
                raise self._error(errno.EPIPE, error)

            assert slkfd == self.process_socket.fileno()

        # Do we have a lockspace?
        try:
            self.spaces[lockspace]
        except KeyError:
            raise self._error(errno.ENOSPC, error)

        key = disks[0]
        res = self.resources[key]
        if not res["acquired"]:
            raise self._error(errno.EPERM, error)

        res["acquired"] = False
        res["host_id"] = 0
        res["generation"] = 0
        res["lvb"] = False
        res["busy"] = False

    def inquire(self, slkfd=-1, pid=-1):
        # Matches sanlock.c error.
        if slkfd == -1 and pid == -1:
            raise self._error(errno.EINVAL, "Invalid slkfd and pid values")

        # Validate slkfd.
        if slkfd != -1:
            if self.process_socket.fileno() == -1:
                raise self._error(errno.EPIPE, "Inquire error")

            assert slkfd == self.process_socket.fileno()

        result = []

        for disk, res in self.resources.items():
            # This state is not expected in vdsm since we serialize calls to
            # acquire(), release(), and inquire(), but I'm not 100% sure that
            # this is not possible.
            if res["busy"]:
                raise self._error(errno.EBUSY, "Inquire error")

            if res["acquired"]:
                info = {
                    "lockspace": res["lockspace"],
                    "resource": res["resource"],
                    # We use only exclusive resources.
                    "flags": self.RES_LVER,
                    "version": res["version"],
                    "disks": [disk],
                }
                result.append(info)

        return result

    def read_resource_owners(
            self, lockspace, resource, disks, align=ALIGN_SIZE[0],
            sector=SECTOR_SIZE[0]):
        error = "Unable to read resource owners"

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
            raise self._error(errno.EINVAL, error)

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
        error = "Sanlock get hosts failure"
        self._validate_bytes(lockspace)
        try:
            self.spaces[lockspace]
        except KeyError:
            raise self._error(errno.ENOSPC, error)

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

    def dump_leases(
            self, path, offset=0, size=None, block_size=None, alignment=None):
        dump = []

        for (rpath, roffset), rinfo in self.resources.items():
            if rpath == path and self._in_range(roffset, offset, size):
                rec = {
                    'offset': roffset,
                    'lockspace': rinfo['lockspace'].decode('utf-8'),
                    'resource': rinfo['resource'].decode('utf-8'),
                    'timestamp': 0,
                    'own': 0,
                    'gen': 0,
                    'lver': 0
                }
                dump.append(rec)

        dump.sort(key=itemgetter('offset'))
        return iter(dump)

    def dump_lockspace(
            self, path, offset=0, size=None, block_size=None, alignment=None):
        dump = []

        for lsname, lsinfo in self.spaces.items():
            loffset = lsinfo['offset']
            lpath = lsinfo['path']
            if lpath == path and self._in_range(loffset, offset, size):
                rec = {
                    'offset': loffset,
                    'lockspace': lsname.decode('utf-8'),
                    'resource': lsinfo.get('host_id', 0),
                    'timestamp': 0,
                    'own': 0,
                    'gen': 0
                }
                dump.append(rec)

        dump.sort(key=itemgetter('offset'))
        return iter(dump)

    def set_lvb(self, lockspace, resource, disks, data):
        self._validate_bytes(lockspace)
        self._validate_bytes(resource)
        error = "Unable to set lvb"

        # Do we have a lockspace?
        try:
            ls = self.spaces[lockspace]
        except KeyError:
            raise self._error(errno.ENOSPC, error)

        if len(data) > 4096 or len(data) > ls["sector"]:
            raise self._error(errno.E2BIG, error)

        path, offset = disks[0]
        res = self.resources[(path, offset)]
        self._validate_lvb_set(res, error)

        # poison the remaining space in the sector to ensure it is properly
        # initialized by callers
        res["lvb_data"] = data.ljust(ls["sector"], LVB_POISON)

    def get_lvb(self, lockspace, resource, disks, size):
        self._validate_bytes(lockspace)
        self._validate_bytes(resource)
        self._validate_int(size)
        error = "Unable to get lvb"

        if size < 1 or size > 4096:
            raise ValueError(
                "Invalid size %d, must be in range: 0 < size <= 4096",
                size)

        # Do we have a lockspace?
        try:
            self.spaces[lockspace]
        except KeyError:
            raise self._error(errno.ENOSPC, error)

        path, offset = disks[0]
        res = self.resources[(path, offset)]
        self._validate_lvb_set(res, error)

        if "lvb_data" not in res:
            return b"\0" * size

        return res["lvb_data"][:size]

    def _in_range(self, offset, start=0, size=None):
        if offset < start:
            return False
        if size is not None and offset >= start + size:
            return False
        return True

    def _validate_bytes(self, arg):
        if not isinstance(arg, bytes):
            raise TypeError("Argument type is not bytes: %r" % arg)

    def _validate_int(self, arg):
        if not isinstance(arg, int):
            raise TypeError("Argument type is not int: %r" % arg)

    def _validate_lvb_set(self, resource, error):
        if not resource["lvb"]:
            # Sanlock returns error 2 if we try to write LVB without
            # acquiring first with the lvb flag
            raise self._error(errno.ENOENT, error)

    def _error(self, code, msg):
        """
        See python/sanlock.c in sanlock source for more info.
        """
        error = os.strerror(code) if code > 0 else self._ERRORS[code]
        return self.SanlockException(code, msg, error)
