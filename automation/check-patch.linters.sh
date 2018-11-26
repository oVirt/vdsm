#!/bin/bash -xe

source automation/common.sh

prepare_env
install_dependencies
build_vdsm

make gitignore execcmd abs_imports py3division imports python3 flake8 pylint
