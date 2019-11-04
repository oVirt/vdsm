# Copyright 2016-2019 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA
# 02110-1301  USA
#
# Refer to the README and COPYING files for full details of the license
#

from __future__ import absolute_import
from __future__ import division

import pytest

from vdsm.network import ifacquire

from network.compat import mock


NIC_NAME = 'eth3'

NIC_IFCFG = [
    'DEVICE=' + NIC_NAME + '\n',
    'BOOTPROTO=dhcp\n',
    'ONBOOT=yes\n',
    'MTU=1500\n',
]

PARTIALLY_ACQUIRED_NIC_IFCFG = ifacquire.ACQUIRED_IFCFG_PREFIX + [
    'DEVICE=' + NIC_NAME + '\n',
    'BOOTPROTO=dhcp\n',
    'ONBOOT=yes\n',
    'MTU=1500\n',
    'NM_CONTROLLED=no  # Set by VDSM\n',
]

IFCFG_WITHOUT_NEEDED_CHANGES = [
    'DEVICE=' + NIC_NAME + '\n',
    'BOOTPROTO=dhcp\n',
    'ONBOOT=no  # Changed by VDSM, original: ONBOOT=yes\n',
    'MTU=1500\n',
    'NM_CONTROLLED=no  # Set by VDSM\n',
]

FULLY_ACQUIRED_NIC_IFCFG = (
    ifacquire.ACQUIRED_IFCFG_PREFIX + IFCFG_WITHOUT_NEEDED_CHANGES
)

NETINFO_NETS = {'net1': {'ports': [NIC_NAME, 'vnet0']}}


class FailTest(Exception):
    pass


class TestAcquireNic(object):
    @mock.patch.object(ifacquire.address, 'flush')
    @mock.patch.object(ifacquire.dhclient, 'kill')
    @mock.patch.object(ifacquire.ifcfg, 'ifdown')
    @mock.patch.object(ifacquire, 'open', create=True)
    def test_do_not_acquire_owned_nic(
        self, mock_open, mock_ifdown, mock_kill, mock_flush
    ):
        with ifacquire.Transaction(netinfo_nets=NETINFO_NETS) as a:
            a.acquire(ifaces=[NIC_NAME])

        mock_open.assert_not_called()
        mock_ifdown.assert_not_called()
        mock_kill.assert_not_called()
        mock_flush.assert_not_called()

    @mock.patch.object(ifacquire.os.path, 'isfile', lambda x: False)
    @mock.patch.object(ifacquire.linkiface, 'iface')
    @mock.patch.object(ifacquire.address, 'flush', return_value=None)
    @mock.patch.object(ifacquire.dhclient, 'kill', return_value=None)
    def test_acquire_non_ifcfg_nic(self, mock_kill, mock_flush, mock_iface):
        mock_iface.return_value.exists.return_value = True
        with ifacquire.Transaction(netinfo_nets={}) as a:
            a.acquire(ifaces=[NIC_NAME])
            mock_kill.assert_any_call(NIC_NAME, family=4)
            mock_kill.assert_any_call(NIC_NAME, family=6)
            mock_flush.assert_called_with(NIC_NAME)

    def test_acquire_ifcfg_nic(self):
        self._test_acquire_ifcfg_nic(
            original_ifcfg=NIC_IFCFG,
            ifcfg_after_turn_down=PARTIALLY_ACQUIRED_NIC_IFCFG,
            ifcfg_after_disable_onboot=FULLY_ACQUIRED_NIC_IFCFG,
        )

    def test_acquire_once_owned_ifcfg_nic(self):
        self._test_acquire_ifcfg_nic(
            original_ifcfg=FULLY_ACQUIRED_NIC_IFCFG,
            ifcfg_after_turn_down=FULLY_ACQUIRED_NIC_IFCFG,
            ifcfg_after_disable_onboot=FULLY_ACQUIRED_NIC_IFCFG,
        )

    def test_acquire_prepared_nic(self):
        self._test_acquire_ifcfg_nic(
            original_ifcfg=IFCFG_WITHOUT_NEEDED_CHANGES,
            ifcfg_after_turn_down=FULLY_ACQUIRED_NIC_IFCFG,
            ifcfg_after_disable_onboot=FULLY_ACQUIRED_NIC_IFCFG,
        )

    @mock.patch.object(ifacquire.os.path, 'isfile', lambda x: True)
    @mock.patch.object(ifacquire.ifcfg, 'ifdown', return_value=None)
    @mock.patch.object(
        ifacquire, 'open', new_callable=mock.mock_open, create=True
    )
    # atomic_file_write has similiar API to open
    @mock.patch.object(
        ifacquire.fileutils, 'atomic_file_write', new_callable=mock.mock_open
    )
    def _test_acquire_ifcfg_nic(
        self,
        mock_atomic_write,
        mock_open,
        mock_ifdown,
        original_ifcfg,
        ifcfg_after_turn_down,
        ifcfg_after_disable_onboot,
    ):
        open_file = mock_open()
        atomic_write_file = mock_atomic_write()
        open_file.readlines.return_value = original_ifcfg
        atomic_write_file.readlines.return_value = original_ifcfg

        with ifacquire.Transaction(netinfo_nets={}) as a:
            a.acquire(ifaces=[NIC_NAME])

            atomic_write_file.writelines.assert_called_with(
                ifcfg_after_turn_down
            )
            atomic_write_file.readlines.return_value = ifcfg_after_turn_down
            mock_ifdown.assert_called_with(NIC_NAME)

        atomic_write_file.writelines.assert_called_with(
            ifcfg_after_disable_onboot
        )

    @mock.patch.object(ifacquire.ifcfg, 'ifdown', lambda x: None)
    @mock.patch.object(ifacquire.os.path, 'isfile', lambda x: True)
    @mock.patch.object(ifacquire.ifcfg, 'ifup', return_value=None)
    @mock.patch.object(
        ifacquire, 'open', new_callable=mock.mock_open, create=True
    )
    # atomic_file_write has similiar API to open
    @mock.patch.object(
        ifacquire.fileutils, 'atomic_file_write', new_callable=mock.mock_open
    )
    def test_rollback_acquired_ifcfg_nic(
        self, mock_atomic_write, mock_open, mock_ifup
    ):
        open_file = mock_open()
        atomic_write_file = mock_atomic_write()
        open_file.readlines.return_value = NIC_IFCFG
        atomic_write_file.readlines.return_value = NIC_IFCFG

        with pytest.raises(FailTest):
            with ifacquire.Transaction(netinfo_nets={}) as a:
                a.acquire(ifaces=[NIC_NAME])

                raise FailTest()

        atomic_write_file.writelines.assert_called_with(NIC_IFCFG)
        mock_ifup.assert_called_with(NIC_NAME)
