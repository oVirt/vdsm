# Copyright 2012 IBM, Inc.
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

import errno
import subprocess
from .. import constants
from . import expose


@expose("set-saslpasswd")
def set_saslpasswd():
    """
    Set vdsm password for libvirt connection
    """
    script = ['/usr/sbin/saslpasswd2', '-p', '-a', 'libvirt',
              constants.SASL_USERNAME]

    try:
        f = open(constants.P_VDSM_LIBVIRT_PASSWD, 'r')
    except IOError as e:
        if e.errno == errno.ENOENT:
            return
        raise

    p = subprocess.Popen(
        script, stdin=f, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, close_fds=True)
    output, err = p.communicate()
    if p.returncode != 0:
        raise RuntimeError("Set password failed: %s" % (err,))
