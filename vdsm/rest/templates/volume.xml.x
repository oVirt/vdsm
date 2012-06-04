<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<volume href="/api/storagedomains/$resource.sdUUID/images/$resource.imgUUID/volumes/$resource.uuid" id="$resource.uuid">
  <description>$resource.info['description']</description>
  <voltype>$resource.info['voltype']</voltype>
  <type>$resource.info['type']</type>
  <disktype>$resource.info['disktype']</disktype>
  <format>$resource.info['format']</format>
  <path>$resource.info['path']</path>
  <apparentsize>$resource.info['apparentsize']</apparentsize>
  <truesize>$resource.info['truesize']</truesize>
  <capacity>$resource.info['capacity']</capacity>
  <ctime>$resource.info['ctime']</ctime>
  <mtime>$resource.info['mtime']</mtime>
  <legality>$resource.info['legality']</legality>
  <parent href="/api/storagedomains/$resource.sdUUID/images/$resource.imgUUID/volumes/$resource.info['parent']" id="$resource.info['parent']"/>
  <children>
#for $c_id in $resource.info['children']
    <link href="/api/storagedomains/$resource.sdUUID/images/$resource.imgUUID/volumes/$c_id" id="$c_id"/>
#end for
  </children>
  <actions>
      <link rel="delete" href="/api/storagedomains/$resource.sdUUID/images/$resource.imgUUID/volumes/$resource.uuid/delete"/>
  </actions>
</volume>
