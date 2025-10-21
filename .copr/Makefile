# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

.PHONY: installdeps git_cfg_safe srpm

installdeps:
	dnf -y install autoconf automake gcc gettext-devel git libtool make openssl python3-dateutil python3-devel python3-libvirt python3-pyyaml systemd-units util-linux

git_cfg_safe:
	# From git 2.35.2 we need to mark temporary directory, where the project is cloned to, as safe, otherwise
	# git commands won't work because of the fix for CVE-2022-24765
	git config --global --add safe.directory "$(shell pwd)"
	
srpm: installdeps git_cfg_safe
	mkdir -p tmp.repos
	./autogen.sh \
            --system \
            --enable-hooks \
            --enable-vhostmd \
            --enable-timestamp
	make dist
	rpmbuild \
		-D "_topdir tmp.repos" \
		-ts ./*.tar.gz
	cp tmp.repos/SRPMS/*.src.rpm $(outdir)
