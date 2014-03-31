#!/usr/bin/python

import os
import re
import sys
import traceback

import hooking

'''
after_vm_destroy:
return the original owner of the usb device
'''

HOOK_HOSTUSB_PATH = '/var/run/vdsm/hooks/hostusb-permissions'


def get_owner(devpath):
    uid = pid = -1
    content = ''

    if not os.path.isfile(HOOK_HOSTUSB_PATH):
        return uid, pid

    f = file(HOOK_HOSTUSB_PATH, 'r')
    for line in f:
        if len(line) > 0 and line.split(':')[0] == devpath:
            entry = line.split(':')
            uid = entry[1]
            pid = entry[2]
        elif len(line) > 0:
            content += line + '\n'

    f.close()
    if uid != -1:
        f = file(HOOK_HOSTUSB_PATH, 'w')
        f.writelines(content)
        f.close()

    return uid, pid


# !TODO:
# merge chown with before_vm_start.py
# maybe put it in hooks.py?
def chown(vendorid, productid):

    # remove the 0x from the vendor and product id
    devid = vendorid[2:] + ':' + productid[2:]
    command = ['lsusb', '-d', devid]
    retcode, out, err = hooking.execCmd(command, raw=True)
    if retcode != 0:
        sys.stderr.write('hostusb: cannot find usb device: %s\n' % devid)
        sys.exit(2)

    devpath = '/dev/bus/usb/' + out[4:7] + '/' + out[15:18]

    uid, gid = get_owner(devpath)
    if uid == -1:
        sys.stderr.write('hostusb after_vm_destroy: cannot find devpath: %s '
                         'in file: %s\n' % (devpath, HOOK_HOSTUSB_PATH))
        return

    # we don't use os.chown because we need sudo
    owner = str(uid) + ':' + str(gid)
    command = ['/bin/chown', owner, devpath]
    retcode, out, err = hooking.execCmd(command, sudo=True, raw=True)
    if retcode != 0:
        sys.stderr.write('hostusb after_vm_destroy: error chown %s to %s, '
                         'err = %s\n' % (devpath, owner, err))
        sys.exit(2)

if 'hostusb' in os.environ:
    try:
        regex = re.compile('^0x[\d,A-F,a-f]{4}$')
        for usb in os.environ['hostusb'].split('&'):
            vendorid, productid = usb.split(':')
            if len(regex.findall(vendorid)) != 1 or \
                    len(regex.findall(productid)) != 1:
                sys.stderr.write('hostusb after_vm_destroy: bad input, '
                                 'expected 0x0000 format for vendor and '
                                 'product id, input: %s:%s\n' %
                                 (vendorid, productid))
                sys.exit(2)
            chown(vendorid, productid)

    except:
        sys.stderr.write('hostusb after_vm_destroy: [unexpected error]: %s\n' %
                         traceback.format_exc())
        sys.exit(2)
