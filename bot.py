import asyncio
import logging
import os
import signal
from datetime import datetime, timedelta
import uuid
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import psutil
from pyrogram import Client, filters, idle
from pyrogram.handlers import MessageHandler
from pyrogram.errors import FloodWait, ChannelPrivate, MessageDeleteForbidden
from config import API_ID, API_HASH, BOT_TOKEN, CHAT_IDS, ID_DUR

# -------------------------
# Basic logging
# -------------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - 🤖 %(name)s - %(levelname)s - ✨ %(message)s'
)
logger = logging.getLogger(__name__)
start_time = datetime.now()

# Global runtime objects
scheduler: AsyncIOScheduler | None = None
app: Client | None = None
shutdown_event: asyncio.Event | None = None
OWNER_ID: int | None = None


async def process_delete(chat_id: int, msg_id: int):
    """Delete a single message with robust error handling and FloodWait support."""
    try:
        await app.delete_messages(chat_id, msg_id)
        logger.info(f"🗑️ Deleted message {msg_id} from {chat_id}")
    except (ChannelPrivate, MessageDeleteForbidden):
        logger.warning(f"🔒 Cannot delete message {msg_id} in {chat_id} - no permission")
    except FloodWait as e:
        wait_time = int(e.value)
        logger.warning(f"⏳ FloodWait {wait_time}s for {chat_id}:{msg_id} - sleeping then retrying")
        await asyncio.sleep(wait_time)
        await process_delete(chat_id, msg_id)
    except Exception as e:
        logger.error(f"💥 Failed to delete {chat_id}:{msg_id} - {e}")


def schedule_deletion(chat_id: int, msg_id: int, duration: int):
    """Schedule deletion of a message after `duration` seconds using the AsyncIOScheduler.

    We schedule `asyncio.create_task(process_delete(...))` to run at the target time so the
    coroutine executes on the event loop.
    """
    if not duration or duration <= 0:
        return

    job_id = f"delete_{uuid.uuid4().hex}"
    run_at = datetime.now() + timedelta(seconds=duration)

    # schedule create_task(coroutine) at run_at
    coroutine = process_delete(chat_id, msg_id)
    try:
        scheduler.add_job(asyncio.create_task, 'date', run_date=run_at, args=[coroutine], id=job_id)
        logger.debug(f"⏰ Scheduled deletion of {msg_id} in {duration}s (job={job_id})")
    except Exception as e:
        logger.error(f"❌ Failed to schedule deletion job for {chat_id}:{msg_id} - {e}")


async def handle_messages(client, message, is_new=True, duration=None):
    """Handler for incoming messages in monitored chats.

    For mode A (bot-only) we only schedule deletion for *new* incoming messages.
    """
    try:
        # Determine duration: explicit passed -> else from ID_DUR mapping
        duration = duration or ID_DUR.get(message.chat.id)
        if duration:
            schedule_deletion(message.chat.id, message.id, duration)
    except Exception as e:
        logger.error(f"❌ Error in handle_messages: {e}")


async def handle_bot_commands(client, message):
    global OWNER_ID
    try:
        cmd = message.command[0].lower() if message.command else ""

        if cmd == "start":
            # Auto-set owner to the first user who /start's the bot privately
            if message.chat.type == "private" and OWNER_ID is None:
                OWNER_ID = message.from_user.id
                logger.info(f"🎯 Owner ID auto-set to: {OWNER_ID} (@{message.from_user.username or 'Unknown'})")

            await message.reply_text(
                "👋 **Hello! I'm your Group Auto-Cleaner Bot.**\n\n"
                "🗑️ I automatically **delete new messages** after a set time.\n"
                "✨ Keep your groups **clean, clutter-free, and spam-free!**"
            )

        elif cmd == "status":
            uptime = str(datetime.now() - start_time).split('.')[0]
            active_jobs = len(scheduler.get_jobs()) if scheduler else 0

            cpu_percent = psutil.cpu_percent(interval=1)
            per_core = psutil.cpu_percent(interval=1, percpu=True)
            memory = psutil.virtual_memory()
            swap = psutil.swap_memory()
            disk = psutil.disk_usage('/')
            processes = len(psutil.pids())

            status_text = (
                f"📊 **Bot Status**\n\n"
                f"⏳ Uptime: `{uptime}`\n"
                f"⚙️ Active Jobs: `{active_jobs}`\n"
                f"📌 Monitored Chats: `{len(CHAT_IDS)}`\n"
                f"🛡️ Status: `🟢 Running`\n\n"
                f"💻 **System Stats**\n"
                f"🔹 CPU Usage: `{cpu_percent}%`\n"
                f"🔹 Per-Core: `{per_core}`\n"
                f"🔹 RAM: `{memory.percent}% of {round(memory.total / (1024**3), 2)} GB`\n"
                f"🔹 Swap: `{swap.percent}% of {round(swap.total / (1024**3), 2)} GB`\n"
                f"🔹 Disk: `{disk.percent}% of {round(disk.total / (1024**3), 2)} GB`\n"
                f"🔹 Processes: `{processes}`"
            )

            await message.reply_text(status_text)

        elif cmd == "ping":
            start = datetime.now()
            msg = await message.reply_text("🏓 Pinging...")
            latency = (datetime.now() - start).total_seconds()
            await msg.edit_text(f"🏓 **Pong!**\n⏱️ Latency: `{latency:.3f}s`")

        elif cmd == "chats":
            if not CHAT_IDS:
                await message.reply_text("⚠️ No chats are being monitored.")
            else:
                text = "📌 **Monitored Chats:**\n" + "\n".join([f"🔐 `{cid}`" for cid in CHAT_IDS])
                await message.reply_text(text)

        else:
            # Unknown command - ignore or show help
            pass

    except Exception as e:
        logger.error(f"❌ Error in handle_bot_commands: {e}")


