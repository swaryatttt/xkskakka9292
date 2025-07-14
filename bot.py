import uuid
import discord
import io
import requests 
import json
import os
import asyncio
import re
from discord.ext import commands
from discord import app_commands
from colorama import Fore, Style, init
from supabase import create_client, Client
from datetime import datetime, timedelta, timezone
from aiohttp import web

# --- Initialization ---
init(autoreset=True)

# --- CONFIGURATION & SECRETS ---
# It's recommended to use environment variables for secrets in production.
DISCORD_BOT_TOKEN = ""
SUPABASE_URL = ""
SUPABASE_KEY = ""
LEVERAGERS_API_KEY = ""

GUILD_ID = discord.Object(id=)  // server id

# --- PERMISSIONS CONFIGURATION ---
BOT_OWNER_IDS = {} // your developer id
OWNER_ROLE_NAME = "Owner"
ADMIN_ROLE_NAME = "Admin"
EMBED_COLOR = 0x5865F2

# --- Globals for Rate Limit Task ---
rate_limit_pause_task = None

# --- Supabase Client Setup ---
try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    print(f"{Fore.GREEN}Successfully connected to Supabase client object.{Style.RESET_ALL}")
except Exception as e:
    print(f"{Fore.RED}FATAL: Failed to initialize Supabase client: {e}{Style.RESET_ALL}")
    exit()

# --- Discord Bot Setup ---
class TyreseBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        self.loop.create_task(setup_bot_api())
        log("Bot is setting up...", Fore.CYAN)

bot = TyreseBot()

# --- Helper Functions ---
def log(msg, color=Fore.WHITE, prefix=" INFO  "):
    print(f"{Style.BRIGHT}{color}[{prefix}]{Style.RESET_ALL} {msg}")

def create_modern_embed(title: str, description: str, color: int = EMBED_COLOR) -> discord.Embed:
    embed = discord.Embed(title=f"**{title}**", description=description, color=color)
    if bot.user and bot.user.avatar:
        embed.set_footer(text="Tyrese | Worker Management", icon_url=bot.user.avatar.url)
    else:
        embed.set_footer(text="Tyrese | Worker Management")
    embed.timestamp = datetime.now(timezone.utc)
    return embed

# --- Permission Checks ---
def is_owner_or_admin():
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.user.id in BOT_OWNER_IDS or (interaction.guild and interaction.user.id == interaction.guild.owner_id):
            return True
        if interaction.guild: # Role checks require a guild context
            required_roles = {OWNER_ROLE_NAME, ADMIN_ROLE_NAME}
            user_roles = {role.name for role in interaction.user.roles}
            if not required_roles.isdisjoint(user_roles):
                return True
        await interaction.response.send_message(embed=create_modern_embed("🚫 Access Denied", "You do not have the required `Owner` or `Admin` role.", 0xFF0000), ephemeral=True)
        return False
    return app_commands.check(predicate)

# --- Rate Limit and Token Validation Logic ---
async def _rate_limit_cooldown(duration: float):
    global rate_limit_pause_task
    log(f"Rate limit detected. Pausing generation for {duration} seconds.", Fore.YELLOW, "RATE-LIMIT")
    await asyncio.sleep(duration)
    try:
        supabase.table("system_status").update({"is_generation_paused": False}).eq("id", 1).execute()
        log("Rate limit cooldown finished. Resuming generation.", Fore.GREEN, "RATE-LIMIT")
    except Exception as e:
        log(f"Failed to resume generation after rate limit: {e}", Fore.RED, "DB_ERROR")
    finally:
        rate_limit_pause_task = None

async def initiate_rate_limit_pause(duration: float):
    global rate_limit_pause_task
    if rate_limit_pause_task and not rate_limit_pause_task.done():
        log("Rate limit pause already in progress.", Fore.YELLOW, "RATE-LIMIT")
        return
    
    try:
        supabase.table("system_status").update({"is_generation_paused": True}).eq("id", 1).execute()
        rate_limit_pause_task = asyncio.create_task(_rate_limit_cooldown(duration + 2))
    except Exception as e:
        log(f"Failed to initiate rate limit pause: {e}", Fore.RED, "DB_ERROR")

