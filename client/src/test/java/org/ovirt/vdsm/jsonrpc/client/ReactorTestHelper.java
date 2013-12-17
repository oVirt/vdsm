package org.ovirt.vdsm.jsonrpc.client;

import org.ovirt.vdsm.jsonrpc.client.reactors.Reactor;

public interface ReactorTestHelper {
    public Reactor getReactor() throws Exception;

    public String getUriScheme();
}
