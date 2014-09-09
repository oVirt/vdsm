package org.ovirt.vdsm.jsonrpc.client.reactors;

import java.io.IOException;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.nio.ByteBuffer;
import java.nio.channels.SelectionKey;
import java.nio.channels.SocketChannel;
import java.util.Deque;
import java.util.List;
import java.util.concurrent.Callable;
import java.util.concurrent.ConcurrentLinkedDeque;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.Future;
import java.util.concurrent.FutureTask;
import java.util.concurrent.locks.Lock;
import java.util.concurrent.locks.ReentrantLock;

import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.ovirt.vdsm.jsonrpc.client.ClientConnectionException;
import org.ovirt.vdsm.jsonrpc.client.utils.LockWrapper;
import org.ovirt.vdsm.jsonrpc.client.utils.OneTimeCallback;
import org.ovirt.vdsm.jsonrpc.client.utils.retry.DefaultConnectionRetryPolicy;
import org.ovirt.vdsm.jsonrpc.client.utils.retry.RetryPolicy;
import org.ovirt.vdsm.jsonrpc.client.utils.retry.Retryable;

/**
 * Abstract implementation of <code>JsonRpcClient</code> which handles low level networking.
 *
 */
public abstract class ReactorClient {
    public interface MessageListener {
        public void onMessageReceived(byte[] message);
    }
    public static final int BUFFER_SIZE = 1024;
    private static Log log = LogFactory.getLog(ReactorClient.class);
    private final String hostname;
    private final int port;
    private final Lock lock;
    private long lastHeartbeat = 0;
    protected RetryPolicy policy = new DefaultConnectionRetryPolicy();
    protected final List<MessageListener> eventListeners;
    protected final Reactor reactor;
    protected final Deque<ByteBuffer> outbox;
    protected SelectionKey key;
    protected ByteBuffer ibuff = null;
    protected SocketChannel channel;

    public ReactorClient(Reactor reactor, String hostname, int port) {
        this.reactor = reactor;
        this.hostname = hostname;
        this.port = port;
        this.eventListeners = new CopyOnWriteArrayList<MessageListener>();
        this.lock = new ReentrantLock();
        this.outbox = new ConcurrentLinkedDeque<>();
    }

    public String getHostname() {
        return this.hostname;
    }

    public void setRetryPolicy(RetryPolicy policy) {
        this.policy = policy;
        if (isOpen()) {
            disconnect();
        }
    }

    public RetryPolicy getRetryPolicy() {
        return this.policy;
    }

    public void connect() throws ClientConnectionException {
        if (isOpen()) {
            return;
        }
        try (LockWrapper wrapper = new LockWrapper(this.lock)) {
            if (isOpen()) {
                return;
            }
            final FutureTask<SocketChannel> task = scheduleTask(new Retryable<SocketChannel>(
                    new Callable<SocketChannel>() {
                        @Override
                        public SocketChannel call() throws IOException {

                            InetAddress address = InetAddress.getByName(hostname);
                            log.info("Connecting to " + address);

                            final InetSocketAddress addr = new InetSocketAddress(address, port);
                            final SocketChannel socketChannel = SocketChannel.open();

                            socketChannel.connect(addr);
                            socketChannel.configureBlocking(false);
                            updateLastHeartbeat();

                            return socketChannel;
                        }
                    }, this.policy));
            this.channel = task.get();
            postConnect(getPostConnectCallback());
        } catch (InterruptedException | ExecutionException e) {
            log.error("Exception during connection", e);
            disconnect();
            throw new ClientConnectionException(e);
        }
    }

    public SelectionKey getSelectionKey() {
        return this.key;
    }

    public void addEventListener(MessageListener el) {
        eventListeners.add(el);
    }

    public void removeEventListener(MessageListener el) {
        eventListeners.remove(el);
    }

    protected void emitOnMessageReceived(byte[] message) {
        for (MessageListener el : eventListeners) {
            el.onMessageReceived(message);
        }
    }

    public final void disconnect() {
        postDisconnect();
        closeChannel();
    }

    public Future<Void> close() {
        final Callable<Void> callable = new Callable<Void>() {
            @Override
            public Void call() {
                disconnect();
                return null;
            }
        };
        return scheduleTask(callable);
    }

    protected <T> FutureTask<T> scheduleTask(Callable<T> callable) {
        final FutureTask<T> task = new FutureTask<>(callable);
        reactor.queueFuture(task);
        return task;
    }

    public void process() throws IOException, ClientConnectionException {
        processIncoming();
        processHeartbeat();
        processOutgoing();
    }

    /**
     * Process incoming channel.
     *
     * @throws IOException Thrown when reading issue occurred.
     * @throws ClientConnectionException  Thrown when issues with connection.
     */
    protected abstract void processIncoming() throws IOException, ClientConnectionException;

    private void processHeartbeat() {
        if (!this.isInInit() && this.policy.isHeartbeat() && this.lastHeartbeat +  this.policy.getHeartbeat() < System.currentTimeMillis()) {
            log.debug("Heartbeat exeeded. Closing channel");
            this.closeChannel();
        }
    }

    protected void updateLastHeartbeat() {
        this.lastHeartbeat = System.currentTimeMillis();
    }

    protected void processOutgoing() throws IOException, ClientConnectionException {
        final ByteBuffer buff = outbox.peekLast();

        if (buff == null) {
            return;
        }

        writeBuffer(buff);

        if (!buff.hasRemaining()) {
            outbox.removeLast();
        }
        updateInterestedOps();
    }

    private void closeChannel() {
        try {
            if (this.channel != null) {
                this.channel.close();
            }
        } catch (IOException e) {
            // Ignore
        } finally {
            this.channel = null;
        }
    }

    public boolean isOpen() {
        return channel != null && channel.isOpen();
    }

    protected int readBuffer(ByteBuffer buff) throws IOException, ClientConnectionException {
        if (!isOpen()) {
            connect();
        }
        return read(buff);
    }

    protected void writeBuffer(ByteBuffer buff) throws IOException, ClientConnectionException {
        if (!isOpen()) {
            connect();
        }
        write(buff);
    }

    /**
     * Sends message using provided byte array.
     *
     * @param message - content of the message to sent.
     */
    public abstract void sendMessage(byte[] message);

    /**
     * Reads provided buffer.
     *
     * @param buff
     *            provided buffer to be read.
     * @throws IOException
     *             when networking issue occurs.
     */
    abstract int read(ByteBuffer buff) throws IOException;

    /**
     * Writes provided buffer.
     *
     * @param buff
     *            provided buffer to be written.
     * @throws IOException
     *             when networking issue occurs.
     */
    abstract void write(ByteBuffer buff) throws IOException;

    /**
     * Transport specific post connection functionality.
     *
     * @throws ClientConnectionException
     *             when issues with connection.
     */
    abstract void postConnect(OneTimeCallback callback) throws ClientConnectionException;

    /**
     * Updates selection key's operation set.
     */
    public abstract void updateInterestedOps();

    /**
     * @return Client specific {@link OneTimeCallback} or null. The callback is executed
     * after the connection is established.
     */
    protected abstract OneTimeCallback getPostConnectCallback();

    /**
     * Cleans resources after disconnect.
     */
    public abstract void postDisconnect();

    /**
     * @return <code>true</code> when connection initialization is in progress like
     *         SSL hand shake. <code>false</code> when connection is initialized.
     */
    public abstract boolean isInInit();
}
