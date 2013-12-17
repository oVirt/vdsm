package org.ovirt.vdsm.jsonrpc.client;

import org.codehaus.jackson.JsonNode;

/**
 * Java bean representing response object.
 *
 */
public final class JsonRpcResponse {
    private JsonNode result;
    private JsonNode error;
    private JsonNode id;

    /**
     * Creates response object.
     * @param result - {@link JsonNode} containing response message.
     * @param error - {@link JsonNode} containing error code and message.
     * @param id - Unique identifier of the message which is exactly the same
     *               as in request.
     */
    public JsonRpcResponse(JsonNode result, JsonNode error, JsonNode id) {
        this.result = result;
        this.error = error;
        this.id = id;
    }

    public JsonNode getResult() {
        return this.result;
    }

    public void setResult(JsonNode result) {
        this.result = result;
    }

    public JsonNode getError() {
        return error;
    }

    public void setError(JsonNode error) {
        this.error = error;
    }

    public JsonNode getId() {
        return id;
    }

    public void setId(JsonNode node) {
        this.id = node;
    }

    /**
     * Validates and builds {@link JsonRpcResponse} based on provided json node.
     * @param node - Json representation of the response.
     * @return Response object.
     */
    public static JsonRpcResponse fromJsonNode(JsonNode node) {
        JsonNode jsonrpcNode = node.get("jsonrpc");
        if (jsonrpcNode == null) {
            throw new IllegalArgumentException(
                    "'jsonrpc' field missing in node");
        }

        String version = jsonrpcNode.asText();
        if (version == null || !version.equals("2.0")) {
            throw new IllegalArgumentException("Only jsonrpc 2.0 is supported");
        }

        final JsonNode id = node.get("id");
        if (id == null) {
            throw new IllegalArgumentException("Response missing id field");
        }

        return new JsonRpcResponse(node.get("result"), node.get("error"), id);
    }
}
