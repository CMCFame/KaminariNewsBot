import discord
import os
import feedparser
import json
import asyncio
import time
import random
import logging
from datetime import datetime
from discord.ext import commands, tasks

# Configuración del logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# Configuración del bot de Discord
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True  # Necesario para detectar servidores
bot = commands.Bot(command_prefix="$", intents=intents)

# Lista de feeds RSS de gaming
GAMING_FEEDS = {
    "Kotaku": "https://kotaku.com/rss",
    "VG247": "https://www.vg247.com/feed/news",
    "Touch Arcade": "https://toucharcade.com/feed/",
    "GameSpot": "https://www.gamespot.com/feeds/mashup/",
    "IGN": "http://feeds.feedburner.com/ign/news",
    "Polygon": "https://www.polygon.com/rss/index.xml",
    "DualShockers": "https://www.dualshockers.com/feed/",
    "Gematsu": "https://www.gematsu.com/feed",
    "Rock Paper Shotgun": "https://www.rockpapershotgun.com/feed/news",
    "PC Gamer": "https://www.pcgamer.com/rss/",
    "Eurogamer": "https://www.eurogamer.net/feed",
    "Twinfinite": "https://twinfinite.net/feed/",
    "Push Square": "https://www.pushsquare.com/feeds/latest",
    "Gamepur": "https://www.gamepur.com/feed",
    "Pocket Gamer": "https://pocket4957.rssing.com/chan-78169779/index-latest.php",
    "Siliconera": "https://www.siliconera.com/feed/",
    "Attack of the Fanboy": "https://attackofthefanboy.com/feed/",
    "Nintendo Everything": "https://nintendoeverything.com/feed/"
}

UPDATE_INTERVAL = 10800  # 3 horas en segundos
MESSAGE_DELAY = (2, 4)  # Rango de delay entre mensajes en segundos
MAX_CACHE_ENTRIES = 100  # Máximo número de entradas en caché por feed

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
            return {}

    def _save_config(self):
        with open(self.config_file, 'w') as f:
            json.dump(self.config, f)

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
        # Limitar el tamaño del caché por feed
        for feed_name in self.cache:
            self.cache[feed_name] = self.cache[feed_name][-MAX_CACHE_ENTRIES:]
        with open(self.cache_file, 'w') as f:
            json.dump(self.cache, f)

    def is_new_entry(self, feed_name, entry_id):
        if not entry_id:  # Si el ID está vacío, considerarlo como nuevo
            return True
            
        if feed_name not in self.cache:
            self.cache[feed_name] = []

        if entry_id not in self.cache[feed_name]:
            self.cache[feed_name].append(entry_id)
            self._save_cache()
            return True
        return False
        
    def clear_cache(self, feed_name=None):
        """Limpia el caché completo o de un feed específico"""
        if feed_name:
            if feed_name in self.cache:
                self.cache[feed_name] = []
        else:
            self.cache = {}
        self._save_cache()

server_config = ServerConfig()
news_cache = NewsCache()

def format_time(dt):
    """Formatea la hora en formato 24h"""
    return dt.strftime("%H:%M")

