# Development

## Environment setup

Fork the project on https://github.com/oVirt/vdsm.

Clone your fork:

    sudo dnf install -y git
    git@github.com:{user}/vdsm.git

Enable oVirt packages for Fedora:

    sudo dnf copr enable -y nsoffer/ioprocess-preview
    sudo dnf copr enable -y nsoffer/ovirt-imageio-preview

Enable
[virt-preview](https://copr.fedorainfracloud.org/coprs/g/virtmaint-sig/virt-preview/)
repository to obtain latest qemu and libvirt versions:

    sudo dnf copr enable @virtmaint-sig/virt-preview

Update the system after enabling all repositories:

    sudo dnf update -y

Install additional packages for Fedora, CentOS, and RHEL:

    sudo dnf install -y `cat automation/check-patch.packages`

Create virtual environment for vdsm:

    make venv


## Building Vdsm

To configure sources (run `./configure --help` to see configuration options):

    git clean -xfd
    ./autogen.sh --system --enable-timestamp
    make

To test Vdsm (refer to tests/README for further tests information):

    make check

To create an RPM:

    make rpm

To upgrade your system with local build's RPM:

    make upgrade


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

When you push patches to GitHub, CI will run its tests according to the
configuration in the `.github/workflows/ci.yml` file.