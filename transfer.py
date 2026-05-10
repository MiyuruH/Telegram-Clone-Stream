import os
import sys
import json
import asyncio
import random
import logging
import signal
import re
from datetime import datetime
from pathlib import Path

from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError,
    ChatWriteForbiddenError,
    ChannelPrivateError,
    UserBannedInChannelError,
)
from telethon.tl.functions.messages import (
    CheckChatInviteRequest,
    ImportChatInviteRequest,
)
from telethon.tl.types import (
    ChatInviteAlready,
    ChatInvite,
)

# SlowModeWaitError doesn't exist in all Telethon versions
try:
    from telethon.errors import SlowModeWaitError
except ImportError:
    SlowModeWaitError = None

# ─────────────────────────────────────────────
#  LOGGING SETUP
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("transfer")

# ─────────────────────────────────────────────
#  CONFIGURATION — FILL IN YOUR DETAILS BELOW
# ─────────────────────────────────────────────

api_id    = 123456789                       # Your API ID (number) from https://my.telegram.org
api_hash  = "Enter-your-telegram-hash"              # Your API Hash (string) from https://my.telegram.org
phone     = "+94711111123"               # Your phone number with country code
source_ch = "https://t.me/+sdfjhru3834"    # Channel to copy FROM (username, @username, or numeric ID)
target_ch = "https://t.me/+asdkfeorut"    # Channel to copy TO   (username, @username, or numeric ID)


def extract_invite_hash(value: str):
    """
    If the value is a private invite link (t.me/+HASH or t.me/joinchat/HASH),
    return the invite hash. Otherwise return None.
    """
    value = value.strip()
    # Match t.me/+HASH or t.me/joinchat/HASH
    m = re.search(r't\.me/\+([A-Za-z0-9_-]+)', value)
    if m:
        return m.group(1)
    m = re.search(r't\.me/joinchat/([A-Za-z0-9_-]+)', value)
    if m:
        return m.group(1)
    return None


def parse_channel(value: str):
    """
    Parse channel identifier from env var.
    Supports:  numeric ID, @username, plain username, https://t.me/... public links
    Private invite links are handled separately via extract_invite_hash().
    """
    value = value.strip()

    # Numeric channel ID (e.g. -1001234567890)
    try:
        return int(value)
    except ValueError:
        pass

    # If it's a private invite link, return as-is (will be resolved async later)
    if extract_invite_hash(value):
        return value  # keep original, resolve later

    # t.me link → extract username (public channels only)
    if "t.me/" in value:
        return value.split("t.me/")[-1].strip("/")

    # @username → strip @
    if value.startswith("@"):
        return value[1:]

    # Plain username
    return value


async def resolve_channel(client_instance, value):
    """
    Resolve a channel value to a Telethon entity.
    Handles private invite links by checking/joining the chat.
    """
    # Check if it's a private invite link
    invite_hash = extract_invite_hash(str(value))
    if invite_hash:
        log.info("Detected private invite link, resolving hash: %s...", invite_hash[:8])
        try:
            result = await client_instance(CheckChatInviteRequest(invite_hash))
            if isinstance(result, ChatInviteAlready):
                # Already a member — get the entity from the chat object
                log.info("Already a member of this chat")
                return result.chat
            elif isinstance(result, ChatInvite):
                # Not yet a member — join first
                log.info("Not a member yet — joining channel: %s", result.title)
                updates = await client_instance(ImportChatInviteRequest(invite_hash))
                return updates.chats[0]
        except Exception as e:
            log.error("Failed to resolve invite link: %s", e)
            raise

    # For public channels / numeric IDs, use get_entity directly
    return await client_instance.get_entity(value)


source_channel = parse_channel(source_ch)
target_channel = parse_channel(target_ch)

# ─────────────────────────────────────────────
#  SAFETY SETTINGS
# ─────────────────────────────────────────────
BASE_DELAY      = 5       # Seconds between forward batches
MAX_DELAY       = 120     # Max backoff delay
JITTER_RANGE    = (1, 3)  # Random extra delay (seconds)
FORWARD_CHUNK   = 99      # Messages to forward at once (Telegram max is 100)
BATCH_SIZE      = 3       # Number of forward chunks before a long pause
BATCH_PAUSE     = 60      # Seconds to pause between batch groups
MAX_RETRIES     = 5       # Retries per forward before skipping

# ─────────────────────────────────────────────
#  SESSION & RESUME STATE
# ─────────────────────────────────────────────
SCRIPT_DIR     = Path(__file__).parent
SESSION_NAME   = str(SCRIPT_DIR / "tg_transfer_session")
PROGRESS_FILE  = SCRIPT_DIR / "transfer_progress.json"

# Graceful shutdown flag
_shutdown = False


