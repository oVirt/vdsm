#!/usr/bin/python2

import os
import sys
import traceback

from vdsm import hugepages


if 'hugepages' in os.environ:
    try:
        pages = int(os.environ.get('hugepages'))

        hugepages.dealloc(pages)
    except Exception:
        sys.stderr.write('hugepages: [unexpected error]: %s\n' %
                         traceback.format_exc())
        sys.exit(2)
