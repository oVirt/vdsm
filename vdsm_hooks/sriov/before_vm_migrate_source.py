#!/usr/bin/python

import os
import sys

if 'sriov' in os.environ:
    sys.stderr.write('sriov: cannot migrate VM with sr-iov devices\n')
    sys.exit(2)
