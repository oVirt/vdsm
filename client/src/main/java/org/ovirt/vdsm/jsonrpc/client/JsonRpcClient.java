package org.ovirt.vdsm.jsonrpc.client;

import static org.ovirt.vdsm.jsonrpc.client.utils.JsonUtils.buildFailedResponse;
import static org.ovirt.vdsm.jsonrpc.client.utils.JsonUtils.jsonToByteArray;

import java.util.Date;
import java.util.List;
import java.util.Queue;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ConcurrentLinkedQueue;
import java.util.concurrent.ConcurrentMap;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;

import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.codehaus.jackson.JsonNode;
import org.codehaus.jackson.map.ObjectMapper;
import org.ovirt.vdsm.jsonrpc.client.internal.BatchCall;
import org.ovirt.vdsm.jsonrpc.client.internal.Call;
import org.ovirt.vdsm.jsonrpc.client.internal.JsonRpcCall;
import org.ovirt.vdsm.jsonrpc.client.reactors.ReactorClient;
import org.ovirt.vdsm.jsonrpc.client.utils.ResponseTracking;
import org.ovirt.vdsm.jsonrpc.client.utils.retry.DefaultClientRetryPolicy;
import org.ovirt.vdsm.jsonrpc.client.utils.retry.RetryContext;
import org.ovirt.vdsm.jsonrpc.client.utils.retry.RetryPolicy;

/**
 * {@link ReactorClient} wrapper which provides ability to send single or batched requests.
 *
 * Each send operation is represented by {@link Call} future which is updated when response arrives.
 *
 */
public class JsonRpcClient {
    private static final int TRACKING_TIMEOUT = 1000;
    private static Log log = LogFactory.getLog(JsonRpcClient.class);
    private final ReactorClient client;
    private final ObjectMapper objectMapper;
    private final ConcurrentMap<JsonNode, JsonRpcCall> runningCalls;
    private RetryPolicy policy = new DefaultClientRetryPolicy();
    private ConcurrentMap<JsonNode, ResponseTracking> map = new ConcurrentHashMap<>();
    private Queue<JsonNode> queue = new ConcurrentLinkedQueue<>();
    private boolean isTracking;

    /**
     * Wraps {@link ReactorClient} to hide response update details.
     *
     * @param client - used communicate.
     */
    public JsonRpcClient(ReactorClient client) {
        this.client = client;
        this.objectMapper = new ObjectMapper();
        this.runningCalls = new ConcurrentHashMap<>();
        this.isTracking = true;
    }

    public void setRetryPolicy(RetryPolicy policy) {
        this.policy = policy;
    }

    /**
     * Sends single request and returns {@link Future} representation of {@link JsonRpcResponse}.
     *
     * @param req - Request which is about to be sent.
     * @return Future representation of the response or <code>null</code> if sending failed.
     * @throws ClientConnectionException
     *             is thrown when connection issues occur.
     * @throws {@link RequestAlreadySentException} when the same requests is attempted to be send twice.
     */
    public Future<JsonRpcResponse> call(JsonRpcRequest req) throws ClientConnectionException {
        final Call call = new Call(req);
        if (this.runningCalls.putIfAbsent(req.getId(), call) != null) {
            throw new RequestAlreadySentException();
        }
        this.getClient().sendMessage(jsonToByteArray(req.toJson(), objectMapper));
        retryCall(req, call);
        return call;
    }

    private void retryCall(final JsonRpcRequest request, final JsonRpcCall call) throws ClientConnectionException {
        ResponseTracking tracking =
                new ResponseTracking(request, call, new RetryContext(policy), getTimeout(this.policy.getRetryTimeOut(),
                        this.policy.getTimeUnit()));
        this.map.put(request.getId(), tracking);
        this.queue.add(request.getId());
    }

    private long getTimeout(int timeout, TimeUnit unit) {
        return new Date().getTime() + TimeUnit.MILLISECONDS.convert(timeout, unit);
    }

    /**
     * Sends requests in batch and returns {@link Future} representation of {@link JsonRpcResponse}.
     *
     * @param requests - <code>List</code> of requests to be sent.
     * @return Future representation of the responses or <code>null</code> if sending failed.
     * @throws ClientConnectionException
     *             is thrown when connection issues occur.
     * @throws {@link RequestAlreadySentException} when the same requests is attempted to be send twice.
     */
    public Future<List<JsonRpcResponse>> batchCall(List<JsonRpcRequest> requests) throws ClientConnectionException {
        final BatchCall call = new BatchCall(requests);
        for (final JsonRpcRequest request : requests) {
            if (this.runningCalls.putIfAbsent(request.getId(), call) != null) {
                throw new RequestAlreadySentException();
            }
        }
        this.getClient().sendMessage(jsonToByteArray(requests, objectMapper));
        retryBatchCall(requests, call);
        return call;
    }

    private void retryBatchCall(final List<JsonRpcRequest> requests, final BatchCall call)
            throws ClientConnectionException {
        for (JsonRpcRequest request : requests) {
            retryCall(request, call);
        }
    }

    private ReactorClient getClient() throws ClientConnectionException {
        if (this.client.isOpen()) {
            return this.client;
        }
        this.client.connect();
        ResponseTracker tracker = new ResponseTracker();
        tracker.start();
        return this.client;
    }

    public void processResponse(JsonRpcResponse response) {
        JsonRpcCall call = this.runningCalls.remove(response.getId());
        if (call == null) {
            return;
        }
        call.addResponse(response);
    }

    public void close() {
        this.client.close();
        this.isTracking = false;
    }

    public boolean isClosed() {
        return client.isOpen();
    }

    private class ResponseTracker extends Thread {
        public ResponseTracker() {
            setName("Response tracker for " + client.getHostname());
        }

        private void removeRequestFromTracking(JsonNode id) {
            queue.remove(id);
            map.remove(id);
        }

        @Override
        public void run() {
            try {
                while (isTracking) {
                    TimeUnit.MILLISECONDS.sleep(TRACKING_TIMEOUT);
                    for (JsonNode id : queue) {
                        if (!runningCalls.containsKey(id)) {
                            removeRequestFromTracking(id);
                            continue;
                        }
                        ResponseTracking tracking = map.get(id);
                        if (System.currentTimeMillis() >= tracking.getTimeout()) {
                            RetryContext context = tracking.getContext();
                            context.decreaseAttempts();
                            if (context.getNumberOfAttempts() <= 0) {
                                runningCalls.remove(id);
                                removeRequestFromTracking(id);
                                tracking.getCall().addResponse(buildFailedResponse(tracking.getRequest()));
                                continue;
                            }
                            try {
                                getClient().sendMessage(jsonToByteArray(tracking.getRequest().toJson(), objectMapper));
                                tracking.setTimeout(getTimeout(context.getTimeout(), context.getTimeUnit()));
                            } catch (ClientConnectionException e) {
                                log.error("Retry failed", e);
                                runningCalls.remove(id);
                                removeRequestFromTracking(id);
                                tracking.getCall().addResponse(buildFailedResponse(tracking.getRequest()));
                            }
                        }
                    }
                }
            } catch (InterruptedException e) {
                log.warn("Tracker thread intrreupted");
            }
        }
    }
}
