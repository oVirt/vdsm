# VDSM network functional tests

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

## VDSM with the nmstate backend

### Configuring / Installing the environment
In order to run the vdsm network functional tests against the
[nmstate](https://github.com/nmstate/nmstate) backend, the following steps are
required:

* Configure VDSM to use the nmstate backend
To configure the nmstate backend in the functional tests, the vdsm configuration
has to be updated, explicitly selecting the nmstate backend. This is done by
ensuring the VDSM configuration file - i.e. ```/etc/vdsm/vdsm.conf``` - features:
```
net_nmstate_enabled = true
```

* Configure NetworkManager to manage ```dummy``` devices
Furthermore, the NetworkManager configuration **must** also be updated,
explicitly indicating the dummy interfaces used by the network functional
tests **are to** be managed by NetworkManager. That is achieved by adding the
following to /etc/NetworkManager/conf.d/vdsm-nmstate-func-net-tests.conf
```
[device]
match-device=dummy*
managed=1
```

* Install nmstate
This step is described in the project's
[install page](https://github.com/nmstate/nmstate/blob/master/README.install.md#nmstate-installation),
and the best way to use it in a development environment is to
[install from source](https://github.com/nmstate/nmstate/blob/master/README.install.md#install-nmstate-from-source).

* Restart VDSM services
Finally, the VDSM services have to be restarted. To restart - and reconfigure -
the relevant vdsm services execute:
```bash
systemctl restart vdsmd supervdsmd
```

### Executing the functional tests against an nmstate backend

* Remove the previous leftovers
Should the system feature any leftover, it should be removed, since it will
impact the test results. To do so, the user should:
```bash
# get rid of all dummy interfaces
nmcli conn del $(nmcli -f name conn show | grep dummy)
nmcli conn del $(nmcli -f name conn show | grep veth)

# get rid of all other entities
source contrib/shell_helper
emergency_net_cleanup
```

* Execute the nmstate network functional tests
Currently, the only tests in scope for the nmstate integration are the
**legacy_switch** tests.
From the *tests* folder, execute ```pytest -vvv -m "legacy_switch and nmstate" network/functional/```.

