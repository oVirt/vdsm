#!/usr/bin/env python
#
# Copyright (C) 2012 Adam Litke, IBM Corporation
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

import sys
import re
import vdsmapi

html_escape_table = {
    "&": "&amp;",
    '"': "&quot;",
    "'": "&apos;",
    ">": "&gt;",
    "<": "&lt;",
}

# Symbols of these types are considered data types
typeKinds = ('class', 'type', 'enum', 'map', 'union', 'alias')


def read_symbol_comment(f, api):
    """
    In the VDSM API schema, each entity is preceeded by a comment that provides
    additional human-readable information about the entity.  The format of this
    comment block is as follows:

    ##
    # @<entity-name>:
    #
    # <entity-description (multi-line)>
    #
    # @<parameter or member>:    <parameter or member description (multi-line)>
    #
    # ... More parameters / members ...
    #
    # [Returns: (for commands only)]
    # [@<return-name>:  <return-description (multi-line)>]
    #
    # ... More returned values ...
    #
    # Since: <vdsm-version>
    #
    # [Notes:  <API notes string (multi-line)>]
    ##
    """

    def find_symbol(api, name):
        """
        Find a symbol by name in the vdsmapi parsed symbol list.
        """
        if '.' in name:
            # This is a command
            ns, method = name.split('.')
            try:
                return api['commands'][ns][method]
            except KeyError:
                pass
        else:
            for sType in ('types', 'enums', 'aliases', 'maps'):
                if name in api[sType]:
                    return api[sType][name]
        raise ValueError("symbol: %s not found" % name)

    # Parse one complete comment block.  Blocks begin and end with '^##'.
    lines = []
    while True:
        line = f.readline()
        if line.strip() == '##':
            break
        if not line.startswith('#'):
            raise ValueError("Interrupted comment block: %s" % line)
        lines.append(line[1:].strip())

    # Find the entity name
    line = lines.pop(0)
    m = re.search('^\@(.*):$', line)
    name = m.group(1)

    # We skip namespace definitions since there is nothing to document
    if name in api['commands']:
        return

    # Find the already processed symbol information
    symbol = find_symbol(api, name)
    symbol.update({'name': name, 'info_data': {}, 'info_return': '',
                   'xxx': []})

    # Pop a blank line
    assert('' == lines.pop(0))

    # Grab the entity description.  It might span multiple lines.
    symbol['desc'] = lines.pop(0)
    while (lines[0] != ''):
        symbol['desc'] += lines.pop(0)

    # Pop a blank line
    assert ('' == lines.pop(0))

    # Populate the rest of the human-readable data.
    # First try to read the parameters/members information.  We are finished
    # once we read a <tag>: line.  After reading a 'Returns:' tag, we will try
    # to read return values in the same way.
    mode = 'info_data'
    last_arg = None
    while lines:
        line = lines.pop(0)
        if line == '':
            if mode == 'notes':
                symbol['notes'] += '\n'
            continue
        elif line.startswith('XXX:'):
            symbol['xxx'].append(line[4:].strip())
        elif mode == 'notes':
            symbol['notes'] += ' ' + line
        elif line.startswith('Returns:'):
            mode = 'info_return'
        elif line.startswith('Since:'):
            symbol['since'] = line[6:].strip()
        elif line.startswith('Notes:'):
            mode = 'notes'
            symbol['notes'] = line[6:]
        elif mode == 'info_data':
            # Try to read a parameter or return value
            m = re.search('^\@(.*?):\s*(.*)', line)
            if m:
                name, desc = (m.group(1), m.group(2))
                if name not in strip_stars(symbol['data']):
                    raise ValueError("'%s' comment mentions '%s' which is "
                                     "not defined" % (symbol['name'], name))
                symbol[mode][name] = desc
                # Track the name in case there is are multiple lines to append
                last_arg = name
            else:
                # Just append it to the last one we added
                symbol[mode][last_arg] += ' ' + line
        elif mode == 'info_return':
            symbol[mode] += line if symbol[mode] else ' ' + line
        else:
            raise ValueError("Line not valid: %s" % line)

    return symbol


def read_schema_doc(f, api):
    """
    Read all of the documentation information from the schema and attach it to
    the relavent symbol definitions we have already parsed.
    """
    while True:
        line = f.readline()
        if not line:
            return api
        if line.strip() == '##':
            read_symbol_comment(f, api)
            continue


def html_escape(text):
    """
    Escape stings for proper display in html documents.
    """
    return "".join(html_escape_table.get(c, c) for c in text)


def strip_stars(items):
    """
    A symbol prepended with '*' means the symbol is optional.  Strip this
    when looking up the symbol documentation.
    """
    ret = []
    for i in items:
        if i.startswith('*'):
            ret.append(i[1:])
        else:
            ret.append(i)
    return ret


