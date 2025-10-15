<!--
SPDX-FileCopyrightText: Red Hat, Inc.
SPDX-License-Identifier: GPL-2.0-or-later
-->

# Managed Volume Adapters

By default the managed volumes in Vdsm are manipulated through an external
helper that interfaces with the `os_brick` package (part of OpenStack).

Similarly on the oVirt Engine side there is another external helper that
interfaces with CinderLib.

The two together can expose any supported Cinder storage driver to oVirt.

- on the oVirt Engine side the helper deals with creating and managing volumes,
  snapshots and preparing them to be attached to a VM;

- on the Vdsm side the helper deals only with attaching and detaching volumes
  that have been created by the oVirt Engine.

The adapter mechanism redirects the execution to vendor-provided helper
executables. To facilitate this redirection both the oVirt Engine and Vdsm have
the notion of `adapter`.

This allows storage vendors to integrate their managed storage directly in
oVirt/Vdsm.

## oVirt Engine

Managed Storage Domains with adapter dispatch have an `adapter` field in their
`driver_options` map that indicates the helper executable to run instead of
the default `cinderlib-client.py` helper.

The vendor packaging is expected to install a symlink in
`/usr/share/ovirt-engine/cinderlib/` named `{adapter}-adapter`.

## Vdsm 

The `adapter` field is passed to Vdsm in the `connection_info` parameter (of
type `ManagedVolumeConnection`).

If the `adapter` field is not present, Vdsm uses the default OS-Brick helper
`managedvolume-helper`.

If the `adapter` field is present, Vdsm will instead execute 
`managedvolume-helper-{adapter}` when attaching and detaching volumes.
