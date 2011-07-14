# Copyright 2009-2010 Red Hat, Inc. All rights reserved.
# Use is subject to license terms.
#
# Description:    Constants definitions for vdsm and utilities.

#
# file ownership definitions
#
DISKIMAGE_USER = 'vdsm'
DISKIMAGE_GROUP = 'qemu'
METADATA_USER = 'vdsm'
METADATA_GROUP = 'kvm'
QEMU_PROCESS_USER = 'qemu'

# This is the domain version translation list
# DO NOT CHANGE OLD VALUES ONLY APPEND
DOMAIN_VERSIONS = (0, 2)

# This contains the domains versions that this VDSM
# accepts currently its all of the version but in the
# future we might slice it (eg. tuple(DOMAIN_VERSION[1:]))
SUPPORTED_DOMAIN_VERSIONS = DOMAIN_VERSIONS

UUID_GLOB_PATTERN = '*-*-*-*-*'

MEGAB = 2 ** 20 # = 1024 ** 2 = 1 MiB

#
# Path definitions
#
P_BIN = '/bin/'
P_SBIN = '/sbin/'
P_LIBVIRT_VMCHANNELS = '/var/lib/libvirt/qemu/channels/'
P_STORAGEPOOLS = '@POOLSDIR@'
P_TRUSTSTORE = '@TRUSTSTORE@'
P_USR_BIN = '/usr/bin/'
P_USR_SBIN = '/usr/sbin/'
P_VDSM = '@VDSMDIR@/'
P_VDSM_BACKUP = '@BACKUPDIR@'
P_VDSM_HOOKS = '@HOOKSDIR@/'
P_VDSM_LIB = '@VDSMLIBDIR@/'
P_VDSM_LIBEXEC = '@LIBEXECDIR@/'
P_VDSM_RUN = '@VDSMRUNDIR@/'
P_VDSM_CONF = '@CONFDIR@/'
P_VDSM_KEYS = '/etc/pki/vdsm/keys/'

P_VDSM_CLIENT_LOG = '@VDSMRUNDIR@/client.log'
P_VDSM_LOG = '@VDSMLOGDIR@'

#
# External programs (sorted, please keep in order).
#
EXT_ADDNETWORK = P_VDSM + 'addNetwork'

EXT_BLOCKDEV = P_SBIN + 'blockdev'
EXT_BRCTL = P_USR_SBIN + 'brctl'

EXT_CAT = P_BIN + 'cat'
EXT_CHOWN = P_BIN + 'chown'
EXT_CP = P_BIN + 'cp'

EXT_DD = P_BIN + 'dd'
EXT_DELNETWORK = P_VDSM + 'delNetwork'
EXT_DMIDECODE = P_USR_SBIN + 'dmidecode'
EXT_DMSETUP = P_SBIN + 'dmsetup'

EXT_EDITNETWORK = P_VDSM + 'editNetwork'

EXT_FENCE_PREFIX = P_USR_SBIN + 'fence_'
EXT_FSCK = P_SBIN + 'e2fsck'
EXT_FUSER = P_SBIN + 'fuser'

EXT_GET_VM_PID = P_VDSM + 'get-vm-pid'

EXT_IFCONFIG = P_SBIN + 'ifconfig'
EXT_IFDOWN = P_SBIN + 'ifdown'
EXT_IFUP = P_SBIN + 'ifup'
EXT_IONICE = P_USR_BIN + 'ionice'
EXT_IPCALC = P_BIN + 'ipcalc'
EXT_ISCSIADM = P_SBIN + 'iscsiadm'

EXT_KILLALL = P_USR_BIN + 'killall'
EXT_KILL = P_BIN + 'kill'

EXT_LVM = P_SBIN + 'lvm'

EXT_MKFS = P_SBIN + 'mke2fs'
EXT_MK_SYSPREP_FLOPPY = P_VDSM + 'mk_sysprep_floppy'
EXT_MOUNT = P_BIN + 'mount'
EXT_MULTIPATH = P_SBIN + 'multipath'
EXT_MV = P_BIN + 'mv'

EXT_NICE = P_BIN + 'nice'

EXT_PERSIST = P_USR_SBIN + 'persist'
EXT_PGREP = P_USR_BIN + 'pgrep'
EXT_PREPARE_VMCHANNEL = P_VDSM + 'prepare-vmchannel'
EXT_PYTHON = P_USR_BIN + 'python'

EXT_QEMUIMG = P_USR_BIN + 'qemu-img'

EXT_REBOOT = P_SBIN + 'reboot'
EXT_RPM = P_BIN + 'rpm'
EXT_RSYNC = P_USR_BIN + 'rsync'

EXT_SCSI_ID = P_SBIN + 'scsi_id' #TBD !
EXT_SERVICE = P_SBIN + 'service'
EXT_SETSID = P_USR_BIN + 'setsid'
EXT_SH = P_BIN + 'sh'
EXT_SHOWMOUNT = P_USR_SBIN + 'showmount'
EXT_SU = P_BIN + 'su'
EXT_SUDO = P_USR_BIN + 'sudo'

EXT_TAR = P_BIN + 'tar'
EXT_TUNE2FS = P_SBIN + 'tune2fs'

EXT_UMOUNT = P_BIN + 'umount'
EXT_UNPERSIST = P_USR_SBIN + 'unpersist'

EXT_VCONFIG = P_SBIN + 'vconfig'
EXT_VDSM_STORE_NET_CONFIG = P_VDSM + 'vdsm-store-net-config'

EXT_WGET = P_USR_BIN + 'wget'
EXT_WRITE_NET_CONFIG = P_VDSM + 'write-net-config'

CMD_LOWPRIO = [EXT_NICE, '-n', '19', EXT_IONICE, '-c', '2', '-n', '7']

#
# Storage constants
#
STRG_ISCSI_HOST = "iscsi_host/"
STRG_SCSI_HOST = "scsi_host/"
STRG_ISCSI_SESSION = "iscsi_session/"
STRG_ISCSI_CONNECION = "iscsi_connection/"
STRG_MPATH_CONF = """

defaults {
    polling_interval        5
    getuid_callout          "/sbin/scsi_id -g -u -d /dev/%n"
    no_path_retry           fail
    user_friendly_names     no
    flush_on_last_del       yes
    fast_io_fail_tmo        5
    dev_loss_tmo            30
    max_fds                 4096
}
"""

