#
# Copyright 2016 Red Hat, Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published
# by the Free Software Foundation; either version 2 of the License, or
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
from __future__ import absolute_import

from collections import namedtuple
import os.path

PROCFS = 'proc'
CGROUPFS = 'sys/fs/cgroup'

_ROOT = '/'
_PROCBASE = os.path.join(_ROOT, PROCFS)
_CGROUPBASE = os.path.join(_ROOT, CGROUPFS)


class Reader(object):

    Stats = None

    def __init__(self, path):
        self._path = path

    @property
    def name(self):
        return self.__class__.__name__.lower()

    def update(self):
        raise NotImplementedError


class Memory(Reader):

    Stats = namedtuple('Stats', ('rss', 'swap'))

    def update(self):
        data = _read_keyvalue(os.path.join(self._path, 'memory.stat'))
        return Memory.Stats(
            rss=int(data['rss']) // 1024.,
            swap=int(data['swap']) // 1024.,
        )


class Cpuacct(Reader):

    Stats = namedtuple('Stats', ('user', 'system'))

    def update(self):
        data = _read_keyvalue(os.path.join(self._path, 'cpuacct.stat'))
        return Cpuacct.Stats(
            user=int(data['user']),
            system=int(data['system']),
        )


_READERS = {
    'memory': Memory,
    'cpuacct': Cpuacct,
}


_READER_ALIASES = {
    'cpu,cpuacct': 'cpuacct',
    'cpuacct,cpu': 'cpuacct',
}


class Monitorable(object):

    def __init__(self, pid):
        self._pid = pid
        self._cgroups = ()
        self._info = {}
        self._readers = {}

    @classmethod
    def from_pid(cls, pid):
        obj = cls(pid)
        obj.setup()
        obj.update()
        return obj

    def setup(self):
        readers = {}
        cgroups = []
        data = _readfile(os.path.join(
            _PROCBASE, str(self._pid), 'cgroup'
        ))
        for line in data.split('\n'):
            if not line:
                continue
            num, name, path = line.strip().split(':')
            if path != '/':
                rname = _READER_ALIASES.get(name, name)
                try:
                    reader = _READERS[rname]
                except KeyError:
                    pass  # we don't support some cgroups
                else:
                    # skip trailing '/' in the cgroup path component,
                    # we don't want it to be interpreted as absolute.
                    inst = reader(os.path.join(_CGROUPBASE, name, path[1:]))
                    readers[rname] = inst
                    cgroups.append(inst.name)
        self._cgroups = tuple(cgroups)
        self._readers = readers

    def update(self):
        self._info = {
            name: inst.update()
            for name, inst in self._readers.items()
        }

    @property
    def cpuacct(self):
        return self._info.get('cpuacct')

    @property
    def memory(self):
        return self._info.get('memory')

    @property
    def blkio(self):
        return self._info.get('blkio')

    @property
    def cgroups(self):
        return self._cgroups

    @property
    def pid(self):
        return self._pid


def _readfile(path):
    with open(path) as src:
        return src.read()


def _read_keyvalue(path, sep=' '):
    res = {}
    with open(path) as src:
        for line in src:
            line = line.strip()
            if not line:
                continue
            key, val = line.split(sep, 1)
            res[key] = val
        return res
