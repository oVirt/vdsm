# Copyright 2016 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#
from __future__ import absolute_import
import os
import sys

from vdsm.config import config

from . import YES, NO
from vdsm import constants
from vdsm.commands import execCmd
from vdsm.common import pki


def validate():
    return _certsExist()


def _exec_vdsm_gencerts():
    rc, out, err = execCmd(
        (
            os.path.join(
                constants.P_VDSM_EXEC,
                'vdsm-gencerts.sh'
            ),
            pki.CA_FILE,
            pki.KEY_FILE,
            pki.CERT_FILE,
        ),
        raw=True,
    )
    sys.stdout.write(out)
    sys.stderr.write(err)
    if rc != 0:
        raise RuntimeError("Failed to perform vdsm-gencerts action.")


def configure():
    _exec_vdsm_gencerts()


def isconfigured():
    return YES if _certsExist() else NO


def _certsExist():
    return not config.getboolean('vars', 'ssl') or\
        os.path.isfile(pki.CERT_FILE)
