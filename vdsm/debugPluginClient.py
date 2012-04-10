#
# Copyright 2012 Red Hat, Inc.
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

from multiprocessing.managers import BaseManager

ADDRESS = "/var/run/vdsm/debugplugin.sock"


class DebugInterpreterManager(BaseManager):
    pass


def unindent(code):
    """
    Unindent the code so that we can execute text that on the client side
    contains an extra indentation.
    """
    i = 0

    for c in code:
        if c == '\n':
            i = 0
        elif c.isspace():
            i += 1
        else:
            break

    return ''.join([line[i:] for line in code.splitlines(True)])


def execCode(code):
    manager = DebugInterpreterManager(address=ADDRESS, authkey="KEY")
    manager.register('interpreter')
    manager.connect()
    executor = manager.interpreter()
    executor.execute(unindent(code))
