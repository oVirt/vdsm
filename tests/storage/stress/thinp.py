# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Stress tests for thinp auto extend volume.

Usage:

1. Create a 50g thin disk on block storage domain

2. Start watching extension completion logs on the host running the vm:

    $ tail -f /var/log/vdsm/vdsm.log | grep 'completed <Clock'

3. Run inside the guest

    $ python3 thinp.py --rate 500 /dev/sdb

"""
import argparse
import mmap
import os
import sys
import time

MiB = 1024**2
GiB = 1024 * MiB
CHUNK = 128 * MiB

parser = argparse.ArgumentParser()

parser.add_argument(
    "-r", "--rate",
    type=lambda s: int(s) * MiB,
    default=500 * MiB,
    help="Write rate in MiB per second (default 500)")

parser.add_argument(
    "-s", "--size",
    type=lambda s: int(s) * GiB,
    default=50 * GiB,
    help="Size in GiB (default 50)")

parser.add_argument(
    "path",
    help="Device path (e.g. /dev/sdb)")

args = parser.parse_args()

start = time.monotonic()
buf = mmap.mmap(-1, 8 * MiB)
fd = os.open(args.path, os.O_RDWR | os.O_DIRECT)
correction = 0

for offset in range(0, args.size, CHUNK):
    # Write a chunk.
    todo = length = min(CHUNK, args.size - offset)
    while todo:
        n = min(len(buf), todo)
        todo -= os.write(fd, memoryview(buf)[:n])

    # Check actual rate.
    done = offset + length
    elapsed = time.monotonic() - start
    expected = done / args.rate

    # Throttle if needed.
    if elapsed + correction < expected:
        time.sleep(expected - elapsed - correction)
        elapsed = time.monotonic() - start
        correction = elapsed - expected

    # Print stats
    done_gb = done / GiB
    rate_mbs = round(done // MiB / elapsed, 1)
    term = "\r" if done < args.size else "\n"
    line = f"{done_gb:.2f} GiB, {elapsed:.2f} s, {rate_mbs:.1f} MiB/s"
    sys.stdout.write(line.ljust(79) + term)
    sys.stdout.flush()

os.close(fd)
