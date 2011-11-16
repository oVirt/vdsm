#!/usr/bin/python

import os
import sys
import hooking
import traceback

'''
pincpu usages
=============
pincpu="0" (use the first cpu)
pincpu="1-4" (use cpus 1-4)
pincpu="^3" (dont use cpu 3)
pincpu="1-4,^3,6" (or all together)
'''

if os.environ.has_key('pincpu'):
    try:
        domxml = hooking.read_domxml()

        vcpu = domxml.getElementsByTagName('vcpu')[0]

        if not vcpu.hasAttribute('cpuset'):
            sys.stderr.write('pincpu: pinning cpu to: %s\n' % os.environ['pincpu'])
            vcpu.setAttribute('cpuset', os.environ['pincpu'])
            hooking.write_domxml(domxml)
        else:
            sys.stderr.write('pincpu: cpuset attribute is present in vcpu, doing nothing\n')
    except:
        sys.stderr.write('pincpu: [unexpected error]: %s\n' % traceback.format_exc())
        sys.exit(2)
