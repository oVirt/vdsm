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

import logging
import httplib
import xml.etree.ElementTree as etree

try:
    import cherrypy
except ImportError:
    from nose.plugins.skip import SkipTest
    raise SkipTest('cherrypy module missing. This test is non-compulsory '
                   'until we have python-cherrypy in el6.')

from testrunner import VdsmTestCase as TestCaseBase

import restData

server = None
ip = ''
port = 9823
_fakeret = {}

api_whitelist = ('StorageDomain.Classes', 'StorageDomain.Types',
    'Volume.Formats', 'Volume.Types', 'Volume.Roles', 'Image.DiskTypes')

content_types = ('xml', 'json')


def getFakeAPI():
    """
    Create a Mock API module for testing.  Mock API will return data from
    the _fakeret global variable instead of calling into vdsm.  _fakeret is
    expected to have the following format:

    {
      '<class1>': {
        '<func1>': [ <ret1>, <ret2>, ... ],
        '<func2>': [ ... ],
      }, '<class2>': {
        ...
      }
    }
    """
    class FakeObj(object):
        def __new__(cls, *args, **kwargs):
            return object.__new__(cls)

        def default(self, *args, **kwargs):
            try:
                return _fakeret[self.type][self.lastFunc].pop(0)
            except (KeyError, IndexError):
                raise Exception("No API data avilable for %s.%s" %
                                (self.type, self.lastFunc))

        def __getattr__(self, name):
            # While we are constructing the API module, use the normal getattr
            if 'API' not in sys.modules:
                return object.__getattr__(name)
            self.lastFunc = name
            return self.default

    import sys
    import imp
    from new import classobj

    _API = __import__('API', globals(), locals(), {}, -1)
    _newAPI = imp.new_module('API')

    for obj in ('Global', 'ConnectionRefs', 'StorageDomain', 'Image', 'Volume',
                'Task', 'StoragePool', 'VM'):
        cls = classobj(obj, (FakeObj,), {'type': obj})
        setattr(_newAPI, obj, cls)

    # Apply the whitelist to our version of API
    for name in api_whitelist:
        parts = name.split('.')
        dst_obj = _newAPI
        src_obj = _API
        # Walk the object hierarchy copying each component of the whitelisted
        # attribute from the real API to our fake one
        for obj in parts:
            src_obj = getattr(src_obj, obj)
            try:
                dst_obj = getattr(dst_obj, obj)
            except AttributeError:
                setattr(dst_obj, obj, src_obj)

    # Install our fake API into the module table for use by the whole program
    sys.modules['API'] = _newAPI


def setUpModule():
    """
    Set up the environment for all REST tests:
    1. Override the API so we can program our own return values
    2. Start an embedded cherrypy instance to serve objects
    """
    global server
    getFakeAPI()
    import rest.BindingREST as BindingREST

    log = logging.getLogger('restTests')
    handler = logging.StreamHandler()
    fmt_str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    formatter = logging.Formatter(fmt_str)
    handler.setFormatter(formatter)
    handler.setLevel(logging.ERROR)
    log.addHandler(handler)

    templatePath = '../vdsm/rest/templates'
    server = BindingREST.BindingREST(None, log, ip, port, templatePath)
    # Silence cherrypy messages
    cherrypy.config.update({"environment": "embedded"})
    cherrypy.server.start()


def tearDownModule():
    global server
    server.prepareForShutdown()


class HTTPError(Exception):
    def __init__(self, resp):
        self.status = resp.status
        self.content = resp.read()

    def __str__(self):
        return "HTTPError (%i):\n%s\n" % (self.status, self.content)


def request(path, fmt='json', data=None, method=None):
    headers = {'Content-Type': 'application/%s' % fmt,
               'Accept': 'application/%s' % fmt}
    if method is None:
        if data is None:
            method = 'GET'
        else:
            method = 'POST'
    conn = httplib.HTTPConnection("127.0.0.1", port)
    conn.request(method, path, data, headers)
    resp = conn.getresponse()
    if resp.status >= 400:
        raise HTTPError(resp)
    return resp.read()


class RestTestBase(TestCaseBase):
    def expectAPI(self, obj, meth, retval):
        global _fakeret
        if obj not in _fakeret:
            _fakeret[obj] = {}
        if meth not in _fakeret[obj]:
            _fakeret[obj][meth] = []
        _fakeret[obj][meth].append(retval)

    def programAPI(self, key):
        key += '_apidata'
        for item in getattr(restData, key):
            self.expectAPI(item.obj, item.meth, item.data)

    def clearAPI(self):
        global _fakeret
        _fakeret = {}

    def okret(self):
        return {'status': {'code': 0}}

    def get_request(self, key, fmt):
        key += '_request_' + fmt
        return getattr(restData, key)[1:]

    def check_response(self, key, fmt, resp):
        key += '_response_' + fmt
        # Strip leading newline
        expected = getattr(restData, key)[1:]
        self.assertEquals(expected, resp)

    def assertHTTPError(self, code, fn, *args):
        try:
            fn(*args)
        except HTTPError, e:
            self.assertEquals(code, e.status)
        else:
            self.fail("Expected HTTP error was not raised")


