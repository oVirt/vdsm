#
# Copyright 2008-2015 Red Hat, Inc.
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
from vdsm.define import doneCode
from vdsm.define import errCode


class MalformedResponse(Exception):

    def __init__(self, response):
        self.response = response

    def __str__(self):
        return "Missing required key in %r" % self.response


def success(message=None, **kwargs):
    kwargs["status"] = {
        "code": doneCode["code"],
        "message": message or doneCode["message"]
    }
    return kwargs


def error(name, message=None):
    status = errCode[name]["status"]
    return {
        "status": {
            "code": status["code"],
            "message": message or status["message"]
        }
    }


def error_raw(code, message):
    return {
        "status": {
            "code": code,
            "message": message
        }
    }


def is_error(res):
    try:
        code = res["status"]["code"]
    except KeyError:
        raise MalformedResponse(res)
    else:
        return code != doneCode["code"]
