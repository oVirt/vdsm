# SPDX-FileCopyrightText: oVirt Developers
# SPDX-License-Identifier: GPL-2.0-or-later

import json
import os
import unittest
import subprocess


MOM_POLICY_VALIDATOR = 'mom_policy_validator.py'


def setupModule():
    if not os.path.exists(MOM_POLICY_VALIDATOR):
        raise unittest.case.SkipTest()


def read_vm_controls(host_data, vm_data, *policy_files):
    cmd = [
        'python',
        MOM_POLICY_VALIDATOR,
        json.dumps(host_data),
        json.dumps(vm_data),
    ]
    cmd.extend(
        os.path.join('../static/etc/vdsm/mom.d/', pfile)
        for pfile in policy_files
    )
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

    def testCpuTuneResetQuotaAlreadyUnlimited(self):
        # vcpu_quota is already at the default (-1), so the reset path must
        # not issue a redundant vcpu_quota Control call.
        controls = read_vm_controls(
            {"cpu_count": 1},
            {
                "vcpu_count": 1,
                "vcpu_user_limit": 100,
                "vcpu_quota": -1,
                "vcpu_period": 100000,
            },
            "00-defines.policy",
            "04-cputune.policy",
        )
        self.assertNotIn("vcpu_period", controls)
        self.assertNotIn("vcpu_quota", controls)

    def testCpuTuneResetQuotaAboveMaxRealQuota(self):
        # vcpu_quota is a large positive "no limit" value (above maxRealQuota,
        # which is defaultPeriod * Host.cpu_count = 100000 * 1), so the reset
        # path must skip the vcpu_quota Control call.
        controls = read_vm_controls(
            {"cpu_count": 1},
            {
                "vcpu_count": 1,
                "vcpu_user_limit": 100,
                "vcpu_quota": 200000,
                "vcpu_period": 100000,
            },
            "00-defines.policy",
            "04-cputune.policy",
        )
        self.assertNotIn("vcpu_period", controls)
        self.assertNotIn("vcpu_quota", controls)

    def testCpuTuneResetQuotaFromRealLimit(self):
        # vcpu_quota holds a real limit (0 < 50000 < maxRealQuota of 100000),
        # so the reset path must reset it back to the default (-1).
        controls = read_vm_controls(
            {"cpu_count": 1},
            {
                "vcpu_count": 1,
                "vcpu_user_limit": 100,
                "vcpu_quota": 50000,
                "vcpu_period": 100000,
            },
            "00-defines.policy",
            "04-cputune.policy",
        )
        self.assertEqual(controls["vcpu_quota"], -1)
        # Value was at the default, so no vcpu_period Control call needed.
        self.assertNotIn("vcpu_period", controls)

    def testCpuTuneResetQuotaAtMaxRealQuota(self):
        # vcpu_quota is exactly at maxRealQuota (defaultPeriod * Host.cpu_count
        # = 100000 * 1), which is the inclusive upper bound of the reset range
        # (<= maxRealQuota), so the reset path must reset it back to the
        # default (-1).
        controls = read_vm_controls(
            {"cpu_count": 1},
            {
                "vcpu_count": 1,
                "vcpu_user_limit": 100,
                "vcpu_quota": 100000,
                "vcpu_period": 100000,
            },
            "00-defines.policy",
            "04-cputune.policy",
        )
        self.assertEqual(controls["vcpu_quota"], -1)
        # Value was at the default, so no vcpu_period Control call needed.
        self.assertNotIn("vcpu_period", controls)

    def testCpuTuneResetPeriodFromNonDefault(self):
        # vcpu_period holds a non-default value (1100000 != 100000), so the
        # reset path must reset it back to the default (100000). vcpu_quota is
        # already at the default (-1), so no vcpu_quota Control call is needed.
        controls = read_vm_controls(
            {"cpu_count": 1},
            {
                "vcpu_count": 1,
                "vcpu_user_limit": 100,
                "vcpu_quota": -1,
                "vcpu_period": 1100000,
            },
            "00-defines.policy",
            "04-cputune.policy",
        )
        self.assertEqual(controls["vcpu_period"], 100000)
        self.assertNotIn("vcpu_quota", controls)

    def testCpuTuneResetQuotaFromZero(self):
        # vcpu_quota is 0, vdsm's "unset" representation. It is valid and
        # within the reset range (0 != -1 and 0 <= maxRealQuota), so the reset
        # path must reset it back to the default (-1).
        controls = read_vm_controls(
            {"cpu_count": 1},
            {
                "vcpu_count": 1,
                "vcpu_user_limit": 100,
                "vcpu_quota": 0,
                "vcpu_period": 100000,
            },
            "00-defines.policy",
            "04-cputune.policy",
        )
        self.assertEqual(controls["vcpu_quota"], -1)
        # Value was at the default, so no vcpu_period Control call needed.
        self.assertNotIn("vcpu_period", controls)

    def testCpuTuneResetQuotaFromInvalid(self):
        # vcpu_quota is None (invalid/unset), so the reset path takes the
        # else-branch and unconditionally resets it back to the default (-1).
        controls = read_vm_controls(
            {"cpu_count": 1},
            {
                "vcpu_count": 1,
                "vcpu_user_limit": 100,
                "vcpu_quota": None,
                "vcpu_period": 100000,
            },
            "00-defines.policy",
            "04-cputune.policy",
        )
        self.assertEqual(controls["vcpu_quota"], -1)
        # Value was at the default, so no vcpu_period Control call needed.
        self.assertNotIn("vcpu_period", controls)
