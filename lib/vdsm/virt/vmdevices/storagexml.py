#
# Copyright 2019 Red Hat, Inc.
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

from __future__ import absolute_import
from __future__ import division

from vdsm.virt import vmxml
from vdsm import utils

from . import core
from . import drivename
from . import hwclass
from . import lookup
from . import storage


_PAYLOAD_PATH = 'PAYLOAD:'


METADATA_KEYS = (
    'GUID',
    'domainID',
    'guestName',
    'imageID',
    'poolID',
    'shared',
    'volumeID',
    'RBD',
    'managed',
)


METADATA_NESTED_KEYS = (
    'diskReplicate',
    'specParams',
    'volumeChain',
    'volumeInfo',
)


def parse(dev, meta):
    """Parse the XML configuration of a storage device and returns
    the corresponding params, such as

    xmlutils.tostring(dev) is equivalent to

    params = parse(dev, meta)
    xmlutils.tostring(vmdevices.storage.Drive(log, **params).getXML())

    Args:
        dev (ElementTree.Element): Root of the XML configuration snippet.
        meta (dict): Device-specific metadata.

    Returns:
        dict: params to be used to configure a storage.Drive.
    """
    disk_type = core.find_device_type(dev)
    params = {
        'device': dev.attrib.get('device', None) or dev.tag,
        'type': dev.tag,
        'diskType': disk_type,
        'specParams': {},
    }
    core.update_device_params(params, dev, ('sgio',))
    _update_meta_params(params, meta)
    _update_source_params(
        params, disk_type, vmxml.find_first(dev, 'source', None)
    )
    _update_payload_params(params, meta)
    _update_auth_params(params, vmxml.find_first(dev, 'auth', None))
    _update_driver_params(params, dev)
    _update_interface_params(params, dev)
    _update_iotune_params(params, dev)
    _update_readonly_params(params, dev)
    _update_boot_params(params, dev)
    _update_serial_params(params, dev)

    add_vdsm_parameters(params)
    return params


def add_vdsm_parameters(params):
    if 'name' in params and 'index' not in params:
        # intentionally ignore 'iface'
        _, params['index'] = drivename.split(params['name'])


def _update_meta_params(params, meta):
    for key in METADATA_KEYS:
        if key in meta:
            params[key] = meta[key]

    for key in METADATA_NESTED_KEYS:
        if key in meta:
            params[key] = utils.picklecopy(meta[key])

    core.update_device_params_from_meta(params, meta)


def get_metadata(drive):
    attrs = {'devtype': hwclass.DISK, 'name': drive.name}
    data = core.get_metadata_values(drive)
    core.update_metadata_from_object(
        data, drive, METADATA_KEYS + METADATA_NESTED_KEYS)
    if 'GUID' in data and 'imageID' not in data:
        # For forward compatibility of the engine with LUN disks.
        data['imageID'] = data['GUID']
    return attrs, data


def _update_source_params(params, disk_type, source):
    path = None
    if source is None:
        path = ''
    elif disk_type == 'block':
        path = source.attrib.get('dev')
        reservations = vmxml.find_first(source, 'reservations', None)
        if (reservations is not None and
                reservations.attrib.get('managed') == 'yes'):
            params['managed_reservation'] = True
    elif disk_type == 'file':
        path = source.attrib.get('file', '')
    elif 'protocol' in source.attrib:
        path = source.attrib.get('name')
        params['protocol'] = source.attrib.get('protocol')
        params['hosts'] = [
            host.attrib.copy()
            for host in vmxml.find_all(source, 'host')
        ]
    params['path'] = path


def _update_payload_params(params, meta):
    payload = {}
    if 'payload' in meta:
        # new-style configuration, Engine >= 4.2
        payload = meta['payload']
    else:
        # old-style legacy configuration, Engine < 4.2
        spec_params = meta.get('specParams', {})
        payload = spec_params.get('vmPayload', {})

    if payload:
        params['specParams']['vmPayload'] = payload

    path = params.get('path')
    if path == _PAYLOAD_PATH:
        if 'path' in meta:
            params['path'] = meta['path']
        else:
            params.pop('path')


def _update_auth_params(params, auth):
    # auth may be None, and this is OK
    if auth is None:
        return
    secret = vmxml.find_first(auth, 'secret', None)
    if secret is None:
        return
    params['auth'] = {
        'username': auth.attrib.get('username'),
        'type': secret.attrib.get('type'),
        'uuid': secret.attrib.get('uuid'),
    }


def _update_driver_params(params, dev):
    driver = vmxml.find_first(dev, 'driver', None)
    if driver is not None:
        driver_params, spec_params = _get_driver_params(driver)
        params.update(driver_params)
        params['specParams'].update(spec_params)
    else:
        # the initialization code always checks this parameter
        params['propagateErrors'] = 'off'


def _update_interface_params(params, dev):
    iface = vmxml.find_attr(dev, 'target', 'bus')
    if iface is not None:
        params['iface'] = iface
    dev_name = vmxml.find_attr(dev, 'target', 'dev')
    if dev_name is not None:
        params['name'] = dev_name


