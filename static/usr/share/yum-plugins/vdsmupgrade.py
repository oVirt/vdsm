#
# Copyright 2018 Red Hat, Inc.
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

from __future__ import absolute_import

from yum.Errors import PackageSackError
from yum.plugins import PluginYumExit, TYPE_CORE, TYPE_INTERACTIVE

import os


requires_api_version = '2.7'
plugin_type = (TYPE_CORE, TYPE_INTERACTIVE)


def predownload_hook(conduit):
    packages = [p for p in conduit.getDownloadPackages() if p.name == 'vdsm']
    if not packages:
        return
    if os.path.exists('/etc/vdsm/allow-live-upgrades'):
        return
    rpmdb = conduit.getRpmDB()
    try:
        if not rpmdb.searchNames(['vdsm']):
            return
    except PackageSackError:
        return
    for proc in os.listdir('/proc'):
        if proc.isdigit():
            try:
                exe = os.readlink(os.path.join('/proc', proc, 'exe'))
            except OSError:
                continue
            if exe == '/usr/libexec/qemu-kvm':
                raise PluginYumExit('Running QEMU processes found, '
                                    'cannot upgrade Vdsm.')
