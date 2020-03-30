# VDSM network unit tests

#### Run the tests
Run the following from the project root directory.
```
./tests/network/unit/run-tests.sh
```
To be able to run the unit tests from shell, run:
```
./tests/network/unit/run-tests.sh --shell
```
After shell is opened, proceed on navigating to the
project working directory (in this case '/vdsm-tmp/vdsm'),
and run the tests with pytest:
```
pytest -vv --log-level=DEBUG tests/network/unit
```

Note: Running the tests without building the image locally first,
will result in an attempt to download the image from an
available repository.

#### Build the container image

This section describes the steps needed to run the unit tests
locally in a container.

To build the container, under the vdsm/docker folder, run:
```
podman build \
    -t ovirt/vdsm-test-unit-network-centos-8 \
    -f "Dockerfile.unit-network-centos-8"
```
Proceed on running the tests as mentioned above.

### Running the tests in Jenkins

The unit tests are ran automatically in check-patch, but in case
a manual run is needed, it is possible through Jenkins as part
of the check-network.

To run the network unit tests manually on Jenkins, go to:
https://jenkins.ovirt.org/job/standard-manual-runner/build

You have to be logged in to Jenkins and have the appropriate
permissions to run builds.

There are three relevant parameters to fill in:

* **STD_CI_CLONE_URL**: `git://gerrit.ovirt.org/vdsm`

* **STD_CI_REFSPEC**: The gerrit refspec. Check how to get it
[here](#extract-the-gerrit-refspec-for-a-patch).

* **STD_CI_STAGE**: `check-patch`

Click build, and the check-patch should start. To see a more verbose version
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