# SPDX-FileCopyrightText: oVirt Developers
# SPDX-License-Identifier: GPL-2.0-or-later

from vdsm.common import exception
from vdsm.common import validate
from vdsm.common import xmlutils
from vdsm.common.hostdev import (
    detach_detachable,
    reattach_detachable,
    device_name_from_address,
    spawn_mdev,
    despawn_mdev,
    MdevPlacement,
    MdevProperties,
    is_nvidia_vgpu_vf,
    name_to_pci_path,
    nvidia_vgpu_vfs_in_use,
    release_nvidia_vgpu,
)
from vdsm.virt import vmxml

from . import core

# Device metadata key that marks the PCI host device of a VF holding a vGPU,
# see HostDevice.get_extra_metadata().
_NVIDIA_VGPU_VF = 'nvidiaVgpuVf'
_USER_ALIAS_PREFIX = 'ua-'


def _get_device_name_type(dev):
    dev_type = core.find_device_type(dev)
    src_dev = vmxml.find_first(dev, 'source')
    src_addr = vmxml.device_address(src_dev)
    if dev_type == 'scsi':
        src_addr = _normalize_scsi_address(dev, src_addr)
    elif dev_type == 'pci':
        src_addr = _normalize_pci_address(**src_addr)
    elif dev_type == 'mdev':
        return src_addr['uuid'], dev_type
    return device_name_from_address(dev_type, src_addr), dev_type


def _normalize_pci_address(domain, bus, slot, function, **kwargs):
    """
    Wrapper around normalize_pci_address to handle transparently
    the extra fields of the address (e.g. type) which don't need
    normalization.
    """
    kwargs.update(
        **validate.normalize_pci_address(domain, bus, slot, function)
    )
    return kwargs


def _normalize_scsi_address(dev, addr):
    adapter = vmxml.find_attr(dev, 'adapter', 'name')
    addr['host'] = adapter.replace('scsi_host', '')
    addr['lun'] = addr.pop('unit')
    return addr


def _mdev_properties(dev, meta):
    mdev_type = None
    mdev_placement = MdevPlacement.COMPACT
    mdev_driver_parameters = meta.get('mdevDriverParameters')
    mdev_metadata = meta.get('mdevType')
    if mdev_metadata:
        mdev_info = mdev_metadata.split('|')
        mdev_type = mdev_info[0]
        if len(mdev_info) > 1:
            mdev_placement = mdev_info[1]
    return MdevProperties(mdev_type, mdev_placement, mdev_driver_parameters)


def _is_local_nvidia_vgpu_vf(type_, name):
    return (
        type_ == 'pci'
        and name is not None
        and is_nvidia_vgpu_vf(name_to_pci_path(name))
    )


def setup_device(dom, meta, log):
    name, type_ = _get_device_name_type(dom)
    if type_ == 'pci' and (
        meta.get(_NVIDIA_VGPU_VF) or _is_local_nvidia_vgpu_vf(type_, name)
    ):
        # Only the running XML of a vGPU VM names a vGPU VF, see
        # spawn_mdev(), e.g. a migration or hibernation from another host,
        # where this address may be another device or none at all. Detaching
        # would take that device from the host, or unbind a VF from the
        # NVIDIA driver, and no vGPU would be created here.
        message = (
            'vgpu: Cannot assign vGPU VF %s (device %s) as a plain PCI '
            'device; VMs with a vGPU on a VF cannot be migrated or restored'
            % (name, core.find_device_alias(dom))
        )
        log.error(message)
        raise exception.ResourceUnavailable(message)
    elif name is None:
        log.debug(
            "Unknown kind of host device: %s",
            xmlutils.tostring(dom, pretty=True),
        )
    elif type_ == 'mdev':
        spawn_mdev(_mdev_properties(dom, meta), name, log)
    else:
        log.info('Detaching device %s from the host.' % (name,))
        detach_detachable(name)
        log.info('Device %s dettached from the host.' % (name,))


def teardown_device(dom, log, meta=None):
    name, type_ = _get_device_name_type(dom)
    if name is None:
        log.debug(
            "Unknown kind of host device: %s",
            xmlutils.tostring(dom, pretty=True),
        )
    elif type_ == 'mdev':
        despawn_mdev(name)
    elif _is_local_nvidia_vgpu_vf(type_, name):
        # A vGPU VF set up by spawn_mdev(): it stays bound to the NVIDIA
        # driver, reattaching it would unbind it.
        _release_vgpu_vf(name_to_pci_path(name), log)
    elif meta and meta.get(_NVIDIA_VGPU_VF):
        # The vGPU VF of another host, setup refused it: whatever sits at
        # this address here was never detached.
        log.info('Device %s is not a vGPU VF on this host, skipping.', name)
    else:
        pci_reattach = type_ == 'pci'
        log.info('Reattaching device %s to the host.' % (name,))
        reattach_detachable(name, pci_reattach=pci_reattach)
        log.info('Device %s reattached to the host.' % (name,))


def _release_vgpu_vf(vf, log):
    # Teardown runs after this VM's domain is gone. If another domain uses the
    # VF, this VM never got it (setup refused it, e.g. on a migration
    # destination or after a failed start) and the vGPU is the other VM's.
    try:
        in_use = nvidia_vgpu_vfs_in_use()
    except Exception:
        # The driver refuses to remove a vGPU that a running VM holds.
        log.exception('Cannot tell whether VF %s is in use', vf)
        in_use = set()
    if vf in in_use:
        log.info('Keeping the vGPU on VF %s, another VM uses it.', vf)
        return
    log.info('Removing vGPU from VF %s.', vf)
    release_nvidia_vgpu(vf)


class HostDevice(core.HotpluggedDevice):

    @classmethod
    def get_identifying_attrs(cls, dom):
        dev_type = core.find_device_type(dom)
        attrs = {'devtype': dom.tag}
        if dev_type == 'mdev':
            attrs['uuid'] = vmxml.device_address(dom)['uuid']
        else:
            attrs['name'] = core.find_device_alias(dom)
        return attrs

    @classmethod
    def get_extra_metadata(cls, dom, md_desc):
        """
        Mark the PCI host device of a VF holding a vGPU, see
        replace_nvidia_vgpu_hostdevs(). Its user alias carries the UUID of
        the mdev device it replaced, and Engine's metadata of that mdev
        device gives its type. Unlike the VF's sysfs, this holds on any host,
        e.g. on a migration destination where this address is another
        device.
        """
        if core.find_device_type(dom) != 'pci':
            return {}
        alias = core.find_device_alias(dom)
        if not alias or not alias.startswith(_USER_ALIAS_PREFIX):
            return {}
        mdev_uuid = alias[len(_USER_ALIAS_PREFIX) :]
        for data in md_desc.all_devices(devtype='hostdev', uuid=mdev_uuid):
            if data.get('mdevType'):
                return {_NVIDIA_VGPU_VF: True}
        return {}

    def setup(self):
        setup_device(self._dom, self._meta, self._log)

    def teardown(self):
        teardown_device(self._dom, self._log, self._meta)
