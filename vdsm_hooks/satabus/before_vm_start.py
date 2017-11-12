#!/usr/bin/python2
#
# Copyright 2017 Red Hat, Inc.
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


'''
Hook set sata bus to disk drive.

Syntax:
   satabus=(off|on)

Example:
   satabus=on
'''

import os
import sys
import traceback
from xml.dom import minidom
import hooking


def addSataBus(domxml):
    for disk in domxml.getElementsByTagName('disk'):
        device = disk.getAttribute('device')
        target = disk.getElementsByTagName('target')[0]
        bus = target.getAttribute('bus')
        if device == 'disk' and (bus in ['scsi', 'ide', 'virtio']):
            target.setAttribute('bus', 'sata')


def main():
    if 'satabus' in os.environ:
        sataConfig = os.environ['satabus']
        domxml = hooking.read_domxml()
        if sataConfig == 'on':
            addSataBus(domxml)
            hooking.write_domxml(domxml)


def test():
    text = """<disk device="disk" snapshot="no" type="network">
<address bus="0" controller="0" target="0" type="drive" unit="1"/>
<source name="rbd/volume-813b3e01-74e9-4e5b-a411-18854c21eff5" protocol="rbd">
<host name="172.16.16.2" port="6789" transport="tcp"/>
<host name="172.16.16.3" port="6789" transport="tcp"/>
<host name="172.16.16.4" port="6789" transport="tcp"/>
</source>
<auth username="cinder">
<secret type="ceph" uuid="e0828f39-2832-4d82-90ee-23b26fc7b20a"/>
</auth>
<target bus="scsi" dev="sda"/>
<driver cache="none" error_policy="stop" io="threads" name="qemu" type="raw"/>
</disk>"""

    xmldom = minidom.parseString(text)

    disk = xmldom.getElementsByTagName('disk')[0]
    print("\nDisk device definition before execution: \n{0}".format(
          disk.toxml(encoding='UTF-8')))

    addSataBus(xmldom)

    print("\nDisk device after set SATA attribute: \n{0}".format(
          disk.toxml(encoding='UTF-8')))


if __name__ == '__main__':
    try:
        if '--test' in sys.argv:
            test()
        else:
            main()
    except:
        hooking.exit_hook(' satabus hook: [unexpected error]: {0}\n'.format(
                          traceback.format_exc()))
