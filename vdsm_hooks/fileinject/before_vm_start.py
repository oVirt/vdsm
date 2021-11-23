#!/usr/bin/python3

from __future__ import absolute_import

import os
import sys
import hooking
import traceback
import guestfs


'''
fileinject vdsm hook
====================
hook is getting target file name and its content and
create that file in target machine.

hook will try to add the file only to the operation system
partition, i.e.
Windows: /c/myfile
Linux: /myfile

Please note that in Windows the path is case sensitive!

syntax:
    fileinject=/<target file name> : <file content>
    fileinject=/myfile:some file content\netc...
'''


def inject_file(filepath, content, drive, diskformat):
    injected = False
    gfs = guestfs.GuestFS()
    try:
        gfs.add_drive_opts(drive, format=diskformat)
    except RuntimeError as e:
        sys.stderr.write('fileinject: [error in inject_file]: %s\n' % e)
    else:
        gfs.launch()
        for root in gfs.inspect_os():
            if gfs.inspect_get_type(root) == "windows":
                filepath = os.path.join(
                    gfs.case_sensitive_path(os.path.dirname(filepath)),
                    os.path.basename(filepath))
            gfs.mount_options("", root, "/")
            try:
                gfs.write(filepath, content)
            except RuntimeError as e:
                sys.stderr.write('fileinject: [upload failed]: %s\n' % e)
            else:
                injected = True
            finally:
                gfs.umount(root)

    return injected


if 'fileinject' in os.environ:
    try:
        pos = os.environ['fileinject'].find(':')

        if pos < 0:
            sys.stderr.write('fileinject: invalid syntax, '
                             'expected file-name:file-content, '
                             'no ":" separation found: %s pos: %d\n' %
                             (os.environ['fileinject'], pos))
            sys.exit(2)

        filepath = os.environ['fileinject'][:pos]
        content = os.environ['fileinject'][pos + 1:]

        if not filepath.startswith('/'):
            sys.stderr.write("fileinject: filepath must start with '/', "
                             "please refer to the README file\n")
            sys.exit(2)

        domxml = hooking.read_domxml()
        disks = domxml.getElementsByTagName('disk')

        injected = False
        diskformat = 'raw'
        rawcount = 0
        for disk in disks:
            if (disk.hasAttribute('device')
                    and disk.attributes['device'].value == 'disk'):
                sources = disk.getElementsByTagName('source')
                if len(sources) > 0:
                    source = sources[0]
                    drivers = disk.getElementsByTagName('driver')
                    if (len(drivers) > 0
                       and drivers[0].hasAttribute('type')
                       and drivers[0].attributes['type'].value == 'qcow2'):
                        # we can only inject to 'raw' file format
                        continue

                    rawcount += 1

                    # disk format can be raw or qcow2
                    # http://libguestfs.org/guestfs.3.html#guestfs_add_drive_opts  # noqa
                    path = None
                    if source.hasAttribute('file'):
                        path = source.attributes['file'].value
                    elif source.hasAttribute('dev'):
                        path = source.attributes['dev'].value

                    if path is not None:
                        injected = inject_file(filepath, content,
                                               path, diskformat)

        if not injected:
            if rawcount == 0:
                sys.stderr.write('fileinject: there is no "preallocated" '
                                 '(RAW format) disk in VM, '
                                 'cannot inject data\n')
            else:
                sys.stderr.write('fileinject: Cannot inject data, '
                                 'path not exists: %s\n' %
                                 os.path.dirname(filepath))
            sys.exit(2)
    except:
        sys.stderr.write('fileinject: [unexpected error]: %s\n' %
                         traceback.format_exc())
        sys.exit(2)
