# Copyright 2014 Red Hat, Inc.
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

from collections import namedtuple
from contextlib import closing, contextmanager
import errno
import logging
import os
import re
import signal
import tarfile
import threading
import xml.etree.ElementTree as ET
import zipfile

import libvirt

from vdsm.constants import P_VDSM_RUN
from vdsm.define import errCode, doneCode
from vdsm import libvirtconnection, response, concurrent
from vdsm.infra import zombiereaper
from vdsm.utils import traceback, CommandPath, execCmd, NICENESS, IOCLASS

import caps


_lock = threading.Lock()
_jobs = {}

_V2V_DIR = os.path.join(P_VDSM_RUN, 'v2v')
_VIRT_V2V = CommandPath('virt-v2v', '/usr/bin/virt-v2v')
_OVF_RESOURCE_CPU = 3
_OVF_RESOURCE_MEMORY = 4
_OVF_RESOURCE_NETWORK = 10

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


class ClientError(Exception):
    ''' Base class for client error '''


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


def supported():
    return not (caps.getos() in (caps.OSName.RHEVH, caps.OSName.RHEL)
                and caps.osversion()['version'].startswith('6'))


def get_external_vms(uri, username, password):
    if not supported():
        return errCode["noimpl"]

    try:
        conn = libvirtconnection.open_connection(uri=uri,
                                                 username=username,
                                                 passwd=password)
    except libvirt.libvirtError as e:
        logging.error('error connection to hypervisor: %r', e.message)
        return {'status': {'code': errCode['V2VConnection']['status']['code'],
                           'message': e.message}}

    with closing(conn):
        vms = []
        for vm in conn.listAllDomains():
            params = {}
            _add_vm_info(vm, params)
            try:
                xml = vm.XMLDesc(0)
            except libvirt.libvirtError as e:
                logging.error("error getting domain xml for vm %r: %s",
                              vm.name(), e)
                continue
            root = ET.fromstring(xml)
            try:
                _add_general_info(root, params)
            except InvalidVMConfiguration as e:
                logging.error('error parsing domain xml, msg: %s  xml: %s',
                              e.message, vm.XMLDesc(0))
                continue
            _add_networks(root, params)
            _add_disks(root, params)
            for disk in params['disks']:
                _add_disk_info(conn, disk)
            vms.append(params)
        return {'status': doneCode, 'vmList': vms}


def convert_external_vm(uri, username, password, vminfo, job_id, irs):
    job = ImportVm.from_libvirt(uri, username, password, vminfo, job_id, irs)
    job.start()
    _add_job(job_id, job)
    return {'status': doneCode}


def convert_ova(ova_path, vminfo, job_id, irs):
    job = ImportVm.from_ova(ova_path, vminfo, job_id, irs)
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
    _add_general_ovf_info(vm, root, ns)
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


def get_storage_domain_path(path):
    '''
    prepareImage returns /prefix/sdUUID/images/imgUUID/volUUID
    we need storage domain absolute path so we go up 3 levels
    '''
    return path.rsplit(os.sep, 3)[0]


