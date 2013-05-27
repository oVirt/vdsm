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

from vdsm.tool import expose
from vdsm.utils import CommandPath
from vdsm.utils import execCmd as _execCmd


def execCmd(argv, raw=True):
    return _execCmd(argv, raw=raw)


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

_srvStartAlts = []
_srvStopAlts = []
_srvStatusAlts = []
_srvRestartAlts = []
_srvDisableAlts = []


class ServiceError(RuntimeError):
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
                return (rc, out, err)
            for line in out:
                if srvName + ".service" == line.split(" ", 1):
                    return systemctlFun(srvName)
            return (1, "", "%s is not native systemctl service")
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
    def _systemctlDisable(srvName):
        cmd = [_SYSTEMCTL.cmd, "disable", srvName]
        return execCmd(cmd)

    _srvStartAlts.append(_systemctlStart)
    _srvStopAlts.append(_systemctlStop)
    _srvStatusAlts.append(_systemctlStatus)
    _srvRestartAlts.append(_systemctlRestart)
    _srvDisableAlts.append(_systemctlDisable)


def _isStopped(message):
    stopRegex = r"\bstopped\b|\bstop\b|\bwaiting\b|\bnot running\b"
    return bool(re.search(stopRegex, message, re.MULTILINE))

try:
    _INITCTL.cmd
except OSError:
    pass
else:
    def _initctlStart(srvName):
        cmd = [_INITCTL.cmd, "start", srvName]
        alreadyRunRegex = r"\bis already running\b"
        rc, out, err = execCmd(cmd)
        if rc != 0:
            # initctl returns an error if the job is already started
            # here we ignore it and return 0 if the job is already running
            rc = int(not re.search(alreadyRunRegex, err, re.MULTILINE))
        return (rc, out, err)

    def _initctlStop(srvName):
        cmd = [_INITCTL.cmd, "stop", srvName]
        alreadyStoppedRegex = r'\bUnknown instance\b'
        rc, out, err = execCmd(cmd)
        if rc != 0:
            # initctl returns an error if the job is already stopped
            # here we ignore it and return 0 if the job is already stopped
            rc = int(not re.search(alreadyStoppedRegex, err, re.MULTILINE))
        return (rc, out, err)

    def _initctlStatus(srvName):
        cmd = [_INITCTL.cmd, "status", srvName]
        rc, out, err = execCmd(cmd)
        if rc == 0:
            # initctl rc is 0 even though the service is stopped
            rc = _isStopped(out)
        return (rc, out, err)

    def _initctlRestart(srvName):
        # "initctl restart someSrv" will not restart the service if it is
        # already running, so we force it to do so
        _initctlStop(srvName)
        return _initctlStart(srvName)

    def _initctlDisable(srvName):
        if not os.path.isfile("/etc/init/%s.conf" % srvName):
            return 1, "", ""
        with open("/etc/init/%s.override" % srvName, "a") as f:
            f.write("manual\n")
        return 0, "", ""

    _srvStartAlts.append(_initctlStart)
    _srvStopAlts.append(_initctlStop)
    _srvStatusAlts.append(_initctlStatus)
    _srvRestartAlts.append(_initctlRestart)
    _srvDisableAlts.append(_initctlDisable)


try:
    _SERVICE.cmd
except OSError:
    pass
else:
    def _serviceStart(srvName):
        cmd = [_SERVICE.cmd, srvName, "start"]
        return execCmd(cmd)

    def _serviceStop(srvName):
        cmd = [_SERVICE.cmd, srvName, "stop"]
        return execCmd(cmd)

    def _serviceStatus(srvName):
        cmd = [_SERVICE.cmd, srvName, "status"]
        rc, out, err = execCmd(cmd)
        if rc == 0:
            # certain service rc is 0 even though the service is stopped
            rc = _isStopped(out)
        return (rc, out, err)

    def _serviceRestart(srvName):
        cmd = [_SERVICE.cmd, srvName, "restart"]
        return execCmd(cmd)

    _srvStartAlts.append(_serviceStart)
    _srvStopAlts.append(_serviceStop)
    _srvRestartAlts.append(_serviceRestart)
    _srvStatusAlts.append(_serviceStatus)


try:
    _CHKCONFIG.cmd
except OSError:
    pass
else:
    def _chkconfigDisable(srvName):
        cmd = [_CHKCONFIG.cmd, srvName, "off"]
        return execCmd(cmd)

    _srvDisableAlts.append(_chkconfigDisable)


try:
    _UPDATERC.cmd
except OSError:
    pass
else:
    def _updatercDisable(srvName):
        cmd = [_UPDATERC.cmd, srvName, "disable"]
        return execCmd(cmd)

    _srvDisableAlts.append(_updatercDisable)


def _runAlts(alts, *args, **kwarg):
    errors = {}
    for alt in alts:
        try:
            rc, out, err = alt(*args, **kwarg)
        except Exception as e:
            errors[alt.func_name] = e
        else:
            if rc == 0:
                return 0
            else:
                errors[alt.func_name] = (rc, out, err)
    raise ServiceError("Tried all alternatives but failed:\n%s" % errors)


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
    Get status of a system service
    """
    return _runAlts(_srvRestartAlts, srvName)


@expose("service-disable")
def service_disable(srvName):
    """
    Disable a system service
    """
    return _runAlts(_srvDisableAlts, srvName)
