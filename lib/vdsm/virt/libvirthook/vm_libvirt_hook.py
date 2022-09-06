#!/usr/bin/python3

# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

import sys
import xml.etree.ElementTree as ET

from vdsm.virt.vmdevices import storage


# dynamic_ownership workaround (required for 4.2 incoming migrations)
# not needed once we only support https://bugzilla.redhat.com/1666795
def _dynamic_ownership(tree):
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


# Newer versions of libvirt accept VNC passwords of the maximum length 8,
# because QEMU uses only the first 8 characters anyway. When migrating VMs
# from older versions with long VNC passwords, the VM fails to start. Let's
# remove the extra unused characters to make libvirt happy.
def _graphics_password(tree):
    graphics = tree.find("./devices/graphics[@type='vnc'][@passwd]")
    if graphics is not None:
        passwd = graphics.attrib['passwd']
        if len(passwd) > 8:
            graphics.set('passwd', passwd[:8])


def main(domain, event, phase, stdin=sys.stdin, stdout=sys.stdout):
    if event not in ('migrate', 'restore') or phase != 'begin':
        sys.exit(0)
    tree = ET.parse(stdin)
    _dynamic_ownership(tree)
    _graphics_password(tree)
    tree.write(stdout, encoding='unicode')


if __name__ == '__main__':
    domain = sys.argv[1]
    event = sys.argv[2]
    phase = sys.argv[3]
    main(domain, event, phase)
