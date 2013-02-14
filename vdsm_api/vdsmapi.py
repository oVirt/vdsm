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
