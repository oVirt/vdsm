package org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl;

import java.nio.channels.SelectionKey;

public interface Reciever {
    void recieve(Message message, SelectionKey key);
}
