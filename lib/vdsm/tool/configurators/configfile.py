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

import ConfigParser
import functools
import os
import tempfile
import re
import selinux
import io

from ... import utils
if utils.isOvirtNode():
    from ovirt.node.utils.fs import Config as NodeCfg

(
    BEFORE,
    WITHIN,
    AFTER
) = range(3)


def context(func):
    @functools.wraps(func)
    def inner(*args, **kwargs):
        if not args[0]._context:
            raise RuntimeError("Must be called from a managed context.")
        func(*args, **kwargs)
    return inner


class ConfigFile(object):
    """
    During installation vdsm is responsible for editing configuration files of
    its dependencies - libvirtd.conf, qemu.conf and others.

    This class represents common operations done on configuration files.

    implementation:
    writing methods must be called inside a ConfigFile context. When
    the context is closed (__exit__ is called) changes are written to a
    temporary file that then replaces the original file.
    file's mode is reapplied if needed and selinux context is restored.

    configuration versioning:
    sections added (by prependSection() or addEntry()) will wrapped between:
    'sectionStart'-'version'\n
    ...
    'sectionEnd'-'version'\n.

    hasConf checks if a file has up to date configuration so it also tests for
    'sectionStart'-'version'\n

    When removing old configuration we remove old as well. so all
    'sectionStart'.*
    ...
    'sectionEnd'.*.
    sections are removed.

    Backward compatibility:
    (remove when upgrade from 3.0 is no longer supported!)
    prior to adding comment wrapped sections vdsm added single lines followed
    by a 'by vdsm' comment. such lines are still removed on removeConf().
    lineComment parameter indicates the content of comment.
    """

    def __init__(self,
                 filename,
                 version,
                 sectionStart='## beginning of configuration section by vdsm',
                 sectionEnd='## end of configuration section by vdsm',
                 prefix='# VDSM backup',
                 lineComment='by vdsm'):
        if not os.path.exists(filename):
            raise OSError(
                'No such file or directory: %s' % (filename, )
            )

        self._filename = filename
        self._context = False
        self._sectionStart = sectionStart
        self._sectionEnd = sectionEnd
        self._prefix = prefix
        # remove 'lineComment' at 4.0. see  'Backward compatibility'
        self._lineComment = lineComment
        self._version = version

    def __enter__(self):
        if self._context:
            raise RuntimeError("can only enter once")
        self._entries = {}
        self._context = True
        self._prefixRemove = None
        self._prefixAdd = None
        self._section = None
        self._oldmod = os.stat(self._filename).st_mode
        self._remove = None
        self._rmstate = BEFORE
        return self

    def _getOldContent(self):
        confpat = re.compile(r'^\s*(?P<key>[^=\s#]*)\s*=')
        oldlines = []
        oldentries = set()
        with open(self._filename, 'r') as f:
            for line in f:
                if self._remove:
                    if (self._rmstate == BEFORE and
                            line.startswith(self._sectionStart)):

                        self._rmstate = WITHIN

                    elif (self._rmstate == WITHIN and
                            line.startswith(self._sectionEnd)):

                        self._rmstate = AFTER
                        continue

                if not self._remove or self._rmstate != WITHIN:
                    if self._prefixRemove:
                        if line.startswith(self._prefix):
                            line = line[len(self._prefix):]
                    if self._prefixAdd:
                        line = self._prefix + line
                    m = confpat.match(line.rstrip())
                    if m:
                        oldentries.add(m.group('key'))
                    # remove this if at 4.0. see  'Backward compatibility'
                    if not self._remove or self._lineComment not in line:
                        oldlines.append(line)
            return oldlines, oldentries

    def _start(self):
        return "%s-%s\n" % (self._sectionStart, self._version)

    def _end(self):
        return "%s-%s\n" % (self._sectionEnd, self._version)

    def _writeSection(self, f):
        f.write(self._start())
        f.write(self._section)
        f.write(self._end())

    def _writeEntries(self, f, oldentries):
        f.write(self._start())
        for key, val in sorted(self._entries.items()):
            if key not in oldentries:
                f.write("{k}={v}\n".format(k=key, v=val))
        f.write(self._end())

    def __exit__(self, exec_ty, exec_val, tb):

        self._context = False
        if exec_ty is None:
            fd, tname = tempfile.mkstemp(dir=os.path.dirname(self._filename))
            try:
                oldlines, oldentries = self._getOldContent()
                with os.fdopen(fd, 'w', ) as f:
                    if self._section:
                        self._writeSection(f)
                    f.writelines(oldlines)
                    if self._entries:
                        self._writeEntries(f, oldentries)

                if utils.isOvirtNode():
                    NodeCfg().unpersist(self._filename)
                os.rename(tname, self._filename)
                if utils.isOvirtNode():
                    NodeCfg().persist(self._filename)

                if self._oldmod != os.stat(self._filename).st_mode:
                    os.chmod(self._filename, self._oldmod)

                if selinux.is_selinux_enabled:
                    try:
                        selinux.restorecon(self._filename)
                    except OSError:
                        pass  # No default label for file
            finally:
                if os.path.exists(tname):
                    os.remove(tname)

    @context
    def addEntry(self, key, val):
        """
        add key=value unless key is already in the file.
        all pairs are added in a comment wrapped section.
        """
        self._entries[key] = val

    @context
    def prependSection(self, section):
        """
        add 'section' in the beginning of the file.
        section is added in a comment wrapped section.

        Only one section is currently supported.
        """
        self._section = section

    @context
    def prefixLines(self):
        """
        Add self.prefix to the beginning of each line.
        No editing is done on new content added by this config file.
        """
        self._prefixAdd = True

    @context
    def unprefixLines(self):
        """
        Remove self.prefix from each line starting with it.
        No editing is done on new content added by this config file.
        """
        self._prefixRemove = True

    @context
    def removeConf(self):
        self._remove = True

    def hasConf(self):
        """
        Notice this method can be called out of context since it is read only
        """
        for line in open(self._filename, 'r'):
            if line == self._start():
                return True
        return False


class ParserWrapper(object):
    """
    ConfigParser is for parsing of ini files. Use this
    class for files with no sections.
    """
    def __init__(self, defaults=None):
        self.wrapped = ConfigParser.RawConfigParser(defaults=defaults)

    def get(self, option):
        return self.wrapped.get('root', option)

    def getboolean(self, option):
        return self.wrapped.getboolean('root', option)

    def getfloat(self, option):
        return self.wrapped.getfloat('root', option)

    def getint(self, option):
        return self.wrapped.getint('root', option)

    def read(self, path):
        with open(path, 'r') as f:
            return self.wrapped.readfp(
                io.StringIO(u'[root]\n' + f.read().decode())
            )
