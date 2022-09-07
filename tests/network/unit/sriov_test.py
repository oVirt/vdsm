# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

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
