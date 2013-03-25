#
# Copyright 2012 Red Hat, Inc.
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
import tempfile
import os

import storage.fileUtils as fileUtils
import testValidation
from testrunner import VdsmTestCase as TestCaseBase


class DirectFileTests(TestCaseBase):
    @classmethod
    def getConfigTemplate(cls):
        return {}

    def testRead(self):
        data = """Vestibulum. Libero leo nostra, pede nunc eu. Pellentesque
        platea lacus morbi nisl montes ve. Ac. A, consectetuer erat, justo eu.
        Elementum et, phasellus fames et rutrum donec magnis eu bibendum. Arcu,
        ante aliquam ipsum ut facilisis ad."""
        srcFd, srcPath = tempfile.mkstemp()
        f = os.fdopen(srcFd, "wb")
        f.write(data)
        f.flush()
        f.close()
        with fileUtils.open_ex(srcPath, "dr") as f:
            self.assertEquals(f.read(), data)
        os.unlink(srcPath)

    def testSeekRead(self):
        data = """
        Habitasse ipsum at fusce litora metus, placerat dui purus aenean ante,
        ve. Pede hymenaeos ut primis cum, rhoncus, lectus, nunc. Vestibulum
        curabitur vitae etiam magna auctor velit, mi tempus vivamus orci eros.
        Pellentesque curabitur risus fermentum eget. Elementum curae, donec
        nisl egestas ve, ut odio eu nunc elit felis primis id. Ridiculus metus
        morbi nulla erat, amet nisi. Amet ligula nisi, id penatibus risus in.
        Purus velit duis. Aenean eget, pellentesque eu rhoncus arcu et
        consectetuer laoreet, augue nisi dictum lacinia urna. Fermentum
        torquent. Ut interdum vivamus duis. Felis consequat nec pede. Orci
        sollicitudin parturient orci felis. Enim, diam velit sapien
        condimentum fames semper nibh. Integer at, egestas pede consectetuer
        ac augue pharetra dolor non placerat quisque id cursus ultricies.
        Ligula mi senectus sit. Habitasse. Integer sollicitudin dapibus cum
        quam.
        """
        self.assertTrue(len(data) > 512)
        srcFd, srcPath = tempfile.mkstemp()
        f = os.fdopen(srcFd, "wb")
        f.write(data)
        f.flush()
        f.close()
        with fileUtils.open_ex(srcPath, "dr") as f:
            f.seek(512)
            self.assertEquals(f.read(), data[512:])
        os.unlink(srcPath)

    def testWrite(self):
        data = """In ut non platea egestas, quisque magnis nunc nostra ac etiam
        suscipit nec integer sociosqu. Fermentum. Ante orci luctus, ipsum
        ullamcorper enim arcu class neque inceptos class. Ut, sagittis
        torquent, commodo facilisi."""
        srcFd, srcPath = tempfile.mkstemp()
        os.close(srcFd)
        with fileUtils.open_ex(srcPath, "dw") as f:
            f.write(data)

        with fileUtils.open_ex(srcPath, "r") as f:
            self.assertEquals(f.read(len(data)), data)
        os.unlink(srcPath)

    def testSmallWrites(self):
        data = """
        Aliquet habitasse tellus. Fringilla faucibus tortor parturient
        consectetuer sodales, venenatis platea habitant. Hendrerit nostra nunc
        odio. Primis porttitor consequat enim ridiculus. Taciti nascetur,
        nibh, convallis sit, cum dis mi. Nonummy justo odio cursus, ac hac
        curabitur nibh. Tellus. Montes, ut taciti orci ridiculus facilisis
        nunc. Donec. Risus adipiscing habitant donec vehicula non vitae class,
        porta vitae senectus. Nascetur felis laoreet integer, tortor ligula.
        Pellentesque vestibulum cras nostra. Ut sollicitudin posuere, per
        accumsan curabitur id, nisi fermentum vel, eget netus tristique per,
        donec, curabitur senectus ut fusce. A. Mauris fringilla senectus et
        eni facilisis magna inceptos eu, cursus habitant fringilla neque.
        Nibh. Elit facilisis sed, elit, nostra ve torquent dictumst, aenean
        sapien quam, habitasse in. Eu tempus aptent, diam, nisi risus
        pharetra, ac, condimentum orci, consequat mollis. Cras lacus augue
        ultrices proin fermentum nibh sed urna. Ve ipsum ultrices curae,
        feugiat faucibus proin et elementum vivamus, lectus. Torquent. Tempus
        facilisi. Cras suspendisse euismod consectetuer ornare nostra. Fusce
        amet cum amet diam.
        """
        self.assertTrue(len(data) > 512)

        srcFd, srcPath = tempfile.mkstemp()
        os.close(srcFd)
        with fileUtils.open_ex(srcPath, "dw") as f:
            f.write(data[:512])
            f.write(data[512:])

        with fileUtils.open_ex(srcPath, "r") as f:
            self.assertEquals(f.read(len(data)), data)
        os.unlink(srcPath)

    def testUpdateRead(self):
        data = """
        Aliquet. Aliquam eni ac nullam iaculis cras ante, adipiscing. Enim
        eget egestas pretium. Ultricies. Urna cubilia in, hac. Curabitur.
        Nibh. Purus ridiculus natoque sed id. Feugiat lacus quam, arcu
        maecenas nec egestas. Hendrerit duis nunc eget dis lacus porttitor per
        sodales class diam condimentum quisque condimentum nisi ligula.
        Dapibus blandit arcu nam non ac feugiat diam, dictumst. Ante eget
        fames eu penatibus in, porta semper accumsan adipiscing tellus in
        sagittis. Est parturient parturient mi fermentum commodo, per
        fermentum. Quis duis velit at quam risus mi. Facilisi id fames.
        Turpis, conubia rhoncus. Id. Elit eni tellus gravida, ut, erat morbi.
        Euismod, enim a ante vestibulum nibh. Curae curae primis vulputate
        adipiscing arcu ipsum suspendisse quam hymenaeos primis accumsan
        vestibulum.
        """
        self.assertTrue(len(data) > 512)

        srcFd, srcPath = tempfile.mkstemp()
        os.close(srcFd)
        with fileUtils.open_ex(srcPath, "wd") as f:
            f.write(data[:512])

        with fileUtils.open_ex(srcPath, "r+d") as f:
            f.seek(512)
            f.write(data[512:])

        with fileUtils.open_ex(srcPath, "r") as f:
            self.assertEquals(f.read(len(data)), data)
        os.unlink(srcPath)


