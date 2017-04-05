#!/usr/bin/python2

import os
import sys
import traceback

from vdsm import hugepages


if 'hugepages' in os.environ:
    try:
        pages = int(os.environ.get('hugepages'))

        hugepages.alloc(pages)
    except:
        sys.stderr.write('hugepages before_vm_migraton_destination: '
                         '[unexpected error]: %s\n' % traceback.format_exc())
        sys.exit(2)