async def fetch_feed(feed_name, feed_url, max_retries=3):
    async def try_fetch_with_backoff(attempt):
        try:
            if attempt > 0:
                delay = min(300, (2 ** attempt) + (random.randint(0, 1000) / 1000))
                await asyncio.sleep(delay)
            
            feed = feedparser.parse(feed_url)
            
            if hasattr(feed, 'status'):
                if feed.status in [301, 302, 307, 308]:  # Códigos de redirección
                    if 'href' in feed and feed.href != feed_url:
                        logging.info(f"Redirigiendo {feed_name} a: {feed.href}")
                        return await try_fetch_with_backoff(0)
                elif feed.status == 429:  # Too Many Requests
                    if attempt < max_retries:
                        logging.warning(f"Rate limit alcanzado para {feed_name}, reintentando...")
                        return await try_fetch_with_backoff(attempt + 1)
                    else:
                        logging.error(f"Máximo de reintentos alcanzado para {feed_name}")
                        return None
                elif feed.status != 200:
                    logging.error(f"Error al obtener {feed_name}: Status {feed.status}")
                    return None
            
            return feed
            
        except Exception as e:
            logging.error(f"Error al procesar {feed_name}: {str(e)}")
            if attempt < max_retries:
                return await try_fetch_with_backoff(attempt + 1)
            return None

    try:
        feed = await try_fetch_with_backoff(0)
        if not feed:
            return []

        news_items = []
        logging.info(f"Procesando {feed_name}: {len(feed.entries)} entradas encontradas")
        
        for entry in feed.entries[:5]:
            entry_id = entry.get('id', '') or entry.get('guid', '') or entry.get('link', '')
            logging.debug(f"Verificando entrada: {entry_id}")
            
            if news_cache.is_new_entry(feed_name, entry_id):
                logging.info(f"Nueva entrada encontrada en {feed_name}")
                title = entry.get('title', 'Sin título')
                link = entry.get('link', '#')
                published = entry.get('published', 'Fecha no disponible')
                
                # Obtener categorías
                categories = []
                if 'tags' in entry:
                    categories = [tag['term'] for tag in entry.get('tags', [])]
                elif 'categories' in entry:
                    categories = entry.get('categories', [])
                categories_str = ', '.join(categories) if categories else 'Sin categorías'

                # Buscar imagen en diferentes ubicaciones comunes del feed
                image_url = None
                if 'media_thumbnail' in entry:
                    image_url = entry['media_thumbnail'][0].get('url')
                elif 'media_content' in entry:
                    image_url = entry['media_content'][0].get('url')
                elif hasattr(entry, 'links'):
                    for link in entry.links:
                        if link.get('type', '').startswith('image/'):
                            image_url = link.get('href')
                            break

                embed = discord.Embed(
                    title=title,
                    url=link,
                    color=discord.Color.blue()
                )
                embed.set_footer(text=f"Fuente: {feed_name} | Publicado: {published}")
                if categories_str:
                    embed.add_field(name="Categorías", value=categories_str, inline=False)
                if image_url:
                    embed.set_thumbnail(url=image_url)

                news_items.append(embed)
            else:
                logging.debug(f"Entrada ya existe en caché: {entry_id}")

        return news_items
    except Exception as e:
        logging.error(f"Error al procesar {feed_name}: {str(e)}")
        return []

async def enviar_noticias_agrupadas(channel, feed_name, news_items):
    """Envía noticias agrupadas por fuente con una cabecera decorativa"""
    if not news_items:
        return

    # Crear la cabecera decorativa
    header = f"{'='*20} Noticias de {feed_name} {'='*20}"
    await channel.send(f"```\n{header}\n```")
    
    # Enviar las noticias de esta fuente
    for embed in news_items:
        try:
            await channel.send(embed=embed)
            await asyncio.sleep(random.uniform(*MESSAGE_DELAY))
        except Exception as e:
            logging.error(f"Error al enviar noticia de {feed_name}: {str(e)}")

@tasks.loop(seconds=UPDATE_INTERVAL)
async def check_feeds():
    current_time = datetime.now()

    for guild in bot.guilds:
        channel_id = server_config.get_news_channel(guild.id)
        if not channel_id:
            continue

        channel = bot.get_channel(channel_id)
        if not channel:
        await ctx.send("❌ No se pudo encontrar el canal configurado.")
        return

    await ctx.send("🎮 **Actualizando noticias de gaming bajo demanda...**")

    news_found = False
    for feed_name, feed_url in GAMING_FEEDS.items():
        news_items = await fetch_feed(feed_name, feed_url)
        if news_items:
            news_found = True
            await enviar_noticias_agrupadas(channel, feed_name, news_items)

    if not news_found:
        await ctx.send("No se encontraron noticias nuevas en esta actualización.")

    server_config.set_last_update(ctx.guild.id, current_time)

