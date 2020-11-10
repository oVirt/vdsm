# Setting iSCSI storage

For testing oVirt setups using iSCSI storage domain, take following steps:

## 1. Install prerequisites (Fedora 32):

```
dnf install targetcli python3-rtslib iscsi-initiator-utils
```

## 2. Create target LUNs:

Here is an example creating new target named "my-target" on a host named
"my-host" with 10 100 GiB LUNs in /target/my-target/:

```
$ sudo contrib/target create my-target -r /target

Creating target
  target_name:   my-target
  target_iqn:    iqn.2003-01.org.my-host.my-target
  target_dir:    /target/my-target
  lun_count:     10
  lun_size:      100 GiB
  cache:         False

Create target? [N/y]:
```
Enter "y" to confirm and create the target.


## 3. Setup firewall for iSCSI service

```
sudo firewall-cmd --permanent --add-service iscsi-target
sudo firewall-cmd --reload
```

## 4. Enable target service:

```
sudo systemctl enable --now target.service
```

## 5. Add iSCSI storage from oVirt Management

Discover LUNs from the add storage domain menu in oVirt web UI, choose
iSCSI domain type with machine's IP address, "login all" and "add" for
choosing the used LUNs.

To achieve multiple device paths mapping for the same LUNs, discover
iSCSI storage of the same target machine from different network
interfaces.


## 6. Cleanup

In this example we delete my-target. Since we use non-standard root
directory we must specify it in the delete command as well.

```
$ sudo contrib/target delete my-target -r /target

Deleting target
  target_name:   my-target
  target_iqn:    iqn.2003-01.org.my-host.my-target
  target_dir:    /target/my-target
  lun_count:     10

Delete target? [N/y]:
```

Enter "y" to confirm and delete the target.
