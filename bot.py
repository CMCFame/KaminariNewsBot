import discord
import os
import feedparser
import json
import asyncio
import time
import random
import logging
import hashlib
import re  # Nueva importación
from datetime import datetime
from discord.ext import commands, tasks

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

# Función para limpiar HTML
def clean_html(text):
    """
    Elimina las etiquetas HTML del texto y limpia el contenido.
    """
    # Eliminar tags img completos
    text = re.sub(r'<img[^>]+>', '', text)
    # Eliminar otros tags HTML
    text = re.sub(r'<[^>]+>', '', text)
    # Eliminar múltiples espacios
    text = re.sub(r'\s+', ' ', text)
    # Limpiar espacios al inicio y final
    text = text.strip()
    return text

# Discord bot configuration
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
bot = commands.Bot(command_prefix="$", intents=intents)

# Updated Gaming RSS feeds list
GAMING_FEEDS = {
    "Destructoid": "https://www.destructoid.com/feed/",
    "Xbox Wire": "https://news.xbox.com/en-us/feed/",
    "Escapist Magazine": "https://www.escapistmagazine.com/feed/",
    "Kotaku": "https://kotaku.com/rss",
    "VG247": "https://www.vg247.com/feed/news",
    "Touch Arcade": "https://toucharcade.com/feed/",
    "GameSpot": "https://www.gamespot.com/feeds/mashup/",
    "IGN": "http://feeds.feedburner.com/ign/news",
    "Polygon": "https://www.polygon.com/rss/index.xml",
    "DualShockers": "https://www.dualshockers.com/feed/",
    "Gematsu": "https://www.gematsu.com/feed",
    "PC Gamer": "https://www.pcgamer.com/rss/",
    "Eurogamer": "https://www.eurogamer.net/feed",
    "Twinfinite": "https://twinfinite.net/feed/",
    "Push Square": "https://www.pushsquare.com/feeds/latest",
    "Pocket Gamer": "https://pocket4957.rssing.com/chan-78169779/index-latest.php",
    "Siliconera": "https://www.siliconera.com/feed/",
    "Nintendo Everything": "https://nintendoeverything.com/feed/",
    "VGC": "https://www.videogameschronicle.com/category/news/feed/"
}

UPDATE_INTERVAL = 10800  # 3 hours in seconds
MAX_RETRIES = 3
RATE_LIMIT_DELAY = 2  # seconds between messages

class ServerConfig:
    def __init__(self, config_file="server_config.json"):
        self.config_file = config_file
        self.config = self._load_config()
        self.last_updates = {}

    def _load_config(self):
        try:
            with open(self.config_file, 'r') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            logger.warning(f"Configuration file {self.config_file} not found or invalid. Creating new config.")
            return {}

    def _save_config(self):
        try:
            with open(self.config_file, 'w') as f:
                json.dump(self.config, f)
        except Exception as e:
            logger.error(f"Error saving config: {str(e)}")

    def set_news_channel(self, guild_id, channel_id):
        self.config[str(guild_id)] = channel_id
        self._save_config()

    def get_news_channel(self, guild_id):
        return self.config.get(str(guild_id))

    def remove_server(self, guild_id):
        if str(guild_id) in self.config:
            del self.config[str(guild_id)]
            self._save_config()

    def get_last_update(self, guild_id):
        return self.last_updates.get(str(guild_id))

    def set_last_update(self, guild_id, time):
        self.last_updates[str(guild_id)] = time

class NewsCache:
    def __init__(self, cache_file="news_cache.json"):
        self.cache_file = cache_file
        self.cache = self._load_cache()

    def _load_cache(self):
        try:
            with open(self.cache_file, 'r') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save_cache(self):
        try:
            with open(self.cache_file, 'w') as f:
                json.dump(self.cache, f)
        except Exception as e:
            logger.error(f"Error saving cache: {str(e)}")

    def _generate_entry_hash(self, entry):
        """Generate a unique hash for an entry based on title and date"""
        hash_content = f"{entry.get('title', '')}{entry.get('published', '')}{entry.get('link', '')}"
        return hashlib.md5(hash_content.encode()).hexdigest()

    def is_new_entry(self, feed_name, entry):
        entry_id = entry.get('id', '') or entry.get('guid', '') or self._generate_entry_hash(entry)
            
        if feed_name not in self.cache:
            self.cache[feed_name] = []

        if entry_id not in self.cache[feed_name]:
            self.cache[feed_name].append(entry_id)
            # Keep only the latest 50 IDs
            self.cache[feed_name] = self.cache[feed_name][-50:]
            self._save_cache()
            return True
        return False
        
    def clear_cache(self, feed_name=None):
        if feed_name:
            if feed_name in self.cache:
                self.cache[feed_name] = []
        else:
            self.cache = {}
        self._save_cache()

