package com.codex.applock;

import android.content.ActivityNotFoundException;
import android.content.Context;
import android.content.Intent;
import android.graphics.Color;
import android.graphics.PixelFormat;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.os.PowerManager;
import android.provider.AlarmClock;
import android.provider.Settings;
import android.util.Log;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

/** 拦截提示浮层：全屏遮罩 + 说明卡片，可倒计时自动消失 */
public class LockOverlay {

    private static final long AUTO_HIDE_MS = 20000L;

    private final Context ctx;
    private final WindowManager wm;
    private final Handler handler = new Handler(Looper.getMainLooper());

    private View root;
    private TextView countdown;
    private TextView subject;
    private String pkg;

    private final Runnable tick = new Runnable() {
        @Override
        public void run() {
            if (root == null) {
                return;
            }
            PowerManager pm = (PowerManager) ctx.getSystemService(Context.POWER_SERVICE);
            if (pm != null && !pm.isInteractive()) {
                hide();
                return;
            }
            long remain = LockConfig.untilUnlock(ctx, System.currentTimeMillis());
            if (remain <= 0) {
                hide();
                return;
            }
            countdown.setText("距离解锁还有 " + human(remain));
            handler.postDelayed(this, 1000L);
        }
    };

    private final Runnable autoHide = new Runnable() {
        @Override
        public void run() {
            hide();
        }
    };

    public LockOverlay(Context ctx) {
        this.ctx = ctx;
        this.wm = (WindowManager) ctx.getSystemService(Context.WINDOW_SERVICE);
    }

    public boolean isShowing() {
        return root != null;
    }

