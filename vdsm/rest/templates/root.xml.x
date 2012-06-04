<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<api>
  <product_info>
    <name>$resource.product_info['name']</name>
    <vendor>$resource.product_info['vendor']</vendor>
    <version major="$resource.product_info['version']['major']" minor="$resource.product_info['version']['minor']" build="$resource.product_info['version']['build']" revision="$resource.product_info['version']['revision']"/>
  </product_info>
  <link href="/api/storageconnectionrefs" rel="storageconnectionrefs"/>
  <link href="/api/storagedomains" rel="storagedomains"/>
  <link href="/api/tasks" rel="tasks"/>
</api>
