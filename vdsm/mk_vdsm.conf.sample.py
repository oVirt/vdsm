#!/usr/bin/python

import re

p = re.compile("config.set\('(.*?)', '(.*?)', '(.*?)'")

for line in file('config.py').readlines():
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
