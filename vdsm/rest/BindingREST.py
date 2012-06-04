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

import threading
import cherrypy

from Dispatcher import vdsm_cpDispatcher
import Controller


def delete_no_body():
    """
    Since we set request.methods_with_bodies to include the DELETE method,
    cherrypy expects all DELETE requests to include a body.  We want to make
    content optional.  To do this, we install this function as a hook in the
    before_request_body stage.  All we do is disable body processing if there
    is no content.
    """
    if cherrypy.request.method.upper() != 'DELETE':
        return
    if 'Content-Length' not in cherrypy.request.headers:
        cherrypy.request.process_request_body = False


class BindingREST:
    def __init__(self, cif, log, ip, port, templatePath):
        self.cif = cif
        self.log = log
        self.serverPort = port
        self.templatePath = templatePath
        if not ip:
            self.serverIP = '0.0.0.0'
        else:
            self.serverIP = ip
        self._create_rest_server()

    def start(self):
        def threaded_start():
            cherrypy.engine.start()
            cherrypy.engine.autoreload.stop()
            cherrypy.engine.autoreload.unsubscribe()
            cherrypy.engine.block()
        threading.Thread(target=threaded_start,
                         name='cherryPy').start()

    def _create_rest_server(self):
        cherrypy.server.socket_host = self.serverIP
        cherrypy.server.socket_port = self.serverPort
        d = vdsm_cpDispatcher()
        cherrypy.tools.delete_no_body = cherrypy.Tool('before_request_body',
                                                      delete_no_body)
        conf = {'/': {'request.dispatch': d,
                      'engine.autoreload.on': False,
                      'tools.trailing_slash.on': False,
                      'tools.delete_no_body.on': True,
                      'request.methods_with_bodies':
                          ('POST', 'PUT', 'DELETE')}}
        obj = Controller.Root(self.cif, self.log, self.templatePath)
        cherrypy.tree.mount(obj, '/api', config=conf)

    def prepareForShutdown(self):
        cherrypy.engine.stop()
