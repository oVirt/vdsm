#
# Copyright 2014 Red Hat, Inc.
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
import inspect

import API
try:
    import gluster.apiwrapper as gapi
    _glusterEnabled = True
except ImportError:
    _glusterEnabled = False

from rpc import Bridge
from testlib import VdsmTestCase as TestCaseBase


class SchemaValidation(TestCaseBase):

    # verbs not used in the engine and lacking definition in schema
    IGNORED_CMDS = ['Image_downloadFromStream', 'Image_uploadToStream',
                    'Volume_setSize', 'Volume_updateSize']

    def test_verify_schema(self):
        apiobj = self._get_api('API')
        self._validate(apiobj)

    def test_verify_gluster_schema(self):
        if _glusterEnabled:
            apiobj = self._get_api('gapi')
            self._validate(apiobj)

    def _validate(self, api_mod):
        bridge = Bridge.DynamicBridge()

        for class_name, class_obj in self._get_api_classes(api_mod):
            apiObj = getattr(api_mod, class_name)
            ctorArgs = apiObj.ctorArgs
            ctor_defaults = []

            spec_class = class_name
            if spec_class == 'Global':
                spec_class = 'Host'

            for method_name, method_obj in inspect.getmembers(
                    class_obj, inspect.ismethod):
                cmd = '%s_%s' % (spec_class, method_name)
                if cmd in self.IGNORED_CMDS:
                    continue

                # gather default args from ctor
                if method_name == '__init__':
                    ctor_defaults = self._get_default_args(method_obj)
                    continue

                # ignore private methods
                if method_name.startswith('_'):
                    continue

                try:
                    # get args from schema
                    method_args = bridge._getArgList(spec_class, method_name)
                except KeyError:
                    raise AssertionError('Missing method %s.%s' % (
                                         spec_class, method_name))
                    continue

                # inspect apiobj and gather args and default args
                args = ctorArgs + self._get_args(method_obj)
                default_args = ctor_defaults + self._get_default_args(
                    method_obj)

                # check len equality
                if len(args) != len(method_args):
                    raise AssertionError(self._prep_msg(class_name,
                                         method_name, method_args, args))
                for marg in method_args:
                    # verify optional arg
                    if marg.startswith('*'):
                        if not marg[1:] in default_args:
                            raise AssertionError(
                                self._prep_msg(class_name, method_name,
                                               method_args, args))
                        continue
                    # verify args from schema in apiobj args
                    if not bridge._symNameFilter(marg) in args:
                        raise AssertionError(self._prep_msg(
                            class_name, method_name, method_args, args))
                try:
                    # verify ret value with entry in command_info
                    ret = bridge._getRetList(spec_class, method_name)
                    ret_info = Bridge.command_info.get(cmd, {}).get('ret')
                    if not ret_info and not ret:
                        continue
                    if not ret_info or not ret:
                        raise AssertionError('wrong return type for ' + cmd)
                except KeyError:
                    raise AssertionError('Missing ret %s.%s' % (
                                         spec_class, method_name))

    def _get_api_classes(self, api_mod):
        for class_name, class_obj in inspect.getmembers(api_mod,
                                                        inspect.isclass):
            if issubclass(class_obj, API.APIBase):
                yield class_name, class_obj

    def _get_args(self, method_obj):
        args = inspect.getargspec(method_obj).args
        args.remove('self')
        return args

    def _get_default_args(self, method_obj):
        argSpec = inspect.getargspec(method_obj)
        if argSpec.defaults:
            return argSpec.args[- len(argSpec.defaults):]
        else:
            return []

    def _prep_msg(self, class_name, method_name, method_args, args):
        return '%s.%s has different args: %s, %s' % (class_name, method_name,
                                                     method_args, args)

    def _get_api(self, selector):
        if (selector == 'API'):
            return API
        elif (selector == 'gapi'):
            return gapi
        else:
            return None
