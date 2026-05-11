# Android setup for ntfy (topic: `vk18mgrx7`)

Goal: get a **loud, DND-piercing push** on your Android phone the moment the watcher fires.

---

## 1. Install the ntfy app

Open **Google Play Store** → search **"ntfy"** → install the one by **Philipp Heckel**
(icon is a white bell on a green background).

Direct link: https://play.google.com/store/apps/details?id=io.heckel.ntfy

> There's also an F-Droid build if you prefer — same app, same author.

---

## 2. Subscribe to the topics

Subscribe to **two** topics in the ntfy app:

**a. Ticket alerts** (loud, DND-breaking)

1. Open the ntfy app → tap **`+`** (bottom-right) → **"Subscribe to topic"**
2. Topic name: **`vk18mgrx7`**
3. Server: `ntfy.sh` (default)
4. Subscribe.

**b. API / infrastructure alerts** (quieter — not DND-breaking)

Same steps but with topic name: **`vk18mgrx7-api-errors`**

This topic receives notifications if district.in starts returning HTTP errors
or becomes unreachable. The watcher auto-pauses itself if the failures persist,
so you get a heads-up to investigate (and re-enable the workflow in GitHub's
Actions UI once district.in is healthy again).

For this second subscription, you probably **don't** want DND override in
step 4 below — these aren't urgent enough to wake you.

---

## 3. Send a test alert

From any terminal (your laptop is fine):

```bash
curl -H "Priority: urgent" -H "Tags: rotating_light" \
     -d "test — ipl ticket watcher" ntfy.sh/vk18mgrx7
```

You should get a push within ~2 seconds with a red/siren emoji and an urgent notification sound.

**If you don't see it:**
- Pull down inside the ntfy app to force-refresh.
- Check the subscription actually landed (topic name is case-sensitive).
- Check Android's notification settings for the ntfy app are enabled (step 4 below).

---

## 4. Make it break through silent mode / DND

This is the part most people miss. Android will happily mute "urgent" pushes unless you tell it otherwise.

### 4a. App-level notification channel

1. Long-press the ntfy app icon → **App info** (or Settings → Apps → ntfy).
2. Tap **Notifications**.
3. You'll see channels like *Default*, *High priority*, *Urgent*, *Max priority*.
4. Open **Urgent** (and **Max priority** if present):
   - **Importance**: Urgent (make sound and pop on screen)
   - **Sound**: pick something that will wake you (the default siren is good)
   - **Vibrate**: on
   - **Override Do Not Disturb**: **ON** ← critical
   - **Bypass Work profile**: on (if applicable)

### 4b. Per-topic override (inside ntfy app)

1. Open the ntfy app → tap the `vk18mgrx7` topic.
2. Tap the **three-dot menu** (top-right) → **Notification settings**.
3. Set:
   - **Sound**: loud/custom ringtone
   - **Minimum priority**: Default (so even non-urgent comes through, but the watcher sends urgent anyway)

### 4c. Allow ntfy through Do Not Disturb globally

1. Android **Settings** → **Sound & vibration** → **Do Not Disturb**.
2. **Apps** (or "Allowed apps" / "Exceptions") → add **ntfy**.
3. Also in **Notifications** → "Priority conversations" or "Override DND apps" → enable **ntfy**.

> Menu names vary by manufacturer (Samsung/OnePlus/Pixel word things slightly differently). The gist: find the DND exceptions list and whitelist ntfy.

---

## 5. Survive battery optimization (VERY IMPORTANT)

Android will aggressively kill background services. If the ntfy app gets killed, pushes won't arrive. Fix:

1. **Settings** → **Apps** → **ntfy** → **Battery**.
2. Set to **Unrestricted** (or "Don't optimize" / "No restrictions").

On **Samsung / Xiaomi / OnePlus / Oppo / Vivo / Realme**, there's usually *another* setting:

- **Samsung**: Settings → Device care → Battery → Background usage limits → **Never sleeping apps** → add ntfy.
- **Xiaomi/MIUI**: Settings → Apps → Manage apps → ntfy → **Autostart: ON** AND Battery saver → **No restrictions**.
- **OnePlus/Oppo**: Settings → Battery → Battery optimization → ntfy → **Don't optimize**. Also Settings → Apps → ntfy → Manage notifications → **Allow notifications** + **Allow floating notifications**.

> If you skip this, the app will silently stop receiving pushes after a few hours and you'll miss the drop.

---

## 6. (Optional) Test that DND override actually works

1. Turn on **Do Not Disturb** on your phone.
2. Send the test curl from step 3 again.
3. Phone should still ring/vibrate loudly. If it's silent, revisit step 4.

---

## 7. Done

When the watcher fires, tapping the notification will open the District event page directly (the script sets a `Click` header).

If you ever want to stop alerts: just unsubscribe from `vk18mgrx7` inside the app, or kill the cron job on EC2.
