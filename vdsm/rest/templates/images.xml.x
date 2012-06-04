<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<images>
#for $resource in $resources
    <image href="/api/storagedomains/$resource.sdUUID/images/$resource.uuid" id="$resource.uuid">
      <link rel="volumes" href="/api/storagedomains/$resource.sdUUID/images/$resource.uuid/volumes"/>
      <actions>
        <link rel="delete" href="/api/storagedomains/$resource.sdUUID/images/$resource.uuid/delete"/>
      </actions>
    </image>
#end for
</images>
