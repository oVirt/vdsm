#!/usr/bin/python

import os
import sys

if os.environ.has_key('sriov'):
    sys.stderr.write('sriov: cannot migrate VM with sr-iov devices\n')
    sys.exit(2)
