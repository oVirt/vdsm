# VDSM network integration tests

#### Run the tests
Run the following from the project root directory.
```
sudo ./tests/network/integration/run-tests.sh
```
To be able to run the integration tests from shell, run:
```
sudo ./tests/network/integration/run-tests.sh --shell
```
After shell is opened, proceed on navigating to the
project working directory (in this case '/vdsm-tmp/vdsm'),
and run the tests with pytest:
```
pytest -vv --log-level=DEBUG tests/network/integration
```

Note: Running the tests without building the image locally first,
will result in an attempt to download the image from an
available repository.

#### Build the container image

This section describes the steps needed to run the integration tests
locally in a container.

To build the container, under the vdsm/docker/network folder, run:
```
sudo make integration
```
Proceed on running the tests as mentioned above.
