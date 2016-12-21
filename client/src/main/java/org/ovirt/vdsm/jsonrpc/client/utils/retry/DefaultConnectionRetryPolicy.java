package org.ovirt.vdsm.jsonrpc.client.utils.retry;

import java.io.IOException;

import org.ovirt.vdsm.jsonrpc.client.internal.ClientPolicy;

/**
 * Default implementation of connection level retry functionality.
 *
 */
public class DefaultConnectionRetryPolicy extends ClientPolicy {
    public DefaultConnectionRetryPolicy() {
        super(2000, 0, 0, IOException.class);
    }
}
