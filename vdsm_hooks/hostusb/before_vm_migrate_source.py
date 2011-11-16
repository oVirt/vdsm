#!/usr/bin/python

import os
import sys

if os.environ.has_key('hostusb'):
    sys.stderr.write('hostusb: cannot migrate VM with host usb devices\n')
    sys.exit(2)
