# Copyright 2014 Red Hat, Inc.
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
import os
import sys

from vdsm.config import config

from . import \
    CONFIGURED, \
    ModuleConfigure, \
    NOT_CONFIGURED
from .. import validate_ovirt_certs
from ...constants import \
    P_VDSM_EXEC, \
    SYSCONF_PATH
from ...utils import \
    execCmd, \
    isOvirtNode

PKI_DIR = os.path.join(SYSCONF_PATH, 'pki/vdsm')
CA_FILE = os.path.join(PKI_DIR, 'certs/cacert.pem')
CERT_FILE = os.path.join(PKI_DIR, 'certs/vdsmcert.pem')
KEY_FILE = os.path.join(PKI_DIR, 'keys/vdsmkey.pem')


class Certificates(ModuleConfigure):
    """
    Responsible for rolling out self signed certificates if vdsm's
    configuration is ssl_enabled and no certificates exist.
    """
    # Todo: validate_ovirt_certs.py
    def getName(self):
        return 'certificates'

    def validate(self):
        return self._certsExist()

    def _exec_vdsm_gencerts(self):
        rc, out, err = execCmd(
            (
                os.path.join(
                    P_VDSM_EXEC,
                    'vdsm-gencerts.sh'
                ),
                CA_FILE,
                KEY_FILE,
                CERT_FILE,
            ),
            raw=True,
        )
        sys.stdout.write(out)
        sys.stderr.write(err)
        if rc != 0:
            raise RuntimeError("Failed to perform vdsm-gencerts action.")

    def configure(self):
        self._exec_vdsm_gencerts()
        if isOvirtNode():
            validate_ovirt_certs.validate_ovirt_certs()

    def isconfigured(self):
        return CONFIGURED if self._certsExist() else NOT_CONFIGURED

    def _certsExist(self):
        config.read(
            os.path.join(
                SYSCONF_PATH,
                'vdsm/vdsm.conf'
            )
        )
        return not config.getboolean('vars', 'ssl') or\
            os.path.isfile(CERT_FILE)
