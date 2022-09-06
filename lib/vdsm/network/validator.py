# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

from vdsm.network.ip import validator as ip_validator
from vdsm.network.link import validator as link_validator
from vdsm.network import netswitch


def validate(networks, bondings, net_info, running_config):
    link_validator.validate(networks, bondings)
    ip_validator.validate(networks)
    netswitch.configurator.validate(
        networks, bondings, net_info, running_config
    )
