package org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl;

import java.nio.channels.SelectionKey;

public interface TestSender {
    void send(byte[] message, SelectionKey key);
}
