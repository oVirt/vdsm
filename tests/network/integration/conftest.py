# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import logging
import os
import re

import pytest

from vdsm.network.ipwrapper import getLinks

from network.nettestlib import Interface

IPV4_ADDRESS1 = '192.168.99.1'  # Tracking the address used in ip_rule_test

TEST_NIC_REGEX = re.compile(
    r'''
    dummy_[a-zA-Z0-9]+|     # match dummy devices
    veth_[a-zA-Z0-9]+|      # match veth devices
    bond_[a-zA-Z0-9]+|      # match bond devices
    vdsm-אבג[a-zA-Z0-9]*|   # match utf-8 bridges
    vdsm-[a-zA-Z0-9]*       # match any generic vdsm test interface
    ''',
    re.VERBOSE,
)


@pytest.fixture(scope='session', autouse=True)
def requires_root():
    if os.geteuid() != 0:
        pytest.skip('Integration tests require root')


@pytest.fixture(scope='session', autouse=True)
def cleanup_leftover_interfaces():
    for interface in getLinks():
        if TEST_NIC_REGEX.match(interface.name):
            logging.warning('Found leftover interface %s', interface)
            try:
                Interface.from_existing_dev_name(interface.name).remove()
            except Exception as e:
                logging.warning('Removal of "%s" failed: %s', interface, e)
