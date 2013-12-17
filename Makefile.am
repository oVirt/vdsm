#
# vdsm-jsonrpc-java - plugable installer
# Copyright (C) 2012-2013 Red Hat, Inc.
#
# This library is free software; you can redistribute it and/or
# modify it under the terms of the GNU Lesser General Public
# License as published by the Free Software Foundation; either
# version 2.1 of the License, or (at your option) any later version.
#
# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public
# License along with this library; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#

AUTOMAKE_OPTIONS = foreign 1.9
ACLOCAL_AMFLAGS = -I m4

MAINTAINERCLEANFILES = \
	config.log config.status \
	$(srcdir)/Makefile.in \
	$(srcdir)/configure \
	$(srcdir)/install-sh $(srcdir)/missing \
	$(srcdir)/depcomp $(srcdir)/py-compile \
	$(srcdir)/config.guess $(srcdir)/config.sub \
	$(srcdir)/config.rpath \
	$(srcdir)/ABOUT-NLS \
	$(srcdir)/aclocal.m4 \
	$(srcdir)/ln-sr \
	$(NULL)

EXTRA_DIST = \
	.gitignore \
	$(NULL)

SUBDIRS = \
	client \
	$(NULL)

dist_noinst_DATA = \
	vdsm-jsonrpc-java.spec \
	$(NULL)

clean-local:
	rm -fr target
if ENABLE_JAVA_BUILD
if WITH_MAVEN
	$(MVN) clean
endif
endif

if ENABLE_JAVA_BUILD
if WITH_MAVEN

targetdir=$(abs_top_builddir)/target

all-local:
	rm -fr "$(targetdir)"
	$(MVN) install
	$(MKDIR_P) "$(targetdir)"
	$(MVN) \
		-D altDeploymentRepository=install::default::file://$(targetdir)/maven \
		deploy
	for artifact in client; do \
		find "$(targetdir)/maven" -name "$(PACKAGE_NAME)-$${artifact}-$(PACKAGE_MAVEN_VERSION)*.jar" | while read f; do \
			cp "$${f}" "$(targetdir)/$$(basename "$${f}")"; \
		done; \
	done
endif
endif
