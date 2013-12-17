package org.ovirt.vdsm.jsonrpc.client.internal;

import org.ovirt.vdsm.jsonrpc.client.JsonRpcClient;

/**
 * Context containing a message and client.
 *
 */
public class MessageContext {
    private JsonRpcClient client;
    private byte[] message;

    public MessageContext(JsonRpcClient client, byte[] message) {
        this.client = client;
        this.message = message;
    }

    public JsonRpcClient getClient() {
        return client;
    }

    public byte[] getMessage() {
        return message;
    }
}
