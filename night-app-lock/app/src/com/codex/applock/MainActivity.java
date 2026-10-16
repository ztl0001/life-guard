package com.codex.applock;

import android.app.Activity;
import android.content.Intent;
import android.graphics.Typeface;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.PowerManager;
import android.provider.Settings;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import java.util.ArrayList;
import java.util.List;
import java.util.Set;

/**
 * 主界面：只读状态 + 试运行 + 每夜限次的临时解锁。
 *
 * 没有任何可以关闭或调整锁定的入口：没有启用开关、没有时段设置、没有白名单编辑。
 * 安装 / 授权入口只在「服务未开启」或「不在锁定时段」时出现，避免变成绕锁通道。
 */
public class MainActivity extends Activity {

    private TextView statusText;
    private TextView allowText;
    private Button unlockButton;
    private LinearLayout setupBox;

    private final Handler handler = new Handler(Looper.getMainLooper());
    private final Runnable refresher = new Runnable() {
        @Override
        public void run() {
            updateStatus();
            handler.postDelayed(this, 1000L);
        }
    };

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        LockConfig.init(this);
        setContentView(buildUi());
    }

    @Override
    protected void onResume() {
        super.onResume();
        fillAllowText();
        handler.removeCallbacks(refresher);
        handler.post(refresher);
    }

    @Override
    protected void onPause() {
        super.onPause();
        handler.removeCallbacks(refresher);
    }

    private View buildUi() {
        ScrollView sv = new ScrollView(this);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(dp(20), dp(24), dp(20), dp(32));
        sv.addView(root);

        TextView title = new TextView(this);
        title.setText("夜间应用锁");
        title.setTextSize(TypedValue.COMPLEX_UNIT_SP, 24);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        root.addView(title);

        root.addView(hint("每天 " + LockConfig.windowText() + " 期间，除短信 / 时钟 / 电话外，"
                + "其他应用都会被立即退回桌面。\n\n锁定时段和放行名单固定在本应用内部，"
                + "这里没有关闭开关、没有时段设置、也没有白名单编辑。"));

        root.addView(sectionTitle("运行状态"));
        statusText = new TextView(this);
        statusText.setTextSize(TypedValue.COMPLEX_UNIT_SP, 14);
        statusText.setLineSpacing(dp(3), 1f);
        statusText.setPadding(0, dp(6), 0, dp(10));
        root.addView(statusText);

        unlockButton = new Button(this);
        unlockButton.setAllCaps(false);
        unlockButton.setTextSize(TypedValue.COMPLEX_UNIT_SP, 16);
        unlockButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                if (LockConfig.unlockNow(MainActivity.this)) {
                    toast("已临时解锁 " + LockConfig.UNLOCK_MINUTES + " 分钟，今晚还剩 "
                            + LockConfig.unlockLeft(MainActivity.this) + " 次");
                } else {
                    toast("今晚的 " + LockConfig.UNLOCK_MAX_PER_DAY + " 次临时解锁已用完");
                }
                updateStatus();
            }
        });
        LinearLayout.LayoutParams unlockLp = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        unlockLp.topMargin = dp(4);
        unlockButton.setLayoutParams(unlockLp);
        root.addView(unlockButton);

        root.addView(sectionTitle("放行应用（自动识别，不可编辑）"));
        allowText = new TextView(this);
        allowText.setTextSize(TypedValue.COMPLEX_UNIT_SP, 14);
        allowText.setLineSpacing(dp(3), 1f);
        allowText.setPadding(0, dp(6), 0, dp(10));
        root.addView(allowText);

        setupBox = new LinearLayout(this);
        setupBox.setOrientation(LinearLayout.VERTICAL);
        setupBox.addView(sectionTitle("安装 / 授权"));
        setupBox.addView(button("① 打开「无障碍」服务设置（必须）", new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                try {
                    startActivity(new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS));
                    toast("在列表中找到「夜间应用锁」，打开开关");
                } catch (Throwable t) {
                    toast("无法打开设置：" + t);
                }
            }
        }));
        setupBox.addView(button("② 授予「悬浮窗」权限（用于提示）", new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                if (Build.VERSION.SDK_INT < Build.VERSION_CODES.M) {
                    toast("当前系统无需手动授权");
                    return;
                }
                try {
                    startActivity(new Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                            Uri.parse("package:" + getPackageName())));
                } catch (Throwable t) {
                    toast("无法打开设置：" + t);
                }
            }
        }));
        setupBox.addView(button("③ 关闭电池优化，防止夜间被杀", new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                if (Build.VERSION.SDK_INT < Build.VERSION_CODES.M) {
                    return;
                }
                try {
                    startActivity(new Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
                            Uri.parse("package:" + getPackageName())));
                } catch (Throwable t) {
                    try {
                        startActivity(new Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS));
                    } catch (Throwable t2) {
                        toast("无法打开设置");
                    }
                }
            }
        }));
        root.addView(setupBox);

        root.addView(sectionTitle("试运行（只加锁，不解除锁定）"));
        root.addView(button("立即测试：锁定 2 分钟", new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                LockConfig.setTestUntil(MainActivity.this, System.currentTimeMillis() + 120000L);
                updateStatus();
                toast("测试锁定已开启：现在打开微信之类的应用会被拦截");
            }
        }));
        root.addView(button("结束试运行", new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                LockConfig.setTestUntil(MainActivity.this, 0L);
                updateStatus();
            }
        }));

        root.addView(sectionTitle("使用提示"));
        root.addView(hint("· 华为 / 荣耀：应用启动管理中允许「自启动 / 关联启动 / 后台活动」，"
                + "否则夜间可能被系统清掉。\n"
                + "· 小米 / 红米：省电策略选「无限制」并打开「自启动」。\n"
                + "· OPPO / vivo：允许后台运行，并在电池设置里允许后台高耗电。\n"
                + "· 重启手机后请确认无障碍服务仍是开启状态。"));
        return sv;
    }

    private void fillAllowText() {
        if (allowText == null) {
            return;
        }
        Set<String> allow = Defaults.effectiveAllow(this);
        List<String> names = new ArrayList<String>();
        for (String p : allow) {
            if (p.equals(getPackageName()) || Defaults.ALWAYS.contains(p)) {
                continue;
            }
            names.add(Defaults.label(this, p));
        }
        StringBuilder sb = new StringBuilder();
        sb.append("短信 / 时钟 / 电话，共识别到 ").append(names.size()).append(" 个应用");
        if (!names.isEmpty()) {
            sb.append("：\n");
            for (int i = 0; i < names.size(); i++) {
                if (i > 0) {
                    sb.append("、");
                }
                sb.append(names.get(i));
            }
        }
        sb.append("\n\n另有桌面、输入法、来电界面等系统必需组件始终放行。");
        allowText.setText(sb.toString());
    }

    private void updateStatus() {
        if (statusText == null || unlockButton == null || setupBox == null) {
            return;
        }
        long now = System.currentTimeMillis();
        boolean window = LockConfig.inWindow(now);
        boolean unlocking = LockConfig.isUnlocking(this, now);
        boolean locked = LockConfig.isLockActive(this, now);

        StringBuilder sb = new StringBuilder();
        sb.append("当前时间：").append(LockConfig.fmt(LockConfig.minuteOfDay(now))).append('\n');
        if (unlocking) {
            sb.append("锁定状态：临时解锁中，剩余 ")
                    .append(LockOverlay.human(LockConfig.unlockRemain(this, now))).append('\n');
        } else if (locked) {
            sb.append("锁定状态：锁定中，距离解锁 ")
                    .append(LockOverlay.human(LockConfig.untilUnlock(this, now))).append('\n');
        } else {
            sb.append("锁定状态：未到时段（下次 ").append(LockConfig.fmt(LockConfig.WINDOW_START))
                    .append(" 开始）\n");
        }
        sb.append("临时解锁：今晚还剩 ").append(LockConfig.unlockLeft(this)).append(" 次（已用 ")
                .append(LockConfig.unlockUsed(this)).append(" / ").append(LockConfig.UNLOCK_MAX_PER_DAY)
                .append("）\n");
        sb.append("无障碍服务：").append(serviceEnabled() ? "已开启" : "未开启（锁不会生效）").append('\n');
        sb.append("悬浮窗权限：").append(LockOverlay.canShow(this) ? "已授予" : "未授予（拦截时不弹提示）")
                .append('\n');
        sb.append("电池优化：").append(batteryOk() ? "已忽略（推荐）" : "未忽略");
        statusText.setText(sb.toString());

        int left = LockConfig.unlockLeft(this);
        if (unlocking) {
            unlockButton.setEnabled(false);
            unlockButton.setText("临时解锁中（剩余 "
                    + LockOverlay.human(LockConfig.unlockRemain(this, now)) + "）");
        } else if (!window && LockConfig.testUntil(this) <= now) {
            unlockButton.setEnabled(false);
            unlockButton.setText("解锁 " + LockConfig.UNLOCK_MINUTES + " 分钟（仅 "
                    + LockConfig.windowText() + " 可用）");
        } else if (left <= 0) {
            unlockButton.setEnabled(false);
            unlockButton.setText("今晚的临时解锁次数已用完（已用 "
                    + LockConfig.UNLOCK_MAX_PER_DAY + " / " + LockConfig.UNLOCK_MAX_PER_DAY + "）");
        } else {
            unlockButton.setEnabled(true);
            unlockButton.setText("解锁 " + LockConfig.UNLOCK_MINUTES + " 分钟（今晚还剩 " + left + " 次）");
        }

        setupBox.setVisibility(setupVisible(now) ? View.VISIBLE : View.GONE);
    }

    /** 授权入口只在锁不可能生效时出现，避免被用来绕锁 */
    private boolean setupVisible(long now) {
        if (!serviceEnabled()) {
            return true;
        }
        if (LockConfig.testUntil(this) > now) {
            return false;
        }
        return !LockConfig.inWindow(now);
    }

    private boolean serviceEnabled() {
        try {
            String flat = Settings.Secure.getString(getContentResolver(),
                    Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES);
            if (flat == null) {
                return false;
            }
            return flat.contains(getPackageName() + "/" + AppLockAccessibilityService.class.getName())
                    || flat.contains(getPackageName() + "/.AppLockAccessibilityService");
        } catch (Throwable t) {
            return false;
        }
    }

    private boolean batteryOk() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.M) {
            return true;
        }
        try {
            PowerManager pm = (PowerManager) getSystemService(POWER_SERVICE);
            return pm != null && pm.isIgnoringBatteryOptimizations(getPackageName());
        } catch (Throwable t) {
            return false;
        }
    }

    private TextView sectionTitle(String text) {
        TextView tv = new TextView(this);
        tv.setText(text);
        tv.setTextSize(TypedValue.COMPLEX_UNIT_SP, 17);
        tv.setTypeface(Typeface.DEFAULT_BOLD);
        tv.setPadding(0, dp(20), 0, dp(6));
        return tv;
    }

    private TextView hint(String text) {
        TextView tv = new TextView(this);
        tv.setText(text);
        tv.setTextSize(TypedValue.COMPLEX_UNIT_SP, 13);
        tv.setTextColor(0xFF808080);
        tv.setPadding(0, dp(8), 0, dp(4));
        return tv;
    }

    private Button button(String text, View.OnClickListener l) {
        Button b = new Button(this);
        b.setText(text);
        b.setAllCaps(false);
        b.setTextSize(TypedValue.COMPLEX_UNIT_SP, 15);
        b.setGravity(Gravity.CENTER_VERTICAL);
        b.setOnClickListener(l);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        lp.topMargin = dp(6);
        b.setLayoutParams(lp);
        return b;
    }

    private void toast(String s) {
        Toast.makeText(this, s, Toast.LENGTH_SHORT).show();
    }

    private int dp(float v) {
        return Math.round(getResources().getDisplayMetrics().density * v);
    }
}
