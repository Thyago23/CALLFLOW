package com.callflow.llamadas;

import android.app.job.JobInfo;
import android.app.job.JobParameters;
import android.app.job.JobScheduler;
import android.app.job.JobService;
import android.content.ComponentName;
import android.content.Context;

/**
 * Cada ~30 minutos, aunque la app esté cerrada, copia el historial de llamadas a la copia propia
 * (RegistroTelefono.sincronizar). Así una llamada que el asesor borre del historial ya quedó copiada.
 */
public class CopiaLlamadas extends JobService {
    private static final int ID = 4107;

    static void programar(Context ctx) {
        JobScheduler js = ctx.getSystemService(JobScheduler.class);
        if (js == null || js.getPendingJob(ID) != null) return;
        js.schedule(new JobInfo.Builder(ID, new ComponentName(ctx, CopiaLlamadas.class))
                .setPeriodic(30 * 60 * 1000L)
                .setPersisted(true)  // sigue después de reiniciar el teléfono (RECEIVE_BOOT_COMPLETED)
                .build());
    }

    @Override
    public boolean onStartJob(JobParameters params) {
        new Thread(() -> {
            try {
                RegistroTelefono.sincronizar(getApplicationContext());
            } catch (Exception ignorada) {
                // se intenta de nuevo en la próxima vuelta
            }
            jobFinished(params, false);
        }).start();
        return true;
    }

    @Override
    public boolean onStopJob(JobParameters params) {
        return true;
    }
}
