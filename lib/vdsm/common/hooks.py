# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import print_function
from __future__ import absolute_import
from __future__ import division

import glob
import hashlib
import itertools
import json
import logging
import os
import os.path
import pkgutil
import subprocess
import sys
import tempfile

import six

from vdsm.common import commands
from vdsm.common import exception
from vdsm.common.constants import P_VDSM_HOOKS, P_VDSM_RUN

_LAUNCH_FLAGS_FILE = 'launchflags'
_LAUNCH_FLAGS_PATH = os.path.join(
    P_VDSM_RUN,
    '%s',
    _LAUNCH_FLAGS_FILE,
)


def _scriptsPerDir(dir_name):
    if os.path.isabs(dir_name):
        raise ValueError("Cannot use absolute path as hook directory")
    head = dir_name
    while head:
        head, tail = os.path.split(head)
        if tail == "..":
            raise ValueError("Hook directory paths cannot contain '..'")
    path = os.path.join(P_VDSM_HOOKS, dir_name, '*')
    return [s for s in glob.glob(path)
            if os.path.isfile(s) and os.access(s, os.X_OK)]


_DOMXML_HOOK = 1
_JSON_HOOK = 2


def _runHooksDir(data, dir, vmconf={}, raiseError=True, errors=None, params={},
                 hookType=_DOMXML_HOOK):
    if errors is None:
        errors = []

    scripts = _scriptsPerDir(dir)
    scripts.sort()

    if not scripts:
        return data

    data_fd, data_filename = tempfile.mkstemp()
    try:
        if hookType == _DOMXML_HOOK:
            os.write(data_fd, data.encode('utf-8') if data else b'')
        elif hookType == _JSON_HOOK:
            os.write(data_fd, json.dumps(data).encode('utf-8'))
        os.close(data_fd)

        scriptenv = os.environ.copy()

        # Update the environment using params and custom configuration
        env_update = [six.iteritems(params),
                      six.iteritems(vmconf.get('custom', {}))]

        # On py2 encode custom properties with default system encoding
        # and save them to scriptenv. Pass str objects (byte-strings)
        # without any conversion
        for k, v in itertools.chain(*env_update):
            try:
                if six.PY2 and isinstance(v, six.text_type):
                    scriptenv[k] = v.encode(sys.getfilesystemencoding())
                else:
                    scriptenv[k] = v
            except UnicodeEncodeError:
                pass

        if vmconf.get('vmId'):
            scriptenv['vmId'] = vmconf.get('vmId')
        ppath = scriptenv.get('PYTHONPATH', '')
        hook = os.path.dirname(pkgutil.get_loader('vdsm.hook').get_filename())
        scriptenv['PYTHONPATH'] = ':'.join(ppath.split(':') + [hook])
        if hookType == _DOMXML_HOOK:
            scriptenv['_hook_domxml'] = data_filename
        elif hookType == _JSON_HOOK:
            scriptenv['_hook_json'] = data_filename

        for s in scripts:
            p = commands.start([s], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, env=scriptenv)

            with commands.terminating(p):
                (out, err) = p.communicate()

            rc = p.returncode
            logging.info('%s: rc=%s err=%s', s, rc, err)
            if rc != 0:
                errors.append(err)

            if rc == 2:
                break
            elif rc > 2:
                logging.warning('hook returned unexpected return code %s', rc)

        if errors and raiseError:
            raise exception.HookError(err)

        with open(data_filename, encoding='utf-8') as f:
            final_data = f.read()
    finally:
        os.unlink(data_filename)
    if hookType == _DOMXML_HOOK:
        return final_data
    elif hookType == _JSON_HOOK:
        return json.loads(final_data)


def before_device_create(devicexml, vmconf={}, customProperties={}):
    return _runHooksDir(devicexml, 'before_device_create', vmconf=vmconf,
                        params=customProperties)


def after_device_create(devicexml, vmconf={}, customProperties={}):
    return _runHooksDir(devicexml, 'after_device_create', vmconf=vmconf,
                        params=customProperties, raiseError=False)


def before_device_destroy(devicexml, vmconf={}, customProperties={}):
    return _runHooksDir(devicexml, 'before_device_destroy', vmconf=vmconf,
                        params=customProperties)


def after_device_destroy(devicexml, vmconf={}, customProperties={}):
    return _runHooksDir(devicexml, 'after_device_destroy', vmconf=vmconf,
                        params=customProperties, raiseError=False)


def before_vm_start(domxml, vmconf={}, final_callback=None):
    errors = []
    final_xml = _runHooksDir(domxml, 'before_vm_start', vmconf=vmconf,
                             raiseError=False, errors=errors)
    if final_callback is not None:
        final_callback(final_xml)
    if errors:
        raise exception.HookError(errors[-1])
    return final_xml


