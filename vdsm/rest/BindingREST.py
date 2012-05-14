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
        conf = {'/': {'request.dispatch': d,
                      'engine.autoreload.on': False,
                      'tools.trailing_slash.on': False}}
        obj = Controller.Root(self.cif, self.log, self.templatePath)
        cherrypy.tree.mount(obj, '/api', config=conf)

    def prepareForShutdown(self):
        cherrypy.engine.stop()
