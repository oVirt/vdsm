package org.ovirt.vdsm.jsonrpc.client.reactors.stomp;

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

import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.ovirt.vdsm.jsonrpc.client.ClientConnectionException;
import org.ovirt.vdsm.jsonrpc.client.reactors.PlainClient;
import org.ovirt.vdsm.jsonrpc.client.reactors.Reactor;
import org.ovirt.vdsm.jsonrpc.client.reactors.ReactorClient;
import org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message;
import org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message.Command;
import org.ovirt.vdsm.jsonrpc.client.utils.OneTimeCallback;

public class StompClient extends PlainClient {
    private static final Log LOG = LogFactory.getLog(StompClient.class);
    public final static String REQUEST_QUEUE = "/queue/_local/vdsm/requests";
    public final static String RESPONSE_QUEUE = "/queue/_local/vdsm/reponses";

    private CountDownLatch connected;
    private CountDownLatch subscribed;

    private String subscribtionId;

    public StompClient(Reactor reactor, Selector selector, String hostname, int port)
            throws ClientConnectionException {
        super(reactor, selector, hostname, port);
    }

    @Override
    public void sendMessage(byte[] message) {
        send(new Message().send()
                .withHeader(HEADER_DESTINATION, REQUEST_QUEUE)
                .withContent(new String(message, UTF8))
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

    @Override
    protected void postConnect(OneTimeCallback callback) throws ClientConnectionException {
        super.postConnect(callback);

        try {
            this.connected = new CountDownLatch(1);
            this.subscribed = new CountDownLatch(1);

            send(new Message().connect().withHeader(HEADER_ACCEPT, "1.2").build());
            this.connected.await(policy.getRetryTimeOut(), policy.getTimeUnit());
            this.subscribtionId = UUID.randomUUID().toString();
            send(new Message().subscribe().withHeader(HEADER_DESTINATION, RESPONSE_QUEUE)
                    .withHeader(HEADER_ID, this.subscribtionId).withHeader(HEADER_ACK, "client").build());
            // TODO wait for the mini broker to be finished
            // this.subscribed.await();
        } catch (InterruptedException e) {
            throw new ClientConnectionException("Timeout during connection", e);
        }
    }

    @Override
    public Future<Void> close() {
        send(new Message().unsubscribe().withHeader(HEADER_ID, this.subscribtionId).build());
        send(new Message().disconnect().withHeader(HEADER_RECEIPT, UUID.randomUUID().toString()).build());
        return super.close();
    }
}
