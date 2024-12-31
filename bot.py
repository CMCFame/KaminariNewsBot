import discord
import os
import feedparser
import json
import asyncio
import time
import random
from datetime import datetime
from discord.ext import commands, tasks

# Configuración del bot de Discord
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True  # Necesario para detectar servidores
bot = commands.Bot(command_prefix="$", intents=intents)

# Lista de feeds RSS de gaming
GAMING_FEEDS = {
    "Destructoid": "https://www.destructoid.com/feed/",
    "GamesFuze": "https://gamesfuze.com/feed/",
    "Xbox Wire": "https://news.xbox.com/en-us/feed/",
    "Playstation Blog": "https://blog.playstation.com/feed/",
    "PCGamesN": "https://www.pcgamesn.com/feed",
    "ShackNews": "https://www.shacknews.com/feed/rss",
    "GamesIndustry": "http://www.gamesindustry.biz/rss/gamesindustry_news_feed.rss",
    "Escapist Magazine": "https://www.escapistmagazine.com/feed/",
    "Niche Gamer": "https://nichegamer.com/feed/"
,
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
    "Nintendo Everything": "https://nintendoeverything.com/feed/",
    "VGC": "https://www.videogameschronicle.com/category/news/feed/"
}

UPDATE_INTERVAL = 10800  # 3 horas en segundos

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
        with open(self.cache_file, 'w') as f:
            json.dump(self.cache, f)

    def is_new_entry(self, feed_name, entry_id):
        if not entry_id:  # Si el ID está vacío, considerarlo como nuevo
            return True
            
        if feed_name not in self.cache:
            self.cache[feed_name] = []

        if entry_id not in self.cache[feed_name]:
            self.cache[feed_name].append(entry_id)
            # Mantener solo los últimos 50 IDs
            self.cache[feed_name] = self.cache[feed_name][-50:]
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
            # Añadir retraso exponencial entre intentos
            if attempt > 0:
                delay = min(300, (2 ** attempt) + (random.randint(0, 1000) / 1000))
                await asyncio.sleep(delay)
            
            # Configurar un contexto SSL más permisivo para feedparser
            import ssl
            if hasattr(ssl, '_create_unverified_context'):
                ssl._create_default_https_context = ssl._create_unverified_context
            
            feed = feedparser.parse(feed_url)
            
            # Manejar redirecciones y errores
            if hasattr(feed, 'status'):
                if feed.status in [301, 302, 307, 308]:  # Códigos de redirección
                    if 'href' in feed and feed.href != feed_url:
                        print(f"Redirigiendo {feed_name} a: {feed.href}")
                        return await try_fetch_with_backoff(0)
                elif feed.status == 429:  # Too Many Requests
                    if attempt < max_retries:
                        print(f"Rate limit alcanzado para {feed_name}, reintentando...")
                        return await try_fetch_with_backoff(attempt + 1)
                    else:
                        print(f"Máximo de reintentos alcanzado para {feed_name}")
                        return None
                elif feed.status != 200:
                    print(f"Error al obtener {feed_name}: Status {feed.status}")
                    return None
            
            return feed
            
        except Exception as e:
            print(f"Error al procesar {feed_name}: {str(e)}")
            if attempt < max_retries:
                return await try_fetch_with_backoff(attempt + 1)
            return None

    try:
        feed = await try_fetch_with_backoff(0)
        if not feed:
            return []

        news_items = []
        print(f"Procesando {feed_name}: {len(feed.entries)} entradas encontradas")
        
        for entry in feed.entries[:5]:
            entry_id = entry.get('id', '') or entry.get('guid', '') or entry.get('link', '')
            print(f"Verificando entrada: {entry_id}")
            
            if news_cache.is_new_entry(feed_name, entry_id):
                print(f"Nueva entrada encontrada en {feed_name}")
                title = entry.get('title', 'Sin título')
                
                # Procesar la URL del enlace
                if isinstance(entry.get('link'), dict):
                    if 'href' in entry.get('link'):
                        link = entry.get('link')['href']
                elif isinstance(entry.get('links', [{}])[0], dict):
                    link = entry.get('links')[0].get('href', '#')
                else:
                    link = entry.get('link', '#')

                # Asegurarse de que la URL sea una cadena válida
                if isinstance(link, dict) and 'href' in link:
                    link = link['href']
                
                # Eliminar parámetros UTM si existen
                if '?' in link:
                    link = link.split('?')[0]

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
                try:
                    if 'media_thumbnail' in entry and entry['media_thumbnail']:
                        image_url = entry['media_thumbnail'][0].get('url')
                    elif 'media_content' in entry and entry['media_content']:
                        image_url = entry['media_content'][0].get('url')
                    elif hasattr(entry, 'links'):
                        for link in entry.links:
                            if isinstance(link, dict) and link.get('type', '').startswith('image/'):
                                image_url = link.get('href')
                                if image_url and not (image_url.startswith('http://') or image_url.startswith('https://')):
                                    image_url = None
                                break

                    # Verificar que la URL de la imagen sea válida
                    if image_url and not (image_url.startswith('http://') or image_url.startswith('https://')):
                        image_url = None
                except Exception as e:
                    print(f"Error al procesar imagen para {feed_name}: {str(e)}")
                    image_url = None

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
                print(f"Entrada ya existe en caché: {entry_id}")

        return news_items
    except Exception as e:
        print(f"Error al procesar {feed_name}: {str(e)}")
        return []

