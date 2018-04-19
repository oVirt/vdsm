#
# Copyright 2013-2016 Red Hat, Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from __future__ import absolute_import
from __future__ import division

import logging

from . import gluster_mgmt_api
from vdsm.gluster import cli
from vdsm.gluster import exception as ge
from vdsm.gluster.cli import TaskType


log = logging.getLogger("Gluster")


def _getTasksData(value):
    data = {}
    state = value['status']
    volumeName = value['volumeName']
    taskType = value['taskType']
    if taskType == TaskType.REBALANCE:
        data = cli.volumeRebalanceStatus(volumeName)
    elif taskType == TaskType.REMOVE_BRICK:
        data = cli.volumeRemoveBrickStatus(volumeName,
                                           value['bricks'])

    summary = data['summary'] if 'summary' in data else {}
    return {"volume": volumeName,
            "status": state,
            "type": taskType,
            "bricks": value['bricks'],
            "data": summary}


@gluster_mgmt_api
def tasksList(taskIds=[]):
    details = {}
    tasks = cli.volumeTasks()
    for tid in tasks:
        if taskIds and tid not in taskIds:
            continue

        try:
            details[tid] = _getTasksData(tasks[tid])
        except ge.GlusterException:
            log.error("gluster exception occured", exc_info=True)

    return details
