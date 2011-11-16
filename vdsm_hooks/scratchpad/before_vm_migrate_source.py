#!/usr/bin/python

import os
import sys

if os.environ.has_key('scratchpad'):
    sys.stderr.write('scratchpad bevort_vm_migrate_source: cannot migrate VM with scratchpad devices\n')
    sys.exit(2)
