# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

from vdsm.storage import nbd

from . import expose


@expose
def nbd_start_transient_service(server_id, config):
    return nbd.start_transient_service(server_id, config)
