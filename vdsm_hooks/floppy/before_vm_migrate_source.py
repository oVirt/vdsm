#!/usr/bin/python

import os
import sys

if 'floppy' in os.environ:
    sys.stderr.write('floppy before_vm_migrate_source: cannot migrate VM with '
                     'floppy hook\n')
    sys.exit(2)
