#
# Copyright 2017-2018 Red Hat, Inc.
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

from __future__ import absolute_import
from __future__ import division

from vdsm.common import systemd


def test_wrap_defaults():
    cmd = systemd.wrap(['a', 'b'])
    res = [systemd.SYSTEMD_RUN, 'a', 'b']
    assert cmd == res


def test_scope():
    cmd = systemd.wrap(['a', 'b'], scope=True)
    res = [systemd.SYSTEMD_RUN, '--scope', 'a', 'b']
    assert cmd == res


def test_unit():
    cmd = systemd.wrap(['a', 'b'], unit='unit')
    res = [systemd.SYSTEMD_RUN, '--unit=unit', 'a', 'b']
    assert cmd == res


def test_slice():
    cmd = systemd.wrap(['a', 'b'], slice='slice')
    res = [systemd.SYSTEMD_RUN, '--slice=slice', 'a', 'b']
    assert cmd == res


def test_uid_gid():
    cmd = systemd.wrap(['a', 'b'], uid=36, gid=36)
    res = [systemd.SYSTEMD_RUN, '--uid=36', '--gid=36', 'a', 'b']
    assert cmd == res


def test_accounting():
    accounting = (
        systemd.Accounting.CPU,
        systemd.Accounting.Memory,
        systemd.Accounting.BlockIO,
    )
    cmd = systemd.wrap(['a', 'b'], accounting=accounting)
    res = [
        systemd.SYSTEMD_RUN,
        '--property=CPUAccounting=1',
        '--property=MemoryAccounting=1',
        '--property=BlockIOAccounting=1',
        'a',
        'b',
    ]
    assert cmd == res
