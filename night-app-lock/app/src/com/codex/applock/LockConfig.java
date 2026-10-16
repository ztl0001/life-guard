package com.codex.applock;

import android.content.Context;
import android.content.SharedPreferences;

import java.util.Calendar;

/**
 * 锁定规则。
 *
 * 全部是硬编码常量，App 内不提供任何修改入口：
 *   锁定时段：01:30 - 06:00（不可开关、不可调整）
 *   放行应用：系统自动识别的 短信 / 时钟 / 电话 + 桌面、输入法、来电界面等必需组件（不可编辑）
 *   临时解锁：锁定时段内可「解锁 3 分钟」，每个自然日最多 3 次，用完即止
 */
public final class LockConfig {

    private static final String PREFS = "applock";

    /** 锁定时段，从 00:00 起算的分钟数 */
    public static final int WINDOW_START = 1 * 60 + 30;  // 01:30
    public static final int WINDOW_END = 6 * 60;         // 06:00

    /** 临时解锁：每次 3 分钟，每自然日 3 次 */
    public static final int UNLOCK_MINUTES = 3;
    public static final int UNLOCK_MAX_PER_DAY = 3;

    private static final String K_TEST_UNTIL = "test_until";
    private static final String K_UNLOCK_UNTIL = "unlock_until";
    private static final String K_UNLOCK_DAY = "unlock_day";
    private static final String K_UNLOCK_USED = "unlock_used";
    private static final String K_LEGACY_CLEANED = "legacy_cleaned";

    /** 旧版本（带开关 / 时段 / 白名单编辑）遗留的键，升级时一律清掉 */
    private static final String[] LEGACY_KEYS = {
            "ready", "enabled", "start_min", "end_min", "allow_set"
    };

    private LockConfig() {
    }

    private static SharedPreferences sp(Context c) {
        return c.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    /** 升级安装后清理旧版可编辑配置，避免残留设置影响判定 */
    public static void cleanupLegacy(Context c) {
        SharedPreferences p = sp(c);
        if (p.getBoolean(K_LEGACY_CLEANED, false)) {
            return;
        }
        SharedPreferences.Editor e = p.edit();
        for (String k : LEGACY_KEYS) {
            e.remove(k);
        }
        e.putBoolean(K_LEGACY_CLEANED, true).apply();
    }

    public static void init(Context c) {
        cleanupLegacy(c);
    }

    // ---------------- 时段 ----------------

    public static boolean inWindow(long timeMs) {
        int now = minuteOfDay(timeMs);
        return now >= WINDOW_START && now < WINDOW_END;
    }

    public static int minuteOfDay(long timeMs) {
        Calendar cal = Calendar.getInstance();
        cal.setTimeInMillis(timeMs);
        return cal.get(Calendar.HOUR_OF_DAY) * 60 + cal.get(Calendar.MINUTE);
    }

    /** 现在是否处于锁定状态（临时解锁中不算） */
    public static boolean isLockActive(Context c, long timeMs) {
        if (isUnlocking(c, timeMs)) {
            return false;
        }
        if (testUntil(c) > timeMs) {
            return true;
        }
        return inWindow(timeMs);
    }

    /** 距离解锁还剩多久；未处于锁定状态返回 0 */
    public static long untilUnlock(Context c, long timeMs) {
        if (!isLockActive(c, timeMs)) {
            return 0L;
        }
        long test = testUntil(c);
        boolean testOutsideWindow = test > timeMs && !inWindow(timeMs);
        if (testOutsideWindow) {
            return test - timeMs;
        }
        long window = msToWindowEnd(timeMs);
        if (test > timeMs) {
            return Math.min(test - timeMs, window);
        }
        return window;
    }

    private static long msToWindowEnd(long timeMs) {
        Calendar cal = Calendar.getInstance();
        cal.setTimeInMillis(timeMs);
        int second = cal.get(Calendar.SECOND);
        int diff = WINDOW_END - minuteOfDay(timeMs);
        if (diff <= 0) {
            diff += 24 * 60;
        }
        return diff * 60000L - second * 1000L;
    }

    // ---------------- 临时解锁 ----------------

    public static long unlockUntil(Context c) {
        return sp(c).getLong(K_UNLOCK_UNTIL, 0L);
    }

    public static boolean isUnlocking(Context c, long timeMs) {
        return unlockUntil(c) > timeMs;
    }

    public static long unlockRemain(Context c, long timeMs) {
        long u = unlockUntil(c);
        return u > timeMs ? u - timeMs : 0L;
    }

    public static int unlockUsed(Context c) {
        SharedPreferences p = sp(c);
        if (!dayKey(System.currentTimeMillis()).equals(p.getString(K_UNLOCK_DAY, ""))) {
            return 0;
        }
        int used = p.getInt(K_UNLOCK_USED, 0);
        if (used < 0) {
            used = 0;
        }
        return Math.min(used, UNLOCK_MAX_PER_DAY);
    }

    public static int unlockLeft(Context c) {
        return Math.max(0, UNLOCK_MAX_PER_DAY - unlockUsed(c));
    }

    /** 解锁 3 分钟。次数用完返回 false */
    public static boolean unlockNow(Context c) {
        if (unlockLeft(c) <= 0) {
            return false;
        }
        long now = System.currentTimeMillis();
        sp(c).edit()
                .putString(K_UNLOCK_DAY, dayKey(now))
                .putInt(K_UNLOCK_USED, unlockUsed(c) + 1)
                .putLong(K_UNLOCK_UNTIL, now + UNLOCK_MINUTES * 60000L)
                .apply();
        notifyChanged();
        return true;
    }

    /** 计数器按自然日归零 */
    public static String dayKey(long timeMs) {
        Calendar cal = Calendar.getInstance();
        cal.setTimeInMillis(timeMs);
        return String.format("%04d-%02d-%02d",
                cal.get(Calendar.YEAR), cal.get(Calendar.MONTH) + 1, cal.get(Calendar.DAY_OF_MONTH));
    }

    // ---------------- 试运行（仅用于验证拦截是否生效，不解除锁定） ----------------

    public static long testUntil(Context c) {
        return sp(c).getLong(K_TEST_UNTIL, 0L);
    }

    public static void setTestUntil(Context c, long ms) {
        sp(c).edit().putLong(K_TEST_UNTIL, ms).apply();
        notifyChanged();
    }

    // ---------------- 展示辅助 ----------------

    public static String fmt(int minutes) {
        return String.format("%02d:%02d", minutes / 60, minutes % 60);
    }

    public static String windowText() {
        return fmt(WINDOW_START) + " - " + fmt(WINDOW_END);
    }

    static void notifyChanged() {
        AppLockAccessibilityService.markConfigDirty();
    }
}