server_config = ServerConfig()
news_cache = NewsCache()

async def send_with_rate_limit(channel, content=None, embed=None):
    """Send messages with rate limiting to avoid Discord API issues"""
    try:
        await asyncio.sleep(RATE_LIMIT_DELAY)
        if embed:
            await channel.send(embed=embed)
        elif content:
            await channel.send(content)
    except Exception as e:
        logger.error(f"Error sending message: {str(e)}")

def format_time(dt):
    return dt.strftime("%H:%M")

async def fetch_feed(feed_name, feed_url, max_retries=MAX_RETRIES):
    async def try_fetch_with_backoff(attempt):
        try:
            if attempt > 0:
                delay = min(300, (2 ** attempt) + (random.randint(0, 1000) / 1000))
                await asyncio.sleep(delay)
            
            import ssl
            if hasattr(ssl, '_create_unverified_context'):
                ssl._create_default_https_context = ssl._create_unverified_context
            
            feed = feedparser.parse(feed_url)
            
            if hasattr(feed, 'status'):
                if feed.status in [301, 302, 307, 308]:
                    if 'href' in feed and feed.href != feed_url:
                        logger.info(f"Redirecting {feed_name} to: {feed.href}")
                        return await try_fetch_with_backoff(0)
                elif feed.status == 429:
                    if attempt < max_retries:
                        logger.warning(f"Rate limit reached for {feed_name}, retrying...")
                        return await try_fetch_with_backoff(attempt + 1)
                    else:
                        logger.error(f"Max retries reached for {feed_name}")
                        return None
                elif feed.status != 200:
                    logger.error(f"Error fetching {feed_name}: Status {feed.status}")
                    return None
            
            return feed
            
        except Exception as e:
            logger.error(f"Error processing {feed_name}: {str(e)}")
            if attempt < max_retries:
                return await try_fetch_with_backoff(attempt + 1)
            return None

    try:
        feed = await try_fetch_with_backoff(0)
        if not feed:
            return []

        news_items = []
        logger.info(f"Processing {feed_name}: {len(feed.entries)} entries found")
        
        for entry in feed.entries[:5]:
            if news_cache.is_new_entry(feed_name, entry):
                logger.info(f"New entry found in {feed_name}")
                title = entry.get('title', 'Sin título')
                
                # Proceso del link
                raw_link = entry.get('link', '#')
                link = extract_url(raw_link)
                
                # Remove UTM parameters
                if '?' in link:
                    link = link.split('?')[0]

                published = entry.get('published', 'Fecha no disponible')
                
                # Obtener y limpiar el resumen
                summary = entry.get('summary', '')
                summary = clean_html(summary)  # Aplicamos la limpieza de HTML
                if len(summary) > 300:
                    summary = summary[:297] + "..."

                # Find image URL
                image_url = None
                try:
                    if 'media_thumbnail' in entry and entry['media_thumbnail']:
                        image_url = extract_url(entry['media_thumbnail'][0].get('url', ''))
                    elif 'media_content' in entry and entry['media_content']:
                        image_url = extract_url(entry['media_content'][0].get('url', ''))
                    elif hasattr(entry, 'links'):
                        for link_item in entry.links:
                            if isinstance(link_item, dict) and link_item.get('type', '').startswith('image/'):
                                image_url = extract_url(link_item)
                                break

                    if image_url and not (image_url.startswith('http://') or image_url.startswith('https://')):
                        image_url = None
                except Exception as e:
                    logger.error(f"Error processing image for {feed_name}: {str(e)}")
                    image_url = None

                embed = discord.Embed(
                    title=title,
                    url=link,
                    description=summary if summary else None,
                    color=discord.Color.blue()
                )
                
                embed.set_footer(text=f"Fuente: {feed_name} | Publicado: {published}")
                
                if image_url:
                    embed.set_thumbnail(url=image_url)

                news_items.append(embed)

        return news_items
    except Exception as e:
        logger.error(f"Error processing {feed_name}: {str(e)}")
        return []
