#
# Copyright 2017-2019 Red Hat, Inc.
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
        out = commands.run(command)
    except cmdutils.Error as e:
        raise ge.GlusterWebhookAddException(e.rc, out, e.err)
    else:
        return True


@gluster_mgmt_api
def webhookUpdate(url, bearerToken=None):
    command = [_glusterEventsApi.cmd, "webhook-mod", url]
    if bearerToken:
        command.append('--bearer_token=%s' % bearerToken)
    try:
        out = commands.run(command)
    except cmdutils.Error as e:
        raise ge.GlusterWebhookUpdateException(e.rc, out, e.err)
    else:
        return True


@gluster_mgmt_api
def webhookSync():
    command = [_glusterEventsApi.cmd, "sync"]
    try:
        out = commands.run(command)
    except cmdutils.Error as e:
        raise ge.GlusterWebhookSyncException(e.rc, out, e.err)
    else:
        return True


@gluster_mgmt_api
def webhookDelete(url):
    command = [_glusterEventsApi.cmd, "webhook-del", url]
    try:
        out = commands.run(command)
    except cmdutils.Error as e:
        raise ge.GlusterWebhookDeleteException(e.rc, out, e.err)
    else:
        return True
