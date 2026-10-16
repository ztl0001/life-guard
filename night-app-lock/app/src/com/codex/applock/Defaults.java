package com.codex.applock;

import android.content.Context;
import android.content.Intent;
import android.content.pm.ApplicationInfo;
import android.content.pm.PackageManager;
import android.content.pm.ResolveInfo;
import android.view.inputmethod.InputMethodInfo;
import android.view.inputmethod.InputMethodManager;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * 白名单默认值。除短信 / 时钟 / 电话外，还包含一批必须永远放行的系统组件
 * （桌面、输入法、SystemUI、来电界面等），否则设备会变得无法使用。
 */
public final class Defaults {

    /** 永远放行的包名：桌面 / 输入法会动态补充 */
    public static final Set<String> ALWAYS = Collections.unmodifiableSet(new HashSet<String>(Arrays.asList(
            "android",
            "com.android.systemui",
            "com.android.keyguard",
            "com.android.permissioncontroller",
            "com.google.android.permissioncontroller",
            "com.android.server.telecom",
            "com.android.incallui",
            "com.android.phone"
    )));

    /** 常见厂商的短信 / 时钟 / 电话包名 */
    public static final Map<String, String[]> KNOWN = new LinkedHashMap<String, String[]>();

    /** 应用名关键词兜底：ROM 包名千奇百怪时靠名称识别 */
    private static final String[][] KEYWORDS = new String[][]{
            {"短信", "信息", "messages", "messaging", "sms"},
            {"时钟", "闹钟", "clock", "alarm"},
            {"电话", "拨号", "通讯录", "dialer", "phone", "contacts", "contact"}
    };

    static {
        KNOWN.put("短信", new String[]{
                "com.android.mms",
                "com.android.messaging",
                "com.google.android.apps.messaging",
                "com.samsung.android.messaging",
                "com.coloros.mms",
                "com.oplus.mms",
                "com.miui.smsextra",
                "com.huawei.mms",
                "com.vivo.messaging",
                "com.android.Mms",
                "com.transsion.smsmms",
                "com.sonymobile.message"
        });
        KNOWN.put("时钟", new String[]{
                "com.android.deskclock",
                "com.google.android.deskclock",
                "com.sec.android.app.clockpackage",
                "com.coloros.alarmclock",
                "com.oplus.alarmclock",
                "com.miui.clock",
                "com.huawei.deskclock",
                "com.android.BBKClock",
                "com.vivo.deskclock",
                "com.oneplus.deskclock",
                "com.oppo.alarmclock",
                "com.transsion.clock",
                "com.android.clock"
        });
        KNOWN.put("电话", new String[]{
                "com.android.dialer",
                "com.google.android.dialer",
                "com.samsung.android.dialer",
                "com.samsung.android.incallui",
                "com.android.contacts",
                "com.google.android.contacts",
                "com.samsung.android.app.contacts",
                "com.coloros.contacts",
                "com.oplus.contacts",
                "com.miui.contacts",
                "com.huawei.contacts",
                "com.android.emergency"
        });
    }

    private Defaults() {
    }

    public static boolean isInstalled(Context c, String pkg) {
        try {
            c.getPackageManager().getPackageInfo(pkg, 0);
            return true;
        } catch (Throwable t) {
            return false;
        }
    }

    /** 桌面（HOME）应用，必须放行 */
    public static Set<String> launchers(Context c) {
        Set<String> out = new HashSet<String>();
        try {
            PackageManager pm = c.getPackageManager();
            Intent i = new Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_HOME);
            for (ResolveInfo r : pm.queryIntentActivities(i, 0)) {
                if (r.activityInfo != null && r.activityInfo.packageName != null) {
                    out.add(r.activityInfo.packageName);
                }
            }
        } catch (Throwable ignored) {
        }
        return out;
    }

    /** 已启用的输入法，必须放行，否则锁定期间无法打字 */
    public static Set<String> imes(Context c) {
        Set<String> out = new HashSet<String>();
        try {
            InputMethodManager imm = (InputMethodManager) c.getSystemService(Context.INPUT_METHOD_SERVICE);
            if (imm != null) {
                for (InputMethodInfo im : imm.getEnabledInputMethodList()) {
                    out.add(im.getPackageName());
                }
            }
        } catch (Throwable ignored) {
        }
        return out;
    }

    /** 所有可从桌面启动的应用（包名, 名称），按名称排序 */
    public static List<String[]> launchableApps(Context c) {
        List<String[]> out = new ArrayList<String[]>();
        Set<String> seen = new HashSet<String>();
        try {
            PackageManager pm = c.getPackageManager();
            Intent i = new Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER);
            for (ResolveInfo r : pm.queryIntentActivities(i, 0)) {
                if (r.activityInfo == null || r.activityInfo.packageName == null) {
                    continue;
                }
                String pkg = r.activityInfo.packageName;
                if (pkg.equals(c.getPackageName()) || !seen.add(pkg)) {
                    continue;
                }
                CharSequence label = r.loadLabel(pm);
                out.add(new String[]{pkg, label == null ? pkg : label.toString()});
            }
        } catch (Throwable ignored) {
        }
        Collections.sort(out, new Comparator<String[]>() {
            @Override
            public int compare(String[] a, String[] b) {
                return a[1].compareToIgnoreCase(b[1]);
            }
        });
        return out;
    }

    /** 首次运行自动生成的白名单：已安装的短信 / 时钟 / 电话 + 名称匹配兜底 */
    public static Set<String> detectAllowSet(Context c) {
        Set<String> out = new LinkedHashSet<String>();
        for (String[] group : KNOWN.values()) {
            for (String pkg : group) {
                if (isInstalled(c, pkg)) {
                    out.add(pkg);
                }
            }
        }
        try {
            for (String[] app : launchableApps(c)) {
                String low = app[1].toLowerCase();
                for (String[] kw : KEYWORDS) {
                    for (String k : kw) {
                        if (low.contains(k.toLowerCase())) {
                            out.add(app[0]);
                            break;
                        }
                    }
                }
            }
        } catch (Throwable ignored) {
        }
        out.remove(c.getPackageName());
        return out;
    }

    /**
     * 实际放行集合：自动识别的 短信 / 时钟 / 电话 + 桌面 + 输入法 + 本应用自身。
     * App 内不提供编辑入口，完全由系统识别结果决定。
     */
    public static Set<String> effectiveAllow(Context c) {
        Set<String> out = new LinkedHashSet<String>();
        out.addAll(detectAllowSet(c));
        out.addAll(launchers(c));
        out.addAll(imes(c));
        out.add(c.getPackageName());
        return out;
    }

    public static String label(Context c, String pkg) {
        try {
            PackageManager pm = c.getPackageManager();
            ApplicationInfo ai = pm.getApplicationInfo(pkg, 0);
            CharSequence cs = pm.getApplicationLabel(ai);
            if (cs != null && cs.length() > 0) {
                return cs.toString();
            }
        } catch (Throwable ignored) {
        }
        return pkg;
    }
}
