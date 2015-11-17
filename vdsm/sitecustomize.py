import sys

if sys.version_info[0] == 2:
    # Allow mixing of unicode objects and strings encoded in utf8.
    sys.setdefaultencoding('utf8')
