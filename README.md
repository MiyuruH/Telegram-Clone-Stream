# 📨 Telegram Channel Transfer

A Python script to copy all messages from one Telegram channel to another using the [Telethon](https://docs.telethon.dev/) library. Messages are sent as original content (no "Forwarded from" label).

---

## ✨ Features

- **Batch sending** — sends 99 messages back-to-back per batch for speed
- **No forward label** — uses `send_message`/`send_file` so messages appear as original
- **Private channel support** — works with private invite links (`t.me/+HASH`)
- **Auto-join** — automatically joins private channels if not already a member
- **Resume support** — saves progress to disk, resumes after interruption
- **Graceful shutdown** — press `Ctrl+C` to stop cleanly and save progress
- **Flood protection** — built-in delays, jitter, batch pauses, and exponential backoff
- **Retry logic** — retries failed messages up to 5 times before skipping
- **Media support** — transfers photos, videos, documents, stickers, and more

---

## 📋 Requirements

- **Python 3.10+**
- **Telethon** library

### Install dependencies

```bash
pip install telethon
```

---

## ⚙️ Setup

### 1. Get Telegram API credentials

1. Go to [https://my.telegram.org](https://my.telegram.org)
2. Log in with your phone number
3. Go to **API development tools**
4. Create an application — you'll get an **API ID** and **API Hash**

### 2. Configure the script

Open `transfer.py` and edit the configuration section near the top:

```python
api_id    = 12345678                              # Your API ID (number)
api_hash  = "your_api_hash_here"                  # Your API Hash (string)
phone     = "+1234567890"                         # Your phone number with country code
source_ch = "https://t.me/+inviteHashHere"        # Channel to copy FROM
target_ch = "https://t.me/+inviteHashHere"        # Channel to copy TO
```

### Supported channel formats

| Format | Example |
|---|---|
| Private invite link | `https://t.me/+abc123XYZ` |
| Join link | `https://t.me/joinchat/abc123XYZ` |
| Public username | `channelname` |
| @username | `@channelname` |
| Numeric ID | `-1001234567890` |

---

## 🚀 Usage

```bash
python transfer.py
```

On first run, you'll be asked to enter a login code sent to your Telegram account. After that, the session is saved and you won't need to log in again.

### Example output

```
16:00:22 | INFO    | ====================================================
16:00:22 | INFO    |   Telegram Channel Transfer
16:00:22 | INFO    | ====================================================
16:00:22 | INFO    | Source : https://t.me/+abc123
16:00:22 | INFO    | Target : https://t.me/+xyz789
16:00:22 | INFO    | Batch  : 99 msgs, 0.5s micro-delay between sends
16:00:22 | INFO    | Delay  : 5s + jitter (1, 3) between batches
16:00:22 | INFO    | Pause  : every 3 batches, 60s pause
16:00:22 | INFO    | ====================================================
16:00:23 | INFO    | Source channel OK: My Source Channel
16:00:24 | INFO    | Target channel OK: My Target Channel
16:00:25 | INFO    | Sending batch of 99 messages (ids 1–99)...
16:01:15 | INFO    | ✓ Batch done — 99 sent, 0 failed | total: 99 copied
```

---

## 🔧 Safety Settings

You can tweak these values at the top of `transfer.py`:

| Setting | Default | Description |
|---|---|---|
| `FORWARD_CHUNK` | `99` | Messages per batch (max 100) |
| `MICRO_DELAY` | `0.5` | Seconds between individual sends within a batch |
| `BASE_DELAY` | `5` | Seconds to pause between batches |
| `JITTER_RANGE` | `(1, 3)` | Random extra seconds added to delay |
| `BATCH_SIZE` | `3` | Number of batches before a long pause |
| `BATCH_PAUSE` | `60` | Long pause duration (seconds) |
| `MAX_RETRIES` | `5` | Retry attempts per message before skipping |
| `MAX_DELAY` | `120` | Maximum backoff delay (seconds) |

### Speed vs Safety tradeoff

| Profile | `MICRO_DELAY` | `BASE_DELAY` | `BATCH_PAUSE` | Speed |
|---|---|---|---|---|
| **Aggressive** | `0.3` | `3` | `30` | ~330 msgs/min |
| **Default** | `0.5` | `5` | `60` | ~120 msgs/min |
| **Conservative** | `1.0` | `10` | `120` | ~60 msgs/min |

> ⚠️ Lower delays = faster transfer but higher risk of FloodWait or account restrictions.

---

## 💾 Resume & Progress

- Progress is saved to `transfer_progress.json` after every batch
- If the script is interrupted (Ctrl+C, crash, etc.), just run it again — it will resume from where it left off
- Progress file is automatically deleted after a successful complete transfer
- If you change source/target channels, progress resets automatically

---

## 🧹 Cleanup

After you're done, delete the session file to protect your account:

```bash
# Windows
del tg_transfer_session.session

# Linux/Mac
rm tg_transfer_session.session
```

---

## 📁 Files

| File | Description |
|---|---|
| `transfer.py` | Main transfer script |
| `tg_transfer_session.session` | Telegram session (created on first login) |
| `transfer_progress.json` | Resume progress (auto-created, auto-deleted) |

---

## ❓ Troubleshooting

### "Cannot find any entity"
- Make sure you're a **member** of both channels
- For private channels, use the full invite link: `https://t.me/+HASH`
- The script will auto-join if you provide an invite link

### FloodWaitError
- Telegram is rate-limiting you — the script will automatically wait and retry
- If it happens frequently, increase `MICRO_DELAY` and `BASE_DELAY`

### "No write permission in target channel"
- You need **admin/post permissions** in the target channel

### Session issues
- Delete `tg_transfer_session.session` and run again to re-authenticate

---

## ⚠️ Disclaimer

This script uses the Telegram API through your personal account. Excessive automation may violate [Telegram's Terms of Service](https://telegram.org/tos) and could result in account restrictions. Use responsibly and at your own risk.
