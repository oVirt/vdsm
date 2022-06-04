.PHONY: installdeps srpm

installdeps:
	dnf -y install autoconf automake gcc gettext-devel git libtool make openssl python3-dateutil python3-devel python3-libvirt python3-pyyaml python3-six systemd-units util-linux

srpm: installdeps
	$(eval SUFFIX=$(shell sh -c " echo '.$$(date -u +%Y%m%d%H%M%S).git$$(git rev-parse --short HEAD)'"))
	mkdir -p tmp.repos
	./autogen.sh \
            --system \
            --enable-hooks \
            --enable-vhostmd
	make dist
	rpmbuild \
		-D "_topdir tmp.repos" \
		-D "vdsm_release ${SUFFIX}" \
		-ts ./*.tar.gz
	cp tmp.repos/SRPMS/*.src.rpm $(outdir)
