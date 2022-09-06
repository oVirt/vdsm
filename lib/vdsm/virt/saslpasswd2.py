# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

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
