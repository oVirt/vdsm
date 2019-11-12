#!/usr/bin/python3

from __future__ import absolute_import

import os
import re
import sys
import traceback
import stat

import hooking

'''
scratchpad vdsm hook
====================
Hook creates a disk for a VM onetime usage,
the disk will be erased when the VM destroyed.
VM cannot be migrated when using scratchpad hook

syntax:
    scratchpad=size,path
    ie:
    scratchpad=20G,/tmp/myimg

    size: Optional suffixes "k" or "K" (kilobyte, 1024)
          "M" (megabyte, 1024k) and "G" (gigabyte, 1024M) and
          T (terabyte, 1024G) are supported.  "b" is ignored (default)

    note: more then one disk can be added:
    scratchpad=20G,/tmp/disk1,1T,/tmp/disk2
'''


def create_image(path, size):
    '''
    Create image file
    '''
    command = ['/usr/bin/qemu-img', 'create', '-f', 'raw', path, size]
    retcode, out, err = hooking.execCmd(command, raw=True)
    if retcode != 0:
        sys.stderr.write('scratchpad: error running command %s, err = %s\n' %
                         (' '.join(command), err))
        sys.exit(2)
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IWGRP)


def indexToDiskName(i):
    s = ''
    while True:
        s = chr(ord('a') + i % 26) + s
        i = i // 26
        if i == 0:
            break
    return 'vd' + (s or 'a')


def add_disk(domxml, path):
    '''
    Create libvirt xml node
    <disk device="disk" type="file" snapshot="no">
        <source file="[path to image]"/>
        <driver cache="writeback" error_policy="stop" type="raw"/>
    </disk>
    '''

    disk = domxml.createElement('disk')
    disk.setAttribute('device', 'disk')
    disk.setAttribute('type', 'file')
    disk.setAttribute('snapshot', 'no')

    source = domxml.createElement('source')
    source.setAttribute('file', path)
    disk.appendChild(source)

    # find a name for hdXXX
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
    driver.setAttribute('cache', 'writeback')
    driver.setAttribute('error_policy', 'stop')
    driver.setAttribute('type', 'raw')
    disk.appendChild(driver)

    devices = domxml.getElementsByTagName('devices')[0]
    devices.appendChild(disk)

if 'scratchpad' in os.environ:
    try:
        disks = os.environ['scratchpad']

        domxml = hooking.read_domxml()
        size_re = re.compile('^[\d]{1,}[k,K,M,G,T,b]?$')

        for disk in disks.split(':'):
            arr = disk.split(',')
            # check right # of parameters supplied
            if len(arr) < 2:
                sys.stderr.write('scratchpad: disk input error, must be '
                                 'size,path - provided: %s\n' % disk)
                sys.exit(2)

            # get single disk parameters
            size = arr[0]
            path = arr[1]

            if size_re.match(size) is None:
                sys.stderr.write('scratchpad: wrong size input %s, please '
                                 'refer to the README file\n' % size)
                sys.exit(2)

            if not path.startswith('/'):
                sys.stderr.write('scratchpad: path %s must be absolute '
                                 '(must start with /)\n' % path)
                sys.exit(2)

            if os.path.exists(path):
                sys.stderr.write('scratchpad: specified path exists, '
                                 'please move the file %s first\n' % path)
                sys.exit(2)

            create_image(path, size)
            add_disk(domxml, path)

        hooking.write_domxml(domxml)

    except:
        sys.stderr.write('scratchpad: [unexpected error]: %s\n' %
                         traceback.format_exc())
        sys.exit(2)
