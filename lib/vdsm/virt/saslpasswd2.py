# Copyright 2019 Red Hat, Inc.
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

import os

from vdsm.common import commands
from vdsm.common import supervdsm

SASL_APP_NAME = 'qemu'
SASL_COMMAND = '/usr/sbin/saslpasswd2'
SASL_PASSWORD_DB = '/etc/sasl2/vnc_passwd.db'


def set_vnc_password(username, passwd):
    if os.geteuid() != 0:
        return supervdsm.getProxy().saslpasswd2_set_vnc_password(username,
                                                                 passwd)

    # Call to Popen.communicate needs string in Python2 and bytes in Python 3
    # string.encode returns string in Python2 and bytes in Python3
    # How convenient!
    commands.run([SASL_COMMAND,
                  "-a", SASL_APP_NAME,
                  "-f", SASL_PASSWORD_DB,
                  "-p",
                  username],
                 input=passwd.encode())


def remove_vnc_password(username):
    if os.geteuid() != 0:
        return supervdsm.getProxy().saslpasswd2_remove_vnc_password(username)

    commands.run([SASL_COMMAND,
                  "-a", SASL_APP_NAME,
                  "-f", SASL_PASSWORD_DB,
                  "-d",
                  username])
