#!/usr/bin/python
#
# Copyright 2011 Red Hat, Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

import sys
import re

from vdsm import constants


def replacement(m):
    s = m.group()
    return getattr(constants, 'EXT_' + s[1:-1],
           getattr(constants, s[1:-1], s))

if len(sys.argv) <= 1:
    print """usage: %s filename...

substitute all @CONSTANT@s in filename.
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