@contextmanager
def password_file(job_id, file_name, password):
    fd = os.open(file_name, os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        os.write(fd, password.value)
    finally:
        os.close(fd)
    try:
        yield
    finally:
        try:
            os.remove(file_name)
        except Exception:
            logging.exception("Job %r error removing passwd file: %s",
                              job_id, file_name)


class ImportVm(object):
    TERM_DELAY = 30
    PROC_WAIT_TIMEOUT = 30

    def __init__(self, vminfo, job_id, irs):
        '''
        do not use directly, use a factory method instead!
        '''
        self._thread = None
        self._vminfo = vminfo
        self._id = job_id
        self._irs = irs

        self._status = STATUS.STARTING
        self._description = ''
        self._disk_progress = 0
        self._disk_count = 1
        self._current_disk = 1
        self._aborted = False
        self._prepared_volumes = []

        self._uri = None
        self._username = None
        self._password = None
        self._passwd_file = None
        self._create_command = None
        self._run_command = None

        self._ova_path = None

    @classmethod
    def from_libvirt(cls, uri, username, password, vminfo, job_id, irs):
        obj = cls(vminfo, job_id, irs)

        obj._uri = uri
        obj._username = username
        obj._password = password
        obj._passwd_file = os.path.join(_V2V_DIR, "%s.tmp" % job_id)
        obj._create_command = obj._from_libvirt_command
        obj._run_command = obj._run_with_password
        return obj

    @classmethod
    def from_ova(cls, ova_path, vminfo, job_id, irs):
        obj = cls(vminfo, job_id, irs)

        obj._ova_path = ova_path
        obj._create_command = obj._from_ova_command
        obj._run_command = obj._run
        return obj

    def start(self):
        self._thread = concurrent.thread(self._run_command)
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

    def _run_with_password(self):
        with password_file(self._id, self._passwd_file, self._password):
            self._run()

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
                self._description = ex.message
                try:
                    self._abort()
                except Exception as e:
                    logging.exception('Job %r, error trying to abort: %r',
                                      self._id, e)
        finally:
            self._teardown_volumes()

    def _import(self):
        # TODO: use the process handling http://gerrit.ovirt.org/#/c/33909/
        self._prepare_volumes()
        cmd = self._create_command()
        logging.info('Job %r starting import', self._id)

        # This is the way we run qemu-img convert jobs. virt-v2v is invoking
        # qemu-img convert to perform the migration.
        self._proc = execCmd(cmd, sync=False, deathSignal=signal.SIGTERM,
                             nice=NICENESS.HIGH, ioclass=IOCLASS.IDLE,
                             env=self._execution_environments())

        self._proc.blocking = True
        self._watch_process_output()
        self._wait_for_process()

        if self._proc.returncode != 0:
            raise V2VProcessError('Job %r process failed exit-code: %r'
                                  ', stderr: %s' %
                                  (self._id, self._proc.returncode,
                                   self._proc.stderr.read(1024)))

        if self._status != STATUS.ABORTED:
            self._status = STATUS.DONE
            logging.info('Job %r finished import successfully', self._id)

    def _execution_environments(self):
        env = {'LIBGUESTFS_BACKEND': 'direct'}
        if 'virtio_iso_path' in self._vminfo:
            env['VIRTIO_WIN'] = self._vminfo['virtio_iso_path']
        return env

    def _wait_for_process(self):
        if self._proc.returncode is not None:
            return
        logging.debug("Job %r waiting for virt-v2v process", self._id)
        if not self._proc.wait(timeout=self.PROC_WAIT_TIMEOUT):
            raise V2VProcessError("Job %r timeout waiting for process pid=%s",
                                  self._id, self._proc.pid)

    def _watch_process_output(self):
        parser = OutputParser()
        for event in parser.parse(self._proc.stdout):
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

    def _from_libvirt_command(self):
        cmd = [_VIRT_V2V.cmd,
               '-ic', self._uri,
               '-o', 'vdsm',
               '-of', self._get_disk_format(),
               '-oa', self._vminfo.get('allocation', 'sparse').lower()]
        cmd.extend(self._generate_disk_parameters())
        cmd.extend(['--password-file',
                    self._passwd_file,
                    '--vdsm-vm-uuid',
                    self._id,
                    '--vdsm-ovf-output',
                    _V2V_DIR,
                    '--machine-readable',
                    '-os',
                    get_storage_domain_path(self._prepared_volumes[0]['path']),
                    self._vminfo['vmName']])
        return cmd

    def _from_ova_command(self):
        cmd = [_VIRT_V2V.cmd,
               '-i', 'ova', self._ova_path,
               '-o', 'vdsm',
               '-of', self._get_disk_format(),
               '-oa', self._vminfo.get('allocation', 'sparse').lower(),
               '--vdsm-vm-uuid',
               self._id,
               '--vdsm-ovf-output',
               _V2V_DIR,
               '--machine-readable',
               '-os',
               get_storage_domain_path(self._prepared_volumes[0]['path'])]
        cmd.extend(self._generate_disk_parameters())
        return cmd

    def abort(self):
        self._status = STATUS.ABORTED
        logging.info('Job %r aborting...', self._id)
        self._abort()

    def _abort(self):
        self._aborted = True
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
                zombiereaper.autoReapPID(self._proc.pid)

    def _get_disk_format(self):
        fmt = self._vminfo.get('format', 'raw').lower()
        if fmt == 'cow':
            return 'qcow2'
        return fmt

    def _generate_disk_parameters(self):
        parameters = []
        for disk in self._vminfo['disks']:
            try:
                parameters.append('--vdsm-image-uuid')
                parameters.append(disk['imageID'])
                parameters.append('--vdsm-vol-uuid')
                parameters.append(disk['volumeID'])
            except KeyError as e:
                raise InvalidInputError('Job %r missing required property: %s'
                                        % (self._id, e))
        return parameters

    def _prepare_volumes(self):
        if len(self._vminfo['disks']) < 1:
            raise InvalidInputError('Job %r cannot import vm with no disk',
                                    self._id)

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
                                  (self._id, drive))

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
                              self._id, e)


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
            chunk += c
            if c == '\r':
                yield chunk
                chunk = ''

    def _parse_progress(self, chunk):
        m = self.DISK_PROGRESS_RE.match(chunk)
        if m is None:
            raise OutputParserError('error parsing progress, chunk: %r'
                                    % chunk)
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


