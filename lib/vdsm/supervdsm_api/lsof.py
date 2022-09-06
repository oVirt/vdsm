# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from vdsm.storage import lsof
from . import expose


@expose
def lsof_run(path):
    return lsof.run(path)
