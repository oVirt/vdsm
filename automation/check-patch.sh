#!/bin/bash

EXPORT_DIR="$PWD/exported-artifacts"

set -xe

./autogen.sh --system --enable-hooks

# 'make check' runs pep8 and pyflakes for all .py files. To generate
# templates (py.in files) we run 'make' before.
make all

make check NOSE_WITH_COVERAGE=1 NOSE_COVER_PACKAGE="$PWD/vdsm,$PWD/lib"

./automation/build-artifacts.sh

# enable complex globs
shopt -s extglob
# if specfile was changed, try to install all created packages
if git diff-tree --no-commit-id --name-only -r HEAD | grep --quiet 'vdsm.spec.in' ; then
    yum -y install "$EXPORT_DIR/"!(*.src).rpm
fi

# Generate coverage report in HTML format
pushd tests
coverage html -d "$EXPORT_DIR/htmlcov"
popd
