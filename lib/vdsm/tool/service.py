# SPDX-FileCopyrightText: 2013 IBM, Inc.
# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division
'''
System service management utlities.
'''

import functools
import re
import sys
from collections import defaultdict

from vdsm.common.cmdutils import CommandPath
from vdsm.common.commands import execCmd as _execCmd
from . import expose, UsageError, ExtraArgsError


def execCmd(argv, raw=True, *args, **kwargs):
    return _execCmd(argv, raw=raw, *args, **kwargs)


_SYSTEMCTL = CommandPath("systemctl",
                         "/bin/systemctl",
                         "/usr/bin/systemctl",
                         )

_srvNameAlts = {
    'iscsid': ['iscsid', 'open-iscsi'],
    'libvirtd': ['libvirtd', 'libvirt-bin'],
    'multipathd': ['multipathd', 'multipath-tools'],
    'network': ['network', 'networking'],
    'smb': ['smb', 'samba']
}

_srvStartAlts = []
_srvStopAlts = []
_srvStatusAlts = []
_srvRestartAlts = []
_srvReloadAlts = []
_srvDisableAlts = []
_srvIsManagedAlts = []


class ServiceError(UsageError):
    def __init__(self, message, out=None, err=None):
        self.out = out
        self.err = err
        self.msg = message

    def __str__(self):
        s = ["%s: %s" % (self.__class__.__name__, self.msg)]
        if self.out:
            s.append(str(self.out))
        if self.err:
            s.append(str(self.err))
        return '\n'.join(s)


class ServiceNotExistError(ServiceError):
    pass


class ServiceOperationError(ServiceError):
    pass


try:
    _SYSTEMCTL.cmd
except OSError:
    pass
else:
    def _systemctlNative(systemctlFun):
        @functools.wraps(systemctlFun)
        def wrapper(srvName):
            cmd = [_SYSTEMCTL.cmd, "--no-pager", "list-unit-files"]
            rc, out, err = execCmd(cmd)
            if rc != 0:
                raise ServiceOperationError(
                    "Error listing unit files", out, err)
            fullName = srvName.encode('utf-8')
            # If unit file type was specified, don't override it.
            if srvName.count('.') < 1:
                fullName += b".service"
            for line in out.splitlines():
                if fullName == line.split(b" ", 1)[0]:
                    return systemctlFun(fullName.decode('utf-8'))
            raise ServiceNotExistError("%s is not native systemctl service" %
                                       srvName)
        return wrapper

    @_systemctlNative
    def _systemctlStart(srvName):
        cmd = [_SYSTEMCTL.cmd, "start", srvName]
        return execCmd(cmd)

    @_systemctlNative
    def _systemctlStop(srvName):
        cmd = [_SYSTEMCTL.cmd, "stop", srvName]
        return execCmd(cmd)

    @_systemctlNative
    def _systemctlStatus(srvName):
        cmd = [_SYSTEMCTL.cmd, "status", srvName]
        return execCmd(cmd)

    @_systemctlNative
    def _systemctlRestart(srvName):
        cmd = [_SYSTEMCTL.cmd, "restart", srvName]
        return execCmd(cmd)

    @_systemctlNative
    def _systemctlReload(srvName):
        cmd = [_SYSTEMCTL.cmd, "reload", srvName]
        return execCmd(cmd)

    @_systemctlNative
    def _systemctlDisable(srvName):
        cmd = [_SYSTEMCTL.cmd, "disable", srvName]
        return execCmd(cmd)

    @_systemctlNative
    def _systemctlIsManaged(srvName):
        return (0, '', '')

    _srvStartAlts.append(_systemctlStart)
    _srvStopAlts.append(_systemctlStop)
    _srvStatusAlts.append(_systemctlStatus)
    _srvRestartAlts.append(_systemctlRestart)
    _srvReloadAlts.append(_systemctlReload)
    _srvDisableAlts.append(_systemctlDisable)
    _srvIsManagedAlts.append(_systemctlIsManaged)


def _isStopped(message):
    stopRegex = r"\bstopped\b|\bstop\b|\bwaiting\b|\bnot running\b"
    return bool(re.search(stopRegex, message, re.MULTILINE))


def _runAlts(alts, srvName, *args, **kwarg):
    errors = defaultdict(list)
    for alt in alts:
        for srv in _srvNameAlts.get(srvName, [srvName]):
            try:
                rc, out, err = alt(srv, *args, **kwarg)
            except ServiceNotExistError as e:
                errors[alt.__name__].append(e)
                continue
            else:
                if rc == 0:
                    return 0
                else:
                    raise ServiceOperationError(
                        "%s failed" % alt.__name__, out, err)
    raise ServiceNotExistError(
        'Tried all alternatives but failed:\n%s' %
        ('\n'.join(str(err) for errs in errors.values() for err in errs)))


@expose("service-start")
def service_start_command(cmdName, *args):
    """
    service-start service-name
    Start a system service.

    Parameters:
    service-start - service to start
    """
    if len(args) != 1:
        raise ExtraArgsError(1)
    return service_start(args[0])


def service_start(srvName):
    return _runAlts(_srvStartAlts, srvName)


@expose("service-stop")
def service_stop_command(cmdName, *args):
    """
    service-stop service-name
    Stop a system service.

    Parameters:
    service-name - service to stop
    """
    if len(args) != 1:
        raise ExtraArgsError(1)
    return service_stop(args[0])


def service_stop(srvName):
    return _runAlts(_srvStopAlts, srvName)


@expose("service-status")
def service_status_command(cmdName, *args):
    """
    service-status service-name
    Get status of a system service.

    Parameters:
    service-name - service to query
    """
    if len(args) != 1:
        raise ExtraArgsError(1)
    return service_status(args[0])


def service_status(srvName, verbose=True):
    try:
        return _runAlts(_srvStatusAlts, srvName)
    except ServiceError as e:
        if verbose:
            sys.stderr.write('service-status: %s\n' % e)
        return 1


@expose("service-restart")
def service_restart_command(cmdName, *args):
    """
    service-restart service-name
    Restart a system service.

    Parameters:
    service-name - service to restart
    """
    if len(args) != 1:
        raise ExtraArgsError(1)
    return service_restart(args[0])


def service_restart(srvName):
    return _runAlts(_srvRestartAlts, srvName)


@expose("service-reload")
def service_reload_command(cmdName, *args):
    """
    service-reload service-name
    Notify a system service to reload configurations.

    Parameters:
    service-name - service to notify
    """
    if len(args) != 1:
        raise ExtraArgsError(1)
    return service_reload(args[0])


def service_reload(srvName):
    return _runAlts(_srvReloadAlts, srvName)


@expose("service-disable")
def service_disable_command(cmdName, *args):
    """
    service-disable service-name
    Disable a system service.

    Parameters:
    service-name - service to disable
    """
    if len(args) != 1:
        raise ExtraArgsError(1)
    return service_disable(args[0])


def service_disable(srvName):
    return _runAlts(_srvDisableAlts, srvName)


@expose("service-is-managed")
def service_is_managed_command(cmdName, *args):
    """
    service-is-managed service-name
    Check the existence of a service.

    Parameters:
    service-name - service to query
    """
    if len(args) != 1:
        raise ExtraArgsError(1)
    return service_is_managed(args[0])


def service_is_managed(srvName):
    try:
        return _runAlts(_srvIsManagedAlts, srvName)
    except ServiceError as e:
        sys.stderr.write('service-is-managed: %s\n' % e)
        return 1
