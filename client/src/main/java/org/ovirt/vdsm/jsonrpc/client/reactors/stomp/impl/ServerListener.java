package org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl;


public class ServerListener {
    private Sender sender;

    public ServerListener(Sender sender) {
        this.sender = sender;
    }

    public void update(Message message) {
        this.sender.send(message.build());
    }
}
