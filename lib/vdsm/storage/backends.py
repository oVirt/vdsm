# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import sanlock
import six

from . import blockSD
from . import constants as sc
from . import glusterSD
from . import localFsSD
from . import nfsSD


_BACKENDS = {
    "FCP": blockSD.BlockStorageDomain,
    "GLUSTERFS": glusterSD.GlusterStorageDomain,
    "ISCSI": blockSD.BlockStorageDomain,
    "LOCALFS": localFsSD.LocalFsStorageDomain,
    "NFS": nfsSD.NfsStorageDomain,
    "POSIXFS": nfsSD.NfsStorageDomain,
}


def supported_block_size():
    # TODO: needed only for 4.3, we require sanlock 3.7.3.
    try:
        have_4k = sc.BLOCK_SIZE_4K in sanlock.SECTOR_SIZE
    except ModuleNotFoundError:
        have_4k = False

    res = {}
    for name, backend in six.iteritems(_BACKENDS):
        if have_4k:
            res[name] = backend.supported_block_size
        else:
            res[name] = (sc.BLOCK_SIZE_512,)

    return res
