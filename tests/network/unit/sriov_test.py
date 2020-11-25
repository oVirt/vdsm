# Copyright 2018-2020 Red Hat, Inc.
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

from unittest import mock

from vdsm.network.link import sriov


PCI1 = '0000.1234.1.1'
PCI2 = '0000.1234.1.2'
NUMVFS = 2


class TestSriov(object):
    @mock.patch.object(sriov.netconfpersistence, 'RunningConfig')
    def test_persist_config(self, mock_rconfig):
        sriov.persist_numvfs(PCI1, NUMVFS)

        mock_rconfig.return_value.set_device.assert_called_once_with(
            PCI1, {'sriov': {'numvfs': NUMVFS}}
        )
        mock_rconfig.return_value.save.assert_called_once()

    @mock.patch.object(sriov, 'glob')
    def test_list_sriov_pci_devices(self, mock_glob):
        mock_glob.return_value = [
            '/sys/bus/pci/devices/' + PCI1 + '/sriov_totalvfs',
            '/sys/bus/pci/devices/' + PCI2 + '/sriov_totalvfs',
        ]

        pci_list = sriov.list_sriov_pci_devices()

        assert pci_list == set([PCI1, PCI2])
