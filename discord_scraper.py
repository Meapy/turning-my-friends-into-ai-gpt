import os
import json
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
    return p


def load_state(path):
    try:
        with open(path, 'r', encoding='utf-8') as sf:
            return json.load(sf)
    except Exception:
        return {}


def save_state(path, state):
    try:
        # Write to a unique temp file in the same directory to reduce rename issues on Windows
        dirpath = os.path.dirname(path) or '.'
        try:
            os.makedirs(dirpath, exist_ok=True)
        except Exception:
            pass
        tmp = os.path.join(dirpath, f".{os.path.basename(path)}.{os.getpid()}.tmp")
        try:
            with open(tmp, 'w', encoding='utf-8') as sf:
                json.dump(state, sf)
                sf.flush()
                try:
                    os.fsync(sf.fileno())
                except Exception:
                    # fsync may fail on some platforms; ignore
                    pass

            try:
                os.replace(tmp, path)
                return
            except PermissionError as pe:
                logging.warning('os.replace PermissionError, will try fallback: %s', pe)
                # Try removing destination then replace
                try:
                    if os.path.exists(path):
                        os.remove(path)
                except Exception:
                    pass
                try:
                    os.replace(tmp, path)
                    return
                except Exception as e2:
                    logging.warning('Fallback replace failed: %s', e2)

            # Last resort: write directly to destination
            try:
                with open(path, 'w', encoding='utf-8') as sf2:
                    json.dump(state, sf2)
                    sf2.flush()
                    try:
                        os.fsync(sf2.fileno())
                    except Exception:
                        pass
                return
            except Exception as e3:
                logging.warning('Failed to write state file directly: %s', e3)
        finally:
            # Clean up temp if it still exists
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass
    except Exception as e:
        logging.warning('Failed to save state: %s', e)


def make_client(intents):
    return discord.Client(intents=intents)


async def run_scraper(token, guild_id, target_user_id, out_path, state_path, checkpoint_every, force_rewrite=False, concurrency=3):
    # Ensure logging to console so progress logs are visible even if the caller
    # did not configure logging. Respect existing handlers but add a default
    # StreamHandler at INFO level when none are configured.
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s: %(message)s'))
        root.addHandler(handler)
        root.setLevel(logging.INFO)

    intents = discord.Intents.default()
    intents.message_content = True
    intents.guilds = True
    intents.messages = True
    intents.members = True

    client = make_client(intents)

    # Shared progress counters
    progress = {
        'total': 0,
        'per_channel': {},
        'start_time': None,
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
            logging.info('Progress: total=%d messages, rate=%.1f msg/min, channels=%d', total, m_per_min, len(progress['per_channel']))


    @client.event
    async def on_ready():
        logging.info('Logged in as %s (ID: %s)', client.user, client.user.id)
        guild = client.get_guild(guild_id)
        if guild is None:
            logging.error('Guild not found. Is the bot in the guild and is the GUILD ID correct?')
            await client.close()
            return

        # ensure messages directory
        messages_dir = os.path.join(os.path.dirname(out_path), 'messages')
        os.makedirs(messages_dir, exist_ok=True)

        # determine state file location (do not reassign outer variable)
        state_file = state_path
        if state_file == '.scrape_state.json':
            state_file = os.path.join(messages_dir, '.scrape_state.json')

        state = load_state(state_file)
        count = 0

        sem = asyncio.Semaphore(concurrency)

        async def process_channel(channel):
            nonlocal count, state
            logging.info('Starting channel %s (%s)', channel.name, channel.id)
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
                    # keep alphanum, dash, underscore; replace others with -
                    import re
                    s = re.sub(r'[^A-Za-z0-9-_]', '-', name)
                    s = s.strip('-') or 'channel'
                    return s[:100]

                base_name = sanitize(channel.name)
                channel_file = os.path.join(messages_dir, f"{base_name}.jsonl")
                # avoid collisions: if file exists but belongs to different channel id, append id
                if os.path.exists(channel_file):
                    try:
                        # peek first line to see if channel id matches
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

                # load existing ids for this channel
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
                try:
                    with open(channel_file, mode, encoding='utf-8') as f:
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
                            f.write(json.dumps(payload, ensure_ascii=False) + '\n')
                            count += 1
                            existing_ids.add(int(msg.id))
                            state[chan_id] = msg.id

                            # update shared progress
                            progress['total'] += 1
                            progress['per_channel'].setdefault(chan_id, 0)
                            progress['per_channel'][chan_id] += 1

                            if count % checkpoint_every == 0:
                                save_state(state_file, state)
                        await asyncio.sleep(1)
                except Exception:
                    logging.exception('Skipping channel %s due to error', channel.name)
            logging.info('Finished channel %s (%s): collected %d messages', channel.name, channel.id, progress['per_channel'].get(chan_id, 0))

        # create tasks for channels and a reporter
        progress['start_time'] = asyncio.get_event_loop().time()
        rep_task = asyncio.create_task(reporter())
        tasks = [asyncio.create_task(process_channel(ch)) for ch in guild.text_channels]
        # run with bounded concurrency
        await asyncio.gather(*tasks)
        rep_task.cancel()

        save_state(state_file, state)
        logging.info('Done. Collected %s new messages to %s', count, out_path)
        await client.close()

    # start the client and wait until it finishes (it will close itself after scraping)
    await client.start(token)


def main():
    parser = make_parser()
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')
    else:
        logging.basicConfig(level=logging.WARNING, format='%(asctime)s %(levelname)s: %(message)s')

    load_dotenv()
    token = args.token or os.getenv('DISCORD_TOKEN')
    guild = args.guild or int(os.getenv('GUILD_ID') or 0)
    user = args.user or int(os.getenv('TARGET_USER_ID') or 0)

    if not token or not guild or not user:
        parser.print_help()
        logging.error('Missing required parameters: token, guild, and user must be set (via args or .env)')
        return

    try:
        asyncio.run(run_scraper(token, guild, user, args.out, args.state, args.checkpoint_every, force_rewrite=args.force_rewrite, concurrency=args.concurrency))
    except KeyboardInterrupt:
        logging.info('Interrupted by user')


if __name__ == '__main__':
    main()
