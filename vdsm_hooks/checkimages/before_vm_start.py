#!/usr/bin/python3

from __future__ import absolute_import
from __future__ import division

import os
import sys
import traceback
import fcntl
import signal
import subprocess
import struct
import threading
import hooking

BLKGETSIZE64 = 0x80081272  # Obtain device size in bytes
FORMAT = 'L'
TIMEPERGIB = 0.02  # Approximate qemu-img check time (in seconds) to check 1GiB
GIB = 2 ** 30  # GiB

'''
checkimages vdsm hook
=====================
Hook performs consistency check on all qcow2 format disk images of a
particular VM using the QEMU disk image utility.

Accepts optional parameter 'timeout' (in seconds) to specify how long
the hook should wait for the QEMU disk image utility operation to complete.

Without 'timeout' specified, particular timeout is computed based on
image size.

syntax:
    checkimages=true(|,timeout:\d+\.{1}\d+);

example:
    checkimages=true,timeout:1.12     # Use 1.12 seconds as timeout

    checkimages=true                  # Compute timeout based on image size

Note: Timeout value is taken in seconds. Check of 1GB image takes ~0.02 s.

'''


def computeImageTimeout(disk_image, driver_type):
    '''
    Compute expected timeout value for image. Use value of 10s as default
    timeout for very small images (where delay in image check launch could
    cause the VM to fail to start. Use precomputed value in cases required
    timeout is bigger than 10 seconds.
    '''
    default_timeout = float(10)
    image_size = getImageSize(disk_image, driver_type)
    image_timeout = float(image_size * TIMEPERGIB)
    if image_timeout > default_timeout:
        return image_timeout
    return default_timeout


def getImageSize(disk_image, driver_type):
    '''
    Obtain qcow2 image size in GiBs
    '''
    if driver_type == 'block':
        dev_buffer = ' ' * 8
        with open(disk_image) as device:
            dev_buffer = fcntl.ioctl(device.fileno(), BLKGETSIZE64, dev_buffer)
        image_bytes = struct.unpack(FORMAT, dev_buffer)[0]
    elif driver_type == 'file':
        image_bytes = os.stat(disk_image).st_size
    return float(image_bytes / GIB)


def checkImage(path, timeout):
    '''
    Check qcow2 image using qemu-img QEMU utility
    '''

    cmd = ['/usr/bin/qemu-img', 'check', '-f', 'qcow2', path]

    # Check the image using qemu-img. Enforce check termination
    # on timeout expiration
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    t = threading.Timer(timeout, p.kill)
    t.start()

    out, err = p.communicate()
    rc = p.returncode

    t.cancel()

    if rc == -signal.SIGKILL:
        sys.stderr.write('checkimages: %s image check operation timed out.' %
                         path)
        sys.stderr.write('Increate timeout or check image availability.')
        sys.exit(2)
    elif rc == 0:
        sys.stderr.write('checkimages: %s image check returned: %s\n' %
                         (path, out))
    else:
        sys.stderr.write('checkimages: Error running %s command: %s\n' %
                         (' '.join(cmd), err))
        sys.exit(2)


if 'checkimages' in os.environ:
    requested_timeout = None
    try:
        env_value = os.environ['checkimages']
        # checkimages=true,timeout:1.23 case => get requested timeout value
        if ',' in env_value:
            timeout = (env_value.split(',', 2)[1]).split(':', 2)[1]
            requested_timeout = float(timeout)

        domxml = hooking.read_domxml()
        disks = domxml.getElementsByTagName('disk')

        for disk in disks:
            disk_device = disk.getAttribute('device')
            if disk_device != 'disk':
                continue
            drivers = disk.getElementsByTagName('driver')
            sources = disk.getElementsByTagName('source')
            if not drivers or not sources:
                continue
            driver_type = drivers[0].getAttribute('type')  # 'raw' or 'qcow2'
            if driver_type != 'qcow2':
                continue
            disk_type = disk.getAttribute('type')  # 'block' or 'file'
            disk_image = None
            if disk_type == 'block':
                disk_image = sources[0].getAttribute('dev')
            elif disk_type == 'file':
                disk_image = sources[0].getAttribute('file')
            if disk_image:
                image_timeout = computeImageTimeout(disk_image, disk_type)
                # Explicit timeout was requested, use it instead of the
                # precomputed one
                if requested_timeout is not None:
                    image_timeout = requested_timeout
                sys.stderr.write('checkimages: Checking image %s. ' %
                                 disk_image)
                checkImage(disk_image, image_timeout)
    except:
        sys.stderr.write('checkimages [unexpected error]: %s\n' %
                         traceback.format_exc())
        sys.exit(2)