class ChownTests(TestCaseBase):
    @testValidation.ValidateRunningAsRoot
    def test(self):
        targetId = 666
        srcFd, srcPath = tempfile.mkstemp()
        os.close(srcFd)
        fileUtils.chown(srcPath, targetId, targetId)
        stat = os.stat(srcPath)
        self.assertTrue(stat.st_uid == stat.st_gid == targetId)
        os.unlink(srcPath)

    @testValidation.ValidateRunningAsRoot
    def testNames(self):
        # I convert to some id because I have no
        # idea what users are defined and what
        # there IDs are apart from root
        tmpId = 666
        srcFd, srcPath = tempfile.mkstemp()
        os.close(srcFd)
        fileUtils.chown(srcPath, tmpId, tmpId)
        stat = os.stat(srcPath)
        self.assertTrue(stat.st_uid == stat.st_gid == tmpId)

        fileUtils.chown(srcPath, "root", "root")
        stat = os.stat(srcPath)
        self.assertTrue(stat.st_uid == stat.st_gid == 0)


class CopyUserModeToGroupTests(TestCaseBase):
    MODE_MASK = 0777

    # format: initialMode, expectedMode
    modesList = [
        (0770, 0770), (0700, 0770), (0750, 0770), (0650, 0660),
    ]

    def testCopyUserModeToGroup(self):
        fd, path = tempfile.mkstemp()
        try:
            os.close(fd)
            for initialMode, expectedMode in self.modesList:
                os.chmod(path, initialMode)
                fileUtils.copyUserModeToGroup(path)
                self.assertEquals(os.stat(path).st_mode & self.MODE_MASK,
                                  expectedMode)
        finally:
            os.unlink(path)
