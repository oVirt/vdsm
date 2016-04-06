FROM centos:7
RUN yum install -y http://resources.ovirt.org/pub/yum-repo/ovirt-release-master.rpm && \
  yum update -y && \
  yum install -y \
    autoconf \
    automake git \
    libselinux-python \
    libvirt-python \
    make \
    psmisc \
    python-cpopen mom \
    python-devel \
    python-inotify \
    python-ioprocess \
    python-netaddr \
    python-nose \
    python-pthreading \
    python-six \
    python34 \
    python34-nose \
    python34-six \
    && \
  easy_install pip && \
  easy_install pyflakes && \
  pip install pep8==1.5.6
