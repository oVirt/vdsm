#
# Copyright 2017 Red Hat, Inc.
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

from __future__ import absolute_import

from contextlib import contextmanager
import logging
import threading

from six.moves import socketserver

from vdsm.common import fileutils


_REPLIES = {}


class FakeCollectd(object):

    _log = logging.getLogger('test.collectd.fake')

    _SOCK = 'test_collectd_vdsm.sock'

    @classmethod
    def create(cls, replies_data):
        fileutils.rm_file(cls._SOCK)
        return cls(replies_data)

    def __init__(self, replies_data=None):
        global _REPLIES
        _REPLIES = replies_data
        self._server = socketserver.UnixStreamServer(
            self._SOCK, CollectdHandler
        )
        self._started = threading.Event()
        self._stopped = threading.Event()
        self._thread = threading.Thread(target=self.serve)
        self._thread.daemon = True
        self._running = False

    @property
    def path(self):
        return self._SOCK

    @property
    def running(self):
        return self._started.wait(1.0)

    def start(self):
        self._thread.start()

    def serve(self):
        while True:
            stop = self._stopped.wait(0.1)
            self._log.info('should stop now? ...%s' % (
                'yep' if stop else 'nay')
            )
            if stop:
                break
            self._started.set()
            self._log.info('handling request...')
            self._server.handle_request()
            self._log.info('handled request...')

    def stop(self):
        self._log.info('stopping...')
        self._stopped.set()
        fileutils.rm_file(self._SOCK)


class CollectdHandler(socketserver.StreamRequestHandler):

    def handle(self):
        while True:
            logging.info("handler waiting for request")
            req = self.rfile.readline().decode('utf-8')
            if not req:
                logging.info("empty request, handler done")
                break
            args = req.split()
            if args[0] == 'LISTVAL':
                res = self.handle_listval()
            elif args[0] == 'GETVAL':
                res = self.handle_getval(args[1].strip('"'))
            else:
                res = '-1 Unknown command: %s\n' % args[0]
            self.wfile.write(res.encode('utf-8'))
            self.wfile.flush()
            logging.info("request served")

    def handle_listval(self):
        global _REPLIES
        logging.info('handling listval with%s replies data...' % (
            'out' if _REPLIES is None else '')
        )
        vals = [] if _REPLIES is None else _REPLIES.keys()
        ret = ['%i Values found\n' % len(vals)]
        ret.extend(val for val in vals)
        return '\n'.join(ret) + '\n'

    def handle_getval(self, what):
        global _REPLIES
        logging.info('handling getval(%s) with%s replies data...' % (
            what, 'out' if _REPLIES is None else '')
        )

        tokens = what.split('/')
        if len(tokens) == 3:
            # hostname, plugin, type: ignore hostname
            _, plugin, rawtype = tokens
        elif len(tokens) == 2:
            # hostname can be omitted if localhost
            plugin, rawtype = tokens
        else:
            return "-1 Malformed request.\n"

        if _REPLIES is not None:
            try:
                return self._make_reply(_REPLIES[what])
            except KeyError:
                return "-1 No such value.\n"
        else:
            if plugin == 'missing':
                return "-1 No such value.\n"
            elif plugin != 'success':
                return "-1 Cannot parse identifier `%s'.\n" % plugin
            return self._make_reply(rawtype.split(','))

    def _make_reply(self, vals):
        ret = ['%i Values found' % len(vals)]
        for val in vals:
            name, value = val.split('=')
            ret.append('%s=%f' % (name, float(value)))
        return '\n'.join(ret) + '\n'


@contextmanager
def run_server(replies_data=None):
    collectd = FakeCollectd.create(replies_data)
    collectd.start()
    try:
        yield collectd
    except Exception as exc:
        logging.exception('caught: %s', exc)
        raise
    finally:
        collectd.stop()
        collectd = None


if __name__ == '__main__':
    import time
    logging.basicConfig(
        format='%(asctime)s %(levelname)s %(message)s',
        level=logging.DEBUG
    )

    with run_server() as serv:
        assert serv.running
        while True:
            time.sleep(0.1)
        logging.info('done, bye')
