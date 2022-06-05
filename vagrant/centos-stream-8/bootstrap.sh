#!/usr/bin/env bash

dnf update -y

# TODO: Should really use ovirt-release-master, but it adds lots of unneeded
# and problematic repos. For now, use Nir's repos.

dnf copr enable -y nsoffer/ioprocess-preview
dnf copr enable -y nsoffer/ovirt-imageio-preview

# TODO: Add NetworkManager, nmstate, python3-libmnstate (see
# automation/check-patch.packages).
# Installing these pacakges breaks the VM network and vagrant ssh cannot
# connect.

dnf install -y \
    autoconf \
    automake \
    bash \
    createrepo \
    device-mapper-multipath \
    dnf \
    dnf-utils \
    e2fsprogs \
    gcc \
    gdb \
    git \
    iproute-tc \
    iscsi-initiator-utils \
    libguestfs-tools-c \
    lshw \
    lsof \
    lvm2 \
    make \
    openssl \
    ovirt-imageio-client \
    python3-augeas \
    python3-blivet \
    python3-cryptography \
    python3-dateutil \
    python3-dbus \
    python3-decorator \
    python3-devel \
    python3-dmidecode \
    python3-ioprocess \
    python3-libselinux \
    python3-libvirt \
    python3-magic \
    python3-nose \
    python3-pip \
    python3-policycoreutils \
    python3-pyyaml \
    python3-requests \
    python3-sanlock \
    python3-six \
    python3-yaml \
    qemu-img \
    rpm-build \
    rpmlint \
    sanlock \
    sudo \
    systemd \
    systemd-udev \
    xfsprogs \
