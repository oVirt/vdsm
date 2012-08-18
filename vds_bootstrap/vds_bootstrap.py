#! /usr/bin/python
#
# Copyright 2011-2012 Red Hat, Inc.
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

# Script to check VDS compatibility.
# Input: url from web portal with last released packages
# Output: stdout as XML format
#
# Steps to perform:
# 1. Check VT/SVM
# 2. OS name + version
# 3. Kernel version
# 4. Check missing RPMs
#   a. Install if needed
# 5. Check missing VDS packages
#   a. Install if needed
# 6. Check switch configuration
# 7. Initiate Certificate Initialization
#   a. Generate certificate and sign request
#   b. Submit sign request
#   c. Wait until signed certificate returns from VDC
#   d. Install certificate and keys for vdsm use.
# 8. Reboot
#

import sys
import getopt
import os
import os.path
import shutil
import logging
import logging.config
import ConfigParser
import socket
import tempfile
from time import strftime

import deployUtil

try:
    LOGDIR=os.environ["OVIRT_LOGDIR"]
except KeyError:
    LOGDIR=tempfile.gettempdir()
logging.basicConfig(level=logging.DEBUG,
                    format='%(asctime)s %(levelname)-8s %(module)s '
                           '%(lineno)d %(message)s',
                    datefmt='%a, %d %b %Y %H:%M:%S',
                    filename='%s/vdsm-bootstrap-%s-%s.log' %
                             (LOGDIR, "phase1", strftime("%Y%m%d%H%M%S")),
                    filemode='w')

rhel6based = deployUtil.versionCompare(deployUtil.getOSVersion(), "6.0") >= 0

# TODO this is an infra-hackish heuristic for identifying Fedora
# drop as soon as possible
fedorabased = deployUtil.versionCompare(deployUtil.getOSVersion(), "16") >= 0

if rhel6based:
    VDSM_NAME = "vdsm"
    VDSM_MIN_VER = "4.9"
    KERNEL_MIN_VR = ("2.6.32", "150")
    MINIMAL_SUPPORTED_PLATFORM = "6.0"
else:
    VDSM_NAME = "vdsm22"
    VDSM_MIN_VER = "4.5"
    KERNEL_MIN_VR = ("2.6.18", "159")
    MINIMAL_SUPPORTED_PLATFORM = "5.5"

# Required packages
REQ_PACK = ('SDL.x86_64','bridge-utils.x86_64','mesa-libGLU.x86_64',
            'openssl.x86_64', 'rsync.x86_64')

if rhel6based:
    DEVEL_PACK = ()
    VDS_PACK = ('qemu-kvm', 'qemu-kvm-tools', VDSM_NAME, VDSM_NAME+'-cli',
                'libjpeg', 'spice-server', 'pixman',
                'seabios', 'qemu-img', 'fence-agents',
                'libselinux-python', 'sanlock', 'sanlock-python')
    # Gluster packages
    GLUSTER_PACK = ('vdsm-gluster', 'glusterfs-server', 'glusterfs-rdma',
                    'glusterfs-geo-replication')
else:
    # Devel packages
    DEVEL_PACK = ('gdb','tcpdump','strace','ltrace','sysstat','ntp',
                    'pstack','vim-common','vim-enhanced',
                    'systemtap','systemtap-runtime')
    # VDS packages
    VDS_PACK = ('kvm', 'kmod-kvm', 'kvm-tools', VDSM_NAME, VDSM_NAME+'-cli', 'qcairo',
                'qffmpeg-libs', 'qspice-libs', 'qpixman', 'log4cpp',
                'etherboot-zroms-kvm', 'kvm-qemu-img', 'fence-agents')
    GLUSTER_PACK = ()

# Conflicting packages- fail if exist
if rhel6based:
    CONFL_PACK = ( )
else:
    CONFL_PACK = ('cman.x86_64', )

# Conflicting packages- delete if exist
if rhel6based:
    DEL_PACK = ()
else:
    DEL_PACK = ('vdsm.x86_64', 'vdsm-cli.x86_64')

