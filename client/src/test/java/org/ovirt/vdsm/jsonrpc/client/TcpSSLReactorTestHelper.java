package org.ovirt.vdsm.jsonrpc.client;

import org.ovirt.vdsm.jsonrpc.client.ClientConnectionException;
import org.ovirt.vdsm.jsonrpc.client.reactors.Reactor;
import org.ovirt.vdsm.jsonrpc.client.reactors.ReactorFactory;

public class TcpSSLReactorTestHelper implements ReactorTestHelper {

    @Override
    public Reactor getReactor() throws ClientConnectionException {
        return ReactorFactory.getReactor(new TestManagerProvider());
    }

    @Override
    public String getUriScheme() {
        return "tcp+ssl";
    }
}
