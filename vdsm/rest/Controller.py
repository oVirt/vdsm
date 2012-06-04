# VDSM REST API
# Copyright (C) 2012 Adam Litke, IBM Corporation

#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public
# License along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA

import json
import cherrypy
from Cheetah.Template import Template
import xml.etree.ElementTree as etree
import API


def mime_in_header(header, mime):
    if not header in cherrypy.request.headers:
        accepts = 'application/xml'
    else:
        accepts = cherrypy.request.headers[header]

    if accepts.find(';') != -1:
        accepts, _ = accepts.split(';', 1)

    if mime in accepts.split(','):
        return True

    return False


def validate_method(allowed):
    method = cherrypy.request.method.upper()
    if method not in allowed:
        raise cherrypy.HTTPError(405)
    return method


def render_template(ctx, filename, data):
    if mime_in_header('Accept', 'application/xml'):
        cherrypy.response.headers['Content-Type'] = 'application/xml'
        return Template(file='%s/%s.xml.x' % (ctx.templatePath, filename),
                        searchList=[data]).respond()
    elif mime_in_header('Accept', 'application/json'):
        cherrypy.response.headers['Content-Type'] = 'application/json'
        return Template(file='%s/%s.json.x' % (ctx.templatePath, filename),
                        searchList=[data]).respond()
    else:
        raise cherrypy.HTTPError(406, "This API only supports "
                                 "'application/xml' and 'application/json'")


def render_file(ctx, filename):
    path = "%s/%s" % (ctx.templatePath, filename)
    return open(path).read()


def parse_request():
    def children_to_dict(children):
        """
        Compact an array of children into a dictionary.  Key collisions are
        resolved by creating an array within the dictionary that is indexed by
        the pluralized form of the colliding key name: eg

        [ { 'bar': 'baz' },                 { 'bar': 'baz',
          { 'bif': 'diy' },             -->   'bif': 'diy',
          { 'dup': 1 }, { 'dup': 2 } ]        'dups': [ 1, 2 ] }

        Preconditions: Each list item is a dictionary with a single key.

        This conversion allows XML and JSON requests to have the same internal
        representation.
        """
        ret = {}

        # First identify the colliding keys
        key_counts = {}
        for k in [d.keys()[0] for d in children if type(d) is dict]:
            key_counts[k] = key_counts.get(k, 0) + 1

        # Now build the return value
        for d in children:
            if len(d) != 1 or type(d) is not dict:
                raise ValueError("Child must be a dict with exactly one key")
            key = d.keys()[0]
            if key_counts[key] == 1:
                # Just insert unique keys directly
                ret[key] = d[key]
            else:
                # For duplicate keys, create an array indexed by a pluralized
                # form of the original key name.
                plural_key = key + 's'
                if plural_key in ret:
                    ret[plural_key].append(d[key])
                else:
                    ret[plural_key] = [d[key]]
        return ret

    def xml_to_dict(element):
        node = {}
        if element.text:
            node[element.tag] = element.text
        else:
            node[element.tag] = {}
            node[element.tag].update(element.items())  # Attributes
        children = element.getchildren()
        if children:
            node[element.tag] = children_to_dict(map(xml_to_dict, children))
        return node

    rawbody = cherrypy.request.body.read()
    if mime_in_header('Content-Type', 'application/xml'):
        try:
            doc = etree.XML(rawbody)
        except etree.ParseError:
            raise cherrypy.HTTPError(400, "Unable to parse XML request")
        # Return the contents of the root element
        return xml_to_dict(doc).values()[0]
    elif mime_in_header('Content-Type', 'application/json'):
        try:
            return json.loads(rawbody)
        except ValueError:
            raise cherrypy.HTTPError(400, "Unable to parse JSON request")
    else:
        raise cherrypy.HTTPError(406, "This API only supports "
                                 "'application/xml' and 'application/json'")


class RestException(Exception):
    def __init__(self, result):
        self.code = result['status']['code']
        self.message = result['status']['message']

    def __repr__(self):
        return 'RestException: code:%i, message:"%s"' \
               % (self.code, self.message)

    def __str__(self):
        return self.__repr__()


def vdsOK(ctx, d, ignore_errors=[]):
    ctx.log.debug(d)
    if d['status']['code'] and d['status']['code'] not in ignore_errors:
        raise RestException(d)
    else:
        return d


class ContextManager:
    def __init__(self, cif, log, templatePath):
        self.cif = cif
        self.log = log
        self.templatePath = templatePath

        # XXX: hostID should either be set in the config file or by an API call
        self.hostID = 1


class Response(object):
    def __init__(self, ctx, retval):
        self.ctx = ctx
        self.retval = retval

    def render(self):
        self.code = self.retval['status']['code']
        self.msg = self.retval['status']['message']
        self.detail = repr(self.retval)
        self.task = self.retval.get('uuid', None)
        return render_template(self.ctx, 'response', {'resource': self})


class Resource(object):
    def __init__(self, ctx):
        self.ctx = ctx
        self._links = {}

    @cherrypy.expose
    def index(self):
        validate_method(('GET',))
        self.lookup()
        return render_template(self.ctx, self.template, {'resource': self})

    def __call__(self):
        pass

    def _dispatch_lookup(self, link):
        return self._links.get(link, lambda: None)()


class Collection(object):
    def __init__(self, ctx):
        self.ctx = ctx

    @cherrypy.expose
    def index(self):
        validate_method(('GET',))
        resources = self._get_resources()
        for obj in resources:
            obj.lookup()
        return render_template(self.ctx, self.template,
                               {'collection': self, 'resources': resources})

    def __call__(self):
        pass

    def _dispatch_lookup(self, uuid):
        """
        This is the custom cherrypy dispatcher hook used by objects that can
        support dynamic lookup.
        """
        if hasattr(self, uuid):
            return None
        try:
            return self._get_resources(uuid)[0]
        except IndexError:
            return None


class Root(Resource):
    def __init__(self, cif, log, templatePath):
        ctx = ContextManager(cif, log, templatePath)
        Resource.__init__(self, ctx)
        self.template = 'root'

    def lookup(self):
        api = API.Global(self.ctx.cif)
        ret = api.getCapabilities()
        vdsOK(self.ctx, ret)
        vers = ret['info']['software_version'].split('.')
        try:
            build = vers[2]
        except IndexError:
            build = 0
        rev = ret['info']['software_revision']
        self.product_info = {
            'name': 'vdsm',
            'vendor': 'oVirt',
            'version': {
                'major': vers[0],
                'minor': vers[1],
                'build': build,
                'revision': rev
            }
        }

    @cherrypy.expose
    def index(self, *args, **kwargs):
        validate_method(('GET',))
        if 'rsdl' in kwargs:
            return render_file(self.ctx, 'rsdl.xml')
        elif 'schema' in kwargs:
            return render_file(self.ctx, 'api.xsd')
        else:
            self.lookup()
            return render_template(self.ctx, self.template, {'resource': self})
