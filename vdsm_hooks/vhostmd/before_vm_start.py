#!/usr/bin/python

import os
import subprocess
import hooking


if hooking.tobool(os.environ.get('sap_agent', False)):
    domxml = hooking.read_domxml()

    subprocess.call(['/usr/bin/sudo', '-n', '/sbin/service', 'vhostmd',
                     'start'])
    devs = domxml.getElementsByTagName('devices')[0]
    diskelem = domxml.createElement('disk')
    diskelem.setAttribute('device', 'disk')

    source = domxml.createElement('source')
    diskelem.setAttribute('type', 'file')
    source.setAttribute('file', '/dev/shm/vhostmd0')
    diskelem.appendChild(source)

    target = domxml.createElement('target')
    target.setAttribute('dev', 'vdzz') # FIXME do not use a static location
    target.setAttribute('bus', 'virtio')
    diskelem.appendChild(target)

    diskelem.appendChild(domxml.createElement('readonly'))

    devs.appendChild(diskelem)

    hooking.write_domxml(domxml)
