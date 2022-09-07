#!/usr/bin/python3

# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import

import os
import traceback

from vdsm.hook import hooking


def main():
    dev = os.environ.get('boot_hostdev')
    if dev:
        domxml = hooking.read_domxml()
        # Free boot order 1, move all existing ones one up
        for boot in domxml.getElementsByTagName('boot'):
            order = int(boot.getAttribute('order'))
            boot.setAttribute('order', str(order + 1))

        # Find specified hostdev, set order = 1
        usr_dev = get_user_device_attrs(dev)
        for hostdev in domxml.getElementsByTagName('hostdev'):
            xml_dev = get_xml_device_attrs(hostdev)
            if xml_dev == usr_dev:
                boot = domxml.createElement('boot')
                boot.setAttribute('order', '1')
                hostdev.appendChild(boot)
                hooking.write_domxml(domxml)
                return
        raise RuntimeError("Could not find device %s" % dev)


def get_user_device_attrs(dev):
    """
    VFIO: pci_0000_0b_00_0
        <hostdev mode='subsystem' type='pci' managed='no'>
          <driver name='vfio'/>
          <source>
            <address domain='0x0000' bus='0x0b' slot='0x00' function='0x0'/>
          </source>
        </hostdev>
    SCSI: scsi_2_0_0_0
        <hostdev mode='subsystem' type='scsi' managed='no' rawio='yes'>
          <source>
            <adapter name='scsi_host2'/>
            <address bus='0' target='0' unit='0'/>
          </source>
        </hostdev>
    USB: usb_usb7
        <hostdev managed="no" mode="subsystem" type="usb">
            <source>
                <address bus="7" device="1"/>
            </source>
        </hostdev>
    USB: usb_2_8
        <hostdev managed="no" mode="subsystem" type="usb">
            <source>
                <address bus="2" device="8"/>
            </source>
        </hostdev>
    """
    attrs = set()
    split_dev = dev.split('_')
    if dev.startswith('scsi'):
        attrs.add(('type', 'scsi'))
        attrs.add(('name', 'scsi_host' + split_dev[1]))
        attrs.add(('bus', split_dev[2]))
        attrs.add(('target', split_dev[3]))
        attrs.add(('unit', split_dev[4]))
    elif dev.startswith('pci'):
        attrs.add(('type', 'pci'))
        attrs.add(('domain', '0x' + split_dev[1]))
        attrs.add(('bus', '0x' + split_dev[2]))
        attrs.add(('slot', '0x' + split_dev[3]))
        attrs.add(('function', '0x' + split_dev[4]))
    elif dev.startswith('usb'):
        attrs.add(('type', 'usb'))
        if len(split_dev) == 2:
            attrs.add(('bus', split_dev[1][3:]))
            attrs.add(('device', "1"))
        else:
            attrs.add(('bus', split_dev[1]))
            attrs.add(('device', split_dev[2]))
    return attrs


def get_xml_device_attrs(hostdev):
    source = hostdev.getElementsByTagName('source')[0]
    address = source.getElementsByTagName('address')[0]
    attrs = {i for i in address.attributes.items()}
    devtype = hostdev.getAttribute('type')
    attrs.add(('type', devtype))
    if devtype == 'scsi':
        adapter = hostdev.getElementsByTagName('adapter')[0]
        attrs.add(('name', adapter.getAttribute('name')))
    return attrs


if __name__ == '__main__':
    try:
        main()
    except:
        hooking.exit_hook(
            'boot_hostdev: %s' % (
                traceback.format_exc()
            ),
            return_code=1
        )
