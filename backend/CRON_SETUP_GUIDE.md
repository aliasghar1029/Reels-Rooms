# Reel Room — Time-Based Scheduling Setup (cron-job.org)

Ye guide add karti hai: har idea apni **exact set date + time** ke qareeb
(~15-30 minute ke andar) automatically publish ho.

⚠️ Pehle `FACEBOOK_SETUP_GUIDE.md` poora ho chuka hona chahiye (Facebook connect,
`env_config.py`, waghera).

---

## Part 1 — `env_config.py` Me Ek Naya Secret Add Karein

1. PythonAnywhere Bash console → `nano Reels-Rooms/backend/env_config.py`
2. Neeche ye line add karein (khud koi bhi lambi random string bana lein):
   ```python
   os.environ['SCHEDULER_SECRET'] = 'apni-koi-bhi-lambi-random-string-yahan'
   ```
3. Save: `Ctrl+O`, Enter, `Ctrl+X`
4. **Web tab** → **Reload** dabayein (naya secret load karne ke liye)

---

## Part 2 — Test Karein Ke Endpoint Kaam Kar Raha Hai

Browser me ye URL kholein (apna username aur secret daal kar):
```
https://admin00000011.pythonanywhere.com/api/scheduler/run?secret=apni-koi-bhi-lambi-random-string-yahan
```

Agar ye response aaye:
```json
{"ok": true, "log": ["Check at 2026-07-20 15:30 PKT (force_all=False)"]}
```
to matlab sab sahi kaam kar raha hai.

---

## Part 3 — cron-job.org Pe Free Account Banayein

1. Jayein: **https://cron-job.org**
2. **"Sign up"** → free account banayein (koi card nahi chahiye)
3. Email confirm karein

---

## Part 4 — Naya Cronjob Banayein

1. Dashboard → **"Create cronjob"**
2. **Title**: `Reel Room Scheduler`
3. **URL**: wahi wali jo Part 2 me test ki thi:
   ```
   https://admin00000011.pythonanywhere.com/api/scheduler/run?secret=apni-koi-bhi-lambi-random-string-yahan
   ```
4. **Execution schedule** → **"Every 15 minutes"** (ya 30 minutes, jitna precise chahiye)
5. **Save**

Bas itna hi! Ab har 15 minute me ye service khud backend ko ping karegi.

---

## Part 5 — Confirm Karein Ye Chal Raha Hai

1. cron-job.org dashboard → apne job pe click karein
2. **"History"** tab me dekhein — har ping ka result (status 200 = sahi) dikhega

---

## Kaise Kaam Karta Hai (Poora Khulasa)

| Kaam | Kaun Karta Hai | Kab |
|---|---|---|
| Time-precise publishing | cron-job.org ping → `/api/scheduler/run` | Har 15-30 minute |
| End-of-day safety net | PythonAnywhere Scheduled Task → `daily_publish.py` | Din me 1 baar (raat ko) |

Agar kisi wajah se koi ping miss ho jaye (rare), to raat ka safety-net task us din
ki bachi hui saari ideas (chahe time guzar chuka ho) publish kar dega — kuch bhi
permanently miss nahi hoga.

## Time Zone Note
Sab kuch **Pakistan Standard Time (PKT, UTC+5)** maan kar calculate hota hai —
jo bhi Time aap Ideas modal me set karenge, wahi PKT samjha jayega.

---

## Common Errors

**`{"error": "Unauthorized"}`**
→ URL me `secret` value `env_config.py` wali value se match nahi kar rahi — dono jagah
  exact same string honi chahiye

**Ping "History" me fail (non-200) dikhaye**
→ PythonAnywhere → Web tab → Error log check karein, exact wajah milegi

**Reel set time pe publish nahi hui**
→ Confirm karein us Page ka Automation tab me connection abhi bhi active hai
  (kabhi kabhi Facebook token expire ho sakta hai, dobara connect karna pade)