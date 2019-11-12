#!/usr/bin/python3

# Copyright 2016-2019 Red Hat, Inc.
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

from __future__ import absolute_import

import sys
import xml.etree.ElementTree as ET

import six

from vdsm.virt.vmdevices import storage


# dynamic_ownership workaround (required for 4.2 incoming migrations)
# not needed once we only support https://bugzilla.redhat.com/1666795
def _process_domxml(tree):
    for xpath in (
            "./devices//disk[@type='%s']//source" %
            (storage.DISK_TYPE.BLOCK,),
            "./devices//disk[@type='%s']//source" %
            (storage.DISK_TYPE.FILE,),
            "./devices//disk[@type='%s']//source[@protocol='gluster']" %
            (storage.DISK_TYPE.NETWORK,)
    ):
        for element in tree.findall(xpath):
            storage.disable_dynamic_ownership(element)


def main(domain, event, phase, stdin=sys.stdin, stdout=sys.stdout):
    if event not in ('migrate', 'restore') or phase != 'begin':
        sys.exit(0)
    tree = ET.parse(stdin)
    _process_domxml(tree)
    encoding = None if six.PY2 else 'unicode'
    tree.write(stdout, encoding=encoding)


if __name__ == '__main__':
    domain = sys.argv[1]
    event = sys.argv[2]
    phase = sys.argv[3]
    main(domain, event, phase)
