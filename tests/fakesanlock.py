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

from testlib import maybefail


class FakeSanlock(object):
    """
    With fake sanlock, you can write and read resources without running a
    sanlock daemon.

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
        self.resources = {}
        self.errors = {}

    @maybefail
    def write_resource(self, lockspace, resource, disks, max_hosts=0,
                       num_hosts=0):
        # We never use more then one disk, not sure why sanlock supports more
        # then one. Fail if called with multiple disks.
        assert len(disks) == 1

        path, offset = disks[0]
        self.resources[(path, offset)] = {"lockspace": lockspace,
                                          "resource": resource,
                                          "version": 0}

    @maybefail
    def read_resource(self, path, offset=0):
        key = (path, offset)
        if key not in self.resources:
            raise self.SanlockException(self.SANLK_LEADER_MAGIC,
                                        "Sanlock resource read failure",
                                        "Sanlock excpetion")
        return self.resources[key]
