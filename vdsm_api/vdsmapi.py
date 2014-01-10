#
# QAPI helper library (with vdsm extensions)
#
# Copyright IBM, Corp. 2011
#
# Authors:
#  Anthony Liguori <aliguori@us.ibm.com>
#  Adam Litke <agl@us.ibm.com>
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

import os

try:
    from collections import OrderedDict
    OrderedDict  # make pyflakes happy
except ImportError:
    from ordereddict import OrderedDict


def tokenize(data):
    while len(data):
        if data[0] in ['{', '}', ':', ',', '[', ']']:
            yield data[0]
            data = data[1:]
        elif data[0] in ' \n':
            data = data[1:]
        elif data[0] == "'":
            data = data[1:]
            string = ''
            while data[0] != "'":
                string += data[0]
                data = data[1:]
            data = data[1:]
            yield string
        else:
            raise ValueError('Invalid data: %r' % data)


def parse(tokens):
    if tokens[0] == '{':
        ret = OrderedDict()
        tokens = tokens[1:]
        while tokens[0] != '}':
            key = tokens[0]
            tokens = tokens[1:]

            tokens = tokens[1:]  # Skip ':'

            value, tokens = parse(tokens)

            if tokens[0] == ',':
                tokens = tokens[1:]

            ret[key] = value
        tokens = tokens[1:]
        return ret, tokens
    elif tokens[0] == '[':
        ret = []
        tokens = tokens[1:]
        while tokens[0] != ']':
            value, tokens = parse(tokens)
            if tokens[0] == ',':
                tokens = tokens[1:]
            ret.append(value)
        tokens = tokens[1:]
        return ret, tokens
    else:
        return tokens[0], tokens[1:]


def evaluate(string):
    return parse(map(lambda x: x, tokenize(string)))[0]


def parse_schema(fp):
    exprs = []
    expr = ''
    expr_eval = None

    for line in fp:
        if line.startswith('#') or line == '\n':
            continue

        if line.startswith(' '):
            expr += line
        elif expr:
            expr_eval = evaluate(expr)
            exprs.append(expr_eval)
            expr = line
        else:
            expr += line

    if expr:
        expr_eval = evaluate(expr)
        exprs.append(expr_eval)

    return exprs


def find_schema(schema_name='vdsmapi', raiseOnError=True):
    """
    Find the API schema file whether we are running from within the source dir
    or from an installed location
    """
    # Don't depend on module VDSM if not looking for schema
    from vdsm import constants

    localpath = os.path.dirname(__file__)
    installedpath = constants.P_VDSM
    for directory in localpath, installedpath:
        path = os.path.join(directory, schema_name + '-schema.json')
        if os.access(path, os.R_OK):
            return path

    if not raiseOnError:
        return None

    raise Exception("Unable to find API schema file in %s or %s",
                    localpath, installedpath)


_api_info = None


def _load_api_info(schema):
    """
    Organize API information from the schema file into a useful structure:

    types: A dictionary of type symbols indexed by type name
    {
     'type': <type name>
     'data': <dict containing this type's attributes in name/type pairs>
     'union': <an optional list of child types to which this type is castable>
    }

    enums: A dictionary of enum symbols indexed by enum name
    {
     'enum': <enum name>,
     'data': <a list of valid values>
    }

    aliases: A dictionary of alias symbols indexed by alias name
    {
     'alias': <alias name>
     'data': <aliased type>
    }

    maps: A dictionary of mapping types indexed by name
    {
     'map': <the name of the mapping type>
     'key': <the type for this map's keys>
     'value': <the type for this map's values>
    }

    commands: A dictionary of command namespaces indexed by namespace name that
    contains dictionaries of command symbols indexed by command name
    {
     'command'
     {
      'class': <the namespace to which the command belongs>
      'name': <the command name>
     }
     'data': <an optional ordered dictionary of parameters in name/type pairs>
     'returns': <the type of the return value, if any>
    }

    unions: A dictionary that describes valid casts between related types.
    Each key is a source type that is castable and the value is a list of types
    to which the source type may be cast.
    """
    global _api_info
    gluster_schema = None

    info_key = schema
    if schema is None:
        schema = find_schema()
        gluster_schema = find_schema(schema_name='gluster/vdsmapi-gluster',
                                     raiseOnError=False)

    with open(schema) as f:
        symbols = parse_schema(f)

    # If gluster schema file present inside gluster directory then read and
    # parse, append to symbols
    if gluster_schema:
        with open(gluster_schema) as f:
            symbols += parse_schema(f)

    info = {'types': {}, 'enums': {}, 'aliases': {}, 'maps': {},
            'commands': {}, 'unions': {}}

    for s in symbols:
        if 'alias' in s:
            info['aliases'][s['alias']] = s
        elif 'type' in s:
            info['types'][s['type']] = s
        elif 'enum' in s:
            info['enums'][s['enum']] = s
        elif 'map' in s:
            info['maps'][s['map']] = s
        elif 'command' in s:
            ns = s['command']['class']
            cmd = s['command']['name']
            if ns not in info['commands']:
                info['commands'][ns] = {cmd: s}
            else:
                info['commands'][ns][cmd] = s

    # Determine the valid casts
    def add_relation(mapping, typeA, typeB):
        if typeA in mapping:
            mapping[typeA].append(typeB)
        else:
            mapping[typeA] = [typeB, ]

    for t in info['types'].values():
        if 'union' not in t:
            continue
        for u in t['union']:
            add_relation(info['unions'], u, t['type'])
            add_relation(info['unions'], t['type'], u)

    _api_info = {info_key: info}


def get_api(schema=None):
    """
    Get organized information about the vdsm API.  If schema is specified,
    read from a specific file.  Otherwise try to find the schema automatically.
    """
    if _api_info is None or schema not in _api_info:
        _load_api_info(schema)
    return _api_info[schema]