class RestTest(RestTestBase):
    def testRootIndex(self):
        """
        Verify XML and Json representation of the Root resource
        """
        for fmt in content_types:
            self.clearAPI()
            self.programAPI('testRootIndex')
            resp = request("/api", fmt)
            self.check_response('testRootIndex', fmt, resp)

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
        self.assertHTTPError(404, request, "/api/doesnotexist")

    def testWrongMethod(self):
        """
        Using the wrong HTTP method should return HTTP:405
        """
        self.assertHTTPError(405, request, "/api", 'json', 'data')

    def testUnexportedFunction(self):
        """
        Functions not annotated with @cherrypy.exposed are not part of the API
        and requesting those paths should cause HTTP:404
        """
        self.assertHTTPError(404, request, "/api/lookup")

    def testStorageConnectionsIndex(self):
        """
        Verify the representation of the storageconnections collection
        """
        for fmt in content_types:
            self.clearAPI()
            self.programAPI('testStorageConnectionsIndex')
            resp = request("/api/storageconnectionrefs", fmt)
            self.check_response('testStorageConnectionsIndex', fmt, resp)

    def testStorageConnectionAcquire(self):
        """
        Verify a successful response for the storageconnection acquire action
        """
        for fmt in content_types:
            self.clearAPI()
            self.programAPI('testStorageConnectionAcquire')
            req = self.get_request('testStorageConnectionAcquire', fmt)
            resp = request("/api/storageconnectionrefs", fmt, req)
            self.check_response('testStorageConnectionAcquire', fmt, resp)

    def testParseError(self):
        """
        Request parse errors should return HTTP:400
        """
        for fmt in content_types:
            req = "blah;`"
            self.assertHTTPError(400, request,
                                "/api/storageconnectionrefs", fmt, req)

    def testStorageDomainIndex(self):
        """
        Test the representation of a storagedomain resource
        """
        for fmt in content_types:
            self.clearAPI()
            self.programAPI('testStorageDomainIndex')
            uri = "/api/storagedomains/a492dfad-be51-4d3c-afbe-2237ef8b46ce"
            resp = request(uri, fmt)
            self.check_response('testStorageDomainIndex', fmt, resp)

    def testResourceNotFound(self):
        """
        Test the dynamic dispatch code.  A non-existent resource always returns
        HTTP:404, even if the collection is valid
        """
        self.clearAPI()
        self.programAPI('StorageDomain_testResourceNotFound')
        self.assertHTTPError(404, request,
                    "/api/storagedomains/0167a13b-110d-49a9-8f4a-7419c0b153f5")

    def testCannotAccept(self):
        """
        Unsupported Content-type or Accept headers should trigger HTTP:406
        """
        self.clearAPI()
        self.programAPI('StorageDomain_testResourceNotFound')
        self.assertHTTPError(406, request, "/api/storagedomains", 'yaml')

    def testVolumeWalk(self):
        """
        Navigate to a volume through two paths and compare the results
        """
        sdUUID = 'bef7ce5f-b6f3-4c8f-9230-aee006d8c5e4'
        imgUUID = 'bcad9af2-9b16-4d61-abbb-1b3ec604e290'
        volUUID = 'c66957de-983d-412b-90ae-f475bc85c16f'
        uri1 = "/api/storagedomains/%s/volumes/%s" % (sdUUID, volUUID)
        uri2 = "/api/storagedomains/%s/images/%s/volumes/%s" % \
                (sdUUID, imgUUID, volUUID)

        self.clearAPI()
        self.programAPI('testVolumeWalk')
        resp1 = request(uri1)
        self.clearAPI()
        self.programAPI('testVolumeWalk')
        resp2 = request(uri2)
        self.assertEquals(resp1, resp2)

    def testTasksIndex(self):
        """
        Verify the representation of the tasks collection
        """
        for fmt in content_types:
            self.clearAPI()
            self.programAPI('testTasksIndex')
            resp = request("/api/tasks", fmt)
            self.check_response('testTasksIndex', fmt, resp)

    def testInternalError(self):
        """
        Unexpected vdsm API errors trigger HTTP:500
        """
        self.clearAPI()
        self.programAPI('testInternalError')
        self.assertHTTPError(500, request, "/api/tasks", 'xml')

    def testMissingParam(self):
        """
        Requests with missing parameters should trigger HTTP:400
        """
        post_uri_list = [
          "/api/storageconnectionrefs",
          "/api/storagedomains",
          "/api/storagedomains/6c82f2de-b686-41f2-8846-6d4c7174c50e/attach",
          "/api/storagepools",
          "/api/storagepools/connect",
          "/api/storagepools/5aa27616-131e-4dda-b22d-8734805013ca/connect",
          "/api/storagepools/5aa27616-131e-4dda-b22d-8734805013ca/disconnect",
        ]
        delete_uri_list = [
          "/api/storagepools/5aa27616-131e-4dda-b22d-8734805013ca",
        ]
        req = self.get_request('testMissingParam', 'xml')
        for uri in post_uri_list:
            self.clearAPI()
            self.programAPI('testMissingParam')
            self.assertHTTPError(400, request, uri, 'xml', req, 'POST')
        for uri in delete_uri_list:
            self.clearAPI()
            self.programAPI('testMissingParam')
            self.assertHTTPError(400, request, uri, 'xml', req, 'DELETE')

    def testDeleteContent(self):
        """
        For DELETE requests with no parameters, a request body is optional.
        """
        uri = '/api/storagedomains/146095d9-b53b-4cf6-81e5-d6497cedde09'
        self.clearAPI()
        self.programAPI('testDeleteContent')
        request(uri, 'json', None, 'DELETE')
        self.clearAPI()
        self.programAPI('testDeleteContent')
        req = self.get_request('testDeleteContent', 'json')
        request(uri, 'json', req, 'DELETE')
