#!/usr/bin/python3

# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import print_function

import io
import json
import sys

import six

import mom


def usage():
    print("""
Usage:
    {0} host_data vm_data policy1 ...

    report the vm controlled based on the specified command line arguments.
    For example,

    {0} \\
        '{{"cpu_count": 120}}' \\
        '{{"vcpu_count": 1, "vcpu_period": null, \\
           "vcpu_user_limit": 50, "vcpu_quota": null}}' \\
        ../static/etc/vdsm/mom.d/00-defines.policy \\
        ../static/etc/vdsm/mom.d/04-cputune.policy
""".format(sys.argv[0]))


def load_policy_file(policy, filename):
    policy_string = io.open(filename, 'r').read()
    if not policy.set_policy(filename, policy_string):
        raise Exception('cannot set policy %s' % filename)


def prepare_entity(name, data):
    cfg = six.moves.configparser.SafeConfigParser()
    cfg.add_section("__int__")
    cfg.set("__int__", "plot-subdir", "")

    ent = mom.Entity.Entity(mom.Monitor.Monitor(cfg, name))
    ent.statistics.append(data)
    ent.monitor.fields = set(ent.statistics[-1].keys())
    ent.monitor.optional_fields = []
    ent._finalize()

    return ent


def list_controls(host_data, vm_data, policy_files):
    p = mom.Policy.Policy.Policy()

    host = prepare_entity("host", host_data)

    vm = prepare_entity("vm", vm_data)

    for policy_file in policy_files:
        load_policy_file(p, policy_file)

    p.evaluate(host, [vm])
    print(json.dumps(vm.controls))


def main():
    if len(sys.argv) < 3:
        usage()
        return 1

    host_data = json.loads(sys.argv[1])
    vm_data = json.loads(sys.argv[2])
    policy_files = sys.argv[3:]
    list_controls(host_data, vm_data, policy_files)


if __name__ == '__main__':
    sys.exit(main())
