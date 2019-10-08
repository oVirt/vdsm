#
# Copyright 2018-2019 Red Hat, Inc.
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

import os

import dnf


class VdsmUpgrade(dnf.Plugin):

    name = 'vdsmupgrade'

    def resolved(self):
        if self.base.conf.downloadonly:
            return
        transaction = self.base.transaction
        # Only upgrades are relevant, i.e. when the package is both
        # removed and installed.
        for package_set in (transaction.install_set, transaction.remove_set,):
            for package in package_set:
                if package.name == 'vdsm':
                    break
            else:
                return
        if os.path.exists('/etc/vdsm/allow-live-upgrades'):
            return
        for proc in os.listdir('/proc'):
            if proc.isdigit():
                try:
                    exe = os.readlink(os.path.join('/proc', proc, 'exe'))
                except OSError:
                    continue
                if exe == '/usr/libexec/qemu-kvm':
                    raise dnf.exceptions.Error('Running QEMU processes found, '
                                               'cannot upgrade Vdsm.')
