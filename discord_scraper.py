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
    return p


def load_state(path):
    try:
        with open(path, 'r', encoding='utf-8') as sf:
            return json.load(sf)
    except Exception:
        return {}


def save_state(path, state):
    try:
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as sf:
            json.dump(state, sf)
        os.replace(tmp, path)
    except Exception as e:
        logging.warning('Failed to save state: %s', e)


def make_client(intents):
    return discord.Client(intents=intents)


async def run_scraper(token, guild_id, target_user_id, out_path, state_path, checkpoint_every):
    intents = discord.Intents.default()
    intents.message_content = True
    intents.guilds = True
    intents.messages = True
    intents.members = True

    client = make_client(intents)

    @client.event
    async def on_ready():
        logging.info('Logged in as %s (ID: %s)', client.user, client.user.id)
        guild = client.get_guild(guild_id)
        if guild is None:
            logging.error('Guild not found. Is the bot in the guild and is the GUILD ID correct?')
            await client.close()
            return

        state = load_state(state_path)
        count = 0

        # append mode
        with open(out_path, 'a', encoding='utf-8') as f:
            for channel in guild.text_channels:
                last_id = state.get(str(channel.id))
                after = None
                if last_id:
                    try:
                        after = discord.Object(id=int(last_id))
                    except Exception:
                        after = None

                try:
                    async for msg in channel.history(limit=None, oldest_first=True, after=after):
                        if msg.author and msg.author.id == target_user_id:
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
                            state[str(channel.id)] = msg.id

                            if count % checkpoint_every == 0:
                                save_state(state_path, state)
                    await asyncio.sleep(1)
                except Exception as e:
                    logging.exception('Skipping channel %s due to error', channel.name)

        save_state(state_path, state)
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
        asyncio.run(run_scraper(token, guild, user, args.out, args.state, args.checkpoint_every))
    except KeyboardInterrupt:
        logging.info('Interrupted by user')


if __name__ == '__main__':
    main()
