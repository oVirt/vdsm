# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

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
