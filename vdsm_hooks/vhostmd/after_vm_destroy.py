#!/usr/bin/python

import os
import subprocess
import hooking
import vdscli

s = vdscli.connect()

res = s.list(True)
if res['status']['code'] == 0:
    if not [ v for v in res['vmList']
             if v.get('vmId') != os.environ.get('vmId') and
                hooking.tobool(v.get('custom', {}).get('sap_agent', False)) ]:
        subprocess.call(['/usr/bin/sudo', '-n', '/sbin/service', 'vhostmd',
                         'stop'])
