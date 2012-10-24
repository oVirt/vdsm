#!/usr/bin/python

import os
import sys
import hooking
import traceback

'''
vmdisk hook:
    add additional disk image for a VM (raw or qcow2)
syntax:
    vmdisk=/path/to/disk.img:qcow2,/other/disk.img:raw
'''

driver_types = ('raw', 'qcow2')

def indexToDiskName(i):
    s = ''
    while True:
        s = chr(ord('a') + i % 26) + s
        i = i / 26
        if i == 0:
            break
    return 'vd' + (s or 'a')

def createDiskElement(domxml, devpath, drivertype):
    '''
    <disk device="disk" type="file">
        <source file="/net/myhost/myimage.img"/>
        <target bus="virtio" dev="vda"/>
        <driver cache="none" error_policy="stop" name="qemu" type="qcow2"/>
    </disk>
    '''

    disk = domxml.createElement('disk')
    disk.setAttribute('device', 'disk')
    disk.setAttribute('type', 'file')

    source = domxml.createElement('source')
    source.setAttribute('file', devpath)
    disk.appendChild(source)

    # find a name for vdXXX
    target = domxml.createElement('target')
    target.setAttribute('bus', 'virtio')
    xmldisks = domxml.getElementsByTagName('disk')
    disks = []
    for d in xmldisks:
        disks.append(d.getElementsByTagName('target')[0].getAttribute('dev'))

    for i in range(0, 27):
        if not indexToDiskName(i) in disks:
            target.setAttribute('dev', indexToDiskName(i))
            break

    disk.appendChild(target)

    driver = domxml.createElement('driver')
    driver.setAttribute('cache', 'none')
    driver.setAttribute('name', 'qemu')
    driver.setAttribute('type', drivertype)
    disk.appendChild(driver)

    return disk

if 'vmdisk' in os.environ:
    try:
        disks = os.environ['vmdisk']

        domxml = hooking.read_domxml()
        devices = domxml.getElementsByTagName('devices')[0]

        for disk in disks.split(','):
            try:
                devpath, drivertype = disk.split(':')
            except ValueError:
                sys.stderr.write('vmdisk: input error, expected diskpath:diskformat ie /path/disk.img:qcow2\n')
                sys.exit(2)

            if not drivertype in driver_types:
                sys.stderr.write('vmdisk: input error, driver type: raw or qcow2\n')
                sys.exit(2)

            diskdev = createDiskElement(domxml, devpath, drivertype)
            devices.appendChild(diskdev)

        hooking.write_domxml(domxml)
    except:
        sys.stderr.write('vmdisk: [unexpected error]: %s\n' % traceback.format_exc())
        sys.exit(2)
