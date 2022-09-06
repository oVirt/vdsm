# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division
from vdsm.common import exception

# TODO: Drop after callers changed to raise the exceptions
errCode = {
    'noVM': exception.NoSuchVM().response(),
    'nfsErr': exception.AccessTimeout().response(),
    'exist': exception.VMExists().response(),
    'noVmType': exception.UnsupportedVMType().response(),
    'down': exception.VMIsDown().response(),
    'copyerr': exception.CopyFailed().response(),
    'sparse': exception.CannotCreateSparse().response(),
    'createErr': exception.CannotCreateVM().response(),
    'noConPeer': exception.NoConnectionToPeer().response(),
    'MissParam': exception.MissingParameter().response(),
    'migrateErr': exception.MigrationError().response(),
    'imageErr': exception.ImageFileNotFound().response(),
    'outOfMem': exception.OutOfMemory().response(),
    'unexpected': exception.UnexpectedError().response(),
    'unsupFormat': exception.UnsupportedImageFormat().response(),
    'ticketErr': exception.SpiceTicketError().response(),
    'nonresp': exception.NonResponsiveGuestAgent().response(),
    # codes 20-35 are reserved for add/delNetwork
    # code 39 was used for:
    # wrongHost - migration destination has an invalid hostname
    'unavail': exception.ResourceUnavailable().response(),
    'changeDisk': exception.ChangeDiskFailed().response(),
    'destroyErr': exception.VMDestroyFailed().response(),
    'fenceAgent': exception.UnsupportedFenceAgent().response(),
    'noimpl': exception.MethodNotImplemented().response(),
    'hotplugDisk': exception.HotplugDiskFailed().response(),
    'hotunplugDisk': exception.HotunplugDiskFailed().response(),
    'migCancelErr': exception.MigrationCancelationFailed().response(),
    'snapshotErr': exception.SnapshotFailed().response(),
    'hotplugNic': exception.HotplugNicFailed().response(),
    'hotunplugNic': exception.HotunplugNicFailed().response(),
    'migInProgress': exception.MigrationInProgress().response(),
    'mergeErr': exception.MergeFailed().response(),
    'balloonErr': exception.BalloonError().response(),
    'momErr': exception.MOMPolicyUpdateFailed().response(),
    'replicaErr': exception.ReplicaError().response(),
    'updateDevice': exception.UpdateDeviceFailed().response(),
    'hwInfoErr': exception.CannotRetrieveHWInfo().response(),
    'resizeErr': exception.BadDiskResizeParameter().response(),
    'transientErr': exception.TransientError().response(),
    'setNumberOfCpusErr': exception.SetNumberOfCpusFailed().response(),
    'haErr': exception.SetHAPolicyFailed().response(),
    'cpuTuneErr': exception.CpuTuneError().response(),
    'updateVmPolicyErr': exception.UpdateVMPolicyFailed().response(),
    'updateIoTuneErr': exception.UpdateIOTuneError().response(),
    'V2VConnection': exception.V2VConnectionError().response(),
    'NoSuchJob': exception.NoSuchJob().response(),
    'V2VNoSuchOvf': exception.V2VNoSuchOVF().response(),
    'JobNotDone': exception.JobNotDone().response(),
    'JobExists': exception.JobExists().response(),
    'JobNotActive': exception.JobNotActive().response(),
    'hotplugMem': exception.HotplugMemFailed().response(),
    'ksmErr': exception.KSMUpdateFailed().response(),
    'secretBadRequestErr': exception.BadSecretRequest().response(),
    'secretRegisterErr': exception.SecretRegistrationFailed().response(),
    'secretUnregisterErr': exception.SecretUnregistrationFailed().response(),
    'unsupportedOperationErr': exception.UnsupportedOperation().response(),
    'freezeErr': exception.FreezeGuestFSFailed().response(),
    'thawErr': exception.ThawGuestFSFailed().response(),
    'hookError': exception.HookFailed().response(),
    'destVolumeTooSmall': exception.DestinationVolumeTooSmall().response(),
    'AbortNotSupported': exception.AbortNotSupported().response(),
    'migNotInProgress': exception.MigrationNotInProgress().response(),
    'migrateLimit': exception.MigrationLimitExceeded().response(),
    'recovery': exception.RecoveryInProgress().response(),
    'hostdevDetachErr': exception.HostdevDetachFailed().response(),
    'migOperationErr': exception.MigrationOperationError().response(),
}


doneCode = {'code': 0, 'message': 'Done'}

# exitCodes
ERROR = 1
NORMAL = 0
