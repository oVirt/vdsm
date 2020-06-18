#
# Copyright 2012-2019 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from __future__ import absolute_import
from __future__ import division
from errno import EINTR
import json
import logging
import threading
import re
import socket

from six.moves.BaseHTTPServer import BaseHTTPRequestHandler, HTTPServer
from six.moves import http_client as httplib

from vdsm import API
from vdsm.common import concurrent
from vdsm.common.define import doneCode
from vdsm.executor import TaskQueue


# The corresponding queue is used only for OVF upload and download.
# If the queue has more than 10 waiting connections there is something
# wrong and it doesn't help anything to queue more connections.
_MAX_QUEUE_TASKS = 10


class RequestException(Exception):
    def __init__(self, httpStatusCode, errorMessage):
        self.httpStatusCode = httpStatusCode
        self.errorMessage = errorMessage


class Server(object):
    def __init__(self, cif, log):
        self.cif = cif
        self.log = log

        self._enabled = False
        self.server = ThreadedServer(ImageRequestHandler)

    def start(self):
        """
        Serve clients until stopped
        """
        def threaded_start():
            self.log.info("Server running")
            self.server.timeout = 1
            self._enabled = True

            while self._enabled:
                try:
                    self.server.handle_request()
                except Exception as e:
                    if e.args[0] != EINTR:
                        self.log.error("http handler exception",
                                       exc_info=True)
            self.log.info("Server stopped")

        self._thread = concurrent.thread(threaded_start, name='http',
                                         log=self.log)
        self._thread.start()

    def add_socket(self, connected_socket, socket_address):
        self.server.add(connected_socket, socket_address)

    def stop(self):
        self.log.info("Stopping http server")
        self._enabled = False
        self.server.server_close()
        self._thread.join()
        return {'status': doneCode}


class ImageRequestHandler(BaseHTTPRequestHandler):

    # Timeout for the request socket
    timeout = 60
    log = logging.getLogger("rpc.http.ImageRequestHandler")

    HEADER_POOL = 'Storage-Pool-Id'
    HEADER_DOMAIN = 'Storage-Domain-Id'
    HEADER_IMAGE = 'Image-Id'
    HEADER_VOLUME = 'Volume-Id'
    HEADER_TASK_ID = 'Task-Id'
    HEADER_RANGE = 'Range'
    HEADER_CONTENT_LENGTH = 'content-length'
    HEADER_CONTENT_TYPE = 'content-type'
    HEADER_CONTENT_RANGE = 'content-range'

    protocol_version = "HTTP/1.1"

    def do_GET(self):
        try:
            length = self._getLength()
            img = self._createImage()
            startEvent = threading.Event()
            methodArgs = {'fileObj': self.wfile,
                          'length': length}

            uploadFinishedEvent, operationEndCallback = \
                self._createEventWithCallback()

            # Optional header
            volUUID = self.headers.get(self.HEADER_VOLUME)

            response = img.uploadToStream(methodArgs,
                                          operationEndCallback,
                                          startEvent, volUUID)

            if response['status']['code'] == 0:
                self.send_response(httplib.PARTIAL_CONTENT)
                self.send_header(self.HEADER_CONTENT_TYPE,
                                 'application/octet-stream')
                self.send_header(self.HEADER_CONTENT_LENGTH, length)
                self.send_header(self.HEADER_CONTENT_RANGE,
                                 "bytes 0-%d" % (length - 1))
                self.send_header(self.HEADER_TASK_ID, response['uuid'])
                self.end_headers()
                startEvent.set()
                self._waitForEvent(uploadFinishedEvent)
            else:
                self._send_error_response(response)

        except RequestException as e:
            # This is an expected exception, so traceback is unneeded
            self.send_error(e.httpStatusCode, e.errorMessage)
        except Exception:
            self.send_error(httplib.INTERNAL_SERVER_ERROR,
                            "error during execution",
                            exc_info=True)

    def do_PUT(self):
        try:
            contentLength = self._getIntHeader(
                self.HEADER_CONTENT_LENGTH,
                httplib.LENGTH_REQUIRED)

            img = self._createImage()

            methodArgs = {'fileObj': self.rfile,
                          'length': contentLength}

            uploadFinishedEvent, operationEndCallback = \
                self._createEventWithCallback()

            # Optional header
            volUUID = self.headers.get(self.HEADER_VOLUME)

            response = img.downloadFromStream(methodArgs,
                                              operationEndCallback,
                                              volUUID)

            if response['status']['code'] == 0:
                while not uploadFinishedEvent.is_set():
                    uploadFinishedEvent.wait()
                self.send_response(httplib.OK)
                self.send_header(self.HEADER_TASK_ID, response['uuid'])
                self.end_headers()
            else:
                self._send_error_response(response)

        except RequestException as e:
            self.send_error(e.httpStatusCode, e.errorMessage)
        except Exception:
            self.send_error(httplib.INTERNAL_SERVER_ERROR,
                            "error during execution",
                            exc_info=True)

    def _createImage(self):
        # Required headers
        spUUID = self.headers.get(self.HEADER_POOL)
        sdUUID = self.headers.get(self.HEADER_DOMAIN)
        imgUUID = self.headers.get(self.HEADER_IMAGE)
        if not all((spUUID, sdUUID, imgUUID)):
            raise RequestException(
                httplib.BAD_REQUEST,
                "missing or empty required header(s):"
                " spUUID=%s sdUUID=%s imgUUID=%s"
                % (spUUID, sdUUID, imgUUID))

        return API.Image(imgUUID, spUUID, sdUUID)

    @staticmethod
    def _createEventWithCallback():
        operationFinishedEvent = threading.Event()

        def setCallback():
            operationFinishedEvent.set()

        return operationFinishedEvent, setCallback

    @staticmethod
    def _waitForEvent(event):
        while not event.is_set():
            event.wait()

    def _getIntHeader(self, headerName, missingError):
        value = self._getRequiredHeader(headerName, missingError)

        return self._getInt(value)

    def _getRequiredHeader(self, headerName, missingError):
        value = self.headers.get(headerName)
        if not value:
            raise RequestException(
                missingError,
                "missing header %s" % headerName)
        return value

    def _getInt(self, value):
        try:
            return int(value)
        except ValueError:
            raise RequestException(
                httplib.BAD_REQUEST,
                "not int value %r" % value)

    def _getLength(self):
        value = self._getRequiredHeader(self.HEADER_RANGE,
                                        httplib.BAD_REQUEST)

        m = re.match(r'^bytes=0-(\d+)$', value)
        if m is None:
            raise RequestException(
                httplib.BAD_REQUEST,
                "Unsupported range: %r , expected: bytes=0-last_byte" %
                value)

        last_byte = m.group(1)
        return self._getInt(last_byte) + 1

    def send_error(self, error, message, exc_info=False):
        # When failing after sending the headers the client will get stuck
        # waiting for data that will never be received, so we must close the
        # connection.
        self.close_connection = True
        try:
            self.log.error(message, exc_info=exc_info)
            self.send_response(error)
            self.end_headers()
        except Exception:
            self.log.error("failed to return response",
                           exc_info=True)

    def _send_error_response(self, response):
        self.send_response(httplib.INTERNAL_SERVER_ERROR)
        json_response = json.dumps(response).encode("utf-8")
        self.send_header(self.HEADER_CONTENT_TYPE,
                         'application/json')
        self.send_header(self.HEADER_CONTENT_LENGTH,
                         len(json_response))
        self.end_headers()
        self.wfile.write(json_response)


