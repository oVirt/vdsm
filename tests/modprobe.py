# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division
import os
from functools import wraps

import pytest

from vdsm.common import cmdutils
from vdsm.common import commands

modprobe = cmdutils.CommandPath("modprobe",
                                "/usr/sbin/modprobe",  # Fedora, EL7
                                )


def RequireDummyMod(f):
    """
    Assumes root privileges to be used after
    ValidateRunningAsRoot decoration.
    """
    return _require_mod(f, 'dummy')


def RequireBondingMod(f):
    """
    Assumes root privileges to be used after
    ValidateRunningAsRoot decoration.
    """
    return _require_mod(f, 'bonding')


def RequireVethMod(f):
    """
    Assumes root privileges to be used after
    ValidateRunningAsRoot decoration.
    """
    return _require_mod(f, 'veth')


def _require_mod(f, name):
    @wraps(f)
    def wrapper(*args, **kwargs):
        _validate_module(name)
        return f(*args, **kwargs)

    return wrapper


def _validate_module(name):
    if not os.path.exists('/sys/module/' + name):
        cmd_modprobe = [modprobe.cmd, name]
        try:
            commands.run(cmd_modprobe, sudo=True)
        except cmdutils.Error as e:
            pytest.skip("This test requires %s module "
                       "(failed to load module: %s)" % (name, e))
