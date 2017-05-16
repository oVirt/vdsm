# Copyright 2015-2017 Red Hat, Inc.
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
from vdsm import constants
from vdsm import commands
from vdsm.common import cache
from vdsm.common import cmdutils


from . import YES, NO


_SASLDBLISTUSERS2 = cmdutils.CommandPath("sasldblistusers2",
                                         "/usr/sbin/sasldblistusers2",
                                         )
_LIBVIRT_SASLDB = "/etc/libvirt/passwd.db"
_SASLPASSWD2 = cmdutils.CommandPath("saslpasswd2",
                                    "/usr/sbin/saslpasswd2",
                                    )
SASL_USERNAME = "vdsm@ovirt"
LIBVIRT_PASSWORD_PATH = constants.P_VDSM_KEYS + 'libvirt_password'


def isconfigured():
    script = (str(_SASLDBLISTUSERS2), '-f', _LIBVIRT_SASLDB)
    _, out, _ = commands.execCmd(script)
    for user in out:
        if SASL_USERNAME in user:
            return YES
    return NO


def configure():
    script = (str(_SASLPASSWD2), '-p', '-a', 'libvirt', SASL_USERNAME)
    rc, _, err = commands.execCmd(script, data=libvirt_password())
    if rc != 0:
        raise RuntimeError("Set password failed: %s" % (err,))


def removeConf():
    if isconfigured() == YES:
        rc, out, err = commands.execCmd(
            (
                str(_SASLPASSWD2),
                '-p',
                '-a', 'libvirt',
                '-d', SASL_USERNAME,
            ),
        )
        if rc != 0:
            raise RuntimeError("Remove password failed: %s" % (err,))


@cache.memoized
def libvirt_password():
    with open(LIBVIRT_PASSWORD_PATH) as passwd_file:
        return passwd_file.readline().rstrip("\n")
