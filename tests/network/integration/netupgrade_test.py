# Copyright 2017-2019 Red Hat, Inc.
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

from contextlib import contextmanager
import os
import tempfile

from testlib import VdsmTestCase, mock

from vdsm.network import netconfpersistence as netconf
from vdsm.network import netupgrade


@mock.patch.object(netupgrade.libvirtnetwork, 'networks', lambda: ())
@mock.patch.object(
    netupgrade.ovs_info, 'is_ovs_service_running', lambda: False
)
class TestNetUpgradeVolatileRunConfig(VdsmTestCase):
    def test_upgrade_volatile_running_config(self):

        with create_running_config(volatile=True) as vol_rconfig:
            with create_running_config(volatile=False) as pers_rconfig:
                vol_rconfig.save()
                netupgrade.upgrade()

                self.assertFalse(vol_rconfig.config_exists())
                self.assertTrue(pers_rconfig.config_exists())


@contextmanager
def create_running_config(volatile):
    conf_dir_to_mock = 'CONF_VOLATILE_RUN_DIR' if volatile else 'CONF_RUN_DIR'
    tempdir = tempfile.mkdtemp()
    with mock.patch.object(netconf, conf_dir_to_mock, tempdir):
        try:
            rconfig = netconf.RunningConfig(volatile)
            yield rconfig
        finally:
            rconfig.delete()
            assert not os.path.exists(tempdir)