def after_vm_start(domxml, vmconf={}):
    return _runHooksDir(domxml, 'after_vm_start',
                        vmconf=vmconf, raiseError=False)


def before_vm_cont(domxml, vmconf={}):
    return _runHooksDir(domxml, 'before_vm_cont', vmconf=vmconf)


def after_vm_cont(domxml, vmconf={}):
    return _runHooksDir(domxml, 'after_vm_cont',
                        vmconf=vmconf, raiseError=False)


def before_vm_pause(domxml, vmconf={}):
    return _runHooksDir(domxml, 'before_vm_pause', vmconf=vmconf)


def after_vm_pause(domxml, vmconf={}):
    return _runHooksDir(domxml, 'after_vm_pause',
                        vmconf=vmconf, raiseError=False)


def before_device_migrate_source(devicexml, vmconf={}, customProperties={}):
    return _runHooksDir(devicexml, 'before_device_migrate_source',
                        vmconf=vmconf, params=customProperties)


def after_device_migrate_source(devicexml, vmconf={}, customProperties={}):
    return _runHooksDir(devicexml, 'after_device_migrate_source',
                        vmconf=vmconf, params=customProperties,
                        raiseError=False)


def before_device_migrate_destination(
        devicexml, vmconf={}, customProperties={}):
    return _runHooksDir(devicexml, 'before_device_migrate_destination',
                        vmconf=vmconf, params=customProperties)


def after_device_migrate_destination(
        devicexml, vmconf={}, customProperties={}):
    return _runHooksDir(devicexml, 'after_device_migrate_destination',
                        vmconf=vmconf, params=customProperties,
                        raiseError=False)


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


def before_vm_dehibernate(domxml, vmconf={}, params={}):
    return _runHooksDir(domxml, 'before_vm_dehibernate', vmconf=vmconf,
                        params=params)


def after_vm_dehibernate(domxml, vmconf={}, params={}):
    return _runHooksDir(domxml, 'after_vm_dehibernate', vmconf=vmconf,
                        raiseError=False, params=params)


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


def before_update_device(devxml, vmconf={}, params={}):
    return _runHooksDir(devxml, 'before_update_device', vmconf=vmconf,
                        params=params)


def after_update_device(devxml, vmconf={}, params={}):
    return _runHooksDir(devxml, 'after_update_device', vmconf=vmconf,
                        raiseError=False, params=params)


def after_update_device_fail(devxml, vmconf={}, params={}):
    return _runHooksDir(devxml, 'after_update_device_fail', vmconf=vmconf,
                        raiseError=False, params=params)


def before_nic_hotplug(nicxml, vmconf={}, params={}):
    return _runHooksDir(nicxml, 'before_nic_hotplug', vmconf=vmconf,
                        params=params)


def after_nic_hotplug(nicxml, vmconf={}, params={}):
    return _runHooksDir(nicxml, 'after_nic_hotplug', vmconf=vmconf,
                        params=params, raiseError=False)


def before_nic_hotunplug(nicxml, vmconf={}, params={}):
    return _runHooksDir(nicxml, 'before_nic_hotunplug', vmconf=vmconf,
                        params=params)


def after_nic_hotunplug(nicxml, vmconf={}, params={}):
    return _runHooksDir(nicxml, 'after_nic_hotunplug', vmconf=vmconf,
                        params=params, raiseError=False)


def after_nic_hotplug_fail(nicxml, vmconf={}, params={}):
    return _runHooksDir(nicxml, 'after_nic_hotplug_fail', vmconf=vmconf,
                        params=params, raiseError=False)


def after_nic_hotunplug_fail(nicxml, vmconf={}, params={}):
    return _runHooksDir(nicxml, 'after_nic_hotunplug_fail', vmconf=vmconf,
                        params=params, raiseError=False)


def after_disk_prepare(disk_dict, vmconf={}):
    return _runHooksDir(disk_dict, 'after_disk_prepare', vmconf=vmconf,
                        raiseError=True, hookType=_JSON_HOOK)


def before_disk_hotplug(domxml, vmconf={}, params={}):
    return _runHooksDir(domxml, 'before_disk_hotplug', vmconf=vmconf,
                        params=params)


def after_disk_hotplug(domxml, vmconf={}, params={}):
    return _runHooksDir(domxml, 'after_disk_hotplug', vmconf=vmconf,
                        params=params, raiseError=False)


def before_disk_hotunplug(domxml, vmconf={}, params={}):
    return _runHooksDir(domxml, 'before_disk_hotunplug', vmconf=vmconf,
                        params=params)


