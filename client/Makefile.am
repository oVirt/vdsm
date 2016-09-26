#
# vdsm-jsonrpc-java - vdsm jspn rpc
# Copyright (C) 2013 Red Hat, Inc.
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

MAINTAINERCLEANFILES = \
	$(srcdir)/Makefile.in \
	$(NULL)

dist_noinst_DATA = \
	MANIFEST.MF \
	src \
	$(NULL)

if ENABLE_JAVA_BUILD
if !WITH_MAVEN

targetdir=$(abs_top_builddir)/target

all-local:	\
		vdsm-jsonrpc-java-client-$(PACKAGE_MAVEN_VERSION).jar \
		$(NULL)
	$(MKDIR_P) "$(targetdir)"
	cp *.jar "$(targetdir)"

vdsm-jsonrpc-java-client-$(PACKAGE_MAVEN_VERSION).jar: \
		src/main/java/org/ovirt/vdsm/jsonrpc/client/*.java \
		$(NULL)
	$(MKDIR_P) target
	$(JAVAC) \
		-source 1.8 -target 1.8 \
		-cp $(JACKSON_MAPPER_ASL):$(JACKSON_CORE_ASL):${SLF4J_JAR} \
		-d target \
		$$(find src/main -name '*.java')
	$(JAR) -cmf "$(srcdir)/MANIFEST.MF" "vdsm-jsonrpc-java-client-$(PACKAGE_MAVEN_VERSION).jar" -C target .

clean-local:
	rm -fr target *.jar

endif
endif
