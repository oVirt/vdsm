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
import base64
import hashlib
import magic
import logging
import selinux
import exception as ge
from functools import wraps
from . import makePublic
from . import safeWrite

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
        if hasattr(magic, "MIME_TYPE"):
            _mimeType = magic.open(magic.MIME_TYPE)
        else:
            _mimeType = magic.open(magic.MAGIC_NONE)
            _mimeType.setflags(magic.MAGIC_MIME)
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
          'mimetype': MIME_TYPE,
          'command': GLUSTERCOMMAND,
          'level': HOOK-LEVEL,
          'md5sum': MD5SUM}]
    """
    def _getHooks(gCmd, hookLevel):
        hooks = []
        path = os.path.join(_glusterHooksPath, gCmd, hookLevel.lower())
        if not os.path.isdir(path):
            return hooks
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
                              'mimetype': hookType,
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
    except OSError as e:
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
    except OSError as e:
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
    except OSError as e:
        if errno.ENOENT == e.errno:
            if os.path.exists(disabledFile):
                log.warn("Disabled hook file:%s already exists" % disabledFile)
            else:
                raise ge.GlusterHookNotFoundException(glusterCmd, hookLevel,
                                                      hookName)
        else:
            errMsg = "[Errno %s] %s: '%s'" % (e.errno, e.strerror, e.filename)
            raise ge.GlusterHookDisableFailedException(err=[errMsg])


@checkArgs
@makePublic
def hookRead(glusterCmd, hookLevel, hookName):
    """
    Returns:
        {'content': HOOK_CONTENT,
        'mimetype': MIME_TYPE,
        'md5sum': MD5SUM}
    """
    enabledFile, disabledFile = _getHookFileNames(glusterCmd,
                                                  hookLevel.lower(), hookName)
    if os.path.exists(enabledFile):
        hookFile = enabledFile
    elif os.path.exists(disabledFile):
        hookFile = disabledFile
    else:
        raise ge.GlusterHookNotFoundException(glusterCmd, hookLevel, hookName)
    try:
        with open(hookFile, 'r') as f:
            encodedString = base64.b64encode(f.read())
        return {'content': encodedString,
                'mimetype': _getMimeType(hookFile),
                'md5sum': _computeMd5Sum(hookFile)}
    except IOError as e:
        errMsg = "[Errno %s] %s: '%s'" % (e.errno, e.strerror, e.filename)
        raise ge.GlusterHookReadException(err=[errMsg])


def _hookUpdateOrAdd(glusterCmd, hookLevel, hookName, hookData, hookMd5Sum,
                     update=True, enable=False):
    enabledFile, disabledFile = _getHookFileNames(glusterCmd,
                                                  hookLevel.lower(), hookName)
    hookStat = [os.path.exists(enabledFile), os.path.exists(disabledFile)]
    if update:
        if not True in hookStat:
            raise ge.GlusterHookNotFoundException(glusterCmd, hookLevel,
                                                  hookName)
    else:
        if True in hookStat:
            raise ge.GlusterHookAlreadyExistException(glusterCmd, hookLevel,
                                                      hookName)
    content = base64.b64decode(hookData)
    md5Sum = hashlib.md5(content).hexdigest()
    if hookMd5Sum != md5Sum:
        raise ge.GlusterHookCheckSumMismatchException(md5Sum, hookMd5Sum)

    if enable or hookStat[0]:
        safeWrite(enabledFile, content)
        st = os.stat(enabledFile)
        os.chmod(enabledFile, st.st_mode | stat.S_IEXEC)
    else:
        safeWrite(disabledFile, content)


@checkArgs
@makePublic
def hookUpdate(glusterCmd, hookLevel, hookName, hookData, hookMd5Sum):
    try:
        return _hookUpdateOrAdd(glusterCmd, hookLevel, hookName, hookData,
                                hookMd5Sum)
    except IOError as e:
        errMsg = "[Errno %s] %s: '%s'" % (e.errno, e.strerror, e.filename)
        raise ge.GlusterHookUpdateFailedException(err=[errMsg])


@checkArgs
@makePublic
def hookAdd(glusterCmd, hookLevel, hookName, hookData, hookMd5Sum,
            enable=False):
    hookPath = os.path.join(_glusterHooksPath, glusterCmd, hookLevel.lower())
    try:
        os.makedirs(hookPath)
        if selinux.is_selinux_enabled():
            try:
                selinux.restorecon(hookPath, recursive=True)
            except OSError:
                logging.error('restorecon %s failed', hookPath, exc_info=True)
    except OSError as e:
        if e.errno != errno.EEXIST:
            errMsg = "[Errno %s] %s: '%s'" % (e.errno, e.strerror, e.filename)
            raise ge.GlusterHookAddFailedException(err=[errMsg])

    try:
        return _hookUpdateOrAdd(glusterCmd, hookLevel, hookName, hookData,
                                hookMd5Sum, update=False, enable=enable)
    except IOError as e:
        errMsg = "[Errno %s] %s: '%s'" % (e.errno, e.strerror, e.filename)
        raise ge.GlusterHookAddFailedException(err=[errMsg])


@checkArgs
@makePublic
def hookRemove(glusterCmd, hookLevel, hookName):
    enabledFile, disabledFile = _getHookFileNames(glusterCmd,
                                                  hookLevel.lower(),
                                                  hookName)
    try:
        os.remove(enabledFile)
    except OSError as e:
        if errno.ENOENT != e.errno:
            errMsg = "[Errno %s] %s: '%s'" % (e.errno, e.strerror, e.filename)
            raise ge.GlusterHookRemoveFailedException(err=[errMsg])
    try:
        os.remove(disabledFile)
    except OSError as e:
        if errno.ENOENT != e.errno:
            errMsg = "[Errno %s] %s: '%s'" % (e.errno, e.strerror, e.filename)
            raise ge.GlusterHookRemoveFailedException(err=[errMsg])
