#
# Copyright 2018 Red Hat, Inc.
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

from __future__ import absolute_import
from __future__ import division

"""
This module contains functions to preprocess the domain XML before submitting
it to libvirt to create the VM.

= Historical context

Up until Vdsm 4.20, Vdsm was in charge to create the domain XML, using the
configuration sent by Engine in custom, json-like format. The actual
configuration format was actually a semi-defined Python dictionary serialized
either to JSON or XML (even earlier versions of Vdsm).

Starting with oVirt 4.2, Vdsm 4.20, the clients, like Engine, are expected
to send the fully formed domain XML.

The end goal is that Vdsm just passes this XML through libvirt; once libvirt
starts the VM, Vdsm will read back the up to date XML and will initialize
its data structures, needed to support all the flows and the API verbs.

However, Vdsm 4.20 can't implement the final goal for various reasons, most
important among them is the amount of legacy code which needs to be gradually
replaced, and the need to support per-host hooks, which may alter the XML -
see the localdisk hook for a prime example.
Storage devices may also need to change the XML.

= Affected flows

Two creation flows need to alter the domain XML, thus require the support of
the functionality of this module.

== Creation flow

In the creation flow, we need to handle
- placeholders. Engine may not know everything about the domain, or anyway
  intend to demand Vdsm some data. Examples are drive leases, or smbios
  settings.
  This is data that is relevent on per-host basis, and that either is not
  easily accessible by Engine, or that is actually more up to date on the
  host.
- hooks. Hooks may need to replace some parts of the XML.
- storage devices. We need to update their XML snippets with host-specific
  information. This requirement is expected to be lifted during the oVirt
  4.3 development cycle, but it still holds now.

== De-hibernation (aka restore state) flow

When Engine restarts a hibernated VM, it may change the volume chains
in the storage devices.
This may happen if some snapshots of the VMs where previewed and now
committed.
Vdsm needs to amend the restored XML to use those new leaf nodes.

= Placeholders

Starting with version 4.2, Engine may send in the domain XML special values
for VM-specific data it doesn't know, or that it know it is more updated
on the host when the VM is started.
A domain XML with placeholders must be syntactically valid, even though
the values which are actually placeholders will make no sense (e.g.
specifying one offset with the string OFFSET rather than with one unsigned
integer).

Vdsm will replace those special values with actual data.
Please check the documentation of the functions in this module to learn
about the supported placeholders and their meaning.
"""

from vdsm import host
from vdsm import osinfo

from vdsm.common import cpuarch
from vdsm.common import hooks
from vdsm.common import xmlutils
from vdsm.virt import domain_descriptor
from vdsm.virt import libvirtxml
from vdsm.virt import metadata
from vdsm.virt import vmdevices
from vdsm.virt import vmxml


def replace_disks_xml(dom, disk_devices):
    """
    Replace the XML snippet of all disk devices with fresh
    configuration obtained from Vdsm.
    Vdsm initializes its disk device objects from the XML.
    Vdsm also needs to do some per-host adjustments on
    the disk devices (e.g. prepare the paths), so we can't
    avoid this replacement.
    """
    # 'devices' MUST be present, so let's fail loudly if it isn't.
    devs = vmxml.find_first(dom, 'devices')

    to_remove = [
        dev_elem
        for dev_elem in vmxml.children(devs)
        if dev_elem.tag == vmdevices.hwclass.DISK
    ]
    for dev_elem in to_remove:
        vmxml.remove_child(devs, dev_elem)

    for dev in disk_devices:
        vmxml.append_child(devs, child=dev.getXML())

    return dom


def update_leases_xml_from_disk_objs(vm, dom, disk_devices):
    # 'devices' MUST be present, so let's fail loudly if it isn't.
    devs = vmxml.find_first(dom, 'devices')

    for dev_elem in vmxml.children(devs):
        if dev_elem.tag != vmdevices.hwclass.LEASE:
            continue

        params = vmdevices.lease.parse_xml(dev_elem, {})
        if not params:
            vm.log.warning('could not parse lease: %s',
                           xmlutils.tostring(dev_elem))
            continue

        info = vmdevices.lease.find_drive_lease_info(
            params['sd_id'], params['lease_id'], disk_devices)
        if info is None:
            vm.log.debug('lease with not corresponding drive info, skipped')
            # must be a vm lease, let's skip it
            continue

        vmdevices.lease.update_lease_element_from_info(
            dev_elem, info, params, vm.log)


