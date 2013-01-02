# Copyright 2012 IBM Corporation.
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
import os.path
import vdsmapi
from vdsm import constants


def findSchema():
    """
    Find the API schema file whether we are running tests from the source dir
    or from the tests install location
    """
    scriptdir = os.path.dirname(__file__)
    localpath = os.path.join(scriptdir, '../vdsm_api/vdsmapi-schema.json')
    installedpath = os.path.join(constants.P_VDSM, 'vdsmapi-schema.json')
    for f in localpath, installedpath:
        if os.access(f, os.R_OK):
            return f
    raise Exception("Unable to find schema in %s or %s" % (localpath,
                                                           installedpath))


class SchemaTest(TestCaseBase):
    def setUp(self):
        self.schema = findSchema()

    def testSchemaParse(self):
        with open(self.schema) as f:
            vdsmapi.parse_schema(f)
