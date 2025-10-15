# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

from vdsm.storage import managedvolume
from . import expose


@expose
def managedvolume_run_helper(cmd, cmd_input=None, adapter=None):
    return managedvolume.run_helper(cmd, cmd_input=cmd_input, adapter=adapter)
