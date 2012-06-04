<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<storagepools>
#for $resource in $resources
    <storagepool href="/api/storagepools/$resource.uuid" id="$resource.uuid">
      <pool_status>$resource.info['pool_status']</pool_status>
    #if $resource.info['pool_status'] == "connected"
      <name>$resource.info['name']</name>
      <isoprefix>$resource.info['isoprefix']</isoprefix>
      <master_uuid>$resource.info['master_uuid']</master_uuid>
      <version>$resource.info['version']</version>
      <spm_id>$resource.info['spm_id']</spm_id>
      <type>$resource.info['type']</type>
      <master_ver>$resource.info['master_ver']</master_ver>
      <lver>$resource.info['lver']</lver>
      <domains>
    #for $sdUUID, $sdStats in $resource.dominfo.items()
        <link href="/api/storagedomains/$sdUUID" id="$sdUUID">
    #if $sdStats['status'] == "Active"
          <diskfree>${sdStats['diskfree']}</diskfree>
          <disktotal>${sdStats['disktotal']}</disktotal>
    #end if
          <status>${sdStats['status']}</status>
        </link>
    #end for
    #end if
      </domains>
      <actions>
        <link href="/api/storagepools/$resource.uuid/destroy" rel="destroy"/>
        <link href="/api/storagepools/$resource.uuid/disconnect" rel="disconnect"/>
        <link href="/api/storagepools/$resource.uuid/spmStart" rel="spmStart"/>
        <link href="/api/storagepools/$resource.uuid/spmStop" rel="spmStop"/>
      </actions>
    </storagepool>
#end for
</storagepools>
