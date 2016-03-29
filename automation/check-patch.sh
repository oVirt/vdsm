#!/bin/bash

EXPORT_DIR="$PWD/exported-artifacts"

set -xe

./autogen.sh --system --enable-hooks

# Run nosetests only over fedora mock to save Jenkins resources
if grep -q 'Fedora' /etc/redhat-release; then
   make check NOSE_WITH_COVERAGE=1 NOSE_COVER_PACKAGE="$PWD/vdsm,$PWD/lib"
fi

./automation/build-artifacts.sh

# enable complex globs
shopt -s extglob
# if specfile was changed, try to install all created packages
if git diff-tree --no-commit-id --name-only -r HEAD | grep --quiet 'vdsm.spec.in' ; then
    yum -y install "$EXPORT_DIR/"!(*.src).rpm
fi

if grep -q 'Fedora' /etc/redhat-release; then
   # Generate coverage report in HTML format
   pushd tests
   coverage html -d "$EXPORT_DIR/htmlcov"
   popd
fi
