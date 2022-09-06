# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import os
import tempfile

MODULE_LIST = ('cli', 'hooks', 'services', 'tasks',
               'gfapi', 'storagedev', 'api', 'events',
               'thinstorage')


def gluster_mgmt_api(func):
    func.gluster_mgmt_api = True
    return func


def gluster_api(func):
    func.gluster_api = True
    return func


def listPublicFunctions(gluster_mgmt_enabled=True):
    methods = []
    for modName in MODULE_LIST:
        try:
            module = __import__('vdsm.gluster.' + modName,
                                fromlist=['gluster'])
            for name in dir(module):
                func = getattr(module, name)
                if callable(func) and \
                        _shouldPublish(func, gluster_mgmt_enabled):
                    funcName = 'gluster%s%s' % (name[0].upper(), name[1:])
                    methods.append((funcName, func))
        except ImportError:
            pass
    return methods


def _shouldPublish(func, gluster_mgmt_enabled):
    if gluster_mgmt_enabled:
        return getattr(func, 'gluster_mgmt_api', False)
    else:
        return getattr(func, 'gluster_api', False)


def safeWrite(fileName, content):
    with tempfile.NamedTemporaryFile(dir=os.path.dirname(fileName),
                                     delete=False) as tmp:
        tmp.write(content)
        tmpFileName = tmp.name
        os.rename(tmpFileName, fileName)
