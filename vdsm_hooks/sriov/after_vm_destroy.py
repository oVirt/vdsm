#!/usr/bin/python

import os
import sys
import traceback

import hooking
from vdsm import libvirtconnection

SYS_NIC_PATH = '/sys/class/net/%s'
VDSM_VAR_HOOKS_DIR = '/var/run/vdsm/hooks'
SRIOV_CACHE_FILENAME = 'sriov.cache'


def returnDeviceToHost(addr, devpath):
    # attach device back to host
    connection = libvirtconnection.get(None)
    nodeDevice = connection.nodeDeviceLookupByName(addr)
    if nodeDevice is not None:
        sys.stderr.write('sriov after_vm_destroy: attaching pci device %s '
                         'back to host\n' % addr)
        nodeDevice.reAttach()

    # return device permissions
    owner = 'root:root'
    for f in os.listdir(devpath):
        if f.startswith('resource') or f == 'rom' or f == 'reset':
            dev = os.path.join(devpath, f)
            command = ['/bin/chown', owner, dev]
            retcode, out, err = hooking.execCmd(command, sudo=True, raw=True)
            if retcode != 0:
                sys.stderr.write('sriov after_vm_destroy: error chown %s to '
                                 '%s, err = %s\n' % (dev, owner, err))
                sys.exit(2)

if 'sriov' in os.environ:
    try:
        lines = ''
        nics = os.environ['sriov']
        path = VDSM_VAR_HOOKS_DIR + '/' + SRIOV_CACHE_FILENAME

        if os.path.exists(path):
            f = open(path, 'r')
            while 1:
                line = f.readline()
                if not line:
                    break
                pass  # do something
                nicAddr = line.split('=')
                if nicAddr[0] in nics:
                    returnDeviceToHost(nicAddr[1], nicAddr[2].strip('\n'))
                else:
                    lines += line
            f.close()

            f = open(path, 'w')
            f.writelines(lines)
            f.close()
        else:
            sys.stderr.write('sriov after_vm_destroy: cannot find sriov cache '
                             'file %s\n' % path)

    except:
        sys.stderr.write('sriov after_vm_destroy: [unexpected error]: %s\n' %
                         traceback.format_exc())
        sys.exit(2)
