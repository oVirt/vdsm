# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import os
import stat
import errno
import base64
import hashlib
import magic
import logging
import selinux
from functools import wraps

import vdsm.gluster.exception as ge

from . import gluster_mgmt_api
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


def _computeSha256Sum(fileName):
    csum = hashlib.sha256()
    with open(fileName, 'rb') as f:
        for pack in iter(lambda: f.read(128 * csum.block_size), b''):
            csum.update(pack)
    return csum.hexdigest()


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


@gluster_mgmt_api
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
          'md5sum': EMPTY,
          'checksum': SHA256CHECKSUM}]
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
                    checksum = _computeSha256Sum(hookPath)
                except IOError:
                    checksum = ''
                hooks.append({'name': hookFile[1:],
                              'status': status,
                              'mimetype': hookType,
                              'command': gCmd,
                              'level': hookLevel,
                              'md5sum': '',
                              'checksum': checksum})
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
@gluster_mgmt_api
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
@gluster_mgmt_api
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
@gluster_mgmt_api
def hookRead(glusterCmd, hookLevel, hookName):
    """
    Returns:
        {'content': HOOK_CONTENT,
        'mimetype': MIME_TYPE,
        'md5sum': EMPTY,
        'checksum': SHA256CHECKSUM}
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
        with open(hookFile, 'rb') as f:
            encodedString = base64.b64encode(f.read())
        return {'content': encodedString.decode('utf-8'),
                'mimetype': _getMimeType(hookFile),
                'md5sum': '',
                'checksum': _computeSha256Sum(hookFile)}
    except IOError as e:
        errMsg = "[Errno %s] %s: '%s'" % (e.errno, e.strerror, e.filename)
        raise ge.GlusterHookReadFailedException(err=[errMsg])


def _hookUpdateOrAdd(glusterCmd, hookLevel, hookName, hookData, hookChecksum,
                     update=True, enable=False):
    enabledFile, disabledFile = _getHookFileNames(glusterCmd,
                                                  hookLevel.lower(), hookName)
    hookStat = [os.path.exists(enabledFile), os.path.exists(disabledFile)]
    if update:
        if not any(hookStat):
            raise ge.GlusterHookNotFoundException(glusterCmd, hookLevel,
                                                  hookName)
    else:
        if any(hookStat):
            raise ge.GlusterHookAlreadyExistException(glusterCmd, hookLevel,
                                                      hookName)
    content = base64.b64decode(hookData)
    checksum = hashlib.sha256(content).hexdigest()
    if hookChecksum != checksum:
        raise ge.GlusterHookCheckSumMismatchException(checksum, hookChecksum)

    if enable or hookStat[0]:
        safeWrite(enabledFile, content)
        st = os.stat(enabledFile)
        os.chmod(enabledFile, st.st_mode | stat.S_IEXEC)
    else:
        safeWrite(disabledFile, content)


@checkArgs
@gluster_mgmt_api
def hookUpdate(glusterCmd, hookLevel, hookName, hookData, hookChecksum):
    try:
        return _hookUpdateOrAdd(glusterCmd, hookLevel, hookName, hookData,
                                hookChecksum)
    except IOError as e:
        errMsg = "[Errno %s] %s: '%s'" % (e.errno, e.strerror, e.filename)
        raise ge.GlusterHookUpdateFailedException(err=[errMsg])


@checkArgs
@gluster_mgmt_api
def hookAdd(glusterCmd, hookLevel, hookName, hookData, hookChecksum,
            enable=False):
    hookPath = os.path.join(_glusterHooksPath, glusterCmd, hookLevel.lower())
    try:
        os.makedirs(hookPath)
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
                                hookChecksum, update=False, enable=enable)
    except IOError as e:
        errMsg = "[Errno %s] %s: '%s'" % (e.errno, e.strerror, e.filename)
        raise ge.GlusterHookAddFailedException(err=[errMsg])


@checkArgs
@gluster_mgmt_api
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
