#
# Copyright 2012 Adam Litke, IBM Corporation
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

import urllib2
import xml.etree.ElementTree as etree

from testrunner import VdsmTestCase as TestCaseBase
from nose.plugins.skip import SkipTest

from vdsm.config import config

if not config.getboolean('vars', 'rest_enable'):
    raise SkipTest("REST Bindings are disabled")
port = config.getint('addresses', 'rest_port')

content_types = ('xml', 'json')


def request(path, fmt='json', data=None):
    headers = {'Content-type': 'application/%s' % fmt,
               'Accept': 'application/%s' % fmt}
    url = "http://127.0.0.1:%s%s" % (port, path)
    req = urllib2.Request(url, data, headers)
    try:
        response = urllib2.urlopen(req).read()
    except urllib2.HTTPError, e:
        print e.read()
        raise
    return response


class RestTestBase(TestCaseBase):
    def assertHTTPError(self, code, fn):
        try:
            fn()
        except urllib2.HTTPError, e:
            self.assertEquals(code, e.code)
        else:
            self.fail("Expected HTTP error was not raised")


class RestTest(RestTestBase):
    def testRSDL(self):
        """
        Ensure the rsdl and xsd files are present and properly formatted
        """
        resp = request("/api?rsdl")
        self.assertEquals('rsdl', etree.XML(resp).tag)
        resp = request("/api?schema")
        self.assertEquals('{http://www.w3.org/2001/XMLSchema}schema',
                          etree.XML(resp).tag)

    def test404(self):
        """
        A non-existent path should return HTTP:404
        """
        self.assertHTTPError(404, lambda: request("/api/doesnotexist"))

    def testWrongMethod(self):
        """
        Using the wrong HTTP method should return HTTP:405
        """
        self.assertHTTPError(405, lambda: request("/api", 'json', 'data'))

    def testUnexportedFunction(self):
        """
        Functions not annotated with @cherrypy.exposed are not part of the API
        and requesting those paths should cause HTTP:404
        """
        self.assertHTTPError(404, lambda: request("/api/lookup"))
