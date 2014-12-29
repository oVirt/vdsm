# Copyright 2014 Red Hat, Inc.
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
import xml.etree.ElementTree as ET
from contextlib import closing

import logging
from vdsm.define import errCode
import caps
import libvirt
from vdsm import libvirtconnection


class InvalidVMConfiguration(ValueError):
    ''' Unexpected error while parsing libvirt domain xml '''


def supported():
    return not (caps.getos() in (caps.OSName.RHEVH, caps.OSName.RHEL)
                and caps.osversion()['version'].startswith('6'))


def get_external_vms(uri, username, password):
    if not supported():
        return errCode["noimpl"]

    conn = libvirtconnection.open_connection(uri=uri,
                                             username=username,
                                             passwd=password)
    with closing(conn):
        ret = []
        for vm in conn.listAllDomains():
            root = ET.fromstring(vm.XMLDesc(0))
            params = {}
            params['vmName'] = vm.name()
            if vm.state()[0] == libvirt.VIR_DOMAIN_SHUTOFF:
                params['status'] = "Down"
            else:
                params['status'] = "Up"
            try:
                _add_general_info(root, params)
            except InvalidVMConfiguration as e:
                logging.error('error parsing domain xml, msg: %s  xml: %s',
                              e.message, vm.XMLDesc(0))
                continue
            _add_disks(root, params)
            _add_networks(root, params)
            ret.append(params)
        return ret


def _mem_to_mib(size, unit):
    lunit = unit.lower()
    if lunit in ('bytes', 'b'):
        return size / 1024 / 1024
    elif lunit in ('kib', 'k'):
        return size / 1024
    elif lunit in ('mib', 'm'):
        return size
    elif lunit in ('gib', 'g'):
        return size * 1024
    elif lunit in ('tib', 't'):
        return size * 1024 * 1024
    else:
        raise InvalidVMConfiguration("Invalid currentMemory unit attribute:"
                                     " %r" % unit)


def _add_general_info(root, params):
    e = root.find('./uuid')
    if e is not None:
        params['vmId'] = e.text

    e = root.find('./currentMemory')
    if e is not None:
        try:
            size = int(e.text)
        except ValueError:
            raise InvalidVMConfiguration("Invalid 'currentMemory' value: %r"
                                         % e.text)
        unit = e.get('unit', 'KiB')
        params['memSize'] = _mem_to_mib(size, unit)

    e = root.find('./vcpu')
    if e is not None:
        try:
            params['smp'] = int(e.text)
        except ValueError:
            raise InvalidVMConfiguration("Invalid 'vcpu' value: %r" % e.text)

    e = root.find('./os/type/[@arch]')
    if e is not None:
        params['arch'] = e.get('arch')


def _add_disks(root, params):
    params['disks'] = []
    disks = root.findall('.//disk[@type="file"]')
    for disk in disks:
        d = {}
        target = disk.find('./target/[@dev]')
        if target is not None:
            d['dev'] = target.get('dev')
        source = disk.find('./source/[@file]')
        if source is not None:
            d['alias'] = source.get('file')
        params['disks'].append(d)


def _add_networks(root, params):
    params['networks'] = []
    interfaces = root.findall('.//interface')
    for iface in interfaces:
        i = {}
        if 'type' in iface.attrib:
            i['type'] = iface.attrib['type']
        mac = iface.find('./mac/[@address]')
        if mac is not None:
            i['macAddr'] = mac.get('address')
        source = iface.find('./source/[@bridge]')
        if source is not None:
            i['bridge'] = source.get('bridge')
        target = iface.find('./target/[@dev]')
        if target is not None:
            i['dev'] = target.get('dev')
        params['networks'].append(i)
