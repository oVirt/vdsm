# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

"""
This module creates vdsm configuration from a default vdsm configuration file
under /etc/vdsm/vdsm.conf. It reads conf files from drop-in dirs and updates
the configuration according to the files.

The semantics of the directories and the search order is as follows:

- /etc/vdsm/vdsm.conf - for user configuration. We install this
  file if missing, and never touch this file during upgrade.
- /etc/vdsm/vdsm.conf.d/ - for admin drop-in conf files.
- /usr/lib/vdsm/vdsm.conf.d/ - for vendor drop-in configuration files.
- /run/vdsm/vdsm.conf.d/ - for admin temporary configuration.

Files with a .conf suffix can be placed into any of the vdsm.conf.d drop-in
directories.

The priority of the configuration files is determined by the number prefix of
each file.
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import configparser

parameters = [
    # Section: [vars]
    (
        'vars',
        [
            ('cpu_affinity', 'auto'),
            ('fake_nics', 'dummy_*,veth_*'),
            ('hidden_nics', 'w*,usb*'),
            ('hidden_bonds', ''),
            ('hidden_vlans', ''),
        ],
    )
]


def set_defaults(config):
    for section, keylist in parameters:
        config.add_section(section)
        for key, value in keylist:
            config.set(section, key, value)


config = configparser.ConfigParser()
set_defaults(config)
