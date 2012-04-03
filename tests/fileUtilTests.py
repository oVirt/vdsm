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
        data = """It is the four pillars of the male heterosexual psyche.
        We like:  naked women, stockings, lesbians, and Sean Connery best
        as James Bond, because that is what being a boy is."""
        # (C) BBC - Coupling
        srcFd, srcPath = tempfile.mkstemp()
        f = os.fdopen(srcFd, "wb")
        f.write(data)
        f.flush()
        f.close()
        with fileUtils.open_ex(srcPath, "dr") as f:
            self.assertEquals(f.read(), data)
        os.unlink(srcPath)

    def testSeekRead(self):
        data = """I want to spend the rest of my life with the woman at the end
        of that table there, but that does not stop me wanting to see several
        thousand more naked bottoms before I die, because that's what being a
        bloke is. When man invented fire, he didn't say, "Hey, let's cook." He
        said, "Great, now we can see naked bottoms in the dark."
        As soon as Caxton invented the printing press, we were using it to make
        pictures of, hey, naked bottoms!  We have turned the Internet into an
        enormous international database of naked bottoms. So you see, the story
        of male achievement through the ages, feeble though it may have been,
        has been the story of our struggle to get a better look at your
        bottoms."""
        # (C) BBC - Coupling
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
        data = """You know, I have never understood the male obsession with
        lesbianism, a whole area of sex with nothing for them to do.
        Just answered my own question, haven't I?"""
        # (C) BBC - Coupling
        srcFd, srcPath = tempfile.mkstemp()
        os.close(srcFd)
        with fileUtils.open_ex(srcPath, "dw") as f:
            f.write(data)

        with fileUtils.open_ex(srcPath, "r") as f:
            self.assertEquals(f.read(len(data)), data)
        os.unlink(srcPath)

    def testSmallWrites(self):
        data = """Do you know what arses are? Arses are the human race's
        favourite thing. We like them on each other, we like them on magazine
        covers, we even like them on babies!  When it itches, we like to
        scratch them, when its cold, we like to warm them, and who among us, in
        a lonely moment hasn't reached back for a discreet fondle?  When God
        gave us our arses he had to stick them round the back just so we
        wouldn't sit and stare at them all day. Cause when God made the arse he
        didn't say "Hey it's just your basic hinge, let's knock off early." He
        said "Behold ye angels, I have created the arse. Throughout the ages to
        come, men and women shall grab hold of these and shout my name!"""
        # (C) BBC - Coupling
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
        data = """Cat: Hey hey hey, I've got you now,
                       buddy! J, O, Z, X, Y, Q, K!
                  Lister: That's not a word.
                  Cat: It's a Cat word.
                  Lister: Jozxyqk?
                  Cat: That's not how you pronounce it!
                  Lister: What does it mean?
                  Cat: It's the sound you make when you get your sexual organs
                       trapped in something.
                  Lister: Is it in the dictionary?
                  Cat: Well it could be, if you're reading in the nude and
                       close the book too quick. Jozxyqk!!!
                  -------------------------------------------------------------
                  Cat: Forget Red - let's go all the way up to Brown Alert!
                  Kryten: There's no such thing as a Brown Alert, sir.
                  Cat: You won't be saying that in a minute! And don't say I
                       didn't alert you!"""
        # (C) BBC - Red Dwarf
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
