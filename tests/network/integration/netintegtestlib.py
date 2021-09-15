#
# Copyright 2019-2020 Red Hat, Inc.
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
