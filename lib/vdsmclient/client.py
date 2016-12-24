#
# Copyright 2016 Red Hat, Inc.
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
"""
vdsm-client - simple Vdsm jsonrpc client

This is a simple generic client that does not know anything about the available
methods and parameters. The user should consult the schema to construct request
that make sense:

    https://github.com/oVirt/vdsm/blob/master/lib/api/vdsm-api.yml

Future version should parse the schema and provide online help.

Invoking simple methods::

    # vdsm-client Host getVMList
    ['b3f6fa00-b315-4ad4-8108-f73da817b5c5']

Invoking methods with simple parameters::

    # vdsm-client VM getStats vmID=b3f6fa00-b315-4ad4-8108-f73da817b5c5
    ...

For invokinng methods with many or complex parameters, you can read the
parameters from a file:

    # vdsm-client StorageDomain activate -f sd-activate
    ...

where file content is::
    {
        "storagedomainID": "75ab40e3-06b1-4a54-a825-2df7a40b93b2",
        "storagepoolID": "b3f6fa00-b315-4ad4-8108-f73da817b5c5"
    }

It is also possible to read parameters from standard input, creating complex
parameters interactively::

    # vdsm-client StorageDomain activate -f -
    {
        "storagedomainID": "75ab40e3-06b1-4a54-a825-2df7a40b93b2",
        "storagepoolID": "b3f6fa00-b315-4ad4-8108-f73da817b5c5"
    }

    (Press Ctrl + D to finish)

"""

from __future__ import absolute_import

import argparse
import json
import os
import sys

from vdsm import client
from vdsm import utils


class UsageError(Exception):
    """ Raised when usage is wrong """


def main(args=None):
    parser = option_parser()
    args = parser.parse_args(args)
    try:
        if args.method_args and args.file is not None:
            raise UsageError("Conflicting command line parameters: %r and "
                             "file option: %r" % (args.method_args, args.file))

        namespace = args.namespace
        method = args.method

        if args.file:
            request_params = parse_file(args.file)
        else:
            request_params = parse_params(args.method_args)

        cli = client.connect(args.host, port=args.port, use_tls=args.use_tls,
                             timeout=args.timeout)

        with utils.closing(cli):
            command = getattr(getattr(cli, namespace), method)
            result = command(**request_params)
            print(json.dumps(result, indent=4))
    except UsageError as e:
        parser.error(str(e))
    except Exception as e:
        fail(e)


def option_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument('-a', '--host', dest="host", default="localhost",
                        help="host address (default localhost)")
    parser.add_argument('-p', '--port', dest="port", default=54321, type=int,
                        help="port (default 54321)")
    parser.add_argument('--unsecure', dest="use_tls", action="store_false",
                        help="unsecured connection")
    parser.set_defaults(use_tls=True)
    parser.add_argument('--timeout', dest="timeout", default=60, type=float,
                        help="timeout (default 60 seconds)")
    parser.add_argument("namespace", help="method namespace")
    parser.add_argument("method", help="remote method name")
    parser.add_argument('-f', '--file', dest="file",
                        help="read method parameters from json file. Set to"
                             " '-' to read from standard input")
    parser.add_argument('method_args', metavar='method_args', type=str,
                        nargs='*',
                        help='method arguments: name1=value1 name2=value2')
    return parser


def parse_params(params):
    """
    Parse ["name=value", ...] to dict {"name": "value", ...}
    """
    d = {}
    for param in params:
        if "=" not in param:
            raise UsageError("Invalid param %r" % param)
        name, value = param.split("=", 1)
        d[name] = value

    return d


def parse_file(filename):
    if filename == "-":
        data = sys.stdin.read()
    else:
        try:
            with open(filename) as f:
                data = f.read()
        except IOError as e:
            raise UsageError(str(e))
    if not data:
        raise UsageError("File is empty")
    try:
        return json.loads(data)
    except (TypeError, ValueError) as e:
        raise UsageError(str(e))


def fail(msg):
    sys.stderr.write("%s: %s\n" % (os.path.basename(sys.argv[0]), msg))
    sys.exit(1)
