# Vdsm: Virtual Desktop Server Manager

[![Build Status](https://travis-ci.org/oVirt/vdsm.svg?branch=master)](https://travis-ci.org/oVirt/vdsm)

The Vdsm service exposes an API for managing virtualization
hosts running the KVM hypervisor technology. Vdsm manages and monitors
the host's storage, memory and networks as well as virtual machine
creation, other host administration tasks, statistics gathering, and
log collection.


## Installation

VDSM uses autoconf and automake as its build system.

To configure the build environment:

    ./autogen.sh --system

To see available options:

    ./configure --help

To create an RPM:

    make rpm

Install the desired Rpms from ~/rpmbuild/RPMS/noarch.

In order to start vdsm at first try, please perform:

    vdsm-tool configure [--force]

`--force` flag will override old conf files with vdsm defaults and
restart services that were configured (if were already running)


## Packaging

The 'vdsm.spec' file demonstrates how to distribute Vdsm as an RPM
package.


## Getting Help

There are two mailing lists for discussions:

- For technical discussions about the project and its code base.

  http://lists.ovirt.org/mailman/listinfo/devel

- For questions by users, who do not want to be swamped by
  technicalities.

  http://lists.ovirt.org/mailman/listinfo/users

The developers also hang out on IRC at #vdsm hosted on freenode.net

The latest upstream code can be obtained from GIT:

    git clone https://gerrit.ovirt.org/vdsm


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
