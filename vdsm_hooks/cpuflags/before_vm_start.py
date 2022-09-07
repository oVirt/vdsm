#!/usr/bin/python3

# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import print_function

'''
VDSM cpuflags hook

This hook allows selection of CPU features that should (not) be visible to the
guest OS. Feature names can be found in /usr/share/libvirt/cpu_map.xml.

Installation:
* Use engine-config to define the appropriate custom properties:

Custom property for cpuflags in a VM:
    sudo engine-config -s "UserDefinedVMProperties=cpuflags=^.*$"

* Verify that the custom properties were added properly:
    sudo engine-config -g UserDefinedVMProperties

Usage:
Set the custom property

cpuflags

using the following syntax:

'+feature,-feature'

where '+' operator indicates inclusion i.e. the flag will be present to the
guest OS, '-' operator indicates exclusion i.e. the flag will be absent from
the guest OS.

A special case 'SAP' exists, that, if specified, will automatically include
features required for SAP. This case takes no operator.

Following naming conventions exist to discern between features and special
cases:

1. features are lowercase, prefixed by + or -
2. groups (or special cases) are UPPERCASE without any prefix

Additionally, note that the order in which features and/or special groups are
specified is not important. Duplicates are ignored.

Examples (the chosen flags are arbitrary):
'SAP' ~> enable special SAP flags
'+mmx,+syscall' ~> ensure MMX and syscall features are present in the guest
'+mmx,SAP,-syscall' ~> enable mmx, disable syscall and ensure SAP flags
'+mmx,-mmx' ~> error, conflicting choice
'mmx' ~> error, missing (ex/in)clusion information

Notes:
The hook is also activated when 'sap_agent' custom property is chosen.
'''

import os
import sys
import getopt
from collections import Counter


def _usage():
    print('Usage: {} option'.format(sys.argv[0]))
    print('\t-h, --help\t\tdisplay this help')
    print('\t-t, --test\t\trun tests')


_SAP_PLACEHOLDER = set(['SAP'])
_SAP_FLAGS = set(['+invtsc', '+rdtscp'])


def _create_feature_xml(domxml, flag, requested):
    '''
    Construct the XML CPU feature snippet
    <feature policy='disable' name='lahf_lm'/>
    '''
    element = domxml.createElement('feature')
    element.setAttribute('name', flag)
    element.setAttribute('policy', requested)

    return element


def _extract_flags(custom_property):
    if custom_property.strip() == '':
        return set()

    flags = set([flag.strip() for flag in custom_property.split(',')])
    if 'SAP' in flags:
        # Replace the 'SAP' string by flags it requires.
        flags = (flags - _SAP_PLACEHOLDER) | _SAP_FLAGS

    return flags


_EXTRACT_FLAGS_TEST_DATA = {
    '': set(),
    '+syscall': {'+syscall'},
    '-syscall': {'-syscall'},
    'SAP': _SAP_FLAGS,
    '+syscall,-mmx,SAP': {'+syscall', '-mmx'} | _SAP_FLAGS,
    '+syscall,-mmx,+mmx': {'+syscall', '-mmx', '+mmx'},
}


def _find_invalid_flags(flags):
    return list(filter(lambda flag: flag[0] not in ('+', '-'), flags))


_FIND_INVALID_FLAGS_TEST_DATA = {
    (): [],
    ('+syscall',): [],
    ('-syscall',): [],
    ('SAP',): ['SAP'],
    ('+syscall', '-mmx', 'SAP'): ['SAP'],
    ('+syscall', '-mmx', '+mmx'): [],
}


def _find_conflicting_flags(flags):
    return [flag for flag, count in
            Counter([flag[1:] for flag in flags]).items() if
            count > 1]


_FIND_CONFLICTING_FLAGS_DATA = {
    (): [],
    ('+syscall',): [],
    ('-syscall',): [],
    ('SAP',): [],
    ('+syscall', '-mmx', 'SAP'): [],
    ('+syscall', '-mmx', '+mmx'): ['mmx'],
}


def _test():
    for fn, table in (
        (_extract_flags, _EXTRACT_FLAGS_TEST_DATA),
        (_find_invalid_flags, _FIND_INVALID_FLAGS_TEST_DATA),
        (_find_conflicting_flags, _FIND_CONFLICTING_FLAGS_DATA),
    ):
        for data_in, data_out in table.items():
            assert fn(data_in) == data_out


def _main(domxml):
    if 'cpuflags' in os.environ:
        flags = os.environ['cpuflags']
    elif hooking.tobool(os.environ.get('sap_agent', False)):
        flags = 'SAP'
    else:
        return

    cpu_element = domxml.getElementsByTagName('cpu')[0]

    flags = _extract_flags(flags)

    # Let's check if each flag begins with '+' or '-' and bail out if it
    # doesn't. Due to importance of the flags for certain applications,
    # guessing the user's intention is out of scope of the hook.
    invalid_flags = _find_invalid_flags(flags)
    if invalid_flags:
        sys.stderr.write(
            'cpuflags: flags {} are missing \'+\' or \'-\' operator.\n'.format(
                ', '.join(invalid_flags)
            )
        )
        sys.exit(1)

    # Yet another check -- let's halt if conflicting flags (e.g. +mmx, -mmx)
    # exist.
    conflicting_flags = _find_conflicting_flags(flags)
    if conflicting_flags:
        sys.stderr.write(
            'cpuflags: flags {} are conflicting.\n'.format(
                ', '.join(conflicting_flags)
            )
        )
        sys.exit(1)

    for flag in flags:
        requested = 'require' if flag[0] == '+' else 'disable'
        cpu_element.appendChild(_create_feature_xml(
            domxml, flag[1:], requested)
        )


if __name__ == '__main__':
    try:
        opts, args = getopt.getopt(sys.argv[1:], 'ht', ['help', 'test'])
    except getopt.GetoptError as err:
        print(str(err))
        _usage()
        sys.exit(1)

    for option, _ in opts:
        if option in ('-h', '--help'):
            _usage()
            sys.exit()
        elif option in ('-t', '--test'):
            _test()
            sys.exit()

    try:
        import hooking
    except ImportError:
        print('Could not import hooking module. You should only run this '
              'script directly with option specified.')
        _usage()
        sys.exit(1)

    domxml = hooking.read_domxml()
    _main(domxml)
    hooking.write_domxml(domxml)
