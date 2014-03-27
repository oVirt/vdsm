package org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl;


public interface Sender {
    void send(byte[] message);
}
