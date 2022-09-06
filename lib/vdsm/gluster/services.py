# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

from vdsm.gluster import exception as ge
from vdsm.tool import service

from . import gluster_mgmt_api


SUPPORTED_SERVICES = frozenset(("glusterd",
                                "memcached",
                                "gluster-swift-proxy",
                                "gluster-swift-container",
                                "gluster-swift-object",
                                "gluster-swift-account",
                                "smb",
                                "vdo"))


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


@gluster_mgmt_api
def servicesAction(serviceNames, action):
    """
    Returns:
    {'services': [
        {'name': SERVICE_NAME, 'status': STATUS, 'message': MESSAGE},..]}
    """
    action = action
    return _action(serviceNames, action)


@gluster_mgmt_api
def servicesGet(serviceNames):
    """
    Returns:
    {'services': [
        {'name': SERVICE_NAME, 'status': STATUS, 'message': MESSAGE},..]}
    """
    action = ServiceActions.STATUS
    return _action(serviceNames, action)
