#
# Copyright 2012 Red Hat, Inc.
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

import os
import stat
import errno
import hashlib
import magic
import logging
import exception as ge
from functools import wraps
from . import makePublic

_glusterHooksPath = '/var/lib/glusterd/hooks/1'
_mimeType = None
log = logging.getLogger("Gluster")

class HookLevel:
    PRE = 'PRE'
    POST = 'POST'


class HookStatus:
    S = 'ENABLED'
    ENABLE = 'S'
    K = 'DISABLED'
    DISABLE = 'K'


def _getMimeType(fileName):
    global _mimeType
    if not _mimeType:
        _mimeType = magic.open(magic.MIME_TYPE)
        _mimeType.load()
    return _mimeType.file(fileName)


def _computeMd5Sum(fileName):
    md5 = hashlib.md5()
    with open(fileName, 'rb') as f:
        for pack in iter(lambda: f.read(128 * md5.block_size), b''):
            md5.update(pack)
    return md5.hexdigest()


def checkArgs(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if False in map(bool, args[:3]) or \
                not (kwargs.get('glusterCmd', True) and
                     kwargs.get('hookLevel', True) and
                     kwargs.get('hookName', True)):
            raise ge.GlusterMissingArgumentException(args, kwargs)
        return func(*args, **kwargs)
    return wrapper


@makePublic
def hooksList():
    """
    It scans files which starts from HookStatus.ENABLE or HookStatus.DISABLE
    other files are ignored

    Returns:
        [{'name': HOOK-NAME,
          'status': STATUS,
          'type': MIME_TYPE,
          'command': GLUSTERCOMMAND,
          'level': HOOK-LEVEL,
          'md5sum': MD5SUM}]
    """
    def _getHooks(gCmd, hookLevel):
        hooks = []
        path = os.path.join(_glusterHooksPath, gCmd, hookLevel.lower())
        for hookFile in os.listdir(path):
            status = getattr(HookStatus, hookFile[0], None)
            if status:
                hookPath = os.path.join(path, hookFile)
                hookType = _getMimeType(hookPath)
                if not hookType:
                    hookType = ''
                try:
                    md5sum = _computeMd5Sum(hookPath)
                except IOError:
                    md5sum = ''
                hooks.append({'name': hookFile[1:],
                              'status': status,
                              'type': hookType,
                              'command': gCmd,
                              'level': hookLevel,
                              'md5sum': md5sum})
        return hooks

    hooks = []
    try:
        for gCmd in os.listdir(_glusterHooksPath):
            if not os.path.isdir(os.path.join(_glusterHooksPath, gCmd)):
                continue
            hooks += _getHooks(gCmd, HookLevel.PRE)
            hooks += _getHooks(gCmd, HookLevel.POST)
        return hooks
    except OSError, e:
        errMsg = "[Errno %s] %s: '%s'" % (e.errno, e.strerror, e.filename)
        raise ge.GlusterHookListException(err=[errMsg])


def _getHookFileNames(glusterCmd, hookLevel, hookName):
    enabledFile = os.path.join(_glusterHooksPath, glusterCmd, hookLevel,
                               HookStatus.ENABLE + hookName)
    disabledFile = os.path.join(_glusterHooksPath, glusterCmd, hookLevel,
                                HookStatus.DISABLE + hookName)
    return enabledFile, disabledFile


@checkArgs
@makePublic
def hookEnable(glusterCmd, hookLevel, hookName):
    enabledFile, disabledFile = _getHookFileNames(glusterCmd,
                                                  hookLevel.lower(), hookName)
    if os.path.exists(enabledFile):
        log.warn("Enabled hook file:%s already exists" % enabledFile)
        return
    try:
        os.rename(disabledFile, enabledFile)
        st = os.stat(enabledFile)
        os.chmod(enabledFile, st.st_mode | stat.S_IEXEC)
    except OSError, e:
        if errno.ENOENT == e.errno:
            raise ge.GlusterHookNotFoundException(glusterCmd, hookLevel,
                                                  hookName)
        else:
            errMsg = "[Errno %s] %s: '%s'" % (e.errno, e.strerror, e.filename)
            raise ge.GlusterHookEnableFailedException(err=[errMsg])


@checkArgs
@makePublic
def hookDisable(glusterCmd, hookLevel, hookName):
    enabledFile, disabledFile = _getHookFileNames(glusterCmd,
                                                  hookLevel.lower(), hookName)
    try:
        os.rename(enabledFile, disabledFile)
    except OSError, e:
        if errno.ENOENT == e.errno:
            if os.path.exists(disabledFile):
                log.warn("Disabled hook file:%s already exists" % disabledFile)
            else:
                raise ge.GlusterHookNotFoundException(glusterCmd, hookLevel,
                                                      hookName)
        else:
            errMsg = "[Errno %s] %s: '%s'" % (e.errno, e.strerror, e.filename)
            raise ge.GlusterHookDisableFailedException(err=[errMsg])
