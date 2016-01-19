#
# Copyright 2012-2016 Red Hat, Inc.
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


class VdsmException(Exception):
    code = 0
    message = "Vdsm Exception"

    def __str__(self):
        return self.message

    def info(self):
        return {'code': self.code, 'message': str(self)}

    def response(self):
        return {'status': self.info()}


class GeneralException(VdsmException):
    code = 100
    message = "General Exception"

    def __init__(self, *value):
        self.value = value

    def __str__(self):
        return "%s: %s" % (self.message, repr(self.value))


class ActionStopped(GeneralException):
    code = 443
    message = "Action was stopped"


class HookError(GeneralException):
    code = 1500
    message = "Hook Error"
