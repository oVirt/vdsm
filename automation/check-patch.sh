#!/bin/bash

EXPORT_PATH="$PWD/exported-artifacts"
TESTS_PATH="$PWD/tests"
COVERAGE_REPORT="$TESTS_PATH/htmlcov"

set -xe

./autogen.sh --system --enable-hooks
make check NOSE_WITH_COVERAGE=1 NOSE_COVER_PACKAGE="$PWD/vdsm,$PWD/lib"

./automation/build-artifacts.sh

# enable complex globs
shopt -s extglob
# if specfile was changed, try to install all created packages
if git diff-tree --no-commit-id --name-only -r HEAD | grep --quiet 'vdsm.spec.in' ; then
    yum -y install exported-artifacts/!(*.src).rpm
fi

# Generate coverage report in HTML format and save it
pushd "$TESTS_PATH"
coverage html
popd
mv "$COVERAGE_REPORT" "$EXPORT_PATH/"
