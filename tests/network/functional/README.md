# VDSM network functional tests

### Running the tests in a container

This section describes the steps needed to run the functional tests
locally in a container.
Multiple runs may be executed in parallel.

#### Note
All of those tests are running with nmstate backend.

#### Build the container image
To build the container, under the vdsm/docker folder, run:
```
sudo podman build \
  --rm \
  -t \
  ovirt/vdsm-test-func-network-centos-8 \
  -f Dockerfile.func-network-centos-8 \
  .
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

### Manually running the tests

To run the functional network tests manually on Jenkins, go to:
https://jenkins.ovirt.org/job/standard-manual-runner/build

You have to be logged in to Jenkins and have the appropriate
permissions to run builds.

There are three relevant parameters to fill in:

* **STD_CI_CLONE_URL**: `git://gerrit.ovirt.org/vdsm`

* **STD_CI_REFSPEC**: The gerrit refspec. Check how to get it
[here](#extract-the-gerrit-refspec-for-a-patch).

* **STD_CI_STAGE**: `check-network`

Click build, and the check network should start. To see a more verbose version
of the output grouped by the substage name, click on 'Open Blue Ocean' on the
left toolbar on the jenkins job page.

### Extract the gerrit refspec for a patch

Go to the gerrit patch page (login is not necessary), on the top right side of
the page there is a 'Download' button. After clicking on it you will find a
'ref' url-type string signifying the version of the patch.

It is possible to switch the patch version by clicking the 'Patch Sets' button
to the left of 'Download'. Click the relevant Patch set, go back to 'Download',
 and grab the updated 'refspec'.

For example, for [this patch](https://gerrit.ovirt.org/#/c/100022/)
the 'refspec' of the latest version would be:

`refs/changes/22/100022/4`

