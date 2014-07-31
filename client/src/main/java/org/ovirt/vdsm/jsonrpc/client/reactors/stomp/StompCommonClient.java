package org.ovirt.vdsm.jsonrpc.client.reactors.stomp;

import static org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message.HEADER_ID;
import static org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message.HEADER_MESSAGE;
import static org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message.HEADER_RECEIPT;
import static org.ovirt.vdsm.jsonrpc.client.utils.JsonUtils.UTF8;
import static org.ovirt.vdsm.jsonrpc.client.utils.JsonUtils.isEmpty;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.util.Arrays;
import java.util.UUID;
import java.util.concurrent.Callable;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Future;

import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.ovirt.vdsm.jsonrpc.client.ClientConnectionException;
import org.ovirt.vdsm.jsonrpc.client.reactors.Reactor;
import org.ovirt.vdsm.jsonrpc.client.reactors.ReactorClient;
import org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message;
import org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message.Command;

public abstract class StompCommonClient extends ReactorClient {
    public final static String REQUEST_QUEUE = "/queue/_local/vdsm/requests";
    public final static String RESPONSE_QUEUE = "/queue/_local/vdsm/reponses";
    protected ByteBuffer headerBuffer = ByteBuffer.allocate(BUFFER_SIZE);
    protected Message message;
    protected CountDownLatch connected;
    protected CountDownLatch subscribed;
    protected String subscribtionId;
    private static final Log LOG = LogFactory.getLog(StompCommonClient.class);

    public StompCommonClient(Reactor reactor, String hostname, int port) {
        super(reactor, hostname, port);
    }

    public void send(byte[] message) {
        outbox.addFirst(ByteBuffer.wrap(message));
        LOG.info("Message sent: " + new String(message, UTF8));

        final ReactorClient client = this;
        scheduleTask(new Callable<Void>() {
            @Override
            public Void call() throws ClientConnectionException {
                client.updateInterestedOps();
                return null;
            }
        });
    }

    void processMessage(Message message) {
        if (Command.CONNECTED.toString().equals(message.getCommand())) {
            // TODO add heart beat interval handling
            this.connected.countDown();
        } else if (Command.ACK.toString().equals(message.getCommand())) {
            String headerId = message.getHeaders().get(HEADER_ID);
            if (!isEmpty(headerId)) {
                this.subscribed.countDown();
            }
        } else if (Command.ERROR.toString().equals(message.getCommand())) {
            String errorMessage = message.getHeaders().get(HEADER_MESSAGE);
            StringBuilder error = new StringBuilder();
            if (!isEmpty(errorMessage)) {
                error.append(errorMessage);
            }
            if (message.getContent().length == 0) {
                error.append(errorMessage);
            }
            LOG.error("Error Message recieved: " + error);
        } else if (Command.MESSAGE.toString().equals(message.getCommand())) {
            super.emitOnMessageReceived(message.getContent());
        }
    }

    @Override
    public Future<Void> close() {
        send(new Message().unsubscribe().withHeader(HEADER_ID, this.subscribtionId).build());
        send(new Message().disconnect().withHeader(HEADER_RECEIPT, UUID.randomUUID().toString()).build());
        return super.close();
    }

    @Override
    protected void processIncoming() throws IOException, ClientConnectionException {
        if (this.ibuff == null) {
            int read = readBuffer(headerBuffer);
            if (read <= 0) {
                return;
            }
            updateLastHeartbeat();

            this.message = getMessage(headerBuffer, read);
            if (this.message == null) {
                clean();
                return;
            }
            int contentLength = this.message.getContentLength();
            if (contentLength == -1) {
                // only for control messages, all other have the header
                emitOnMessageReceived(this.message);
                return;
            }
            int length = this.message.getContent().length;
            if (contentLength == length - 1) {
                emitOnMessageReceived(this.message);
                return;
            } else if (contentLength > length) {
                this.ibuff = ByteBuffer.allocate(contentLength - length + 1);
            } else {
                byte[] content = this.message.getContent();
                this.message.withContent(Arrays.copyOfRange(content, 0, contentLength));
                emitOnMessageReceived(this.message);
                headerBuffer.put(Arrays.copyOfRange(content, contentLength, content.length));
            }
        }

        readBuffer(this.ibuff);
        int length = this.message.getContent().length + this.ibuff.position();
        if (this.message.getContentLength() != length - 1) {
            return;
        }
        this.message.withAdditionalContent(this.ibuff.array());
        emitOnMessageReceived(this.message);
    }

    private Message getMessage(ByteBuffer buffer, int read) {
        if (read > BUFFER_SIZE) {
            read = BUFFER_SIZE;
        }
        byte[] array = new byte[read];
        buffer.rewind();
        buffer.get(array);
        return Message.parse(array);
    }

    private void clean() {
        headerBuffer = ByteBuffer.allocate(BUFFER_SIZE);
        this.ibuff = null;
        this.message = null;
    }

    protected void emitOnMessageReceived(Message message) {
        message.trimEndOfMessage();
        clean();
        processMessage(message);
    }

}
