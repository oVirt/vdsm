FROM centos:7
RUN yum install -y http://resources.ovirt.org/pub/yum-repo/ovirt-release-master.rpm && \
  yum update -y && \
  yum install -y \
    make \
    autoconf \
    automake git \
    python-nose \
    python-six \
    python-devel \
    libvirt-python \
    python-netaddr \
    python-inotify \
    libselinux-python \
    python-pthreading \
    python-cpopen mom \
    python34 \
    python34-nose \
    python34-six && \
  easy_install pip && \
  easy_install pyflakes && \
  pip install pep8==1.5.6

