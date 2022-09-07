# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

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
