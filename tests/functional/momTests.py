# SPDX-FileCopyrightText: 2012 IBM Corp.
# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division
import random
import time
from functools import wraps
import os
import errno
from collections import namedtuple
from math import floor
from math import ceil

import testValidation
from testlib import VdsmTestCase as TestCaseBase

import pytest

from vdsm.common.define import errCode
from .utils import getProxy, SUCCESS


def skipNoMOM(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        status, msg, info = self.s.getVdsCapabilities()
        self.assertEqual(status, SUCCESS)
        if not info['packages2'].get('mom'):
            pytest.skip('MOM is not installed')
        return method(self, *args, **kwargs)
    return wrapped


class MOMTest(TestCaseBase):
    # Define the initial, low and high value of shrink and grow operation.
    # Initial is the 'balloon_cur' value before the operation performed.
    # (low, high) is the proper range for 'balloon_cur' after the
    # operation. This range is calculated according to initial value,
    # expected value and adjustment step in policy.
    # This range also takes accuracy impact into account(The number is
    # rounded to integer).
    BalloonRatio = namedtuple('BalloonRatio', 'initial, low, high')

    def setUp(self):
        self.s = getProxy()

    @testValidation.ValidateRunningAsRoot
    @skipNoMOM
    def testKSM(self):
        run = 1
        pages_to_scan = random.randint(100, 200)

        # Set a simple MOM policy to change KSM parameters unconditionally.
        testPolicyStr = """
            (Host.Control "ksm_run" %d)
            (Host.Control "ksm_pages_to_scan" %d)""" % \
            (run, pages_to_scan)
        status, msg = self.s.setMOMPolicy(testPolicyStr)
        self.assertEqual(status, SUCCESS, msg)

        # Wait for the policy taking effect
        time.sleep(10)

        status, msg, hostStats = self.s.getVdsStats()
        self.assertEqual(bool(run), hostStats['ksmState'])
        self.assertEqual(pages_to_scan, hostStats['ksmPages'])

    def _statsOK(self, stats):
        try:
            return stats['status'] == 'Up' and stats['balloonInfo'] \
                and stats['memoryStats']
        except KeyError:
            return False

    def _prepare(self, balloonRatio):
        # Get vms' statistics before the operation.
        status, msg, statsList = self.s.getAllVmStats()
        self.assertEqual(status, SUCCESS, msg)

        # Filter all vms' statistics to get balloon operation candidates.
        candidateStats = [s for s in statsList if self._statsOK(s)]

        # Set the balloon target to initial value before shrink
        # or grow operation.
        # The initial value is max for shrink operation and
        # 0.95*max for grow operation.
        for stats in candidateStats:
            initial = int(stats['balloonInfo']['balloon_max']) * \
                balloonRatio.initial
            if int(stats['balloonInfo']['balloon_cur']) != initial:
                status, msg = self.s.setBalloonTarget(
                    stats['vmId'],
                    initial)
                self.assertEqual(status, SUCCESS, msg)
        return [stats['vmId'] for stats in candidateStats]

    def _setCpuTune(self, vcpuQuota, vcpuPeriod):
        # Get vms' statistics before the operation.
        status, msg, statsList = self.s.getAllVmStats()
        self.assertEqual(status, SUCCESS, msg)

        # Filter all vms' statistics to get balloon operation candidates.
        candidateStats = [s for s in statsList if self._statsOK(s)]

        # Set the balloon target to initial value before shrink
        # or grow operation.
        # The initial value is max for shrink operation and
        # 0.95*max for grow operation.
        for stats in candidateStats:
            status, msg = self.s.setCpuTuneQuota(
                stats['vmId'],
                vcpuQuota)
            self.assertEqual(status, SUCCESS, msg)

            status, msg = self.s.setCpuTunePeriod(
                stats['vmId'],
                vcpuPeriod)
            self.assertEqual(status, SUCCESS, msg)

    def _setPolicy(self, policy):
        curpath = os.path.dirname(__file__)
        file_name = os.path.join(curpath, policy)
        try:
            with open(file_name, 'r') as f:
                testPolicyStr = f.read()
        except IOError as e:
            if e.errno == errno.ENOENT:
                pytest.skip('The policy file %s is missing.' % file_name)
            else:
                pytest.skip(str(e))

        status, msg = self.s.setMOMPolicy(testPolicyStr)
        self.assertEqual(status, SUCCESS, msg)

    def _checkResult(self, vmCandidates, balloonRatio):
        # Check the new balloon_cur in the proper range.
        for vmId in vmCandidates:
            r = self.s.getVmStats(vmId)
            if len(r) == 2:
                status, msg = r
            else:
                status, msg, vmNewStats = r

            # Vm doesn't exist.
            if status == errCode['noVM']['status']['code']:
                continue
            else:
                self.assertEqual(status, SUCCESS, msg)
                if self._statsOK(vmNewStats):
                    balloonMax = int(vmNewStats['balloonInfo']['balloon_max'])
                    balloonCur = int(vmNewStats['balloonInfo']['balloon_cur'])
                    self.assertTrue(
                        balloonCur >= floor(balloonRatio.low * balloonMax))
                    self.assertTrue(
                        balloonCur <= ceil(balloonRatio.high * balloonMax))

    def _basicBalloon(self, balloonRatio, policy):
        vmCandidates = self._prepare(balloonRatio)

        if not vmCandidates:
            pytest.skip('No VM can be candidate of ballooning operation.')

        # Set policy to trigger the balloon operation.
        self._setPolicy(policy)
        # Wait for the policy taking effect.
        time.sleep(22)

        self._checkResult(vmCandidates, balloonRatio)

    @testValidation.ValidateRunningAsRoot
    @skipNoMOM
    @testValidation.slowtest
    def testBalloonShrink(self):
        self._basicBalloon(self.BalloonRatio(1, 0.9475, 0.95),
                           '60_test_balloon_shrink.policy')

    @testValidation.ValidateRunningAsRoot
    @skipNoMOM
    @testValidation.slowtest
    def testBalloonGrow(self):
        self._basicBalloon(self.BalloonRatio(0.95, 0.9975, 1),
                           '70_test_balloon_grow.policy')

    @testValidation.ValidateRunningAsRoot
    @skipNoMOM
    @testValidation.slowtest
    def testCpuTune(self):
        self._setCpuTune(2000, 10000)
