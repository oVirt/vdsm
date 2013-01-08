#!/usr/bin/python

import os
import sys
import traceback

import hooking

'''
after_vm_destroy:
remove the device node that we created
'''

DEV_DIRECTLUN_PATH = '/dev/directlun'


def removeDeviceNode(devpath):

    # we don't use os.unlink because we need sudo
    command = ['/bin/rm', '-f', devpath]
    retcode, out, err = hooking.execCmd(command, sudo=True, raw=True)
    if retcode != 0:
        sys.stderr.write('directlun after_vm_destroy: error rm -f %s, '
                         'err = %s\n' % (devpath, err))
        sys.exit(2)

if 'directlun' in os.environ:
    try:
        luns = os.environ['directlun']
        domxml = hooking.read_domxml()

        for lun in luns.split(','):
            try:
                lun, options = lun.split(':')
            except ValueError:
                options = ''

            uuid = domxml.getElementsByTagName('uuid')[0]
            uuid = uuid.childNodes[0].nodeValue
            devpath = DEV_DIRECTLUN_PATH + '/' + lun + '-' + uuid

            removeDeviceNode(devpath)

        hooking.write_domxml(domxml)
    except:
        sys.stderr.write('directlun after_vm_destroy: [unexpected error]: '
                         '%s\n' % traceback.format_exc())
        sys.exit(2)
