#
# Copyright 2008-2019 Red Hat, Inc.
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
from __future__ import division

import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

from vdsm.common import cpuarch
from vdsm.virt import vmxml


_DEFAULT_MACHINES = {
    cpuarch.X86_64: 'pc',
    cpuarch.PPC64: 'pseries',
    cpuarch.PPC64LE: 'pseries',
    cpuarch.S390X: 's390-ccw-virtio',
}


def make_placeholder_domain_xml(vm):
    return '''<domain type='qemu'>
  <name>{name}</name>
  <uuid>{id}</uuid>
  <memory unit='KiB'>{memory}</memory>
  <os>
    <type arch="{arch}" machine="{machine}">hvm</type>
  </os>
</domain>'''.format(name=escape(vm.name), id=vm.id, memory=vm.mem_size_mb(),
                    arch=vm.arch, machine=_DEFAULT_MACHINES[vm.arch])


def update_sysinfo(dom, osname, osversion, hostserial):
    sys_info = vmxml.find_first(dom, 'sysinfo/system', None)
    if sys_info is None:
        # TODO: log?
        return

    replaceables = {
        'product': ('OS-NAME:', osname),
        'version': ('OS-VERSION:', osversion),
        'serial': ('HOST-SERIAL:', hostserial),
    }

    for entry in vmxml.children(sys_info):
        name = entry.attrib.get('name', None)
        if name not in replaceables:
            continue

        placeholder, value = replaceables[name]
        if entry.text.startswith(placeholder):
            entry.text = value


def make_mdev_element(mdev_uuid):
    hostdev = ET.Element('hostdev')
    hostdev.set('mode', 'subsystem')
    hostdev.set('type', 'mdev')
    hostdev.set('model', 'vfio-pci')
    source = ET.SubElement(hostdev, 'source')
    address = ET.SubElement(source, 'address')
    address.set('uuid', mdev_uuid)
    return hostdev
