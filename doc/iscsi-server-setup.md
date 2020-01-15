# Setting iSCSI storage

For testing oVirt setups using iSCSI storage domain, take following steps:

## 1. Install prerequisits (Fedora 30):
```
yum install targetcli  python3-rtslib  iscsi-initiator-utils
```

## 2. Create target LUNs:

```
cd contrib/target-tools/
./create-target  iqn1  -s 100 -r /target
```
Use -h for options list.

Expected output:
```
Creating target
  target_name:   iqn1
  target_iqn:    iqn.2003-01.org.vm-18-58.iqn1
  target_dir:    /target/iqn1
  lun_count:     10
  lun_size:      100

Create target? [N/y]: y
Creating target directory '/target/iqn1'
Creating target 'iqn.2003-01.org.vm-18-58.iqn1'
Created target iqn.2003-01.org.vm-18-58.iqn1.
Created TPG 1.
Global pref auto_add_default_portal=true
Created default portal listening on all IPs (0.0.0.0), port 3260.
Setting permissions (any host can access this target)
Parameter authentication is now '0'.
Parameter demo_mode_write_protect is now '0'.
Parameter generate_node_acls is now '1'.
Parameter cache_dynamic_acls is now '1'.
Creating disks
Creating backing file '/target/iqn1/00'
Creating backing store '/backstores/fileio/iqn1-00'
Created fileio iqn1-00 with size 1073741824
Parameter emulate_tpu is now '1'.
Parameter emulate_tpws is now '1'.
Parameter max_write_same_len is now '65335'.
Adding lun for '/backstores/fileio/iqn1-00'
Created LUN 0.
Creating backing file '/target/iqn1/01'
Creating backing store '/backstores/fileio/iqn1-01'
Created fileio iqn1-01 with size 1073741824
Parameter emulate_tpu is now '1'.
Parameter emulate_tpws is now '1'.
Parameter max_write_same_len is now '65335'.
Adding lun for '/backstores/fileio/iqn1-01'
Created LUN 1.
...
```

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

Discover LUNs from the add storage domain menu in oVirt web UI, choose iSCSI domain type
with machine's ip, "login all" and "add" for choosing the used LUNs.

To achieve multiple device paths mapping for the same LUNs, discover iSCSI storage of the same
target machine from different network interfaces.


## 6. Cleanup
```
cd contrib/target_tools/
./delete-target  -r /target
```
Use -h for options list.