    /** 是否具备弹浮层的能力（悬浮窗权限） */
    public static boolean canShow(Context c) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            return Settings.canDrawOverlays(c);
        }
        return true;
    }

    public void show(String packageName) {
        this.pkg = packageName;
        if (root != null) {
            if (subject != null) {
                subject.setText("已被拦截：" + Defaults.label(ctx, packageName));
            }
            return;
        }
        if (wm == null) {
            return;
        }
        root = buildView();
        WindowManager.LayoutParams lp = new WindowManager.LayoutParams(
                WindowManager.LayoutParams.MATCH_PARENT,
                WindowManager.LayoutParams.MATCH_PARENT,
                Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
                        ? WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
                        : WindowManager.LayoutParams.TYPE_PHONE,
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
                        | WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
                PixelFormat.RGBA_8888);
        lp.gravity = Gravity.TOP | Gravity.START;
        if (Build.VERSION.SDK_INT >= 28) {
            lp.layoutInDisplayCutoutMode =
                    WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES;
        }
        try {
            wm.addView(root, lp);
        } catch (Throwable t) {
            Log.w(AppLockAccessibilityService.TAG, "addView failed: " + t);
            root = null;
            return;
        }
        handler.post(tick);
        handler.postDelayed(autoHide, AUTO_HIDE_MS);
    }

    public void hide() {
        handler.removeCallbacks(tick);
        handler.removeCallbacks(autoHide);
        if (root != null) {
            try {
                wm.removeView(root);
            } catch (Throwable ignored) {
            }
            root = null;
        }
    }

    /** 用户从浮层里点了电话 / 短信 / 时钟，先收浮层再跳转 */
    public void open(Intent intent) {
        hide();
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        try {
            ctx.startActivity(intent);
        } catch (ActivityNotFoundException e) {
            Log.w(AppLockAccessibilityService.TAG, "no activity for " + intent);
        } catch (Throwable t) {
            Log.w(AppLockAccessibilityService.TAG, "startActivity failed: " + t);
        }
    }

    private View buildView() {
        int pad = dp(20);

        LinearLayout rootLayout = new LinearLayout(ctx);
        rootLayout.setOrientation(LinearLayout.VERTICAL);
        rootLayout.setGravity(Gravity.CENTER);
        rootLayout.setBackgroundColor(0xF2000000);
        rootLayout.setPadding(pad, pad, pad, pad);
        rootLayout.setClickable(true);

        LinearLayout card = new LinearLayout(ctx);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setGravity(Gravity.CENTER_HORIZONTAL);
        card.setPadding(dp(22), dp(26), dp(22), dp(22));
        GradientDrawable bg = new GradientDrawable();
        bg.setColor(0xFF15161C);
        bg.setCornerRadius(dp(20));
        bg.setStroke(dp(1), 0x33FFFFFF);
        card.setBackground(bg);

        TextView title = new TextView(ctx);
        title.setText("\uD83C\uDF19 夜间锁已开启");
        title.setTextColor(Color.WHITE);
        title.setTextSize(TypedValue.COMPLEX_UNIT_SP, 22);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        card.addView(title);

        TextView rule = new TextView(ctx);
        rule.setText(LockConfig.windowText() + " 期间，除短信 / 时钟 / 电话外\n其他应用不可使用");
        rule.setTextColor(0xFFB9BCC7);
        rule.setTextSize(TypedValue.COMPLEX_UNIT_SP, 14);
        rule.setGravity(Gravity.CENTER);
        rule.setPadding(0, dp(10), 0, dp(4));
        card.addView(rule);

        subject = new TextView(ctx);
        subject.setText("已被拦截：" + Defaults.label(ctx, pkg));
        subject.setTextColor(0xFFFF8A80);
        subject.setTextSize(TypedValue.COMPLEX_UNIT_SP, 15);
        subject.setGravity(Gravity.CENTER);
        subject.setPadding(0, dp(12), 0, dp(2));
        card.addView(subject);

        countdown = new TextView(ctx);
        countdown.setText("距离解锁还有 " + human(LockConfig.untilUnlock(ctx, System.currentTimeMillis())));
        countdown.setTextColor(0xFF9AA0B0);
        countdown.setTextSize(TypedValue.COMPLEX_UNIT_SP, 13);
        countdown.setGravity(Gravity.CENTER);
        countdown.setPadding(0, dp(6), 0, dp(14));
        card.addView(countdown);

        LinearLayout quick = new LinearLayout(ctx);
        quick.setOrientation(LinearLayout.HORIZONTAL);
        quick.setGravity(Gravity.CENTER);
        quick.addView(button("电话", new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                open(new Intent(Intent.ACTION_DIAL));
            }
        }));
        quick.addView(button("短信", new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                open(new Intent(Intent.ACTION_VIEW, Uri.parse("sms:")));
            }
        }));
        quick.addView(button("时钟", new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                open(new Intent(AlarmClock.ACTION_SHOW_ALARMS));
            }
        }));
        card.addView(quick);

        final Button unlockBtn = new Button(ctx);
        unlockBtn.setAllCaps(false);
        unlockBtn.setTextSize(TypedValue.COMPLEX_UNIT_SP, 14);
        unlockBtn.setTextColor(0xFF14161C);
        unlockBtn.setBackgroundColor(0xFFFFC66D);
        refreshUnlockLabel(unlockBtn);
        unlockBtn.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                if (LockConfig.unlockNow(ctx)) {
                    handler.removeCallbacks(tick);
                    handler.removeCallbacks(autoHide);
                    rule.setText("已临时解锁 " + LockConfig.UNLOCK_MINUTES + " 分钟");
                    subject.setText("这期间所有应用都可以正常使用");
                    countdown.setText("今晚还剩 " + LockConfig.unlockLeft(ctx) + " 次临时解锁");
                    unlockBtn.setEnabled(false);
                    handler.postDelayed(autoHide, 2000L);
                    Toast.makeText(ctx, "已临时解锁 " + LockConfig.UNLOCK_MINUTES + " 分钟",
                            Toast.LENGTH_SHORT).show();
                } else {
                    refreshUnlockLabel(unlockBtn);
                    Toast.makeText(ctx, "今晚的 " + LockConfig.UNLOCK_MAX_PER_DAY + " 次临时解锁已用完",
                            Toast.LENGTH_SHORT).show();
                }
            }
        });
        LinearLayout.LayoutParams unlockLp = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        unlockLp.topMargin = dp(12);
        unlockBtn.setLayoutParams(unlockLp);
        card.addView(unlockBtn);

        Button home = button("返回桌面", new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                Intent i = new Intent(Intent.ACTION_MAIN);
                i.addCategory(Intent.CATEGORY_HOME);
                open(i);
            }
        });
        LinearLayout.LayoutParams homeLp = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        homeLp.topMargin = dp(10);
        home.setLayoutParams(homeLp);
        card.addView(home);

        TextView close = new TextView(ctx);
        close.setText("点这里关闭提示");
        close.setTextColor(0xFF7C8194);
        close.setTextSize(TypedValue.COMPLEX_UNIT_SP, 13);
        close.setPadding(dp(10), dp(16), dp(10), dp(6));
        close.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                hide();
            }
        });
        card.addView(close);

        rootLayout.addView(card, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT));
        return rootLayout;
    }

    private Button button(String text, View.OnClickListener l) {
        Button b = new Button(ctx);
        b.setText(text);
        b.setTextSize(TypedValue.COMPLEX_UNIT_SP, 14);
        b.setAllCaps(false);
        b.setOnClickListener(l);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(0,
                LinearLayout.LayoutParams.WRAP_CONTENT, 1f);
        lp.leftMargin = dp(4);
        lp.rightMargin = dp(4);
        b.setLayoutParams(lp);
        return b;
    }

    private int dp(float v) {
        return Math.round(ctx.getResources().getDisplayMetrics().density * v);
    }

    private void refreshUnlockLabel(Button b) {
        int left = LockConfig.unlockLeft(ctx);
        if (left <= 0) {
            b.setEnabled(false);
            b.setText("今晚的临时解锁次数已用完（" + LockConfig.UNLOCK_MAX_PER_DAY + " / "
                    + LockConfig.UNLOCK_MAX_PER_DAY + "）");
        } else {
            b.setEnabled(true);
            b.setText("解锁 " + LockConfig.UNLOCK_MINUTES + " 分钟（今晚还剩 " + left + " 次）");
        }
    }

    public static String human(long ms) {
        if (ms < 0) {
            ms = 0;
        }
        long totalSec = ms / 1000L;
        long h = totalSec / 3600L;
        long m = (totalSec % 3600L) / 60L;
        long s = totalSec % 60L;
        if (h > 0) {
            return h + " 小时 " + m + " 分";
        }
        if (m > 0) {
            return m + " 分 " + s + " 秒";
        }
        return s + " 秒";
    }
}
