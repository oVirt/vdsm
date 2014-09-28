package org.ovirt.vdsm.jsonrpc.client.reactors;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.channels.ClosedChannelException;
import java.nio.channels.SelectionKey;
import java.nio.channels.Selector;
import java.nio.channels.SocketChannel;
import java.util.concurrent.Callable;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.FutureTask;

import javax.net.ssl.SSLContext;
import javax.net.ssl.SSLEngine;
import javax.net.ssl.SSLException;

import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.ovirt.vdsm.jsonrpc.client.ClientConnectionException;
import org.ovirt.vdsm.jsonrpc.client.reactors.stomp.StompCommonClient;
import org.ovirt.vdsm.jsonrpc.client.utils.OneTimeCallback;
import org.ovirt.vdsm.jsonrpc.client.utils.retry.Retryable;

/**
 * <code>ReactorClient</code> implementation to provide encrypted communication.
 *
 */
public abstract class SSLClient extends StompCommonClient {
    private static Log log = LogFactory.getLog(SSLClient.class);
    protected final Selector selector;
    protected SSLEngineNioHelper nioEngine;
    private SSLContext sslContext;
    private boolean client;

    public SSLClient(Reactor reactor, Selector selector,
            String hostname, int port, SSLContext sslctx) throws ClientConnectionException {
        super(reactor, hostname, port);
        this.selector = selector;
        this.sslContext = sslctx;
        this.client = true;
    }

    public SSLClient(Reactor reactor, Selector selector, String hostname, int port,
            SSLContext sslctx, SocketChannel socketChannel) throws ClientConnectionException {
        super(reactor, hostname, port);
        this.selector = selector;
        this.sslContext = sslctx;
        this.client = false;
        channel = socketChannel;

        postConnect(null);
    }

    protected SSLEngine createSSLEngine(boolean clientMode) {
        final SSLEngine engine = this.sslContext.createSSLEngine();
        engine.setUseClientMode(clientMode);
        return engine;
    }

    @Override
    public void updateInterestedOps() {
        if (outbox.isEmpty() && (this.nioEngine == null || !this.nioEngine.handshakeInProgress())) {
            getSelectionKey().interestOps(SelectionKey.OP_READ);
        } else {
            getSelectionKey().interestOps(SelectionKey.OP_READ | SelectionKey.OP_WRITE);
        }
    }

    private Runnable pendingOperations() throws IOException, ClientConnectionException {
        if (this.nioEngine == null) {
            return null;
        }

        return nioEngine.process();

    }

    @Override
    protected int read(ByteBuffer buff) throws IOException {
        if (this.nioEngine != null) {
            return this.nioEngine.read(buff);
        } else {
            return channel.read(buff);
        }
    }

    @Override
    void write(ByteBuffer buff) throws IOException {
        if (this.nioEngine != null) {
            this.nioEngine.write(buff);
        } else {
            channel.write(buff);
        }
    }

    @Override
    public void process() throws IOException, ClientConnectionException {
        final Runnable op = pendingOperations();
        if (op != null) {
            key.interestOps(0);
            scheduleTask(new Callable<Void>() {
                @Override
                public Void call() {
                    op.run();
                    updateInterestedOps();
                    selector.wakeup();
                    return null;
                }
            });
        }

        if (isInInit()) {
            return;
        }
        super.process();
    }

    @Override
    protected void postConnect(OneTimeCallback callback) throws ClientConnectionException {
        try {
            final ReactorClient client = this;
            final FutureTask<SelectionKey> task = scheduleTask(new Retryable<SelectionKey>(
                    new Callable<SelectionKey>() {

                        @Override
                        public SelectionKey call() throws ClosedChannelException {
                            return channel.register(selector, SelectionKey.OP_READ | SelectionKey.OP_WRITE , client);
                        }
                    }, this.policy));

            key = task.get();

            this.nioEngine = new SSLEngineNioHelper(channel, createSSLEngine(this.client), callback, this);
            this.nioEngine.beginHandshake();
        } catch (SSLException | InterruptedException | ExecutionException e) {
            log.error("Connection issues during ssl client creation", e);
            throw new ClientConnectionException(e);
        }
        if (key == null) {
            throw new ClientConnectionException("Connection issue during post connect");
        }
    }

    @Override
    public void postDisconnect() {
        if (this.nioEngine != null) {
            this.nioEngine.clearBuff();
        }
        outbox.clear();
        this.nioEngine = null;
    }
}
