# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

"""
testing - Testing environment helpers
"""

from __future__ import absolute_import
from __future__ import division

import os


def on_centos(ver=''):
    with open('/etc/redhat-release') as f:
        return 'CentOS Linux release {}'.format(ver) in f.readline()


def on_rhel(ver=''):
    with open('/etc/redhat-release') as f:
        return 'Red Hat Enterprise Linux release {}'.format(
            ver) in f.readline()


def on_fedora(ver=''):
    with open('/etc/redhat-release') as f:
        return 'Fedora release {}'.format(ver) in f.readline()


def on_travis_ci():
    return 'TRAVIS_CI' in os.environ


def on_ovirt_ci():
    return 'OVIRT_CI' in os.environ
