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
