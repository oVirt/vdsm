from __future__ import absolute_import
from __future__ import division

import json
import os
import unittest

import six

from vdsm.common.compat import subprocess
from testValidation import skipif

MOM_POLICY_VALIDATOR = 'mom_policy_validator.py'


def setupModule():
    if not os.path.exists(MOM_POLICY_VALIDATOR):
        raise unittest.case.SkipTest()


@skipif(six.PY3, "mom is not available for python 3 yet")
def read_vm_controls(host_data, vm_data, *policy_files):
    cmd = [
        'python', MOM_POLICY_VALIDATOR,
        json.dumps(host_data),
        json.dumps(vm_data),
    ]
    cmd.extend(
        os.path.join('../static/etc/vdsm/mom.d/', pfile)
        for pfile in policy_files)
    out = subprocess.check_output(cmd)
    return json.loads(out)


class MomPolicyTests(unittest.TestCase):

    def testCpuTuneBasicTest(self):
        controls = read_vm_controls(
            {"cpu_count": 1},
            {
                "vcpu_count": 1,
                "vcpu_user_limit": 50,
                "vcpu_quota": None,
                "vcpu_period": None,
            },
            "00-defines.policy",
            "04-cputune.policy",
        )

        self.assertEqual(controls["vcpu_quota"], 50000)
        self.assertEqual(controls["vcpu_period"], 100000)

    def testCpuTuneHundredCpus(self):
        controls = read_vm_controls(
            {"cpu_count": 120},
            {
                "vcpu_count": 100,
                "vcpu_user_limit": 50,
                "vcpu_quota": None,
                "vcpu_period": None,
            },
            "00-defines.policy",
            "04-cputune.policy",
        )

        self.assertEqual(controls["vcpu_quota"], 60000)
        self.assertEqual(controls["vcpu_period"], 100000)

    def testCpuTuneNoLimit(self):
        controls = read_vm_controls(
            {"cpu_count": 120},
            {
                "vcpu_count": 100,
                "vcpu_user_limit": 100,
                "vcpu_quota": None,
                "vcpu_period": None,
            },
            "00-defines.policy",
            "04-cputune.policy",
        )
        self.assertEqual(controls["vcpu_quota"], -1)
        self.assertEqual(controls["vcpu_period"], 100000)

    def testCpuTuneTooSmall(self):
        controls = read_vm_controls(
            {"cpu_count": 1},
            {
                "vcpu_count": 100,
                "vcpu_user_limit": 10,
                "vcpu_quota": None,
                "vcpu_period": None,
            },
            "00-defines.policy",
            "04-cputune.policy",
        )
        self.assertEqual(controls["vcpu_quota"], 1100)
        self.assertEqual(controls["vcpu_period"], 1100000)
