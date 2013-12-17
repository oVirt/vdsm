package org.ovirt.vdsm.jsonrpc.client;

import java.io.IOException;
import java.lang.reflect.Array;
import java.util.HashMap;
import java.util.Map;

import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.codehaus.jackson.map.DeserializationConfig;
import org.codehaus.jackson.map.ObjectMapper;
import org.codehaus.jackson.type.TypeReference;
import org.ovirt.vdsm.jsonrpc.client.internal.JsonRpcError;

/**
 * Decomposes of the response as objects which type is provided.
 *
 */
public class ResponseDecomposer {
    private static Log log = LogFactory.getLog(ResponseDecomposer.class);
    private static ObjectMapper mapper = new ObjectMapper();
    private JsonRpcResponse response;

    /**
     * Creates decomposer for a response.
     * @param response - Used for decomposition.
     */
    public ResponseDecomposer(JsonRpcResponse response) {
        this.response = response;
        mapper.configure(
                DeserializationConfig.Feature.USE_JAVA_ARRAY_FOR_JSON_ARRAY,
                true);
    }

    /**
     * @return <code>true</code> when response contains {@link JsonRpcError} object
     *         otherwise <code>false</code>
     */
    public boolean isError() {
        return this.response.getError() != null;
    }

    /**
     * Decomposes response to provided type.
     * @param clazz - Type of the object to which response will be decomposed.
     * @return Decomposed response of provided type.
     */
    @SuppressWarnings("unchecked")
    public <T> T decomposeResponse(Class<T> clazz) {
        try {
            T t = mapper.readValue(this.response.getResult(),
                    new TypeReference<T>() {
                    });
            if (String.class.equals(clazz) && !String.class.isInstance(t)) {
                t = (T) t.toString();
            }
            return t;
        } catch (IOException e) {
            log.error("Response decomposition failed", e);
            return null;
        }
    }

    /**
     * Decomposes response to provided typed array.
     * @param array - An array of objects to be decomposed.
     * @param clazz - Type of objects in array to which response are decomposed.
     * @param subtypeKey - The key which is used to store objects in decomposed map.
     * @return Decomposed response of provided type.
     */
    @SuppressWarnings({ "unchecked", "rawtypes" })
    public <T> T[] decomposeTypedArray(Object[] array, Class<T> clazz, String subtypeKey) {
        T[] result = (T[]) Array.newInstance(clazz, array.length);
        for (int i = 0; i < array.length; i++) {
            if (Map.class.isAssignableFrom(clazz)) {
                Map map = new HashMap<>();
                map.put(subtypeKey, array[i]);
                result[i] = (T) map;
            } else { 
                result[i] = (T) array;
            }
        }
        return result;
    }

    /**
     * Decomposes response error as <code>Map</code>.
     * @return Decomposed response error.
     */
    public Map<String, Object> decomposeError() {
        try {
            Map<String, Object> status = mapper.readValue(this.response.getError(),
                    new TypeReference<HashMap<String, Object>>() {
                    });
            Map<String, Object> map = new HashMap<>();
            map.put("status", status);
            return map;
        } catch (IOException e) {
            log.error("Response decomposition failed", e);
            return new HashMap<String, Object>();
        }
    }
}
