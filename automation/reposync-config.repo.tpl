[main]
reposdir=/etc/reposync.repos.d

[local-vdsm-build-fc23]
name=VDSM local built rpms
baseurl=file://@PWD@/exported-artifacts
enabled=1
gpgcheck=0

[ovirt-4.0-snapshot-fc23]
name=oVirt Master Nightly Test Releases
baseurl=http://resources.ovirt.org/pub/ovirt-4.0-snapshot/rpm/fc23/
exclude=vdsm-* ovirt-node-* *-debuginfo ovirt-engine-appliance ovirt*engine* *win* *jboss*
enabled=0
gpgcheck=0

[ovirt-4.0-snapshot-static-fc23]
name=oVirt Master Nightly Statics
baseurl=http://resources.ovirt.org/pub/ovirt-4.0-snapshot-static/rpm/fc23/
exclude=jasperreports-server ovirt-guest-tools-iso ovirt-engine-jboss-as *wildfly*
enabled=0
gpgcheck=0
