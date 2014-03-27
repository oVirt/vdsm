package org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl;


public interface CommandExecutor {
    public Message execute(Message message);
}
