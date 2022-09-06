# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

from vdsm.storage import multipath
from . import expose


@expose
def multipath_resize_map(name):
    return multipath.resize_map(name)


@expose
def multipath_is_ready():
    return multipath.is_ready()


@expose
def multipath_get_scsi_serial(physdev):
    return multipath.get_scsi_serial(physdev)