# Services VDSM needs
NEEDED_SERVICES = ['iscsid', 'multipathd', 'ntpd']

# Services conflicting VDSM
CONFLICT_SERVICES = ['cpuspeed']

if rhel6based:
    NEEDED_SERVICES.append('libvirtd')
    CONFLICT_SERVICES.append('libvirt-guests')
else:
    CONFLICT_SERVICES.append('libvirtd')

VDSM_CONF = '/etc/vdsm/vdsm.conf'
VDSM_DIR = "/usr/share/vdsm/"

# Adding VDSM_DIR to the current python path
try:
    os.mkdir(VDSM_DIR, 0755)
except OSError:
    pass
sys.path.append(VDSM_DIR)

__SYSCONFIG_IPTABLES__ = '/etc/sysconfig/iptables'

def _safeWrite(fname, s):
    "Write s into fname atomically"

    # Python 2.4 (RHEL5 does not support delete= to NamedTemporaryFile
    # we can use mkstemp() as an alternative for RHEL5 hosts)
    if rhel6based:
        t = tempfile.NamedTemporaryFile(delete=False)
        tname = t.name
    else:
        tfd, tname = tempfile.mkstemp()
        t = os.fdopen(tfd, "w")
    t.write(s)
    t.close()

    try:
        oldstat = os.stat(fname)
    except:
        oldstat = None

    shutil.move(tname, fname)

    try:
        if oldstat is not None:
            os.chmod(fname, oldstat.st_mode)
            os.chown(fname, oldstat.st_uid, oldstat.st_gid)

        deployUtil.silentRestoreCon(fname)
    except OSError:
        logging.debug('trying to maintain file permissions', exc_info=True)

def _constantTSC():
    return all(' constant_tsc ' in line
               for line in file('/proc/cpuinfo')
               if line.startswith('flags\t'))

