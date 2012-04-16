#
# Copyright 2010-2011 Red Hat, Inc.
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

from vdsm import utils
import glob
import os
import tempfile
import logging

import hashlib

from vdsm.constants import P_VDSM_HOOKS, P_VDSM

class HookError(Exception): pass

# dir path is relative to '/' for test purposes
# otherwise path is relative to P_VDSM_HOOKS
def _scriptsPerDir(dir):
    if (dir[0] == '/'):
        path = dir
    else:
        path = P_VDSM_HOOKS + dir
    return [ s for s in glob.glob(path + '/*')
             if os.access(s, os.X_OK) ]

def _runHooksDir(domxml, dir, vmconf={}, raiseError=True, params={}):
    scripts = _scriptsPerDir(dir)
    scripts.sort()

    if not scripts:
        return domxml

    xmlfd, xmlname = tempfile.mkstemp()
    try:
        os.write(xmlfd, domxml or '')
        os.close(xmlfd)

        scriptenv = os.environ.copy()
        scriptenv.update(vmconf.get('custom', {}))
        if len(params) > 0:
            scriptenv.update(params)
        if vmconf.get('vmId'):
            scriptenv['vmId'] = vmconf.get('vmId')
        ppath = scriptenv.get('PYTHONPATH', '')
        scriptenv['PYTHONPATH'] = ':'.join(ppath.split(':') + [P_VDSM])
        scriptenv['_hook_domxml'] = xmlname
        for k, v in scriptenv.iteritems():
            scriptenv[k] = unicode(v).encode('utf-8')

        errorSeen = False
        for s in scripts:
            rc, out, err = utils.execCmd([s], sudo=False, raw=True,
                                         env=scriptenv)
            logging.info(err)
            if rc != 0:
                errorSeen = True

            if rc == 2:
                break
            elif rc > 2:
                logging.warn('hook returned unexpected return code %s', rc)

        if errorSeen and raiseError:
            raise HookError()

        finalxml = file(xmlname).read()
    finally:
        os.unlink(xmlname)
    return finalxml

def before_vm_start(domxml, vmconf={}):
    return _runHooksDir(domxml, 'before_vm_start', vmconf=vmconf)

def after_vm_start(domxml, vmconf={}):
    return _runHooksDir(domxml, 'after_vm_start', vmconf=vmconf, raiseError=False)

def before_vm_cont(domxml, vmconf={}):
    return _runHooksDir(domxml, 'before_vm_cont', vmconf=vmconf)

def after_vm_cont(domxml, vmconf={}):
    return _runHooksDir(domxml, 'after_vm_cont', vmconf=vmconf, raiseError=False)

def before_vm_pause(domxml, vmconf={}):
    return _runHooksDir(domxml, 'before_vm_pause', vmconf=vmconf)

def after_vm_pause(domxml, vmconf={}):
    return _runHooksDir(domxml, 'after_vm_pause', vmconf=vmconf, raiseError=False)

def before_vm_migrate_source(domxml, vmconf={}):
    return _runHooksDir(domxml, 'before_vm_migrate_source', vmconf=vmconf)

def after_vm_migrate_source(domxml, vmconf={}):
    return _runHooksDir(domxml, 'after_vm_migrate_source', vmconf=vmconf,
                        raiseError=False)

def before_vm_migrate_destination(domxml, vmconf={}):
    return _runHooksDir(domxml, 'before_vm_migrate_destination', vmconf=vmconf)

def after_vm_migrate_destination(domxml, vmconf={}):
    return _runHooksDir(domxml, 'after_vm_migrate_destination', vmconf=vmconf,
                        raiseError=False)

def before_vm_hibernate(domxml, vmconf={}):
    return _runHooksDir(domxml, 'before_vm_hibernate', vmconf=vmconf)

def after_vm_hibernate(domxml, vmconf={}):
    return _runHooksDir(domxml, 'after_vm_hibernate', vmconf=vmconf,
                        raiseError=False)

def before_vm_dehibernate(domxml, vmconf={}):
    return _runHooksDir(domxml, 'before_vm_dehibernate', vmconf=vmconf)

def after_vm_dehibernate(domxml, vmconf={}):
    return _runHooksDir(domxml, 'after_vm_hibernate', vmconf=vmconf,
                        raiseError=False)

def before_vm_destroy(domxml, vmconf={}):
    return _runHooksDir(None, 'before_vm_destroy', vmconf=vmconf,
                        raiseError=False)

def after_vm_destroy(domxml, vmconf={}):
    return _runHooksDir(domxml, 'after_vm_destroy', vmconf=vmconf,
                        raiseError=False)

def before_vm_set_ticket(domxml, vmconf={}, params={}):
    return _runHooksDir(domxml, 'before_vm_set_ticket', vmconf=vmconf,
                        raiseError=False, params=params)

def after_vm_set_ticket(domxml, vmconf={}, params={}):
    return _runHooksDir(domxml, 'after_vm_set_ticket', vmconf=vmconf,
                        raiseError=False, params=params)

def before_vdsm_start():
    return _runHooksDir(None, 'before_vdsm_start', raiseError=False)

def after_vdsm_stop():
    return _runHooksDir(None, 'after_vdsm_stop', raiseError=False)

def _getScriptInfo(path):
    try:
        with file(path) as f:
            md5 = hashlib.md5(f.read()).hexdigest()
    except:
        md5 = ''
    return {'md5': md5}

def _getHookInfo(dir):
    def scripthead(script):
        return script[len(P_VDSM_HOOKS) + len(dir) + 1:]
    return dict([ (scripthead(script), _getScriptInfo(script))
                  for script in _scriptsPerDir(dir) ])

def installed():
    res = {}
    for dir in os.listdir(P_VDSM_HOOKS):
        inf = _getHookInfo(dir)
        if inf:
            res[dir] = inf
    return res

if __name__ == '__main__':
    import sys
    def usage():
        print 'Usage: %s hook_name' % sys.argv[0]
        sys.exit(1)

    if len(sys.argv) >= 2:
        globals()[sys.argv[1]](*sys.argv[2:])
    else:
        usage()
