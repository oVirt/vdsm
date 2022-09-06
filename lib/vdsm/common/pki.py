# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import os
from . import constants

PKI_DIR = os.path.join(constants.SYSCONF_PATH, 'pki', 'vdsm')
KEY_FILE = os.path.join(PKI_DIR, 'keys', 'vdsmkey.pem')
CERT_FILE = os.path.join(PKI_DIR, 'certs', 'vdsmcert.pem')
CA_FILE = os.path.join(PKI_DIR, 'certs', 'cacert.pem')
