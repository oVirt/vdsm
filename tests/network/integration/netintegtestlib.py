# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from contextlib import contextmanager

import pytest

from vdsm.network import cmd
from vdsm.network.ipwrapper import netns_add
from vdsm.network.ipwrapper import netns_delete


_SYSTEMCTL = 'systemctl'


def requires_systemctl():
    rc, _, err = cmd.exec_sync([_SYSTEMCTL, 'status', 'foo'])
    run_chroot_err = 'Running in chroot'
    if rc == 1 or run_chroot_err in err:
        pytest.skip('systemctl is not available')


@contextmanager
def network_namespace(name):
    netns_add(name)
    try:
        yield name
    finally:
        netns_delete(name)
