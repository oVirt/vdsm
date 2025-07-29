#!/bin/bash
set -e

WORKSPACE=/workspace

git config --add --global safe.directory $WORKSPACE
cat $WORKSPACE/.devcontainer/bashrc_additions >> ~/.bashrc