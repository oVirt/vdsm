# Vdsm: Virtual Desktop Server Manager

[![Build Status](https://travis-ci.org/oVirt/vdsm.svg?branch=master)](https://travis-ci.org/oVirt/vdsm)

The Vdsm service exposes an API for managing virtualization
hosts running the KVM hypervisor technology. Vdsm manages and monitors
the host's storage, memory and networks as well as virtual machine
creation, other host administration tasks, statistics gathering, and
log collection.

## Manual installation

Add ovirt repositories to your repositories list:

    sudo dnf install -y http://resources.ovirt.org/pub/yum-repo/ovirt-release-master.rpm

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


## Development environment setup

Set local git clone:

    sudo dnf install -y git
    git clone "https://gerrit.ovirt.org/vdsm"

Install additional packages for Fedora, CentOS, and RHEL:

    sudo dnf install -y `cat automation/check-patch.packages.el8`

Create virtual environment for vdsm:

    python3 -m venv ~/.venv/vdsm
    source ~/.venv/vdsm/bin/activate
    pip install --upgrade pip
    pip install -r docker/requirements.txt
    deactivate

Before running vdsm tests, activate the environment:

    source ~/.venv/vdsm/bin/activate

When done, you can deactivate the environment:

    deactivate

## Building Vdsm

To configure sources (run `./configure --help` to see configuration options):

    git clean -xfd
    ./autogen.sh --system --enable-timestamp
    make

To test Vdsm (refer to tests/README for further tests information):

    make check

To create an RPM:

    rm -rf ~/rpmbuild/RPMS/*/vdsm*.rpm
    make rpm

To update your system with local build's RPM:

    (cd ~/rpmbuild/RPMS && sudo dnf upgrade */vdsm*.rpm)


## Making new releases

Release process of Vdsm version `VERSION` consists of the following
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

- Updating releng-tools with the new Vdsm version.  See releng-tools
  repo (`git clone https://gerrit.ovirt.org/releng-tools`) and Vdsm
  related patches there for examples.


## CI

Running tests locally is convenient, but before your changes can be
merged, we need to test them on all supported distributions and
architectures.

When you submit patches to gerrit, oVirt's Jenkins CI will run its tests
according to configuration in the stdci.yaml file.

### Travis CI for storage patches

oVirt's Jenkins CI is the integrated method for testing Vdsm patches,
however for storage related patches we have to cover also 4k tests which
are not covered currently by Jenkins CI. This can be achieved in a fast
way manually and independently from gerrit by invoking Travis CI on your
github branch:

- Fork the project on github.
- Visit https://travis-ci.org, register using your github account, and
  enable builds for your Vdsm fork.
- Push your changes to your github fork to trigger a build.

See .travis.yml file for tested Travis platforms and tests configurations.


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

To setup development with ovirt gerrit visit:

  https://ovirt.org/develop/dev-process/working-with-gerrit.html


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
