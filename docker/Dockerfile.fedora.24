FROM fedora:24
RUN dnf install -y http://resources.ovirt.org/pub/yum-repo/ovirt-release-master.rpm && \
  dnf update -y && \
  dnf install -y \
    PyYAML \
    autoconf \
    automake \
    bridge-utils \
    dbus-python \
    dosfstools \
    e2fsprogs \
    gcc \
    genisoimage \
    git \
    glusterfs-api \
    libselinux-python \
    libselinux-python3 \
    libvirt-python \
    libvirt-python3 \
    lshw \
    make \
    mom \
    openvswitch \
    ovirt-imageio-common \
    psmisc \
    python-blivet \
    python-cpopen \
    python-dateutil \
    python-decorator \
    python-devel \
    python-inotify \
    python-ioprocess \
    python-magic \
    python-netaddr \
    python-pip \
    python-pthreading \
    python-requests \
    python-six \
    python2-mock \
    python3 \
    python3-dateutil \
    python3-dbus \
    python3-decorator \
    python3-netaddr \
    python3-nose \
    python3-six \
    python3-yaml \
    redhat-rpm-config \
    rpm-python \
    sanlock-python \
    sudo \
    systemd \
    systemd-udev \
    which \
    && \
  pip install nose==1.3.7 tox==2.5.0 yappi==0.93 && \
  dnf clean all