class Deploy:
    """
        This class holds the relevant functionality for vdsm deployment on RHEL.
    """
    def _xmlOutput(self, component, status, resultKey, result, msg, test=False):
        """
            Internal: publish results to server and log.
        """
        if test:
            message = "Validate '"
        else:
            message = "<BSTRAP component='"

        message += (
            component +
            "' status='" +
            str(status)
        )
        if resultKey is not None:
            message += ("' " + str(resultKey) + "='" + str(result))

        #Fix xml encoding:
        msg = deployUtil.escapeXML(str(msg))
        message += ("' message='" + msg + "'")

        if not test:
            message += "/>"

        print message
        logging.debug(message)
        sys.stdout.flush()

    def checkRegistration(self):
        """
            Check RHN registration using vdsm package lookup
        """
        status = "OK"
        message = 'Host properly registered with RHN/Satellite.'
        rc = True

        try:
            rc = bool(deployUtil.yumListPackages(VDSM_NAME))
        except:
            rc = False
            logging.error("checkRegistration: Error searching for VDSM package!",
                    exc_info=True)

        if not rc:
            message = "Unable to fetch " + VDSM_NAME + " package. Please check if host is registered to RHN, Satellite or other yum repository"
            status = "FAIL"
            logging.error(message)
        else:
            logging.debug(message)

        self._xmlOutput('RHN_REGISTRATION', status, None, None, message)
        return rc

    def checkMajorVersion(self):
        """
            Check available vdsm package matches the allwoed minimal version
        """
        status = "OK"
        message = 'Available VDSM matches requirements'
        rc = True

        try:
            rc = deployUtil.yumSearchVersion(VDSM_NAME, VDSM_MIN_VER)
        except:
            rc = False
            logging.error("checkMajorVersion: Error searching for VDSM version!",
                    exc_info=True)

        if not rc:
            message = "Unable to fetch VDSM with minimal version of " + VDSM_MIN_VER + ". Please check if host is properly registered with updated yum repository"
            status = "FAIL"
            logging.error(message)
        else:
            logging.debug(message)

        self._xmlOutput('VDSM_MAJOR_VER', status, None, None, message)
        return rc

    def virtExplorer(self, rnum):
        """
            Check the VT/SVM compatibility
        """
        self.test = False
        self.vt_svm = None
        self.res = ''
        self.message = ''
        self.rc = True

        if self.rc:
            if deployUtil.virtEnabledInCpuAndBios():
                self.vt_svm = "OK"
                self.message = "Server supports virtualization"
            else:
                # We can't use the regular vdsm.config module here because
                # vdsm-python might not be installed yet.
                config = ConfigParser.ConfigParser()
                config.read(VDSM_CONF)

                try:
                    fake_kvm = config.getboolean('vars', 'fake_kvm_support')
                except:
                    fake_kvm = False

                if fake_kvm:
                    self.vt_svm = "OK"
                    self.message = "Server uses the fake kvm virtualization"
                else:
                    self.vt_svm = "FAIL"
                    self.message = "Server does not support virtualization"
                    self.rc = False

            if "GenuineIntel" == deployUtil.cpuVendorID():
                self.res = "Intel"
            else:
                self.res = "AMD"

        if self.vt_svm is None:
            self.vt_svm = "NA"

        self._xmlOutput('VT_SVM', self.vt_svm, "processor", self.res, self.message, self.test)
        return self.rc

    def osExplorer(self):
        """
            Check the compatibility of OS and kernel
        """
        os_status = "FAIL"
        kernel_status = "FAIL"
        os_message = "Unsupported platform version"
        os_name = "Unknown OS"
        kernel_message = ''
        self.rc = True

        res = deployUtil.getOSVersion()

        os_message = "Unsupported platform version: " + res
        verTest = deployUtil.versionCompare(res, MINIMAL_SUPPORTED_PLATFORM)
        if verTest == 99:
            #import error
            os_message = "Unable to test for minimal platform version: missing python library"
            self.rc = False
        elif verTest < 0:
            self.rc = False
        else:
            if fedorabased:
                os_name = "FEDORA"
            elif rhel6based:
                os_name = "RHEL6"
            else:
                os_name = "RHEL5"
            os_message = "Supported platform version"
            os_status = "OK"

        if self.rc:
            kernel_vr = deployUtil.getKernelVR()
            if deployUtil.compareVR(kernel_vr, KERNEL_MIN_VR) >= 0:
                kernel_status = "OK"
                kernel_message = "Supported kernel version: " + str(kernel_vr)
            else:
                kernel_status = "FAIL"
                kernel_message = (
                    "Unsupported kernel version: " + str(kernel_vr) +
                    ". Minimal supported version: " + str(KERNEL_MIN_VR)
                )
                self.rc = False

        if os_name is not None:
            self._xmlOutput('OS', os_status, "type", os_name, os_message)
        self._xmlOutput('KERNEL', kernel_status, "version", '-'.join(kernel_vr), kernel_message)

        return self.rc

    def kernelArgs(self):
        """
            Add required kernel args (hoping that future kernel updates keeps them)
        """
        self.st = "OK"
        self.message = ''
        self.rc = True

        args = ['elevator=deadline']
        if rhel6based and not _constantTSC():
            args += ['processor.max_cstate=1']

        for arg in args:
            ret = deployUtil.updateKernelArgs(arg)
            if ret:
                self.message += "Added kernel arg '%s'. " % arg
            else:
                self.st = "WARN"
                self.message += "Error adding kernel arg '%s'. " % arg

        if self.st != "OK":
            self._xmlOutput('KernelArgs', self.st, None, None, self.message)

        return self.rc

    def _initPackagesExplorer(self):
        self.req_pack = []
        self.devel_pack = []
        self.vds_pack = []
        self.confl_pack = []
        self.del_pack = []
        self.res = ''
        self.message = ''
        self.rc = 0

    def _avoidPKGConflict(self):
        for pack in CONFL_PACK:
            self.res, self.message = deployUtil.getPackageInfo("CONFL", pack, 'status')
            res = self.res #Reverse display status
            if res == "WARN":
                res = "OK"
            self._xmlOutput('CONFLICTING PACKAGES', res, "result", pack, self.message)
            if self.res == "OK":
                self.confl_pack.append(pack)
                logging.debug('>>> Conflicting package %s installed', pack)

    def _delPKG(self):
        for pack in DEL_PACK:
            self.res, self.message = deployUtil.getPackageInfo("DEL", pack, 'status')
            res = self.res   #Reverse display status
            if res == "WARN":
                res = "OK"
            else:            # PKG needs to be deleted....
                self.del_pack.append(pack)
                logging.debug('>>> Obsolete package %s installed', pack)
                res = "WARN"
            self._xmlOutput('OBSOLETE PACKAGES', res, "result", pack, self.message)

    def _getAllPackages(self):
        logging.debug('Check required packages ...')
        for pack in REQ_PACK:
            self.res, self.message = deployUtil.getPackageInfo("REQ", pack, 'status')
            self._xmlOutput('REQ PACKAGES', self.res, "result", pack, self.message)
            if self.res == "WARN":
                self.req_pack.append(pack)

        for p in self.req_pack:
            logging.debug('>>> %s should be installed',p)
        logging.debug('Check VDS packages ...')
        for pack in VDS_PACK:
            self.res, self.message = deployUtil.getPackageInfo("VDS", pack, 'status')
            self._xmlOutput('VDS PACKAGES', self.res, "result", pack, self.message)
            if self.res == "WARN":
                self.vds_pack.append(pack)

        for p in self.vds_pack:
            logging.debug('>>> %s should be installed',p)
        logging.debug('Check development packages ...')
        for pack in DEVEL_PACK:
            self.res, self.message = deployUtil.getPackageInfo("DEVEL", pack, 'status')
            self._xmlOutput('DEVEL PACKAGES', self.res, "result", pack, self.message)
            if self.res == "WARN":
                self.devel_pack.append(pack)

        for p in self.devel_pack:
            logging.debug('>>> %s should be installed',p)

    def _installPackage(self, pack, type, update=0):
        nReturn = 0
        logging.debug('Installing %s %d',pack, update )
        if type == "REQ" or type == "DEVEL":
            self.res, self.message = deployUtil.installAndVerify(type, pack, "install")
            res = "OK"
            if not self.res:
                res = "FAIL"
                nReturn = 1
            self._xmlOutput(type + ' PACKAGES', res, "result", pack, self.message)
        elif type == "VDS":
            yumcmd = "install"
            if update == 1:
                yumcmd = "update"

            self.res, self.message = deployUtil.installAndVerify(type, pack, yumcmd)
            res = "OK"
            if not self.res:
                res = "FAIL"
                nReturn = 1
            self._xmlOutput(type +' PACKAGES', res, "result", pack, self.message)
        elif type == "GLUSTER":
            yumcmd = "install"
            if update == 1:
                yumcmd = "update"

            self.res, self.message = deployUtil.installAndVerify(type, pack,
                                                                 yumcmd)
            res = "OK"
            if not self.res:
                res = "FAIL"
                nReturn = 1
            self._xmlOutput(type +' PACKAGES', res, "result", pack,
                            self.message)
        else:
            nReturn = 1
            logging.debug('Unknown package type: %s', type)

        return nReturn

    def _delPackages(self):
        res = "OK"
        logging.debug('Delete obsolete packages ...')
        logging.debug('Deleting packages ...  %s', self.del_pack.__repr__())

        while self.del_pack:
            pack = self.del_pack.pop()
            out, err, self.rc = deployUtil.yumInstallDeleteUpdate(pack, "remove")
            if self.rc:
                res = "FAIL"
                self.message = err
                self._xmlOutput('OBSOLETE PACKAGES', res, "result", pack, self.message)
                return 1
            else:
                self._xmlOutput('OBSOLETE PACKAGES', res, "result", pack, "Removed successfully")
        return 0

    def _installPackages(self):
        # clean yum cache
        deployUtil.yumCleanCache()

        # install/update packages
        while self.req_pack:
            logging.debug('Install required packages ...')
            self.rc = self._installPackage(self.req_pack.pop(),"REQ")
            if self.rc:
                return

        logging.debug('Install/Update VDS packages ...')
        logging.debug('Install VDS packages ... %s', VDS_PACK.__repr__())
        logging.debug('Update VDS packages ...  %s', self.vds_pack.__repr__())
        for pack in VDS_PACK:
            if pack not in self.vds_pack:
                self.rc = self._installPackage(pack,"VDS", 1)
                if self.rc:
                    return
        while self.vds_pack:
            self.rc = self._installPackage(self.vds_pack.pop(),"VDS")
            if self.rc:
                return

        while self.devel_pack:
            logging.debug('Install development packages ...')
            self._installPackage(self.devel_pack.pop(),"DEVEL")

    def packagesExplorer(self):
        """
            Check and install software packages
        """
        self._initPackagesExplorer()

        self._avoidPKGConflict()
        if len(self.confl_pack) > 0:
            self.res = "FAIL"
            self.rc = 1
            self.message = "Conflicting packages found: " + str(self.confl_pack)
            logging.error(self.message)
            self._xmlOutput('CONFL', self.res, "result", "conflict found", self.message)

        self._delPKG()
        if len(self.del_pack) > 0:
            self.rc = self._delPackages()

        if not self.rc:
            self._getAllPackages()
            deployUtil.setService("vdsmd", "stop")
            self._installPackages()

        return self.rc

    def installGlusterPackages(self):
        packages = []
        updates = []
        for pack in GLUSTER_PACK:
            self.res, self.message = deployUtil.getPackageInfo("GLUSTER", pack,
                                                               'status')
            self._xmlOutput('GLUSTER PACKAGES', self.res, "result", pack,
                            self.message)
            if self.res == "WARN":
                packages.append(pack)
            else:
                updates.append(pack)

        self.rc = 0
        logging.debug('Install GLUSTER packages ... %s', packages.__repr__())
        while (not self.rc and packages):
            self.rc = self._installPackage(packages.pop(), "GLUSTER")

        if not self.rc and updates:
            logging.debug('Update GLUSTER packages ...  %s', updates.__repr__())
        while (not self.rc and updates):
            self.rc = self._installPackage(updates.pop(), "GLUSTER", 1)
        return self.rc

    def _makeConfig(self):
        import datetime
        config = deployUtil.vdsmImport("config").config

        if not os.path.exists(VDSM_CONF):
            logging.debug("makeConfig: generating conf.")
            lines = []
            lines.append ("# Auto-generated by vds_bootstrap at:" + str(datetime.datetime.now()) + "\n")
            lines.append ("\n")

            lines.append ("[vars]\n") #Adding ts for the coming scripts.
            lines.append ("trust_store_path = " + config.get('vars', 'trust_store_path') + "\n")
            lines.append ("ssl = " + config.get('vars', 'ssl') + "\n")

            if config.getboolean('vars', 'fake_kvm_support'):
                lines.append ("fake_kvm_support = true\n")

            lines.append ("\n")

            lines.append ("[addresses]\n") #Adding mgt port for the coming scripts.
            lines.append ("management_port = " + config.get('addresses', 'management_port') + "\n")

            logging.debug("makeConfig: writing the following to " + VDSM_CONF)
            logging.debug(lines)
            fd, tmpName = tempfile.mkstemp()
            f = os.fdopen(fd, 'w')
            f.writelines(lines)
            f.close()
            os.chmod(tmpName, 0644)
            shutil.move(tmpName, VDSM_CONF)
        else:
            self.message = 'Basic configuration found, skipping this step'
            logging.debug(self.message)

    def createConf(self):
        """
            Generate initial configuration file for VDSM. Must run after package installation!
        """
        self.message = 'Basic configuration set'
        self.rc = True
        self.status = 'OK'

        try:
            self._makeConfig()
        except Exception, e:
            logging.error('', exc_info=True)
            self.message = 'Basic configuration failed'
            if isinstance(e, ImportError):
                self.message = self.message + ' to import default values'
            self.rc = False
            self.status = 'FAIL'

        self._xmlOutput('CreateConf', self.status, None, None, self.message)
        return self.rc

    def _addNetwork(self, vdcName, vdcPort):
        fReturn = True

        #add management bridge
        try:
            fReturn = deployUtil.makeBridge(vdcName, VDSM_DIR)
            if fReturn: #save current config by removing the undo files:
                if not vdcPort:
                    vdcPort = 80
                vdcUrl = "http://%s:%s" % (vdcName, vdcPort)
                try:
                    if not deployUtil.waitRouteRestore(60, vdcUrl):
                        fReturn = False
                        self.message = "No route to %s. Check switch/router " \
                            "settings and try registering again." % vdcName
                        logging.error(self.message)
                except:
                    logging.error("Error restoring route", exc_info=True)
            else:
                self.message = "addNetwork error trying to add management bridge"
                logging.error(self.message)
                fReturn = False
        except:
            fReturn = False
            self.message = "addNetwork Failed to add management bridge"
            logging.error(self.message, exc_info=True)

        if not fReturn:
            self.status = "FAIL"
            self.res = 1

        return fReturn

    def checkLocalHostname(self):
        # This is missing and not used on rhel5
        import ethtool

        self.status = "OK"
        self.rc = True
        self.message = "Local hostname is correct."

        try:
            localip = map(ethtool.get_ipaddr, ethtool.get_active_devices())
            localip = filter(lambda x: x != "127.0.0.1", localip)
        except:
            logging.error("ethtool error", exc_info=True)
            localip = ()

        try:
            fqdnip = socket.gethostbyname(socket.gethostname())
        except:
            logging.error("gethostbyname error", exc_info=True)
            fqdnip = None

        if fqdnip is None or fqdnip not in localip:
            if len(localip) < 1:
                self.message = "Unable to get local ip addresses."
            elif fqdnip is None:
                self.message = "Unable to resolve local hostname."
            else:
                self.message = "Local hostname is configured badly."
            self.status = "WARN"
            logging.error(self.message)

        self._xmlOutput('CheckLocalHostname',
                        self.status, None, None, self.message)
        return self.rc

    def setNetworking(self, iurl):
        """
            Create management bridge.
            This class will try to create a management bridge named "rehvm". Class
            always succeeds to allow network configuration from managment server
            even in case this class will fail to set the management bridge.
            Note: expected input format: http://www.redhat.com/a/b/c or: ftp://10.0.0.23/d/e/f
        """
        self.status = "OK"
        self.rc = True
        self.message = "Created management bridge."

        if rhel6based:
             deployUtil.setService("libvirtd", "start")

        if deployUtil.preventDuplicate():
            self.message = "Bridge management already exists. Skipping bridge creation."
            logging.debug(self.message)
        else:
            url, port = deployUtil.getAddress(iurl)
            if url is None:
                self.message = "Failed to parse manager URL!"
                self.status = "FAIL"
                logging.error(self.message)
                #Do not set rc to allow changes from rhev-m.
            else:
                self._addNetwork(url, port)

        self._xmlOutput('SetNetworking', self.status, None, None, self.message)
        return self.rc

    def setSSHAccess(self, url, engine_ssh_key):
        """
            Sets ssh access for this host from the managment server.
        """
        self.message = "SUCCESS"
        self.status = "OK"
        self.rc = True
        strKey = None

        # TODO remove legacy
        if deployUtil.getBootstrapInterfaceVersion() == 1 and engine_ssh_key == None:
            vdcAddress = None
            vdcPort = None

            vdcAddress, vdcPort = deployUtil.getAddress(url)
            if vdcAddress is not None:
                strKey = deployUtil.getAuthKeysFile(vdcAddress, vdcPort)
                if strKey is None:
                    self.rc = False
                    self.message = "Failed to retrieve server SSH key."
            else:
                self.message = "Failed to extract server address."
                self.rc = False
        else:
            try:
                strKey = file(engine_ssh_key).read()
            except Exception, e:
                self.message = "Failed to read SSH key file " + str(e)
                self.rc = False

        if self.rc:
            if not deployUtil.handleSSHKey(strKey):
                self.rc = False
                self.message = "Failed to write server's SSH key."

        if not self.rc:
            self.status = "FAIL"
        self._xmlOutput('SetSSHAccess', self.status, None, None, self.message)
        return self.rc

    def overrideFirewall(self, firewallRulesFile):
        self.message = 'overridden firewall successfully'
        self.rc = True
        self.st = 'OK'

        try:
            rules = file(firewallRulesFile).read()
            _safeWrite(__SYSCONFIG_IPTABLES__, rules)
        except Exception, e:
            self.message = str(e)
            self.rc = False
            self.st = 'FAIL'

        self._xmlOutput('Firewall', self.st, None, None, self.message)
        return self.rc

    def setSystemTime(self, systime):
        """
            Set host system time
        """
        self.message = 'setSystemTime ended successfully'
        self.rc = True
        self.st = 'OK'

        self.rc = deployUtil.setHostTime(systime)
        if not self.rc:
            self.st = 'FAIL'
            self.message = "Unable to set host time."

        self._xmlOutput('SET_SYSTEM_TIME', self.st, None, None, self.message)
        return self.rc

    def verifyServices(self):
        """
            Make sure needed services are on in vdsm relevant runlevels.
        """
        self.message = 'Needed services set'
        self.rc = True
        self.status = 'OK'

        for srv in CONFLICT_SERVICES:
            deployUtil.setService(srv, "stop")
            out, err, ret = deployUtil.chkConfig(srv, "off")
            if ret:
                message = "VerifyServices: Failed to unset conflicting service " + srv + "."
                logging.error(self.message)
                logging.error("Details: " + str(out) + "\n" + str(err))
                self._xmlOutput('VerifyServices', 'WARN', None, None, message)

        if self.status == 'OK':
            for srv in NEEDED_SERVICES:
                out, err, ret = deployUtil.chkConfig(srv, "on", "345")
                if ret:
                    self.message = "VerifyServices: Failed to set service " + srv + "."
                    self.status = 'FAIL'
                    logging.error(self.message)
                    logging.error("Details: " + str(out) + "\n" + str(err))
                    break

        self._xmlOutput('VerifyServices', self.status, None, None, self.message)
        return self.rc

    def setCertificates(self, subject, random_num, orgName='Red Hat, Inc.'):
        """
            Makes sure that vdsmd has its Certificate in place
            NOTE: setCertificates must be created AFTER rpms are installed, so
            that vdsm.conf already exists.
        """
        config = ConfigParser.ConfigParser()
        config.read(VDSM_CONF)
        try:
            tsDir = config.get('vars', 'trust_store_path')
        except:
            if rhel6based:
                tsDir = '/etc/pki/vdsm'
            else:
                tsDir = '/var/vdsm/ts'

        vdsmKey = tsDir + '/keys/vdsmkey.pem'
        vdsmCert = tsDir + '/certs/vdsmcert.pem'

        deployUtil.pkiCleanup(vdsmKey, vdsmCert)
        deployUtil.createCSR(orgName, subject, random_num, tsDir, vdsmKey)
        self._xmlOutput('Encryption setup', 'OK', None, None, "Ended successfully")
