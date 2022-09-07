<!--
SPDX-FileCopyrightText: Red Hat, Inc.
SPDX-License-Identifier: GPL-2.0-or-later
-->

# Storage tests


## Setting up user storage

Some tests modules require pre-configured storage in a well-known
location. If the storage is not configured, these tests can skip, xfail
or fail (depending on the test).

To setup storage for these tests, run:

    make storage

This can be run once when setting up development environment, and must
be run again after rebooting the host.

If you want to tear down the user storage, run:

    make clean-storage

There is no need to tear down the storage normally. The loop devices are
backed up by sparse files and do not consume much resources.


## Storage test matrix

The storage is configured in the file storage.py.
Tests that use user storage, load this configuration file and access the
storage via the BACKENDS dict.
These are the available storage configurations:

- file-512 - a file on a file system backed by loop device with 512
  bytes sector size.

- file-4k - a file on a file system backed by loop device with 4k sector
  size. This configuration is not available on CentOS 7 and is known to
  be fail randomly on oVirt CI.

- mount-512 - mounted file system backed by loop device with 512 bytes
  sector size.

- mount-4k - mounted file system backed by loop device with 4k sector
  size.  This configuration is not available on CentOS 7 and is known to
  be fail randomly on oVirt CI.

Storage configurations which are not supported on the current
environment are skipped automatically. The tests must deal with the
missing storage configuration by detecting that the storage does not
exist.

To add new storage configurations edit the BACKENDS list in storage.py.

For more info on userstorage, including details of how it works,
see https://github.com/nirs/userstorage.
