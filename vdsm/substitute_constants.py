#!/usr/bin/python

import sys, re
import constants

def replacement(m):
    s = m.group()
    return getattr(constants, 'EXT_' + s[1:-1],
           getattr(constants, s[1:-1], s))

if len(sys.argv) <= 1:
    print """usage: %s filename...

subsitute all @CONSTANT@s in filename.
""" % sys.argv[0]

for fname in sys.argv[1:]:
    if fname == '-':
        f = sys.stdin
    else:
        f = file(fname)

    s = f.read()
    r = re.sub('@[^@\n]*@', replacement, s)

    if fname == '-':
        f = sys.stdout
    else:
        f = file(fname, 'w')

    f.write(r)
