#
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

import os
from vdsm.sslutils import SSLContext

CERT_DIR = os.path.abspath(os.path.dirname(__file__))
CRT_FILE = os.path.join(CERT_DIR, "server.crt")
KEY_FILE = os.path.join(CERT_DIR, "server.key")
OTHER_CRT_FILE = os.path.join(CERT_DIR, "other.crt")
OTHER_KEY_FILE = os.path.join(CERT_DIR, "other.key")

DEAFAULT_SSL_CONTEXT = SSLContext(
    CRT_FILE, KEY_FILE, session_id="server-tests")
