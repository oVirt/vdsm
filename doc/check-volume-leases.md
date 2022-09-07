<!--
SPDX-FileCopyrightText: Red Hat, Inc.
SPDX-License-Identifier: GPL-2.0-or-later
-->

# Check Volume Leases

Running legacy cold merge, when the cluster level <= 4.0, ends with
broken volume leases, because the volumes are renamed but not their
corresponding leases. While this doesn't affect users as long as they
run DC levels <= 4.0, when upgrading to ovirt >= 4.1, they will not be
able to perform storage operations that acquire volumes lease, e.g. new
cold merge, copy disk, etc.

To recover from this situation, we introduce a new tool,
check-volume-leases, that is executed using vdsm-tool (see examples
below).  The tool runs at the pool level, checks volume leases
of all the connected and active storage domains, and repair broken
leases if specified.

## Requirements

Before running the tool to repair leases, make sure the following
requirements are met:

- The storage domains are active
- No storage operation, like creating or removing snapshots, is
  performed

## Running the tool

There are two modes to run the tool: interactive and non-interactive.

### Interactive mode

In this mode, the user is prompted to confirm the operation twice:
before checking volume leases and before repairing the broken leases.

    $ vdsm-tool check-volume-leases

    WARNING: Make sure there are no running storage operations.

    Do you want to check volume leases? [yes,NO] yes

    Checking active storage domains. This can take several minutes, please wait.

    The following volume leases need repair:

    - domain: 4d19107b-1674-47ab-983f-65158926c90f

      - image: c2ad4977-e170-48cd-a73e-4dccca67234c
        - volume: 2be487e9-325c-4f79-8d24-38421fbfb412

    - domain: 9d5901c4-51fa-4858-b7d2-06a5c3776fe0

      - image: cbc68aeb-a730-4dc8-bf67-b7f9270556ee
        - volume: 433f7908-90eb-44b0-a6c7-8066081f0b9f

    Do you want to repair the leases? [yes,NO] yes

    Repairing volume leases ...
    Repaired (2/2) volume leases.

### Non-interactive mode

In this mode, the tool will run without any user interaction. The tool
will check volume leases and will repair the ones that need to be
repaired. This mode is triggered when the user provides the --repair
option when running the tool.

    $ vdsm-tool check-volume-leases --repair

    Checking active storage domains. This can take several minutes, please wait.

    The following volume leases need repair:

    - domain: 4d19107b-1674-47ab-983f-65158926c90f

      - image: c2ad4977-e170-48cd-a73e-4dccca67234c
        - volume: 5f6fd73f-ff73-4fda-8608-7fb8bce921d0

    - domain: 9d5901c4-51fa-4858-b7d2-06a5c3776fe0

      - image: cbc68aeb-a730-4dc8-bf67-b7f9270556ee
        - volume: 5a87bb6e-ceca-42e2-9064-e5f054d7a9a5

    Repairing volume leases ...
    Repaired (2/2) volume leases.