def _check_token_validity(token: str) -> str:
    """ Performs a robust, two-step check on a Discord token. """
    if not token or token == "N/A" or len(token) < 50:
        return "INVALID"
    
    headers = {
        "Authorization": token,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    try:
        me_response = requests.get("https://discord.com/api/v9/users/@me", headers=headers, timeout=10)
        if me_response.status_code == 401: return "INVALID"
        billing_response = requests.get("https://discord.com/api/v9/users/@me/billing/payment-sources", headers=headers, timeout=10)
        return "VALID" if billing_response.status_code == 200 else "LOCKED"
    except requests.RequestException:
        return "INVALID"

# --- API Handlers ---
async def validate_worker_key(worker_key: str):
    if not worker_key or not worker_key.startswith("LEVER-WORKER-"): return None
    try:
        res = supabase.table("workers").select("user_id, is_banned").eq("private_key", worker_key).single().execute()
        return res.data["user_id"] if res.data and not res.data.get("is_banned") else None
    except Exception:
        return None

async def handle_get_email(request):
    try:
        data = await request.json()
        if not await validate_worker_key(data.get("worker_key")):
            return web.json_response({"error": "Invalid or banned worker key."}, status=403)
        
        api_headers = {"Authorization": f"Bearer {LEVERAGERS_API_KEY}"}
        response = requests.post("https://leveragers.xyz/api/email", headers=api_headers, json={})
        return web.json_response(response.json(), status=response.status_code)
    except Exception as e:
        log(f"Error in handle_get_email: {e}", Fore.RED, "API_ERROR")
        return web.json_response({"error": "An internal server error occurred."}, status=500)

async def handle_get_verification_link(request):
    try:
        data = await request.json()
        if not await validate_worker_key(data.get("worker_key")):
            return web.json_response({"error": "Invalid or banned worker key."}, status=403)
        email = data.get("email")
        if not email: return web.json_response({"error": "Email parameter is required."}, status=400)
        
        api_headers = {"Authorization": f"Bearer {LEVERAGERS_API_KEY}", "Content-Type": "application/json"}
        for _ in range(34): # Poll for ~100 seconds
            try:
                res = requests.post("https://leveragers.xyz/api/result", headers=api_headers, json={"email": email})
                if res.status_code == 200 and res.json().get("data"):
                    for mail in res.json()["data"]:
                        if "Verify Email Address" in mail.get("subject", ""):
                            if match := re.search(r"https:\/\/(?:click\.)?discord\.com\/(?:verify|ls/click)\S+", mail.get("text", "")):
                                return web.json_response({"link": match.group(0)}, status=200)
            except requests.RequestException: pass
            await asyncio.sleep(3)
        return web.json_response({"error": "Verification link not found in inbox."}, status=404)
    except Exception as e:
        log(f"Error in handle_get_verification_link: {e}", Fore.RED, "API_ERROR")
        return web.json_response({"error": "An internal server error occurred."}, status=500)

async def handle_get_balance(request):
    try:
        data = await request.json()
        worker_key = data.get("worker_key")
        if not await validate_worker_key(worker_key):
            return web.json_response({"error": "Invalid or banned worker key."}, status=403)
            
        res = supabase.table("workers").select("balance").eq("private_key", worker_key).single().execute()
        return web.json_response({"balance": res.data.get("balance", 0)}, status=200) if res.data else web.json_response({"error": "Worker not found."}, status=404)
    except Exception as e:
        log(f"Error in handle_get_balance: {e}", Fore.RED, "API_ERROR")
        return web.json_response({"error": "Internal server error."}, status=500)

async def handle_check_ratelimit(request):
    try:
        data = await request.json()
        duration = float(data.get("retry_after", 0))
        if duration > 0:
            await initiate_rate_limit_pause(duration)
            return web.json_response({"status": "pausing"}, status=200)
        return web.json_response({"status": "ok"}, status=200)
    except Exception as e:
        log(f"Error in handle_check_ratelimit: {e}", Fore.RED, "API_ERROR")
        return web.json_response({"error": "Internal server error"}, status=500)

async def handle_save_account(request):
    try:
        status_res = supabase.table("system_status").select("is_generation_paused").eq("id", 1).single().execute()
        if status_res.data and status_res.data.get("is_generation_paused"):
            return web.json_response({"error": "Account generation is temporarily paused by an administrator."}, status=423) # 423 Locked
    except Exception as e:
        log(f"Could not check system status: {e}", Fore.RED, "API_ERROR")
        return web.json_response({"error": "Could not verify system status. Please try again later."}, status=500)

    try:
        data = await request.json()
        worker_id = await validate_worker_key(data.get("worker_key"))
        if not worker_id:
            return web.json_response({"error": "Invalid or banned worker key."}, status=403)
        
        token = data.get("token")
        token_status = _check_token_validity(token)
        
        if token_status == "INVALID":
            log(f"Rejected invalid token from worker {worker_id}.", Fore.YELLOW, "TOKEN_CHECK")
            return web.json_response({"error": "The generated token was invalid and was rejected."}, status=400)

        account_data = {k: v for k, v in data.items() if k != 'worker_key'}
        account_data['worker_id'] = worker_id
        account_data['is_locked'] = (token_status == "LOCKED")
        
        supabase.table('generated_accounts').insert(account_data).execute()
        log(f"Saved {token_status} account from worker {worker_id}.", Fore.GREEN, "TOKEN_CHECK")
        return web.json_response({"status": "success"}, status=200)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def setup_bot_api():
    app = web.Application()
    app.router.add_post('/save-account', handle_save_account)
    app.router.add_post('/check-ratelimit', handle_check_ratelimit)
    app.router.add_post('/get-balance', handle_get_balance)
    app.router.add_post('/get-email', handle_get_email)
    app.router.add_post('/get-verification-link', handle_get_verification_link)
    runner = web.AppRunner(app)
    await runner.setup()
    port = os.environ.get("PORT", 8080) 
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    log(f"Bot API server started on port {port}", Fore.GREEN)

# --- Bot Events ---
@bot.event
async def on_ready():
    log(f'Bot logged in as {bot.user}', Fore.CYAN)
    try:
        synced = await bot.tree.sync(guild=GUILD_ID)
        log(f"Synced {len(synced)} slash command(s).", Fore.GREEN)
    except Exception as e:
        log(f"Failed to sync commands: {e}", Fore.RED)

# --- USER COMMANDS ---
@bot.tree.command(name="requestkey", description="Request a private key to use the generation tool.", guild=GUILD_ID)
async def requestkey(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    try:
        res = supabase.table("workers").select("user_id").eq("user_id", str(interaction.user.id)).execute()
        if res.data:
            await interaction.followup.send(embed=create_modern_embed("⚠️ Key Already Exists", "You already have a key. Use `/mykey` to retrieve it."))
            return
        key = f"LEVER-WORKER-{uuid.uuid4()}"
        supabase.table('workers').insert({"user_id": str(interaction.user.id), "user_name": interaction.user.name, "private_key": key}).execute()
        await interaction.user.send(embed=create_modern_embed("✅ Your Private Key", f"Here is your unique private key. **Do not share it!**\n```\n{key}\n```"))
        await interaction.followup.send(embed=create_modern_embed("🔑 Key Generated", "Your private key has been sent to your DMs."))
    except discord.Forbidden:
        await interaction.followup.send(embed=create_modern_embed("❌ DM Failed", "I couldn't send you a DM. Please enable DMs from server members.", 0xFF0000))
    except Exception as e:
        await interaction.followup.send(embed=create_modern_embed("❌ Error", f"An unexpected error occurred: {e}", 0xFF0000))

@bot.tree.command(name="mykey", description="Retrieves your private key if you have lost it.", guild=GUILD_ID)
async def mykey(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    try:
        res = supabase.table("workers").select("private_key").eq("user_id", str(interaction.user.id)).single().execute()
        if res.data and res.data.get("private_key"):
            key = res.data["private_key"]
            await interaction.user.send(embed=create_modern_embed("🔑 Your Private Key", f"You requested your key. **Do not share it!**\n```\n{key}\n```"))
            await interaction.followup.send(embed=create_modern_embed("✅ Key Sent", "I have sent your private key to your DMs."))
        else:
            await interaction.followup.send(embed=create_modern_embed("🤷 No Key Found", "You don't have a key yet. Use `/requestkey` to get one."))
    except Exception:
         await interaction.followup.send(embed=create_modern_embed("🤷 No Key Found", "You don't have a key yet. Use `/requestkey` to get one."))

@bot.tree.command(name="stats", description="Shows server-wide generation statistics and token age.", guild=GUILD_ID)
async def stats(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        now = datetime.now(timezone.utc)
        time_24h_ago = (now - timedelta(hours=24)).isoformat()
        time_7d_ago = (now - timedelta(days=7)).isoformat()

        total_gens = supabase.table("generated_accounts").select("id", count='exact').execute().count
        gens_24h = supabase.table("generated_accounts").select("id", count='exact').gte("created_at", time_24h_ago).execute().count
        gens_7d = supabase.table("generated_accounts").select("id", count='exact').gte("created_at", time_7d_ago).execute().count
        
        embed = create_modern_embed("📊 Generation Statistics", "Live statistics for all generated accounts.")
        embed.add_field(name="Total Accounts", value=f"```{total_gens}```", inline=True)
        embed.add_field(name="Past 24 Hours", value=f"```{gens_24h}```", inline=True)
        embed.add_field(name="Past 7 Days", value=f"```{gens_7d}```", inline=True)
        await interaction.followup.send(embed=embed)
    except Exception as e:
        log(f"Error in /stats: {e}", Fore.RED)
        await interaction.followup.send(embed=create_modern_embed("❌ Error", "Could not fetch statistics. Ensure `created_at` column exists.", 0xFF0000))

@bot.tree.command(name="leaderboard", description="Shows the top 10 generators.", guild=GUILD_ID)
async def leaderboard(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        res = supabase.rpc('get_generation_leaderboard').execute()
        if not res.data:
            await interaction.followup.send(embed=create_modern_embed("Leaderboard is empty.", "No accounts have been generated yet."))
            return

        description = ""
        for i, entry in enumerate(res.data):
            worker_id_str = entry.get('worker_id') 
            if not worker_id_str:
                continue

            user = bot.get_user(int(worker_id_str))
            username = user.name if user else f"ID: {worker_id_str}"
            
            rank = i + 1
            medal = ""
            if rank == 1: medal = "🥇"
            elif rank == 2: medal = "🥈"
            elif rank == 3: medal = "🥉"
            description += f"**{rank}. {medal} {username}** - {entry.get('gen_count', 0)} gens\n"
            
        await interaction.followup.send(embed=create_modern_embed("🏆 Generation Leaderboard", description))
    except Exception as e:
        log(f"Error in /leaderboard: {e}", Fore.RED)
        await interaction.followup.send(embed=create_modern_embed("❌ Error", "Could not fetch the leaderboard. Ensure the RPC function `get_generation_leaderboard` exists and is correct.", 0xFF0000))

# --- ADMIN COMMANDS ---
admin_group = app_commands.Group(name="admin", description="Admin commands for managing workers.", guild_ids=[GUILD_ID.id])

@admin_group.command(name="deliver", description="Deliver accounts to a customer.")
@is_owner_or_admin()
async def deliver(interaction: discord.Interaction, customer: discord.Member, quantity: int):
    await interaction.response.defer(ephemeral=True)
    if quantity <= 0:
        await interaction.followup.send(embed=create_modern_embed("❌ Invalid Quantity", "Please provide a quantity greater than zero."))
        return
        
    try:
        res = supabase.table("generated_accounts").select("id, email, password, token").is_("delivered_to_user_id", None).limit(quantity).execute()
        
        accounts = res.data
        if not accounts or len(accounts) < quantity:
            await interaction.followup.send(embed=create_modern_embed("❌ Insufficient Stock", f"Only **{len(accounts)}** accounts are available, but you requested **{quantity}**."))
            return

        formatted_accounts = [f"{acc['email']}:{acc['password']}:{acc['token']}" for acc in accounts]
        delivery_content = "\n".join(formatted_accounts)
        delivery_file = io.BytesIO(delivery_content.encode('utf-8'))
        
        try:
            await customer.send(
                embed=create_modern_embed("📦 Your Delivery", f"Here are the **{quantity}** accounts you requested."), 
                file=discord.File(delivery_file, filename=f"delivery_{quantity}_tokens.txt")
            )
        except discord.Forbidden:
            await interaction.followup.send(embed=create_modern_embed("❌ DM Failed", f"Could not DM **{customer.display_name}**. They may have DMs disabled."))
            return

        delivered_ids = [acc['id'] for acc in accounts]
        update_data = {
            "delivered_to_user_id": str(customer.id),
            "delivered_by_admin_id": str(interaction.user.id),
            "delivered_at": datetime.now(timezone.utc).isoformat()
        }
        supabase.table("generated_accounts").update(update_data).in_("id", delivered_ids).execute()

        await interaction.followup.send(embed=create_modern_embed("✅ Delivery Successful", f"Successfully delivered **{quantity}** accounts to **{customer.display_name}** and marked them as delivered."))

    except Exception as e:
        log(f"Error in /admin deliver: {e}", Fore.RED)
        await interaction.followup.send(embed=create_modern_embed("❌ Error", "An unexpected error occurred during delivery.", 0xFF0000))

@admin_group.command(name="stock", description="Check the number of available, undelivered accounts.")
@is_owner_or_admin()
async def stock(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    try:
        res = supabase.table("generated_accounts").select("id", count='exact').is_("delivered_to_user_id", None).execute()
        await interaction.followup.send(embed=create_modern_embed("📦 Available Stock", f"There are currently **{res.count}** accounts available for delivery."))
    except Exception as e:
        await interaction.followup.send(embed=create_modern_embed("❌ Error", "Could not fetch stock count.", 0xFF0000))


@admin_group.command(name="pause_generation", description="Temporarily stops workers from saving new accounts.")
@is_owner_or_admin()
async def pause_generation(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    try:
        # Check current status before updating
        status_res = supabase.table("system_status").select("is_generation_paused").eq("id", 1).single().execute()
        if status_res.data and status_res.data.get("is_generation_paused"):
            await interaction.followup.send(embed=create_modern_embed("⚠️ Already Paused", "Generation is already paused."))
            return

        supabase.table("system_status").update({"is_generation_paused": True}).eq("id", 1).execute()
        log(f"Generation PAUSED by {interaction.user.name}", Fore.YELLOW, "ADMIN")
        await interaction.followup.send(embed=create_modern_embed("⏸️ Generation Paused", "Workers will no longer be able to save new accounts until resumed."))
    except Exception as e:
        await interaction.followup.send(embed=create_modern_embed("❌ Error", f"Could not update system status: {e}", 0xFF0000))

@admin_group.command(name="resume_generation", description="Allows workers to save new accounts again.")
@is_owner_or_admin()
async def resume_generation(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    try:
        # Check current status before updating
        status_res = supabase.table("system_status").select("is_generation_paused").eq("id", 1).single().execute()
        if status_res.data and not status_res.data.get("is_generation_paused"):
            await interaction.followup.send(embed=create_modern_embed("⚠️ Already Active", "Generation is not currently paused."))
            return

        global rate_limit_pause_task
        if rate_limit_pause_task and not rate_limit_pause_task.done():
            rate_limit_pause_task.cancel()
            rate_limit_pause_task = None
            log("Manual resume cancelled an automatic rate-limit task.", Fore.YELLOW)

        supabase.table("system_status").update({"is_generation_paused": False}).eq("id", 1).execute()
        log(f"Generation RESUMED by {interaction.user.name}", Fore.GREEN, "ADMIN")
        await interaction.followup.send(embed=create_modern_embed("▶️ Generation Resumed", "Workers can now save new accounts."))
    except Exception as e:
        await interaction.followup.send(embed=create_modern_embed("❌ Error", f"Could not update system status: {e}", 0xFF0000))

@admin_group.command(name="ban", description="Bans a worker from using the tool.")
@is_owner_or_admin()
async def ban(interaction: discord.Interaction, worker: discord.Member, reason: str = "No reason provided."):
    await interaction.response.defer(ephemeral=True)
    try:
        supabase.table("workers").update({"is_banned": True}).eq("user_id", str(worker.id)).execute()
        embed = create_modern_embed("🚫 Worker Banned", f"**{worker.display_name}** has been banned.\n**Reason:** {reason}")
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(embed=create_modern_embed("❌ Error", "A database error occurred.", 0xFF0000))

@admin_group.command(name="unban", description="Unbans a worker.")
@is_owner_or_admin()
async def unban(interaction: discord.Interaction, worker: discord.Member):
    await interaction.response.defer(ephemeral=True)
    try:
        supabase.table("workers").update({"is_banned": False}).eq("user_id", str(worker.id)).execute()
        embed = create_modern_embed("✅ Worker Unbanned", f"**{worker.display_name}** has been unbanned and can use the tool again.")
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(embed=create_modern_embed("❌ Error", "A database error occurred.", 0xFF0000))

@admin_group.command(name="pay", description="Sets a worker's balance to a specific amount.")
@is_owner_or_admin()
async def pay(interaction: discord.Interaction, worker: discord.Member, amount: float):
    await interaction.response.defer(ephemeral=True)
    try:
        supabase.table("workers").update({"balance": amount}).eq("user_id", str(worker.id)).execute()
        embed = create_modern_embed("💰 Payment Processed", f"**{worker.display_name}**'s balance has been set to **${amount:,.2f}**.")
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(embed=create_modern_embed("❌ Error", "A database error occurred.", 0xFF0000))

@admin_group.command(name="revoke", description="Permanently revokes a worker's key.")
@is_owner_or_admin()
async def revoke(interaction: discord.Interaction, worker: discord.Member):
    await interaction.response.defer(ephemeral=True)
    try:
        supabase.table("workers").delete().eq("user_id", str(worker.id)).execute()
        await interaction.followup.send(embed=create_modern_embed("🗑️ Key Revoked", f"**{worker.display_name}**'s key has been revoked."))
    except Exception as e:
        await interaction.followup.send(embed=create_modern_embed("❌ Error", "A database error occurred.", 0xFF0000))

@admin_group.command(name="info", description="Get detailed info about a worker.")
@is_owner_or_admin()
async def info(interaction: discord.Interaction, worker: discord.Member):
    await interaction.response.defer(ephemeral=True)
    try:
        worker_res = supabase.table("workers").select("*").eq("user_id", str(worker.id)).single().execute()
        if not worker_res.data:
            await interaction.followup.send(embed=create_modern_embed("🤷 Not Found", f"**{worker.display_name}** is not a registered worker."))
            return
            
        worker_data = worker_res.data
        stats_res = supabase.table("generated_accounts").select("id", count='exact').eq("worker_id", str(worker.id)).execute()
        
        embed = create_modern_embed(f"ℹ️ Worker Info: {worker.display_name}", "")
        embed.set_thumbnail(url=worker.display_avatar.url)
        embed.add_field(name="💰 Balance", value=f"${worker_data.get('balance', 0):,.2f}", inline=True)
        embed.add_field(name="✅ Gens", value=str(stats_res.count), inline=True)
        embed.add_field(name="🚫 Banned", value="Yes" if worker_data.get('is_banned') else "No")
        embed.add_field(name="🔑 Private Key", value=f"||`{worker_data.get('private_key')}`||", inline=False)
        await interaction.followup.send(embed=embed)
    except Exception as e:
        log(f"Error in /admin info: {e}", Fore.RED)
        await interaction.followup.send(embed=create_modern_embed("❌ Error", "A database error occurred.", 0xFF0000))

bot.tree.add_command(admin_group, guild=GUILD_ID)

if __name__ == "__main__":
    # It's good practice to ensure the system_status table has an entry on startup.
    try:
        res = supabase.table("system_status").select("id").eq("id", 1).execute()
        if not res.data:
            supabase.table("system_status").insert({"id": 1, "is_generation_paused": False}).execute()
            log("Initialized system_status row in database.", Fore.CYAN)
    except Exception as e:
        log(f"Could not initialize system_status: {e}", Fore.RED)

    bot.run(DISCORD_BOT_TOKEN)
