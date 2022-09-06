# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

"""
This is (cold) merge data operation job.
This job performs the following steps:
1. Prepare all volumes in chain
2. Executes qemuimg commit
3. Tears down the image
"""

from __future__ import absolute_import

import logging

from vdsm.storage import bitmaps
from vdsm.storage import constants as sc
from vdsm.storage import guarded
from vdsm.storage import qemuimg

from . import base


class Job(base.Job):
    log = logging.getLogger('storage.sdm.merge')

    def __init__(self, job_id, subchain, merge_bitmaps=False):
        super(Job, self).__init__(job_id, 'merge_subchain',
                                  subchain.host_id)
        self.subchain = subchain
        self.operation = None
        self.merge_bitmaps = merge_bitmaps

    @property
    def progress(self):
        return getattr(self.operation, 'progress', None)

    def _run(self):
        self.log.info("Merging subchain %s", self.subchain)
        with guarded.context(self.subchain.locks):
            self.subchain.validate()
            with self.subchain.prepare(), self.subchain.volume_operation():
                top_vol_path = self.subchain.top_vol.getVolumePath()
                base_vol_path = self.subchain.base_vol.getVolumePath()
                self.log.info(
                    "Committing data from %s to %s",
                    top_vol_path, base_vol_path)

                self.operation = qemuimg.commit(
                    top_vol_path,
                    topFormat=sc.fmt2str(self.subchain.top_vol.getFormat()),
                    base=base_vol_path)
                self.operation.run()

                if (self.subchain.base_vol.getFormat() == sc.COW_FORMAT and
                        self.merge_bitmaps):
                    self.log.info(
                        "Merging bitmaps from %s to %s",
                        top_vol_path, base_vol_path)
                    # Add and merge all the bitmaps from top_vol that don't
                    # exist on the base_vol and not handled by block-commit.
                    base_parent_vol = self.subchain.base_vol.getParentVolume()
                    base_parent_path = (base_parent_vol.getVolumePath()
                                        if base_parent_vol else None)
                    bitmaps.merge_bitmaps(
                        base_vol_path, top_vol_path,
                        base_parent_path=base_parent_path)
