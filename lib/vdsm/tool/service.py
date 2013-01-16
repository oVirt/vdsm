# Copyright 2013 IBM, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

'''
System service management utlities.
'''

import os
import functools
import re
import sys
from collections import defaultdict

from vdsm.tool import expose
from vdsm.utils import CommandPath
from vdsm.utils import execCmd as _execCmd


def execCmd(argv, raw=True, *args, **kwargs):
    return _execCmd(argv, raw=raw, *args, **kwargs)


_SYSTEMCTL = CommandPath("systemctl",
                         "/bin/systemctl",
                         "/usr/bin/systemctl",
                         )

_INITCTL = CommandPath("initctl",
                       "/sbin/initctl",
                       )

_SERVICE = CommandPath("service",
                       "/sbin/service",
                       "/usr/sbin/service",
                       )

_CHKCONFIG = CommandPath("chkconfig",
                         "/sbin/chkconfig",
                         )

_UPDATERC = CommandPath("update-rc.d",
                        "/usr/sbin/update-rc.d",
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


class ServiceError(RuntimeError):
    def __init__(self, message, out=None, err=None):
        self.out = out
        self.err = err
        self.message = message

    def __str__(self):
        s = ["%s: %s" % (self.__class__.__name__, self.message)]
        if self.out:
            s.append(self.out)
        if self.err:
            s.append(self.err)
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
            rc, out, err = execCmd(cmd, raw=False)
            if rc != 0:
                raise ServiceOperationError(
                    "Error listing unit files", '\n'.join(out), '\n'.join(err))
            fullName = srvName + ".service"
            for line in out:
                if fullName == line.split(" ", 1)[0]:
                    return systemctlFun(fullName)
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

try:
    _INITCTL.cmd
except OSError:
    pass
else:
    def _initctlNative(initctlFun):
        @functools.wraps(initctlFun)
        def wrapper(srvName):
            cmd = [_INITCTL.cmd, "usage", srvName]
            rc, out, err = execCmd(cmd, raw=False)
            if rc != 0:
                raise ServiceNotExistError("%s is not an Upstart service" %
                                           srvName)

            return initctlFun(srvName)
        return wrapper

    @_initctlNative
    def _initctlStart(srvName):
        cmd = [_INITCTL.cmd, "start", srvName]
        alreadyRunRegex = r"\bis already running\b"
        rc, out, err = execCmd(cmd)
        if rc != 0:
            # initctl returns an error if the job is already started
            # here we ignore it and return 0 if the job is already running
            rc = int(not re.search(alreadyRunRegex, err, re.MULTILINE))
        return (rc, out, err)

    @_initctlNative
    def _initctlStop(srvName):
        cmd = [_INITCTL.cmd, "stop", srvName]
        alreadyStoppedRegex = r'\bUnknown instance\b'
        rc, out, err = execCmd(cmd)
        if rc != 0:
            # initctl returns an error if the job is already stopped
            # here we ignore it and return 0 if the job is already stopped
            rc = int(not re.search(alreadyStoppedRegex, err, re.MULTILINE))
        return (rc, out, err)

    @_initctlNative
    def _initctlStatus(srvName):
        cmd = [_INITCTL.cmd, "status", srvName]
        rc, out, err = execCmd(cmd)
        if rc == 0:
            # initctl rc is 0 even though the service is stopped
            rc = _isStopped(out)
        return (rc, out, err)

    @_initctlNative
    def _initctlRestart(srvName):
        # "initctl restart someSrv" will not restart the service if it is
        # already running, so we force it to do so
        _initctlStop(srvName)
        return _initctlStart(srvName)

    @_initctlNative
    def _initctlReload(srvName):
        cmd = [_INITCTL.cmd, "reload", srvName]
        rc, out, err = execCmd(cmd)
        return (rc, out, err)

    @_initctlNative
    def _initctlDisable(srvName):
        if not os.path.isfile("/etc/init/%s.conf" % srvName):
            return 1, "", ""
        with open("/etc/init/%s.override" % srvName, "a") as f:
            f.write("manual\n")
        return 0, "", ""

    @_initctlNative
    def _initctlIsManaged(srvName):
        return (0, '', '')

    _srvStartAlts.append(_initctlStart)
    _srvStopAlts.append(_initctlStop)
    _srvStatusAlts.append(_initctlStatus)
    _srvRestartAlts.append(_initctlRestart)
    _srvReloadAlts.append(_initctlReload)
    _srvDisableAlts.append(_initctlDisable)
    _srvIsManagedAlts.append(_initctlIsManaged)


def _sysvNative(sysvFun):
    @functools.wraps(sysvFun)
    def wrapper(srvName):
        srvPath = os.path.join(os.sep + 'etc', 'init.d', srvName)
        if os.path.exists(srvPath):
            return sysvFun(srvName)

        raise ServiceNotExistError("%s is not a SysV service" % srvName)
    return wrapper

try:
    _SERVICE.cmd
except OSError:
    pass
else:
    _sysvEnv = {'SYSTEMCTL_SKIP_REDIRECT': '1'}
    _execSysvEnv = functools.partial(execCmd, env=_sysvEnv)

    @_sysvNative
    def _serviceStart(srvName):
        cmd = [_SERVICE.cmd, srvName, "start"]
        return _execSysvEnv(cmd)

    @_sysvNative
    def _serviceStop(srvName):
        cmd = [_SERVICE.cmd, srvName, "stop"]
        return _execSysvEnv(cmd)

    @_sysvNative
    def _serviceStatus(srvName):
        cmd = [_SERVICE.cmd, srvName, "status"]
        rc, out, err = _execSysvEnv(cmd)
        if rc == 0:
            # certain service rc is 0 even though the service is stopped
            rc = _isStopped(out)
        return (rc, out, err)

    @_sysvNative
    def _serviceRestart(srvName):
        cmd = [_SERVICE.cmd, srvName, "restart"]
        return _execSysvEnv(cmd)

    @_sysvNative
    def _serviceReload(srvName):
        cmd = [_SERVICE.cmd, srvName, "reload"]
        return _execSysvEnv(cmd)

    @_sysvNative
    def _serviceIsManaged(srvName):
        return (0, '', '')

    _srvStartAlts.append(_serviceStart)
    _srvStopAlts.append(_serviceStop)
    _srvRestartAlts.append(_serviceRestart)
    _srvReloadAlts.append(_serviceReload)
    _srvStatusAlts.append(_serviceStatus)
    _srvIsManagedAlts.append(_serviceIsManaged)


try:
    _CHKCONFIG.cmd
except OSError:
    pass
else:
    @_sysvNative
    def _chkconfigDisable(srvName):
        cmd = [_CHKCONFIG.cmd, srvName, "off"]
        return execCmd(cmd)

    _srvDisableAlts.append(_chkconfigDisable)


try:
    _UPDATERC.cmd
except OSError:
    pass
else:
    @_sysvNative
    def _updatercDisable(srvName):
        cmd = [_UPDATERC.cmd, srvName, "disable"]
        return execCmd(cmd)

    _srvDisableAlts.append(_updatercDisable)


def _runAlts(alts, srvName, *args, **kwarg):
    errors = defaultdict(list)
    for alt in alts:
        for srv in _srvNameAlts.get(srvName, [srvName]):
            try:
                rc, out, err = alt(srv, *args, **kwarg)
            except ServiceNotExistError as e:
                errors[alt.func_name].append(e)
                continue
            else:
                if rc == 0:
                    return 0
                else:
                    raise ServiceOperationError(
                        "%s failed" % alt.func_name, out, err)
    raise ServiceNotExistError(
        'Tried all alternatives but failed:\n%s' %
        ('\n'.join(str(e) for errs in errors.values() for e in errs)))


@expose("service-start")
def service_start(srvName):
    """
    Start a system service
    """
    return _runAlts(_srvStartAlts, srvName)


@expose("service-stop")
def service_stop(srvName):
    """
    Stop a system service
    """
    return _runAlts(_srvStopAlts, srvName)


@expose("service-status")
def service_status(srvName):
    """
    Get status of a system service
    """
    try:
        return _runAlts(_srvStatusAlts, srvName)
    except ServiceError as e:
        sys.stderr.write('service-status: %s\n' % e)
        return 1


@expose("service-restart")
def service_restart(srvName):
    """
    Restart a system service
    """
    return _runAlts(_srvRestartAlts, srvName)


@expose("service-reload")
def service_reload(srvName):
    """
    Notify a system service to reload configurations
    """
    return _runAlts(_srvReloadAlts, srvName)


@expose("service-disable")
def service_disable(srvName):
    """
    Disable a system service
    """
    return _runAlts(_srvDisableAlts, srvName)


@expose("service-is-managed")
def service_is_managed(srvName):
    """
    Check the existence of a service
    """
    try:
        return _runAlts(_srvIsManagedAlts, srvName)
    except ServiceError as e:
        sys.stderr.write('service-is-managed: %s\n' % e)
        return 1
