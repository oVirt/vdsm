FROM centos:7
RUN yum install -y http://resources.ovirt.org/pub/yum-repo/ovirt-release-master.rpm && \
  yum update -y && \
  yum install -y \
    PyYAML \
    autoconf \
    automake git \
    dosfstools \
    e2fsprogs \
    genisoimage \
    libselinux-python \
    libvirt-python \
    make \
    psmisc \
    python-cpopen mom \
    python-decorator \
    python-devel \
    python-inotify \
    python-ioprocess \
    python-netaddr \
    python-nose \
    python-pthreading \
    python-setuptools \
    python-six \
    python34 \
    python34-nose \
    python34-six \
    sanlock-python \
    which \
    && \
  easy_install pip && \
  easy_install pyflakes && \
  pip install pep8==1.5.6
