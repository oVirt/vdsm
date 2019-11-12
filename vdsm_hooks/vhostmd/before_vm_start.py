#!/usr/bin/python3
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
from __future__ import absolute_import

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
    target.setAttribute('dev', 'vdzz')  # FIXME do not use a static location
    target.setAttribute('bus', 'virtio')
    diskelem.appendChild(target)

    diskelem.appendChild(domxml.createElement('readonly'))

    devs.appendChild(diskelem)

    hooking.write_domxml(domxml)
