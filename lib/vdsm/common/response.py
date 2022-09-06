# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division
from vdsm.common.define import doneCode
from vdsm.common.define import errCode


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


def success_raw(result=None, message=None):
    ret = {
        'status': {
            "code": doneCode["code"],
            "message": message or doneCode["message"],
        }
    }

    if result:
        ret['result'] = result

    return ret


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


def is_error(res, err=None):
    try:
        code = res["status"]["code"]
    except KeyError:
        raise MalformedResponse(res)
    else:
        if err:
            return code == errCode[err]["status"]["code"]
        else:
            return code != doneCode["code"]


def is_valid(res):
    """
    Return True if the argument is a valid response,
    False otherwise. A valid response is produced
    by success() and error() functions, and looks like:
    response = {
      # ...
      status: {
        # ...
        code: INTEGER,
        message: STRING,
      }
    }
    """
    # catching AttributeError is even uglier
    if not isinstance(res, dict):
        return False
    try:
        status = res["status"]
    except KeyError:
        return False
    else:
        return "message" in status and "code" in status