class ThreadedServer(HTTPServer):
    """
    This server does not listen to to connections; the user is responsible for
    accepting connections and adding them to the server.

    For each connection added, request_handler is invoked in a new thread,
    handling all requests sent over this connection.
    """

    _STOP = object()

    log = logging.getLogger("vds.http.Server")

    def __init__(self, RequestHandlerClass):
        HTTPServer.__init__(self, None, RequestHandlerClass, False)
        self.requestHandler = RequestHandlerClass
        self.queue = TaskQueue("http-server", _MAX_QUEUE_TASKS)

    def add(self, connected_socket, socket_address):
        self.queue.put((connected_socket, socket_address))

    def handle_request(self):
        sock, addr = self.queue.get()
        if sock is self._STOP:
            return
        self.log.info("Starting request handler for %s:%d", addr[0], addr[1])
        t = concurrent.thread(self._process_requests, args=(sock, addr),
                              log=self.log)
        t.start()

    def server_close(self):
        self.queue.clear()
        self.queue.put((self._STOP, self._STOP))

    def _process_requests(self, sock, addr):
        self.log.info("Request handler for %s:%d started", addr[0], addr[1])
        try:
            self.requestHandler(sock, addr, self)
        except Exception:
            self.log.exception("Unhandled exception in request handler for "
                               "%s:%d", addr[0], addr[1])
        finally:
            self._shutdown_connection(sock)
        self.log.info("Request handler for %s:%d stopped", addr[0], addr[1])

    def _shutdown_connection(self, sock):
        try:
            sock.shutdown(socket.SHUT_WR)
        except socket.error:
            pass  # Some platforms may raise ENOTCONN here
        finally:
            sock.close()


class HttpDetector():
    log = logging.getLogger("HttpDetector")
    NAME = "http"
    HTTP_VERBS = (b"PUT /", b"GET /")
    REQUIRED_SIZE = max(len(v) for v in HTTP_VERBS)

    def __init__(self, server):
        self.server = server

    def detect(self, data):
        return data.startswith(HttpDetector.HTTP_VERBS)

    def handle_socket(self, client_socket, socket_address):
        self.server.add_socket(client_socket, socket_address)
        self.log.debug("http detected from %s", socket_address)
