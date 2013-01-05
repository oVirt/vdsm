#!/usr/bin/python
#
# Copyright 2011 Red Hat, Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

import os
import subprocess
import hooking
from vdsm import vdscli

s = vdscli.connect()

res = s.list(True)
if res['status']['code'] == 0:
    if not [v for v in res['vmList']
            if v.get('vmId') != os.environ.get('vmId') and
            hooking.tobool(v.get('custom', {}).get('sap_agent', False))]:
        subprocess.call(['/usr/bin/sudo', '-n', '/sbin/service', 'vhostmd',
                         'stop'])
