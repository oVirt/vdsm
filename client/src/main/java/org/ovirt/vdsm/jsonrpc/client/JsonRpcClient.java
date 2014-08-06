package org.ovirt.vdsm.jsonrpc.client;

import static org.ovirt.vdsm.jsonrpc.client.utils.JsonUtils.getTimeout;
import static org.ovirt.vdsm.jsonrpc.client.utils.JsonUtils.jsonToByteArray;

import java.util.List;
import java.util.concurrent.Future;

import org.ovirt.vdsm.jsonrpc.client.internal.BatchCall;
import org.ovirt.vdsm.jsonrpc.client.internal.Call;
import org.ovirt.vdsm.jsonrpc.client.internal.JsonRpcCall;
import org.ovirt.vdsm.jsonrpc.client.internal.ResponseTracker;
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
    private final ReactorClient client;
    private RetryPolicy policy = new DefaultClientRetryPolicy();
    private ResponseTracker tracker;

    /**
     * Wraps {@link ReactorClient} to hide response update details.
     *
     * @param client - used communicate.
     */
    public JsonRpcClient(ReactorClient client, ResponseTracker tracker) {
        this.client = client;
        this.tracker = tracker;
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
        this.tracker.registerCall(req, call);
        this.getClient().sendMessage(jsonToByteArray(req.toJson()));
        retryCall(req, call);
        return call;
    }

    private void retryCall(final JsonRpcRequest request, final JsonRpcCall call) throws ClientConnectionException {
        ResponseTracking tracking =
                new ResponseTracking(request, call, new RetryContext(policy), getTimeout(this.policy.getRetryTimeOut(),
                        this.policy.getTimeUnit()), client);
        this.tracker.registerTrackingRequest(request, tracking);
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
            this.tracker.registerCall(request, call);
        }
        this.getClient().sendMessage(jsonToByteArray(requests));
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
        return this.client;
    }

    public void processResponse(JsonRpcResponse response) {
        JsonRpcCall call = this.tracker.removeCall(response.getId());
        if (call == null) {
            return;
        }
        call.addResponse(response);
    }

    public void close() {
        this.client.close();
    }

    public boolean isClosed() {
        return client.isOpen();
    }
}
