#!/usr/bin/python

import os
import sys
import hooking
import traceback
import guestfs
import tempfile

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
    try:
        g = guestfs.GuestFS()
        g.add_drive_opts(drive, format=diskformat)
        g.launch()

        roots = g.inspect_os()

        for root in roots:
            temp = tempfile.NamedTemporaryFile()
            g.mount_options("", root, "/")
            try:
                temp.file.write(content)
                temp.file.flush()

                if g.inspect_get_type == "windows":
                    directory = g.case_sensitive_path(os.path.dirname(
                                                                filepath))
                    filepath = "%s/%s" % (directory,
                                          os.path.basename(filepath))

                g.upload(temp.name, filepath)

                # if no error we uploaded the file
                sys.stderr.write('fileinject: file %s '
                                 'was uploaded successfully to VMs disk\n' %
                                 filepath)
                return True

            except Exception as e1:
                sys.stderr.write('fileinject: '
                                 '[error in inject_file uploading file]: '
                                 '%s\n' % e1.message)

            g.umount(root)
            temp.close()

    except Exception as e:
        sys.stderr.write('fileinject: [general error in inject_file]: %s\n' %
                         e.message)

    return False

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
            if (disk.hasAttribute('device') and
                disk.attributes['device'].value == 'disk'):
                sources = disk.getElementsByTagName('source')
                if len(sources) > 0:
                    source = sources[0]
                    drivers = disk.getElementsByTagName('driver')
                    if (len(drivers) > 0 and
                        drivers[0].hasAttribute('type') and
                        drivers[0].attributes['type'].value == 'qcow2'):
                        # we can only inject to 'raw' file format
                        continue

                    rawcount += 1

                  # disk format can be raw or qcow2
                  # http://libguestfs.org/guestfs.3.html#guestfs_add_drive_opts
                    path = None
                    if source.hasAttribute('file'):
                        path = source.attributes['file'].value
                    elif source.hasAttribute('dev'):
                        path = source.attributes['dev'].value

                    if not path is None:
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
