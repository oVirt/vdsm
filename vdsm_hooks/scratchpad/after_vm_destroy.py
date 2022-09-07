#!/usr/bin/python3

# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import

import os
import sys
import traceback

if 'scratchpad' in os.environ:
    try:
        disks = os.environ['scratchpad']

        for disk in disks.split(':'):
            arr = disk.split(',')
            if os.path.exists(arr[1]):
                os.remove(arr[1])
            else:
                sys.stderr.write('scratchpad after_vm_destroy: '
                                 'cannot find image file %s\n' % arr[1])
    except:
        sys.stderr.write('scratchpad after_vm_destroy: '
                         '[unexpected error]: %s\n' % traceback.format_exc())
        sys.exit(2)
