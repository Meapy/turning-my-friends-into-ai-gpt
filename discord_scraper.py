import os
import json
import time
try:
    import ujson as _ujson
except Exception:
    _ujson = None
try:
    import aiofiles
except Exception:
    aiofiles = None
import asyncio
import argparse
import logging
from dotenv import load_dotenv
import discord


def make_parser():
    p = argparse.ArgumentParser(description='Discord message scraper with resume support')
    p.add_argument('--token', help='Discord bot token (overrides .env)')
    p.add_argument('--guild', type=int, help='Guild ID')
    p.add_argument('--user', type=int, help='Target user ID')
    p.add_argument('--out', default='messages.jsonl', help='Output JSONL file')
    p.add_argument('--state', default='.scrape_state.json', help='State/checkpoint file')
    p.add_argument('--checkpoint-every', type=int, default=50, help='Checkpoint frequency (messages)')
    p.add_argument('--verbose', action='store_true')
    p.add_argument('--force-rewrite', action='store_true', help='If set, ignore existing output and rewrite (no duplicate checks)')
    p.add_argument('--concurrency', type=int, default=3, help='Number of channels to scrape concurrently')
    p.add_argument('--durable-state', action='store_true', help='Perform fsync when saving state (slower but safer)')
    p.add_argument('--precount', action='store_true', help='Pre-count messages to provide a percentage-complete estimate (may be slow)')
    return p


def load_state(path):
    try:
        with open(path, 'r', encoding='utf-8') as sf:
            return json.load(sf)
    except Exception:
        return {}


def save_state(path, state, durable=False):
    dirpath = os.path.dirname(path) or '.'
    try:
        os.makedirs(dirpath, exist_ok=True)
    except Exception:
        pass
    base = os.path.basename(path).lstrip('.') or 'state'
    tmp = os.path.join(dirpath, f"{base}.{os.getpid()}.tmp")

    try:
        try:
            # write state to temp
            with open(tmp, 'w', encoding='utf-8') as sf:
                json.dump(state, sf)
                sf.flush()
                try:
                    if durable:
                        os.fsync(sf.fileno())
                except Exception:
                    pass

            # attempt atomic replace with retries + exponential backoff
            backoff = 0.1
            for attempt in range(5):
                try:
                    os.replace(tmp, path)
                    return True
                except PermissionError as pe:
                    logging.warning('os.replace PermissionError (attempt %d): %s', attempt + 1, pe)
                    time.sleep(backoff)
                    backoff = min(1.0, backoff * 2)
                    continue

            # fallback: try removing destination then single replace
            try:
                if os.path.exists(path):
                    os.remove(path)
            except Exception:
                pass
            try:
                os.replace(tmp, path)
                return True
            except Exception as e2:
                logging.warning('Fallback replace failed: %s', e2)

            # last resort: write directly
            try:
                with open(path, 'w', encoding='utf-8') as sf2:
                    json.dump(state, sf2)
                    sf2.flush()
                    try:
                        if durable:
                            os.fsync(sf2.fileno())
                    except Exception:
                        pass
                return True
            except Exception as e3:
                logging.warning('Failed to write state file directly: %s', e3)
                return False
        except Exception as e:
            logging.warning('Unexpected error saving state: %s', e)
            return False
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass


def make_client(intents):
    return discord.Client(intents=intents)


