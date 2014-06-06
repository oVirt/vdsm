package org.ovirt.vdsm.jsonrpc.client.reactors.stomp;

import static org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message.HEADER_ACCEPT;
import static org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message.HEADER_ACK;
import static org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message.HEADER_DESTINATION;
import static org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message.HEADER_ID;

import java.nio.channels.Selector;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;

import javax.net.ssl.SSLContext;

import org.ovirt.vdsm.jsonrpc.client.ClientConnectionException;
import org.ovirt.vdsm.jsonrpc.client.reactors.Reactor;
import org.ovirt.vdsm.jsonrpc.client.reactors.SSLClient;
import org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message;
import org.ovirt.vdsm.jsonrpc.client.utils.OneTimeCallback;

public class SSLStompClient extends SSLClient {

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

    public SSLStompClient(Reactor reactor, Selector selector, String hostname, int port, SSLContext sslContext)
            throws ClientConnectionException {
        super(reactor, selector, hostname, port, sslContext);
        this.connected = new CountDownLatch(1);
        this.subscribed = new CountDownLatch(1);
    }

    @Override
    public void sendMessage(byte[] message) {
        waitForConnect();

        send(new Message().send()
                .withHeader(HEADER_DESTINATION, REQUEST_QUEUE)
                .withContent(message)
                .build());
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
}