async def delete_messages():
    """Called periodically by the scheduler. For BOT-ONLY mode it will only report queue size.

    Older-message cleanup requires a user session and therefore is not supported in mode A.
    """
    try:
        queue_size = sum(1 for job in scheduler.get_jobs() if getattr(job, 'id', '').startswith('delete_'))
        logger.info(f"🧹 Periodic cleanup tick — {queue_size} deletions scheduled")
    except Exception as e:
        logger.error(f"❌ delete_messages() failed: {e}")


async def heartbeat():
    while not shutdown_event.is_set():
        try:
            uptime = str(datetime.now() - start_time).split('.')[0]
            active_jobs = len(scheduler.get_jobs()) if scheduler else 0
            logger.info(f"💓 Heartbeat | ⏳ Up: {uptime} | 🛠️ Jobs: {active_jobs}")
            await asyncio.sleep(300)
        except Exception as e:
            logger.error(f"💔 Heartbeat failed: {e}")
            await asyncio.sleep(60)


async def main():
    global app, scheduler, shutdown_event

    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()

    # Unix signal handlers
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, lambda: shutdown_event.set())
        except NotImplementedError:
            # Some platforms (like Windows) don't support loop.add_signal_handler
            pass

    # Scheduler
    scheduler = AsyncIOScheduler(event_loop=loop)
    scheduler.start()
    # Regular tick — used primarily for logging/housekeeping in BOT-only mode
    scheduler.add_job(delete_messages, 'interval', minutes=4, id="regular_cleanup")
    logger.info("⏰ Scheduler started with regular cleanup every 4 minutes.")

    # Bot client (BOT only)
    app = Client("AutoWiperBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

    # Register handlers
    app.add_handler(MessageHandler(
        handle_bot_commands,
        filters=filters.command(["start", "status", "ping", "chats"]) & filters.private
    ))

    # Auto-delete for new messages in monitored chats
    app.add_handler(MessageHandler(
        handle_messages,
        filters=filters.chat(CHAT_IDS) & ~filters.pinned_message
    ))

    # Start bot
    await app.start()
    logger.info("🚀 AutoWiperBot (BOT-only) started")

    # Start heartbeat
    hb_task = asyncio.create_task(heartbeat())

    try:
        # Initial cleanup tick (will only report queue size)
        await delete_messages()

        if not CHAT_IDS:
            logger.warning("⚠️ No CHAT_IDS configured — bot will not monitor any chats.")

        # Keep running until a shutdown signal
        await idle()
        await shutdown_event.wait()

    except Exception as e:
        logger.error(f"💥 Critical error in main loop: {e}")

    finally:
        logger.info("🛑 Shutting down gracefully...")

        if hb_task and not hb_task.done():
            hb_task.cancel()
            try:
                await hb_task
            except asyncio.CancelledError:
                pass

        if app and app.is_connected:
            await app.stop()

        if scheduler and scheduler.running:
            scheduler.shutdown()
            logger.info("📋 Scheduler stopped.")

        logger.info("✅ Bot stopped gracefully. Goodbye! 👋")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Bot stopped manually by user.")
    

