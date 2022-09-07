#!/usr/bin/python3

# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import

import os
import sys

if 'scratchpad' in os.environ:
    sys.stderr.write('scratchpad bevort_vm_migrate_source: '
                     'cannot migrate VM with scratchpad devices\n')
    sys.exit(2)
