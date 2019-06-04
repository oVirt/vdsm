#!/usr/bin/python2
#
# Copyright 2017-2019 Red Hat, Inc.
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
VDSM hostdev_scsi hook

The hook is able to translate a SCSI generic device into a more optimized one.
See the README for the usage instructions.
'''

from __future__ import absolute_import

import os
import traceback
from xml.dom import minidom

from vdsm.hook import hooking
from vdsm.common import base26


_SCSI_PATH_TMPL = '/sys/bus/scsi/devices/{address}/block'
_SCSI_ADAPTER = 'scsi_host'
_DEVICE_TYPES = ('scsi_block', 'scsi_hd')

# We need to provide unique <target dev='sdx" .../> names. Since this is only a
# hint, we start with "sdaaa" to avoid conflicts with existing devices.
# TODO: This will not work if a VM has more than 701 scsi devices.
# see <target> documentation in
# https://libvirt.org/formatdomain.html#elementsDisks
_BASE_INDEX = base26.decode("aaa")


def main():
    dev_type = os.environ.get('hostdev_scsi')
    if dev_type is None:
        # No translation requested for the given VM
        return

    _check_supported(dev_type)

    domxml = hooking.read_domxml()

    dom = _find_element_by_tag_name(domxml, 'domain')
    devs = _find_element_by_tag_name(dom, 'devices')

    index = _BASE_INDEX

    for dev in _get_child_nodes(devs):
        if not _is_scsi_dev(dev):
            continue

        dev_info = _parse_devs_xml(dev)
        new_dev = _make_disk_device(domxml, dev_info, dev_type, index)
        devs.replaceChild(new_dev, dev)
        index += 1

    hooking.write_domxml(domxml)


def _check_supported(dev_type):
    if dev_type not in _DEVICE_TYPES:
        raise RuntimeError('unsupported device type: %s' % dev_type)


def _is_scsi_dev(dev):
    """
    <hostdev mode='subsystem' type='scsi' managed='no' rawio='yes'>
      <source>
        <adapter name='scsi_host0'/>
        <address bus='0' target='6' unit='0'/>
      </source>
      <alias name='hostdev0'/>
      <address type='drive' controller='0' bus='0' target='0' unit='1'/>
    </hostdev>
    """
    if dev.nodeName != 'hostdev':
        return False
    return _match_attrs(dev, (
        ('mode', 'subsystem'),
        ('type', 'scsi'),
        ('managed', 'no'),
        ('rawio', 'yes'),
    ))


def _parse_devs_xml(dev):
    src_addr = _parse_src_addr(dev)
    info = {
        'source_device': _find_source_device(src_addr),
    }
    _add_address(dev, info)
    _add_alias(dev, info)
    return info


def _parse_src_addr(dev):
    """
    <source>
      <adapter name='scsi_host0'/>
      <address bus='0' target='6' unit='0'/>
    </source>
    """
    source = _find_element_by_tag_name(dev, 'source')
    addr = _parse_address(source)
    adapter = _find_element_by_tag_name(source, 'adapter')
    adapter_name = adapter.attributes['name'].value
    addr['controller'] = adapter_name[len(_SCSI_ADAPTER):]
    return addr


def _find_source_device(src_addr):
    address = '{controller}:{bus}:{target}:{unit}'.format(**src_addr)
    return os.listdir(_SCSI_PATH_TMPL.format(address=address))[0]


def _parse_address(node):
    """
    <address type='drive' controller='0' bus='0' target='0' unit='1'/>
    """
    address = _find_element_by_tag_name(node, 'address')
    return {
        key: value for key, value in address.attributes.items()
    }


def _add_address(dev, info):
    try:
        addr = _parse_address(dev)
    except LookupError:
        # it may happen, perhaps first boot
        pass
    else:
        info['address'] = addr


def _add_alias(dev, info):
    """
    <alias name='hostdev0'/>
    """
    try:
        alias = _parse_alias(dev)
    except LookupError:
        # it may happen, perhaps first boot
        pass
    else:
        info['alias'] = alias


def _parse_alias(dev):
    alias = _find_element_by_tag_name(dev, 'alias')
    return alias.attributes['name'].value


def _make_disk_device(domxml, dev_info, dev_type, index):
    """
    scsi_block:
    <disk type='block' device='lun' rawio='yes'>
      <driver name='qemu' type='raw' cache='none' io='native'/>
      <source dev='/dev/sdd'/>
      <target bus='scsi'/>
      <address type='drive' controller='0' bus='0' target='0' unit='1'/>
      <alias name='hostdev0'/>
    </disk>

    scsi_hd:
    <disk type='block' device='disk'>
      <driver name='qemu' type='raw' cache='none' io='native'/>
      <source dev='/dev/sdd'/>
      <target bus='scsi'/>
      <address type='drive' controller='0' bus='0' target='0' unit='1'/>
      <alias name='hostdev0'/>
    </disk>
    """
    disk_dev = domxml.createElement('disk')
    disk_dev.setAttribute('type', 'block')
    if dev_type == 'scsi_block':
        disk_dev.setAttribute('device', 'lun')
        disk_dev.setAttribute('rawio', 'yes')
    elif dev_type == 'scsi_hd':
        disk_dev.setAttribute('device', 'disk')

    if 'address' in dev_info:
        address = domxml.createElement('address')
        address.setAttribute('type', 'drive')
        addr_info = dev_info['address']
        for key in ('controller', 'bus', 'target', 'unit'):
            address.setAttribute(key, addr_info[key])
        disk_dev.appendChild(address)

    if 'alias' in dev_info:
        alias = domxml.createElement('alias')
        alias.setAttribute('name', dev_info['alias'])
        disk_dev.appendChild(alias)

    driver = domxml.createElement('driver')
    driver.setAttribute('name', 'qemu')
    driver.setAttribute('type', 'raw')
    driver.setAttribute('cache', 'none')
    driver.setAttribute('io', 'native')
    disk_dev.appendChild(driver)

    source = domxml.createElement('source')
    source.setAttribute(
        'dev', '/dev/{name}'.format(name=dev_info['source_device'])
    )
    disk_dev.appendChild(source)

    target = domxml.createElement('target')
    target.setAttribute('dev', 'sd' + base26.encode(index))
    target.setAttribute('bus', 'scsi')
    disk_dev.appendChild(target)

    return disk_dev


def _match_attrs(node, items):
    """
    Return True if node has the specified attributes and
    the attributes values match, otherwise False.
    """
    for key, value in items:
        try:
            actual_value = node.attributes[key].value
        except KeyError:
            return False
        if actual_value != value:
            return False
    return True


def _find_element_by_tag_name(parent, name):
    """
    Find a node with tag `name' in the direct childrens
    of the `parent' node. Raise LookupError() otherwise.
    Compare with xml.dom.minidom's getElementsByTagName
    which will recursively scan all the children of the given
    `parent' node.
    """
    for node in _get_child_nodes(parent):
        if node.tagName == name:
            return node
    raise LookupError(
        "Cannot find node with tag '{name}' in {parent_xml}".format(
            name=name, parent_xml=parent.toxml(encoding='utf-8')
        )
    )


def _get_child_nodes(node):
    """
    Get all child nodes of the given parent `node'.
    Use this helper to skip other child Elements, like
    ELEMENT_TEXT.
    """
    for node in node.childNodes:
        if node.nodeType != minidom.Node.ELEMENT_NODE:
            continue
        yield node


if __name__ == '__main__':
    try:
        main()
    except:
        # TODO: do we want to fail hard or to keep processing the hooks?
        hooking.exit_hook(
            'hostdev_scsi: %s' % (
                traceback.format_exc()
            )
        )
