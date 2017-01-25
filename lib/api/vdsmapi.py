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

import logging
import os
import six
import yaml

from vdsm.config import config
from vdsm.logUtils import Suppressed
from yajsonrpc import JsonRpcInvalidParamsError


PRIMITIVE_TYPES = {'boolean': lambda value: isinstance(value, bool),
                   'float': lambda value: isinstance(value, float),
                   'int': lambda value: isinstance(value, int),
                   'long': lambda value: isinstance(value, (six.integer_types,
                                                            float)),
                   'string': lambda value: isinstance(value, six.string_types),
                   'uint': lambda value: isinstance(value, int) and value >= 0}
TYPE_KEYS = list(PRIMITIVE_TYPES.keys())


DEFAULT_VALUES = {'{}': {},
                  '()': (),
                  '[]': []}


_log_devel = logging.getLogger("devel")


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

    log = logging.getLogger("SchemaCache")

    def __init__(self, paths):
        self._strict_mode = config.getboolean('devel', 'api_strict_mode')
        self._methods = {}
        self._types = {}
        try:
            for path in paths:
                with open(path) as f:
                    loaded_schema = yaml.load(f, Loader=yaml.CLoader)

                types = loaded_schema.pop('types')
                self._types.update(types)
                self._methods.update(loaded_schema)
        except EnvironmentError:
            raise SchemaNotFound("Unable to find API schema file")

    def get_args(self, class_name, method_name):
        return self.get_method(class_name, method_name).get('params', [])

    def get_arg_names(self, class_name, method_name):
        return [arg.get('name') for arg in self.get_args(class_name,
                                                         method_name)]

    def get_default_arg_names(self, class_name, method_name):
        return frozenset([arg.get('name') for arg in self.get_args(class_name,
                                                                   method_name)
                          if 'defaultvalue' in arg])

    def get_default_arg_values(self, class_name, method_name):
        return [DEFAULT_VALUES.get(arg.get('defaultvalue'),
                                   arg.get('defaultvalue'))
                for arg in self.get_args(class_name, method_name)
                if 'defaultvalue' in arg]

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

    def _check_primitive_type(self, t, value, name):
        condition = PRIMITIVE_TYPES.get(t)
        if not condition(value):
            self._report_inconsistency('Parameter %s is not %s type'
                                       % (name, t))

    def _report_inconsistency(self, message):
        if self._strict_mode:
            raise JsonRpcInvalidParamsError(message)

    def verify_args(self, class_name, method_name, args):
        try:
            # check whether there are extra parameters
            unknown_args = [key for key in args if key not in
                            self.get_arg_names(class_name, method_name)]
            if unknown_args:
                self._report_inconsistency('Following parameters %s were not'
                                           ' recognized' % (unknown_args))

            # verify types of provided parameters
            for param in self.get_args(class_name, method_name):
                name = param.get('name')
                arg = args.get(name)
                if arg is None:
                    # check if missing paramter was defined as optional
                    if 'defaultvalue' not in param:
                        self._report_inconsistency(
                            'Required parameter %s is not '
                            'provided when calling %s.%s' % (name, class_name,
                                                             method_name))
                    continue
                self._verify_type(param, arg, class_name, method_name)
        except JsonRpcInvalidParamsError:
            raise
        except Exception:
            self._report_inconsistency('Unexpected issue with request type'
                                       ' verification for %s.%s'
                                       % (class_name, method_name))

    def _verify_type(self, param, value, class_name, method_name):
        # check whether a parameter is in a list
        if isinstance(param, list):
            if not isinstance(value, list):
                self._report_inconsistency('Parameter %s is not a list'
                                           % (value))
            for a in value:
                self._verify_type(param[0], a, class_name, method_name)
            return
        # check whether a parameter is defined as primitive type
        elif param in TYPE_KEYS:
            self._check_primitive_type(param, value, param)
            return

        # get type and name
        name = param.get('name')
        t = param.get('type')
        if t == 'dict':
            # it seems that there is no other way to have it fixed
            self._report_inconsistency(
                'Unsupported type %s in %s.%s please fix'
                % (t, class_name, method_name))
        # check whether it is a primitive type
        elif t in TYPE_KEYS:
            self._check_primitive_type(t, value, name)

        else:
            # if type is a string call type verification method
            if isinstance(t, six.string_types):
                self._verify_complex_type(t, param, value, name, class_name,
                                          method_name)
            # if type is in a list we need to get the type and call
            # type verification method
            elif isinstance(t, list):
                if not isinstance(value, list):
                    self._report_inconsistency('Parameter %s is not list'
                                               % (value))
                for a in value:
                    self._verify_type(t[0], a, class_name, method_name)
            else:
                # call complex type verification
                self._verify_complex_type(t.get('type'), t, value, name,
                                          class_name, method_name)

    def _verify_complex_type(self, t_type, t, arg, name, class_name,
                             method_name):
        """
        This method verify whether argument value align with different
        types we support such as: alias, map, union, enum and object.
        """
        if t_type == 'alias':
            # if alias we need to check sourcetype
            self._check_primitive_type(t.get('sourcetype'), arg, name)
        elif t_type == 'map':
            # if map we need to check key and value types
            for key, value in six.iteritems(arg):
                self._verify_type(t.get('key-type'), key, class_name,
                                  method_name)
                self._verify_type(t.get('value-type'), value, class_name,
                                  method_name)
        elif t_type == 'union':
            # if union we need to check whether parameter matches on of the
            # values defined
            for value in t.get('values'):
                props = value.get('properties')
                prop_names = [prop.get('name') for prop in props]
                if not [key for key in arg if key not in prop_names]:
                    self._verify_complex_type(value.get('type'), value, arg,
                                              name, class_name, method_name)
                    return
            self._report_inconsistency('Provided parameters %s do not match'
                                       ' any of union %s values'
                                       % (arg, t.get('name')))
        elif t_type == 'enum':
            # if enum we need to check whether provided parameter is in values
            if arg not in t.get('values'):
                self._report_inconsistency('Provided value "%s" not'
                                           ' defined in %s enum for'
                                           ' %s.%s' % (arg,
                                                       t.get('name'),
                                                       class_name,
                                                       method_name))
        else:
            # if custom time (object) we need to check whether all the
            # properties match values provided
            self._verify_object_type(t, arg, class_name, method_name)

    def _verify_object_type(self, t, arg, class_name, method_name):
        props = t.get('properties')
        prop_names = [prop.get('name') for prop in props]
        # check if there are any extra prarameters
        unknown_props = [key for key in arg
                         if key not in prop_names]
        if unknown_props:
            self._report_inconsistency('Following parameters %s were not'
                                       ' recognized' % (unknown_props))
        # iterate over properties
        for prop in props:
            p_name = prop.get('name')
            a = arg.get(p_name)

            # check whether parameter is defined as optional and
            # check default type
            if 'defaultvalue' in prop:
                value = prop.get('defaultvalue')
                if value == 'needs updating':
                    self._report_inconsistency(
                        'No default value specified for %s parameter in'
                        ' %s.%s' % (p_name, class_name, method_name))
                if value == 'no-default':
                    continue
                if a is None or a == value:
                    continue
            else:
                if a is None:
                    self._report_inconsistency(
                        'Required property %s is not provided when calling'
                        ' %s.%s' % (p_name, class_name, method_name))
                    continue
            # call type verification
            self._verify_type(prop, a, class_name, method_name)

    def verify_retval(self, class_name, method_name, ret):
        try:
            ret_args = self.get_ret_param(class_name, method_name)

            if ret_args:
                if isinstance(ret, Suppressed):
                    ret = ret.value
                self._verify_type(ret_args.get('type'), ret, class_name,
                                  method_name)
        except JsonRpcInvalidParamsError:
            raise
        except Exception:
            self._report_inconsistency('Unexpected issue with response type'
                                       ' verification for %s.%s'
                                       % (class_name, method_name))
