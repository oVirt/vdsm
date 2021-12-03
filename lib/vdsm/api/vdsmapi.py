#
# Copyright 2016-2021 Red Hat, Inc.
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
from __future__ import division

import io
import json
import logging
import os
import pickle
import six

from enum import Enum

from vdsm import utils
from vdsm.common.password import HiddenValue
from yajsonrpc.exception import JsonRpcInvalidParamsError


PRIMITIVE_TYPES = {'boolean': lambda value: isinstance(value, bool),
                   'float': lambda value: isinstance(value, float),
                   'int': lambda value: isinstance(value, int),
                   'long': lambda value: isinstance(value, (six.integer_types,
                                                            float)),
                   'ulong': lambda value: isinstance(value,
                                                     (six.integer_types,
                                                      float)) and value >= 0,
                   'string': lambda value: isinstance(value, six.string_types),
                   'uint': lambda value: isinstance(value, int) and value >= 0}
TYPE_KEYS = list(PRIMITIVE_TYPES.keys())


DEFAULT_VALUES = {'{}': {},
                  '()': (),
                  '[]': []}


_log_inconsistency = logging.getLogger("schema.inconsistency").debug


class SchemaNotFound(Exception):
    pass


class TypeNotFound(Exception):
    pass


class MethodNotFound(Exception):
    pass


class SchemaType(Enum):
    VDSM_API = "vdsm-api"
    VDSM_API_GLUSTER = "vdsm-api-gluster"
    VDSM_EVENTS = "vdsm-events"

    @staticmethod
    def schema_dirs():
        """
        Schema dir can be one of: source dir, if we're running directly
        within the source tree, or the directory in which they can be
        found after installation.
        """
        local_path = os.path.dirname(__file__)
        installed_path = os.path.join(local_path, '..', 'rpc')
        return (local_path, installed_path)

    def path(self):
        filename = self.value + ".pickle"
        potential_paths = [os.path.join(dir_path, filename)
                           for dir_path in SchemaType.schema_dirs()]

        for path in potential_paths:
            if os.path.exists(path):
                return path

        raise SchemaNotFound("Unable to find API schema file, tried: %s" %
                             ", ".join(potential_paths))


class MethodRep(object):

    def __init__(self, class_name, method_name):
        self._id = '%s.%s' % (class_name, method_name)
        self._class_name = class_name

    @property
    def id(self):
        return self._id

    @property
    def object_name(self):
        return self._class_name


class EventRep(object):
    def __init__(self, sub_id):
        self._id = self._trim_subscription_id(sub_id)

    def _trim_subscription_id(self, sub_id):
        idx = len(sub_id) - sub_id.rfind('|')
        return sub_id[:1 - idx]

    @property
    def id(self):
        return self._id


