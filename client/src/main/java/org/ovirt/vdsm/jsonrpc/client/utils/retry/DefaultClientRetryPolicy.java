package org.ovirt.vdsm.jsonrpc.client.utils.retry;

import java.io.IOException;

import org.ovirt.vdsm.jsonrpc.client.internal.ClientPolicy;

/**
 * Default implementation of operation level retry functionality.
 *
 */
public class DefaultClientRetryPolicy extends ClientPolicy {
    public DefaultClientRetryPolicy() {
        super(180000, 0, 10000, IOException.class);
    }
}
