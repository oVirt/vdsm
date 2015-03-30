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
from collections import namedtuple
from contextlib import closing
import logging
import re
import xml.etree.ElementTree as ET

import libvirt

from vdsm.define import errCode, doneCode
from vdsm import libvirtconnection

import caps


ImportProgress = namedtuple('ImportProgress',
                            ['current_disk', 'disk_count', 'description'])
DiskProgress = namedtuple('DiskProgress', ['progress'])


class V2VError(Exception):
    ''' Base class for v2v errors '''


class InvalidVMConfiguration(ValueError):
    ''' Unexpected error while parsing libvirt domain xml '''


class OutputParserError(V2VError):
    ''' Error while parsing virt-v2v output '''


def supported():
    return not (caps.getos() in (caps.OSName.RHEVH, caps.OSName.RHEL)
                and caps.osversion()['version'].startswith('6'))


def get_external_vms(uri, username, password):
    if not supported():
        return errCode["noimpl"]

    try:
        conn = libvirtconnection.open_connection(uri=uri,
                                                 username=username,
                                                 passwd=password)
    except libvirt.libvirtError as e:
        logging.error('error connection to hypervisor: %r', e.message)
        return {'status': {'code': errCode['V2VConnection']['status']['code'],
                           'message': e.message}}

    with closing(conn):
        vms = []
        for vm in conn.listAllDomains():
            root = ET.fromstring(vm.XMLDesc(0))
            params = {}
            _add_vm_info(vm, params)
            try:
                _add_general_info(root, params)
            except InvalidVMConfiguration as e:
                logging.error('error parsing domain xml, msg: %s  xml: %s',
                              e.message, vm.XMLDesc(0))
                continue
            _add_networks(root, params)
            _add_disks(root, params)
            for disk in params['disks']:
                _add_disk_info(conn, disk)
            vms.append(params)
        return {'status': doneCode, 'vmList': vms}


class OutputParser(object):
    COPY_DISK_RE = re.compile(r'.*(Copying disk (\d+)/(\d+)).*')
    DISK_PROGRESS_RE = re.compile(r'\s+\((\d+).*')

    def parse(self, stream):
        for line in stream:
            if 'Copying disk' in line:
                description, current_disk, disk_count = self._parse_line(line)
                yield ImportProgress(int(current_disk), int(disk_count),
                                     description)
                for chunk in self._iter_progress(stream):
                    progress = self._parse_progress(chunk)
                    yield DiskProgress(progress)
                    if progress == 100:
                        break

    def _parse_line(self, line):
        m = self.COPY_DISK_RE.match(line)
        if m is None:
            raise OutputParserError('unexpected format in "Copying disk"'
                                    ', line: %r' % line)
        return m.group(1), m.group(2), m.group(3)

    def _iter_progress(self, stream):
        chunk = ''
        while True:
            c = stream.read(1)
            chunk += c
            if c == '\r':
                yield chunk
                chunk = ''

    def _parse_progress(self, chunk):
        m = self.DISK_PROGRESS_RE.match(chunk)
        if m is None:
            raise OutputParserError('error parsing progress, chunk: %r'
                                    % chunk)
        try:
            return int(m.group(1))
        except ValueError:
            raise OutputParserError('error parsing progress regex: %r'
                                    % m.groups)


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


def _add_vm_info(vm, params):
    params['vmName'] = vm.name()
    if vm.state()[0] == libvirt.VIR_DOMAIN_SHUTOFF:
        params['status'] = "Down"
    else:
        params['status'] = "Up"


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


def _add_disk_info(conn, disk):
    if 'alias' in disk.keys():
        try:
            vol = conn.storageVolLookupByPath(disk['alias'])
            _, capacity, alloc = vol.info()
        except libvirt.libvirtError:
            logging.exception("Error getting disk size")

        disk['capacity'] = str(capacity)
        disk['allocation'] = str(alloc)


def _add_disks(root, params):
    params['disks'] = []
    disks = root.findall('.//disk[@type="file"]')
    for disk in disks:
        d = {}
        device = disk.get('device')
        if device is not None:
            d['type'] = device
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
        model = iface.find('./model/[@type]')
        if model is not None:
            i['model'] = model.get('type')
        params['networks'].append(i)
