#!/usr/bin/python

import os
import sys

if os.environ.has_key('floppy'):
    sys.stderr.write('floppy before_vm_migrate_source: cannot migrate VM with floppy hook\n')
    sys.exit(2)