class Schema(object):

    log = logging.getLogger("SchemaCache")

    def __init__(self, schema_types, strict_mode):
        """
        Constructs schema object based on an iterable of schema type
        enumerations and a mode which determines request/response
        validation behavior. Usually it is based on api_strict_mode
        property from config.py
        """
        self._strict_mode = strict_mode
        self._methods = {}
        self._types = {}
        try:
            for schema_type in schema_types:
                with io.open(schema_type.path(), 'rb') as f:
                    loaded_schema = pickle.loads(f.read())

                types = loaded_schema.pop('types')
                self._types.update(types)
                self._methods.update(loaded_schema)
        except EnvironmentError:
            raise SchemaNotFound("Unable to find API schema file")

    @staticmethod
    def vdsm_api(strict_mode, *args, **kwargs):
        schema_types = {SchemaType.VDSM_API}
        if kwargs.pop('with_gluster', False):
            schema_types.add(SchemaType.VDSM_API_GLUSTER)
        return Schema(schema_types, strict_mode, *args, **kwargs)

    @staticmethod
    def vdsm_events(strict_mode, *args, **kwargs):
        return Schema((SchemaType.VDSM_EVENTS,), strict_mode, *args, **kwargs)

    def get_args(self, rep):
        method = self.get_method(rep)
        return method.get('params', [])

    def get_arg_names(self, rep):
        return [arg.get('name') for arg in self.get_args(rep)]

    def get_default_arg_names(self, rep):
        return frozenset([arg.get('name') for arg in self.get_args(rep)
                          if 'defaultvalue' in arg])

    def get_default_arg_values(self, rep):
        return [DEFAULT_VALUES.get(arg.get('defaultvalue'),
                                   arg.get('defaultvalue'))
                for arg in self.get_args(rep)
                if 'defaultvalue' in arg]

    def get_ret_param(self, rep):
        retval = self.get_method(rep)
        return retval.get('return', {})

    def get_method(self, rep):
        try:
            return self._methods[rep.id]
        except KeyError:
            raise MethodNotFound(rep.id)

    @property
    def get_methods(self):
        return utils.picklecopy(self._methods)

    def get_method_description(self, rep):
        method = self.get_method(rep)
        return method.get('description', '')

    def get_type(self, type_name):
        try:
            return self._types[type_name]
        except KeyError:
            raise TypeNotFound(type_name)

    @property
    def get_types(self):
        return utils.picklecopy(self._types)

    def _check_primitive_type(self, t, value, name):
        condition = PRIMITIVE_TYPES.get(t)
        if not condition(value):
            self._report_inconsistency('Parameter %s is not %s type'
                                       % (name, t))

    def _report_inconsistency(self, message):
        if self._strict_mode:
            raise JsonRpcInvalidParamsError(message)
        else:
            _log_inconsistency('%s', message)

    def verify_args(self, rep, args):
        try:
            # check whether there are extra parameters
            unknown_args = [key for key in args if key not in
                            self.get_arg_names(rep)]
            if unknown_args:
                self._report_inconsistency('Following parameters %s were not'
                                           ' recognized' % (unknown_args))

            # verify types of provided parameters
            for param in self.get_args(rep):
                name = param.get('name')
                arg = args.get(name)
                if arg is None:
                    # check if missing paramter was defined as optional
                    if 'defaultvalue' not in param:
                        self._report_inconsistency(
                            'Required parameter %s is not '
                            'provided when calling %s' % (name, rep.id))
                    continue
                self._verify_type(param, arg, rep.id)
        except JsonRpcInvalidParamsError:
            raise
        except Exception:
            self._report_inconsistency('Unexpected issue with request type'
                                       ' verification for %s' % rep.id)

    def _verify_type(self, param, value, identifier):
        # check whether a parameter is in a list
        if isinstance(param, list):
            if not isinstance(value, list):
                self._report_inconsistency('Parameter %s is not a list'
                                           % (value))
            for a in value:
                self._verify_type(param[0], a, identifier)
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
                'Unsupported type %s in %s please fix' % (t, identifier))

        # check whether it is a primitive type
        elif t in TYPE_KEYS:
            self._check_primitive_type(t, value, name)

        else:
            # if type is a string call type verification method
            if isinstance(t, six.string_types):
                self._verify_complex_type(t, param, value, name, identifier)

            # if type is in a list we need to get the type and call
            # type verification method
            elif isinstance(t, list):
                if not isinstance(value, (list, tuple)):
                    self._report_inconsistency('Parameter %s is not a sequence'
                                               % (value))
                for a in value:
                    self._verify_type(t[0], a, identifier)
            else:
                # call complex type verification
                self._verify_complex_type(t.get('type'), t, value, name,
                                          identifier)

    def _verify_complex_type(self, t_type, t, arg, name, identifier):
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
                self._verify_type(t.get('key-type'), key, identifier)
                self._verify_type(t.get('value-type'), value, identifier)
        elif t_type == 'union':
            # if union we need to check whether parameter matches on of the
            # values defined
            for value in t.get('values'):
                props = value.get('properties')
                prop_names = [prop.get('name') for prop in props]
                if not [key for key in arg if key not in prop_names]:
                    self._verify_complex_type(value.get('type'), value, arg,
                                              name, identifier)
                    return
            self._report_inconsistency('Provided parameters %s do not match'
                                       ' any of union %s values'
                                       % (arg, t.get('name')))
        elif t_type == 'enum':
            # if enum we need to check whether provided parameter is in values
            if arg not in t.get('values'):
                self._report_inconsistency('Provided value "%s" not'
                                           ' defined in %s enum for'
                                           ' %s' % (arg,
                                                    t.get('name'),
                                                    identifier))
        else:
            # if custom time (object) we need to check whether all the
            # properties match values provided
            self._verify_object_type(t, arg, identifier)

    def _verify_object_type(self, t, arg, identifier):
        props = t.get('properties')
        prop_names = [prop.get('name') for prop in props]
        # check if there are any extra prarameters
        unknown_props = [key for key in arg
                         if key not in prop_names]
        if unknown_props:
            if 'any_string' in prop_names:
                return
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
                        ' %s' % (p_name, identifier))
                if value == 'no-default':
                    continue
                if a is None or a == value:
                    continue
            else:
                if a is None:
                    self._report_inconsistency(
                        'Required property %s is not provided when calling'
                        ' %s' % (p_name, identifier))
                    continue
            # call type verification
            self._verify_type(prop, a, identifier)

    def verify_retval(self, rep, ret):
        try:
            ret_args = self.get_ret_param(rep)

            if ret_args:
                if isinstance(ret, HiddenValue):
                    ret = ret.value
                self._verify_type(ret_args.get('type'), ret, rep.id)
        except JsonRpcInvalidParamsError:
            raise
        except Exception:
            self._report_inconsistency('Unexpected issue with response type'
                                       ' verification for %s' % rep.id)

    def verify_event_params(self, sub_id, args):
        rep = EventRep(sub_id)
        try:
            # due to issue with vm status changes key names (vm_ids)
            # we are not able to find unknown params
            for param in self.get_args(rep):
                name = param.get('name')
                if name == 'no_name':
                    for key, value in six.iteritems(args):
                        if key == "notify_time":
                            continue
                        self._verify_type(param, {key: value}, rep.id)
                    continue
                arg = args.get(name)
                if arg is None:
                    if 'defaultvalue' not in param:
                        self._report_inconsistency(
                            'Required parameter %s is not '
                            'provided when sending %s' % (name, rep.id))
                    continue
                self._verify_type(param, arg, rep.id)
        except JsonRpcInvalidParamsError:
            raise
        except Exception:
            self._report_inconsistency('Unexpected issue with event type'
                                       ' verification for %s' % rep.id)

    def _get_arg_dict(self, arg_type, name, params_dict):
        '''
        creates a dictionary representing an argument that can consist nested
        argument types.

        Args:
            arg_type:    argument type, can be primitive or complex (extracted
                         from the schema)
            name:        arg name
            params_dict: empty dictionary that will be filled with argument's
                         nested types.
        '''
        if isinstance(arg_type.get('type'), list):
            params_dict[name] = []
            for member in arg_type.get('type'):
                member_dict = {}
                if hasattr(member, 'get'):
                    self._get_arg_dict(
                        member, member.get('name'), member_dict)
                params_dict[name].append(member_dict)

        elif arg_type.get('type') == 'object':
            for prop in arg_type.get('properties'):
                if isinstance(prop.get('type'), dict):
                    self._get_arg_dict(
                        prop.get('type'), prop.get('name'), params_dict)
                else:
                    self._get_arg_dict(
                        prop, prop.get('name'), params_dict)

        elif arg_type.get('type') == 'union':
            params_dict[name] = []
            for value in arg_type.get('values'):
                if hasattr(value, 'get'):
                    params_dict[name].append(
                        (value.get('name'), value.get('type')))
                else:
                    params_dict[name].append(value)

        elif arg_type.get('type') == 'enum':
            params_dict[name] = 'enum {}'.format(
                [value for value in arg_type.get('values')])

        elif arg_type.get('type') == 'alias':
            params_dict[name] = arg_type.get('name')

        else:
            params_dict[name] = arg_type.get('type')

    def get_args_dict(self, namespace, method):
        '''
        This function creates a dictionary represenation of all
        nested arguments, where key represents the argument name and
        value is the argument type.

        Args:
            namespace(string): namespace containing the method
            method(string):    method name

        Returns:
            a dictionary contatining all method arguments
        '''
        method_rep = MethodRep(namespace, method)
        args = self.get_args(method_rep)
        if not args:
            return None
        params_dict = {}
        for arg in args:
            if isinstance(arg.get('type'), dict):
                arg_name = arg.get('type').get('name')
                arg_type = self.get_type(arg_name)
                param_dict = {}
                self._get_arg_dict(arg_type, arg_name, param_dict)
                params_dict[arg.get('name')] = param_dict
            elif isinstance(arg.get('type'), list):
                params_dict[arg.get('name')] = []
                for param in arg.get('type'):
                    param_dict = {}
                    if not hasattr(param, 'get'):
                        params_dict[arg.get('name')].append(param)
                    elif isinstance(param.get('type'), dict):
                        param_type = self.get_type(
                            param.get('type').get('name'))
                        self._get_arg_dict(
                            param_type, param.get('type').get('name'),
                            param_dict)
                    else:
                        self._get_arg_dict(
                            param, param.get('name'), param_dict)
                    params_dict[arg.get('name')].append(param_dict)
            else:
                params_dict[arg.get('name')] = arg.get('type')
        return json.dumps(params_dict, indent=4)
