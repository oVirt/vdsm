#
# Copyright 2016 Red Hat, Inc.
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
from __future__ import division

from vdsm.common import commands
from vdsm.tool.service import service_start, service_status, service_stop

from testlib import VdsmTestCase

el7_ovirt36_repo = 'http://resources.ovirt.org/pub/ovirt-3.6/rpm/el7/'


def downgrade_vdsm(url):
    commands.run(['yum-config-manager', '--add-repo', url])
    commands.run(['yum', 'swap', '--', 'erase', '-y', 'vdsm\*',
                 '--', 'install', '-y', 'vdsm-4.17.10.1-0.el7.centos.noarch'])


def upgrade_vdsm():
    commands.run(['yum-config-manager', '--enable', 'localsync'])
    commands.run(['yum', 'update', '-y', 'vdsm'])


class UpgradeTest(VdsmTestCase):
    def setUp(self):
        commands.run(['yum-config-manager', '--disable', 'localsync'])

    def tearDown(self):
        commands.run(['yum-config-manager', '--disable', '*ovirt-3.6*'])
        commands.run(['yum-config-manager', '--enable', 'localsync'])
        # make sure vdsm is installed and running
        commands.run(['yum', 'install', '-y', 'vdsm'])
        service_start('vdsmd')

    def service_up_test(self):
        service_start('vdsmd')
        vdsm_version = commands.run(['rpm', '-q', 'vdsm'])
        downgrade_vdsm(el7_ovirt36_repo)
        upgrade_vdsm()

        self.assertEqual(commands.run(['rpm', '-q', 'vdsm']), vdsm_version)
        self.assertEqual(service_status('vdsmd'), 0)

    def service_down_test(self):
        service_stop('vdsmd')
        vdsm_version = commands.run(['rpm', '-q', 'vdsm'])
        downgrade_vdsm(el7_ovirt36_repo)
        upgrade_vdsm()

        self.assertEqual(commands.run(['rpm', '-q', 'vdsm']), vdsm_version)
        self.assertEqual(service_status('vdsmd'), 1)
