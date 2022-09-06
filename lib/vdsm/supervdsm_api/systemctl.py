# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

from vdsm.common import systemctl
from . import expose


@expose
def systemctl_stop(name):
    return systemctl.stop(name)


@expose
def systemctl_enable(name):
    return systemctl.enable(name)
