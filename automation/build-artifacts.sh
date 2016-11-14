#!/bin/bash -xe



MAVEN_SETTINGS="/etc/maven/settings.xml"

# Use ovirt mirror if able, fall back to central maven
mkdir -p "${MAVEN_SETTINGS%/*}"
cat >"$MAVEN_SETTINGS" <<EOS
<?xml version="1.0"?>
<settings xmlns="http://maven.apache.org/POM/4.0.0"
          xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
          xsi:schemaLocation="http://maven.apache.org/POM/4.0.0
          http://maven.apache.org/xsd/settings-1.0.0.xsd">

<mirrors>
        <mirror>
                <id>ovirt-maven-repository</id>
                <name>oVirt artifactory proxy</name>
                <url>http://artifactory.ovirt.org/artifactory/ovirt-mirror</url>
                <mirrorOf>*</mirrorOf>
        </mirror>
        <mirror>
                <id>root-maven-repository</id>
                <name>Official maven repo</name>
                <url>http://repo.maven.apache.org/maven2</url>
                <mirrorOf>*</mirrorOf>
        </mirror>
</mirrors>
</settings>
EOS


mkdir -p exported-artifacts

# build tarballs
./autogen.sh --system

./configure
make dist
mv *.tar.gz exported-artifacts/

## install deps
yum-builddep vdsm-jsonrpc-java.spec

## build src.rpm
SUFFIX=".$(date -u +%Y%m%d%H%M%S).git$(git rev-parse --short HEAD)"
rpmbuild \
    -D "_topdir $PWD/rpmbuild"  \
    -D "release_suffix ${SUFFIX}" \
    -ta exported-artifacts/*.gz

find rpmbuild -iname \*.rpm -exec mv {} exported-artifacts/ \;

## we don't need the rpmbuild dir no more
rm -Rf rpmbuild
