# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import

from vdsm import jobs


class Job(jobs.Job):
    _JOB_TYPE = "storage"
    autodelete = True

    def __init__(self, job_id, desc, host_id):
        super(Job, self).__init__(job_id, desc)
        self.host_id = host_id
