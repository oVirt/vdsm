# Copyright 2014-2017 Red Hat, Inc.
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
"""
When importing a VM a thread start with a new process of virt-v2v.
The way to feedback the information on the progress and the status of the
process (ie job) is via getVdsStats() with the fields progress and status.
progress is a number which represent percentage of a single disk copy,
status is a way to feedback information on the job (init, error etc)
"""

from __future__ import absolute_import

from collections import namedtuple
from contextlib import closing, contextmanager
import errno
import io
import logging
import os
import re
import subprocess
import tarfile
import time
import threading
import xml.etree.ElementTree as ET
import zipfile

import libvirt

from vdsm import libvirtconnection
from vdsm.cmdutils import wrap_command
from vdsm.commands import execCmd, BUFFSIZE
from vdsm.common import cmdutils
from vdsm.common import concurrent
from vdsm.common import password
from vdsm.common import response
from vdsm.common import zombiereaper
from vdsm.common.compat import CPopen
from vdsm.common.define import errCode, doneCode
from vdsm.common.logutils import traceback
from vdsm.common.time import monotonic_time
from vdsm.constants import P_VDSM_LOG, P_VDSM_RUN, EXT_KVM_2_OVIRT
from vdsm.utils import terminating, NICENESS, IOCLASS

try:
    import ovirt_imageio_common
except ImportError:
    ovirt_imageio_common = None


_lock = threading.Lock()
_jobs = {}

_V2V_DIR = os.path.join(P_VDSM_RUN, 'v2v')
_LOG_DIR = os.path.join(P_VDSM_LOG, 'import')
_VIRT_V2V = cmdutils.CommandPath('virt-v2v', '/usr/bin/virt-v2v')
_SSH_AGENT = cmdutils.CommandPath('ssh-agent', '/usr/bin/ssh-agent')
_SSH_ADD = cmdutils.CommandPath('ssh-add', '/usr/bin/ssh-add')
_XEN_SSH_PROTOCOL = 'xen+ssh'
_VMWARE_PROTOCOL = 'vpx'
_KVM_PROTOCOL = 'qemu'
_SSH_AUTH_RE = '(SSH_AUTH_SOCK)=([^;]+).*;\nSSH_AGENT_PID=(\d+)'
_OVF_RESOURCE_CPU = 3
_OVF_RESOURCE_MEMORY = 4
_OVF_RESOURCE_NETWORK = 10
_QCOW2_COMPAT_SUPPORTED = ('0.10', '1.1')

# OVF Specification:
# https://www.iso.org/obp/ui/#iso:std:iso-iec:17203:ed-1:v1:en
_OVF_NS = 'http://schemas.dmtf.org/ovf/envelope/1'
_RASD_NS = 'http://schemas.dmtf.org/wbem/wscim/1/cim-schema/2/' \
           'CIM_ResourceAllocationSettingData'

ImportProgress = namedtuple('ImportProgress',
                            ['current_disk', 'disk_count', 'description'])
DiskProgress = namedtuple('DiskProgress', ['progress'])


class STATUS:
    '''
    STARTING: request granted and starting the import process
    COPYING_DISK: copying disk in progress
    ABORTED: user initiated aborted
    FAILED: error during import process
    DONE: convert process successfully finished
    '''
    STARTING = 'starting'
    COPYING_DISK = 'copying_disk'
    ABORTED = 'aborted'
    FAILED = 'error'
    DONE = 'done'


class V2VError(Exception):
    ''' Base class for v2v errors '''
    err_name = 'unexpected'  # TODO: use more specific error


class ClientError(Exception):
    ''' Base class for client error '''
    err_name = 'unexpected'


class InvalidVMConfiguration(ValueError):
    ''' Unexpected error while parsing libvirt domain xml '''


class OutputParserError(V2VError):
    ''' Error while parsing virt-v2v output '''


class JobExistsError(ClientError):
    ''' Job already exists in _jobs collection '''
    err_name = 'JobExistsError'


class VolumeError(ClientError):
    ''' Error preparing volume '''


class NoSuchJob(ClientError):
    ''' Job not exists in _jobs collection '''
    err_name = 'NoSuchJob'


class JobNotDone(ClientError):
    ''' Import process still in progress '''
    err_name = 'JobNotDone'


class NoSuchOvf(V2VError):
    ''' Ovf path is not exists in /var/run/vdsm/v2v/ '''
    err_name = 'V2VNoSuchOvf'


class V2VProcessError(V2VError):
    ''' virt-v2v process had error in execution '''


class InvalidInputError(ClientError):
    ''' Invalid input received '''


def get_external_vms(uri, username, password, vm_names=None):
    if vm_names is not None:
        if not vm_names:
            vm_names = None
        else:
            vm_names = frozenset(vm_names)

    try:
        conn = libvirtconnection.open_connection(uri=uri,
                                                 username=username,
                                                 passwd=password)
    except libvirt.libvirtError as e:
        logging.exception('error connecting to hypervisor')
        return {'status': {'code': errCode['V2VConnection']['status']['code'],
                           'message': str(e)}}

    with closing(conn):
        vms = []
        for vm in _list_domains(conn):
            if vm_names is not None and vm.name() not in vm_names:
                # Skip this VM.
                continue
            elif conn.getType() == "ESX" and _vm_has_snapshot(vm):
                logging.error("vm %r has snapshots and therefore can not be "
                              "imported since snapshot conversion is not "
                              "supported for VMware", vm.name())
                continue
            _add_vm(conn, vms, vm)
        return {'status': doneCode, 'vmList': vms}


