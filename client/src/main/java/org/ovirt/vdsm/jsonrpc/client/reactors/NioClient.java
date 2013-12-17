package org.ovirt.vdsm.jsonrpc.client.reactors;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.channels.ClosedChannelException;
import java.nio.channels.SelectionKey;
import java.nio.channels.Selector;
import java.nio.channels.SocketChannel;

import org.ovirt.vdsm.jsonrpc.client.ClientConnectionException;

/**
 * <code>ReactorClient</code> implementation to provide not encrypted communication.
 *
 */
public final class NioClient extends ReactorClient {
    private final Selector selector;

    public NioClient(Reactor reactor, Selector selector, String hostname, int port) throws ClientConnectionException {
        super(reactor, hostname, port);
        this.selector = selector;
    }

    public NioClient(Reactor reactor, Selector selector, String hostname, int port, SocketChannel socketChannel)
            throws ClientConnectionException {
        super(reactor, hostname, port);
        this.selector = selector;

        channel = socketChannel;
        postConnect();
    }

    @Override
    public void updateInterestedOps() throws ClientConnectionException {
        if (outbox.isEmpty()) {
            getSelectionKey().interestOps(SelectionKey.OP_READ);
        } else {
            getSelectionKey().interestOps(SelectionKey.OP_READ | SelectionKey.OP_WRITE);
        }
    }

    @Override
    void read(ByteBuffer buff) throws IOException {
        channel.read(buff);
    }

    @Override
    void write(ByteBuffer buff) throws IOException {
        channel.write(buff);
    }

    @Override
    void postConnect() throws ClientConnectionException {
        try {
            int interestedOps = SelectionKey.OP_READ;
            reactor.wakeup();
            key = this.channel.register(selector, interestedOps, this);
        } catch (ClosedChannelException e) {
            throw new ClientConnectionException(e);
        }
    }
}
