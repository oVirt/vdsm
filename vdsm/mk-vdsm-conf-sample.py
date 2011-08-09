#!/usr/bin/python

import re
import sys

if len(sys.argv) != 2:
    sys.exit("usage: %s config.py" % sys.argv[0])

p = re.compile("config.set\('(.*?)', '(.*?)', '(.*?)'")

with file(sys.argv[1]) as f:
    for line in f:
        try:
            if line.startswith('#'):
                print line,
            elif line.startswith('config.add_section'):
                print "[%s]" % line[line.index("'")+1:line.rindex("'")]
            elif line.startswith('config.set'):
                section, key, val = p.match(line).groups()
                print "%s = %s\n" % (key, val)
        except:
            print "# err converting %s" % line
