#!/bin/sh
GIT_RELEASE=`git describe | awk -F- '{print $3 "." $5}' | tr -d '\n'`
GIT_BRANCH=`git branch | awk '/^*/{print $2}' | sed 's/[^a-zA-Z0-9_.]//g'`
echo -n $GIT_RELEASE.$GIT_BRANCH