def _update_iotune_params(params, dev):
    iotune = vmxml.find_first(dev, 'iotune', None)
    if iotune is None:
        return

    iotune_params = {
        setting.tag: int(setting.text)
        for setting in iotune
    }
    if iotune_params:
        params['iotune'] = iotune_params


def _update_readonly_params(params, dev):
    if vmxml.find_first(dev, 'readonly', None) is not None:
        params['readonly'] = True


def _update_boot_params(params, dev):
    boot_order = vmxml.find_attr(dev, 'boot', 'order')
    if boot_order:
        params['bootOrder'] = boot_order


def _update_serial_params(params, dev):
    serial = vmxml.find_first(dev, 'serial', None)
    if serial is not None:
        params['serial'] = vmxml.text(serial)


def _get_driver_params(driver):
    params = {
        'discard': driver.attrib.get('discard') == 'unmap',
        'format': 'cow' if driver.attrib.get('type') == 'qcow2' else 'raw',
    }

    error_policy = driver.attrib.get('error_policy', 'stop')
    if error_policy == 'report':
        params['propagateErrors'] = 'report'
    elif error_policy == 'enospace':
        params['propagateErrors'] = 'on'
    else:
        params['propagateErrors'] = 'off'

    cache = driver.attrib.get('cache', None)
    if cache:
        params['cache'] = cache
    specParams = {}
    iothread = driver.attrib.get('iothread')
    if iothread is not None:
        specParams['pinToIoThread'] = iothread
    return params, specParams


def change_disk(disk_element, disk_devices, log):
    # TODO: Code below is broken and will not work as expected.
    # Drives of different types require different data to be provided
    # by the engine. For example file based drives need just
    # a path to file, while network based drives require host information.
    # Therefore, replacing disk type on the fly is not safe and
    # will lead to incorrect drive configuration.
    # Even more - we do not support snapshots on different types of drives
    # and have a special check for that in the snapshotting code,
    # so it should never happen.
    diskType = vmxml.attr(disk_element, 'type')
    if diskType not in storage.SOURCE_ATTR:
        return

    try:
        vm_drive = lookup.drive_from_element(disk_devices, disk_element)
    except LookupError as exc:
        log.warning('%s', str(exc))
    else:
        update_disk_element_from_object(disk_element, vm_drive, log)


def update_disk_element_from_object(disk_element, vm_drive, log,
                                    replace_attribs=False):
    # key: (old_value, new_value)
    changes = {}

    # the only change relevant for floppies is the path,
    # because their only usage left is sysprep - aka payload devices.
    device = disk_element.attrib.get('device')
    old_disk_type = disk_element.attrib.get('type')
    disk_type = vm_drive.diskType

    if device != 'floppy':
        vmxml.set_attr(disk_element, 'type', disk_type)
        changes['type'] = (old_disk_type, disk_type)

    # update the path
    source = vmxml.find_first(disk_element, 'source')
    old_disk_attr = storage.SOURCE_ATTR[old_disk_type]
    # on resume, empty CDroms may lack the 'file' attribute
    old_path = source.attrib.get(old_disk_attr, None)
    if old_path is not None and replace_attribs:
        del source.attrib[old_disk_attr]
    disk_attr = storage.SOURCE_ATTR[disk_type]
    vmxml.set_attr(source, disk_attr, vm_drive.path)
    changes['path'] = (
        '%s=%s' % (old_disk_attr, '' if old_path is None else old_path),
        # We intentionally create the new value using a different format
        # - note leading asterisk - to force the _log_changes function to
        # always log this information.
        # We want to see how good is Engine at predicting drive paths,
        # and how often Vdsm needs to correct that.
        '*%s=%s' % (disk_attr, vm_drive.path),
    )

    if device != 'floppy':
        # update the format (the disk might have been collapsed)
        driver = vmxml.find_first(disk_element, 'driver')
        drive_format = 'qcow2' if vm_drive.format == 'cow' else 'raw'
        # on resume, CDroms may have minimal 'driver' attribute
        old_drive_format = driver.attrib.get('type')
        vmxml.set_attr(driver, 'type', drive_format)
        changes['format'] = (old_drive_format, drive_format)

    # dynamic_ownership workaround (required for 4.2 incoming migrations)
    # not needed once we only support https://bugzilla.redhat.com/1666795
    try:
        vmxml.find_first(source, 'seclabel')
    except vmxml.NotFound:
        if disk_type == storage.DISK_TYPE.NETWORK and \
                source.attrib.get('protocol') != 'gluster':
            # skip for non-gluster drives (CINDER) as per LibvirtVmXmlBuilder
            pass
        else:
            storage.disable_dynamic_ownership(source)

    _log_changes(log, 'drive', vm_drive.name, changes)


def _log_changes(log, device, device_id, changes):
    for key, (old_value, new_value) in changes.items():
        if old_value == new_value:
            continue

        log.info(
            '%s %r %s: %r -> %r',
            device, device_id, key, old_value, new_value)
