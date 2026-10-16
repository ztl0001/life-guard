package com.codex.applock;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.util.Log;

import java.util.TreeSet;

/**
 * 试运行 / 状态查询入口（需要 adb）。只能「加锁」，不能解除锁定、不能改时段、不能改白名单：
 *   adb shell am broadcast -a com.codex.applock.CMD --es cmd test --ei seconds 120
 *   adb shell am broadcast -a com.codex.applock.CMD --es cmd clear
 *   adb shell am broadcast -a com.codex.applock.CMD --es cmd status
 */
public class TestReceiver extends BroadcastReceiver {

    @Override
    public void onReceive(Context c, Intent intent) {
        LockConfig.init(c);
        String cmd = intent.getStringExtra("cmd");
        if (cmd == null) {
            cmd = "status";
        }
        if ("test".equals(cmd)) {
            int sec = intent.getIntExtra("seconds", 120);
            LockConfig.setTestUntil(c, System.currentTimeMillis() + sec * 1000L);
        } else if ("clear".equals(cmd)) {
            LockConfig.setTestUntil(c, 0L);
        }
        dump(c, cmd);
    }

    static void dump(Context c, String tag) {
        long now = System.currentTimeMillis();
        Log.i(AppLockAccessibilityService.TAG, "STATUS cmd=" + tag
                + " window=" + LockConfig.windowText()
                + " inWindow=" + LockConfig.inWindow(now)
                + " lockActive=" + LockConfig.isLockActive(c, now)
                + " remainSec=" + LockConfig.untilUnlock(c, now) / 1000L
                + " testUntil=" + LockConfig.testUntil(c)
                + " unlockUntil=" + LockConfig.unlockUntil(c)
                + " unlockLeft=" + LockConfig.unlockLeft(c)
                + " allow=" + new TreeSet<String>(Defaults.effectiveAllow(c)));
    }
}
