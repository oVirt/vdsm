package org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl;

import java.nio.channels.SelectionKey;

public class TestServerListener {

    private SelectionKey key;
    private TestSender sender;

    public TestServerListener(SelectionKey key, TestSender sender) {
        this.key = key;
        this.sender = sender;
    }

    public void update(Message message) {
        this.sender.send(message.build(), key);
    }

    public SelectionKey getKey() {
        return this.key;
    }
}
