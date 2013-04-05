#!/usr/bin/python

import os
import sys
import traceback

import hooking

VDSM_VAR_HOOKS_DIR = '/var/run/vdsm/hooks/sriov'


def restoreDevicePermissions(devpath):
    owner = 'root:root'
    for f in os.listdir(devpath):
        if f.startswith('resource') or f in ('rom', 'reset'):
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
        for nic in os.environ['sriov'].split(','):
            vfFilePath = os.path.join(VDSM_VAR_HOOKS_DIR, nic)
            if os.path.exists(vfFilePath):
                with open(vfFilePath, 'r') as vfFile:
                    restoreDevicePermissions(vfFile.read())
                os.unlink(vfFilePath)
            else:
                sys.stderr.write('sriov after_vm_destroy: cannot find the '
                                 'virtual function reservation file of %s'
                                 'that should be at %s\n' % (nic, vfFilePath))

    except:
        sys.stderr.write('sriov after_vm_destroy: [unexpected error]: %s\n' %
                         traceback.format_exc())
        sys.exit(2)
