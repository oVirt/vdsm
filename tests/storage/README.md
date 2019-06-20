# Storage tests


## Setting up user storage

Some tests modules require pre-configured storage in a well-known
location. If the storage is not configured, these tests can skip, xfail
or fail (depending on the test).

To setup storage for these tests, run:

    python tests/storage/userstorage.py setup

This can be run once when setting up development environment, and must
be run again after rebooting the host.

If you want to tear down the user storage, run:

    python tests/storage/userstorage.py teardown

There is no need to tear down the storage normally. The loop devices are
backed up by sparse files and do not consume much resources.


## Storage test matrix

These are the supported storage configurations:

- file-512 - file system backed by loop device with 512 bytes sector
  size.

- file-4k - file system backed by loop device with 4k sector size. This
  configuration is not available on CentOS 7 and is known to be fail
  randomly on oVirt CI.

Storage configurations which are not supported on the current
environment are skipped automatically. The tests must deal with the
missing storage configuration by detecting that the storage does not
exist.

To add new storage configurations edit the STORAGE list in the
userstorage.py tool.


## How user storage works?

The userstorage.py tool creates this directory layout:

$ tree /var/tmp/vdsm-storage/
/var/tmp/vdsm-storage/
├── backing.file-4k
├── backing.file-512
├── loop.file-4k -> /dev/loop5
├── loop.file-512 -> /dev/loop4
├── mount.file-4k
│   └── file
└── mount.file-512
    └── file

The symbolic links (e.g. loop.file-4k) link to the loop devices created
by the helper (/dev/loop4), and used to tear down the storage.

The actual file used for the tests are created inside the mounted
filesystem (/var/tmp/vdsm-storage/mount.file-4k/file).
