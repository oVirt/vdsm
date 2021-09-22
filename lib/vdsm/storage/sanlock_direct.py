#
# Copyright 2020 Red Hat, Inc.
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

import logging
import os

from vdsm.common import commands
from vdsm.common import supervdsm
from vdsm.common.cmdutils import CommandPath
from vdsm.storage import constants as sc

SANLOCK = CommandPath('sanlock', '/usr/sbin/sanlock')

_LEASES_FIELDS = [
    ("offset", int),
    ("lockspace", str),
    ("resource", str),
    ("timestamp", int),
    ("own", int),
    ("gen", int),
    ("lver", int)
]

_LOCKSPACE_FIELDS = [
    ("offset", int),
    ("lockspace", str),
    ("resource", str),
    ("timestamp", int),
    ("own", int),
    ("gen", int)
]


log = logging.getLogger("storage.sanlock_direct")


def dump_leases(
        path,
        offset=0,
        size=None,
        block_size=sc.BLOCK_SIZE_512,
        alignment=sc.ALIGNMENT_1M):

    return _dump(
        _LEASES_FIELDS,
        path,
        offset=offset,
        size=size,
        block_size=block_size,
        alignment=alignment)


def dump_lockspace(
        path,
        offset=0,
        size=None,
        block_size=sc.BLOCK_SIZE_512,
        alignment=sc.ALIGNMENT_1M):

    return _dump(
        _LOCKSPACE_FIELDS,
        path,
        offset=offset,
        size=size,
        block_size=block_size,
        alignment=alignment)


def run_dump(
        path,
        offset=0,
        size=None,
        block_size=sc.BLOCK_SIZE_512,
        alignment=sc.ALIGNMENT_1M):

    if os.geteuid() != 0:
        return supervdsm.getProxy().sanlock_direct_run_dump(
            path=path,
            offset=offset,
            size=size,
            block_size=block_size,
            alignment=alignment)

    # Split path to dirname and filename as sanlock direct command
    # would fail when its path argument contains a colon sign
    # as we have for fileSD mount paths.
    dirname, filename = os.path.split(path)

    filespec = "{}:{}".format(filename, offset)
    if size is not None:
        filespec = "{}:{}".format(filespec, size)

    # Run "sanlock direct dump filespec:offset[:size]"
    cmd = [
        SANLOCK.cmd,
        "direct",
        "dump",
        filespec,
        "-Z", str(block_size),
        "-A", str(alignment // sc.ALIGNMENT_1M) + "M"
    ]

    return commands.run(cmd, cwd=dirname)


def _dump(
        fields,
        path,
        offset=0,
        size=None,
        block_size=sc.BLOCK_SIZE_512,
        alignment=sc.ALIGNMENT_1M):

    out = run_dump(path, offset=offset, size=size)
    # Sanlock lockspace and resource names may have arbitrary values.
    lines = out.decode("utf-8", errors="replace").splitlines()

    # Remove heading line.
    lines = lines[1:]

    for line in lines:
        values = line.split()
        try:
            record = {name: conv(value)
                      for (name, conv), value in zip(fields, values)}
        except Exception as e:
            log.warning("Failed to parse line %r from %s: %s", line, path, e)
            continue

        yield record
