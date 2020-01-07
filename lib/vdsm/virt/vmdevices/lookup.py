#
# Copyright 2018-2020 Red Hat, Inc.
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


from vdsm.common import xmlutils
from vdsm.virt.vmdevices import core
from vdsm.virt.vmdevices import hwclass
from vdsm.virt import vmxml


def device_from_xml_alias(devices, device_xml):
    dev = xmlutils.fromstring(device_xml)
    alias = core.find_device_alias(dev)
    return device_by_alias(devices, alias)


def drive_from_element(disk_devices, disk_element):
    # we try serial first for backward compatibility
    # REQUIRED_FOR: vdsm <= 4.2
    serial_elem = vmxml.find_first(disk_element, 'serial', None)
    if serial_elem is not None:
        serial = vmxml.text(serial_elem)
        try:
            return drive_by_serial(disk_devices, serial)
        except LookupError:
            pass  # try again by alias before to give up

    alias = core.find_device_alias(disk_element)
    return device_by_alias(disk_devices, alias)


def device_by_alias(devices, alias):
    for device in devices:
        if getattr(device, 'alias', None) == alias:
            return device
    raise LookupError("No such device: alias=%r" % alias)


def xml_device_by_alias(device_xml, alias):
    """
    Return an XML device having the given alias.

    :param device_xml: parsed <devices> element, typically taken
      from DomainDescriptor.devices
    :type device_xml: DOM object
    :param alias: device alias
    :type alias: string
    :returns: DOM object of the device element having the given alias
    :raises: `LookupError` if no device with `alias` is found
    """
    for dom in vmxml.children(device_xml):
        xml_alias = core.find_device_alias(dom)
        if xml_alias and xml_alias == alias:
            return dom
    raise LookupError("Unable to find matching XML for device %r" %
                      (alias,))


def hotpluggable_device_by_alias(device_dict, alias):
    for device_hwclass in hwclass.HOTPLUGGABLE:
        try:
            return device_by_alias(device_dict[device_hwclass][:], alias), \
                device_hwclass
        except LookupError:
            pass
    raise LookupError("No such device: alias=%r" % alias)


def drive_by_serial(disk_devices, serial):
    for device in disk_devices:
        if device.serial == serial:
            return device
    raise LookupError("No such drive: '%s'" % serial)


def drive_by_name(disk_devices, name):
    for device in disk_devices:
        if device.name == name:
            return device
    raise LookupError("No such drive: '%s'" % name)


def conf_by_alias(conf, dev_type, alias):
    for dev_conf in conf[:]:
        try:
            if dev_conf['alias'] == alias and dev_conf['type'] == dev_type:
                return dev_conf
        except KeyError:
            continue
    raise LookupError('Configuration of device identified by alias %s '
                      'and type %s not found' % (alias, dev_type,))


def conf_by_path(conf, path):
    for dev_conf in conf[:]:
        if dev_conf.get('path') == path:
            return dev_conf
    raise LookupError(
        'Configuration of device with path %r not found' % path)