@tasks.loop(seconds=UPDATE_INTERVAL)
async def check_feeds():
    current_time = datetime.now()
    logger.info("Starting scheduled news check")

    for guild in bot.guilds:
        channel_id = server_config.get_news_channel(guild.id)
        if not channel_id:
            continue

        channel = bot.get_channel(channel_id)
        if not channel:
            continue

        last_update = server_config.get_last_update(guild.id)
        update_message = "🎮 **Actualizando noticias de gaming**"
        if last_update:
            update_message += f"\nÚltima actualización fue a las {format_time(last_update)}"

        await send_with_rate_limit(channel, content=update_message)

        news_found = False
        for feed_name, feed_url in GAMING_FEEDS.items():
            news_items = await fetch_feed(feed_name, feed_url)
            if news_items:
                news_found = True
                try:
                    header = f"▓▓▓▓▓▓▓▓▓▓ Noticias de {feed_name} ▓▓▓▓▓▓▓▓▓▓"
                    await send_with_rate_limit(channel, content=f"**{header}**")
                    
                    for embed in news_items:
                        await send_with_rate_limit(channel, embed=embed)
                    
                    await send_with_rate_limit(channel, content="_ _")
                except Exception as e:
                    logger.error(f"Error sending news from {feed_name} in {guild.name}: {str(e)}")
                    continue

        if not news_found:
            await send_with_rate_limit(channel, content="No se encontraron noticias nuevas en esta actualización.")

        server_config.set_last_update(guild.id, current_time)

@bot.event
async def on_ready():
    logger.info(f'{bot.user} has logged in')
    if not check_feeds.is_running():
        check_feeds.start()

@bot.event
async def on_resumed():
    logger.info('Bot reconnected after disconnection')

@bot.event
async def on_connect():
    logger.info('Bot connected to Discord')

@bot.event
async def on_guild_join(guild):
    logger.info(f'Bot joined new guild: {guild.name}')
    for channel in guild.text_channels:
        try:
            await channel.send(
                "¡Hola! Soy un bot de noticias de gaming. Para comenzar, usa el comando "
                "`$configurar_canal` en el canal donde deseas recibir las noticias."
            )
            break
        except discord.Forbidden:
            continue

@bot.command()
@commands.check_any(
    commands.has_permissions(administrator=True),
    commands.has_permissions(manage_channels=True),
    commands.has_permissions(manage_guild=True)
)
async def configurar_canal(ctx):
    """Configura el canal actual como el canal de noticias"""
    server_config.set_news_channel(ctx.guild.id, ctx.channel.id)
    await ctx.send(f"✅ Canal {ctx.channel.mention} configurado correctamente para recibir noticias de gaming.")
    logger.info(f'Channel configured for guild {ctx.guild.name}: {ctx.channel.name}')

@bot.command()
@commands.has_permissions(administrator=True)
async def desactivar_noticias(ctx):
    """Desactiva las noticias en este servidor"""
    server_config.remove_server(ctx.guild.id)
    await ctx.send("❌ Las noticias han sido desactivadas en este servidor.")
    logger.info(f'News disabled for guild {ctx.guild.name}')

@bot.command()
async def fuentes(ctx):
    """Muestra la lista de fuentes configuradas"""
    embed = discord.Embed(
        title="Fuentes de Noticias Configuradas",
        color=discord.Color.green()
    )
    sources_text = "\n".join([f"• {name}" for name in GAMING_FEEDS.keys()])
    embed.description = sources_text
    await ctx.send(embed=embed)

@bot.command()
async def estado(ctx):
    """Muestra el estado actual del bot en este servidor"""
    channel_id = server_config.get_news_channel(ctx.guild.id)
    if channel_id:
        channel = bot.get_channel(channel_id)
        last_update = server_config.get_last_update(ctx.guild.id)
        status = (
            f"✅ Bot activo en este servidor\n"
            f"📡 Canal de noticias: {channel.mention}\n"
        )
        if last_update:
            status += f"🕒 Última actualización: {format_time(last_update)}\n"
        status += f"⏱️ Intervalo de actualización: cada 3 horas"
    else:
        status = "❌ El bot no está configurado en este servidor. Usa `$configurar_canal` para activarlo."

    embed = discord.Embed(
        title="Estado del Bot",
        description=status,
        color=discord.Color.blue()
    )
    await ctx.send(embed=embed)
