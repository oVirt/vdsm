#!/usr/bin/env python
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#
from __future__ import absolute_import

import os
import yaml


class SchemaNotFound(Exception):
    pass


class TypeNotFound(Exception):
    pass


class MethodNotFound(Exception):
    pass


def find_schema(schema_name='vdsm-api'):
    """
    Find the API schema file whether we are running from within the source
    dir or from an installed location
    """
    # Don't depend on module VDSM if not looking for schema
    from vdsm import constants

    localpath = os.path.dirname(__file__)
    installedpath = constants.P_VDSM_RPC
    for directory in (localpath, installedpath):
        path = os.path.join(directory, schema_name + '.yml')
        # we use source tree and deployment directory
        # so we need to check whether file exists
        if os.path.exists(path):
            return path

    raise SchemaNotFound("Unable to find API schema file in %s or %s",
                         localpath, installedpath)


class Schema(object):

    def __init__(self, paths):
        self._methods = {}
        self._types = {}
        try:
            for path in paths:
                with open(path) as f:
                    loaded_schema = yaml.load(f)

                types = loaded_schema.pop('types')
                self._types.update(types)
                self._methods.update(loaded_schema)
        except EnvironmentError:
            raise SchemaNotFound("Unable to find API schema file")

    def get_params(self, class_name, method_name):
        return self.get_method(class_name, method_name).get('params', [])

    def get_ret_param(self, class_name, method_name):
        return self.get_method(class_name, method_name).get('return', {})

    def get_method(self, class_name, method_name):
        verb_name = '%s.%s' % (class_name, method_name)
        try:
            return self._methods[verb_name]
        except KeyError:
            raise MethodNotFound(verb_name)

    def get_type(self, type_name):
        try:
            return self._types[type_name]
        except KeyError:
            raise TypeNotFound(type_name)
