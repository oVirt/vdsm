# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later


class Dns(object):
    def __init__(self, netconf, runconf):
        self._netconf = netconf
        self._runconf = runconf
        self._state = self._create_dns()

    @property
    def state(self):
        return self._state

    @property
    def auto_dns(self):
        return self._netconf.default_route and not self._state

    def _create_dns(self):
        """
        The DNS state may include one of the following outputs:
            - None: The network does not include any DNS info.
            - Empty list:
                - The nameservers have been explicitly cleared.
                - The network or its d.route is removed and it has nameservers.
            - The nameservers have been explicitly set for the network.
        """
        nameservers = None
        if self._netconf.default_route:
            nameservers = self._netconf.nameservers
        elif self._runconf.default_route and self._runconf.nameservers:
            nameservers = []
        return nameservers
