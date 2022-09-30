<!--
SPDX-FileCopyrightText: Red Hat, Inc.
SPDX-License-Identifier: GPL-2.0-or-later
-->

# Virtual Desktop Server Manager

[![CI Status](https://github.com/oVirt/vdsm/actions/workflows/ci.yml/badge.svg)](https://github.com/oVirt/vdsm/actions)
[![Copr build status](https://copr.fedorainfracloud.org/coprs/ovirt/ovirt-master-snapshot/package/vdsm/status_image/last_build.png)](https://copr.fedorainfracloud.org/coprs/ovirt/ovirt-master-snapshot/package/vdsm/)

Welcome to the Vdsm source repository.

The Vdsm service exposes an API for managing virtualization
hosts running the KVM hypervisor technology. Vdsm manages and monitors
the host's storage, memory and networks as well as virtual machine
creation, other host administration tasks, statistics gathering, and
log collection.

## How to contribute

### Contibuting

To contribute please read the [development](./doc/development.md)
documentation.

### Submitting patches

Please use GitHub pull requests.

### Found a bug or documentation issue?

To submit a bug or suggest an enhancement for Vdsm please use
GitHub [issues](https://github.com/oVirt/vdsm/issues).

If you find a documentation issue on the oVirt website please navigate
and click "Report an issue on GitHub" in the page footer.

### Code review history

Vdsm moved to GitHub on Jan 9, 2022. To look up code reviews before this
date, please check the [Gerrit vdsm project](https://gerrit.ovirt.org/q/project:vdsm+is:merged).

## Manual installation

Add ovirt repositories to your repositories list.

For CentOS Stream 8 use:

    sudo dnf copr enable -y ovirt/ovirt-master-snapshot centos-stream-8
    sudo dnf install -y ovirt-release-master

For more info see
[copr master-snapshot repositories](https://copr.fedorainfracloud.org/coprs/ovirt/ovirt-master-snapshot/).

Install Vdsm:

    sudo dnf install vdsm vdsm-client

Configure Vdsm:

    sudo vdsm-tool configure --force

`--force` flag will override old conf files with vdsm defaults and
restart services that were configured (if were already running).

Enable and start Vdsm service:

    sudo systemctl enable --now vdsmd

To inspect Vdsm service status:

    sudo systemctl status vdsmd

Vdsm logs can be found at `/var/log/vdsm/*.log` (refer to README.logging for further information).


## Getting Help

There are two mailing lists for discussions:

- For technical discussions about the project and its code base.

  https://lists.ovirt.org/admin/lists/devel.ovirt.org/

- For questions by users, who do not want to be swamped by
  technicalities.

  https://lists.ovirt.org/admin/lists/users.ovirt.org/

The developers also hang out on IRC at #vdsm hosted on freenode.net

The latest upstream code can be obtained from GIT:

    git clone https://github.com/oVirt/vdsm


## Licensing

Vdsm is provided under the terms of the GNU General Public License,
version 2 or later. Please see the COPYING file for complete GPLv2+
license terms.

In addition, as a special exception, Red Hat, Inc. and its affiliates
give you permission to distribute this program, or a work based on it,
linked or combined with the OpenSSL project's OpenSSL library (or a
modified version of that library) to the extent that the library, or
modified version, is covered by the terms of the OpenSSL or SSLeay
licenses.  Corresponding source code for the object code form of such
a combination shall include source code for the parts of OpenSSL
contained in the combination.

If you modify this program, you may extend this exception to your
version, but you are not obligated to do so.  If you do not wish to do
so, delete this exception statement from your version.

