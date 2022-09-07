# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

from vdsm.network import dns


class TestNetworkDnsIntegration(object):
    def test_read_dns_entries_from_resolv_conf(self):
        # Assuming at least one DNS entry exists on the host running the tests
        nameservers = dns.get_host_nameservers()
        assert nameservers, 'No DNS entries detected'
