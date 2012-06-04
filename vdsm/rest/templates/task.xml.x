<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<task href="/api/tasks/$resource.uuid" id="$resource.uuid">
  <verb>$resource.props['taskInfo']['verb']</verb>
  <message>$resource.props['taskStatus']['message']</message>
  <code>$resource.props['taskStatus']['code']</code>
  <result>$resource.props['taskStatus']['taskResult']</result>
  <state>$resource.props['taskStatus']['taskState']</state>
  <actions>
    <link rel="clear" href="/api/tasks/$resource.uuid/clear" />
    <link rel="revert" href="/api/tasks/$resource.uuid/revert" />
    <link rel="stop" href="/api/tasks/$resource.uuid/stop" />
  </actions>
</task>
