# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import os.path

from vdsm.common.cache import memoized

_SOURCES = {
    'random': '/dev/random',
    'urandom': '/dev/urandom',
    'hwrng': '/dev/hwrng'
}


def list_available():
    return [
        source for (source, path) in _SOURCES.items()
        if os.path.exists(path) and
        # REQUIRE_FOR: Engine <= 4.0
        source != 'urandom'
    ]


def get_device(name):
    return _SOURCES[name]


@memoized
def get_source_name(dev):
    for name, path in _SOURCES.items():
        if dev == path:
            return name
    raise KeyError(dev)
