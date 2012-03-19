#!/usr/bin/python
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301  USA
#
# Refer to the README and COPYING files for full details of the license
#

import os
import sys
import socket
import getopt
import httplib
from ConfigParser import ConfigParser

import deployUtil
from config import config

VDSM_REG_CONF_FILE = '/etc/vdsm-reg/vdsm-reg.conf'

USAGE_ERROR = -1
SUCCESS = 0
INVALID_PORT_ERROR = 1
CONF_FILE_READ_ERROR = 2
OVIRT_ENGINE_NOT_REACHABLE_ERROR = 3
CONF_FILE_WRITE_ERROR = 4
VDSM_REG_RESTART_FAILED_ERROR = 5


def usage():
    name = os.path.basename(sys.argv[0])
    print """

Usage: %(name)s [-f | --force] [-p PORT | --port PORT] OVIRT_ENGINE

Register current node to OVIRT_ENGINE

Options:
    -p, --port=PORT          Use PORT to connect to OVIRT_ENGINE
    -f, --force              Register to OVIRT_ENGINE forcefully
    -h, --help               Show this help

Example:
  # %(name)s ovirt-engine-server
  # %(name)s -p 8443 ovirt-engine-server
""" % {'name': name}


def isHostReachable(host, port=None, ssl=True, timeout=15):
    try:
        if ssl:
            _connect = httplib.HTTPSConnection
        else:
            _connect = httplib.HTTPConnection
        if port:
            conn = _connect(host, port=port, timeout=timeout)
        else:
            conn = _connect(host, timeout=timeout)
        conn.request('HEAD', '/')
        return True
    except socket.error:
        return False


def main():
    """
    Usage:
    register-to-engine.py [-f | --force] [-p PORT | --port PORT] OVIRT_ENGINE
    """

    port = None
    force = False
    try:
        opts, args = getopt.getopt(sys.argv[1:], "hfp:",
                                   ["help", "force", "port="])
        if len(args) != 1:
            usage()
            sys.exit(USAGE_ERROR)
        newVdcHostName = args[0]
        for o, v in opts:
            if o in ("-h", "--help"):
                usage()
                sys.exit(SUCCESS)
            elif o in ("-p", "--port"):
                try:
                    port = int(v)
                except ValueError:
                    sys.stderr.write('invalid port: %s\n' % v)
                    sys.exit(INVALID_PORT_ERROR)
            elif o in ("-f", "--force"):
                force = True
    except getopt.GetoptError, e:
        sys.stderr.write("ERROR: %s\n" % (e.msg))
        usage()
        sys.exit(USAGE_ERROR)

    config.read(VDSM_REG_CONF_FILE)
    if not port:
        try:
            port = config.get('vars', 'vdc_host_port')
        except ConfigParser.NoOptionError:
            sys.stderr.write("Failed to retrieve port number " +
                             "from config file: %s\n" % VDSM_REG_CONF_FILE)
            sys.exit(CONF_FILE_READ_ERROR)

    try:
        vdcHostName = config.get('vars', 'vdc_host_name')
    except ConfigParser.NoOptionError:
        vdcHostName = None

    if not force and vdcHostName and "NONE" != vdcHostName.upper():
        sys.stdout.write('Node already configured to Engine %s\n' % \
                             vdcHostName)
        sys.stdout.write('Do you want to reset and use %s (yes/NO): ' % \
                             newVdcHostName)
        ans = sys.stdin.readline()
        if "YES" != ans.strip().upper():
            sys.exit(0)

    if not isHostReachable(newVdcHostName, port):
        if not isHostReachable(newVdcHostName, port, ssl=False):
            sys.stderr.write('Engine %s ' % newVdcHostName +
                             ' is not reachable by HTTP or HTTPS\n')
            sys.exit(OVIRT_ENGINE_NOT_REACHABLE_ERROR)

    if not deployUtil.setVdsConf("vdc_host_name=%s" % newVdcHostName,
                                 VDSM_REG_CONF_FILE):
        sys.exit(CONF_FILE_WRITE_ERROR)
    if not deployUtil.setVdsConf("vdc_host_port=%s" % port, VDSM_REG_CONF_FILE):
        sys.exit(CONF_FILE_WRITE_ERROR)

    out, err, rv = deployUtil.setService("vdsm-reg", "restart")
    if rv != 0:
        sys.stderr.write("Failed to restart vdsm-reg service: ")
        sys.stderr.write("(%s, %s, %s)\n" % (rv, out, err))
        sys.exit(VDSM_REG_RESTART_FAILED_ERROR)
    sys.exit(SUCCESS)

if __name__ == "__main__":
    main()
