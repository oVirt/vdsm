# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from vdsm.storage import sanlock_direct
from vdsm.storage import constants as sc
from . import expose


@expose
def sanlock_direct_run_dump(
        path,
        offset=0,
        size=None,
        block_size=sc.BLOCK_SIZE_512,
        alignment=sc.ALIGNMENT_1M):

    return sanlock_direct.run_dump(
        path=path,
        offset=offset,
        size=size,
        block_size=block_size,
        alignment=alignment)
