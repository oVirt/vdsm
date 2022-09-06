# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

from vdsm import jobs
from vdsm.common import concurrent


class Job(jobs.Job):
    _JOB_TYPE = "virt"
    autodelete = True


def schedule(job):
    t = concurrent.thread(job.run, name="virt/" + job.id[:8])
    t.start()
