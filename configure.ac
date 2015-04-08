dnl
dnl vdsm-jsonrpc-java - vdsm json rpc
dnl Copyright (C) 2013 Red Hat, Inc.
dnl
dnl This library is free software; you can redistribute it and/or
dnl modify it under the terms of the GNU Lesser General Public
dnl License as published by the Free Software Foundation; either
dnl version 2.1 of the License, or (at your option) any later version.
dnl
dnl This library is distributed in the hope that it will be useful,
dnl but WITHOUT ANY WARRANTY; without even the implied warranty of
dnl MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
dnl Lesser General Public License for more details.
dnl
dnl You should have received a copy of the GNU Lesser General Public
dnl License along with this library; if not, write to the Free Software
dnl Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
dnl

AC_PREREQ(2.60)

define([VERSION_MAJOR], [1])
define([VERSION_MINOR], [1])
define([VERSION_FIX], [1])
define([VERSION_NUMBER], VERSION_MAJOR[.]VERSION_MINOR[.]VERSION_FIX)
define([VERSION_SUFFIX], [_master])

AC_INIT([vdsm-jsonrpc-java], VERSION_NUMBER[]VERSION_SUFFIX)
PACKAGE_RPM_VERSION="VERSION_NUMBER"
PACKAGE_RPM_RELEASE="0.0.$(echo VERSION_SUFFIX | sed 's/^_//')"
PACKAGE_MAVEN_VERSION="VERSION_MAJOR.VERSION_MINOR.VERSION_FIX"
AC_SUBST([PACKAGE_RPM_VERSION])
AC_SUBST([PACKAGE_RPM_RELEASE])
AC_SUBST([PACKAGE_MAVEN_VERSION])

AC_CONFIG_AUX_DIR([.])
AC_CONFIG_SRCDIR([Makefile.am])
AC_CONFIG_MACRO_DIR([m4])
AC_USE_SYSTEM_EXTENSIONS
AM_INIT_AUTOMAKE([tar-pax])

AC_ARG_ENABLE(
	[java-build],
	[AS_HELP_STRING([--disable-java-build], [disable java build @<:@default=yes@:>@])],
	,
	[enable_java_build="yes"]
)

AC_ARG_WITH(
	[maven],
	[AS_HELP_STRING([--with-maven], [use maven to build java @<:@default=no@:>@])],
	,
	[with_maven="no"]
)

AC_ARG_WITH(
	[maven-version-snapshot],
	[AS_HELP_STRING([--with-maven-version-snapshot], [maven version snapshot @<:@default=yes@:>@])],
	,
	[with_maven_version_snapshot="yes"]
)

AC_PROG_INSTALL
AC_PROG_SED
AC_PROG_MAKE_SET
AC_PROG_MKDIR_P

AC_ARG_VAR([MVN], [path to mvn utility])
AC_CHECK_PROGS([MVN], [mvn])
AC_ARG_VAR([JAVAC], [path to java compiler])
AC_CHECK_PROGS([JAVAC], [javac])
AC_ARG_VAR([jar], [path to jar utility])
AC_CHECK_PROGS([JAR], [jar])

AC_ARG_VAR([JACKSON_CORE_ASL], [path to jackson-core-asl jar])
AC_ARG_VAR([JACKSON_MAPPER_ASL], [path to jackson-mapper-asl jar])
AC_ARG_VAR([COMMONS_LOGGING_JAR], [path to apache-commons-logging jar])

if test "${with_maven}" = "yes"; then
	test -z "${MVN}" && \
		AC_MSG_ERROR([maven is required but not found])
else
	test -z "${JAVAC}" && \
		AC_MSG_ERROR([javac is required but not found])
	test -z "${JAR}" && \
		AC_MSG_ERROR([jar is required but not found])
fi

if test "${with_maven_version_snapshot}" = "no"; then
	MAVEN_VERSION_SNAPSHOT=""
else
	MAVEN_VERSION_SNAPSHOT="-SNAPSHOT"
fi
AC_SUBST([MAVEN_VERSION_SNAPSHOT])

AM_CONDITIONAL([ENABLE_JAVA_BUILD], [test "${enable_java_build}" = "yes"])
AM_CONDITIONAL([WITH_MAVEN], [test "${with_maven}" = "yes"])

AC_CONFIG_FILES([
	Makefile
	pom.xml
	vdsm-jsonrpc-java.spec
	client/Makefile
	client/pom.xml
])
AC_OUTPUT
