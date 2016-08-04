[main]
reposdir=/etc/reposync.repos.d

[local-vdsm-build-fc24]
name=VDSM local built rpms
baseurl=file://@PWD@/exported-artifacts
enabled=1
gpgcheck=0

[ovirt-master-snapshot-fc24]
name=oVirt Master Nightly Test Releases
baseurl=http://resources.ovirt.org/pub/ovirt-master-snapshot/rpm/fc24/
exclude=vdsm-* ovirt-node-* *-debuginfo ovirt-engine-appliance ovirt*engine* *win* *jboss*
enabled=0
gpgcheck=0

[ovirt-master-snapshot-static-fc24]
name=oVirt Master Nightly Statics
baseurl=http://resources.ovirt.org/pub/ovirt-master-snapshot-static/rpm/fc24/
exclude=jasperreports-server ovirt-guest-tools-iso ovirt-engine-jboss-as *wildfly*
enabled=0
gpgcheck=0
