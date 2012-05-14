#!/usr/bin/python

import os
import sys
import hooking
import traceback

'''
floppy vdsm hook
================
adding floppy to libvirt domain entry:

syntax:
floppy=/path/to/vfd

<disk type='file' device='floppy'>
    <source file='/home/iso/my.vfd'/>
    <target dev='fda' />
</disk>

Note:
    some linux distro need to load the floppy disk kernel module:
    # modprobe floppy
'''

if os.environ.has_key('floppy'):
    try:
        floppy = os.environ['floppy']

        if not os.path.isfile(floppy):
            sys.stderr.write('floppy: file not exists or not enough permissions: %s\n' % floppy)
            sys.exit(2)

        domxml = hooking.read_domxml()
        devices = domxml.getElementsByTagName('devices')[0]

        disk = domxml.createElement('disk')
        disk.setAttribute('type', 'file')
        disk.setAttribute('device', 'floppy')
        devices.appendChild(disk)

        source = domxml.createElement('source')
        source.setAttribute('file', floppy)
        disk.appendChild(source)

        target = domxml.createElement('target')
        target.setAttribute('dev', 'fda')
        disk.appendChild(target)

        hooking.write_domxml(domxml)

    except:
        sys.stderr.write('floppy: [unexpected error]: %s\n' % traceback.format_exc())
        sys.exit(2)