@bot.command()
async def limpiar_cache(ctx, fuente=None):
    """Limpia el caché del bot. Si se especifica una fuente, solo limpia esa fuente"""
    if fuente:
        # Verificar si la fuente existe
        fuente_encontrada = None
        for nombre_fuente in GAMING_FEEDS.keys():
            if nombre_fuente.lower() == fuente.lower():
                fuente_encontrada = nombre_fuente
                break
        
        if fuente_encontrada:
            news_cache.clear_cache(fuente_encontrada)
            await ctx.send(f"🧹 Cache limpiado para la fuente: {fuente_encontrada}")
        else:
            fuentes_disponibles = "\n".join([f"• {name}" for name in GAMING_FEEDS.keys()])
            await ctx.send(f"❌ Fuente no encontrada. Las fuentes disponibles son:\n{fuentes_disponibles}")
    else:
        news_cache.clear_cache()
        await ctx.send("🧹 Cache limpiado completamente")

@bot.command()
async def forzar_actualizar(ctx):
    """Fuerza la actualización de noticias ignorando el caché"""
    news_cache.clear_cache()
    await ctx.send("🔄 Cache limpiado. Forzando actualización de noticias...")
    await actualizar(ctx)

@bot.command()
async def estadisticas(ctx):
    """Muestra estadísticas del bot"""
    embed = discord.Embed(title="Estadísticas del Bot", color=discord.Color.blue())
    embed.add_field(name="Servidores activos", value=str(len(bot.guilds)))
    embed.add_field(name="Feeds configurados", value=str(len(GAMING_FEEDS)))
    embed.add_field(name="Última actualización", 
                   value=format_time(server_config.get_last_update(ctx.guild.id)) if server_config.get_last_update(ctx.guild.id) else "No hay datos")
    await ctx.send(embed=embed)

@configurar_canal.error
@desactivar_noticias.error
async def admin_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ Necesitas permisos de administrador para usar este comando.")

# Iniciar el bot
if __name__ == "__main__":
    TOKEN = os.getenv('DISCORD_TOKEN')
    if not TOKEN:
        logging.error("Error: No se encontró el token de Discord en las variables de entorno")
        exit(1)
        
    while True:
        try:
            bot.run(TOKEN)
        except Exception as e:
            logging.error(f"Error al conectar: {e}")
            logging.info("Reintentando en 30 segundos...")
            time.sleep(30)
            continue

        last_update = server_config.get_last_update(guild.id)
        update_message = "🎮 **Actualizando noticias de gaming**"
        if last_update:
            update_message += f"\nÚltima actualización fue a las {format_time(last_update)}"

        await channel.send(update_message)

        news_found = False
        for feed_name, feed_url in GAMING_FEEDS.items():
            news_items = await fetch_feed(feed_name, feed_url)
            if news_items:
                news_found = True
                await enviar_noticias_agrupadas(channel, feed_name, news_items)

        if not news_found:
            await channel.send("No se encontraron noticias nuevas en esta actualización.")

        server_config.set_last_update(guild.id, current_time)

@bot.event
async def on_ready():
    logging.info(f'{bot.user} ha iniciado sesión')
    if not check_feeds.is_running():
        check_feeds.start()

@bot.event
async def on_guild_join(guild):
    """Envía un mensaje de bienvenida cuando el bot se une a un nuevo servidor"""
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
@commands.has_permissions(administrator=True)
async def configurar_canal(ctx):
    """Configura el canal actual como el canal de noticias"""
    server_config.set_news_channel(ctx.guild.id, ctx.channel.id)
    await ctx.send(f"✅ Canal {ctx.channel.mention} configurado correctamente para recibir noticias de gaming.")

@bot.command()
@commands.has_permissions(administrator=True)
async def desactivar_noticias(ctx):
    """Desactiva las noticias en este servidor"""
    server_config.remove_server(ctx.guild.id)
    await ctx.send("❌ Las noticias han sido desactivadas en este servidor.")

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