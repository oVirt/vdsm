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
from testrunner import VdsmTestCase as TestCaseBase
import storage.persistentDict as persistentDict


class DummyFailWriter(object):

    def writelines(self, lines):
        raise RuntimeError("You might have a very minor case of "
                           "serious brain damage")
        # (C) Valve - Portal 2

    def readlines(self):
        data = """Edward Tattsyrup: The time has come to find him a mate!
                  Tubbs Tattsyrup: A no-tail? But where will we get one?
                  Edward Tattsyrup: [grabs an animal trap]
                                    Leave it to me, Tubbs!
                                    I...have a way with women!"""
        # (C) BBC - The League of Gentlemen
        lines = data.splitlines()
        return dict(zip([str(i) for i in range(len(lines))], lines))


class DummyWriter(object):
    def __init__(self):
        self.lines = []

    def readlines(self):
        return self.lines[:]

    def writelines(self, lines):
        self.lines = lines[:]


class SpecialError (RuntimeError):
    pass


class PersistentDictTests(TestCaseBase):
    def testFailedWrite(self):
        data = "Scotty had a will of her own, which was always " + \
               "dangerous in a woman."
        # (C) Philip K. Dick - The Three Stigmata of Palmer Eldritch
        pd = persistentDict.PersistentDict(DummyFailWriter())
        self.assertRaises(RuntimeError, pd.__setitem__, "4", data)

    def testFailedNestedTransaction(self):
        pd = persistentDict.PersistentDict(DummyFailWriter())
        try:
            with pd.transaction():
                with pd.transaction():
                    raise SpecialError("Take the Kama Sutra. How many people "
                                       "died from the Kama Sutra, as opposed "
                                       "to the Bible? Who wins?")
                    # (C) Frank Zappa
        except RuntimeError:
            return

        self.fail("Exception was not thrown")