@bot.command()
async def actualizar(ctx):
    """Actualiza las noticias bajo demanda"""
    current_time = datetime.now()
    channel_id = server_config.get_news_channel(ctx.guild.id)
    if not channel_id:
        await ctx.send("❌ El canal de noticias no está configurado. Usa `$configurar_canal` para configurarlo.")
        return

    channel = bot.get_channel(channel_id)
    if not channel:
        await ctx.send("❌ No se pudo encontrar el canal configurado.")
        return

    await send_with_rate_limit(channel, content="🎮 **Actualizando noticias de gaming bajo demanda...**")
    logger.info(f'Manual update requested in guild {ctx.guild.name}')

    news_found = False
    for feed_name, feed_url in GAMING_FEEDS.items():
        news_items = await fetch_feed(feed_name, feed_url)
        if news_items:
            news_found = True
            header = f"▓▓▓▓▓▓▓▓▓▓ Noticias de {feed_name} ▓▓▓▓▓▓▓▓▓▓"
            await send_with_rate_limit(channel, content=f"**{header}**")
            
            for embed in news_items:
                await send_with_rate_limit(channel, embed=embed)
            
            await send_with_rate_limit(channel, content="_ _")

    if not news_found:
        await send_with_rate_limit(channel, content="No se encontraron noticias nuevas en esta actualización.")

    server_config.set_last_update(ctx.guild.id, current_time)

@bot.command()
async def limpiar_cache(ctx, fuente=None):
    """Limpia el caché del bot"""
    if fuente:
        fuente_encontrada = None
        for nombre_fuente in GAMING_FEEDS.keys():
            if nombre_fuente.lower() == fuente.lower():
                fuente_encontrada = nombre_fuente
                break
        
        if fuente_encontrada:
            news_cache.clear_cache(fuente_encontrada)
            await ctx.send(f"🧹 Cache limpiado para la fuente: {fuente_encontrada}")
            logger.info(f'Cache cleared for source {fuente_encontrada}')
        else:
            fuentes_disponibles = "\n".join([f"• {name}" for name in GAMING_FEEDS.keys()])
            await ctx.send(f"❌ Fuente no encontrada. Las fuentes disponibles son:\n{fuentes_disponibles}")
    else:
        news_cache.clear_cache()
        await ctx.send("🧹 Cache limpiado completamente")
        logger.info('Complete cache clear performed')

@bot.command()
async def verificar_permisos(ctx):
    """Verifica los permisos del bot en el canal actual"""
    perms = ctx.channel.permissions_for(ctx.guild.me)
    
    embed = discord.Embed(
        title="Permisos del Bot",
        color=discord.Color.blue()
    )
    
    permisos = {
        "Enviar Mensajes": perms.send_messages,
        "Incrustar Enlaces": perms.embed_links,
        "Adjuntar Archivos": perms.attach_files,
        "Leer Historial": perms.read_message_history,
        "Usar Enlaces Externos": perms.use_external_emojis
    }
    
    for perm, value in permisos.items():
        status = "✅" if value else "❌"
        embed.add_field(name=perm, value=status, inline=True)
    
    await ctx.send(embed=embed)

@configurar_canal.error
@desactivar_noticias.error
async def admin_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ Necesitas permisos de administrador para usar este comando.")
        logger.warning(f'Permission denied for user in guild {ctx.guild.name}')

# Main bot startup
if __name__ == "__main__":
    TOKEN = os.getenv('DISCORD_TOKEN')
    if not TOKEN:
        logger.error("Discord token not found in environment variables")
        exit(1)
    
    max_retries = 5
    retry_count = 0
    
    while retry_count < max_retries:
        try:
            logger.info(f"Starting bot (attempt {retry_count + 1} of {max_retries})...")
            bot.run(TOKEN, reconnect=True)
            logger.info("Bot connected successfully")
            break
        except discord.LoginFailure:
            logger.error("Invalid or expired Discord token")
            exit(1)
        except discord.ConnectionClosed as e:
            retry_count += 1
            logger.error(f"Connection error (attempt {retry_count}): {e}")
            if retry_count < max_retries:
                logger.info("Retrying in 30 seconds...")
                time.sleep(30)
        except Exception as e:
            retry_count += 1
            logger.error(f"Unexpected error (attempt {retry_count}): {type(e).__name__} - {str(e)}")
            if retry_count < max_retries:
                logger.info("Retrying in 30 seconds...")
                time.sleep(30)
    
    if retry_count >= max_retries:
        logger.error("Maximum retry attempts reached. Stopping bot.")