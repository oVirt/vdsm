# Copyright 2010 Red Hat, Inc. and/or its affiliates.
#
# Licensed to you under the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.  See the files README and
# LICENSE_GPL_v2 which accompany this distribution.
#

import utils
import glob
import os
import tempfile
import logging

import hashlib

from constants import P_VDSM_HOOKS, P_VDSM

class HookError(Exception): pass

def _scriptsPerDir(dir):
    return [ s for s in glob.glob(P_VDSM_HOOKS + dir + '/*')
             if os.access(s, os.X_OK) ]

def _runHooksDir(domxml, dir, vmconf={}, raiseError=True):
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

def after_vm_destroy(domxml, vmconf={}):
    return _runHooksDir(domxml, 'after_vm_destroy', vmconf=vmconf, raiseError=False)

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
