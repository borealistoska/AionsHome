package com.aion.chat.miband;

import android.content.Context;
import android.content.ContextWrapper;
import android.content.SharedPreferences;

import org.junit.Test;

import java.lang.reflect.Constructor;
import java.lang.reflect.Field;
import java.lang.reflect.Proxy;
import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.TimeUnit;

import static org.junit.Assert.*;

public class MiBandReconnectTest {
    @Test public void serviceRestartAllowsReconnectButPreservesUserDisconnect() throws Exception {
        MiBandRuntime runtime = runtime();
        try {
            runtime.connectSaved();
            drain(runtime);
            assertEquals("waiting_reconnect", runtime.status().state);
            runtime.releaseForServiceStop();
            drain(runtime);
            runtime.autoConnect();
            drain(runtime);
            assertEquals("waiting_reconnect", runtime.status().state);

            runtime.disconnect();
            drain(runtime);
            runtime.releaseForServiceStop();
            drain(runtime);
            runtime.autoConnect();
            drain(runtime);
            assertEquals("disconnected", runtime.status().state);
        } finally {
            runtime.shutdown();
        }
    }

    @Test public void selectingReplacementReleasesTheOldAuthenticatedSession() throws Exception {
        MiBandRuntime runtime = runtime();
        try {
            Field sessionField = MiBandRuntime.class.getDeclaredField("session");
            sessionField.setAccessible(true);
            Object session = sessionField.get(runtime);
            for (String name : new String[]{"connected", "authenticated"}) {
                Field flag = MiBandGattSession.class.getDeclaredField(name);
                flag.setAccessible(true);
                flag.setBoolean(session, true);
            }
            runtime.saveSelectedDevice("AA:BB:CC:DD:EE:FF", "old band", "",
                    MiBandPreferences.Settings.defaults());
            drain(runtime);
            assertTrue("Saving the same device must retain its connection", runtime.status().authenticated);

            runtime.saveSelectedDevice("11:22:33:44:55:66", "new band", "",
                    MiBandPreferences.Settings.defaults());
            drain(runtime);
            assertEquals("11:22:**:**:55:66", runtime.savedDeviceMaskedAddress());
            assertFalse("The old band's session must not masquerade as the replacement",
                    runtime.status().connected);
        } finally {
            runtime.shutdown();
        }
    }

    private static void drain(MiBandRuntime runtime) throws Exception {
        Field field = MiBandRuntime.class.getDeclaredField("operationExecutor");
        field.setAccessible(true);
        ((ExecutorService) field.get(runtime)).submit(() -> {}).get(2, TimeUnit.SECONDS);
    }

    private static MiBandRuntime runtime() throws Exception {
        Map<String, Object> values = new HashMap<>();
        values.put(MiBandPreferences.KEY_ADDRESS, "AA:BB:CC:DD:EE:FF");
        values.put(MiBandPreferences.KEY_AUTH, "00000000000000000000000000000000");
        SharedPreferences.Editor editor = (SharedPreferences.Editor) Proxy.newProxyInstance(
                SharedPreferences.Editor.class.getClassLoader(),
                new Class<?>[]{SharedPreferences.Editor.class}, (proxy, method, args) -> {
                    if (method.getName().startsWith("put")) values.put((String) args[0], args[1]);
                    if (method.getName().equals("remove")) values.remove(args[0]);
                    if (method.getName().equals("apply")) return null;
                    if (method.getName().equals("commit")) return true;
                    return proxy;
                });
        SharedPreferences preferences = (SharedPreferences) Proxy.newProxyInstance(
                SharedPreferences.class.getClassLoader(), new Class<?>[]{SharedPreferences.class},
                (proxy, method, args) -> {
                    if (method.getName().equals("edit")) return editor;
                    if (method.getName().startsWith("get")) return values.getOrDefault(args[0], args[1]);
                    throw new UnsupportedOperationException(method.getName());
                });
        Context context = new ContextWrapper(null) {
            @Override public Context getApplicationContext() { return this; }
            @Override public SharedPreferences getSharedPreferences(String name, int mode) { return preferences; }
            @Override public Object getSystemService(String name) { return null; }
        };
        Constructor<MiBandRuntime> constructor = MiBandRuntime.class.getDeclaredConstructor(Context.class);
        constructor.setAccessible(true);
        return constructor.newInstance(context);
    }
}
