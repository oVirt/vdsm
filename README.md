# Virtual Desktop Server Manager

[![CI Status](https://github.com/oVirt/vdsm/actions/workflows/ci.yml/badge.svg)](https://github.com/oVirt/vdsm/actions)
[![Copr build status](https://copr.fedorainfracloud.org/coprs/ovirt/ovirt-master-snapshot/package/vdsm/status_image/last_build.png)](https://copr.fedorainfracloud.org/coprs/ovirt/ovirt-master-snapshot/package/vdsm/)

Welcome to the VDSM source repository.

The VDSM service exposes an API for managing virtualization
hosts running the KVM hypervisor technology. VDSM manages and monitors
the host's storage, memory and networks as well as virtual machine
creation, other host administration tasks, statistics gathering, and
log collection.

## How to contribute

### Submitting patches

Please use GitHub pull requests.

### Found a bug or documentation issue?

To submit a bug or suggest an enhancement for VDSM please use
[oVirt Bugzilla for VDSM product](https://bugzilla.redhat.com/enter_bug.cgi?product=vdsm).

If you find a documentation issue on the oVirt website please navigate
and click "Report an issue on GitHub" in the page footer.

### Code review history

VDSM moved to GitHub on Jan 9, 2022. To look up code reviews before this
date, please check the [Gerrit VDSM project](https://gerrit.ovirt.org/q/project:vdsm+is:merged).

## Manual installation

Add ovirt repositories to your repositories list.

For CentOS Stream 8 use:

    sudo dnf copr enable -y ovirt/ovirt-master-snapshot centos-stream-8
    sudo dnf install -y ovirt-release-master

For more info see
[copr master-snapshot repositories](https://copr.fedorainfracloud.org/coprs/ovirt/ovirt-master-snapshot/).

Install VDSM:

    sudo dnf install vdsm vdsm-client

Configure VDSM:

    sudo vdsm-tool configure --force

`--force` flag will override old conf files with VDSM defaults and
restart services that were configured (if were already running).

Enable and start VDSM service:

    sudo systemctl enable --now vdsmd

To inspect VDSM service status:

    sudo systemctl status vdsmd

VDSM logs can be found at `/var/log/vdsm/*.log` (refer to README.logging for further information).


## Development environment setup

Fork the project on https://github.com/oVirt/vdsm.

Clone your fork:

    sudo dnf install -y git
    git@github.com:{user}/vdsm.git

Enable oVirt packages for Fedora:

    sudo dnf copr enable -y nsoffer/ioprocess-preview
    sudo dnf copr enable -y nsoffer/ovirt-imageio-preview

Install additional packages for Fedora, CentOS, and RHEL:

    sudo dnf install -y `cat automation/check-patch.packages`

Create virtual environment for VDSM:

    python3 -m venv ~/.venv/vdsm
    source ~/.venv/vdsm/bin/activate
    pip install --upgrade pip
    pip install -r docker/requirements.txt
    deactivate

Before running VDSM tests, activate the environment:

    source ~/.venv/vdsm/bin/activate

When done, you can deactivate the environment:

    deactivate

## Building VDSM

To configure sources (run `./configure --help` to see configuration options):

    git clean -xfd
    ./autogen.sh --system --enable-timestamp
    make

To test VDSM (refer to tests/README for further tests information):

    make check

To create an RPM:

    rm -rf ~/rpmbuild/RPMS/*/vdsm*.rpm
    make rpm

To update your system with local build's RPM:

    (cd ~/rpmbuild/RPMS && sudo dnf upgrade */vdsm*.rpm)


## Making new releases

Release process of VDSM version `VERSION` consists of the following
steps:

- Changing `Version:` field value in `vdsm.spec.in` to `VERSION`.

- Updating `%changelog` line in `vdsm.spec.in` to the current date,
  the committer, and `VERSION`.

- Committing these changes, with subject "New release: `VERSION`" and
  posting the patch to gerrit.

- Verifying the patch by checking that the Jenkins build produced a
  correct set of rpm's with the correct version.

- Merging the patch (no review needed).

- Tagging the commit immediately after merge with an annotated tag:
  `git tag -a vVERSION`

- Setting "Keep this build forever" for the check-merge Jenkins build.

- Updating releng-tools with the new VDSM version.  See releng-tools
  repo (`git clone https://gerrit.ovirt.org/releng-tools`) and VDSM
  related patches there for examples.


## CI

Running tests locally is convenient, but before your changes can be
merged, we need to test them on all supported distributions and
architectures.

When you submit patches to gerrit, oVirt's Jenkins CI will run its tests
according to configuration in the stdci.yaml file.

### Travis CI for storage patches

oVirt's Jenkins CI is the integrated method for testing VDSM patches,
however for storage related patches we have to cover also 4k tests which
are not covered currently by Jenkins CI. This can be achieved in a fast
way manually and independently from gerrit by invoking Travis CI on your
github branch:

- Fork the project on github.
- Visit https://travis-ci.org, register using your github account, and
  enable builds for your VDSM fork.
- Push your changes to your github fork to trigger a build.

See .travis.yml file for tested Travis platforms and tests configurations.


## Getting Help

There are two mailing lists for discussions:

- For technical discussions about the project and its code base.

  https://lists.ovirt.org/admin/lists/devel.ovirt.org/

- For questions by users, who do not want to be swamped by
  technicalities.

  https://lists.ovirt.org/admin/lists/users.ovirt.org/

The developers also hang out on IRC at #vdsm hosted on freenode.net

The latest upstream code can be obtained from GIT:

    git clone https://gerrit.ovirt.org/vdsm


## Licensing

VDSM is provided under the terms of the GNU General Public License,
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
