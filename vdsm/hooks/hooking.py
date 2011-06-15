"""
hooking - various stuff useful when writing vdsm hooks

A vm hook expects domain xml in a file named by an environment variable called
_hook_domxml. The hook may change the xml, but the "china store rule" applies -
if you break something, you own it.

before_migration_destination hook receives the xml of the domain from the
source host. The xml of the domain at the destination will differ in various
details.

Return codes:
0 - the hook ended successfully.
1 - the hook failed, other hooks should be processed.
2 - the hook failed, no further hooks should be processed.
>2 - reserverd
"""

import os
from xml.dom import minidom

def tobool(s):
    """Convert the argument into a boolean"""
    try:
        if s == None:
            return False
        if type(s) == bool:
            return s
        if s.lower() == 'true':
            return True
        return bool(int(s))
    except:
        return False

def read_domxml():
    return minidom.parseString(file(os.environ['_hook_domxml']).read())

def write_domxml(domxml):
    file(os.environ['_hook_domxml'], 'w').write(domxml.toxml(encoding='utf-8'))
