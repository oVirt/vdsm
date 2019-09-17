#!/usr/bin/python2
#
# Copyright 2015 Red Hat, Inc.
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
from __future__ import print_function

'''
To Enable this set fake_kvm_support=true in /etc/vdsm/vdsm.conf.
'''
from functools import wraps
import getopt
import sys

from vdsm import cpuinfo
from vdsm.common import cpuarch
from vdsm.config import config


_TESTS = []


_PPC64LE_MACHINES = ['pseries',
                     'pseries-rhel7.2.0',
                     'pseries-rhel7.3.0',
                     'pseries-rhel7.4.0',
                     'pseries-rhel7.5.0',
                     'pseries-rhel7.5.0-sxxm',
                     'pseries-rhel7.6.0-sxxm']
_X86_64_MACHINES = ['pc-i440fx-rhel7.1.0',
                    'rhel6.3.0',
                    'pc-q35-rhel7.6.0',
                    'pc-q35-rhel7.5.0',
                    'pc-q35-rhel7.4.0',
                    'pc-q35-rhel7.3.0',
                    'pc-i440fx-rhel7.0.0',
                    'pc-i440fx-2.6',
                    'rhel6.1.0',
                    'rhel6.6.0',
                    'rhel6.2.0',
                    'pc',
                    'pc-i440fx-rhel7.6.0',
                    'pc-i440fx-rhel7.5.0',
                    'pc-i440fx-rhel7.4.0',
                    'pc-i440fx-rhel7.3.0',
                    'q35',
                    'pc-i440fx-rhel7.2.0',
                    'rhel6.4.0',
                    'rhel6.0.0',
                    'rhel6.5.0']
_AARCH64_MACHINES = ['virt',
                     'virt-rhel7.3.0',
                     'virt-rhel7.4.0',
                     'virt-rhel7.5.0']


def _usage():
    print('Usage: ./10_faqemu option')
    print('\t-h, --help\t\tdisplay this help')
    print('\t-t, --test\t\trun tests')


def _fake_caps_arch(caps, arch):
    '''
    Mutate 'caps' to act as an architecture set by fake_kvm_architecture
    configuration option.

    Arguments:

    caps        The host capabilities as returned by hooking.read_json.
    '''
    arch = arch

    caps['kvmEnabled'] = True

    if cpuarch.is_x86(arch):
        caps['emulatedMachines'] = _X86_64_MACHINES
        caps['cpuModel'] = 'Intel(Fake) CPU'

        flag_list = ['vmx', 'sse2', 'nx']

        if cpuarch.real() == cpuarch.X86_64:
            flag_list += cpuinfo.flags()

        flags = set(flag_list)

        caps['cpuFlags'] = ','.join(flags) + ',model_486,model_pentium,' \
            'spec_ctrl,ssbd,md_clear,model_Skylake-Client,' \
            'model_qemu32,model_coreduo,model_core2duo,model_n270,' \
            'model_Conroe,model_Westmere,model_Nehalem,model_Opteron_G5'
    elif cpuarch.is_ppc(arch):
        caps['emulatedMachines'] = _PPC64LE_MACHINES
        caps['cpuModel'] = 'POWER 8(fake)'
        caps['cpuFlags'] = 'powernv,model_POWER8'
    elif cpuarch.is_arm(arch):
        caps['emulatedMachines'] = _AARCH64_MACHINES
        caps['cpuModel'] = 'AARCH64 (fake)'
        caps['cpuFlags'] = ''
    else:
        raise cpuarch.UnsupportedArchitecture(arch)


def add_testcase():
    '''
    Method to register workaround test
    '''
    def workaround(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            return function(*args, **kwargs)
        _TESTS.append(wrapped)
        return wrapped
    return workaround


@add_testcase()
def x86_64_test():
    caps = {'cpuModel': None,
            'cpuFlags': None,
            'emulatedMachines': None,
            'kvmEnabled': False}

    expected_caps = {'cpuModel': 'Intel(Fake) CPU',
                     'cpuFlags': ',model_486,model_pentium,model_pentium2,'
                     'model_pentium3,model_pentiumpro,model_qemu32,'
                     'model_coreduo,model_core2duo,model_n270,model_Conroe,'
                     'model_Penryn,model_Nehalem,model_Opteron_G1',
                     'emulatedMachines': _X86_64_MACHINES,
                     'kvmEnabled': True}

    # This is duplicate of the real functionality and is required because we do
    # not know which flags are added unless we query the host cpu.
    flag_list = ['vmx', 'sse2', 'nx']
    if cpuarch.real() == cpuarch.X86_64:
        flag_list += cpuinfo.flags()

    expected_caps['cpuFlags'] = (','.join(set(flag_list)) +
                                 expected_caps['cpuFlags'])
    _fake_caps_arch(caps, cpuarch.X86_64)

    return caps == expected_caps


@add_testcase()
def ppc64le_test():
    caps = {'cpuModel': None,
            'cpuFlags': None,
            'emulatedMachines': None,
            'kvmEnabled': False}

    expected_caps = {'cpuModel': 'POWER 8(fake)',
                     'cpuFlags': 'powernv,model_POWER8',
                     'emulatedMachines': _PPC64LE_MACHINES,
                     'kvmEnabled': True}

    _fake_caps_arch(caps, cpuarch.PPC64LE)

    return caps == expected_caps


@add_testcase()
def noarch_test():
    try:
        _fake_caps_arch({}, 'noarch')
    except cpuarch.UnsupportedArchitecture:
        return True

    return False


def _test():
    for test in _TESTS:
        print('{:<70}{:>10}'.format(test.__name__, 'ok' if test() else 'fail'))


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

    fake_kvm_support = config.getboolean('vars', 'fake_kvm_support')
    fake_kvm_arch = config.get('vars', 'fake_kvm_architecture')

    if fake_kvm_support:
        # Why here? So anyone can run -t and -h without setting the path.
        try:
            import hooking
        except ImportError:
            print('Could not import hooking module. You should only run this '
                  'script directly with option specified.')
            _usage()
            sys.exit(1)

        caps = hooking.read_json()
        _fake_caps_arch(caps, fake_kvm_arch)
        hooking.write_json(caps)