def update_disks_xml_from_objs(vm, dom, disk_devices):
    """
    Perform host-local changes to the XML disk configuration.

    The XML may change because of the following:

    - the after_disk_prepare hook point (aka the localdisk hook)
      The hook may change:
      * diskType
      * path
      * format
    - Vdsm itself needs to prepare the images.
      Vdsm may change:
      * path

    Engine can sometimes predict the path, but Vdsm is in charge
    to set up the images locally, so it can (and should be expected to)
    change the image path.
    """
    # 'devices' MUST be present, so let's fail loudly if it isn't.
    devs = vmxml.find_first(dom, 'devices')
    for dev_elem in vmxml.children(devs):
        if dev_elem.tag != vmdevices.hwclass.DISK:
            continue

        # we use the device name because
        # - `path` uniquely identifies a device, but it is expected to change
        # - `serial` uniquely identifies a device, but it is not available
        #   for LUN devices
        # TODO: user-provided aliases are the best solution, we need to
        # switch to them.
        attrs = vmdevices.storage.Drive.get_identifying_attrs(dev_elem)
        if not attrs:
            vm.log.warning('could not identify drive: %s',
                           xmlutils.tostring(dev_elem))
            continue

        try:
            disk_obj = vmdevices.lookup.drive_by_name(disk_devices,
                                                      attrs['name'])
        except LookupError:
            vm.log.warning('unknown drive %r, skipped', attrs['name'])
            continue

        vmdevices.storagexml.update_disk_element_from_object(
            dev_elem, disk_obj, vm.log, replace_attribs=True)


def replace_device_xml_with_hooks_xml(dom, vm_id, vm_custom, md_desc=None):
    """
    Process the before_device_create hook point. This means that
    some device XML snippet may be entirely replaced by the output
    of one hook.
    Hook are invoked only if a given device has custom properties.
    Please note that this explicitely required by the contract of
    the hook point, so we can't really do better than that.
    """
    if md_desc is None:
        md_desc = metadata.Descriptor.from_tree(dom)
    # 'devices' MUST be present, so let's fail loudly if it isn't.
    devs = vmxml.find_first(dom, 'devices')

    to_remove = []
    to_append = []

    for dev_elem in vmxml.children(devs):
        if dev_elem.tag == vmdevices.hwclass.DISK:
            # disk devices need special processing, to be done separately
            continue

        try:
            dev_meta = vmdevices.common.dev_meta_from_elem(
                dev_elem, vm_id, md_desc)
        except vmdevices.core.SkipDevice:
            # metadata is optional, so it is ok to just skip devices
            # with no metadata attached.
            continue

        try:
            dev_custom = dev_meta['custom']
        except KeyError:
            # custom properties are optional, and mostly used for
            # network devices. It is OK to just go ahead.
            continue

        hook_xml = hooks.before_device_create(
            xmlutils.tostring(dev_elem, pretty=True),
            vm_custom,
            dev_custom)

        to_remove.append(dev_elem)
        to_append.append(xmlutils.fromstring(hook_xml))

    for dev_elem in to_remove:
        vmxml.remove_child(devs, dev_elem)

    for dev_elem in to_append:
        vmxml.append_child(devs, etree_child=dev_elem)


def replace_placeholders(dom, arch, serial=None):
    """
    Replace the placeholders, if any, in the domain XML.
    This is the entry point orchestration function.
    See the documentation of the specific functions
    for the supported placeholders.
    """
    if cpuarch.is_x86(arch):
        osd = osinfo.version()
        os_version = osd.get('version', '') + '-' + osd.get('release', '')
        serial_number = host.uuid() if serial is None else serial
        libvirtxml.update_sysinfo(
            dom, osd['name'], os_version, serial_number)


def _make_disk_devices(engine_xml, log):
    """
    Build disk devices, the same way VM class does.
    To process placeholders, we actually need the disk device parameters,
    but we use device instances out of convenience.

    Performance-wise the hardest part is the parsing of the XML, so
    using device instances is almost free.
    """
    engine_domain = domain_descriptor.DomainDescriptor(engine_xml)
    engine_md = metadata.Descriptor.from_xml(engine_xml)
    params = vmdevices.common.storage_device_params_from_domain_xml(
        engine_domain.id, engine_domain, engine_md, log)
    return [vmdevices.storage.Drive(log, **p) for p in params]
