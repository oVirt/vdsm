# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import contextlib
import os

from vdsm.common import libvirtconnection
from testlib import VdsmTestCase as TestCaseBase
from monkeypatch import MonkeyPatch


class TerminationException(Exception):
    pass


class LibvirtMock(object):
    VIR_CRED_AUTHNAME, \
        VIR_CRED_PASSPHRASE, \
        VIR_FROM_RPC, \
        VIR_FROM_REMOTE, \
        VIR_ERR_SYSTEM_ERROR, \
        VIR_ERR_INTERNAL_ERROR, \
        VIR_ERR_NO_CONNECT, \
        VIR_ERR_INVALID_CONN = list(range(8))

    SOME_ERROR_LEVEL = 3

    class libvirtError(Exception):
        def get_error_code(self):
            return LibvirtMock.VIR_ERR_SYSTEM_ERROR

        def get_error_domain(self):
            return LibvirtMock.VIR_FROM_RPC

        def get_error_level(self):
            return LibvirtMock.SOME_ERROR_LEVEL

        def get_error_message(self):
            return ''

    class virConnect(object):
        failGetLibVersion = False
        failNodeDeviceLookupByName = False

        def nodeDeviceLookupByName(self):
            if LibvirtMock.virConnect.failNodeDeviceLookupByName:

                raise LibvirtMock.libvirtError()
            else:
                return ''

        def getLibVersion(self):
            if LibvirtMock.virConnect.failGetLibVersion:
                raise LibvirtMock.libvirtError()
            else:
                return ''

        def close(self):
            pass

    class virDomain(object):
        pass

    def openAuth(self, *args):
        return LibvirtMock.virConnect()

    class virEventRegisterDefaultImpl(object):
        pass

    def virEventRunDefaultImpl(*args, **kwargs):
        return 0


def _kill(*args):
    raise TerminationException()


@contextlib.contextmanager
def run_libvirt_event_loop():
    libvirtconnection.start_event_loop()
    try:
        yield
    finally:
        libvirtconnection.stop_event_loop()


class testLibvirtconnection(TestCaseBase):

    def tearDown(self):
        libvirtconnection._clear()

    @MonkeyPatch(libvirtconnection, 'libvirt', LibvirtMock())
    @MonkeyPatch(libvirtconnection, 'libvirt_password', lambda: '/dev/null')
    def testCallSucceeded(self):
        """Positive test - libvirtMock does not raise any errors"""
        with run_libvirt_event_loop():
            LibvirtMock.virConnect.failGetLibVersion = False
            LibvirtMock.virConnect.failNodeDeviceLookupByName = False
            connection = libvirtconnection.get()
            connection.nodeDeviceLookupByName()

    @MonkeyPatch(libvirtconnection, 'libvirt', LibvirtMock())
    @MonkeyPatch(os, 'kill', _kill)
    @MonkeyPatch(libvirtconnection, 'libvirt_password', lambda: '/dev/null')
    def testCallFailedConnectionUp(self):
        """
        libvirtMock will raise an error when nodeDeviceLookupByName is called.
        When getLibVersion is called
        (used by libvirtconnection to recognize disconnections)
        it will not raise an error -> in that case an error should be raised
        ('Unknown libvirterror').
        """
        with run_libvirt_event_loop():
            connection = libvirtconnection.get(killOnFailure=True)
            LibvirtMock.virConnect.failNodeDeviceLookupByName = True
            LibvirtMock.virConnect.failGetLibVersion = False
            self.assertRaises(LibvirtMock.libvirtError,
                              connection.nodeDeviceLookupByName)

    @MonkeyPatch(libvirtconnection, 'libvirt', LibvirtMock())
    @MonkeyPatch(os, 'kill', _kill)
    @MonkeyPatch(libvirtconnection, 'libvirt_password', lambda: '/dev/null')
    def testCallFailedConnectionDown(self):
        """
        libvirtMock will raise an error when nodeDeviceLookupByName is called.
        When getLibVersion is called
        (used by libvirtconnection to recognize disconnections)
        it will also raise an error -> in that case os.kill should be called
        ('connection to libvirt broken.').
        """
        with run_libvirt_event_loop():
            connection = libvirtconnection.get(killOnFailure=True)
            LibvirtMock.virConnect.failNodeDeviceLookupByName = True
            LibvirtMock.virConnect.failGetLibVersion = True
            self.assertRaises(TerminationException,
                              connection.nodeDeviceLookupByName)
