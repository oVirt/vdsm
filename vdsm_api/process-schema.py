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


def read_symbol_comment(f, symbols):
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

    def find_symbol(symbols, name):
        """
        Find a symbol by name in the vdsmapi parsed symbol list.
        """
        for s in symbols:
            if '.' in name:
                cls, member = name.split('.')
                if member == 'init' and s.get('init') == cls:
                    return s
                if 'command' not in s:
                    continue
                if s['command']['class'] == cls and \
                        s['command']['name'] == member:
                    return s
            else:
                for k in typeKinds:
                    if s.get(k) == name:
                        return s
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

    # Find the already processed symbol information
    symbol = find_symbol(symbols, name)
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


def read_schema_doc(f, symbols):
    """
    Read all of the documentation information from the schema and attach it to
    the relavent symbol definitions we have already parsed.
    """
    while True:
        line = f.readline()
        if not line:
            return symbols
        if line.strip() == '##':
            read_symbol_comment(f, symbols)
            continue


def html_escape(text):
    """
    Escape stings for proper display in html documents.
    """
    return "".join(html_escape_table.get(c, c) for c in text)


def write_symbol(f, s):
    """
    Write an HTML reprentation of a symbol definition and documentation.
    """
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


def create_doc(symbols, filename):
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

    # Sort commands by their expanded names
    cmdKey = lambda k: k.get('command', {}).get('class', '') + '.' + \
                            k.get('command', {}).get('name', '')
    commands = [s for s in sorted(symbols, key=cmdKey)
                    if 'command' in s]
    # Types come after commands but they are not sorted
    types = [s for s in symbols if 'command' not in s]
    for s in commands:
        write_symbol(f, s)
    for s in types:
        write_symbol(f, s)
    f.write(footer)


def main():
    schema = sys.argv[1]
    output = sys.argv[2]

    symbols = None
    # First read in the progmatic schema definition
    with open(schema) as f:
        symbols = vdsmapi.parse_schema(f)
    # Now merge in the information from the comments
    with open(schema) as f:
        symbols = read_schema_doc(f, symbols)
    create_doc(symbols, output)


if __name__ == '__main__':
    main()
