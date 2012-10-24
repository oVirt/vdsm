#!/usr/bin/python

import os
import sys

if 'hostusb' in os.environ:
    sys.stderr.write('hostusb: cannot migrate VM with host usb devices\n')
    sys.exit(2)
