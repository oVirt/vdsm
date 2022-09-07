#!/usr/bin/python3

# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import

import os
import subprocess
import hooking

from vdsm import client
from vdsm.config import config
from vdsm import utils


if hooking.tobool(os.environ.get('sap_agent', False)):
    use_tls = config.getboolean('vars', 'ssl')

    cli = client.connect('localhost', use_tls=use_tls)
    with utils.closing(cli):
        res = cli.Host.getVMFullList()
        if not [v for v in res
                if v.get('vmId') != os.environ.get('vmId')
                and hooking.tobool(
                    v.get('custom', {}).get('sap_agent', False))]:
            subprocess.call(['/usr/bin/sudo', '-n', '/sbin/service', 'vhostmd',
                             'stop'])
