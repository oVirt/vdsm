# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from collections import namedtuple

import pytest
import userstorage

from vdsm import utils
from vdsm.common import commands
from vdsm.storage import sanlock_direct
from vdsm.storage import constants as sc

from .marks import requires_root

# Wait 1 second for lockspace initialization for quick tests.
INIT_LOCKSPACE_TIMEOUT = 1
BACKENDS = userstorage.load_config("storage.py").BACKENDS

Storage = namedtuple("Storage", "path, block_size, alignment")


@pytest.fixture(
    params=[
        pytest.param(
            (BACKENDS["file-512"], sc.ALIGNMENT_1M),
            id="file-512-1m"),
        pytest.param(
            (BACKENDS["file-4k"], sc.ALIGNMENT_2M),
            id="file-4k-2m"),
    ]
)
def storage(request):
    backend, alignment = request.param
    with backend:
        yield Storage(backend.path, backend.sector_size, alignment)


@requires_root
@pytest.mark.root
def test_dump_leases(storage):
    block_size = storage.block_size
    align = storage.alignment

    # Test empty leases store.
    dump = sanlock_direct.dump_leases(
        path=storage.path,
        offset=0,
        size=None,
        block_size=block_size,
        alignment=align)
    assert list(dump) == []

    # Add resources.
    _write_resource("LS", "RS0", storage.path, 0 * align, block_size, align)
    _write_resource("LS", "RS1", storage.path, 1 * align, block_size, align)
    _write_resource("LS", "RS2", storage.path, 2 * align, block_size, align)

    expected = [{
        "offset": i * storage.alignment,
        "lockspace": "LS",
        "resource": "RS{}".format(i),
        "timestamp": 0,
        "own": 0,
        "gen": 0,
        "lver": 0
    } for i in range(3)]

    dump = sanlock_direct.dump_leases(
        path=storage.path,
        offset=0,
        size=4 * align,
        block_size=block_size,
        alignment=align)
    assert list(dump) == expected


@requires_root
@pytest.mark.root
def test_dump_ids(storage):
    block_size = storage.block_size
    align = storage.alignment

    # Test empty ids store.
    dump = sanlock_direct.dump_lockspace(
        path=storage.path,
        offset=0,
        size=None,
        block_size=block_size,
        alignment=align)
    assert list(dump) == []

    # Initialize lockspace.
    _write_lockspace("LS", storage.path, 0, block_size, align)
    # Add lockspace lease entry.
    _add_lockspace("LS", storage.path, 0, block_size, align)

    dump = list(sanlock_direct.dump_lockspace(
        path=storage.path,
        offset=0,
        size=None,
        block_size=block_size,
        alignment=align))

    assert len(dump) == 1
    rec = dump[0]
    assert rec["offset"] == 0
    assert rec["lockspace"] == "LS"
    assert rec["own"] == 1
    assert rec["gen"] == 1


@requires_root
@pytest.mark.root
def test_dump_holes(storage):
    block_size = storage.block_size
    align = storage.alignment

    # Simulated leases volume:
    # slot   0: -
    # slot   1: SDM
    # slot   2: -
    # slot   3: -
    # slot   4: RS4
    # slot   5: RS5
    # slot   6: -

    _write_resource("LS", "SDM", storage.path, 1 * align, block_size, align)
    _write_resource("LS", "RS4", storage.path, 4 * align, block_size, align)
    _write_resource("LS", "RS5", storage.path, 5 * align, block_size, align)

    # Without specifiying size the dump stops at the hole at slot 0.
    dump = sanlock_direct.dump_leases(
        path=storage.path,
        block_size=block_size,
        alignment=align)

    assert list(dump) == []

    # With specified size the dump passes all holes.
    dump = sanlock_direct.dump_leases(
        path=storage.path,
        size=6 * align,
        block_size=block_size,
        alignment=align)

    resources = [(r["offset"], r["resource"]) for r in dump]
    assert resources == [
        (1 * align, "SDM"),
        (4 * align, "RS4"),
        (5 * align, "RS5"),
    ]


def _write_lockspace(ls_name, dev, offset, block_size, alignment):
    args = [
        "-o", str(INIT_LOCKSPACE_TIMEOUT),
        "-s", "{}:1:{}:{}".format(ls_name, dev, offset)
    ]
    _sanlock_direct("init", args, block_size, alignment)


def _add_lockspace(ls_name, dev, offset, block_size, alignment):
    args = ["-s", "{}:1:{}:{}".format(ls_name, dev, offset)]
    _sanlock_direct("acquire_id", args, block_size, alignment)


def _write_resource(ls_name, rs_name, dev, offset, block_size, alignment):
    args = ["-r", "{}:{}:{}:{}".format(ls_name, rs_name, dev, offset)]
    _sanlock_direct("init", args, block_size, alignment)


def _sanlock_direct(cmd, args, block_size, alignment):
    options = [
        "-Z", str(block_size),
        "-A", str(alignment // sc.ALIGNMENT_1M) + "M"
    ]

    with utils.stopwatch("sanlock direct {} {}".format(cmd, args)):
        commands.run(
            [sanlock_direct.SANLOCK.cmd, "direct", cmd] + args + options)
