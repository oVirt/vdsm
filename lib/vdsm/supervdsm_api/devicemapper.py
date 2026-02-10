# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from vdsm.storage import devicemapper
from . import expose


@expose
def devicemapper_removeMapping(deviceName):
    return devicemapper.removeMapping(deviceName)
