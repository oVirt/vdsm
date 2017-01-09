import os.path
from six.moves import configparser

from mom.Policy.Policy import Policy
from mom.Entity import Entity
from mom.Monitor import Monitor
from unittest import TestCase

# This is a very hacky way of implementing the test scenario
# we should update MOM to offer test capability and use it here.
# Unfortunately bug #1207610 has very high priority and won't
# wait for MOM to appear in Fedora repositories
# TODO replace with proper test API once it appears


class MomPolicyTests(TestCase):
    def _getPolicyContent(self, name):
        path = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                            "../static/etc/vdsm/mom.d",
                            name)
        return open(path, "r").read()

    def _loadPolicyFile(self, policy, filename):
        """Load MOM policy from static/etc/vdsm/mom.d/+filename and apply it
           under the 'basename without extension' policy name.

           Example:
           00-constants.policy is loaded from
           static/etc/vdsm/mom.d/00-constants.policy
           and inserted as 00-costants policy.
        """

        policy_string = self._getPolicyContent(filename)
        policy_name = os.path.splitext(os.path.basename(filename))[0]
        self.assertTrue(policy.set_policy(policy_name, policy_string))

    def _prepareEntity(self, name, data):
        cfg = configparser.SafeConfigParser()
        cfg.add_section("__int__")
        cfg.set("__int__", "plot-subdir", "")

        ent = Entity(Monitor(cfg, name))
        ent.statistics.append(data)
        ent.monitor.fields = set(ent.statistics[-1].keys())
        ent.monitor.optional_fields = []
        ent._finalize()

        return ent

    def testCpuTuneBasicTest(self):
        p = Policy()

        host = self._prepareEntity("host", {
            "cpu_count": 1
        })

        vm = self._prepareEntity("vm", {
            "vcpu_count": 1,
            "vcpu_user_limit": 50,

            "vcpu_quota": None,
            "vcpu_period": None
        })

        self._loadPolicyFile(p, "00-defines.policy")
        self._loadPolicyFile(p, "04-cputune.policy")

        p.evaluate(host, [vm])

        self.assertEqual(vm.controls["vcpu_quota"], 50000)
        self.assertEqual(vm.controls["vcpu_period"], 100000)

    def testCpuTuneHundredCpus(self):
        p = Policy()

        host = self._prepareEntity("host", {
            "cpu_count": 120
        })

        vm = self._prepareEntity("vm", {
            "vcpu_count": 100,
            "vcpu_user_limit": 50,

            "vcpu_quota": None,
            "vcpu_period": None
        })

        self._loadPolicyFile(p, "00-defines.policy")
        self._loadPolicyFile(p, "04-cputune.policy")

        p.evaluate(host, [vm])

        self.assertEqual(vm.controls["vcpu_quota"], 60000)
        self.assertEqual(vm.controls["vcpu_period"], 100000)

    def testCpuTuneNoLimit(self):
        p = Policy()

        host = self._prepareEntity("host", {
            "cpu_count": 120
        })

        vm = self._prepareEntity("vm", {
            "vcpu_count": 100,
            "vcpu_user_limit": 100,

            "vcpu_quota": None,
            "vcpu_period": None
        })

        self._loadPolicyFile(p, "00-defines.policy")
        self._loadPolicyFile(p, "04-cputune.policy")

        p.evaluate(host, [vm])

        self.assertEqual(vm.controls["vcpu_quota"], -1)
        self.assertEqual(vm.controls["vcpu_period"], 100000)

    def testCpuTuneTooSmall(self):
        p = Policy()

        host = self._prepareEntity("host", {
            "cpu_count": 1
        })

        vm = self._prepareEntity("vm", {
            "vcpu_count": 100,
            "vcpu_user_limit": 10,

            "vcpu_quota": None,
            "vcpu_period": None
        })

        self._loadPolicyFile(p, "00-defines.policy")
        self._loadPolicyFile(p, "04-cputune.policy")

        p.evaluate(host, [vm])

        self.assertEqual(vm.controls["vcpu_quota"], 1100)
        self.assertEqual(vm.controls["vcpu_period"], 1100000)
