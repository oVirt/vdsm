#
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from __future__ import absolute_import
from __future__ import division

from network.compat import mock
from testlib import VdsmTestCase

from vdsm.network.ip import dhclient

DEVNAME = 'eth99'
DHCLIENT_CMDLINE_WITH_HOST_AT_TAIL = '\0'.join(
    [
        '/sbin/dhclient',
        '-6',
        '-1',
        '-lf',
        '/var/lib/dhclient/dhclient6--veth_y6Bn2k.lease',
        '-pf',
        '/var/run/dhclient6-veth_y6BPRwrn2k.pid',
        'eth99',
        '-H',
        'vdsm_functional_tests_host-el7',
    ]
)


class TestIPDhclient(VdsmTestCase):
    @mock.patch.object(
        dhclient,
        'open',
        mock.mock_open(read_data=DHCLIENT_CMDLINE_WITH_HOST_AT_TAIL),
        create=True,
    )
    @mock.patch.object(dhclient, 'pgrep', lambda x: (0,))
    def test_daemon_cmdline_with_last_arg_as_hostname(self):
        """
        In most cases, the dhclient is executed with a cmdline that locates
        the device name at the last argument. However, an exception has been
        detected with DHCPv6 from ifcfg initscripts, in which the last argument
        is the host name and the device name is placed just before it.
        """
        dhcp_info = dhclient.dhcp_info(devices=(DEVNAME,))
        expected = {DEVNAME: {dhclient.DHCP4: False, dhclient.DHCP6: True}}
        self.assertEqual(expected, dhcp_info)
