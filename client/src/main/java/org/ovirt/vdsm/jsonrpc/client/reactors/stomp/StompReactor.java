package org.ovirt.vdsm.jsonrpc.client.reactors.stomp;

import java.io.IOException;
import java.nio.channels.Selector;
import java.nio.channels.SocketChannel;

import org.ovirt.vdsm.jsonrpc.client.ClientConnectionException;
import org.ovirt.vdsm.jsonrpc.client.reactors.Reactor;
import org.ovirt.vdsm.jsonrpc.client.reactors.ReactorClient;

public class StompReactor extends Reactor {

    public StompReactor() throws IOException {
        super();
    }

    @Override
    public String getReactorName() {
        return "Stomp Reactor";
    }

    @Override
    public ReactorClient createClient(Reactor reactor, Selector selector, String hostname, int port)
            throws ClientConnectionException {
        return new StompClient(reactor, selector, hostname, port);
    }

    @Override
    public ReactorClient createConnectedClient(Reactor reactor, Selector selector,
            String hostname, int port, SocketChannel channel) throws ClientConnectionException {
        return new StompListener(reactor, selector, hostname, port, channel);
    }

}
