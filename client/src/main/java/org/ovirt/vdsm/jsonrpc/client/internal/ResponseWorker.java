package org.ovirt.vdsm.jsonrpc.client.internal;

import static org.ovirt.vdsm.jsonrpc.client.utils.JsonUtils.UTF8;
import static org.ovirt.vdsm.jsonrpc.client.utils.JsonUtils.logException;

import java.util.Iterator;
import java.util.concurrent.ForkJoinPool;
import java.util.concurrent.ForkJoinWorkerThread;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.ForkJoinPool.ForkJoinWorkerThreadFactory;

import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.codehaus.jackson.JsonNode;
import org.codehaus.jackson.JsonParser;
import org.codehaus.jackson.map.ObjectMapper;
import org.codehaus.jackson.node.NullNode;
import org.ovirt.vdsm.jsonrpc.client.JsonRpcClient;
import org.ovirt.vdsm.jsonrpc.client.JsonRpcEvent;
import org.ovirt.vdsm.jsonrpc.client.JsonRpcResponse;
import org.ovirt.vdsm.jsonrpc.client.events.EventPublisher;
import org.ovirt.vdsm.jsonrpc.client.reactors.ReactorClient;
import org.ovirt.vdsm.jsonrpc.client.reactors.ReactorClient.MessageListener;
import org.ovirt.vdsm.jsonrpc.client.reactors.ReactorFactory;

/**
 * <code>ResponseWorker</code> is responsible to process responses for all the {@link JsonRpcClient} and it is produced
 * by {@link ReactorFactory}.
 *
 */
public final class ResponseWorker extends Thread {
    private final LinkedBlockingQueue<MessageContext> queue;
    private final static ObjectMapper MAPPER = new ObjectMapper();
    private ResponseTracker tracker;
    private EventPublisher publisher;
    private static Log log = LogFactory.getLog(ResponseWorker.class);
    static {
        MAPPER.configure(JsonParser.Feature.INTERN_FIELD_NAMES, false);
        MAPPER.configure(JsonParser.Feature.CANONICALIZE_FIELD_NAMES, false);
    }

    public ResponseWorker() {
        this.queue = new LinkedBlockingQueue<>();
        this.tracker = new ResponseTracker();
        this.publisher =
                new EventPublisher(new ForkJoinPool(Runtime.getRuntime().availableProcessors(),
                        new ForkJoinWorkerThreadFactory() {

                            @Override
                            public ForkJoinWorkerThread newThread(ForkJoinPool pool) {
                                return new ResponseForkJoinWorkerThread(pool);
                            }

                        },
                        null,
                        true));

        Thread trackerThread = new Thread(this.tracker);
        trackerThread.setName("Response tracker");
        trackerThread.setDaemon(true);
        trackerThread.start();

        setName("ResponseWorker");
        setDaemon(true);
        start();
    }

    class ResponseForkJoinWorkerThread extends ForkJoinWorkerThread {

        protected ResponseForkJoinWorkerThread(ForkJoinPool pool) {
            super(pool);
        }
    }

    /**
     * Registers new client with <code>ResponseWorker</code>.
     *
     * @param client
     *            - {@link JsonRpcClient} to be registered.
     * @return Client wrapper.
     */
    public JsonRpcClient register(ReactorClient client) {
        final JsonRpcClient jsonRpcClient = new JsonRpcClient(client, this.tracker);
        client.addEventListener(new MessageListener() {

            @Override
            public void onMessageReceived(byte[] message) {
                queue.add(new MessageContext(jsonRpcClient, message));
            }
        });
        return jsonRpcClient;
    }

    public void run() {
        MessageContext context = null;
        while (true) {
            try {
                context = this.queue.take();
                if (context.getClient() == null) {
                    break;
                }
                if (log.isDebugEnabled()) {
                    log.debug("Message received: " + new String(context.getMessage(), UTF8));
                }
                JsonNode rootNode = MAPPER.readTree(context.getMessage());
                if (!rootNode.isArray()) {
                    processIncomingObject(context.getClient(), rootNode);
                } else {
                    final Iterator<JsonNode> iter = rootNode.getElements();
                    while (iter.hasNext()) {
                        final JsonNode node = iter.next();
                        processIncomingObject(context.getClient(), node);
                    }
                }
            } catch (Exception e) {
                log.warn("Exception thrown during message processing");
                if (log.isDebugEnabled()) {
                    log.debug(e.getMessage(), e);
                }
                continue;
            }
        }
    }

    private void processIncomingObject(JsonRpcClient client, JsonNode node) {
        final JsonNode id = node.get("id");
        final JsonNode error = node.get("error");
        if ((id == null || NullNode.class.isInstance(id)) && (error == null || NullNode.class.isInstance(error))) {
            JsonRpcEvent event = JsonRpcEvent.fromJsonNode(node);
            String method = client.getHostname() + event.getMethod();
            event.setMethod(method);
            processNotifications(event);
            return;
        }
        try {
            client.processResponse(JsonRpcResponse.fromJsonNode(node));
        } catch (IllegalArgumentException e) {
            logException(log, "Recieved response is not correct", e);
            return;
        }
    }

    private void processNotifications(JsonRpcEvent notification) {
        this.publisher.process(notification);
    }

    public void close() {
        this.queue.add(new MessageContext(null, null));
        this.tracker.close();
    }

    /**
     * @return publisher which can be used to subscribe to events defined by subscription id.
     */
    public EventPublisher getPublisher() {
        return publisher;
    }

}
