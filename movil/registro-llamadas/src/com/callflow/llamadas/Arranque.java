package com.callflow.llamadas;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/** Vuelve a poner en marcha el vigilante de pausas al reiniciar el teléfono y al instalar una versión nueva de la app. */
public class Arranque extends BroadcastReceiver {
    @Override
    public void onReceive(Context ctx, Intent intent) {
        String a = intent.getAction();
        if (Intent.ACTION_BOOT_COMPLETED.equals(a) || Intent.ACTION_MY_PACKAGE_REPLACED.equals(a)) Vigilante.actualizar(ctx);
    }
}
