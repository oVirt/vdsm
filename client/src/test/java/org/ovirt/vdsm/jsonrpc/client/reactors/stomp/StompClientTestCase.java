package org.ovirt.vdsm.jsonrpc.client.reactors.stomp;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotNull;
import static org.ovirt.vdsm.jsonrpc.client.utils.JsonUtils.UTF8;

import java.io.IOException;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;

import org.junit.After;
import org.junit.Before;
import org.junit.Test;
import org.ovirt.vdsm.jsonrpc.client.ClientConnectionException;
import org.ovirt.vdsm.jsonrpc.client.reactors.ReactorClient;
import org.ovirt.vdsm.jsonrpc.client.reactors.ReactorClient.MessageListener;
import org.ovirt.vdsm.jsonrpc.client.reactors.ReactorListener;
import org.ovirt.vdsm.jsonrpc.client.reactors.ReactorListener.EventListener;

public class StompClientTestCase {
    private final static int TIMEOUT_SEC = 6;
    private final static String HOSTNAME = "localhost";
    private StompReactor listeningReactor;
    private StompReactor sendingReactor;

    @Before
    public void setUp() throws IOException {
        this.listeningReactor = new StompReactor();
        this.sendingReactor = new StompReactor();
    }

    @After
    public void tearDown() throws IOException {
        this.sendingReactor.close();
        this.listeningReactor.close();
    }

    @Test
    public void testHelloWrold() throws InterruptedException, ExecutionException, ClientConnectionException {
        testEchoMessage("Hello world!", 61626);
    }

    @Test
    public void testLongMessage() throws InterruptedException, ExecutionException, ClientConnectionException {
        String message =
                "Python is an easy to learn, powerful programming language. It has efficient high-level data structures and a simple but effective approach to object-oriented programming. Python’s elegant syntax and dynamic typing, together with its interpreted nature, make it an ideal language for scripting and rapid application development in many areas on most platforms."
                        + "The Python interpreter and the extensive standard library are freely available in source or binary form for all major platforms from the Python Web site, http://www.python.org/, and may be freely distributed. The same site also contains distributions of and pointers to many free third party Python modules, programs and tools, and additional documentation."
                        + "The Python interpreter is easily extended with new functions and data types implemented in C or C++ (or other languages callable from C). Python is also suitable as an extension language for customizable applications."
                        + "This tutorial introduces the reader informally to the basic concepts and features of the Python language and system. It helps to have a Python interpreter handy for hands-on experience, but all examples are self-contained, so the tutorial can be read off-line as well."
                        + "For a description of standard objects and modules, see The Python Standard Library. The Python Language Reference gives a more formal definition of the language. To write extensions in C or C++, read Extending and Embedding the Python Interpreter and Python/C API Reference Manual. There are also several books covering Python in depth."
                        + "This tutorial does not attempt to be comprehensive and cover every single feature, or even every commonly used feature. Instead, it introduces many of Python’s most noteworthy features, and will give you a good idea of the language’s flavor and style. After reading it, you will be able to read and write Python modules and programs, and you will be ready to learn more about the various Python library modules described in The Python Standard Library.";
        testEchoMessage(message, 61627);
    }

    private void testEchoMessage(String message, int port) throws ClientConnectionException, InterruptedException,
            ExecutionException {
        final BlockingQueue<byte[]> queue = new ArrayBlockingQueue<>(1);
        Future<ReactorListener> futureListener =
                this.listeningReactor.createListener(HOSTNAME, port, new EventListener() {

                    @Override
                    public void onAcccept(final ReactorClient client) {
                        client.addEventListener(new MessageListener() {
                            @Override
                            public void onMessageReceived(byte[] message) {
                                client.sendMessage(message);
                            }
                        });
                    }
                });

        ReactorListener listener = futureListener.get();
        assertNotNull(listener);

        ReactorClient client = this.sendingReactor.createClient(HOSTNAME, port);
        client.addEventListener(new ReactorClient.MessageListener() {

            @Override
            public void onMessageReceived(byte[] message) {
                queue.add(message);
            }
        });
        client.connect();

        client.sendMessage(message.getBytes(UTF8));
        byte[] response = queue.poll(TIMEOUT_SEC, TimeUnit.SECONDS);

        client.close();
        listener.close();

        assertNotNull(response);
        assertEquals(message, new String(response, UTF8));
    }
}
