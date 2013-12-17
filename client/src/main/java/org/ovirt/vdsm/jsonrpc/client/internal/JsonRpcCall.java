package org.ovirt.vdsm.jsonrpc.client.internal;

import org.ovirt.vdsm.jsonrpc.client.JsonRpcResponse;

/**
 * Abstraction for single and batch calls to be updated
 * when response arrives.
 *
 */
public interface JsonRpcCall {
    /**
     * @param response Added to current call object.
     */
    void addResponse(JsonRpcResponse response);
}
