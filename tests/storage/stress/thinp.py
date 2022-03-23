"""
Stress tests for thinp auto extend volume.

Usage:

1. Create a 50g thin disk on block storage domain

2. Update RATE to the desired write rate

3. Update PATH for the actual device in the guest.

4. Start watching extension completion logs on the host running the vm:

    $ tail -f /var/log/vdsm/vdsm.log | grep 'completed <Clock'

5. Run inside the guest

    $ python3 thinp.py

"""
import mmap
import os
import sys
import time

RATE = 500 * 1024**2
SIZE = 50 * 1024**3
PATH = "/dev/sdb"
CHUNK = 128 * 1024**2

start = time.monotonic()
buf = mmap.mmap(-1, 8 * 1024**2)
fd = os.open(PATH, os.O_RDWR | os.O_DIRECT)

for offset in range(0, SIZE, CHUNK):
    # Write a chunk.
    for _ in range(CHUNK // len(buf)):
        os.write(fd, buf)

    # Check actual rate.
    done = offset + CHUNK
    elapsed = time.monotonic() - start
    expected = done / RATE

    # Throttle if needed.
    if elapsed < expected:
        time.sleep(expected - elapsed)

    # Print stats
    done_gb = done / 1024**3
    rate_mbs = done // 1024**2 / elapsed
    term = "\r" if done < SIZE else "\n"
    line = f"{done_gb:.2f} GiB, {elapsed:.2f} s, {rate_mbs:.2f} MiB/s"
    sys.stdout.write(line.ljust(79) + term)
    sys.stdout.flush()

os.close(fd)