def _add_vm_info(vm, params):
    params['vmName'] = vm.name()
    if vm.state()[0] == libvirt.VIR_DOMAIN_SHUTOFF:
        params['status'] = "Down"
    else:
        params['status'] = "Up"


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


def _add_disk_info(conn, disk):
    if 'alias' in disk.keys():
        try:
            vol = conn.storageVolLookupByPath(disk['alias'])
            _, capacity, alloc = vol.info()
        except libvirt.libvirtError:
            logging.exception("Error getting disk size")
        else:
            disk['capacity'] = str(capacity)
            disk['allocation'] = str(alloc)


def _add_disks(root, params):
    params['disks'] = []
    disks = root.findall('.//disk[@type="file"]')
    for disk in disks:
        d = {}
        device = disk.get('device')
        if device is not None:
            d['type'] = device
        target = disk.find('./target/[@dev]')
        if target is not None:
            d['dev'] = target.get('dev')
        source = disk.find('./source/[@file]')
        if source is not None:
            d['alias'] = source.get('file')
        params['disks'].append(d)


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
    # FIXME: change to tarfile package when support --to-stdout
    cmd = ['/usr/bin/tar', 'xf', ova_path, '*.ovf', '--to-stdout']
    rc, output, error = execCmd(cmd)
    if rc:
        raise V2VError(error)

    return ''.join(output)


def _add_general_ovf_info(vm, node, ns):
    vm['status'] = 'Down'
    vmName = node.find('./ovf:VirtualSystem/ovf:Name', ns)
    if vmName is not None:
        vm['vmName'] = vmName.text
    else:
        raise V2VError('Error parsing ovf information: no ovf:Name')

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


def _add_disks_ovf_info(vm, node, ns):
    vm['disks'] = []
    for d in node.findall(".//ovf:DiskSection/ovf:Disk", ns):
        disk = {'type': 'disk'}
        capacity = d.attrib.get('{%s}capacity' % _OVF_NS)
        disk['capacity'] = str(int(capacity) * 1024 * 1024 * 1024)
        fileref = d.attrib.get('{%s}fileRef' % _OVF_NS)
        alias = node.find('.//ovf:References/ovf:File[@ovf:id="%s"]' %
                          fileref, ns)
        if alias is not None:
            disk['alias'] = alias.attrib.get('{%s}href' % _OVF_NS)
            disk['allocation'] = str(alias.attrib.get('{%s}size' % _OVF_NS))
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
