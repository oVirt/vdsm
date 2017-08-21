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

## Containers support

While Vdsm focus is on managing KVM virtual machines, it could also run
containers alongside virtual machines, using docker.

Containers are reported as special-purpose VMs to the clients, and respond
to the Vdsm API invoked on them.
If a particular container runtime doesn't support an operation, this will
fail with a standard Vdsm error.

To try this out, you just need to install the 'vdsm-containers subpackage'.
Make sure to restart *both* supervdsmd and vdsmd once that package is installed.
You'll also need to have the container runtime you wish to use installed on
the same host which runs Vdsm. At the moment, only docker is supported.

To check if the Vdsm is properly configured to run containers, just do:

    # vdsm-client Host getCapabilities | grep containers
            "containers": true,

This means that this Vdsm could also run docker containers.

Any Engine >= 3.6 could handle containers - they are just VMs from its perspective.
You just need to set a few custom properties. Run this command
on your Engine host:

    # engine-config -s UserDefinedVMProperties='volumeMap=^[a-zA-Z_-]+:[a-zA-Z_-]+$;containerImage=^[a-zA-Z]+(://|)[a-zA-Z]+$;containerType=^docker$' --cver=4.1

replace --cver=4.1 with the version of the Engine you are using.
There is no need to configure the regular expressions to match your environment,
they should be used verbatim.
Now restart Ovirt Engine, and log in.

You can now run any container. The user defined VM properties define
the key settings which are not (yet) exposed in the engine UI.
You can change those values freely using the "edit VM" window in the
Engine webadmin UI.

- volumeMap allows you to mount any disk inside the container, should you
  need any persistence. It is a mapping between disks (e.g. vda)
  and mountpoint (e.g. data). The mountpoints are just container-dependent labels.

- containerImage is the URL of any container image supported by your
  runtime. E.g. 'redis'. You must use the [same format docker uses](https://docs.docker.com/engine/reference/run/)

- containerType allows to select the runtime you want to use. Currently only
  the docker runtime is supported. This variable actually enables all the container
  support infrastructure, on a per-VM basis.
  Without this property set, the default is to ignore any container setting.

Please be aware that many settings are ignored by containers, like all
the device configurations. Only memory and CPU settings are honoured.


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
