<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<storagedomains>
#for $resource in $resources
    <storagedomain href="/api/storagedomains/$resource.uuid" id="$resource.uuid">
      <name>$resource.info['name']</name>
      <type>$resource.info['type']</type>
      <class>$resource.info['class']</class>
      <role>$resource.info['role']</role>
      <remotePath>$resource.info['remotePath']</remotePath>
      <version>$resource.info['version']</version>
      <master_ver>$resource.info['master_ver']</master_ver>
      <lver>$resource.info['lver']</lver>
      <spm_id>$resource.info['spm_id']</spm_id>
    #if $resource.spUUID is not None
      <storagepool id="$resource.spUUID" href="/api/storagepools/$resource.spUUID"/>
      <link href="/api/storagedomains/$resource.uuid/images" rel="images"/>
      <link href="/api/storagedomains/$resource.uuid/volumes" rel="volumes"/>
#end if
      <actions>
        <link href="/api/storagedomains/$resource.uuid/format" rel="format"/>
        <link href="/api/storagedomains/$resource.uuid/attach" rel="attach"/>
        <link href="/api/storagedomains/$resource.uuid/detach" rel="detach"/>
        <link href="/api/storagedomains/$resource.uuid/activate" rel="activate"/>
        <link href="/api/storagedomains/$resource.uuid/deactivate" rel="deactivate"/>
      </actions>
    </storagedomain>
#end for
</storagedomains>
