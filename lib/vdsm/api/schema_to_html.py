#!/usr/bin/python3
#
# Copyright (C) 2012-2016 Adam Litke, IBM Corporation
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

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
import sys
from contextlib import contextmanager

import argparse
import six

from vdsm.api import vdsmapi

html_escape_table = {
    "&": "&amp;",
    '"': "&quot;",
    "'": "&apos;",
    ">": "&gt;",
    "<": "&lt;",
}


def html_escape(text):
    """
    Escape stings for proper display in html documents.
    """
    return "".join(html_escape_table.get(c, c) for c in text)


def start_table(f):
    f.write('<table class="attrlist">\n')


def write_caption(caption, f):
    f.write('<caption>%s</caption>\n' % caption)


def end_table(f):
    f.write('</table>\n')


@contextmanager
def write_table(caption, f):
    start_table(f)
    write_caption(caption, f)

    yield

    end_table(f)


def filter_types(item):
    """
    When creating the type crosslink, if an entity is a list container we
    want to link to the element type.
    """
    if type(item) is list:
        item = item[0]

    if item in vdsmapi.TYPE_KEYS or item == 'dict':
        return item
    else:
        return item['name']


def write_no_params(f):
    f.write('<tr><td class="attrlist">None</td>'
            '<td class="attrlist"></td></tr>\n')


def attr_table(name, dataType, desc, f):
    """
    Produce a table line with attributes using attribute name, type links
    and the description
    """
    f.write('<tr>')
    f.write('<td class="attrlist">%s</td>' % name)
    if dataType is None:
        link = ''
    elif dataType not in vdsmapi.TYPE_KEYS:
        dataType = html_escape(dataType)
        link = '<a href="#%s">%s</a>' % (dataType, dataType)
    else:
        link = dataType
    f.write('<td class="attrlist">%s</td>' % link)
    f.write('<td class="attrlist">%s</td>' % html_escape(desc or ''))
    f.write('</tr>\n')


def write_params(params, caption, f):
    with write_table(caption, f):
        if not params:
            write_no_params(f)
        else:
            for param in params:
                name = param.get('name')
                param_type = filter_types(param.get('type'))
                decs = param.get('description')
                attr_table(name, param_type, decs, f)


def write_symbol(f, s):
    """
    Write an HTML reprentation of a symbol definition and documentation.
    """
    f.write('<p>\n')
    # Add anchor
    f.write('<a name="%s" />\n' % s['name'])
    # Bold name
    f.write('<b>%s:</b><br/>\n' % s['name'])
    # Description
    f.write('%s<br/>\n' % html_escape(s['description']))

    if 'type' not in s.keys():
        # Command parameters
        params = s.get('params')
        write_params(params, 'Arguments', f)

        # Command return value(s)
        ret_value = s.get('return')
        if ret_value:
            ret_type = filter_types(ret_value.get('type'))
            ret_desc = ret_value.get('description')

            with write_table('Returns', f):
                attr_table([''], ret_type, ret_desc, f)
    else:
        t_name = s['type']
        if 'object' in t_name:
            # Type members
            props = s.get('properties')
            write_params(props, 'Members', f)
        elif 'union' in t_name:
            # Union member types
            with write_table('Types', f):
                values = s.get('values')
                for value in values:
                    value = filter_types(value)
                    if (value in vdsmapi.TYPE_KEYS or
                            isinstance(value, six.string_types)):
                        name = value
                    else:
                        name = value.get('name')
                    attr_table(name, value, None, f)
        elif 'enum' in t_name:
            # Enum values
            with write_table('Values', f):
                values = s.get('values')
                for value in values:
                    attr_table(value, None, values.get(value), f)
        elif 'map' in t_name:
            # Mapping key and value
            with write_table('Key', f):
                key = filter_types(s.get('key-type'))
                attr_table(key, key, None, f)

            with write_table('Value', f):
                value = filter_types(s.get('value-type'))
                attr_table(value, value, None, f)
        elif 'alias' in t_name:
            # Aliased type
            with write_table('Type', f):
                name = s.get('name')
                source_type = filter_types(s.get('sourcetype'))
                desc = s.get('description')
                attr_table(name, source_type, desc, f)

    f.write('</p><br/>\n')


def create_doc(api_schema, filename):
    with open(filename, 'w') as f:

        header = """
        <html>
          <head>
            <title>VDSM API Schema</title>
            <style type="text/css">
              table {
                margin: 10px 0px 10px 10px;
                border-top: 1px solid black;
                border-bottom: 1px solid black;
              }
              td {
                padding:2px 15px 2px 2px;
                vertical-align: top;
              }
              caption {
                text-align:left;
              }
            </style>
          </head>
          <body>
        """
        footer = """
          </body>
        </html>
        """
        f.write(header)

        # First, write out commands in sorted order
        for method_name in api_schema.get_methods:
            className, methodName = method_name.split('.', 1)
            method = api_schema.get_method(
                vdsmapi.MethodRep(className, methodName))
            method['name'] = method_name
            write_symbol(f, method)

        # Write out the data types
        for type_name in api_schema.get_types:
            write_symbol(f, api_schema.get_type(type_name))

        f.write(footer)


def main():
    parser = argparse.ArgumentParser(
        "A schema definition to HTML documentation converter")
    parser.add_argument("schema_type",
                        choices=[st.value for st in vdsmapi.SchemaType])
    parser.add_argument("html_path")
    args = parser.parse_args(sys.argv[1:])

    schema_type = vdsmapi.SchemaType(args.schema_type)
    api_schema = vdsmapi.Schema((schema_type,), strict_mode=False)
    create_doc(api_schema, args.html_path)


if __name__ == '__main__':
    main()
