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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#
from . import _parser
from . import _wrapper
_TC_PRIO_MAX = 15


def add(dev, kind, parent, classid, **opts):
    """Adds a class to a device. Opts should be used with list values for
    complex inputs, e.g. {'ls': ['rate', '400kbps']}"""
    command = ['class', 'add', 'dev', dev, 'parent', parent,
               'classid', classid]
    command.append(kind)
    for key, value in opts.items():
        if isinstance(value, str):
            command += [key, value]
        else:
            command.append(key)
            command += value
    _wrapper.process_request(command)


def delete(dev, classid, parent=None):
    command = ['class', 'del', 'dev', dev, 'classid', classid]
    if parent is not None:
        command += ['parent', parent]
    _wrapper.process_request(command)


def show(dev, parent=None, classid=None):
    command = ['class', 'show', 'dev', dev]
    if parent is not None:
        command += ['parent', parent]
    if classid is not None:
        command += ['classid', classid]
    return _wrapper.process_request(command)


def parse(tokens):
    """Takes a token generator and returns a dictionary of general class
    attributes and class kind (TCA_KIND) specific attributes."""
    kind = next(tokens)
    data = {'kind': kind, 'handle': _parser.parse_str(tokens)}
    for token in tokens:
        if token in ('dev', 'leaf', 'parent'):
            data[token] = _parser.parse_str(tokens)
        elif token == 'root':
            data[token] = _parser.parse_true(tokens)
        else:
            # Finished with the general class attrs. Loop for kind attrs
            spec_parser = _spec.get(kind, ())
            while True:
                if token in spec_parser:
                    value = spec_parser[token](tokens)
                    try:
                        data[kind][token] = value
                    except KeyError:
                        data[kind] = {token: value}
                else:
                    pass  # Consume anything that we don't know how to parse
                try:
                    token = next(tokens)
                except StopIteration:
                    break
    if kind == 'hfsc' and 'sc' in data.get(kind, {}):
        #  sc is a shorthand for when rt an ls are equal. For reporting to the
        #  api we separate it into the two components
        curve = data[kind].pop('sc')
        data[kind]['rt'] = data[kind]['ls'] = curve
    return data


def _parse_hfsc_curve(tokens):
    return dict((
        (_parser.parse_str(tokens),  # Get and consume 'm1'
         _parser.parse_rate(tokens)),
        (_parser.parse_str(tokens),  # Get and consume 'd'
         _parser.parse_time(tokens)),
        (_parser.parse_str(tokens),  # Get and consume 'm2'
         _parser.parse_rate(tokens)),
    ))


_spec = {
    'hfsc': {
        'sc': _parse_hfsc_curve,
        'rt': _parse_hfsc_curve,
        'ls': _parse_hfsc_curve,
        'ul': _parse_hfsc_curve,
    },
}
