#
# Copyright 2013 Red Hat, Inc.
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

import exception as ge
from vdsm.tool import service
from . import makePublic


SUPPORTED_SERVICES = frozenset(("glusterd",
                                "memcached",
                                "gluster-swift-proxy",
                                "gluster-swift-container",
                                "gluster-swift-object",
                                "gluster-swift-account",
                                "smb"))


class StatusTypes:
    RUNNING = 'RUNNING'
    STOPPED = 'STOPPED'
    ERROR = 'ERROR'
    NOT_AVAILABLE = 'NOT_AVAILABLE'
    NOT_SUPPORTED = 'NOT_SUPPORTED'


class StatusBasedOnAction:
    START = 'RUNNING'
    STOP = 'STOPPED'
    RESTART = 'RUNNING'


class ServiceActions:
    START = 'start'
    STOP = 'stop'
    RESTART = 'restart'
    STATUS = 'status'


def _formatStatus(serviceName, status, message=''):
    return {'name': serviceName, 'status': status, 'message': message}


def _serviceStatus(serviceName):
    rc = service.service_status(serviceName)

    if rc == 0:
        return _formatStatus(serviceName, StatusTypes.RUNNING)

    # If rc is not zero, then service may not be available or stopped
    # Check if service is managed, if not managed return the status as
    # NOT_AVAILABLE else return STOPPED
    rc1 = service.service_is_managed(serviceName)
    if rc1 == 0:
        return _formatStatus(serviceName, StatusTypes.STOPPED)
    else:
        return _formatStatus(serviceName, StatusTypes.NOT_AVAILABLE)


def _serviceAction(serviceName, action):
    if action == ServiceActions.STATUS:
        return _serviceStatus(serviceName)

    # lambda to safegaurd if the attr/method is not available
    # which will not happen since supported actions are validated
    # before sending here.
    func = getattr(service, 'service_%s' % action, lambda x: 1)
    try:
        func(serviceName)
        # If Action is successful then return the status without
        # querying the status again
        status = getattr(StatusBasedOnAction, action.upper(), '')
        return _formatStatus(serviceName, status)
    except service.ServiceNotExistError:
        return _formatStatus(serviceName, StatusTypes.NOT_AVAILABLE)
    except service.ServiceOperationError as e:
        return _formatStatus(serviceName, StatusTypes.ERROR, message=e.err)


def _action(serviceNames, action):
    if not getattr(ServiceActions, action.upper(), None):
        raise ge.GlusterServiceActionNotSupportedException(action=action)

    statusOutput = []
    for serviceName in serviceNames:
        if serviceName in SUPPORTED_SERVICES:
            resp = _serviceAction(serviceName, action)
        else:
            resp = _formatStatus(serviceName, StatusTypes.NOT_SUPPORTED)

        statusOutput.append(resp)

    return statusOutput


@makePublic
def servicesAction(serviceNames, action):
    """
    Returns:
    {'services': [
        {'name': SERVICE_NAME, 'status': STATUS, 'message': MESSAGE},..]}
    """
    action = action
    return _action(serviceNames, action)


@makePublic
def servicesGet(serviceNames):
    """
    Returns:
    {'services': [
        {'name': SERVICE_NAME, 'status': STATUS, 'message': MESSAGE},..]}
    """
    action = ServiceActions.STATUS
    return _action(serviceNames, action)
