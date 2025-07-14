import discord

from discord.ext import commands

import json

import requests


API_KEY = "0NSOZ14L5PX71PBNIQ5OIMNVSOVWHAMQ"

OWNER_ID = 1387901242239352892  # Replace with your Discord ID (NO QUOTES)

intents = discord.Intents.default()

intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

EMAIL_FILE = "emails.json"

BLACKLIST_FILE = "blacklist.json"

STATS_FILE = "stats.json"

def load_json(file):

    try:

        with open(file, "r") as f:

            return json.load(f)

    except:

        return {} if file != "blacklist.json" else []

def save_json(file, data):

    with open(file, "w") as f:

        json.dump(data, f, indent=2)

def is_blacklisted(user_id):

    blacklist = load_json(BLACKLIST_FILE)

    return str(user_id) in blacklist

def is_owner(ctx):

    return ctx.author.id == OWNER_ID

@bot.event

async def on_ready():

    print(f"✅ Logged in as {bot.user}")

@bot.command()

async def generate_email(ctx):

    if is_blacklisted(ctx.author.id):

        await ctx.send("❌ You are blacklisted.")

        return

    headers = {

        "Authorization": f"Bearer {API_KEY}",

        "Content-Type": "application/json"

    }

    res = requests.post("https://leveragers.xyz/api/email", headers=headers, json={"domain": "leveragers.shop"})

    if res.ok:

        email = res.json().get("email")

        emails = load_json(EMAIL_FILE)

        user_id = str(ctx.author.id)

        emails.setdefault(user_id, []).append(email)

        save_json(EMAIL_FILE, emails)

        stats = load_json(STATS_FILE)

        stats["emails_generated"] = stats.get("emails_generated", 0) + 1

        save_json(STATS_FILE, stats)

        await ctx.author.send(f"📧 Your temp email: `{email}`")

        await ctx.send("✅ Email sent to your DMs.")

    else:

        await ctx.send("⚠️ Failed to generate email.")

@bot.command()

async def my_email(ctx):

    user_id = str(ctx.author.id)

    emails = load_json(EMAIL_FILE).get(user_id, [])

    if not emails:

        await ctx.send("📭 No emails found.")

    else:

        await ctx.author.send("📬 Your Emails:\n" + "\n".join(emails))

        await ctx.send("📤 Sent your emails to DMs.")

@bot.command()

async def inbox(ctx):

    user_id = str(ctx.author.id)

    emails = load_json(EMAIL_FILE).get(user_id, [])

    if not emails:

        await ctx.send("📭 You haven’t generated any email yet.")

        return

    latest_email = emails[-1]

    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}

    res = requests.post("https://leveragers.xyz/api/result", headers=headers, json={"email": latest_email})

    if res.ok:

        messages = res.json().get("messages", [])

        stats = load_json(STATS_FILE)

        stats["messages_received"] = stats.get("messages_received", 0) + len(messages)

        save_json(STATS_FILE, stats)

        if not messages:

            await ctx.send("📭 Inbox is empty.")

        else:

            for m in messages:

                await ctx.author.send(f"✉️ **{m['subject']}**\nFrom: {m['from']}\n{m['text'][:300]}")

            await ctx.send("📥 Inbox sent to DMs.")

    else:

        await ctx.send("⚠️ Failed to fetch inbox.")

@bot.command()

async def stats(ctx):

    if not is_owner(ctx):

        await ctx.send("❌ Owner only.")

        return

    stats = load_json(STATS_FILE)

    await ctx.send(f"📊 Emails Generated: `{stats.get('emails_generated', 0)}`\nMessages Received: `{stats.get('messages_received', 0)}`")

@bot.command()

async def user_mails(ctx, user: discord.User):

    if not is_owner(ctx):

        await ctx.send("❌ Owner only.")

        return

    emails = load_json(EMAIL_FILE).get(str(user.id), [])

    await ctx.send(f"👤 {user.mention} has generated `{len(emails)}` emails.")

@bot.command()

async def blacklist(ctx, user: discord.User):

    if not is_owner(ctx):

        await ctx.send("❌ Owner only.")

        return

    bl = load_json(BLACKLIST_FILE)

    bl.append(str(user.id))

    bl = list(set(bl))

    save_json(BLACKLIST_FILE, bl)

    await ctx.send(f"🚫 {user.mention} is now blacklisted.")

@bot.command()

async def unblacklist(ctx, user: discord.User):

    if not is_owner(ctx):

        await ctx.send("❌ Owner only.")

        return

    bl = load_json(BLACKLIST_FILE)

    if str(user.id) in bl:

        bl.remove(str(user.id))

        save_json(BLACKLIST_FILE, bl)

        await ctx.send(f"✅ {user.mention} is now unblacklisted.")

    else:

        await ctx.send("⚠️ User is not blacklisted.")

@bot.command()

async def make_bulk_mails(ctx, number: int):

    if not is_owner(ctx):

        await ctx.send("❌ Owner only.")

        return

    headers = {

        "Authorization": f"Bearer {API_KEY}",

        "Content-Type": "application/json"

    }

    emails = []

    for _ in range(number):

        res = requests.post("https://leveragers.xyz/api/email", headers=headers, json={"domain": "leveragers.shop"})

        if res.ok:

            emails.append(res.json().get("email"))

    await ctx.author.send("\n".join(emails))

    await ctx.send("✅ Bulk emails sent to your DMs.")

bot.run("MTM5Mzk4NDgyMTM1NjY1ODg0OA.GPlKTM.NlV1ujF5KUQhvOWG8kP4g0-bIKgwsoaqZSy3UM")