def write_symbol(f, s):
    """
    Write an HTML reprentation of a symbol definition and documentation.
    """
    def filter_types(items):
        """
        When creating the type crosslink, if an entity is a list container we
        want to link to the element type.
        """
        ret = []
        for i in items:
            if type(i) is list:
                i = i[0]
            ret.append(i)
        return ret

    def attr_table(caption, names, types, details):
        """
        Produce a table listing attributes with type links and the description
        """
        f.write('<table class="attrlist">\n')
        f.write('<caption>%s</caption>\n' % caption)
        if not names:
            f.write('<tr><td class="attrlist">None</td>'
                    '<td class="attrlist"></td></tr>\n')
        for name, dataType, desc in zip(names, types, details):
            f.write('<tr>')
            f.write('<td class="attrlist">%s</td>' % name)
            if dataType:
                dataType = html_escape(dataType)
                link = '<a href="#%s">%s</a>' % (dataType, dataType)
            else:
                link = ''
            f.write('<td class="attrlist">%s</td>' % link)
            f.write('<td class="attrlist">%s</td>' % html_escape(desc or ''))
            f.write('</tr>\n')
        f.write('</table>\n')

    f.write('<p>\n')
    # Add anchor
    f.write('<a name="%s" />\n' % s['name'])
    # Bold name
    f.write('<b>%s:</b><br/>\n' % s['name'])
    # Description
    f.write('%s<br/>\n' % html_escape(s['desc']))

    if 'command' in s:
        # Command parameters
        names = strip_stars(s.get('data', {}).keys())
        types = filter_types(s.get('data', {}).values())
        details = [s['info_data'][n] for n in names]
        attr_table('Arguments', names, types, details)
    elif 'type' in s:
        # Type members
        names = strip_stars(s.get('data', {}).keys())
        types = filter_types(s.get('data', {}).values())
        details = [s['info_data'][n] for n in names]
        attr_table('Members', names, types, details)
    elif 'union' in s:
        # Union member types
        names = strip_stars(s.get('data', []))
        types = filter_types(names)
        details = [None for n in names]
        attr_table('Types', names, types, details)
    elif 'enum' in s:
        # Enum values
        names = strip_stars(s.get('data', []))
        types = [None for n in names]
        details = [s['info_data'][n] for n in names]
        attr_table('Values', names, types, details)
    elif 'map' in s:
        # Mapping key and value
        attr_table('Key', [s['key']], filter_types([s['key']]), [None])
        attr_table('Value', [s['value']], filter_types([s['value']]), [None])
    elif 'alias' in s:
        # Aliased type
        attr_table('Type', [s['data']], filter_types([s['data']]), [None])

    if 'command' in s and 'returns' in s:
        # Command return value(s)
        types = filter_types([s.get('returns')])
        detail = [s['info_return']]
        attr_table('Returns', [''], types, detail)

    if 'notes' in s:
        f.write('Notes:\n<ul>\n')
        for line in s['notes'].split('\n'):
            f.write('<li>%s</li>\n' % html_escape(line))
        f.write('</ul>\n')

    f.write('Since: <em>%s</em><br/>\n' % s['since'])

    f.write('</p><br/>\n')


def create_doc(api, filename):
    f = open(filename, 'w')

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
    for ns in sorted(api['commands'].iterkeys()):
        for cmd in sorted(api['commands'][ns].iterkeys()):
            write_symbol(f, api['commands'][ns][cmd])

    # Write out the data types
    for sType in ('aliases', 'types', 'maps', 'enums'):
        for name in sorted(api[sType].iterkeys()):
            write_symbol(f, api[sType][name])

    f.write(footer)


def verify_symbols(symbols):
    def filter_name(name):
        if isinstance(name, list):
            name = name[0]
        if name.startswith('*'):
            name = name[1:]
        return name

    names = ['str', 'bool', 'int', 'uint', 'float']
    names.extend([s['type'] for s in symbols if 'type' in s])
    names.extend([s['enum'] for s in symbols if 'enum' in s])
    names.extend([s['alias'] for s in symbols if 'alias' in s])
    names.extend([s['map'] for s in symbols if 'map' in s])

    # Make sure all type references are defined
    for s in symbols:
        if 'type' in s or 'command' in s:
            for k, v in s.get('data', {}).items():
                if filter_name(v) not in names:
                    raise ValueError("'%s': undefined type reference '%s'" %
                                     (s['type'], v))


def main():
    schema = sys.argv[1]
    output = sys.argv[2]

    api = vdsmapi.get_api(schema)
    # verify_symbols(symbols)

    # Now merge in the information from the comments
    with open(schema) as f:
        symbols = read_schema_doc(f, api)

    create_doc(symbols, output)


if __name__ == '__main__':
    main()
