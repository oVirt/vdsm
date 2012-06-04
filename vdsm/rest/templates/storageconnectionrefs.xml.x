<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<storageconnectionrefs>
#for $resource in $resources
  <storageconnectionref href="/api/storageconnectionrefs/$resource.uuid" id="$resource.uuid">
    <type>$resource.info['connectionInfo']['type']</type>
    <parameters>
  #if $resource.info['connectionInfo']['type'] == 'localfs'
      <path>$resource.info['connectionInfo']['params']['path']</path>
  #end if
  #if $resource.info['connectionInfo']['type'] == 'iscsi'
      <target>$resource.info['connectionInfo']['params']['target']</target>
      <iface>$resource.info['connectionInfo']['params']['iface']</iface>
      <credentials>$resource.info['connectionInfo']['params']['credentials']</credentials>
  #end if
  #if $resource.info['connectionInfo']['type'] in ('sharedfs', 'posixfs')
      <spec>$resource.info['connectionInfo']['params']['spec']</spec>
      <vfsType>$resource.info['connectionInfo']['params']['vfsType']</vfsType>
      <options>$resource.info['connectionInfo']['params']['options']</options>
  #end if
  #if $resource.info['connectionInfo']['type'] == 'nfs'
      <export>$resource.info['connectionInfo']['params']['export']</export>
      <retrans>$resource.info['connectionInfo']['params']['retrans']</retrans>
      <timeout>$resource.info['connectionInfo']['params']['timeout']</timeout>
      <version>$resource.info['connectionInfo']['params']['version']</version>
  #end if
    </parameters>
    <lastError>
      <code>$resource.info['lastError'][0]</code>
      <message>$resource.info['lastError'][1]</message>
    </lastError>
    <connected>$str($resource.info['connected']).lower()</connected>
    <actions>
      <link href="/api/storageconnectionrefs/$resource.uuid/release" rel="release"/>
    </actions>
  </storageconnectionref>
#end for
  <actions>
    <link href="/api/storageconnectionrefs/acquire" rel="acquire"/>
  </actions>
</storageconnectionrefs>