def get_external_vm_names(uri, username, password):
    try:
        conn = libvirtconnection.open_connection(uri=uri,
                                                 username=username,
                                                 passwd=password)
    except libvirt.libvirtError as e:
        logging.exception('error connecting to hypervisor')
        return response.error('V2VConnection', str(e))

    with closing(conn):
        vms = [vm.name() for vm in _list_domains(conn)]
        return response.success(vmNames=vms)


def convert_external_vm(uri, username, password, vminfo, job_id, irs):
    if uri.startswith(_XEN_SSH_PROTOCOL):
        command = XenCommand(uri, vminfo, job_id, irs)
    elif uri.startswith(_VMWARE_PROTOCOL):
        command = LibvirtCommand(uri, username, password, vminfo, job_id,
                                 irs)
    elif uri.startswith(_KVM_PROTOCOL):
        if ovirt_imageio_common is None:
            raise V2VError('Unsupported protocol KVM, ovirt_imageio_common'
                           'package is needed for importing KVM images')
        command = KVMCommand(uri, username, password, vminfo, job_id, irs)
    else:
        raise ClientError('Unknown protocol for Libvirt uri: %s', uri)
    job = ImportVm(job_id, command)
    job.start()
    _add_job(job_id, job)
    return {'status': doneCode}


def convert_ova(ova_path, vminfo, job_id, irs):
    command = OvaCommand(ova_path, vminfo, job_id, irs)
    job = ImportVm(job_id, command)
    job.start()
    _add_job(job_id, job)
    return response.success()


def get_ova_info(ova_path):
    ns = {'ovf': _OVF_NS, 'rasd': _RASD_NS}

    try:
        root = ET.fromstring(_read_ovf_from_ova(ova_path))
    except ET.ParseError as e:
        raise V2VError('Error reading ovf from ova, position: %r' % e.position)

    vm = {}
    _add_general_ovf_info(vm, root, ns, ova_path)
    _add_disks_ovf_info(vm, root, ns)
    _add_networks_ovf_info(vm, root, ns)

    return response.success(vmList=vm)


def get_converted_vm(job_id):
    try:
        job = _get_job(job_id)
        _validate_job_done(job)
        ovf = _read_ovf(job_id)
    except ClientError as e:
        logging.info('Converted VM error %s', e)
        return errCode[e.err_name]
    except V2VError as e:
        logging.error('Converted VM error %s', e)
        return errCode[e.err_name]
    return {'status': doneCode, 'ovf': ovf}


def delete_job(job_id):
    try:
        job = _get_job(job_id)
        _validate_job_finished(job)
        _remove_job(job_id)
    except ClientError as e:
        logging.info('Cannot delete job, error: %s', e)
        return errCode[e.err_name]
    return {'status': doneCode}


def abort_job(job_id):
    try:
        job = _get_job(job_id)
        job.abort()
    except ClientError as e:
        logging.info('Cannot abort job, error: %s', e)
        return errCode[e.err_name]
    return {'status': doneCode}


def get_jobs_status():
    ret = {}
    with _lock:
        items = tuple(_jobs.items())
    for job_id, job in items:
        ret[job_id] = {
            'status': job.status,
            'description': job.description,
            'progress': job.progress
        }
    return ret


def _add_job(job_id, job):
    with _lock:
        if job_id in _jobs:
            raise JobExistsError("Job %r exists" % job_id)
        _jobs[job_id] = job


def _get_job(job_id):
    with _lock:
        if job_id not in _jobs:
            raise NoSuchJob("No such job %r" % job_id)
        return _jobs[job_id]


def _remove_job(job_id):
    with _lock:
        if job_id not in _jobs:
            raise NoSuchJob("No such job %r" % job_id)
        del _jobs[job_id]


def _validate_job_done(job):
    if job.status != STATUS.DONE:
        raise JobNotDone("Job %r is %s" % (job.id, job.status))


def _validate_job_finished(job):
    if job.status not in (STATUS.DONE, STATUS.FAILED, STATUS.ABORTED):
        raise JobNotDone("Job %r is %s" % (job.id, job.status))


def _read_ovf(job_id):
    file_name = os.path.join(_V2V_DIR, "%s.ovf" % job_id)
    try:
        with open(file_name, 'r') as f:
            return f.read()
    except IOError as e:
        if e.errno != errno.ENOENT:
            raise
        raise NoSuchOvf("No such ovf %r" % file_name)


class SSHAgent(object):
    """
    virt-v2v uses ssh-agent for importing xen vms from libvirt,
    after virt-v2v log in to the machine it needs to copy its disks
    which ssh-agent let it handle without passwords while the session
    is on.
    for more information please refer to the virt-v2v man page:
    http://libguestfs.org/virt-v2v.1.html
    """
    def __init__(self):
        self._auth = None
        self._agent_pid = None
        self._ssh_auth_re = re.compile(_SSH_AUTH_RE)

    def __enter__(self):
        rc, out, err = execCmd([_SSH_AGENT.cmd], raw=True)
        if rc != 0:
            raise V2VError('Error init ssh-agent, exit code: %r'
                           ', out: %r, err: %r' %
                           (rc, out, err))

        m = self._ssh_auth_re.match(out)
        # looking for: SSH_AUTH_SOCK=/tmp/ssh-VEE74ObhTWBT/agent.29917
        self._auth = {m.group(1): m.group(2)}
        self._agent_pid = m.group(3)

        try:
            rc, out, err = execCmd([_SSH_ADD.cmd], env=self._auth)
        except:
            self._kill_agent()
            raise

        if rc != 0:
            # 1 = general fail
            # 2 = no agnet
            if rc != 2:
                self._kill_agent()
            raise V2VError('Error init ssh-add, exit code: %r'
                           ', out: %r, err: %r' %
                           (rc, out, err))

    def __exit__(self, *args):
        rc, out, err = execCmd([_SSH_ADD.cmd, '-d'], env=self._auth)
        if rc != 0:
            logging.error('Error deleting ssh-add, exit code: %r'
                          ', out: %r, err: %r' %
                          (rc, out, err))

        self._kill_agent()

    def _kill_agent(self):
        rc, out, err = execCmd([_SSH_AGENT.cmd, '-k'],
                               env={'SSH_AGENT_PID': self._agent_pid})
        if rc != 0:
            logging.error('Error killing ssh-agent (PID=%r), exit code: %r'
                          ', out: %r, err: %r' %
                          (self._agent_pid, rc, out, err))

    @property
    def auth(self):
        return self._auth


