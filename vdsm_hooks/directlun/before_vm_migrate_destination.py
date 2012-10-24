#!/usr/bin/python

import os
import sys
import grp
import pwd
import traceback

import hooking

DEV_MAPPER_PATH = "/dev/mapper"
DEV_DIRECTLUN_PATH = '/dev/directlun'

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

if 'directlun' in os.environ:
    try:
        luns = os.environ['directlun']

        domxml = hooking.read_domxml()

        createDirectory(DEV_DIRECTLUN_PATH)

        for lun in luns.split(','):
            try:
                lun, options = lun.split(':')
            except ValueError:
                options = ''
            options = options.split(';')

            srcpath = DEV_MAPPER_PATH + '/' + lun
            if not os.path.exists(srcpath):
                sys.stderr.write('directlun before_vm_migration_destination: device not found %s\n' % srcpath)
                sys.exit(2)

            uuid = domxml.getElementsByTagName('uuid')[0]
            uuid = uuid.childNodes[0].nodeValue
            devpath = DEV_DIRECTLUN_PATH + '/' + lun + '-' + uuid

            cloneDeviceNode(srcpath, devpath)

        hooking.write_domxml(domxml)
    except:
        sys.stderr.write('directlun before_vm_migration_destination: [unexpected error]: %s\n' % traceback.format_exc())
        sys.exit(2)
