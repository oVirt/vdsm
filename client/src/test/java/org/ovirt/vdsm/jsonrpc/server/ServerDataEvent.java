package org.ovirt.vdsm.jsonrpc.server;

import java.nio.channels.SocketChannel;

public class ServerDataEvent {
    public JsonRpcServer server;
    public SocketChannel socket;
    public byte[] data;

    public ServerDataEvent(JsonRpcServer server, SocketChannel socket, byte[] data) {
        this.server = server;
        this.socket = socket;
        this.data = data;
    }
}