# End of deploy class.

def VdsValidation(iurl, subject, random_num, rev_num, orgName, systime,
        firewallRulesFile, engine_ssh_key, installVirtualizationService, installGlusterService):
    """ --- Check VDS Compatibility.
    """
    logging.debug("Entered VdsValidation(subject = '%s', random_num = '%s', rev_num = '%s', installVirtualizationService = '%s', installGlusterService = '%s')"%(subject, random_num, rev_num, installVirtualizationService, installGlusterService))

    if installGlusterService:
        if not rhel6based:
            logging.error('unsupported system for Gluster service')
            return False

    oDeploy = Deploy()

    if not oDeploy.checkRegistration():
        logging.error('checkRegistration test failed')
        return False

    if not oDeploy.checkMajorVersion():
        logging.error('checkMajorVersion test failed')
        return False

    if installVirtualizationService:
        logging.debug('virtExplorer testing')
        if not oDeploy.virtExplorer(random_num):
            logging.error('virtExplorer test failed')
            return False

    if not oDeploy.osExplorer():
        logging.error('osExplorer test failed')
        return False

    if not oDeploy.kernelArgs():
        logging.error('kernelArgs failed')
        return False

    if oDeploy.packagesExplorer():
        logging.error('packagesExplorer test failed')
        return False

    if installGlusterService:
        if oDeploy.installGlusterPackages():
            logging.error('installGlusterPackages failed')
            return False

    if not oDeploy.createConf():
        logging.error('createConf failed')
        return False

    if rhel6based:
        if not oDeploy.checkLocalHostname():
            logging.error('checkLocalHostname test failed')
            return False

    if not oDeploy.setNetworking(iurl):
        logging.error('setNetworking test failed')
        return False

    if not oDeploy.setSSHAccess(iurl, engine_ssh_key):
        logging.error('setSSHAccess test failed')
        return False

    if firewallRulesFile:
        if not oDeploy.overrideFirewall(firewallRulesFile):
            logging.error('Failed to set default firewall')
            return False

    if systime:
        if not oDeploy.setSystemTime(systime):
            logging.error('setSystemTime failed')
            return False

    if not oDeploy.verifyServices():
        logging.error('verifyServices failed')
        return False

    oDeploy.setCertificates(subject, random_num, orgName)

    return True

