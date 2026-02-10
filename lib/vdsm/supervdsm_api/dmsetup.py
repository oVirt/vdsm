# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from vdsm.storage import dmsetup
from . import expose


@expose
def dmsetup_run_status(target=None):
    return dmsetup.run_status(target)
