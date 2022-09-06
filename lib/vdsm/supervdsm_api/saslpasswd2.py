# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import

from vdsm.virt import saslpasswd2
from . import expose


@expose
def saslpasswd2_set_vnc_password(username, passwd):
    return saslpasswd2.set_vnc_password(username, passwd)


@expose
def saslpasswd2_remove_vnc_password(username):
    return saslpasswd2.remove_vnc_password(username)