def main():
    """
Usage: vds_bootstrap.py [options] <url> <subject> <random_num>

options:
    -v <bootstrap inteface version> - default 1
    -O <organizationName>
    -t <systemTime>
    -f <firewall_rules_file> -- override firewall rules.
    -V - don't install virtualization service
    -g - install gluster service
obsolete options:
    -r <rev_num>
    """
    try:
        rev_num = None
        orgName = 'Red Hat Inc.'
        systime = None
        firewallRulesFile = None
        engine_ssh_key = None
        installVirtualizationService = True
        installGlusterService = False
        opts, args = getopt.getopt(sys.argv[1:], "v:r:O:t:f:S:n:u:Vg")
        for o,v in opts:
            if o == "-v":
                deployUtil.setBootstrapInterfaceVersion(int(v))
            if o == "-r":
                rev_num = v
            if o == "-O":
                orgName = v
            if o == "-t":
                systime = v
            if o == "-V":
                installVirtualizationService = False
            if o == "-g":
                installGlusterService = True
            elif o == '-f':
                firewallRulesFile = v
                NEEDED_SERVICES.append('iptables')
            elif o == '-S':
                engine_ssh_key = v

        url = args[0]
        subject = args[1]
        random_num = args[2]
        # Where is REVISION defined ????
        #if not rev_num:
        #    rev_num = REVISION
    except:
        print main.__doc__
        return False

    logging.debug('**** Start VDS Validation ****')
    try:
        ret = VdsValidation(url, subject, random_num, rev_num,
                            orgName, systime, firewallRulesFile, engine_ssh_key, installVirtualizationService, installGlusterService)
    except:
        logging.error("VDS validation failed", exc_info=True)
        logging.error(main.__doc__)
        logging.debug("<BSTRAP component='RHEV_INSTALL' status='FAIL'/>")
        print "<BSTRAP component='RHEV_INSTALL' status='FAIL'/>"
        return False
    else:
        message = ("<BSTRAP component='RHEV_INSTALL' status=")
        if ret:
            message += ("'OK'/>")
        else:
            message += ("'FAIL'/>")

        print(message)
        logging.debug(message)

    logging.debug('**** End VDS Validation ****')
    sys.stdout.flush()
    return ret

if __name__ == "__main__":
    sys.exit(not main())
