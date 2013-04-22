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
import tempfile
from functools import wraps

MODULE_LIST = ('cli', 'hooks')


def makePublic(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)

    wrapper.superVdsm = True
    return wrapper


def listPublicFunctions():
    methods = []
    for modName in MODULE_LIST:
        try:
            module = __import__('gluster.' + modName, fromlist=['gluster'])
            for name in dir(module):
                func = getattr(module, name)
                if getattr(func, 'superVdsm', False):
                    funcName = 'gluster%s%s' % (name[0].upper(), name[1:])
                    methods.append((funcName, func))
        except ImportError:
            pass
    return methods


def safeWrite(fileName, content):
    with tempfile.NamedTemporaryFile(dir=os.path.dirname(fileName),
                                     delete=False) as tmp:
        tmp.write(content)
        tmpFileName = tmp.name
        os.rename(tmpFileName, fileName)
