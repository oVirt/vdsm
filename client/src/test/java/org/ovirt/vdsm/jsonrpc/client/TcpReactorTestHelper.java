package org.ovirt.vdsm.jsonrpc.client;

import org.ovirt.vdsm.jsonrpc.client.ClientConnectionException;
import org.ovirt.vdsm.jsonrpc.client.reactors.Reactor;
import org.ovirt.vdsm.jsonrpc.client.reactors.ReactorFactory;

public class TcpReactorTestHelper implements ReactorTestHelper {
    @Override
    public Reactor getReactor() throws ClientConnectionException {
        return ReactorFactory.getReactor(null);
    }

    @Override
    public String getUriScheme() {
        return "tcp";
    }

}
