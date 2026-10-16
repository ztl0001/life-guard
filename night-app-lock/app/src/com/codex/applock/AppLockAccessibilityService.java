package com.codex.applock;

import android.accessibilityservice.AccessibilityService;
import android.content.Intent;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.util.Log;
import android.view.accessibility.AccessibilityEvent;

import java.util.HashSet;
import java.util.Set;

/**
 * 核心：监听前台窗口。命中锁定时段且不是放行应用时，立刻退回桌面并弹出提示浮层。
 *
 * 放行名单完全由系统自动识别（短信 / 时钟 / 电话 + 桌面、输入法、来电界面等必需组件），
 * App 内不提供任何修改入口；锁定时段同样是硬编码的。
 */
public class AppLockAccessibilityService extends AccessibilityService {

    public static final String TAG = "AppLock";

    private static volatile AppLockAccessibilityService live;
    private static volatile boolean configDirty = true;

    private final Handler handler = new Handler(Looper.getMainLooper());

    private LockOverlay overlay;
    private Set<String> allowFull = new HashSet<String>();
    private Set<String> launchers = new HashSet<String>();
    private long cacheAt;

    private String lastPkg;
    private long lastBlockAt;

    public static void markConfigDirty() {
        configDirty = true;
        final AppLockAccessibilityService s = live;
        if (s != null) {
            s.handler.post(new Runnable() {
                @Override
                public void run() {
                    AppLockAccessibilityService cur = live;
                    if (cur != null) {
                        cur.reloadCache();
                    }
                }
            });
        }
    }

    public static AppLockAccessibilityService get() {
        return live;
    }

    @Override
    protected void onServiceConnected() {
        super.onServiceConnected();
        live = this;
        LockConfig.init(this);
        if (overlay == null) {
            overlay = new LockOverlay(this);
        }
        reloadCache();
        Log.i(TAG, "service connected, window=" + LockConfig.windowText()
                + ", lockActive=" + LockConfig.isLockActive(this, System.currentTimeMillis())
                + ", unlockLeft=" + LockConfig.unlockLeft(this)
                + ", allow=" + allowFull.size());
    }

    @Override
    public boolean onUnbind(Intent intent) {
        if (overlay != null) {
            overlay.hide();
        }
        if (live == this) {
            live = null;
        }
        Log.i(TAG, "service unbound");
        return super.onUnbind(intent);
    }

    @Override
    public void onDestroy() {
        if (overlay != null) {
            overlay.hide();
            overlay = null;
        }
        if (live == this) {
            live = null;
        }
        super.onDestroy();
    }

    @Override
    public void onAccessibilityEvent(AccessibilityEvent event) {
        if (event == null) {
            return;
        }
        int type = event.getEventType();
        if (type != AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED
                && type != AccessibilityEvent.TYPE_WINDOWS_CHANGED) {
            return;
        }
        CharSequence cs = event.getPackageName();
        if (cs == null) {
            return;
        }
        String pkg = cs.toString();
        boolean mine = pkg.equals(getPackageName());

        if (overlay != null && overlay.isShowing() && !mine) {
            // 用户从浮层跳到了电话 / 短信 / 时钟，或临时解锁后打开了别的应用，浮层自动收起
            if (isAllowedNow(pkg) && !launchers.contains(pkg)) {
                overlay.hide();
            }
        }
        if (mine) {
            return;
        }
        if (isAllowedNow(pkg)) {
            return;
        }
        if (!LockConfig.isLockActive(this, System.currentTimeMillis())) {
            return;
        }
        long now = SystemClock.uptimeMillis();
        if (pkg.equals(lastPkg) && now - lastBlockAt < 800L) {
            return;
        }
        lastPkg = pkg;
        lastBlockAt = now;
        block(pkg);
    }

    private void block(final String pkg) {
        Log.i(TAG, "BLOCK " + pkg + " at "
                + LockConfig.fmt(LockConfig.minuteOfDay(System.currentTimeMillis())));
        try {
            performGlobalAction(GLOBAL_ACTION_HOME);
        } catch (Throwable t) {
            Log.w(TAG, "performGlobalAction failed: " + t);
        }
        handler.postDelayed(new Runnable() {
            @Override
            public void run() {
                if (!LockConfig.isLockActive(AppLockAccessibilityService.this, System.currentTimeMillis())) {
                    return;
                }
                if (overlay == null) {
                    overlay = new LockOverlay(AppLockAccessibilityService.this);
                }
                if (LockOverlay.canShow(AppLockAccessibilityService.this)) {
                    overlay.show(pkg);
                } else {
                    Log.i(TAG, "no overlay permission, blocked silently: " + pkg);
                }
            }
        }, 320L);
    }

    private boolean isAllowedNow(String pkg) {
        if (configDirty || SystemClock.uptimeMillis() - cacheAt > 60000L) {
            reloadCache();
        }
        return allowFull.contains(pkg) || Defaults.ALWAYS.contains(pkg);
    }

    private void reloadCache() {
        allowFull = Defaults.effectiveAllow(this);
        launchers = Defaults.launchers(this);
        cacheAt = SystemClock.uptimeMillis();
        configDirty = false;
    }

    @Override
    public void onInterrupt() {
    }
}
