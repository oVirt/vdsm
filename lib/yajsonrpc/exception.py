# Copyright 2017 Red Hat, Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public
# License along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA
from __future__ import absolute_import
from __future__ import division

from vdsm.common import exception


class JsonRpcErrorBase(exception.ContextException):
    """ Base class for JSON RPC errors """


class JsonRpcParseError(JsonRpcErrorBase):
    code = -32700
    msg = ("Invalid JSON was received by the server. "
           "An error occurred on the server while parsing "
           "the JSON text.")


class JsonRpcInvalidRequestError(JsonRpcErrorBase):
    code = -32600
    msg = "Invalid request"


class JsonRpcMethodNotFoundError(JsonRpcErrorBase):
    code = -32601
    msg = ("The method does not exist or is not "
           "available")


class JsonRpcInvalidParamsError(JsonRpcErrorBase):
    code = -32602
    msg = "Invalid method parameter(s)"


class JsonRpcInternalError(JsonRpcErrorBase):
    code = -32603
    msg = "Internal JSON-RPC error"


class JsonRpcBindingsError(JsonRpcErrorBase):
    code = -32604
    msg = "Missing bindings for JSON-RPC."


class JsonRpcNoResponseError(JsonRpcErrorBase):
    code = -32605
    msg = "No response for JSON-RPC request"


class JsonRpcServerError(JsonRpcErrorBase):
    """
    Legacy API methods return an error code instead of raising an exception.
    This class is used to wrap the returned code and message.

    It is also used on the client side, when an error is returned
    in JsonRpcResponse.
    """

    def __init__(self, code, message):
        self.code = code
        self.msg = message

    @classmethod
    def from_dict(cls, d):
        return cls(d["code"], d["message"])
