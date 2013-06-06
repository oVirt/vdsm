#
# Copyright 2013 Red Hat, Inc.
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

from vdsm import utils, constants

_curl = utils.CommandPath("curl",
                          "/usr/bin/curl",)  # Fedora, EL6

CURL_OPTIONS = ["-q", "--silent", "--fail", "--show-error"]


class CurlError(Exception):
    def __init__(self, ecode, stdout, stderr, message=None):
        self.ecode = ecode
        self.stdout = stdout
        self.stderr = stderr
        self.message = message

    def __str__(self):
        return "ecode=%s, stdout=%s, stderr=%s, message=%s" % (
            self.ecode, self.stdout, self.stderr, self.message)


def _headersToOptions(headers):
    options = []
    for k, v in headers.iteritems():
        options.extend(("--header", "%s: %s" % (k, v)))
    return options


def head(url, headers={}):
    # Cannot be moved out because _curl.cmd is lazy-evaluated
    cmd = [_curl.cmd] + CURL_OPTIONS + ["--head", url]

    cmd.extend(_headersToOptions(headers))
    rc, out, err = utils.execCmd(cmd)

    if rc != 0:
        raise CurlError(rc, out, err)

    # Parse and return headers
    return dict([x.split(": ", 1) for x in out[1:-1]])


def download(url, path, headers={}):
    cmd = [constants.EXT_CURL_IMG_WRAP, "--download"]
    cmd.extend(_headersToOptions(headers) + [path, url])

    rc, out, err = utils.execCmd(cmd)

    if rc != 0:
        raise CurlError(rc, out, err)


def upload(url, path, headers={}):
    cmd = [constants.EXT_CURL_IMG_WRAP, "--upload"]
    cmd.extend(_headersToOptions(headers) + [path, url])

    rc, out, err = utils.execCmd(cmd)

    if rc != 0:
        raise CurlError(rc, out, err)
