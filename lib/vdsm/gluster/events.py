# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

from vdsm.common import cmdutils
from vdsm.common import commands

from . import exception as ge
from . import gluster_mgmt_api


_glusterEventsApi = cmdutils.CommandPath("gluster-eventsapi",
                                         "/sbin/gluster-eventsapi",
                                         "/usr/sbin/gluster-eventsapi",)


@gluster_mgmt_api
def webhookAdd(url, bearerToken=None):
    command = [_glusterEventsApi.cmd, "webhook-add", url]
    if bearerToken:
        command.append('--bearer_token=%s' % bearerToken)
    try:
        commands.run(command)
    except cmdutils.Error as e:
        raise ge.GlusterWebhookAddException(rc=e.rc, err=e.err)
    return True


@gluster_mgmt_api
def webhookUpdate(url, bearerToken=None):
    command = [_glusterEventsApi.cmd, "webhook-mod", url]
    if bearerToken:
        command.append('--bearer_token=%s' % bearerToken)
    try:
        commands.run(command)
    except cmdutils.Error as e:
        raise ge.GlusterWebhookUpdateException(rc=e.rc, err=e.err)
    return True


@gluster_mgmt_api
def webhookSync():
    command = [_glusterEventsApi.cmd, "sync"]
    try:
        commands.run(command)
    except cmdutils.Error as e:
        raise ge.GlusterWebhookSyncException(rc=e.rc, err=e.err)
    return True


@gluster_mgmt_api
def webhookDelete(url):
    command = [_glusterEventsApi.cmd, "webhook-del", url]
    try:
        commands.run(command)
    except cmdutils.Error as e:
        raise ge.GlusterWebhookDeleteException(rc=e.rc, err=e.err)
    return True
