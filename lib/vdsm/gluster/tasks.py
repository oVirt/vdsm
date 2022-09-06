# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

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