def after_disk_hotunplug(domxml, vmconf={}, params={}):
    return _runHooksDir(domxml, 'after_disk_hotunplug', vmconf=vmconf,
                        params=params, raiseError=False)


def before_set_num_of_cpus(vmconf={}, params={}):
    return _runHooksDir(None, 'before_set_num_of_cpus', vmconf=vmconf,
                        params=params, raiseError=True)


def after_set_num_of_cpus(vmconf={}, params={}):
    return _runHooksDir(None, 'after_set_num_of_cpus', vmconf=vmconf,
                        params=params, raiseError=False)


def before_memory_hotplug(domxml, vmconf={}, params={}):
    return _runHooksDir(domxml, 'before_memory_hotplug', vmconf=vmconf,
                        params=params)


def after_memory_hotplug(domxml, vmconf={}, params={}):
    return _runHooksDir(domxml, 'after_memory_hotplug', vmconf=vmconf,
                        params=params, raiseError=False)


def before_vdsm_start():
    return _runHooksDir(None, 'before_vdsm_start', raiseError=False)


def after_vdsm_stop():
    return _runHooksDir(None, 'after_vdsm_stop', raiseError=False)


def before_network_setup(network_config_dict):
    return _runHooksDir(network_config_dict, 'before_network_setup',
                        hookType=_JSON_HOOK)


def after_network_setup(network_config_dict):
    return _runHooksDir(network_config_dict, 'after_network_setup',
                        raiseError=False, hookType=_JSON_HOOK)


def after_network_setup_fail(network_config_dict):
    return _runHooksDir(network_config_dict, 'after_network_setup_fail',
                        raiseError=False, hookType=_JSON_HOOK)


def before_get_vm_stats():
    return _runHooksDir({}, 'before_get_vm_stats', raiseError=True,
                        hookType=_JSON_HOOK)


def after_get_vm_stats(stats):
    return _runHooksDir(stats, 'after_get_vm_stats', raiseError=False,
                        hookType=_JSON_HOOK)


def before_get_all_vm_stats():
    return _runHooksDir({}, 'before_get_all_vm_stats', raiseError=True,
                        hookType=_JSON_HOOK)


def after_get_all_vm_stats(stats):
    return _runHooksDir(stats, 'after_get_all_vm_stats', raiseError=False,
                        hookType=_JSON_HOOK)


def before_get_caps():
    return _runHooksDir({}, 'before_get_caps', raiseError=True,
                        hookType=_JSON_HOOK)


def after_get_caps(caps):
    return _runHooksDir(caps, 'after_get_caps', raiseError=False,
                        hookType=_JSON_HOOK)


def before_get_stats():
    return _runHooksDir({}, 'before_get_stats', raiseError=True,
                        hookType=_JSON_HOOK)


def after_get_stats(caps):
    return _runHooksDir(caps, 'after_get_stats', raiseError=False,
                        hookType=_JSON_HOOK)


def after_hostdev_list_by_caps(devices):
    return _runHooksDir(devices, 'after_hostdev_list_by_caps',
                        raiseError=False, hookType=_JSON_HOOK)


def _getScriptInfo(path):
    try:
        with open(path, 'rb') as f:
            digest = hashlib.sha256(f.read()).hexdigest()
    except EnvironmentError:
        digest = ''
    return {'checksum': digest}


def load_vm_launch_flags_from_file(vm_id):
    flags_file = _LAUNCH_FLAGS_PATH % vm_id
    if os.path.isfile(flags_file):
        with open(flags_file) as f:
            return int(f.readline())
    # The following return value must match libvirt.VIR_DOMAIN_NONE
    return 0


def dump_vm_launch_flags_to_file(vm_id, flags):
    flags_file = _LAUNCH_FLAGS_PATH % vm_id

    dir_name = os.path.dirname(flags_file)
    try:
        os.makedirs(dir_name)
    except OSError:
        pass
    with open(flags_file, mode='w') as f:
        f.write(str(flags))


def remove_vm_launch_flags_file(vm_id):
    flags_file = _LAUNCH_FLAGS_PATH % vm_id
    os.remove(flags_file)


def _getHookInfo(dir):
    return dict((os.path.basename(script), _getScriptInfo(script))
                for script in _scriptsPerDir(dir))


def installed():
    res = {}
    for dir in os.listdir(P_VDSM_HOOKS):
        inf = _getHookInfo(dir)
        if inf:
            res[dir] = inf
    return res


if __name__ == '__main__':
    def usage():
        print('Usage: %s hook_name' % sys.argv[0])
        sys.exit(1)

    if len(sys.argv) >= 2:
        globals()[sys.argv[1]](*sys.argv[2:])
    else:
        usage()