def save_progress(last_msg_id: int, copied: int, skipped: int):
    """Save progress to disk so we can resume after interruption."""
    data = {
        "last_message_id": last_msg_id,
        "copied": copied,
        "skipped": skipped,
        "source": source_ch,
        "target": target_ch,
        "timestamp": datetime.now().isoformat(),
    }
    PROGRESS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_progress() -> dict | None:
    """Load previous progress if it matches the current source/target."""
    if not PROGRESS_FILE.exists():
        return None
    try:
        data = json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
        # Only resume if source and target match
        if data.get("source") == source_ch and data.get("target") == target_ch:
            return data
        log.info("Previous progress is for a different channel pair — starting fresh.")
        return None
    except Exception:
        return None


def clear_progress():
    """Remove the progress file after successful completion."""
    if PROGRESS_FILE.exists():
        PROGRESS_FILE.unlink()


# ─────────────────────────────────────────────
#  CLIENT
# ─────────────────────────────────────────────
client = TelegramClient(SESSION_NAME, api_id, api_hash)


# ─────────────────────────────────────────────
#  SMART DELAY
# ─────────────────────────────────────────────
async def smart_delay(base: float = BASE_DELAY):
    """Sleep with random jitter to look human."""
    jitter = random.uniform(*JITTER_RANGE)
    await asyncio.sleep(base + jitter)


# ─────────────────────────────────────────────
#  SEND SINGLE MESSAGE (no "Forwarded from" label)
# ─────────────────────────────────────────────
UNSUPPORTED_MEDIA = {
    "MessageMediaPoll",
    "MessageMediaContact",
    "MessageMediaGeo",
    "MessageMediaGeoLive",
    "MessageMediaVenue",
    "MessageMediaDice",
    "MessageMediaGame",
    "MessageMediaInvoice",
    "MessageMediaUnsupported",
}


async def send_single(message, target, attempt: int = 1) -> bool:
    """
    Send a single message to target via send_file/send_message (no forward label).
    Returns True on success, False if permanently skipped.
    """
    if _shutdown:
        return False

    try:
        if message.media:
            media_type = type(message.media).__name__
            if media_type in UNSUPPORTED_MEDIA:
                log.debug("Skipping unsupported media '%s' (msg id=%d)", media_type, message.id)
                return True  # not an error

            await client.send_file(
                target,
                message.media,
                caption=message.text or "",
            )
        elif message.text:
            await client.send_message(target, message.text)
        else:
            log.debug("Skipped empty/service message id=%d", message.id)

        return True

    except FloodWaitError as e:
        wait = e.seconds + 10
        log.warning(
            "FloodWait! Wait %ds. Sleeping %ds (attempt %d/%d)",
            e.seconds, wait, attempt, MAX_RETRIES,
        )
        await asyncio.sleep(wait)
        if attempt < MAX_RETRIES:
            return await send_single(message, target, attempt + 1)
        log.error("Giving up on msg id=%d after %d retries", message.id, MAX_RETRIES)
        return False

    except Exception as e:
        if SlowModeWaitError and isinstance(e, SlowModeWaitError):
            wait = e.seconds + 5
            log.warning("SlowMode active — waiting %ds", wait)
            await asyncio.sleep(wait)
            if attempt < MAX_RETRIES:
                return await send_single(message, target, attempt + 1)
            return False

        if isinstance(e, ChatWriteForbiddenError):
            log.error("No write permission in target channel. Aborting.")
            raise SystemExit(1)
        if isinstance(e, ChannelPrivateError):
            log.error("Cannot access channel (private/banned). Aborting.")
            raise SystemExit(1)
        if isinstance(e, UserBannedInChannelError):
            log.error("Your account is banned in the target channel. Aborting.")
            raise SystemExit(1)

        backoff = min(BASE_DELAY * (2 ** attempt), MAX_DELAY)
        log.warning("Error msg id=%d (attempt %d/%d): %s — retry in %ds",
                     message.id, attempt, MAX_RETRIES, e, backoff)
        await asyncio.sleep(backoff)
        if attempt < MAX_RETRIES:
            return await send_single(message, target, attempt + 1)
        log.error("Permanently skipped msg id=%d", message.id)
        return False


# ─────────────────────────────────────────────
#  SEND A BATCH (99 msgs back-to-back, tiny micro-delay)
# ─────────────────────────────────────────────
MICRO_DELAY = 0.5  # seconds between sends within a batch (just enough to avoid flood)


async def send_batch(messages, target):
    """
    Send a list of message objects one-by-one with only a tiny micro-delay.
    Returns (success_count, fail_count).
    """
    ok = 0
    fail = 0
    for msg in messages:
        if _shutdown:
            break
        success = await send_single(msg, target)
        if success:
            ok += 1
        else:
            fail += 1
        # tiny delay between individual sends to avoid instant flood triggers
        await asyncio.sleep(MICRO_DELAY)
    return ok, fail


