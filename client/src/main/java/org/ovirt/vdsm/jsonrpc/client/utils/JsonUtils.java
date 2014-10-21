package org.ovirt.vdsm.jsonrpc.client.utils;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.charset.Charset;
import java.util.Date;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.TimeUnit;

import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.codehaus.jackson.JsonEncoding;
import org.codehaus.jackson.JsonFactory;
import org.codehaus.jackson.JsonGenerator;
import org.codehaus.jackson.JsonNode;
import org.codehaus.jackson.map.DeserializationConfig;
import org.codehaus.jackson.map.ObjectMapper;
import org.codehaus.jackson.type.TypeReference;
import org.ovirt.vdsm.jsonrpc.client.JsonRpcRequest;
import org.ovirt.vdsm.jsonrpc.client.JsonRpcResponse;
import org.ovirt.vdsm.jsonrpc.client.ResponseBuilder;

/**
 * Utility class for json marshalling.
 *
 */
public class JsonUtils {
    public static final Charset UTF8 = Charset.forName("UTF-8");
    private static Log log = LogFactory.getLog(JsonUtils.class);
    private static ObjectMapper mapper = new ObjectMapper();
    private static JsonFactory factory = mapper.getJsonFactory();
    static {
        mapper.configure(
                DeserializationConfig.Feature.USE_JAVA_ARRAY_FOR_JSON_ARRAY,
                true);
    }

    public static Map<String, Object> mapValues(JsonNode node) {
        Map<String, Object> map = null;
        try {
            map = mapper.readValue(node,
                    new TypeReference<HashMap<String, Object>>() {
                    });
        } catch (IOException e) {
            log.debug("Exception thrown during marshalling json", e);
        }
        return map;
    }

    public static byte[] jsonToByteArray(JsonNode json) {
        final ByteArrayOutputStream os = new ByteArrayOutputStream();
        try {
            try (JsonGenerator gen = factory.createJsonGenerator(os, JsonEncoding.UTF8)) {
                gen.writeTree(json);
            }
        } catch (IOException e) {
            log.debug("Exception thrown during marshalling json", e);
        }
        return os.toByteArray();
    }

    public static byte[] jsonToByteArray(List<JsonRpcRequest> requests) {
        final ByteArrayOutputStream os = new ByteArrayOutputStream();
        try {
            try (JsonGenerator gen = factory.createJsonGenerator(os, JsonEncoding.UTF8)) {
                gen.writeStartArray();
                for (final JsonRpcRequest request : requests) {
                    gen.writeTree(request.toJson());
                }
                gen.writeEndArray();
            }
        } catch (IOException e) {
            log.debug("Exception thrown during marshalling json", e);
        }
        return os.toByteArray();
    }

    public static <T> JsonRpcResponse buildErrorResponse(JsonNode id, T code, String message) {
        Map<String, Object> error = new HashMap<>();
        error.put("code", code);
        error.put("message", message);
        return new ResponseBuilder(id).withError(error).build();
    }

    public static JsonRpcResponse buildFailedResponse(JsonRpcRequest request) {
        return buildErrorResponse(request.getId(),
                5022,
                "Message timeout which can be caused by communication issues");
    }

    public static String getAddress(String host, int port) {
        return host + ":" + port;
    }

    public static ByteBuffer cloneBuffer(ByteBuffer original) {
        int pos = original.position();
        original.clear();
        ByteBuffer clone = ByteBuffer.allocate(original.capacity());
        clone.put(original);
        clone.flip();
        clone.position(pos);
        return clone;
    }

    public static boolean isEmpty(String value) {
        return value == null || "".equals(value.trim());
    }

    public static int reduceGracePeriod(int interval) {
        return interval - (int) (interval * 0.1);
    }

    public static long getTimeout(int timeout, TimeUnit unit) {
        return new Date().getTime() + TimeUnit.MILLISECONDS.convert(timeout, unit);
    }

    public static void logException(Log logger, String message, Throwable throwable) {
        if (logger.isDebugEnabled()) {
            logger.debug(message, throwable);
        } else {
            logger.error(message);
        }
    }
}
