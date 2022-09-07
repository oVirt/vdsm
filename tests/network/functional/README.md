<!--
SPDX-FileCopyrightText: Red Hat, Inc.
SPDX-License-Identifier: GPL-2.0-or-later
-->

# VDSM network functional tests

### Running the tests in a container

This section describes the steps needed to run the functional tests
locally in a container.
Multiple runs may be executed in parallel.

#### Note
All of those tests are running with nmstate backend.

#### Build the container image
To build the container, under the vdsm/docker/network folder, run:
```
sudo make functional
```
Note: Building the container image is needed only if the Dockerfile
changes.

#### Usage examples

- Run to get the help message:
  `sudo ./tests/network/functional/run-tests.sh --help`

- Run tests based on nmstate backend:
  `sudo ./tests/network/functional/run-tests.sh`

- Run tests based on nmstate backend with nmstate installed from source:
  `sudo ./tests/network/functional/run-tests.sh --nmstate-pr=<PR_ID>`

- Run tests based on nmstate backend with local nmstate installed from source:
  `sudo ./tests/network/functional/run-tests.sh --nmstate-source=<PATH_TO_NMSTATE_SRC>`

- Open the container shell without executing the test run:
  `sudo ./tests/network/functional/run-tests.sh --shell`
  - At the container shell, you can run the tests:
  ```
  pytest \
    -x \
    -vv \
    --target-lib \
    -m "legacy_switch and nmstate" tests/network/functional
  ```

- Run tests based on ovs-switch:
  `sudo ./tests/network/functional/run-tests.sh --switch-type=ovs`
