# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import

import six

from vdsm.common import cmdutils
from vdsm.common import commands
from vdsm import constants

_curl = cmdutils.CommandPath("curl",
                             "/usr/bin/curl", )  # Fedora, EL6

CURL_OPTIONS = ["-q", "--silent", "--fail", "--show-error"]


class CurlError(Exception):
    def __init__(self, ecode, stdout, stderr, message=None):
        self.ecode = ecode
        self.stdout = stdout
        self.stderr = stderr
        self.msg = message

    def __str__(self):
        return "ecode=%s, stdout=%s, stderr=%s, message=%s" % (
            self.ecode, self.stdout, self.stderr, self.msg)


def _headersToOptions(headers):
    options = []
    for k, v in six.iteritems(headers):
        options.extend(("--header", "%s: %s" % (k, v)))
    return options


def _run_curl(cmd, headers=None):
    if headers:
        cmd.extend(_headersToOptions(headers))

    try:
        return commands.run(cmd)
    except cmdutils.Error as e:
        raise CurlError(e.rc, e.out, e.err)


def parse_headers(out):
    lines = out.decode("iso-8859-1").splitlines(False)
    # Besides headers curl returns also HTTP status as the first line and the
    # last line is empty. Therefore we skip first and last line.
    headers = lines[1:-1]
    return dict([x.split(": ", 1) for x in headers])


def head(url, headers=None):
    # Cannot be moved out because _curl.cmd is lazy-evaluated
    cmd = [_curl.cmd] + CURL_OPTIONS + ["--head", url]
    out = _run_curl(cmd, headers)
    # Parse and return headers
    return parse_headers(out)


def get(url, headers=None):
    # Cannot be moved out because _curl.cmd is lazy-evaluated
    cmd = [_curl.cmd] + CURL_OPTIONS + [url]
    return _run_curl(cmd, headers)


def download(url, path, headers=None):
    cmd = [constants.EXT_CURL_IMG_WRAP, "--download"]
    if headers:
        cmd.extend(_headersToOptions(headers))
    cmd.extend([path, url])

    rc, out, err = commands.execCmd(cmd)

    if rc != 0:
        raise CurlError(rc, out, err)


def upload(url, path, headers=None):
    cmd = [constants.EXT_CURL_IMG_WRAP, "--upload"]
    if headers:
        cmd.extend(_headersToOptions(headers))
    cmd.extend([path, url])

    rc, out, err = commands.execCmd(cmd)

    if rc != 0:
        raise CurlError(rc, out, err)
