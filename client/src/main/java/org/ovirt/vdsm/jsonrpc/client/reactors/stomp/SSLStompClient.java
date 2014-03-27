package org.ovirt.vdsm.jsonrpc.client.reactors.stomp;

import static org.ovirt.vdsm.jsonrpc.client.reactors.stomp.StompClient.REQUEST_QUEUE;
import static org.ovirt.vdsm.jsonrpc.client.reactors.stomp.StompClient.RESPONSE_QUEUE;
import static org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message.HEADER_ACCEPT;
import static org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message.HEADER_ACK;
import static org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message.HEADER_DESTINATION;
import static org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message.HEADER_ID;
import static org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message.HEADER_MESSAGE;
import static org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message.HEADER_RECEIPT;
import static org.ovirt.vdsm.jsonrpc.client.utils.JsonUtils.UTF8;
import static org.ovirt.vdsm.jsonrpc.client.utils.JsonUtils.isEmpty;

import java.nio.ByteBuffer;
import java.nio.channels.Selector;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.Callable;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Future;

import javax.net.ssl.SSLContext;

import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.ovirt.vdsm.jsonrpc.client.ClientConnectionException;
import org.ovirt.vdsm.jsonrpc.client.reactors.Reactor;
import org.ovirt.vdsm.jsonrpc.client.reactors.ReactorClient;
import org.ovirt.vdsm.jsonrpc.client.reactors.SSLClient;
import org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message;
import org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message.Command;
import org.ovirt.vdsm.jsonrpc.client.utils.OneTimeCallback;

public class SSLStompClient extends SSLClient {
    private static final Log LOG = LogFactory.getLog(SSLStompClient.class);
    private CountDownLatch connected = new CountDownLatch(1);
    private CountDownLatch subscribed = new CountDownLatch(1);
    private String subscribtionId;

    private OneTimeCallback callback = new OneTimeCallback() {

        @Override
        public void execute() {
            if (connected.getCount() == 0) {
                connected = new CountDownLatch(1);
            }
            if (subscribed.getCount() == 0) {
                subscribed = new CountDownLatch(1);
            }

            send(new Message().connect().withHeader(HEADER_ACCEPT, "1.2").build());

            subscribtionId = UUID.randomUUID().toString();
            send(new Message().subscribe().withHeader(HEADER_DESTINATION, RESPONSE_QUEUE)
                    .withHeader(HEADER_ID, subscribtionId).withHeader(HEADER_ACK, "client").build());
        }

    };

    @Override
    public void sendMessage(byte[] message) {
        waitForConnect();

        send(new Message().send()
                .withHeader(HEADER_DESTINATION, REQUEST_QUEUE)
                .withContent(new String(message,UTF8))
                .build());
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

    @Override
    protected void emitOnMessageReceived(byte[] message) {
        List<Message> messages =
                Message.buildMessages(new String(message, UTF8));
        for (Message msg : messages) {
            processMessage(msg);
        }
    }

    void processMessage(Message message) {
        if (Command.CONNECTED.toString().equals(message.getCommand())) {
            this.connected.countDown();
        } else if (Command.ACK.toString().equals(message.getCommand())) {
            String headerId = message.getHeaders().get(HEADER_ID);
            if (!isEmpty(headerId)) {
                this.subscribed.countDown();
            }
        } else if (Command.ERROR.toString().equals(message.getCommand())) {
            String errorMessage = message.getHeaders().get(HEADER_MESSAGE);
            StringBuilder error = new StringBuilder();
            if (!isEmpty(errorMessage)){
                error.append(errorMessage);
            }
            if (!isEmpty(message.getContent())) {
                error.append(errorMessage);
            }
            LOG.error("Error Message recieved: " + error);
        } else if (Command.MESSAGE.toString().equals(message.getCommand())) {
            super.emitOnMessageReceived(message.getContent().getBytes(UTF8));
        }
    }

    public SSLStompClient(Reactor reactor, Selector selector, String hostname, int port, SSLContext sslContext)
            throws ClientConnectionException {
        super(reactor, selector, hostname, port, sslContext);
    }

    @Override
    protected void postConnect(OneTimeCallback callback) throws ClientConnectionException {
        super.postConnect(this.callback);
    }

    private void waitForConnect() {
        try {
            this.connected.await(policy.getRetryTimeOut(), policy.getTimeUnit());
            // TODO wait for the mini broker to be finished
            // this.subscribed.await();
        } catch (InterruptedException e) {
            throw new IllegalStateException("Communication interrupted");
        }
    }

    @Override
    public Future<Void> close() {
        send(new Message().unsubscribe().withHeader(HEADER_ID, this.subscribtionId).build());
        send(new Message().disconnect().withHeader(HEADER_RECEIPT, UUID.randomUUID().toString()).build());
        return super.close();
    }
}