class V2VCommand(object):
    def __init__(self, vminfo, vmid, irs):
        self._vminfo = vminfo
        self._vmid = vmid
        self._irs = irs
        self._prepared_volumes = []
        self._passwd_file = os.path.join(_V2V_DIR, "%s.tmp" % vmid)
        self._password = password.ProtectedPassword('')
        self._base_command = [_VIRT_V2V.cmd, '-v', '-x']
        self._query_v2v_caps()
        if 'qcow2_compat' in vminfo:
            qcow2_compat = vminfo['qcow2_compat']
            if qcow2_compat not in _QCOW2_COMPAT_SUPPORTED:
                logging.error('Invalid QCOW2 compat version %r' %
                              qcow2_compat)
                raise ValueError('Invalid QCOW2 compat version %r' %
                                 qcow2_compat)
            if 'vdsm-compat-option' in self._v2v_caps:
                self._base_command.extend(['--vdsm-compat', qcow2_compat])
            elif qcow2_compat != '0.10':
                # Note: qcow2 is only a suggestion from the engine
                # if virt-v2v doesn't support it we fall back to default
                logging.info('virt-v2v not supporting qcow2 compat version: '
                             '%r', qcow2_compat)

    def execute(self):
        raise NotImplementedError("Subclass must implement this")

    def _command(self):
        raise NotImplementedError("Subclass must implement this")

    def _start_helper(self):
        timestamp = time.strftime('%Y%m%dT%H%M%S')
        log = os.path.join(_LOG_DIR,
                           "import-%s-%s.log" % (self._vmid, timestamp))
        logging.info("Storing import log at: %r", log)
        v2v = _simple_exec_cmd(self._command(),
                               nice=NICENESS.HIGH,
                               ioclass=IOCLASS.IDLE,
                               env=self._environment(),
                               stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT)
        tee = _simple_exec_cmd(['tee', log],
                               nice=NICENESS.HIGH,
                               ioclass=IOCLASS.IDLE,
                               stdin=v2v.stdout,
                               stdout=subprocess.PIPE)

        return PipelineProc(v2v, tee)

    def _get_disk_format(self):
        fmt = self._vminfo.get('format', 'raw').lower()
        return "qcow2" if fmt == "cow" else fmt

    def _disk_parameters(self):
        parameters = []
        for disk in self._vminfo['disks']:
            try:
                parameters.append('--vdsm-image-uuid')
                parameters.append(disk['imageID'])
                parameters.append('--vdsm-vol-uuid')
                parameters.append(disk['volumeID'])
            except KeyError as e:
                raise InvalidInputError('Job %r missing required property: %s'
                                        % (self._vmid, e))
        return parameters

    @contextmanager
    def _volumes(self):
        self._prepare_volumes()
        try:
            yield
        finally:
            self._teardown_volumes()

    def _prepare_volumes(self):
        if len(self._vminfo['disks']) < 1:
            raise InvalidInputError('Job %r cannot import vm with no disk',
                                    self._vmid)

        for disk in self._vminfo['disks']:
            drive = {'poolID': self._vminfo['poolID'],
                     'domainID': self._vminfo['domainID'],
                     'volumeID': disk['volumeID'],
                     'imageID': disk['imageID']}
            res = self._irs.prepareImage(drive['domainID'],
                                         drive['poolID'],
                                         drive['imageID'],
                                         drive['volumeID'])
            if res['status']['code']:
                raise VolumeError('Job %r bad volume specification: %s' %
                                  (self._vmid, drive))

            drive['path'] = res['path']
            self._prepared_volumes.append(drive)

    def _teardown_volumes(self):
        for drive in self._prepared_volumes:
            try:
                self._irs.teardownImage(drive['domainID'],
                                        drive['poolID'],
                                        drive['imageID'])
            except Exception as e:
                logging.error('Job %r error tearing down drive: %s',
                              self._vmid, e)

    def _get_storage_domain_path(self, path):
        '''
        prepareImage returns /prefix/sdUUID/images/imgUUID/volUUID
        we need storage domain absolute path so we go up 3 levels
        '''
        return path.rsplit(os.sep, 3)[0]

    def _environment(self):
        # Provide some sane environment
        env = os.environ.copy()

        # virt-v2v specific variables
        env['LIBGUESTFS_BACKEND'] = 'direct'
        if 'virtio_iso_path' in self._vminfo:
            env['VIRTIO_WIN'] = self._vminfo['virtio_iso_path']
        return env

    @contextmanager
    def _password_file(self):
        fd = os.open(self._passwd_file, os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            if self._password.value is None:
                os.write(fd, "")
            else:
                os.write(fd, self._password.value)
        finally:
            os.close(fd)
        try:
            yield
        finally:
            try:
                os.remove(self._passwd_file)
            except Exception:
                logging.exception("Job %r error removing passwd file: %s",
                                  self._vmid, self._passwd_file)

    def _query_v2v_caps(self):
        self._v2v_caps = frozenset()
        p = _simple_exec_cmd([_VIRT_V2V.cmd, '--machine-readable'],
                             env=os.environ.copy(),
                             stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE)
        with terminating(p):
            try:
                out, err = p.communicate()
            except Exception:
                logging.exception('Terminating virt-v2v process after error')
                raise
        if p.returncode != 0:
            raise V2VProcessError(
                'virt-v2v exited with code: %d, stderr: %r' %
                (p.returncode, err))

        self._v2v_caps = frozenset(out.splitlines())
        logging.debug("Detected virt-v2v capabilities: %r", self._v2v_caps)


class LibvirtCommand(V2VCommand):
    def __init__(self, uri, username, password, vminfo, vmid, irs):
        super(LibvirtCommand, self).__init__(vminfo, vmid, irs)
        self._uri = uri
        self._username = username
        self._password = password

    def _command(self):
        cmd = self._base_command
        cmd.extend(['-ic', self._uri,
                    '-o', 'vdsm',
                    '-of', self._get_disk_format(),
                    '-oa', self._vminfo.get('allocation', 'sparse').lower()])
        cmd.extend(self._disk_parameters())
        cmd.extend(['--password-file',
                    self._passwd_file,
                    '--vdsm-vm-uuid',
                    self._vmid,
                    '--vdsm-ovf-output',
                    _V2V_DIR,
                    '--machine-readable',
                    '-os',
                    self._get_storage_domain_path(
                        self._prepared_volumes[0]['path']),
                    self._vminfo['vmName']])
        return cmd

    @contextmanager
    def execute(self):
        with self._volumes(), self._password_file():
            yield self._start_helper()


class OvaCommand(V2VCommand):
    def __init__(self, ova_path, vminfo, vmid, irs):
        super(OvaCommand, self).__init__(vminfo, vmid, irs)
        self._ova_path = ova_path

    def _command(self):
        cmd = self._base_command
        cmd.extend(['-i', 'ova', self._ova_path,
                    '-o', 'vdsm',
                    '-of', self._get_disk_format(),
                    '-oa', self._vminfo.get('allocation', 'sparse').lower(),
                    '--vdsm-vm-uuid',
                    self._vmid,
                    '--vdsm-ovf-output',
                    _V2V_DIR,
                    '--machine-readable',
                    '-os',
                    self._get_storage_domain_path(
                        self._prepared_volumes[0]['path'])])
        cmd.extend(self._disk_parameters())
        return cmd

    @contextmanager
    def execute(self):
        with self._volumes():
            yield self._start_helper()


class XenCommand(V2VCommand):
    """
    Importing Xen via virt-v2v require to use xen+ssh protocol.
    this requires:
    - enable the vdsm user in /etc/passwd
    - generate ssh keys via ssh-keygen
    - public key exchange with the importing hosts user
    - host must be in ~/.ssh/known_hosts (done automatically
      by ssh to the host before importing vm)
    """
    def __init__(self, uri, vminfo, job_id, irs):
        super(XenCommand, self).__init__(vminfo, job_id, irs)
        self._uri = uri
        self._ssh_agent = SSHAgent()

    def _command(self):
        cmd = self._base_command
        cmd.extend(['-ic', self._uri,
                    '-o', 'vdsm',
                    '-of', self._get_disk_format(),
                    '-oa', self._vminfo.get('allocation', 'sparse').lower()])
        cmd.extend(self._disk_parameters())
        cmd.extend(['--vdsm-vm-uuid',
                    self._vmid,
                    '--vdsm-ovf-output',
                    _V2V_DIR,
                    '--machine-readable',
                    '-os',
                    self._get_storage_domain_path(
                        self._prepared_volumes[0]['path']),
                    self._vminfo['vmName']])
        return cmd

    @contextmanager
    def execute(self):
        with self._volumes(), self._ssh_agent:
            yield self._start_helper()

    def _environment(self):
        env = super(XenCommand, self)._environment()
        env.update(self._ssh_agent.auth)
        return env


class KVMCommand(V2VCommand):
    def __init__(self, uri, username, password, vminfo, vmid, irs):
        super(KVMCommand, self).__init__(vminfo, vmid, irs)
        self._uri = uri
        self._username = username
        self._password = password

    def _command(self):
        cmd = [EXT_KVM_2_OVIRT,
               '--uri', self._uri]
        if self._username is not None:
            cmd.extend([
                '--username', self._username,
                '--password-file', self._passwd_file])
        src, fmt = self._source_images()
        cmd.append('--source')
        cmd.extend(src)
        cmd.append('--dest')
        cmd.extend(self._dest_images())
        cmd.append('--storage-type')
        cmd.extend(fmt)
        cmd.append('--vm-name')
        cmd.append(self._vminfo['vmName'])
        return cmd

    @contextmanager
    def execute(self):
        with self._volumes(), self._password_file():
            yield self._start_helper()

    def _source_images(self):
        con = libvirtconnection.open_connection(uri=self._uri,
                                                username=self._username,
                                                passwd=self._password)

        with closing(con):
            vm = con.lookupByName(self._vminfo['vmName'])
            if vm:
                params = {}
                root = ET.fromstring(vm.XMLDesc(0))
                _add_disks(root, params)
                src = []
                fmt = []
                for disk in params['disks']:
                    if 'alias' in disk:
                        src.append(disk['alias'])
                        fmt.append(disk['disktype'])
                return src, fmt

    def _dest_images(self):
        ret = []
        for vol in self._prepared_volumes:
            ret.append(vol['path'])
        return ret


class PipelineProc(object):

    def __init__(self, proc1, proc2):
        self._procs = (proc1, proc2)
        self._stdout = proc2.stdout

    def kill(self):
        """
        Kill all processes in a pipeline.

        Some of the processes may have already terminated, but some may be
        still running. Regular kill() raises OSError if the process has already
        terminated. Since we are dealing with multiple processes, to avoid any
        confusion we do not raise OSError at all.
        """
        for p in self._procs:
            logging.debug("Killing pid=%d", p.pid)
            try:
                p.kill()
            except OSError as e:
                # Probably the process has already terminated
                if e.errno != errno.ESRCH:
                    raise e

    @property
    def pids(self):
        return [p.pid for p in self._procs]

    @property
    def returncode(self):
        """
        Returns None if any of the processes is still running. Returns 0 if all
        processes have finished with a zero exit code, otherwise return first
        nonzero exit code.
        """
        ret = 0
        for p in self._procs:
            p.poll()
            if p.returncode is None:
                return None
            if p.returncode != 0 and ret == 0:
                # One of the processes has failed
                ret = p.returncode

        # All processes have finished
        return ret

    @property
    def stdout(self):
        return self._stdout

    def wait(self, timeout=None):
        if timeout is not None:
            deadline = monotonic_time() + timeout
        else:
            deadline = None

        for p in self._procs:
            if deadline is not None:
                # NOTE: CPopen doesn't support timeout argument.
                while monotonic_time() < deadline:
                    p.poll()
                    if p.returncode is not None:
                        break
                    time.sleep(1)
            else:
                p.wait()

        if deadline is not None:
            if deadline < monotonic_time() or self.returncode is None:
                # Timed out
                return False

        return True


class ImportVm(object):
    TERM_DELAY = 30
    PROC_WAIT_TIMEOUT = 30

    def __init__(self, job_id, command):
        self._id = job_id
        self._command = command
        self._thread = None

        self._status = STATUS.STARTING
        self._description = ''
        self._disk_progress = 0
        self._disk_count = 1
        self._current_disk = 1
        self._aborted = False
        self._proc = None

    def start(self):
        self._thread = concurrent.thread(self._run, name="v2v/" + self._id[:8])
        self._thread.start()

    def wait(self):
        if self._thread is not None and self._thread.is_alive():
            self._thread.join()

    @property
    def id(self):
        return self._id

    @property
    def status(self):
        return self._status

    @property
    def description(self):
        return self._description

    @property
    def progress(self):
        '''
        progress is part of multiple disk_progress its
        flat and not 100% accurate - each disk take its
        portion ie if we have 2 disks the first will take
        0-50 and the second 50-100
        '''
        completed = (self._disk_count - 1) * 100
        return (completed + self._disk_progress) / self._disk_count

    @traceback(msg="Error importing vm")
    def _run(self):
        try:
            self._import()
        except Exception as ex:
            if self._aborted:
                logging.debug("Job %r was aborted", self._id)
            else:
                logging.exception("Job %r failed", self._id)
                self._status = STATUS.FAILED
                self._description = str(ex)
                try:
                    if self._proc is not None:
                        self._abort()
                except Exception as e:
                    logging.exception('Job %r, error trying to abort: %r',
                                      self._id, e)

    def _import(self):
        logging.info('Job %r starting import', self._id)

        with self._command.execute() as self._proc:
            self._watch_process_output()
            self._wait_for_process()

            if self._proc.returncode != 0:
                raise V2VProcessError('Job %r process failed exit-code: %r' %
                                      (self._id,
                                       self._proc.returncode))

            if self._status != STATUS.ABORTED:
                self._status = STATUS.DONE
                logging.info('Job %r finished import successfully',
                             self._id)

    def _wait_for_process(self):
        if self._proc.returncode is not None:
            return
        logging.debug("Job %r waiting for virt-v2v process", self._id)
        if not self._proc.wait(timeout=self.PROC_WAIT_TIMEOUT):
            raise V2VProcessError("Job %r timeout waiting for process pid=%s",
                                  self._id, self._proc.pids)

    def _watch_process_output(self):
        out = io.BufferedReader(io.FileIO(self._proc.stdout.fileno(),
                                mode='r', closefd=False), BUFFSIZE)
        parser = OutputParser()
        for event in parser.parse(out):
            if isinstance(event, ImportProgress):
                self._status = STATUS.COPYING_DISK
                logging.info("Job %r copying disk %d/%d",
                             self._id, event.current_disk, event.disk_count)
                self._disk_progress = 0
                self._current_disk = event.current_disk
                self._disk_count = event.disk_count
                self._description = event.description
            elif isinstance(event, DiskProgress):
                self._disk_progress = event.progress
                if event.progress % 10 == 0:
                    logging.info("Job %r copy disk %d progress %d/100",
                                 self._id, self._current_disk, event.progress)
            else:
                raise RuntimeError("Job %r got unexpected parser event: %s" %
                                   (self._id, event))

    def abort(self):
        self._status = STATUS.ABORTED
        logging.info('Job %r aborting...', self._id)
        self._abort()

    def _abort(self):
        self._aborted = True
        if self._proc is None:
            logging.warning(
                'Ignoring request to abort job %r; the job failed to start',
                self._id)
            return
        if self._proc.returncode is None:
            logging.debug('Job %r killing virt-v2v process', self._id)
            try:
                self._proc.kill()
            except OSError as e:
                if e.errno != errno.ESRCH:
                    raise
                logging.debug('Job %r virt-v2v process not running',
                              self._id)
            else:
                logging.debug('Job %r virt-v2v process was killed',
                              self._id)
            finally:
                for pid in self._proc.pids:
                    zombiereaper.autoReapPID(pid)


class OutputParser(object):
    COPY_DISK_RE = re.compile(r'.*(Copying disk (\d+)/(\d+)).*')
    DISK_PROGRESS_RE = re.compile(r'\s+\((\d+).*')

    def parse(self, stream):
        for line in stream:
            if 'Copying disk' in line:
                description, current_disk, disk_count = self._parse_line(line)
                yield ImportProgress(int(current_disk), int(disk_count),
                                     description)
                for chunk in self._iter_progress(stream):
                    progress = self._parse_progress(chunk)
                    if progress is not None:
                        yield DiskProgress(progress)
                    if progress == 100:
                        break

    def _parse_line(self, line):
        m = self.COPY_DISK_RE.match(line)
        if m is None:
            raise OutputParserError('unexpected format in "Copying disk"'
                                    ', line: %r' % line)
        return m.group(1), m.group(2), m.group(3)

    def _iter_progress(self, stream):
        chunk = ''
        while True:
            c = stream.read(1)
            if not c:
                raise OutputParserError('copy-disk stream closed unexpectedly')
            chunk += c
            if c == '\r':
                yield chunk
                chunk = ''

    def _parse_progress(self, chunk):
        m = self.DISK_PROGRESS_RE.match(chunk)
        if m is None:
            return None
        try:
            return int(m.group(1))
        except ValueError:
            raise OutputParserError('error parsing progress regex: %r'
                                    % m.groups)


def _mem_to_mib(size, unit):
    lunit = unit.lower()
    if lunit in ('bytes', 'b'):
        return size / 1024 / 1024
    elif lunit in ('kib', 'k'):
        return size / 1024
    elif lunit in ('mib', 'm'):
        return size
    elif lunit in ('gib', 'g'):
        return size * 1024
    elif lunit in ('tib', 't'):
        return size * 1024 * 1024
    else:
        raise InvalidVMConfiguration("Invalid currentMemory unit attribute:"
                                     " %r" % unit)


def _list_domains(conn):
    try:
        for vm in conn.listAllDomains():
            yield vm
    # TODO: use only the new API (no need to fall back to listDefinedDomains)
    #       when supported in Xen under RHEL 5.x
    except libvirt.libvirtError as e:
        if e.get_error_code() != libvirt.VIR_ERR_NO_SUPPORT:
            raise
        # Support for old libvirt clients
        seen = set()
        for name in conn.listDefinedDomains():
            try:
                vm = conn.lookupByName(name)
            except libvirt.libvirtError as e:
                logging.error("Error looking up vm %r: %s", name, e)
            else:
                seen.add(name)
                yield vm
        for domainId in conn.listDomainsID():
            try:
                vm = conn.lookupByID(domainId)
            except libvirt.libvirtError as e:
                logging.error("Error looking up vm by id %r: %s", domainId, e)
            else:
                if vm.name() not in seen:
                    yield vm


def _add_vm(conn, vms, vm):
    params = {}
    try:
        _add_vm_info(vm, params)
    except libvirt.libvirtError as e:
        logging.error("error getting domain information: %s", e)
        return
    try:
        xml = vm.XMLDesc(0)
    except libvirt.libvirtError as e:
        logging.error("error getting domain xml for vm %r: %s",
                      vm.name(), e)
        return
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as e:
        logging.error('error parsing domain xml: %s', e)
        return
    if not _block_disk_supported(conn, root):
        return
    try:
        _add_general_info(root, params)
    except InvalidVMConfiguration as e:
        logging.error("error adding general info: %s", e)
        return
    _add_snapshot_info(conn, vm, params)
    _add_networks(root, params)
    _add_disks(root, params)
    _add_graphics(root, params)
    _add_video(root, params)

    disk_info = None
    for disk in params['disks']:
        disk_info = _get_disk_info(conn, disk, vm)
        if disk_info is None:
            break
        disk.update(disk_info)
    if disk_info is not None:
        vms.append(params)
    else:
        logging.warning('Cannot add VM %s due to disk storage error',
                        vm.name())


def _block_disk_supported(conn, root):
    '''
    Currently we do not support importing VMs with block device from
    Xen on Rhel 5.x
    '''
    if conn.getType() == 'Xen':
        block_disks = root.findall('.//disk[@type="block"]')
        block_disks = [d for d in block_disks
                       if d.attrib.get('device', None) == "disk"]
        return len(block_disks) == 0

    return True


def _add_vm_info(vm, params):
    params['vmName'] = vm.name()
    # TODO: use new API: vm.state()[0] == libvirt.VIR_DOMAIN_SHUTOFF
    #       when supported in Xen under RHEL 5.x
    if vm.isActive():
        params['status'] = "Up"
    else:
        params['status'] = "Down"


def _add_general_info(root, params):
    e = root.find('./uuid')
    if e is not None:
        params['vmId'] = e.text

    e = root.find('./currentMemory')
    if e is not None:
        try:
            size = int(e.text)
        except ValueError:
            raise InvalidVMConfiguration("Invalid 'currentMemory' value: %r"
                                         % e.text)
        unit = e.get('unit', 'KiB')
        params['memSize'] = _mem_to_mib(size, unit)

    e = root.find('./vcpu')
    if e is not None:
        try:
            params['smp'] = int(e.text)
        except ValueError:
            raise InvalidVMConfiguration("Invalid 'vcpu' value: %r" % e.text)

    e = root.find('./os/type/[@arch]')
    if e is not None:
        params['arch'] = e.get('arch')


def _get_disk_info(conn, disk, vm):
    if 'alias' in disk.keys():
        try:
            if disk['disktype'] == 'file':
                vol = conn.storageVolLookupByPath(disk['alias'])
                _, capacity, alloc = vol.info()
            elif disk['disktype'] == 'block':
                vol = vm.blockInfo(disk['alias'])
                # We use the physical for allocation
                # in blockInfo can report 0
                capacity, _, alloc = vol
            else:
                logging.error('Unsupported disk type: %r', disk['disktype'])

        except libvirt.libvirtError:
            logging.exception("Error getting disk size")
            return None
        else:
            return {'capacity': str(capacity), 'allocation': str(alloc)}
    return {}


def _convert_disk_format(format):
    # TODO: move to volume format when storage/volume.py
    #       will be accessible for /lib/vdsm/v2v.py
    if format == 'qcow2':
        return 'COW'
    elif format == 'raw':
        return 'RAW'
    raise KeyError


def _add_disks(root, params):
    params['disks'] = []
    disks = root.findall('.//disk[@type="file"]')
    disks = disks + root.findall('.//disk[@type="block"]')
    for disk in disks:
        d = {}
        disktype = disk.get('type')
        device = disk.get('device')
        if device is not None:
            if device == 'cdrom':
                # Skip CD-ROM drives
                continue
            d['type'] = device
        target = disk.find('./target/[@dev]')
        if target is not None:
            d['dev'] = target.get('dev')
        if disktype == 'file':
            d['disktype'] = 'file'
            source = disk.find('./source/[@file]')
            if source is not None:
                d['alias'] = source.get('file')
        elif disktype == 'block':
            d['disktype'] = 'block'
            source = disk.find('./source/[@dev]')
            if source is not None:
                d['alias'] = source.get('dev')
        else:
            logging.error('Unsupported disk type: %r', type)

        driver = disk.find('./driver/[@type]')
        if driver is not None:
            try:
                d["format"] = _convert_disk_format(driver.get('type'))
            except KeyError:
                logging.warning("Disk %s has unsupported format: %r", d,
                                format)
        params['disks'].append(d)


def _add_graphics(root, params):
    e = root.find('./devices/graphics/[@type]')
    if e is not None:
        params['graphics'] = e.get('type')


def _add_video(root, params):
    e = root.find('./devices/video/model/[@type]')
    if e is not None:
        params['video'] = e.get('type')


def _add_networks(root, params):
    params['networks'] = []
    interfaces = root.findall('.//interface')
    for iface in interfaces:
        i = {}
        if 'type' in iface.attrib:
            i['type'] = iface.attrib['type']
        mac = iface.find('./mac/[@address]')
        if mac is not None:
            i['macAddr'] = mac.get('address')
        source = iface.find('./source/[@bridge]')
        if source is not None:
            i['bridge'] = source.get('bridge')
        target = iface.find('./target/[@dev]')
        if target is not None:
            i['dev'] = target.get('dev')
        model = iface.find('./model/[@type]')
        if model is not None:
            i['model'] = model.get('type')
        params['networks'].append(i)


def _add_snapshot_info(conn, vm, params):
    # Snapshot related API is not yet implemented in the libvirt's Xen driver
    if conn.getType() == 'Xen':
        return

    try:
        ret = vm.hasCurrentSnapshot()
    except libvirt.libvirtError:
        logging.exception('Error checking for existing snapshots.')
    else:
        params['has_snapshots'] = ret > 0


def _vm_has_snapshot(vm):
    try:
        return vm.hasCurrentSnapshot() == 1
    except libvirt.libvirtError:
        logging.exception('Error checking if snapshot exist for vm: %s.',
                          vm.name())
        return False


def _read_ovf_from_ova(ova_path):
    """
       virt-v2v support ova in tar, zip formats as well as
       extracted directory
    """
    if os.path.isdir(ova_path):
        return _read_ovf_from_ova_dir(ova_path)
    elif zipfile.is_zipfile(ova_path):
        return _read_ovf_from_zip_ova(ova_path)
    elif tarfile.is_tarfile(ova_path):
        return _read_ovf_from_tar_ova(ova_path)
    raise ClientError('Unknown ova format, supported formats:'
                      ' tar, zip or a directory')


def _find_ovf(entries):
    for entry in entries:
        if '.ovf' == os.path.splitext(entry)[1].lower():
            return entry
    return None


def _read_ovf_from_ova_dir(ova_path):
    files = os.listdir(ova_path)
    name = _find_ovf(files)
    if name is not None:
        with open(os.path.join(ova_path, name), 'r') as ovf_file:
            return ovf_file.read()
    raise ClientError('OVA directory %s does not contain ovf file' % ova_path)


def _read_ovf_from_zip_ova(ova_path):
    with open(ova_path, 'rb') as fh:
        zf = zipfile.ZipFile(fh)
        name = _find_ovf(zf.namelist())
        if name is not None:
            return zf.read(name)
    raise ClientError('OVA does not contains file with .ovf suffix')


def _read_ovf_from_tar_ova(ova_path):
    with tarfile.open(ova_path) as tar:
        for member in tar:
            if member.name.endswith('.ovf'):
                with closing(tar.extractfile(member)) as ovf:
                    return ovf.read()
        raise ClientError('OVA does not contains file with .ovf suffix')


def _add_general_ovf_info(vm, node, ns, ova_path):
    vm['status'] = 'Down'
    vmName = node.find('./ovf:VirtualSystem/ovf:Name', ns)
    if vmName is not None:
        vm['vmName'] = vmName.text
    else:
        vm['vmName'] = os.path.splitext(os.path.basename(ova_path))[0]

    memSize = node.find('.//ovf:Item[rasd:ResourceType="%d"]/'
                        'rasd:VirtualQuantity' % _OVF_RESOURCE_MEMORY, ns)
    if memSize is not None:
        vm['memSize'] = int(memSize.text)
    else:
        raise V2VError('Error parsing ovf information: no memory size')

    smp = node.find('.//ovf:Item[rasd:ResourceType="%d"]/'
                    'rasd:VirtualQuantity' % _OVF_RESOURCE_CPU, ns)
    if smp is not None:
        vm['smp'] = int(smp.text)
    else:
        raise V2VError('Error parsing ovf information: no cpu info')


def _get_max_disk_size(populated_size, size):
    if populated_size is None:
        return size
    if size is None:
        return populated_size
    return str(max(int(populated_size), int(size)))


def _parse_allocation_units(units):
    """
    Parse allocation units of the form "bytes * x * y^z"
    The format is defined in:
    DSP0004: Common Information Model (CIM) Infrastructure,
    ANNEX C.1 Programmatic Units

    We conform only to the subset of the format specification and
    base-units must be bytes.
    """
    # Format description
    sp = '[ \t\n]?'
    base_unit = 'byte'
    operator = '[*]'  # we support only multiplication
    number = '[+]?[0-9]+'  # we support only positive integers
    exponent = '[+]?[0-9]+'  # we support only positive integers
    modifier1 = '(?P<m1>{op}{sp}(?P<m1_num>{num}))'.format(
        op=operator,
        num=number,
        sp=sp)
    modifier2 = \
        '(?P<m2>{op}{sp}' \
        '(?P<m2_base>[0-9]+){sp}\^{sp}(?P<m2_exp>{exp}))'.format(
            op=operator,
            exp=exponent,
            sp=sp)
    r = '^{base_unit}({sp}{mod1})?({sp}{mod2})?$'.format(
        base_unit=base_unit,
        mod1=modifier1,
        mod2=modifier2,
        sp=sp)

    m = re.match(r, units, re.MULTILINE)
    if m is None:
        raise V2VError('Failed to parse allocation units: %r' % units)
    g = m.groupdict()

    ret = 1
    if g['m1'] is not None:
        try:
            ret *= int(g['m1_num'])
        except ValueError:
            raise V2VError("Failed to parse allocation units: %r" % units)
    if g['m2'] is not None:
        try:
            ret *= pow(int(g['m2_base']), int(g['m2_exp']))
        except ValueError:
            raise V2VError("Failed to parse allocation units: %r" % units)

    return ret


def _add_disks_ovf_info(vm, node, ns):
    vm['disks'] = []
    for d in node.findall(".//ovf:DiskSection/ovf:Disk", ns):
        disk = {'type': 'disk'}
        capacity = int(d.attrib.get('{%s}capacity' % _OVF_NS))
        if '{%s}capacityAllocationUnits' % _OVF_NS in d.attrib:
            units = d.attrib.get('{%s}capacityAllocationUnits' % _OVF_NS)
            capacity *= _parse_allocation_units(units)
        disk['capacity'] = str(capacity)
        fileref = d.attrib.get('{%s}fileRef' % _OVF_NS)
        alias = node.find('.//ovf:References/ovf:File[@ovf:id="%s"]' %
                          fileref, ns)
        if alias is not None:
            disk['alias'] = alias.attrib.get('{%s}href' % _OVF_NS)
            populated_size = d.attrib.get('{%s}populatedSize' % _OVF_NS, None)
            size = alias.attrib.get('{%s}size' % _OVF_NS)
            disk['allocation'] = _get_max_disk_size(populated_size, size)
        else:
            raise V2VError('Error parsing ovf information: disk href info')
        vm['disks'].append(disk)


def _add_networks_ovf_info(vm, node, ns):
    vm['networks'] = []
    for n in node.findall('.//ovf:Item[rasd:ResourceType="%d"]'
                          % _OVF_RESOURCE_NETWORK, ns):
        net = {}
        dev = n.find('./rasd:ElementName', ns)
        if dev is not None:
            net['dev'] = dev.text
        else:
            raise V2VError('Error parsing ovf information: '
                           'network element name')

        model = n.find('./rasd:ResourceSubType', ns)
        if model is not None:
            net['model'] = model.text
        else:
            raise V2VError('Error parsing ovf information: network model')

        bridge = n.find('./rasd:Connection', ns)
        if bridge is not None:
            net['bridge'] = bridge.text
            net['type'] = 'bridge'
        else:
            net['type'] = 'interface'
        vm['networks'].append(net)


def _simple_exec_cmd(command, env=None, nice=None, ioclass=None,
                     stdin=None, stdout=None, stderr=None):

    command = wrap_command(command, with_ioclass=ioclass,
                           ioclassdata=None, with_nice=nice,
                           with_setsid=False, with_sudo=False,
                           reset_cpu_affinity=True)

    logging.debug(cmdutils.command_log_line(command, cwd=None))

    p = CPopen(command, close_fds=True, cwd=None, env=env,
               stdin=stdin, stdout=stdout, stderr=stderr)
    return p