# ─────────────────────────────────────────────
#  MAIN TRANSFER
# ─────────────────────────────────────────────
async def copy_messages():
    global _shutdown

    log.info("=" * 52)
    log.info("  Telegram Channel Transfer")
    log.info("=" * 52)
    log.info("Source : %s", source_ch)
    log.info("Target : %s", target_ch)
    log.info("Phone  : %s", phone)
    log.info("Batch  : %d msgs, %.1fs micro-delay between sends", FORWARD_CHUNK, MICRO_DELAY)
    log.info("Delay  : %ds + jitter %s between batches", BASE_DELAY, JITTER_RANGE)
    log.info("Pause  : every %d batches, %ds pause", BATCH_SIZE, BATCH_PAUSE)
    log.info("=" * 52)

    # --- Verify channels ---
    try:
        src = await resolve_channel(client, source_channel)
        log.info("Source channel OK: %s", getattr(src, "title", source_channel))
    except Exception as e:
        log.error("Cannot access source channel: %s", e)
        return

    try:
        tgt = await resolve_channel(client, target_channel)
        log.info("Target channel OK: %s", getattr(tgt, "title", target_channel))
    except Exception as e:
        log.error("Cannot access target channel: %s", e)
        return

    resolved_source = src
    resolved_target = tgt

    # --- Resume support ---
    resume = load_progress()
    resume_after_id = None
    copied = 0
    skipped_count = 0

    if resume:
        resume_after_id = resume["last_message_id"]
        copied = resume.get("copied", 0)
        skipped_count = resume.get("skipped", 0)
        log.info(
            "Resuming from message id=%d (%d copied, %d skipped previously)",
            resume_after_id, copied, skipped_count,
        )

    start_time = datetime.now()
    past_resume_point = resume_after_id is None

    # Collect full message objects into batches of FORWARD_CHUNK
    batch_buffer = []       # Current batch of message objects
    chunks_sent = 0

    async for message in client.iter_messages(resolved_source, reverse=True):
        if _shutdown:
            log.info("Shutdown requested — saving progress...")
            save_progress(message.id, copied, skipped_count)
            break

        # Skip already processed messages (resume)
        if not past_resume_point:
            if message.id <= resume_after_id:
                continue
            else:
                past_resume_point = True
                log.info("Reached resume point — continuing transfer...")

        # Buffer the full message object
        batch_buffer.append(message)

        # When buffer is full, send the whole batch
        if len(batch_buffer) >= FORWARD_CHUNK:
            if _shutdown:
                save_progress(batch_buffer[-1].id, copied, skipped_count)
                break

            log.info("Sending batch of %d messages (ids %d–%d)...",
                     len(batch_buffer), batch_buffer[0].id, batch_buffer[-1].id)

            ok, fail = await send_batch(batch_buffer, resolved_target)
            copied += ok
            skipped_count += fail
            log.info("✓ Batch done — %d sent, %d failed | total: %d copied", ok, fail, copied)

            # Save progress
            save_progress(batch_buffer[-1].id, copied, skipped_count)
            batch_buffer = []
            chunks_sent += 1

            # Progress stats
            elapsed = (datetime.now() - start_time).total_seconds()
            rate = copied / (elapsed / 60) if elapsed > 0 else 0
            log.info("Progress: %d copied, %d skipped | %.1f msgs/min", copied, skipped_count, rate)

            # Long pause every BATCH_SIZE chunks
            if chunks_sent % BATCH_SIZE == 0:
                log.info("Batch group pause: sleeping %ds...", BATCH_PAUSE)
                await asyncio.sleep(BATCH_PAUSE)
            else:
                await smart_delay()

    # --- Send remaining messages in the buffer ---
    if batch_buffer and not _shutdown:
        log.info("Sending final batch of %d messages (ids %d–%d)...",
                 len(batch_buffer), batch_buffer[0].id, batch_buffer[-1].id)

        ok, fail = await send_batch(batch_buffer, resolved_target)
        copied += ok
        skipped_count += fail
        save_progress(batch_buffer[-1].id, copied, skipped_count)

    # --- Summary ---
    elapsed = (datetime.now() - start_time).total_seconds()
    log.info("=" * 52)
    log.info("  TRANSFER COMPLETE")
    log.info("=" * 52)
    log.info("Copied  : %d", copied)
    log.info("Skipped : %d", skipped_count)
    log.info("Duration: %.1f minutes", elapsed / 60)
    log.info("=" * 52)

    if not _shutdown:
        clear_progress()
        log.info("Progress file cleared (transfer finished).")


# ─────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────
async def main():
    """Authenticate and run the transfer."""

    # Connect & authenticate with phone number
    await client.start(phone=phone)
    log.info("Logged in successfully!")

    # Register Ctrl+C handler for graceful shutdown
    def handle_signal(*_):
        global _shutdown
        _shutdown = True
        log.info("Ctrl+C detected — finishing current message then stopping...")

    try:
        signal.signal(signal.SIGINT, handle_signal)
    except (OSError, ValueError):
        pass  # signal handling may not work on all platforms

    try:
        await copy_messages()
    finally:
        await client.disconnect()

    # Session cleanup reminder
    session_file = SESSION_NAME + ".session"
    if os.path.exists(session_file):
        log.warning(
            "Session file '%s' exists on disk. "
            "Delete it when you're done to protect your account:",
            session_file,
        )
        log.warning("  del \"%s\"", session_file)


if __name__ == "__main__":
    asyncio.run(main())
