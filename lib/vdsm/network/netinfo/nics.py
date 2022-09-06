# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import io

OPERSTATE_UP = 'up'


def operstate(nic_name):
    with io.open('/sys/class/net/%s/operstate' % nic_name) as operstateFile:
        return operstateFile.read().strip()


def info(link):
    return {'hwaddr': link.address}
