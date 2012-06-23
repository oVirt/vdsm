#!/usr/bin/python

import os
import sys
import grp
import pwd
import traceback

import hooking

DEV_MAPPER_PATH = "/dev/mapper"
DEV_DIRECTLUN_PATH = '/dev/directlun'
NUM_OF_PCI = 27

def indexToDiskName(i):
    s = ''
    while True:
        s = chr(ord('a') + i % 26) + s
        i = i / 26
        if i == 0:
            break
    return 'vd' + (s or 'a')

def createDiskElement(domxml, devpath, lunid, options):
    '''
    <disk device="disk" type="block">
        <source dev="/dev/mapper/lunid"/>
        <target bus="virtio" dev="vda"/>
        <driver cache="none" error_policy="stop" name="qemu" type="raw"/>
    </disk>
    '''

    disk = domxml.createElement('disk')
    disk.setAttribute('device', 'disk')
    disk.setAttribute('type', 'block')

    source = domxml.createElement('source')
    source.setAttribute('dev', devpath)
    disk.appendChild(source)

    # find a name for vdXXX
    target = domxml.createElement('target')
    target.setAttribute('bus', 'virtio')
    xmldisks = domxml.getElementsByTagName('disk')
    disks = []
    for d in xmldisks:
        disks.append(d.getElementsByTagName('target')[0].getAttribute('dev'))

    for i in range(0, NUM_OF_PCI):
        if not indexToDiskName(i) in disks:
            target.setAttribute('dev', indexToDiskName(i))
            break

    disk.appendChild(target)

    driver = domxml.createElement('driver')
    driver.setAttribute('cache', 'none')
    driver.setAttribute('name', 'qemu')
    driver.setAttribute('type', 'raw')
    disk.appendChild(driver)

    if 'readonly' in options:
        readonly = domxml.createElement('readonly')
        disk.appendChild(readonly)

    return disk


def createDirectory(dirpath):

    # we don't use os.mkdir/chown because we need sudo
    command = ['/bin/mkdir', '-p', dirpath]
    retcode, out, err = hooking.execCmd(command, sudo=True, raw=True)
    if retcode != 0:
        sys.stderr.write('directlun: error mkdir %s, err = %s\n' % (dirpath, err))
        sys.exit(2)

    mode = '755'
    command = ['/bin/chmod', mode, dirpath]
    if retcode != 0:
        sys.stderr.write('directlun: error chmod %s %s, err = %s\n' % (dirpath, mode, err))
        sys.exit(2)


def cloneDeviceNode(srcpath, devpath):
    '''Clone a device node into a temporary private location.'''

    # we don't use os.remove/mknod/chmod/chown because we need sudo
    command = ['/bin/rm', '-f', devpath]
    retcode, out, err = hooking.execCmd(command, sudo=True, raw=True)
    if retcode != 0:
        sys.stderr.write('directlun: error rm -f %s, err = %s\n' % (devpath, err))
        sys.exit(2)

    stat = os.stat(srcpath)
    major = os.major(stat.st_rdev)
    minor = os.minor(stat.st_rdev)
    command = ['/bin/mknod', devpath, 'b', str(major), str(minor)]
    retcode, out, err = hooking.execCmd(command, sudo=True, raw=True)
    if retcode != 0:
        sys.stderr.write('directlun: error mknod %s, err = %s\n' % (devpath, err))
        sys.exit(2)

    mode = '660'
    command = ['/bin/chmod', mode, devpath]
    retcode, out, err = hooking.execCmd(command, sudo=True, raw=True)
    if retcode != 0:
        sys.stderr.write('directlun: error chmod %s to %s, err = %s\n' % (devpath, mode, err))
        sys.exit(2)

    group = grp.getgrnam('qemu')
    gid = group.gr_gid
    user = pwd.getpwnam('qemu')
    uid = user.pw_uid
    owner = str(uid) + ':' + str(gid)
    command = ['/bin/chown', owner, devpath]
    retcode, out, err = hooking.execCmd(command, sudo=True, raw=True)
    if retcode != 0:
        sys.stderr.write('directlun: error chown %s to %s, err = %s\n' % (devpath, owner, err))
        sys.exit(2)


if os.environ.has_key('directlun'):
    try:
        luns = os.environ['directlun']

        domxml = hooking.read_domxml()
        devices = domxml.getElementsByTagName('devices')[0]

        createDirectory(DEV_DIRECTLUN_PATH)

        for lun in luns.split(','):
            try:
                lun, options = lun.split(':')
            except ValueError:
                options = ''
            options = options.split(';')

            srcpath = DEV_MAPPER_PATH + '/' + lun
            if not os.path.exists(srcpath):
                sys.stderr.write('directlun: device not found %s\n' % srcpath)
                sys.exit(2)

            uuid = domxml.getElementsByTagName('uuid')[0]
            uuid = uuid.childNodes[0].nodeValue
            devpath = DEV_DIRECTLUN_PATH + '/' + lun + '-' + uuid

            cloneDeviceNode(srcpath, devpath)

            sys.stderr.write('directlun: adding lun %s\n' % devpath)
            diskdev = createDiskElement(domxml, devpath, lun, options)
            sys.stderr.write('directlun: adding xml: %s\n' % diskdev.toxml())
            devices.appendChild(diskdev)

        hooking.write_domxml(domxml)
    except:
        sys.stderr.write('directlun: [unexpected error]: %s\n' % traceback.format_exc())
        sys.exit(2)