async def run_scraper(token, guild_id, target_user_id, out_path, state_path, checkpoint_every, force_rewrite=False, concurrency=3, durable=False, precount=False):
    # Ensure logging to console so progress logs are visible even if the caller
    # did not configure logging. Respect existing handlers but add a default
    # StreamHandler at INFO level when none are configured.
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s: %(message)s'))
        root.addHandler(handler)
        root.setLevel(logging.INFO)
    else:
        # If handlers already exist but the level is higher than INFO, lower it
        # so progress/info logs are still emitted. Do not force DEBUG.
        try:
            if root.level > logging.INFO:
                root.setLevel(logging.INFO)
        except Exception:
            pass

    intents = discord.Intents.default()
    intents.message_content = True
    intents.guilds = True
    intents.messages = True
    intents.members = True

    client = make_client(intents)

    # Shared state so we can save on graceful shutdown
    messages_dir = os.path.join(os.path.dirname(out_path), 'messages')
    state_file = state_path
    if state_file == '.scrape_state.json':
        state_file = os.path.join(messages_dir, '.scrape_state.json')

    state = {}
    count = 0

    # Shared progress counters
    progress = {
        'total': 0,
        'per_channel': {},
        'start_time': None,
    'active_channels': set(),
    }

    async def reporter():
        # Periodically report messages/min and totals
        while True:
            await asyncio.sleep(60)
            elapsed = 0
            if progress['start_time']:
                elapsed = max(1, int(asyncio.get_event_loop().time() - progress['start_time']))
            total = progress['total']
            m_per_min = (total / elapsed) * 60 if elapsed > 0 else 0
            active = list(progress.get('active_channels') or [])[:5]
            active_str = ','.join(active)
            if progress.get('total_estimate'):
                pct = (total / max(1, progress['total_estimate'])) * 100
                logging.info('Progress: total=%d messages (%.1f%%), rate=%.1f msg/min, channels=%d, active=%s', total, pct, m_per_min, len(progress['per_channel']), active_str)
            else:
                logging.info('Progress: total=%d messages, rate=%.1f msg/min, channels=%d, active=%s', total, m_per_min, len(progress['per_channel']), active_str)


    @client.event
    async def on_ready():
        # allow modifying the outer state and count
        nonlocal state, count
        logging.info('Logged in as %s (ID: %s)', client.user, client.user.id)
        guild = client.get_guild(guild_id)
        if guild is None:
            logging.error('Guild not found. Is the bot in the guild and is the GUILD ID correct?')
            await client.close()
            return

        # ensure messages directory
        os.makedirs(messages_dir, exist_ok=True)

        # load state
        state = load_state(state_file)
        count = 0

        # Optional precount to estimate total messages (expensive). Run in background
        # while scraping so percent updates progressively instead of blocking start.
        collect_task = None
        if precount:
            logging.info('Precount enabled: starting background precount (this may take a while)')
            estimate = 0
            pre_sem = asyncio.Semaphore(max(1, concurrency))

            async def precount_channel(ch):
                c = 0
                async with pre_sem:
                    try:
                        async for m in ch.history(limit=None, oldest_first=True):
                            if m.author and m.author.id == target_user_id:
                                c += 1
                    except Exception:
                        logging.debug('Precount failed for channel %s', ch.id)
                return c

            tasks_pc = [asyncio.create_task(precount_channel(ch)) for ch in guild.text_channels if (ch.name or '').lower() != 'log']

            async def collect_pc():
                nonlocal estimate
                for t in asyncio.as_completed(tasks_pc):
                    try:
                        got = await t
                        estimate += got
                        progress['total_estimate'] = estimate
                        logging.info('Precount progress: estimated %d messages so far', estimate)
                    except Exception:
                        pass

            collect_task = asyncio.create_task(collect_pc())

        sem = asyncio.Semaphore(concurrency)

        async def process_channel(channel):
            nonlocal count, state
            logging.info('Starting channel %s (%s)', channel.name, channel.id)
            # register active channel for reporter
            try:
                progress['active_channels'].add(channel.name)
            except Exception:
                pass
            async with sem:
                chan_id = str(channel.id)
                last_id = state.get(chan_id)
                after = None
                if last_id:
                    try:
                        after = discord.Object(id=int(last_id))
                    except Exception:
                        after = None

                # per-channel output file: prefer sanitized channel name
                def sanitize(name):
                    import re
                    s = re.sub(r'[^A-Za-z0-9-_]', '-', name)
                    s = s.strip('-') or 'channel'
                    return s[:100]

                base_name = sanitize(channel.name)
                channel_file = os.path.join(messages_dir, f"{base_name}.jsonl")
                # avoid collisions
                if os.path.exists(channel_file):
                    try:
                        with open(channel_file, 'r', encoding='utf-8') as peekf:
                            first = peekf.readline().strip()
                            if first:
                                try:
                                    obj = json.loads(first)
                                    existing_chan_id = obj.get('channel', {}).get('id')
                                    if str(existing_chan_id) != str(channel.id):
                                        channel_file = os.path.join(messages_dir, f"{base_name}-{channel.id}.jsonl")
                                except Exception:
                                    channel_file = os.path.join(messages_dir, f"{base_name}-{channel.id}.jsonl")
                    except Exception:
                        channel_file = os.path.join(messages_dir, f"{base_name}-{channel.id}.jsonl")

                existing_ids = set()
                if os.path.exists(channel_file) and not force_rewrite:
                    try:
                        with open(channel_file, 'r', encoding='utf-8') as rf:
                            for line in rf:
                                line = line.strip()
                                if not line:
                                    continue
                                try:
                                    obj = json.loads(line)
                                    if 'id' in obj:
                                        existing_ids.add(int(obj['id']))
                                except Exception:
                                    continue
                    except Exception as e:
                        logging.warning('Failed to read existing channel file %s: %s', channel_file, e)

                mode = 'a' if not force_rewrite else 'w'
                serializer = (_ujson.dumps if _ujson is not None else json.dumps)
                FLUSH_BATCH = 50
                buffer = []
                loop = asyncio.get_event_loop()

                try:
                    if aiofiles is not None:
                        afp = await aiofiles.open(channel_file, mode, encoding='utf-8')
                        try:
                            async for msg in channel.history(limit=None, oldest_first=True, after=after):
                                if not (msg.author and msg.author.id == target_user_id):
                                    continue

                                if not force_rewrite and int(msg.id) in existing_ids:
                                    continue

                                payload = {
                                    'id': msg.id,
                                    'content': msg.content,
                                    'author': {'id': msg.author.id, 'name': str(msg.author)},
                                    'channel': {'id': channel.id, 'name': channel.name},
                                    'created_at': msg.created_at.isoformat() if msg.created_at else None,
                                    'attachments': [a.url for a in msg.attachments]
                                }
                                line = serializer(payload, ensure_ascii=False) + '\n'
                                buffer.append(line)
                                count += 1
                                existing_ids.add(int(msg.id))
                                state[chan_id] = msg.id

                                # update shared progress
                                progress['total'] += 1
                                progress['per_channel'].setdefault(chan_id, 0)
                                progress['per_channel'][chan_id] += 1

                                if len(buffer) >= FLUSH_BATCH:
                                    await afp.write(''.join(buffer))
                                    try:
                                        await afp.flush()
                                    except Exception:
                                        pass
                                    buffer.clear()

                                if count % checkpoint_every == 0:
                                    # schedule save in executor to avoid blocking and ensure exceptions don't propagate
                                    try:
                                        await loop.run_in_executor(None, save_state, state_file, state, durable)
                                    except Exception as e:
                                        logging.warning('save_state failed in executor: %s', e)
                            await asyncio.sleep(0)
                        finally:
                            if buffer:
                                try:
                                    await afp.write(''.join(buffer))
                                    try:
                                        await afp.flush()
                                    except Exception:
                                        pass
                                except Exception:
                                    logging.warning('Failed to flush buffer for %s', channel_file)
                            await afp.close()
                    else:
                        f = open(channel_file, mode, encoding='utf-8')
                        try:
                            async for msg in channel.history(limit=None, oldest_first=True, after=after):
                                if not (msg.author and msg.author.id == target_user_id):
                                    continue

                                if not force_rewrite and int(msg.id) in existing_ids:
                                    continue

                                payload = {
                                    'id': msg.id,
                                    'content': msg.content,
                                    'author': {'id': msg.author.id, 'name': str(msg.author)},
                                    'channel': {'id': channel.id, 'name': channel.name},
                                    'created_at': msg.created_at.isoformat() if msg.created_at else None,
                                    'attachments': [a.url for a in msg.attachments]
                                }
                                line = serializer(payload, ensure_ascii=False) + '\n'
                                buffer.append(line)
                                count += 1
                                existing_ids.add(int(msg.id))
                                state[chan_id] = msg.id

                                # update shared progress
                                progress['total'] += 1
                                progress['per_channel'].setdefault(chan_id, 0)
                                progress['per_channel'][chan_id] += 1

                                if len(buffer) >= FLUSH_BATCH:
                                    await loop.run_in_executor(None, f.write, ''.join(buffer))
                                    await loop.run_in_executor(None, f.flush)
                                    buffer.clear()

                                if count % checkpoint_every == 0:
                                    await loop.run_in_executor(None, save_state, state_file, state, durable)
                            await asyncio.sleep(0)
                        finally:
                            if buffer:
                                try:
                                    await loop.run_in_executor(None, f.write, ''.join(buffer))
                                    await loop.run_in_executor(None, f.flush)
                                except Exception:
                                    logging.warning('Failed to flush buffer for %s', channel_file)
                            try:
                                f.close()
                            except Exception:
                                pass
                except Exception:
                    logging.exception('Skipping channel %s due to error', channel.name)

                logging.info('Finished channel %s (%s): collected %d messages', channel.name, channel.id, progress['per_channel'].get(chan_id, 0))
                try:
                    progress['active_channels'].discard(channel.name)
                except Exception:
                    pass

        # create tasks for channels and a reporter
        progress['start_time'] = asyncio.get_event_loop().time()
        rep_task = asyncio.create_task(reporter())
        tasks = [asyncio.create_task(process_channel(ch)) for ch in guild.text_channels if (ch.name or '').lower() != 'log']
        # run with bounded concurrency
        await asyncio.gather(*tasks)
        rep_task.cancel()
        try:
            save_state(state_file, state, durable=durable)
        except Exception as e:
            logging.warning('save_state at end failed: %s', e)
        # ensure background precount finishes or is cancelled
        if collect_task is not None:
            try:
                collect_task.cancel()
                await collect_task
            except Exception:
                pass
        logging.info('Done. Collected %s new messages to %s', count, out_path)
        await client.close()

    # start the client and wait until it finishes (it will close itself after scraping)
    try:
        await client.start(token)
    except asyncio.CancelledError:
        logging.info('Shutdown requested (cancelled).')
        raise
    finally:
        # Attempt graceful shutdown: save current state and close client
        try:
            save_state(state_file, state, durable=durable)
        except Exception as e:
            logging.warning('Failed to save state during shutdown: %s', e)
        try:
            await client.close()
        except Exception:
            pass


def main():
    parser = make_parser()
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')
    else:
        # Make INFO visible by default so progress logs are shown without --verbose.
        # Users can still enable more verbose output with --verbose.
        logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')

    load_dotenv()
    token = args.token or os.getenv('DISCORD_TOKEN')
    guild = args.guild or int(os.getenv('GUILD_ID') or 0)
    user = args.user or int(os.getenv('TARGET_USER_ID') or 0)

    if not token or not guild or not user:
        parser.print_help()
        logging.error('Missing required parameters: token, guild, and user must be set (via args or .env)')
        return

    try:
        asyncio.run(run_scraper(token, guild, user, args.out, args.state, args.checkpoint_every, force_rewrite=args.force_rewrite, concurrency=args.concurrency, durable=args.durable_state, precount=args.precount))
    except KeyboardInterrupt:
        logging.info('Interrupted by user')


if __name__ == '__main__':
    main()