@tasks.loop(seconds=UPDATE_INTERVAL)
async def check_feeds():
    current_time = datetime.now()

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

        await channel.send(update_message)

        news_found = False
        for feed_name, feed_url in GAMING_FEEDS.items():
            news_items = await fetch_feed(feed_name, feed_url)
            if news_items:
                news_found = True
                try:
                    # Enviar encabezado
                    header = f"▓▓▓▓▓▓▓▓▓▓ Noticias de {feed_name} ▓▓▓▓▓▓▓▓▓▓"
                    await channel.send(f"**{header}**")
                    
                    # Enviar noticias
                    for embed in news_items:
                        await channel.send(embed=embed)
                        await asyncio.sleep(random.uniform(2, 4))
                    
                    # Espacio entre fuentes
                    await channel.send("_ _")
                except Exception as e:
                    print(f"Error al enviar noticias de {feed_name} en {guild.name}: {str(e)}")
                    continue

        if not news_found:
            await channel.send("No se encontraron noticias nuevas en esta actualización.")

        server_config.set_last_update(guild.id, current_time)

@bot.event

async def on_ready():
    if not check_feeds.is_running():
        check_feeds.start()
    else:
        print("El loop de check_feeds ya está en ejecución.")

    print(f'{bot.user} ha iniciado sesión')
    if not check_feeds.is_running():
        check_feeds.start()

@bot.event
async def on_resumed():
    print('Bot reconectado después de una desconexión')

@bot.event
async def on_connect():
    print('Bot conectado a Discord')

@bot.event
async def on_guild_join(guild):
    """Envía un mensaje de bienvenida cuando el bot se une a un nuevo servidor"""
    # Buscar el primer canal donde el bot puede escribir
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
        await ctx.send("❌ No se pudo encontrar el canal configurado.")
        return

    
    await ctx.send("🎮 **Actualizando noticias de gaming bajo demanda...**")
    for feed_name, feed_url in GAMING_FEEDS.items():
        news_items = await fetch_feed(feed_name, feed_url)
        if news_items:
            header = f"▓▓▓▓▓▓▓▓▓▓ Noticias de {feed_name} ▓▓▓▓▓▓▓▓▓▓"
            await ctx.send(f"**{header}**")


    news_found = False
    for feed_name, feed_url in GAMING_FEEDS.items():
        news_items = await fetch_feed(feed_name, feed_url)
        if news_items:
            news_found = True
            for embed in news_items:
                try:
                    await channel.send(embed=embed)
                    await asyncio.sleep(random.uniform(2, 4))
                except discord.HTTPException as e:
                    print(f"Error HTTP al enviar noticia de {feed_name} en {ctx.guild.name}: {str(e)}")
                    if e.code == 50035:  # Invalid Form Body
                        print(f"Detalles del embed que causó el error:")
                        print(f"Título: {embed.title}")
                        print(f"URL: {embed.url}")
                        if embed.thumbnail:
                            print(f"Thumbnail URL: {embed.thumbnail.url}")
                except Exception as e:
                    print(f"Error al enviar noticia de {feed_name} en {ctx.guild.name}: {str(e)}")

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
    news_cache.clear_cache()  # Usa el nuevo método
    await ctx.send("🔄 Cache limpiado. Forzando actualización de noticias...")
    await actualizar(ctx)

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
        await ctx.send("❌ Necesitas permisos de administrador, gestión de canales o gestión del servidor para usar este comando.")

# Iniciar el bot
if __name__ == "__main__":
    TOKEN = os.getenv('DISCORD_TOKEN')
    if not TOKEN:
        print("Error: No se encontró el token de Discord en las variables de entorno")
        exit(1)
    
    max_retries = 5
    retry_count = 0
    
    while retry_count < max_retries:
        try:
            print(f"Iniciando el bot (intento {retry_count + 1} de {max_retries})...")
            # Intentar la conexión
            bot.run(TOKEN, reconnect=True)
            # Si llegamos aquí, la conexión fue exitosa
            print("Bot conectado exitosamente")
            break
        except discord.LoginFailure:
            print("Error: Token de Discord inválido o expirado")
            exit(1)  # Salir inmediatamente si el token es inválido
        except discord.ConnectionClosed as e:
            retry_count += 1
            print(f"Error de conexión (intento {retry_count}): {e}")
            if retry_count < max_retries:
                print("Reintentando en 30 segundos...")
                time.sleep(30)
        except Exception as e:
            retry_count += 1
            print(f"Error inesperado (intento {retry_count}): {type(e).__name__} - {str(e)}")
            if retry_count < max_retries:
                print("Reintentando en 30 segundos...")
                time.sleep(30)
    
    if retry_count >= max_retries:
        print("Número máximo de reintentos alcanzado. Deteniendo el bot.")