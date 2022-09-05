#!/bin/bash -xe

./autogen.sh --system
make
make lint
