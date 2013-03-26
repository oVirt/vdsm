from testrunner import VdsmTestCase as TestCaseBase
import supervdsm
import testValidation
import tempfile
from vdsm import utils
import os
import uuid
from vdsm import constants
from storage import misc
from monkeypatch import MonkeyPatch
from time import sleep


@utils.memoized
def getNeededPythonPath():
    testDir = os.path.dirname(__file__)
    base = os.path.dirname(testDir)
    vdsmModPath = os.path.join(base, 'lib')
    vdsmPath = os.path.join(base, 'vdsm')
    cliPath = os.path.join(base, 'client')
    pyPath = "PYTHONPATH=" + ':'.join([base, vdsmPath, cliPath, vdsmModPath])
    return pyPath


def monkeyStart(self):
    self._authkey = str(uuid.uuid4())
    self._log.debug("Launching Super Vdsm")

    superVdsmCmd = [getNeededPythonPath(), constants.EXT_PYTHON,
                    supervdsm.SUPERVDSM,
                    self._authkey, str(os.getpid()),
                    self.pidfile, self.timestamp, self.address,
                    str(os.getuid())]
    misc.execCmd(superVdsmCmd, sync=False, sudo=True)
    sleep(2)


class TestSuperVdsm(TestCaseBase):
    def setUp(self):
        testValidation.checkSudo(['python', supervdsm.SUPERVDSM])
        self._proxy = supervdsm.getProxy()

        # temporary values to run temporary svdsm
        self.pidfd, pidfile = tempfile.mkstemp()
        self.timefd, timestamp = tempfile.mkstemp()
        self.addfd, address = tempfile.mkstemp()

        self._proxy.setIPCPaths(pidfile, timestamp, address)

    def tearDown(self):
        supervdsm.extraPythonPathList = []
        for fd in (self.pidfd, self.timefd, self.addfd):
            os.close(fd)
        self._proxy.kill()  # cleanning old temp files

    @MonkeyPatch(supervdsm.SuperVdsmProxy, '_start', monkeyStart)
    def testIsSuperUp(self):
        self._proxy.ping()  # this call initiate svdsm
        self.assertTrue(self._proxy.isRunning())

    @MonkeyPatch(supervdsm.SuperVdsmProxy, '_start', monkeyStart)
    def testKillSuper(self):
        self._proxy.ping()
        self._proxy.kill()
        self.assertFalse(self._proxy.isRunning())
        self._proxy.ping()  # Launching vdsm after kill
        self.assertTrue(self._proxy.isRunning())

    @MonkeyPatch(supervdsm.SuperVdsmProxy, '_start', monkeyStart)
    def testNoPidFile(self):
        self._proxy.ping()  # svdsm is up
        self.assertTrue(self._proxy.isRunning())
        utils.rmFile(self._proxy.timestamp)
        self.assertRaises(IOError, self._proxy.isRunning)
