# ============================================================
# DFG Discord Bot - Single File Edition
# ============================================================
# المتطلبات:
#   pip install -U discord.py
#
# مهم من Discord Developer Portal:
#   Message Content Intent = ON
#   Server Members Intent  = ON
#
# صلاحيات البوت المهمة:
#   Manage Messages, Moderate Members, Manage Channels,
#   Manage Roles, View Audit Log, Send Messages, Embed Links
# ============================================================

# ============================================================
# 1) الاستيرادات والإعدادات الأساسية
# ============================================================
import discord
from discord.ext import commands, tasks
from discord import app_commands

import sqlite3
import json
import os
import re
import random
import time
import datetime
from collections import OrderedDict


DEVELOPER_ID = 1385335759224963154
BOT_TOKEN = "ضع_توكن_البوت_الخاص_بك_هنا"

# ---------------- XP Settings ----------------
TEXT_XP_MIN = 10
TEXT_XP_MAX = 20
TEXT_XP_COOLDOWN = 60          # رسالة XP واحدة كل 60 ثانية
VOICE_XP_PER_MINUTE = 5
VOICE_MIN_HUMANS = 2            # لازم شخصين بشريين على الأقل في الروم الصوتي

# ---------------- Moderation ----------------
ANTI_LINK_TIMEOUT_MINUTES = 2
MAX_TIMEOUT_MINUTES = 40320     # 28 يوم - الحد الأقصى في Discord

# ---------------- Cache ----------------
MAX_MESSAGE_CACHE = 10000
message_cache = OrderedDict()
xp_cooldowns = {}
last_texts = {}
recent_timeout_context = {}

# دعوات السيرفر: guild_id -> {invite_code: {uses, inviter_id}}
invite_cache = {}

# ---------------- Ticket files ----------------
TICKET_FILE = "ticket_data.json"


# ============================================================
# 2) قاعدة البيانات
# ============================================================
conn = sqlite3.connect("bot_data.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS shortcuts (
    guild_id INTEGER,
    trigger TEXT,
    action TEXT,
    PRIMARY KEY (guild_id, trigger)
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS admin_roles (
    guild_id INTEGER PRIMARY KEY,
    helper_role INTEGER,
    mod_role INTEGER,
    admin_role INTEGER
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS warnings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER,
    user_id INTEGER,
    reason TEXT,
    moderator_id INTEGER,
    date TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS system_config (
    guild_id INTEGER PRIMARY KEY,
    log_channel_id INTEGER,
    welcome_title TEXT,
    welcome_msg TEXT,
    welcome_color TEXT,
    welcome_img TEXT,
    leave_title TEXT,
    leave_msg TEXT,
    leave_color TEXT,
    leave_img TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS auto_roles (
    guild_id INTEGER PRIMARY KEY,
    role_id INTEGER
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS user_xp (
    guild_id INTEGER,
    user_id INTEGER,
    text_xp INTEGER DEFAULT 0,
    voice_xp INTEGER DEFAULT 0,
    level INTEGER DEFAULT 1,
    PRIMARY KEY (guild_id, user_id)
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS level_roles (
    guild_id INTEGER,
    level INTEGER,
    role_id INTEGER,
    PRIMARY KEY (guild_id, level)
)
""")

# إعدادات جديدة حتى لا نكسر قواعد البيانات القديمة
cursor.execute("""
CREATE TABLE IF NOT EXISTS bot_settings (
    guild_id INTEGER PRIMARY KEY,
    levelup_channel_id INTEGER,
    antilink_enabled INTEGER DEFAULT 1
)
""")

# ===================== إضافات جديدة =====================
cursor.execute("""
CREATE TABLE IF NOT EXISTS auto_replies (
    guild_id INTEGER,
    trigger TEXT,
    response TEXT NOT NULL,
    PRIMARY KEY (guild_id, trigger)
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS auto_reply_panels (
    message_id INTEGER PRIMARY KEY,
    guild_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS reaction_role_panels (
    message_id INTEGER PRIMARY KEY,
    guild_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    roles_json TEXT NOT NULL
)
""")

# نضيف رومات الترحيب والمغادرة لقاعدة البيانات القديمة بدون كسرها.
def _ensure_column(table, column, definition):
    cols = [row[1] for row in cursor.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in cols:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

_ensure_column("system_config", "welcome_channel_id", "INTEGER")
_ensure_column("system_config", "leave_channel_id", "INTEGER")

conn.commit()

# ===================== نظام التقديم =====================
cursor.execute("""
CREATE TABLE IF NOT EXISTS application_config (
    guild_id INTEGER PRIMARY KEY,
    accept_role_id INTEGER,
    allowed_role_id INTEGER,
    applications_channel_id INTEGER,
    message_title TEXT DEFAULT '📝 التقديم على الرتبة',
    message_description TEXT DEFAULT 'اضغط الزر بالأسفل لبدء التقديم.'
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS application_questions (
    guild_id INTEGER,
    position INTEGER,
    question TEXT,
    PRIMARY KEY (guild_id, position)
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS application_setup_panels (
    message_id INTEGER PRIMARY KEY,
    guild_id INTEGER,
    channel_id INTEGER
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS application_apply_panels (
    message_id INTEGER PRIMARY KEY,
    guild_id INTEGER,
    channel_id INTEGER
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS application_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER,
    user_id INTEGER,
    message_id INTEGER,
    channel_id INTEGER,
    status TEXT DEFAULT 'pending',
    answers_json TEXT,
    created_at TEXT,
    reviewer_id INTEGER,
    decision_reason TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS application_sessions (
    guild_id INTEGER,
    user_id INTEGER,
    question_index INTEGER DEFAULT 0,
    answers_json TEXT DEFAULT '[]',
    status TEXT DEFAULT 'ready',
    created_at TEXT,
    PRIMARY KEY (guild_id, user_id)
)
""")

conn.commit()


def db_commit():
    conn.commit()


def get_settings(guild_id: int):
    row = cursor.execute(
        "SELECT levelup_channel_id, antilink_enabled FROM bot_settings WHERE guild_id=?",
        (guild_id,)
    ).fetchone()
    if not row:
        cursor.execute(
            "INSERT INTO bot_settings (guild_id, levelup_channel_id, antilink_enabled) VALUES (?, NULL, 1)",
            (guild_id,)
        )
        db_commit()
        return None, 1
    return row


def normalize_reply_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).casefold()


async def handle_auto_reply(message: discord.Message) -> bool:
    if not message.guild or message.author.bot or not message.content.strip():
        return False
    trigger = normalize_reply_text(message.content)
    row = cursor.execute(
        "SELECT response FROM auto_replies WHERE guild_id=? AND trigger=?",
        (message.guild.id, trigger)
    ).fetchone()
    if not row:
        return False
    try:
        await message.channel.send(row[0])
    except (discord.Forbidden, discord.HTTPException):
        pass
    return True


# ============================================================
# 3) ملف بيانات التكتات
# ============================================================
def load_ticket_data():
    if not os.path.exists(TICKET_FILE):
        return {"panels": {}, "stats": {}, "tickets": {}}
    try:
        with open(TICKET_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("panels", {})
        data.setdefault("stats", {})
        data.setdefault("tickets", {})
        return data
    except Exception:
        return {"panels": {}, "stats": {}, "tickets": {}}


ticket_data = load_ticket_data()


def save_ticket_data():
    with open(TICKET_FILE, "w", encoding="utf-8") as f:
        json.dump(ticket_data, f, ensure_ascii=False, indent=2)


# ============================================================
# 4) أدوات عامة
# ============================================================
def utcnow():
    return datetime.datetime.now(datetime.timezone.utc)


def truncate(text, limit=1000):
    if text is None:
        return ""
    text = str(text)
    if len(text) <= limit:
        return text
    return text[:limit - 3] + "..."


def parse_color(value):
    if not value:
        return discord.Color.blurple()
    value = str(value).strip().lower().replace("#", "")
    try:
        return discord.Color(int(value, 16))
    except Exception:
        colors = {
            "red": discord.Color.red(),
            "green": discord.Color.green(),
            "blue": discord.Color.blue(),
            "yellow": discord.Color.gold(),
            "orange": discord.Color.orange(),
            "purple": discord.Color.purple(),
            "black": discord.Color.dark_grey(),
        }
        return colors.get(value, discord.Color.blurple())


async def safe_respond(interaction, content=None, *, embed=None, view=None, ephemeral=False):
    try:
        if interaction.response.is_done():
            return await interaction.followup.send(
                content=content, embed=embed, view=view, ephemeral=ephemeral
            )
        return await interaction.response.send_message(
            content=content, embed=embed, view=view, ephemeral=ephemeral
        )
    except (discord.HTTPException, discord.Forbidden):
        return None


# ============================================================
# 5) صلاحيات الإدارة
# ============================================================
def get_admin_roles(guild_id):
    row = cursor.execute(
        "SELECT helper_role, mod_role, admin_role FROM admin_roles WHERE guild_id=?",
        (guild_id,)
    ).fetchone()
    return row if row else (None, None, None)


def member_has_staff_role(member: discord.Member):
    helper, mod, admin = get_admin_roles(member.guild.id)
    role_ids = {x for x in (helper, mod, admin) if x}
    return any(role.id in role_ids for role in member.roles)


def member_is_admin_exempt(member: discord.Member):
    if member.id == DEVELOPER_ID:
        return True
    if member.guild.owner_id == member.id:
        return True
    if member.guild_permissions.administrator:
        return True

    # رتبة الإدارة المخصصة تعتبر مستثناة أيضاً
    _, _, admin_role = get_admin_roles(member.guild.id)
    if admin_role and any(r.id == admin_role for r in member.roles):
        return True

    return False


async def check_admin_permission(interaction: discord.Interaction, required_level="admin"):
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        return False

    member = interaction.user

    if member.id == DEVELOPER_ID or member.id == interaction.guild.owner_id:
        return True

    if member.guild_permissions.administrator:
        return True

    helper, mod, admin = get_admin_roles(interaction.guild.id)
    role_ids = {x for x in (helper, mod, admin) if x}

    if required_level == "helper":
        return any(r.id in role_ids for r in member.roles)
    if required_level == "mod":
        return any(r.id in {x for x in (mod, admin) if x} for r in member.roles)
    return any(r.id in {x for x in (admin,) if x} for r in member.roles)


# ============================================================
# 6) نظام اللوق
# ============================================================
async def send_system_log(guild: discord.Guild, embed: discord.Embed):
    if not guild:
        return
    row = cursor.execute(
        "SELECT log_channel_id FROM system_config WHERE guild_id=?",
        (guild.id,)
    ).fetchone()
    if not row or not row[0]:
        return

    channel = guild.get_channel(row[0])
    if not channel:
        return

    try:
        await channel.send(embed=embed)
    except (discord.Forbidden, discord.HTTPException):
        pass


def log_embed(title, color=discord.Color.blurple(), description=None):
    return discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=utcnow()
    )


async def get_recent_audit_entry(guild, action, target_id=None, max_age=10):
    try:
        async for entry in guild.audit_logs(limit=8, action=action):
            if target_id is not None:
                if getattr(entry.target, "id", None) != target_id:
                    continue
            age = abs((utcnow() - entry.created_at).total_seconds())
            if age <= max_age:
                return entry
    except (discord.Forbidden, discord.HTTPException):
        return None
    return None


# ============================================================
# 7) نظام XP - صعوبة تدريجية
# ============================================================
def xp_stage_multiplier(level):
    # كل مرحلة أعلى تصبح أصعب
    if level >= 75:
        return 1.50
    if level >= 50:
        return 1.35
    if level >= 25:
        return 1.20
    if level >= 10:
        return 1.10
    return 1.00


def xp_needed_for_level(level):
    """XP المطلوبة للانتقال من المستوى الحالي للمستوى التالي.
    الصيغة تصاعدية شبيهة بفكرة أنظمة XP المشهورة، مع مراحل صعوبة إضافية.
    """
    base = 100 + (50 * level) + (5 * level * level)
    return int(base * xp_stage_multiplier(level))


def calculate_level(total_xp):
    level = 1
    spent = 0

    while True:
        needed = xp_needed_for_level(level)
        if total_xp < spent + needed:
            progress = max(0, total_xp - spent)
            return level, progress, needed
        spent += needed
        level += 1

        # حماية احتياطية من قيم تالفة ضخمة جداً
        if level >= 1000:
            return level, 0, xp_needed_for_level(level)


def cache_message(message: discord.Message):
    if not message.guild:
        return

    data = {
        "guild_id": message.guild.id,
        "channel_id": message.channel.id,
        "author_id": message.author.id,
        "content": message.content or "",
        "created_at": message.created_at,
        "attachments": [a.url for a in message.attachments],
    }

    message_cache[message.id] = data
    message_cache.move_to_end(message.id)

    while len(message_cache) > MAX_MESSAGE_CACHE:
        message_cache.popitem(last=False)


async def add_xp(member: discord.Member, text_amount=0, voice_amount=0):
    if member.bot:
        return

    row = cursor.execute(
        "SELECT text_xp, voice_xp, level FROM user_xp WHERE guild_id=? AND user_id=?",
        (member.guild.id, member.id)
    ).fetchone()

    if row:
        old_text, old_voice, old_level = row
    else:
        old_text, old_voice, old_level = 0, 0, 1

    old_text = old_text or 0
    old_voice = old_voice or 0
    old_level = old_level or 1

    new_text = old_text + text_amount
    new_voice = old_voice + voice_amount
    total = new_text + new_voice

    new_level, _, _ = calculate_level(total)

    cursor.execute("""
        INSERT INTO user_xp (guild_id, user_id, text_xp, voice_xp, level)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(guild_id, user_id) DO UPDATE SET
            text_xp=excluded.text_xp,
            voice_xp=excluded.voice_xp,
            level=excluded.level
    """, (member.guild.id, member.id, new_text, new_voice, new_level))
    db_commit()

    if new_level > old_level:
        await handle_level_up(member, old_level, new_level)


async def handle_level_up(member: discord.Member, old_level, new_level):
    """لا توجد تهاني عامة. التهنئة ترسل في الخاص فقط إذا حصل العضو على رتبة مكافأة."""
    rewarded_roles = []

    for level in range(old_level + 1, new_level + 1):
        row = cursor.execute(
            "SELECT role_id FROM level_roles WHERE guild_id=? AND level=?",
            (member.guild.id, level)
        ).fetchone()
        if not row:
            continue

        role = member.guild.get_role(row[0])
        if not role:
            continue

        try:
            if role not in member.roles:
                await member.add_roles(role, reason=f"DFG Level {level} Reward")
                rewarded_roles.append(role)
        except (discord.Forbidden, discord.HTTPException):
            continue

    # لا نرسل أي تهنئة إذا لم توجد رتبة مكافأة أو لم يستطع البوت إعطاءها.
    if not rewarded_roles:
        return

    role_text = " ".join(role.mention for role in rewarded_roles)
    try:
        await member.send(
            embed=discord.Embed(
                title="🎉 مبروك! حصلت على مكافأة XP",
                description=(
                    f"مبروك {member.mention}! وصلت إلى **المستوى {new_level}** 🔥\n\n"
                    f"🎁 **رتبة المكافأة:** {role_text}"
                ),
                color=discord.Color.gold(),
                timestamp=utcnow()
            )
        )
    except (discord.Forbidden, discord.HTTPException):
        pass

# ============================================================
# 8) نظام الروابط والحماية
# ============================================================
URL_PATTERN = re.compile(
    r"(?i)(?:https?://|www\.)[^\s<>]+|(?<!@)\b(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/[^\s<>]*)?"
)


def contains_link(content: str):
    if not content:
        return False
    return bool(URL_PATTERN.search(content))


def remember_timeout_context(guild_id, user_id, actor_id, reason, duration_text):
    recent_timeout_context[(guild_id, user_id)] = {
        "time": time.monotonic(),
        "actor_id": actor_id,
        "reason": reason,
        "duration": duration_text,
    }


# ============================================================
# 9) نظام التقييمات
# ============================================================
class RatingModal(discord.ui.Modal, title="⭐ تقييم التكت"):
    rating = discord.ui.TextInput(
        label="التقييم من 1 إلى 10",
        placeholder="مثال: 10",
        required=True,
        min_length=1,
        max_length=2
    )

    feedback = discord.ui.TextInput(
        label="الملاحظة",
        placeholder="اكتب ملاحظتك عن الخدمة...",
        required=True,
        style=discord.TextStyle.paragraph,
        max_length=1000
    )

    def __init__(self, ticket_log_message_id, ticket_channel_name, log_channel_id):
        super().__init__()
        self.ticket_log_message_id = ticket_log_message_id
        self.ticket_channel_name = ticket_channel_name
        self.log_channel_id = log_channel_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            score = int(str(self.rating.value).strip())
        except ValueError:
            return await interaction.response.send_message(
                "❌ التقييم لازم يكون رقم من 1 إلى 10.", ephemeral=True
            )

        if score < 1 or score > 10:
            return await interaction.response.send_message(
                "❌ التقييم لازم يكون من 1 إلى 10 فقط.", ephemeral=True
            )

        note = str(self.feedback.value).strip() or "بدون ملاحظة"

        guild = interaction.guild
        # رسالة التقييم تكون في DM، لذلك نبحث عن اللوق عبر ticket_data
        ticket_info = ticket_data.get("tickets", {}).get(str(self.ticket_log_message_id))

        log_channel = None
        if guild and self.log_channel_id:
            log_channel = guild.get_channel(self.log_channel_id)

        # fallback: ابحث في جميع سيرفرات البوت
        if not log_channel:
            for g in bot.guilds:
                ch = g.get_channel(self.log_channel_id) if self.log_channel_id else None
                if ch:
                    log_channel = ch
                    guild = g
                    break

        if not log_channel:
            return await interaction.response.send_message(
                "⚠️ تم استلام تقييمك، لكن لم أجد روم لوق التكت لتحديثه.", ephemeral=True
            )

        try:
            message = await log_channel.fetch_message(self.ticket_log_message_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return await interaction.response.send_message(
                "⚠️ تم استلام تقييمك، لكن رسالة لوق التكت غير موجودة.", ephemeral=True
            )

        embed = message.embeds[0] if message.embeds else discord.Embed(
            title="🎫 Ticket Log",
            color=discord.Color.green()
        )

        # تعديل نفس البانل وعدم إرسال بانل جديد
        found_rating = False
        found_note = False
        for field in embed.fields:
            if field.name == "⭐ التقييم":
                field.value = f"**{score}/10**"
                found_rating = True
            elif field.name == "📝 الملاحظة":
                field.value = truncate(note, 1024)
                found_note = True

        if not found_rating:
            embed.add_field(name="⭐ التقييم", value=f"**{score}/10**", inline=True)
        if not found_note:
            embed.add_field(name="📝 الملاحظة", value=truncate(note, 1024), inline=False)

        embed.set_footer(text=f"تم التقييم بواسطة {interaction.user} • {score}/10")
        embed.color = discord.Color.green()

        try:
            await message.edit(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            return await interaction.response.send_message(
                "⚠️ استلمت التقييم لكن ما قدرت أعدل رسالة اللوق.", ephemeral=True
            )

        # حذف بيانات التقييم المؤقتة إذا كانت موجودة
        for key, value in list(ticket_data.get("tickets", {}).items()):
            if value.get("log_message_id") == self.ticket_log_message_id:
                value["rating"] = score
                value["feedback"] = note
                save_ticket_data()
                break

        await interaction.response.send_message(
            "✅ تم تسجيل تقييمك وتحديث لوق التكت. شكراً لك ❤️",
            ephemeral=True
        )


class DMFeedbackView(discord.ui.View):
    def __init__(self, ticket_log_message_id, ticket_channel_name, log_channel_id):
        super().__init__(timeout=86400)
        self.ticket_log_message_id = ticket_log_message_id
        self.ticket_channel_name = ticket_channel_name
        self.log_channel_id = log_channel_id

    @discord.ui.button(label="⭐ قيّم التكت", style=discord.ButtonStyle.success)
    async def rate_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(
            RatingModal(
                self.ticket_log_message_id,
                self.ticket_channel_name,
                self.log_channel_id
            )
        )


# ============================================================
# 10) تأكيد إغلاق التكت
# ============================================================
class CloseConfirmView(discord.ui.View):
    def __init__(self, ticket_view):
        super().__init__(timeout=60)
        self.ticket_view = ticket_view

    def can_close(self, member: discord.Member):
        if member_is_admin_exempt(member):
            return True
        if self.ticket_view.claimed_by and member.id == self.ticket_view.claimed_by:
            return True
        return False

    @discord.ui.button(label="✅ نعم، إغلاق", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member) or not self.can_close(interaction.user):
            return await interaction.response.send_message(
                "❌ فقط الإدارة أو الإداري المستلم للتكت يستطيع تأكيد الإغلاق.",
                ephemeral=True
            )

        await interaction.response.send_message("🔒 جاري إغلاق التكت...", ephemeral=True)
        await self.ticket_view.finish_close(interaction)

    @discord.ui.button(label="❌ إلغاء", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member) or not self.can_close(interaction.user):
            return await interaction.response.send_message(
                "❌ هذا التأكيد مخصص للإدارة أو الإداري المستلم فقط.",
                ephemeral=True
            )
        await interaction.response.edit_message(content="❌ تم إلغاء إغلاق التكت.", view=None)


# ============================================================
# 11) نظام التكتات
# ============================================================
TICKET_BUTTON_STYLES = {
    "blue": discord.ButtonStyle.primary,
    "أزرق": discord.ButtonStyle.primary,
    "gray": discord.ButtonStyle.secondary,
    "grey": discord.ButtonStyle.secondary,
    "رمادي": discord.ButtonStyle.secondary,
    "green": discord.ButtonStyle.success,
    "أخضر": discord.ButtonStyle.success,
    "red": discord.ButtonStyle.danger,
    "أحمر": discord.ButtonStyle.danger,
}


def normalize_ticket_style(value):
    value = str(value).strip().lower()
    return value if value in TICKET_BUTTON_STYLES else "blue"


class TicketControlView(discord.ui.View):
    def __init__(self, ticket_type, log_channel_id, owner_id, staff_role_id, claimed_by=None, created_at=None):
        super().__init__(timeout=None)
        self.ticket_type = ticket_type
        self.log_channel_id = log_channel_id
        self.owner_id = owner_id
        self.staff_role_id = staff_role_id
        self.claimed_by = claimed_by
        self.created_at = created_at or utcnow()

        # استرجاع حالة الاستلام بعد إعادة تشغيل البوت
        for item in self.children:
            if isinstance(item, discord.ui.Button) and item.custom_id == "dfg_ticket_claim":
                if self.claimed_by:
                    item.label = "✅ تم استلام التكت"
                    item.disabled = True

    def is_staff(self, member: discord.Member):
        if member_is_admin_exempt(member):
            return True
        if self.staff_role_id and any(r.id == self.staff_role_id for r in member.roles):
            return True
        return member_has_staff_role(member)

    def is_owner(self, member: discord.Member):
        return member.id == self.owner_id

    @discord.ui.button(
        label="✋ استلام التكت",
        style=discord.ButtonStyle.primary,
        custom_id="dfg_ticket_claim"
    )
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member) or not self.is_staff(interaction.user):
            return await interaction.response.send_message(
                "❌ هذا الزر للإدارة فقط.", ephemeral=True
            )

        if self.claimed_by:
            return await interaction.response.send_message(
                "⚠️ التكت مستلم بالفعل.", ephemeral=True
            )

        self.claimed_by = interaction.user.id

        info = ticket_data.get("tickets", {}).get(str(interaction.channel.id))
        if info:
            info["claimed_by"] = self.claimed_by
            save_ticket_data()

        # إحصائيات الاستلام
        guild_stats = ticket_data.setdefault("stats", {}).setdefault(str(interaction.guild.id), {})
        guild_stats[str(interaction.user.id)] = guild_stats.get(str(interaction.user.id), 0) + 1
        save_ticket_data()

        button.label = "✅ تم استلام التكت"
        button.disabled = True

        try:
            await interaction.channel.edit(
                name=f"ticket-{interaction.user.display_name}"[:90]
            )
        except (discord.Forbidden, discord.HTTPException):
            pass

        await interaction.response.edit_message(view=self)
        await interaction.followup.send(
            f"✋ تم استلام التكت بواسطة {interaction.user.mention}",
            ephemeral=False
        )

    @discord.ui.button(
        label="🔒 إغلاق التكت",
        style=discord.ButtonStyle.danger,
        custom_id="dfg_ticket_close"
    )
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("❌ لا يمكن تنفيذ ذلك هنا.", ephemeral=True)

        # فقط الإدارة العامة أو الإداري المستلم يستطيع الإغلاق مباشرة
        if member_is_admin_exempt(interaction.user) or (
            self.claimed_by is not None and interaction.user.id == self.claimed_by
        ):
            await interaction.response.send_message("🔒 جاري إغلاق التكت...", ephemeral=True)
            await self.finish_close(interaction)
            return

        # صاحب التكت لا يغلق مباشرة، بل يطلب من المستلم
        if self.is_owner(interaction.user):
            if not self.claimed_by:
                return await interaction.response.send_message(
                    "⚠️ التكت لم يتم استلامه من إداري حتى الآن. اطلب من الإدارة إغلاقه.",
                    ephemeral=True
                )

            staff = interaction.guild.get_member(self.claimed_by)
            if not staff:
                return await interaction.response.send_message(
                    "⚠️ الإداري المستلم غير موجود حالياً. اطلب من الإدارة إغلاق التكت.",
                    ephemeral=True
                )

            embed = discord.Embed(
                title="🔒 طلب إغلاق التكت",
                description=(
                    f"{staff.mention}\n\n"
                    f"العضو {interaction.user.mention} يريد إغلاق التكت.\n"
                    f"**هل تريد الإغلاق؟**"
                ),
                color=discord.Color.orange(),
                timestamp=utcnow()
            )

            await interaction.response.send_message(
                embed=embed,
                view=CloseConfirmView(self)
            )
            return

        return await interaction.response.send_message(
            "❌ لا تملك صلاحية إغلاق هذا التكت.", ephemeral=True
        )

    async def finish_close(self, interaction: discord.Interaction):
        channel = interaction.channel
        guild = interaction.guild
        if not channel or not guild:
            return

        info = ticket_data.get("tickets", {}).get(str(channel.id), {})
        owner_id = info.get("owner_id", self.owner_id)
        owner = guild.get_member(owner_id)

        # ----------------------------------------------------
        # إنشاء لوق التكت + التقييم: لم يتم التقييم
        # ----------------------------------------------------
        log_channel = guild.get_channel(self.log_channel_id) if self.log_channel_id else None
        log_message = None

        if log_channel:
            created_at = info.get("created_at") or self.created_at.isoformat()
            closed_at = utcnow().isoformat()
            claimed_id = info.get("claimed_by") or self.claimed_by
            claimed_member = guild.get_member(claimed_id) if claimed_id else None

            embed = discord.Embed(
                title="🎫 Ticket Log",
                description=f"**التكت:** `{channel.name}`",
                color=discord.Color.blurple(),
                timestamp=utcnow()
            )
            embed.add_field(
                name="👤 العضو",
                value=f"<@{owner_id}>" if owner_id else "غير معروف",
                inline=True
            )
            embed.add_field(
                name="🧑‍💼 الإداري المستلم",
                value=claimed_member.mention if claimed_member else "لم يتم الاستلام",
                inline=True
            )
            embed.add_field(
                name="📂 القسم",
                value=self.ticket_type,
                inline=True
            )
            embed.add_field(name="🕐 وقت الإنشاء", value=f"<t:{int(datetime.datetime.fromisoformat(created_at).timestamp())}:F>" if created_at else "غير معروف", inline=False)
            embed.add_field(name="🔒 وقت الإغلاق", value=f"<t:{int(datetime.datetime.fromisoformat(closed_at).timestamp())}:F>", inline=False)
            embed.add_field(name="⭐ التقييم", value="لم يتم التقييم", inline=True)
            embed.add_field(name="📝 الملاحظة", value="لم يتم التقييم", inline=False)
            embed.set_footer(text="سيتم تحديث نفس اللوق عند وصول التقييم")

            try:
                log_message = await log_channel.send(embed=embed)
            except (discord.Forbidden, discord.HTTPException):
                log_message = None

        # حفظ بيانات اللوق قبل حذف التكت
        if log_message:
            info["log_message_id"] = log_message.id
            info["log_channel_id"] = log_channel.id
            info["closed_at"] = utcnow().isoformat()
            info["rating"] = None
            info["feedback"] = None
            save_ticket_data()

        # ----------------------------------------------------
        # إرسال طلب التقييم للعضو
        # ----------------------------------------------------
        if owner:
            try:
                if log_message:
                    await owner.send(
                        embed=discord.Embed(
                            title="⭐ قيّم تجربتك",
                            description=(
                                f"تم إغلاق تكتك **{channel.name}**.\n\n"
                                "نحتاج تقييمك للخدمة، اضغط الزر واكتب تقييمك وملاحظتك."
                            ),
                            color=discord.Color.gold(),
                            timestamp=utcnow()
                        ),
                        view=DMFeedbackView(
                            log_message.id,
                            channel.name,
                            log_channel.id
                        )
                    )
            except (discord.Forbidden, discord.HTTPException):
                pass

        # لوق إضافي عن الإغلاق
        close_embed = log_embed("🔒 تم إغلاق تكت", discord.Color.orange())
        close_embed.add_field(name="التكت", value=f"`{channel.name}`", inline=True)
        close_embed.add_field(name="بواسطة", value=interaction.user.mention, inline=True)
        await send_system_log(guild, close_embed)

        # إزالة التكت من قائمة التكتات المفتوحة بعد حفظ اللوق
        ticket_data.get("tickets", {}).pop(str(channel.id), None)
        save_ticket_data()

        try:
            await channel.delete(reason=f"DFG Ticket Closed by {interaction.user}")
        except (discord.Forbidden, discord.HTTPException):
            pass


class DynamicLauncher(discord.ui.View):
    def __init__(self, tickets_config):
        super().__init__(timeout=None)
        self.tickets_config = tickets_config

        for index, data in enumerate(tickets_config):
            style = TICKET_BUTTON_STYLES.get(
                normalize_ticket_style(data.get("button_color", "blue")),
                discord.ButtonStyle.primary
            )
            button = discord.ui.Button(
                label=data.get("name", f"تكت {index + 1}")[:80],
                style=style,
                emoji=data.get("emoji"),
                custom_id=f"dfg_ticket_open_{index}"
            )
            button.callback = self.make_callback(data)
            self.add_item(button)

    def make_callback(self, ticket_config):
        async def callback(interaction: discord.Interaction):
            guild = interaction.guild
            member = interaction.user
            if not guild or not isinstance(member, discord.Member):
                return await interaction.response.send_message("❌ لا يمكن فتح تكت هنا.", ephemeral=True)

            role = guild.get_role(int(ticket_config.get("role_id", 0)))
            category = guild.get_channel(int(ticket_config.get("category_id", 0)))

            if not role or not isinstance(category, discord.CategoryChannel):
                return await interaction.response.send_message(
                    "❌ إعدادات هذا التكت غير صحيحة (الرتبة أو الكاتيجوري غير موجود).",
                    ephemeral=True
                )

            # منع فتح أكثر من تكت من نفس النوع لنفس العضو
            for ch_id, info in ticket_data.get("tickets", {}).items():
                if info.get("guild_id") != guild.id:
                    continue
                if info.get("owner_id") != member.id:
                    continue
                if info.get("ticket_type") != ticket_config.get("name"):
                    continue
                existing = guild.get_channel(int(ch_id))
                if existing:
                    return await interaction.response.send_message(
                        f"⚠️ لديك تكت مفتوح بالفعل: {existing.mention}",
                        ephemeral=True
                    )

            await interaction.response.defer(ephemeral=True)

            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                member: discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    attach_files=True,
                    embed_links=True
                ),
                role: discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    manage_messages=True
                ),
            }

            if guild.me:
                overwrites[guild.me] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    manage_channels=True,
                    manage_messages=True
                )

            try:
                channel = await guild.create_text_channel(
                    name=f"ticket-{member.name}"[:90],
                    category=category,
                    overwrites=overwrites,
                    reason=f"DFG Ticket opened by {member}"
                )
            except (discord.Forbidden, discord.HTTPException) as e:
                return await interaction.followup.send(
                    f"❌ ما قدرت أفتح التكت. تأكد من صلاحيات البوت.\n`{e}`",
                    ephemeral=True
                )

            created_at = utcnow().isoformat()
            ticket_data.setdefault("tickets", {})[str(channel.id)] = {
                "guild_id": guild.id,
                "owner_id": member.id,
                "ticket_type": ticket_config.get("name", "Ticket"),
                "role_id": role.id,
                "staff_role_id": role.id,
                "category_id": category.id,
                "created_at": created_at,
                "claimed_by": None,
                "log_channel_id": ticket_config.get("log_channel_id"),
                "rating": None,
                "feedback": None,
            }
            save_ticket_data()

            embed = discord.Embed(
                title=f"🎫 {ticket_config.get('name', 'Ticket')}",
                description=ticket_config.get(
                    "welcome_msg",
                    f"أهلاً {member.mention}! اكتب مشكلتك هنا وسيتم الرد عليك من الإدارة."
                ),
                color=discord.Color.blurple(),
                timestamp=utcnow()
            )
            embed.add_field(name="👤 العضو", value=member.mention, inline=True)
            embed.add_field(name="📂 القسم", value=ticket_config.get("name", "Ticket"), inline=True)
            embed.set_footer(text="DFG Ticket System")

            log_channel_id = ticket_config.get("log_channel_id")
            view = TicketControlView(
                ticket_config.get("name", "Ticket"),
                log_channel_id,
                member.id,
                role.id,
                claimed_by=None,
                created_at=utcnow()
            )

            try:
                control_message = await channel.send(
                    content=member.mention,
                    embed=embed,
                    view=view
                )
            except (discord.Forbidden, discord.HTTPException):
                ticket_data.get("tickets", {}).pop(str(channel.id), None)
                save_ticket_data()
                try:
                    await channel.delete(reason="Failed to send ticket panel")
                except Exception:
                    pass
                return await interaction.followup.send("❌ فشل إرسال لوحة التكت.", ephemeral=True)

            info = ticket_data["tickets"][str(channel.id)]
            info["control_message_id"] = control_message.id
            save_ticket_data()

            # persistent view للتكت نفسه
            try:
                bot.add_view(view, message_id=control_message.id)
            except Exception:
                pass

            await interaction.followup.send(
                f"✅ تم فتح التكت: {channel.mention}", ephemeral=True
            )

        return callback


class AddTicketModal(discord.ui.Modal, title="➕ إضافة قسم تكت"):
    name = discord.ui.TextInput(
        label="اسم الزر",
        placeholder="مثال: الدعم الفني",
        max_length=80
    )
    role_id = discord.ui.TextInput(
        label="ID رتبة الإدارة",
        placeholder="123456789..."
    )
    category_id = discord.ui.TextInput(
        label="ID الكاتيجوري",
        placeholder="123456789..."
    )
    welcome_msg = discord.ui.TextInput(
        label="رسالة التكت",
        placeholder="اكتب رسالة الترحيب...",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=1500
    )
    button_color = discord.ui.TextInput(
        label="لون الزر",
        placeholder="blue / gray / green / red",
        required=False,
        max_length=10
    )

    def __init__(self, setup_view):
        super().__init__()
        self.setup_view = setup_view

    async def on_submit(self, interaction: discord.Interaction):
        try:
            role_id = int(str(self.role_id.value).strip())
            category_id = int(str(self.category_id.value).strip())
        except ValueError:
            return await interaction.response.send_message(
                "❌ الـ IDs لازم تكون أرقام.", ephemeral=True
            )

        role = interaction.guild.get_role(role_id)
        category = interaction.guild.get_channel(category_id)
        if not role:
            return await interaction.response.send_message("❌ رتبة الإدارة غير موجودة.", ephemeral=True)
        if not isinstance(category, discord.CategoryChannel):
            return await interaction.response.send_message("❌ الـ ID ليس لكاتيجوري.", ephemeral=True)

        color = normalize_ticket_style(self.button_color.value or "blue")

        data = {
            "name": str(self.name.value).strip(),
            "role_id": role_id,
            "category_id": category_id,
            "welcome_msg": str(self.welcome_msg.value).strip() or "أهلاً بك! اكتب طلبك وسيتم الرد عليك.",
            "button_color": color,
            "log_channel_id": self.setup_view.log_channel_id,
        }

        self.setup_view.tickets_list.append(data)
        await interaction.response.send_message(
            f"✅ تمت إضافة **{data['name']}** — لون الزر: **{color}**",
            ephemeral=True
        )


class MainMessageModal(discord.ui.Modal, title="📝 رسالة لوحة التكت"):
    title_text = discord.ui.TextInput(
        label="العنوان",
        placeholder="🎫 الدعم الفني",
        max_length=256
    )
    description = discord.ui.TextInput(
        label="الوصف",
        placeholder="اختر القسم المناسب...",
        style=discord.TextStyle.paragraph,
        max_length=4000
    )

    def __init__(self, setup_view):
        super().__init__()
        self.setup_view = setup_view

    async def on_submit(self, interaction: discord.Interaction):
        self.setup_view.main_title = str(self.title_text.value)
        self.setup_view.main_description = str(self.description.value)
        await interaction.response.send_message("✅ تم حفظ رسالة لوحة التكت.", ephemeral=True)


class SetupDashboardView(discord.ui.View):
    def __init__(self, log_channel_id=None):
        super().__init__(timeout=600)
        self.tickets_list = []
        self.log_channel_id = log_channel_id
        self.main_title = "🎫 نظام التكتات"
        self.main_description = "اختر القسم المناسب من الأزرار بالأسفل."

    @discord.ui.button(label="➕ إضافة تكت", style=discord.ButtonStyle.success)
    async def add_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await check_admin_permission(interaction, "admin"):
            return await interaction.response.send_message("❌ للإدارة فقط.", ephemeral=True)
        await interaction.response.send_modal(AddTicketModal(self))

    @discord.ui.button(label="📝 تعديل الرسالة", style=discord.ButtonStyle.primary)
    async def edit_message(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await check_admin_permission(interaction, "admin"):
            return await interaction.response.send_message("❌ للإدارة فقط.", ephemeral=True)
        await interaction.response.send_modal(MainMessageModal(self))

    @discord.ui.button(label="📋 عرض الأقسام", style=discord.ButtonStyle.secondary)
    async def list_tickets(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.tickets_list:
            return await interaction.response.send_message("لا توجد أقسام مضافة.", ephemeral=True)

        lines = []
        for i, item in enumerate(self.tickets_list, 1):
            lines.append(
                f"**{i}. {item['name']}** — اللون: `{item.get('button_color', 'blue')}`"
            )
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @discord.ui.button(label="🚀 نشر اللوحة", style=discord.ButtonStyle.success)
    async def deploy(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await check_admin_permission(interaction, "admin"):
            return await interaction.response.send_message("❌ للإدارة فقط.", ephemeral=True)

        if not self.tickets_list:
            return await interaction.response.send_message("❌ أضف قسم تكت واحد على الأقل.", ephemeral=True)

        embed = discord.Embed(
            title=self.main_title,
            description=self.main_description,
            color=discord.Color.blurple()
        )
        embed.set_footer(text="DFG Ticket System")

        view = DynamicLauncher(self.tickets_list)
        try:
            message = await interaction.channel.send(embed=embed, view=view)
        except (discord.Forbidden, discord.HTTPException):
            return await interaction.response.send_message("❌ فشل نشر لوحة التكت.", ephemeral=True)

        # حفظ اللوحة حتى يمكن إعادتها بعد إعادة تشغيل البوت
        ticket_data.setdefault("panels", {})[str(message.id)] = {
            "guild_id": interaction.guild.id,
            "channel_id": interaction.channel.id,
            "log_channel_id": self.log_channel_id,
            "tickets": self.tickets_list,
            "title": self.main_title,
            "description": self.main_description,
        }
        save_ticket_data()

        try:
            bot.add_view(view, message_id=message.id)
        except Exception:
            pass

        await interaction.response.send_message("✅ تم نشر لوحة التكت وحفظها.", ephemeral=True)


# ============================================================
# 25) نظام التقديم على الرتبة
# ============================================================
def get_application_config(guild_id):
    row = cursor.execute(
        "SELECT accept_role_id, allowed_role_id, applications_channel_id, message_title, message_description "
        "FROM application_config WHERE guild_id=?",
        (guild_id,)
    ).fetchone()
    if not row:
        cursor.execute(
            "INSERT INTO application_config (guild_id) VALUES (?)",
            (guild_id,)
        )
        db_commit()
        return {
            "accept_role_id": None,
            "allowed_role_id": None,
            "applications_channel_id": None,
            "message_title": "📝 التقديم على الرتبة",
            "message_description": "اضغط الزر بالأسفل لبدء التقديم."
        }
    return {
        "accept_role_id": row[0],
        "allowed_role_id": row[1],
        "applications_channel_id": row[2],
        "message_title": row[3] or "📝 التقديم على الرتبة",
        "message_description": row[4] or "اضغط الزر بالأسفل لبدء التقديم."
    }


def get_application_questions(guild_id):
    rows = cursor.execute(
        "SELECT position, question FROM application_questions WHERE guild_id=? ORDER BY position",
        (guild_id,)
    ).fetchall()
    return [q for _, q in rows]


def set_application_config(guild_id, **values):
    get_application_config(guild_id)
    allowed = {
        "accept_role_id", "allowed_role_id", "applications_channel_id",
        "message_title", "message_description"
    }
    values = {k: v for k, v in values.items() if k in allowed}
    if not values:
        return
    assignments = ", ".join(f"{key}=?" for key in values)
    params = list(values.values()) + [guild_id]
    cursor.execute(f"UPDATE application_config SET {assignments} WHERE guild_id=?", params)
    db_commit()


def get_pending_application(guild_id, user_id):
    return cursor.execute(
        "SELECT id, message_id, channel_id, status FROM application_requests "
        "WHERE guild_id=? AND user_id=? AND status='pending' ORDER BY id DESC LIMIT 1",
        (guild_id, user_id)
    ).fetchone()


def get_active_application_session(guild_id, user_id):
    # الجلسة لا تنتهي بوقت. إذا بدأ العضو من جديد يتم استبدال الجلسة القديمة.
    return cursor.execute(
        "SELECT status, question_index, created_at FROM application_sessions "
        "WHERE guild_id=? AND user_id=? AND status='interviewing'",
        (guild_id, user_id)
    ).fetchone()


class ApplicationRoleSelectView(discord.ui.View):
    def __init__(self, mode):
        super().__init__(timeout=120)
        self.mode = mode
        select = discord.ui.RoleSelect(
            placeholder="اختر الرتبة...",
            min_values=1,
            max_values=1
        )
        select.callback = self.callback
        self.add_item(select)

    async def callback(self, interaction: discord.Interaction):
        role = self.children[0].values[0]
        if self.mode == "accept":
            set_application_config(interaction.guild.id, accept_role_id=role.id)
            text = f"✅ رتبة القبول: {role.mention}"
        else:
            set_application_config(interaction.guild.id, allowed_role_id=role.id)
            text = f"✅ الرتبة المسموح لها بالتقديم: {role.mention}"
        await interaction.response.edit_message(content=text, view=None)


class ApplicationChannelSelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)
        select = discord.ui.ChannelSelect(
            placeholder="اختر روم استقبال الطلبات...",
            min_values=1,
            max_values=1,
            channel_types=[discord.ChannelType.text]
        )
        select.callback = self.callback
        self.add_item(select)

    async def callback(self, interaction: discord.Interaction):
        channel = self.children[0].values[0]
        set_application_config(interaction.guild.id, applications_channel_id=channel.id)
        await interaction.response.edit_message(
            content=f"✅ روم استقبال طلبات التقديم: {channel.mention}",
            view=None
        )


class ApplicationQuestionSetup:
    def __init__(self, guild_id, count):
        self.guild_id = guild_id
        self.count = count
        self.questions = []


class ApplicationQuestionCountView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)
        options = [
            discord.SelectOption(label=f"{i} أسئلة", value=str(i), emoji="📝")
            for i in range(1, 11)
        ]
        select = discord.ui.Select(
            placeholder="اختر عدد الأسئلة من 1 إلى 10...",
            min_values=1,
            max_values=1,
            options=options
        )
        select.callback = self.callback
        self.add_item(select)

    async def callback(self, interaction: discord.Interaction):
        count = int(self.children[0].values[0])
        setup = ApplicationQuestionSetup(interaction.guild.id, count)
        await interaction.response.send_modal(
            ApplicationQuestionModal(setup, 0)
        )


class ApplicationQuestionModal(discord.ui.Modal):
    def __init__(self, setup, start):
        super().__init__(title=f"📝 الأسئلة {start + 1}-{min(start + 5, setup.count)}")
        self.setup = setup
        self.start = start
        self.inputs = []

        for index in range(start, min(start + 5, setup.count)):
            field = discord.ui.TextInput(
                label=f"السؤال {index + 1}",
                placeholder="اكتب السؤال هنا...",
                required=True,
                max_length=200
            )
            self.inputs.append(field)
            self.add_item(field)

    async def on_submit(self, interaction: discord.Interaction):
        self.setup.questions.extend(str(field.value).strip() for field in self.inputs)

        if self.start + 5 < self.setup.count:
            return await interaction.response.send_modal(
                ApplicationQuestionModal(self.setup, self.start + 5)
            )

        questions = [q for q in self.setup.questions if q]
        if not questions:
            return await interaction.response.send_message("❌ يجب إضافة سؤال واحد على الأقل.", ephemeral=True)

        cursor.execute("DELETE FROM application_questions WHERE guild_id=?", (self.setup.guild_id,))
        for position, question in enumerate(questions, 1):
            cursor.execute(
                "INSERT INTO application_questions (guild_id, position, question) VALUES (?, ?, ?)",
                (self.setup.guild_id, position, question)
            )
        db_commit()

        await interaction.response.send_message(
            f"✅ تم حفظ **{len(questions)}** سؤال بالترتيب.",
            ephemeral=True
        )


class ApplicationMessageModal(discord.ui.Modal, title="📝 رسالة التقديم"):
    title_text = discord.ui.TextInput(
        label="عنوان رسالة التقديم",
        placeholder="📝 التقديم على الرتبة",
        max_length=256
    )
    description = discord.ui.TextInput(
        label="رسالة التقديم",
        placeholder="اكتب الرسالة التي سيشاهدها الأعضاء...",
        style=discord.TextStyle.paragraph,
        max_length=4000
    )

    async def on_submit(self, interaction: discord.Interaction):
        set_application_config(
            interaction.guild.id,
            message_title=str(self.title_text.value).strip(),
            message_description=str(self.description.value).strip()
        )
        await interaction.response.send_message("✅ تم حفظ رسالة التقديم.", ephemeral=True)


class ApplicationControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def admin_only(self, interaction):
        if not await check_admin_permission(interaction, "admin"):
            await interaction.response.send_message("❌ لوحة التقديم للإدارة فقط.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="🎯 رتبة القبول", style=discord.ButtonStyle.success, custom_id="dfg_app_accept_role")
    async def accept_role(self, interaction, button):
        if not await self.admin_only(interaction):
            return
        await interaction.response.send_message(
            "اختر الرتبة التي سيحصل عليها العضو عند القبول:",
            view=ApplicationRoleSelectView("accept"),
            ephemeral=True
        )

    @discord.ui.button(label="👤 رتبة السماح بالتقديم", style=discord.ButtonStyle.primary, custom_id="dfg_app_allowed_role")
    async def allowed_role(self, interaction, button):
        if not await self.admin_only(interaction):
            return
        await interaction.response.send_message(
            "اختر الرتبة التي يسمح لها بالتقديم:",
            view=ApplicationRoleSelectView("allowed"),
            ephemeral=True
        )

    @discord.ui.button(label="📝 إضافة الأسئلة", style=discord.ButtonStyle.secondary, custom_id="dfg_app_questions")
    async def questions(self, interaction, button):
        if not await self.admin_only(interaction):
            return
        await interaction.response.send_message(
            "اختر عدد الأسئلة. يمكن اختيار من 1 إلى 10، وسيتم إدخالها على دفعتين إذا اخترت أكثر من 5.",
            view=ApplicationQuestionCountView(),
            ephemeral=True
        )

    @discord.ui.button(label="💬 رسالة التقديم", style=discord.ButtonStyle.primary, custom_id="dfg_app_message")
    async def application_message(self, interaction, button):
        if not await self.admin_only(interaction):
            return
        await interaction.response.send_modal(ApplicationMessageModal())

    @discord.ui.button(label="📥 روم الطلبات", style=discord.ButtonStyle.secondary, custom_id="dfg_app_channel")
    async def applications_channel(self, interaction, button):
        if not await self.admin_only(interaction):
            return
        await interaction.response.send_message(
            "اختر الروم الذي ستصل إليه طلبات التقديم:",
            view=ApplicationChannelSelectView(),
            ephemeral=True
        )

    @discord.ui.button(label="🚀 نشر التقديم", style=discord.ButtonStyle.success, custom_id="dfg_app_publish")
    async def publish(self, interaction, button):
        if not await self.admin_only(interaction):
            return

        config = get_application_config(interaction.guild.id)
        questions = get_application_questions(interaction.guild.id)

        missing = []
        if not config["accept_role_id"]:
            missing.append("رتبة القبول")
        if not config["allowed_role_id"]:
            missing.append("رتبة السماح بالتقديم")
        if not config["applications_channel_id"]:
            missing.append("روم الطلبات")
        if not questions:
            missing.append("الأسئلة")

        if missing:
            return await interaction.response.send_message(
                "❌ لم يكتمل الإعداد. الناقص: " + "، ".join(missing),
                ephemeral=True
            )

        embed = discord.Embed(
            title=config["message_title"],
            description=config["message_description"],
            color=discord.Color.blurple(),
            timestamp=utcnow()
        )
        embed.set_footer(text="اضغط الزر بالأسفل لبدء التقديم")

        try:
            message = await interaction.channel.send(embed=embed, view=ApplicationApplyView())
            cursor.execute(
                "INSERT OR REPLACE INTO application_apply_panels (message_id,guild_id,channel_id) VALUES (?,?,?)",
                (message.id, interaction.guild.id, interaction.channel.id)
            )
            db_commit()
            bot.add_view(ApplicationApplyView(), message_id=message.id)
            await interaction.response.send_message("✅ تم نشر لوحة التقديم.", ephemeral=True)
        except (discord.Forbidden, discord.HTTPException) as e:
            await interaction.response.send_message(f"❌ فشل نشر لوحة التقديم.\n`{e}`", ephemeral=True)


class ApplicationApplyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="📝 ابدأ التقديم", style=discord.ButtonStyle.success, custom_id="dfg_app_start")
    async def start_application(self, interaction, button):
        guild = interaction.guild
        member = interaction.user
        if not guild or not isinstance(member, discord.Member):
            return await interaction.response.send_message("❌ هذا الزر يعمل داخل السيرفر فقط.", ephemeral=True)

        config = get_application_config(guild.id)
        accept_role = guild.get_role(config["accept_role_id"]) if config["accept_role_id"] else None
        allowed_role = guild.get_role(config["allowed_role_id"]) if config["allowed_role_id"] else None

        if accept_role and accept_role in member.roles:
            return await interaction.response.send_message(
                "❌ لديك رتبة القبول بالفعل، ولا يمكنك التقديم مرة أخرى.", ephemeral=True
            )

        if allowed_role and allowed_role not in member.roles and not member.guild_permissions.administrator:
            return await interaction.response.send_message(
                f"❌ لا يمكنك التقديم. يجب أن تكون لديك رتبة {allowed_role.mention}.", ephemeral=True
            )

        if get_pending_application(guild.id, member.id):
            return await interaction.response.send_message(
                "⏳ لديك طلب تقديم قيد المراجعة بالفعل. انتظر قرار الإدارة.", ephemeral=True
            )

        questions = get_application_questions(guild.id)
        if not questions:
            return await interaction.response.send_message("❌ لم يتم إعداد أسئلة التقديم بعد.", ephemeral=True)

        # أي جلسة قديمة يتم استبدالها، حتى لو أغلق العضو الخاص أو خرج من الاستجواب.
        cursor.execute(
            "INSERT INTO application_sessions (guild_id,user_id,question_index,answers_json,status,created_at) "
            "VALUES (?,?,?,?,?,?) ON CONFLICT(guild_id,user_id) DO UPDATE SET "
            "question_index=0, answers_json='[]', status='interviewing', created_at=excluded.created_at",
            (guild.id, member.id, 0, "[]", "interviewing", utcnow().isoformat())
        )
        db_commit()

        try:
            await member.send(
                embed=discord.Embed(
                    title="📝 بدء التقديم",
                    description=(
                        f"مرحباً {member.mention}!\n\n"
                        f"سيتم سؤالك **{len(questions)}** أسئلة بالترتيب.\n"
                        "أرسل إجابتك على كل سؤال برسالة عادية هنا.\n"
                        "لا توجد مهلة زمنية؛ وإذا أوقفت الاستجواب يمكنك البدء من جديد من لوحة التقديم."
                    ),
                    color=discord.Color.blurple()
                )
            )
            await member.send(f"**السؤال 1 من {len(questions)}:**\n{questions[0]}")
        except discord.Forbidden:
            cursor.execute("DELETE FROM application_sessions WHERE guild_id=? AND user_id=?", (guild.id, member.id))
            db_commit()
            return await interaction.response.send_message(
                "❌ لا أستطيع إرسال الخاص لك. افتح الرسائل الخاصة من السيرفر ثم حاول مرة أخرى.",
                ephemeral=True
            )

        await interaction.response.send_message(
            "📩 بدأ التقديم في الخاص. أرسل إجاباتك على الأسئلة برسائل عادية.", ephemeral=True
        )


class ApplicationDMStartView(discord.ui.View):
    # احتياط للتوافق مع أي لوحة قديمة؛ التقديم الجديد لا يستخدم هذا الزر.
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="▶️ بدء الاستجواب", style=discord.ButtonStyle.primary, custom_id="dfg_app_begin_dm")
    async def begin(self, interaction, button):
        await interaction.response.send_message(
            "ℹ️ التقديم الجديد يبدأ تلقائياً. ارجع إلى لوحة التقديم واضغط ابدأ التقديم من جديد.",
            ephemeral=True
        )


async def submit_application_from_answers(guild_id, user_id, answers, reply_channel):
    config = get_application_config(guild_id)
    questions = get_application_questions(guild_id)
    guild = bot.get_guild(guild_id)
    if not guild:
        await reply_channel.send("❌ لم أستطع العثور على السيرفر.")
        return False

    channel = guild.get_channel(config["applications_channel_id"]) if config["applications_channel_id"] else None
    if not channel:
        await reply_channel.send("❌ روم استقبال الطلبات غير موجود.")
        return False

    member = guild.get_member(user_id)
    if not member:
        try:
            member = await guild.fetch_member(user_id)
        except (discord.NotFound, discord.HTTPException):
            await reply_channel.send("❌ العضو لم يعد موجوداً في السيرفر.")
            return False

    cursor.execute(
        "INSERT INTO application_requests "
        "(guild_id,user_id,message_id,channel_id,status,answers_json,created_at) VALUES (?,?,?,?,?,?,?)",
        (guild_id, user_id, 0, channel.id, "pending", json.dumps(answers, ensure_ascii=False), utcnow().isoformat())
    )
    request_id = cursor.lastrowid
    db_commit()

    embed = discord.Embed(
        title="📨 طلب تقديم رتبة",
        description=f"👤 **العضو:** {member.mention}\n🆔 **ID:** `{member.id}`",
        color=discord.Color.blurple()
    )
    for index, (question, answer) in enumerate(zip(questions, answers), 1):
        embed.add_field(
            name=f"❓ السؤال {index}: {truncate(question, 200)}",
            value=truncate(answer or "بدون إجابة", 1024),
            inline=False
        )
    embed.add_field(name="📌 الحالة", value="⏳ قيد المراجعة", inline=False)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text=f"Application #{request_id}")

    try:
        review_message = await channel.send(embed=embed, view=ApplicationReviewView(request_id))
    except (discord.Forbidden, discord.HTTPException) as e:
        cursor.execute("DELETE FROM application_requests WHERE id=?", (request_id,))
        db_commit()
        await reply_channel.send(f"❌ تعذر إرسال الطلب للإدارة.\n`{e}`")
        return False

    cursor.execute("UPDATE application_requests SET message_id=? WHERE id=?", (review_message.id, request_id))
    cursor.execute(
        "UPDATE application_sessions SET status='submitted', answers_json=?, question_index=? WHERE guild_id=? AND user_id=?",
        (json.dumps(answers, ensure_ascii=False), len(questions), guild_id, user_id)
    )
    db_commit()
    bot.add_view(ApplicationReviewView(request_id), message_id=review_message.id)
    await reply_channel.send("✅ تم إرسال طلبك للإدارة بنجاح. انتظر الرد.")
    return True


async def handle_application_dm_message(message: discord.Message):
    row = cursor.execute(
        "SELECT guild_id,question_index,answers_json,status FROM application_sessions "
        "WHERE user_id=? AND status='interviewing' ORDER BY created_at DESC LIMIT 1",
        (message.author.id,)
    ).fetchone()
    if not row:
        return False

    guild_id, question_index, answers_json, status = row
    questions = get_application_questions(guild_id)
    if not questions or question_index >= len(questions):
        cursor.execute("DELETE FROM application_sessions WHERE guild_id=? AND user_id=?", (guild_id, message.author.id))
        db_commit()
        return False

    try:
        answers = json.loads(answers_json or "[]")
    except Exception:
        answers = []
    while len(answers) < question_index:
        answers.append("")
    answers.append(message.content.strip())

    next_index = question_index + 1
    if next_index < len(questions):
        cursor.execute(
            "UPDATE application_sessions SET question_index=?,answers_json=? WHERE guild_id=? AND user_id=?",
            (next_index, json.dumps(answers, ensure_ascii=False), guild_id, message.author.id)
        )
        db_commit()
        await message.channel.send(f"**السؤال {next_index + 1} من {len(questions)}:**\n{questions[next_index]}")
        return True

    await submit_application_from_answers(guild_id, message.author.id, answers, message.channel)
    return True


class ApplicationNextQuestionView(discord.ui.View):
    """زر للانتقال للسؤال التالي؛ لا نحاول فتح Modal من داخل Modal submit مباشرة."""
    def __init__(self, guild_id, user_id, question_index):
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.user_id = user_id
        self.question_index = question_index

    @discord.ui.button(label="➡️ السؤال التالي", style=discord.ButtonStyle.primary)
    async def next_question(self, interaction, button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("❌ هذا الزر ليس لك.", ephemeral=True)

        row = cursor.execute(
            "SELECT question_index, status FROM application_sessions WHERE guild_id=? AND user_id=?",
            (self.guild_id, self.user_id)
        ).fetchone()
        if not row or row[1] != "interviewing":
            return await interaction.response.send_message("❌ جلسة التقديم انتهت أو غير موجودة.", ephemeral=True)

        if row[0] != self.question_index:
            return await interaction.response.send_message("❌ هذا السؤال لم يعد متاحاً.", ephemeral=True)

        questions = get_application_questions(self.guild_id)
        if self.question_index >= len(questions):
            return await interaction.response.send_message("❌ انتهت الأسئلة.", ephemeral=True)

        await interaction.response.send_modal(
            ApplicationQuestionAnswerModal(self.guild_id, self.user_id, self.question_index)
        )


class ApplicationQuestionAnswerModal(discord.ui.Modal):
    def __init__(self, guild_id, user_id, question_index):
        questions = get_application_questions(guild_id)
        question = questions[question_index]
        super().__init__(title=f"السؤال {question_index + 1} من {len(questions)}")
        self.guild_id = guild_id
        self.user_id = user_id
        self.question_index = question_index
        self.question_text = question
        self.answer = discord.ui.TextInput(
            label=truncate(question, 45),
            placeholder="اكتب إجابتك هنا...",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=1500
        )
        self.add_item(self.answer)

    async def on_submit(self, interaction):
        row = cursor.execute(
            "SELECT answers_json,status FROM application_sessions WHERE guild_id=? AND user_id=?",
            (self.guild_id, self.user_id)
        ).fetchone()
        if not row or row[1] != "interviewing":
            return await interaction.response.send_message("❌ جلسة التقديم غير موجودة أو انتهت.")

        try:
            answers = json.loads(row[0] or "[]")
        except Exception:
            answers = []

        answers.append(str(self.answer.value).strip())
        questions = get_application_questions(self.guild_id)
        next_index = self.question_index + 1

        if next_index < len(questions):
            cursor.execute(
                "UPDATE application_sessions SET question_index=?, answers_json=? WHERE guild_id=? AND user_id=?",
                (next_index, json.dumps(answers, ensure_ascii=False), self.guild_id, self.user_id)
            )
            db_commit()
            return await interaction.response.send_message(
                f"✅ تم حفظ إجابتك. اضغط الزر للانتقال إلى **السؤال {next_index + 1}**.",
                view=ApplicationNextQuestionView(self.guild_id, self.user_id, next_index)
            )

        # انتهت الأسئلة: إنشاء الطلب وإرساله لروم الطلبات.
        config = get_application_config(self.guild_id)
        guild = bot.get_guild(self.guild_id)
        if not guild:
            return await interaction.response.send_message("❌ لم أستطع العثور على السيرفر.")

        channel = guild.get_channel(config["applications_channel_id"]) if config["applications_channel_id"] else None
        if not channel:
            return await interaction.response.send_message("❌ روم استقبال الطلبات غير موجود.")

        member = guild.get_member(self.user_id)
        if not member:
            try:
                member = await guild.fetch_member(self.user_id)
            except (discord.NotFound, discord.HTTPException):
                return await interaction.response.send_message("❌ العضو لم يعد موجوداً في السيرفر.")

        cursor.execute(
            "INSERT INTO application_requests "
            "(guild_id,user_id,message_id,channel_id,status,answers_json,created_at) VALUES (?,?,?,?,?,?,?)",
            (self.guild_id, self.user_id, 0, channel.id, "pending", json.dumps(answers, ensure_ascii=False), utcnow().isoformat())
        )
        request_id = cursor.lastrowid
        db_commit()

        embed = discord.Embed(
            title="📨 طلب تقديم رتبة",
            description=(
                f"👤 **العضو:** {member.mention}\n"
                f"🆔 **ID:** `{member.id}`\n"
                f"📅 **وقت التقديم:** <t:{int(utcnow().timestamp())}:F>"
            ),
            color=discord.Color.blurple(),
            timestamp=utcnow()
        )

        for index, (question, answer) in enumerate(zip(questions, answers), 1):
            embed.add_field(
                name=f"❓ السؤال {index}: {truncate(question, 200)}",
                value=truncate(answer or "بدون إجابة", 1024),
                inline=False
            )
        embed.add_field(name="📌 الحالة", value="⏳ قيد المراجعة", inline=False)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"Application #{request_id}")

        try:
            review_message = await channel.send(embed=embed, view=ApplicationReviewView(request_id))
        except (discord.Forbidden, discord.HTTPException) as e:
            cursor.execute("DELETE FROM application_requests WHERE id=?", (request_id,))
            db_commit()
            return await interaction.response.send_message(f"❌ تعذر إرسال الطلب للإدارة.\n`{e}`")

        cursor.execute(
            "UPDATE application_requests SET message_id=? WHERE id=?",
            (review_message.id, request_id)
        )
        cursor.execute(
            "UPDATE application_sessions SET status='submitted', answers_json=?, question_index=? WHERE guild_id=? AND user_id=?",
            (json.dumps(answers, ensure_ascii=False), len(questions), self.guild_id, self.user_id)
        )
        db_commit()
        bot.add_view(ApplicationReviewView(request_id), message_id=review_message.id)

        await interaction.response.send_message(
            "✅ تم إرسال طلبك للإدارة بنجاح. انتظر الرد."
        )


class ApplicationDecisionModal(discord.ui.Modal):
    def __init__(self, request_id, accepted):
        super().__init__(title="✅ سبب القبول" if accepted else "❌ سبب الرفض")
        self.request_id = request_id
        self.accepted = accepted
        self.reason = discord.ui.TextInput(
            label="سبب القرار",
            placeholder="اكتب السبب...",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=1000
        )
        self.add_item(self.reason)

    async def on_submit(self, interaction):
        row = cursor.execute(
            "SELECT guild_id,user_id,message_id,channel_id,status FROM application_requests WHERE id=?",
            (self.request_id,)
        ).fetchone()
        if not row:
            return await interaction.response.send_message("❌ الطلب غير موجود.", ephemeral=True)
        if row[4] != "pending":
            return await interaction.response.send_message("⚠️ تم اتخاذ قرار على هذا الطلب مسبقاً.", ephemeral=True)

        guild = interaction.guild
        if not guild:
            return await interaction.response.send_message("❌ لا يمكن تنفيذ القرار هنا.", ephemeral=True)

        member = guild.get_member(row[1])
        reason = str(self.reason.value).strip()
        accept_role = None

        if self.accepted:
            config = get_application_config(guild.id)
            accept_role = guild.get_role(config["accept_role_id"]) if config["accept_role_id"] else None
            if not accept_role:
                return await interaction.response.send_message("❌ رتبة القبول غير موجودة أو لم يتم تعيينها.", ephemeral=True)
            if not member:
                return await interaction.response.send_message("❌ العضو لم يعد موجوداً في السيرفر.", ephemeral=True)
            if guild.me and accept_role >= guild.me.top_role:
                return await interaction.response.send_message("❌ رتبة القبول أعلى من رتبة البوت.", ephemeral=True)
            try:
                await member.add_roles(accept_role, reason=f"Application accepted by {interaction.user}: {reason}")
            except discord.Forbidden:
                return await interaction.response.send_message("❌ لا أستطيع إعطاء رتبة القبول.", ephemeral=True)

        status = "accepted" if self.accepted else "rejected"
        cursor.execute(
            "UPDATE application_requests SET status=?, reviewer_id=?, decision_reason=? WHERE id=?",
            (status, interaction.user.id, reason, self.request_id)
        )
        db_commit()

        # تعديل نفس رسالة الطلب بدلاً من إرسال رسالة جديدة.
        channel = guild.get_channel(row[3])
        if channel:
            try:
                message = await channel.fetch_message(row[2])
                if message.embeds:
                    embed = message.embeds[0]
                else:
                    embed = discord.Embed(title="📨 طلب تقديم رتبة")
                embed.color = discord.Color.green() if self.accepted else discord.Color.red()
                embed.add_field(name="📌 الحالة", value="✅ تم القبول" if self.accepted else "❌ تم الرفض", inline=False)
                embed.add_field(name="👮 بواسطة", value=interaction.user.mention, inline=True)
                embed.add_field(name="📝 السبب", value=truncate(reason, 1024), inline=False)
                embed.set_footer(text=f"Application #{self.request_id} • تم اتخاذ القرار")
                await message.edit(embed=embed, view=None)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass

        if member:
            try:
                if self.accepted:
                    await member.send(
                        embed=discord.Embed(
                            title="🎉 تم قبول طلبك",
                            description=f"لقد تم قبولك في **{config.get('message_title', 'التقديم')}**.\n\n**السبب:** {reason}",
                            color=discord.Color.green()
                        )
                    )
                else:
                    await member.send(
                        embed=discord.Embed(
                            title="❌ تم رفض طلبك",
                            description=f"تم رفض طلبك.\n\n**السبب:** {reason}",
                            color=discord.Color.red()
                        )
                    )
            except (discord.Forbidden, discord.HTTPException):
                pass

        await interaction.response.send_message(
            "✅ تم تسجيل القرار وإرسال النتيجة للعضو.", ephemeral=True
        )


class ApplicationReviewView(discord.ui.View):
    def __init__(self, request_id):
        super().__init__(timeout=None)
        self.request_id = request_id
        accept = discord.ui.Button(
            label="قبول", style=discord.ButtonStyle.success,
            emoji="✅", custom_id=f"dfg_app_accept_{request_id}"
        )
        reject = discord.ui.Button(
            label="رفض", style=discord.ButtonStyle.danger,
            emoji="❌", custom_id=f"dfg_app_reject_{request_id}"
        )
        accept.callback = self.accept_callback
        reject.callback = self.reject_callback
        self.add_item(accept)
        self.add_item(reject)

    async def accept_callback(self, interaction):
        if not await check_admin_permission(interaction, "admin"):
            return await interaction.response.send_message("❌ القرار للإدارة فقط.", ephemeral=True)
        row = cursor.execute("SELECT user_id,status FROM application_requests WHERE id=?", (self.request_id,)).fetchone()
        if row and row[0] == interaction.user.id:
            return await interaction.response.send_message("❌ لا يمكنك اتخاذ قرار على طلبك أنت.", ephemeral=True)
        await interaction.response.send_modal(ApplicationDecisionModal(self.request_id, True))

    async def reject_callback(self, interaction):
        if not await check_admin_permission(interaction, "admin"):
            return await interaction.response.send_message("❌ القرار للإدارة فقط.", ephemeral=True)
        row = cursor.execute("SELECT user_id,status FROM application_requests WHERE id=?", (self.request_id,)).fetchone()
        if row and row[0] == interaction.user.id:
            return await interaction.response.send_message("❌ لا يمكنك اتخاذ قرار على طلبك أنت.", ephemeral=True)
        await interaction.response.send_modal(ApplicationDecisionModal(self.request_id, False))


async def application_panel(interaction: discord.Interaction):
    if not await check_admin_permission(interaction, "admin"):
        return await safe_respond(interaction, "❌ لوحة التقديم للإدارة فقط.", ephemeral=True)

    embed = discord.Embed(
        title="📝 لوحة تحكم التقديم",
        description=(
            "من هنا اضبط نظام التقديم بالكامل:\n\n"
            "🎯 رتبة القبول\n"
            "👤 رتبة السماح بالتقديم\n"
            "📝 الأسئلة — من 1 إلى 10\n"
            "💬 رسالة التقديم\n"
            "📥 روم استقبال الطلبات\n"
            "🚀 نشر التقديم"
        ),
        color=discord.Color.blurple()
    )
    message = await interaction.channel.send(embed=embed, view=ApplicationControlView())
    cursor.execute(
        "INSERT OR REPLACE INTO application_setup_panels (message_id,guild_id,channel_id) VALUES (?,?,?)",
        (message.id, interaction.guild.id, interaction.channel.id)
    )
    db_commit()
    bot.add_view(ApplicationControlView(), message_id=message.id)
    await safe_respond(interaction, "✅ تم إرسال لوحة تحكم التقديم.", ephemeral=True)

# ============================================================
# 11.5) نظام التكت بالأسئلة - منفصل عن التكت الأساسي
# ============================================================
cursor.execute("""
CREATE TABLE IF NOT EXISTS ticket_question_config (
    guild_id INTEGER PRIMARY KEY,
    staff_role_id INTEGER,
    category_id INTEGER,
    log_channel_id INTEGER,
    welcome_message TEXT DEFAULT 'أهلاً بك في التكت!',
    questions_json TEXT DEFAULT '[]',
    panel_title TEXT DEFAULT '🎫 فتح تكت',
    panel_description TEXT DEFAULT 'أجب عن الأسئلة أولاً، وبعدها سيتم فتح التكت.'
)
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS ticket_question_panels (
    message_id INTEGER PRIMARY KEY,
    guild_id INTEGER,
    channel_id INTEGER
)
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS ticket_question_sessions (
    guild_id INTEGER,
    user_id INTEGER,
    question_index INTEGER DEFAULT 0,
    answers_json TEXT DEFAULT '[]',
    status TEXT DEFAULT 'interviewing',
    created_at TEXT,
    PRIMARY KEY (guild_id, user_id)
)
""")
db_commit()

# ============================================================
# 11.6) تصحيح نظام التقديم: سؤال برسالة مستقلة + مهلة 10 دقائق
# ============================================================
def application_session_expired(row):
    if not row:
        return True
    try:
        return datetime.datetime.fromisoformat(row[2]) <= utcnow()
    except Exception:
        return True


def get_application_session_row(guild_id, user_id):
    return cursor.execute(
        "SELECT status, question_index, created_at, answers_json FROM application_sessions "
        "WHERE guild_id=? AND user_id=? AND status IN ('ready','interviewing')",
        (guild_id, user_id)
    ).fetchone()


async def send_application_question_v2(user, guild_id, user_id, index):
    questions = get_application_questions(guild_id)
    if index >= len(questions):
        return
    expiry = utcnow() + datetime.timedelta(minutes=10)
    cursor.execute(
        "UPDATE application_sessions SET question_index=?, status='interviewing', created_at=? "
        "WHERE guild_id=? AND user_id=?",
        (index, expiry.isoformat(), guild_id, user_id)
    )
    db_commit()
    embed = discord.Embed(
        title=f"❓ السؤال {index + 1} من {len(questions)}",
        description=f"**{questions[index]}**\n\n⏱️ أمامك **10 دقائق** للإجابة على هذا السؤال.",
        color=discord.Color.blurple()
    )
    try:
        await user.send(embed=embed, view=ApplicationAnswerButtonViewV2(guild_id, user_id, index))
    except (discord.Forbidden, discord.HTTPException):
        cursor.execute("DELETE FROM application_sessions WHERE guild_id=? AND user_id=?", (guild_id, user_id))
        db_commit()


class ApplicationDMStartViewV2(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="▶️ بدء الاستجواب", style=discord.ButtonStyle.primary, custom_id="dfg_app_begin_dm_v2")
    async def begin(self, interaction, button):
        rows = cursor.execute(
            "SELECT guild_id, question_index, answers_json, status, created_at FROM application_sessions "
            "WHERE user_id=? AND status IN ('ready','interviewing') ORDER BY created_at DESC LIMIT 1",
            (interaction.user.id,)
        ).fetchone()
        if not rows:
            return await interaction.response.send_message("❌ لا يوجد استجواب جاهز لك.")
        guild_id, _, _, _, expiry_raw = rows
        try:
            expiry = datetime.datetime.fromisoformat(expiry_raw)
        except Exception:
            expiry = utcnow() - datetime.timedelta(seconds=1)
        if expiry <= utcnow():
            cursor.execute("DELETE FROM application_sessions WHERE guild_id=? AND user_id=?", (guild_id, interaction.user.id))
            db_commit()
            return await interaction.response.send_message("⌛ انتهت مهلة الاستجواب. ارجع للسيرفر وابدأ تقديمًا جديدًا.")
        questions = get_application_questions(guild_id)
        if not questions:
            return await interaction.response.send_message("❌ أسئلة التقديم غير موجودة.")
        cursor.execute(
            "UPDATE application_sessions SET question_index=0, answers_json='[]', status='interviewing', created_at=? "
            "WHERE guild_id=? AND user_id=?",
            ((utcnow()+datetime.timedelta(minutes=10)).isoformat(), guild_id, interaction.user.id)
        )
        db_commit()
        await interaction.response.edit_message(content="✅ بدأ الاستجواب. كل سؤال له 10 دقائق.", embed=None, view=None)
        await send_application_question_v2(interaction.user, guild_id, interaction.user.id, 0)


class ApplicationAnswerButtonViewV2(discord.ui.View):
    def __init__(self, guild_id, user_id, index):
        super().__init__(timeout=600)
        self.guild_id= guild_id
        self.user_id = user_id
        self.index = index

    @discord.ui.button(label="✍️ الإجابة", style=discord.ButtonStyle.success)
    async def answer(self, interaction, button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("❌ هذا السؤال ليس لك.")
        row = cursor.execute(
            "SELECT question_index,status,created_at FROM application_sessions WHERE guild_id=? AND user_id=?",
            (self.guild_id, self.user_id)
        ).fetchone()
        if not row or row[1] != 'interviewing' or row[0] != self.index:
            return await interaction.response.send_message("⌛ انتهت مهلة هذا السؤال. ارجع للسيرفر وابدأ تقديمًا جديدًا.")
        try:
            expiry=datetime.datetime.fromisoformat(row[2])
        except Exception:
            expiry=utcnow()-datetime.timedelta(seconds=1)
        if expiry <= utcnow():
            cursor.execute("DELETE FROM application_sessions WHERE guild_id=? AND user_id=?", (self.guild_id,self.user_id))
            db_commit()
            return await interaction.response.send_message("⌛ انتهت مهلة هذا السؤال. ارجع للسيرفر وابدأ تقديمًا جديدًا.")
        await interaction.response.send_modal(ApplicationQuestionAnswerModalV2(self.guild_id,self.user_id,self.index))


class ApplicationQuestionAnswerModalV2(discord.ui.Modal):
    def __init__(self,guild_id,user_id,index):
        questions=get_application_questions(guild_id)
        super().__init__(title=f"السؤال {index+1} من {len(questions)}")
        self.guild_id=guild_id; self.user_id=user_id; self.index=index
        self.answer=discord.ui.TextInput(label=truncate(questions[index],45), placeholder="اكتب إجابتك هنا...", style=discord.TextStyle.paragraph, required=True, max_length=1500)
        self.add_item(self.answer)

    async def on_submit(self, interaction):
        row=cursor.execute(
            "SELECT answers_json,status,created_at,question_index FROM application_sessions WHERE guild_id=? AND user_id=?",
            (self.guild_id,self.user_id)
        ).fetchone()
        if not row or row[1] != 'interviewing' or row[3] != self.index:
            return await interaction.response.send_message("❌ جلسة الاستجواب انتهت أو غير موجودة.")
        try: expiry=datetime.datetime.fromisoformat(row[2])
        except Exception: expiry=utcnow()-datetime.timedelta(seconds=1)
        if expiry <= utcnow():
            cursor.execute("DELETE FROM application_sessions WHERE guild_id=? AND user_id=?", (self.guild_id,self.user_id)); db_commit()
            return await interaction.response.send_message("⌛ انتهت مهلة السؤال. ابدأ تقديمًا جديدًا من السيرفر.")
        try: answers=json.loads(row[0] or '[]')
        except Exception: answers=[]
        while len(answers) <= self.index: answers.append('')
        answers[self.index]=str(self.answer.value).strip()
        questions=get_application_questions(self.guild_id)
        nxt=self.index+1
        if nxt < len(questions):
            cursor.execute(
                "UPDATE application_sessions SET question_index=?,answers_json=?,created_at=? WHERE guild_id=? AND user_id=?",
                (nxt,json.dumps(answers,ensure_ascii=False),(utcnow()+datetime.timedelta(minutes=10)).isoformat(),self.guild_id,self.user_id)
            ); db_commit()
            await interaction.response.send_message(f"✅ تم حفظ الإجابة. انتقلنا للسؤال **{nxt+1}**.")
            await send_application_question_v2(interaction.user,self.guild_id,self.user_id,nxt)
            return

        # إعادة استخدام نفس منطق إرسال الطلب الموجود سابقاً.
        config=get_application_config(self.guild_id); guild=bot.get_guild(self.guild_id)
        channel=guild.get_channel(config['applications_channel_id']) if guild and config['applications_channel_id'] else None
        if not channel:
            return await interaction.response.send_message("❌ روم استقبال الطلبات غير موجود.")
        member=guild.get_member(self.user_id) if guild else None
        if not member:
            try: member=await guild.fetch_member(self.user_id)
            except Exception: return await interaction.response.send_message("❌ العضو لم يعد موجوداً في السيرفر.")
        cursor.execute(
            "INSERT INTO application_requests (guild_id,user_id,message_id,channel_id,status,answers_json,created_at) VALUES (?,?,?,?,?,?,?)",
            (self.guild_id,self.user_id,0,channel.id,'pending',json.dumps(answers,ensure_ascii=False),utcnow().isoformat())
        )
        request_id=cursor.lastrowid; db_commit()
        embed=discord.Embed(title='📨 طلب تقديم رتبة',description=f"👤 **العضو:** {member.mention}\n🆔 **ID:** `{member.id}`\n📅 **وقت التقديم:** <t:{int(utcnow().timestamp())}:F>",color=discord.Color.blurple(),timestamp=utcnow())
        for i,(q,a) in enumerate(zip(questions,answers),1): embed.add_field(name=f"❓ السؤال {i}: {truncate(q,200)}",value=truncate(a or 'بدون إجابة',1024),inline=False)
        embed.add_field(name='📌 الحالة',value='⏳ قيد المراجعة',inline=False); embed.set_thumbnail(url=member.display_avatar.url); embed.set_footer(text=f'Application #{request_id}')
        try: review=await channel.send(embed=embed,view=ApplicationReviewView(request_id))
        except Exception as e:
            cursor.execute('DELETE FROM application_requests WHERE id=?',(request_id,)); db_commit()
            return await interaction.response.send_message(f"❌ تعذر إرسال الطلب للإدارة.\n`{e}`")
        cursor.execute('UPDATE application_requests SET message_id=? WHERE id=?',(review.id,request_id))
        cursor.execute('DELETE FROM application_sessions WHERE guild_id=? AND user_id=?',(self.guild_id,self.user_id)); db_commit()
        bot.add_view(ApplicationReviewView(request_id),message_id=review.id)
        await interaction.response.send_message('✅ تم إرسال طلبك للإدارة. انتظر الرد.')


# نستبدل الكلاسات القديمة بالأسماء التي يستخدمها بقية الكود.
ApplicationDMStartView = ApplicationDMStartViewV2
ApplicationQuestionAnswerModal = ApplicationQuestionAnswerModalV2

# ============================================================
# 12) نظام الرتب بالأزرار
# ============================================================
ROLE_PANEL_STYLE = discord.ButtonStyle.primary


def get_role_panel(message_id):
    row = cursor.execute(
        "SELECT guild_id, channel_id, title, description, roles_json FROM reaction_role_panels WHERE message_id=?",
        (message_id,)
    ).fetchone()
    if not row:
        return None
    try:
        roles = json.loads(row[4] or "[]")
    except Exception:
        roles = []
    return {"guild_id": row[0], "channel_id": row[1], "title": row[2], "description": row[3], "roles": roles}


class ReactionRoleView(discord.ui.View):
    def __init__(self, message_id, roles):
        super().__init__(timeout=None)
        self.message_id = int(message_id)
        for index, role_id in enumerate(roles[:25]):
            self.add_item(ReactionRoleButton(self.message_id, int(role_id), index))


class ReactionRoleButton(discord.ui.Button):
    def __init__(self, message_id, role_id, index):
        super().__init__(
            label="جاري التحميل…",
            style=ROLE_PANEL_STYLE,
            custom_id=f"dfg_role:{message_id}:{role_id}",
            row=min(index // 5, 4)
        )
        self.message_id = int(message_id)
        self.role_id = int(role_id)

    async def callback(self, interaction: discord.Interaction):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await safe_respond(interaction, "❌ هذا الزر يعمل داخل السيرفر فقط.", ephemeral=True)
        role = interaction.guild.get_role(self.role_id)
        if not role:
            return await safe_respond(interaction, "❌ هذه الرتبة لم تعد موجودة.", ephemeral=True)
        if role.is_default() or role.managed:
            return await safe_respond(interaction, "❌ لا يمكن إعطاء هذه الرتبة.", ephemeral=True)
        me = interaction.guild.me
        if me and role >= me.top_role:
            return await safe_respond(interaction, "❌ رتبة البوت أقل من هذه الرتبة.", ephemeral=True)

        try:
            if role in interaction.user.roles:
                await interaction.user.remove_roles(role, reason="Reaction Role button")
                return await safe_respond(interaction, f"✅ تم إلغاء إضافة {role.mention} لك.", ephemeral=True)
            await interaction.user.add_roles(role, reason="Reaction Role button")
            await safe_respond(interaction, f"✅ تمت إضافة {role.mention} لك.", ephemeral=True)
        except discord.Forbidden:
            await safe_respond(interaction, "❌ لا أستطيع تعديل هذه الرتبة.", ephemeral=True)
        except discord.HTTPException:
            await safe_respond(interaction, "❌ حدث خطأ من Discord.", ephemeral=True)


async def refresh_role_button_labels(message: discord.Message, roles):
    if not isinstance(message, discord.Message):
        return
    view = ReactionRoleView(message.id, roles)
    for item in view.children:
        if isinstance(item, ReactionRoleButton):
            role = message.guild.get_role(item.role_id) if message.guild else None
            item.label = role.name[:80] if role else "رتبة محذوفة"
    try:
        await message.edit(view=view)
    except (discord.Forbidden, discord.HTTPException):
        pass


class RolePanelBuilder(discord.ui.View):
    def __init__(self, guild_id):
        super().__init__(timeout=600)
        self.guild_id = guild_id
        self.roles = []
        self.title = "🎭 رتب الأعضاء"
        self.description = "اختر الرتبة التي تريدها من الأزرار بالأسفل."

    @discord.ui.button(label="➕ إضافة رتبة", style=discord.ButtonStyle.success)
    async def add_role(self, interaction, button):
        if not await check_admin_permission(interaction, "admin"):
            return await safe_respond(interaction, "❌ للإدارة فقط.", ephemeral=True)
        view = discord.ui.View(timeout=120)
        select = discord.ui.RoleSelect(placeholder="اختر الرتبة التي تريد إضافتها…", min_values=1, max_values=1)
        async def selected(i):
            role = select.values[0]
            if role.id not in self.roles:
                if len(self.roles) >= 25:
                    return await safe_respond(i, "❌ الحد الأقصى 25 زرًا في اللوحة.", ephemeral=True)
                self.roles.append(role.id)
            await safe_respond(i, f"✅ تمت إضافة زر {role.mention} للوحة.", ephemeral=True)
        select.callback = selected
        view.add_item(select)
        await safe_respond(interaction, "اختر الرتبة:", view=view, ephemeral=True)

    @discord.ui.button(label="📝 تعديل الرسالة", style=discord.ButtonStyle.primary)
    async def edit_text(self, interaction, button):
        if not await check_admin_permission(interaction, "admin"):
            return await safe_respond(interaction, "❌ للإدارة فقط.", ephemeral=True)
        await interaction.response.send_modal(RolePanelTextModal(self))

    @discord.ui.button(label="📋 الرتب الحالية", style=discord.ButtonStyle.secondary)
    async def list_roles(self, interaction, button):
        lines=[]
        for rid in self.roles:
            role=interaction.guild.get_role(rid)
            lines.append(role.mention if role else f"`{rid}` (محذوفة)")
        await safe_respond(interaction, "**رتب اللوحة:**\n" + ("\n".join(lines) if lines else "لا توجد رتب."), ephemeral=True)

    @discord.ui.button(label="🚀 نشر اللوحة", style=discord.ButtonStyle.success)
    async def publish(self, interaction, button):
        if not await check_admin_permission(interaction, "admin"):
            return await safe_respond(interaction, "❌ للإدارة فقط.", ephemeral=True)
        if not self.roles:
            return await safe_respond(interaction, "❌ أضف رتبة واحدة على الأقل.", ephemeral=True)
        embed=discord.Embed(title=self.title, description=self.description, color=discord.Color.blurple())
        try:
            msg=await interaction.channel.send(embed=embed, view=ReactionRoleView(0, []))
            # نبني الـView بعد معرفة ID الرسالة حتى تكون custom_id ثابتة.
            view=ReactionRoleView(msg.id,self.roles)
            for item in view.children:
                role=interaction.guild.get_role(item.role_id)
                item.label=role.name[:80] if role else "رتبة محذوفة"
            await msg.edit(view=view)
            cursor.execute("INSERT OR REPLACE INTO reaction_role_panels(message_id,guild_id,channel_id,title,description,roles_json) VALUES (?,?,?,?,?,?)", (msg.id,interaction.guild.id,interaction.channel.id,self.title,self.description,json.dumps(self.roles)))
            db_commit()
            bot.add_view(view,message_id=msg.id)
            await safe_respond(interaction,"✅ تم نشر لوحة الرتب وحفظها.",ephemeral=True)
        except (discord.Forbidden,discord.HTTPException):
            await safe_respond(interaction,"❌ تعذر نشر اللوحة.",ephemeral=True)


class RolePanelTextModal(discord.ui.Modal, title="📝 تعديل لوحة الرتب"):
    title_text=discord.ui.TextInput(label="العنوان",max_length=256)
    description_text=discord.ui.TextInput(label="الوصف",style=discord.TextStyle.paragraph,max_length=4000)
    def __init__(self,builder):
        super().__init__(); self.builder=builder
        self.title_text.default=builder.title
        self.description_text.default=builder.description
    async def on_submit(self,interaction):
        self.builder.title=str(self.title_text.value).strip()
        self.builder.description=str(self.description_text.value).strip()
        await safe_respond(interaction,"✅ تم حفظ نص اللوحة.",ephemeral=True)


# ============================================================
# 13) إنشاء البوت
# ============================================================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True


class IntegratedBot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix="/",
            intents=intents,
            help_command=None
        )

    async def setup_hook(self):
        # ---------------- التكتات ----------------
        for message_id, panel in ticket_data.get("panels", {}).items():
            try:
                view = DynamicLauncher(panel.get("tickets", []))
                self.add_view(view, message_id=int(message_id))
            except Exception as e:
                print("[TICKET PANEL RESTORE]", e)

        for channel_id, info in ticket_data.get("tickets", {}).items():
            message_id = info.get("control_message_id")
            if not message_id:
                continue
            try:
                created = info.get("created_at")
                created_at = datetime.datetime.fromisoformat(created) if created else utcnow()
                view = TicketControlView(
                    info.get("ticket_type", "Ticket"),
                    info.get("log_channel_id"),
                    info.get("owner_id"),
                    info.get("staff_role_id"),
                    info.get("claimed_by"),
                    created_at
                )
                self.add_view(view, message_id=int(message_id))
            except Exception as e:
                print("[TICKET RESTORE]", e)

        # ---------------- التقديم ----------------
        self.add_view(ApplicationDMStartView())
        for message_id, _, _ in cursor.execute(
            "SELECT message_id,guild_id,channel_id FROM application_setup_panels"
        ).fetchall():
            try:
                self.add_view(ApplicationControlView(), message_id=int(message_id))
            except Exception as e:
                print("[APPLICATION SETUP RESTORE]", e)

        for message_id, _, _ in cursor.execute(
            "SELECT message_id,guild_id,channel_id FROM application_apply_panels"
        ).fetchall():
            try:
                self.add_view(ApplicationApplyView(), message_id=int(message_id))
            except Exception as e:
                print("[APPLICATION PANEL RESTORE]", e)

        for request_id, message_id in cursor.execute(
            "SELECT id,message_id FROM application_requests WHERE status='pending' AND message_id IS NOT NULL"
        ).fetchall():
            try:
                self.add_view(ApplicationReviewView(request_id), message_id=int(message_id))
            except Exception as e:
                print("[APPLICATION REVIEW RESTORE]", e)

        # ---------------- تكت الأسئلة ----------------
        for message_id, _, _ in cursor.execute(
            "SELECT message_id,guild_id,channel_id FROM ticket_question_panels"
        ).fetchall():
            try:
                self.add_view(QTicketOpenView(), message_id=int(message_id))
            except Exception as e:
                print("[QUESTION TICKET RESTORE]", e)

        # ---------------- لوحات الرتب بالأزرار ----------------
        for message_id, roles_json in cursor.execute("SELECT message_id, roles_json FROM reaction_role_panels").fetchall():
            try:
                roles=json.loads(roles_json or "[]")
                view=ReactionRoleView(int(message_id), roles)
                self.add_view(view, message_id=int(message_id))
            except Exception as e:
                print("[ROLE PANEL RESTORE]", e)

        # ---------------- لوحة الردود التلقائية ----------------
        for message_id, guild_id in cursor.execute("SELECT message_id, guild_id FROM auto_reply_panels").fetchall():
            try:
                self.add_view(AutoReplyPanelView(int(guild_id)), message_id=int(message_id))
            except Exception as e:
                print("[AUTO REPLY PANEL RESTORE]", e)

        # ---------------- المساعدة ----------------
        self.add_view(HelpView())

        try:
            await self.tree.sync()
        except Exception as e:
            print("[SYNC ERROR]", e)


bot = IntegratedBot()
bot.tree.add_command(app_commands.Command(name="application_panel", description="إنشاء لوحة تحكم التقديم على رتبة", callback=application_panel))


# ============================================================
# 13) XP الصوتي التلقائي
# ============================================================
@tasks.loop(minutes=1)
async def voice_xp_loop():
    for guild in bot.guilds:
        for vc in guild.voice_channels:
            humans = [
                m for m in vc.members
                if not m.bot
                and m.voice
                and not m.voice.self_deaf
                and not m.voice.deaf
            ]

            if len(humans) < VOICE_MIN_HUMANS:
                continue

            for member in humans:
                await add_xp(member, voice_amount=VOICE_XP_PER_MINUTE)


@voice_xp_loop.before_loop
async def before_voice_xp_loop():
    await bot.wait_until_ready()


# ============================================================
# 14) أحداث الرسائل - الحماية + XP + الاختصارات
# ============================================================
@bot.event
async def on_message(message: discord.Message):
    # التقديم على الرتبة يعمل في الخاص برسائل عادية: السؤال رسالة، والإجابة رسالة.
    if not message.guild:
        if not message.author.bot:
            handled = await handle_application_dm_message(message)
            if handled:
                return
        return

    cache_message(message)

    if message.author.bot:
        return

    # ---------------- الردود التلقائية ----------------
    if await handle_auto_reply(message):
        # لا نوقف بقية الحماية والاختصارات؛ الرد التلقائي مجرد إضافة.
        pass

    # ---------------- Anti-Link ----------------
    _, antilink_enabled = get_settings(message.guild.id)

    if antilink_enabled and contains_link(message.content):
        if not member_is_admin_exempt(message.author):
            reason = "Anti-Link: إرسال رابط"
            remember_timeout_context(
                message.guild.id,
                message.author.id,
                bot.user.id if bot.user else DEVELOPER_ID,
                reason,
                "دقيقتان"
            )

            try:
                await message.delete()
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                pass

            timed_out = False
            try:
                if message.guild.me and message.guild.me.guild_permissions.moderate_members:
                    if message.author != message.guild.owner and message.author.top_role < message.guild.me.top_role:
                        await message.author.timeout(
                            datetime.timedelta(minutes=ANTI_LINK_TIMEOUT_MINUTES),
                            reason=reason
                        )
                        timed_out = True
            except (discord.Forbidden, discord.HTTPException):
                timed_out = False

            if timed_out:
                try:
                    warning = await message.channel.send(
                        f"🚫 {message.author.mention} تم حذف الرابط وتم إعطاؤك Timeout لمدة **دقيقتين**.",
                        delete_after=5
                    )
                except (discord.Forbidden, discord.HTTPException):
                    pass
            else:
                try:
                    await message.channel.send(
                        f"🚫 {message.author.mention} تم حذف الرابط، لكن تعذر تطبيق الـ Timeout.",
                        delete_after=5
                    )
                except (discord.Forbidden, discord.HTTPException):
                    pass

            # لا XP للرسالة المخالفة
            return

    # ---------------- XP النصي ----------------
    now = time.monotonic()
    key = (message.guild.id, message.author.id)
    normalized = message.content.strip().lower()

    # لا نعطي XP للرسائل الفارغة جداً أو التكرار المباشر
    if len(normalized) >= 3:
        last_time = xp_cooldowns.get(key, 0)
        previous_text = last_texts.get(key)

        if now - last_time >= TEXT_XP_COOLDOWN and normalized != previous_text:
            amount = random.randint(TEXT_XP_MIN, TEXT_XP_MAX)
            xp_cooldowns[key] = now
            last_texts[key] = normalized
            await add_xp(message.author, text_amount=amount)

    # ---------------- الاختصارات ----------------
    # الاختصار يُقرأ من أول كلمة حتى يعمل مع المعطيات:
    # !ban @user السبب / !timeout @user 10 السبب / !rank @user ...
    parts = message.content.strip().split()
    shortcut = None
    if parts:
        shortcut = cursor.execute(
            "SELECT action FROM shortcuts WHERE guild_id=? AND trigger=?",
            (message.guild.id, parts[0])
        ).fetchone()
        if not shortcut:
            shortcut = cursor.execute(
                "SELECT action FROM shortcuts WHERE guild_id=? AND LOWER(trigger)=LOWER(?)",
                (message.guild.id, parts[0])
            ).fetchone()

    if shortcut:
        await run_shortcut(message, shortcut[0])

    await bot.process_commands(message)


# ============================================================
# 15) لوق حذف وتعديل الرسائل
# ============================================================
@bot.event
async def on_raw_message_delete(payload: discord.RawMessageDeleteEvent):
    data = message_cache.pop(payload.message_id, None)
    if not data:
        return

    guild = bot.get_guild(data["guild_id"])
    if not guild:
        return

    channel = guild.get_channel(data["channel_id"])
    author = guild.get_member(data["author_id"])

    embed = log_embed("🗑️ رسالة محذوفة", discord.Color.red())
    embed.add_field(name="👤 العضو", value=author.mention if author else f"<@{data['author_id']}>", inline=True)
    embed.add_field(name="📍 الروم", value=channel.mention if channel else f"<#{data['channel_id']}>", inline=True)
    embed.add_field(name="🕐 الوقت", value=f"<t:{int(utcnow().timestamp())}:F>", inline=False)
    embed.add_field(name="💬 المحتوى", value=truncate(data.get("content") or "[بدون نص]", 1024), inline=False)

    if data.get("attachments"):
        embed.add_field(
            name="📎 المرفقات",
            value=truncate("\n".join(data["attachments"]), 1024),
            inline=False
        )

    await send_system_log(guild, embed)


@bot.event
async def on_raw_message_edit(payload: discord.RawMessageUpdateEvent):
    data = message_cache.get(payload.message_id)
    if not data:
        return

    guild = bot.get_guild(data["guild_id"])
    if not guild:
        return

    old_content = data.get("content", "")
    new_content = payload.data.get("content")

    if new_content is None:
        return
    if old_content == new_content:
        data["content"] = new_content
        return

    channel = guild.get_channel(data["channel_id"])
    author = guild.get_member(data["author_id"])

    embed = log_embed("✏️ رسالة تم تعديلها", discord.Color.orange())
    embed.add_field(name="👤 العضو", value=author.mention if author else f"<@{data['author_id']}>", inline=True)
    embed.add_field(name="📍 الروم", value=channel.mention if channel else f"<#{data['channel_id']}>", inline=True)
    embed.add_field(name="قبل التعديل", value=truncate(old_content or "[فارغ]", 1024), inline=False)
    embed.add_field(name="بعد التعديل", value=truncate(new_content or "[فارغ]", 1024), inline=False)

    await send_system_log(guild, embed)
    data["content"] = new_content


# ============================================================
# 16) لوق Timeout / Role / Nickname / Voice
# ============================================================
@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    # ---------------- Timeout ----------------
    if before.timed_out_until != after.timed_out_until:
        key = (after.guild.id, after.id)
        context = recent_timeout_context.get(key)
        actor = None
        reason = "غير معروف"
        duration = "غير معروف"

        if context and time.monotonic() - context["time"] <= 10:
            actor = after.guild.get_member(context["actor_id"])
            reason = context["reason"]
            duration = context["duration"]
            recent_timeout_context.pop(key, None)
        else:
            entry = await get_recent_audit_entry(
                after.guild,
                discord.AuditLogAction.member_update,
                after.id
            )
            if entry:
                actor = entry.user
                reason = entry.reason or "بدون سبب"

        embed = log_embed(
            "🔇 Timeout تم تطبيقه" if after.timed_out_until else "🔊 تم إلغاء Timeout",
            discord.Color.red() if after.timed_out_until else discord.Color.green()
        )
        embed.add_field(name="👤 العضو", value=after.mention, inline=True)
        embed.add_field(name="🛡️ بواسطة", value=actor.mention if actor else "غير معروف", inline=True)
        embed.add_field(name="📝 السبب", value=truncate(reason, 1024), inline=False)

        if after.timed_out_until:
            remaining = after.timed_out_until - utcnow()
            duration = duration if duration != "غير معروف" else str(remaining).split(".")[0]
            embed.add_field(name="⏱️ المدة", value=str(duration), inline=True)
            embed.add_field(name="⏰ ينتهي", value=f"<t:{int(after.timed_out_until.timestamp())}:R>", inline=True)

        await send_system_log(after.guild, embed)

    # ---------------- Nickname ----------------
    if before.nick != after.nick:
        embed = log_embed("✏️ تغيير الاسم", discord.Color.orange())
        embed.add_field(name="👤 العضو", value=after.mention, inline=True)
        embed.add_field(name="قبل", value=before.nick or "بدون اسم", inline=True)
        embed.add_field(name="بعد", value=after.nick or "بدون اسم", inline=True)
        await send_system_log(after.guild, embed)

    # ---------------- Roles ----------------
    before_roles = {r.id: r for r in before.roles}
    after_roles = {r.id: r for r in after.roles}

    added = [r for rid, r in after_roles.items() if rid not in before_roles and r.id != after.guild.default_role.id]
    removed = [r for rid, r in before_roles.items() if rid not in after_roles and r.id != after.guild.default_role.id]

    if added or removed:
        embed = log_embed("🎭 تغيير رتب عضو", discord.Color.blurple())
        embed.add_field(name="👤 العضو", value=after.mention, inline=False)
        if added:
            embed.add_field(name="➕ تمت الإضافة", value=" ".join(r.mention for r in added), inline=False)
        if removed:
            embed.add_field(name="➖ تمت الإزالة", value=" ".join(r.mention for r in removed), inline=False)
        await send_system_log(after.guild, embed)


@bot.event
async def on_voice_state_update(member, before, after):
    if member.bot:
        return

    if before.channel != after.channel:
        embed = log_embed("🔊 تغيير القناة الصوتية", discord.Color.blurple())
        embed.add_field(name="👤 العضو", value=member.mention, inline=True)
        embed.add_field(name="من", value=before.channel.mention if before.channel else "خارج الصوت", inline=True)
        embed.add_field(name="إلى", value=after.channel.mention if after.channel else "خرج من الصوت", inline=True)
        await send_system_log(member.guild, embed)


# ============================================================
# 17) لوق الدخول والخروج والباند والكِك
# ============================================================
@bot.event
async def on_member_join(member):
    # رتبة الدخول
    row = cursor.execute("SELECT role_id FROM auto_roles WHERE guild_id=?", (member.guild.id,)).fetchone()
    if row:
        role = member.guild.get_role(row[0])
        if role:
            try:
                await member.add_roles(role, reason="DFG Auto Role")
            except (discord.Forbidden, discord.HTTPException):
                pass

    # الترحيب يكون في الخاص فقط
    cfg = cursor.execute(
        "SELECT welcome_title,welcome_msg,welcome_color,welcome_img FROM system_config WHERE guild_id=?",
        (member.guild.id,)
    ).fetchone()
    if cfg and cfg[1]:
        text = str(cfg[1]).replace("{user}", member.mention).replace("{username}", member.display_name).replace("{server}", member.guild.name).replace("{count}", str(member.guild.member_count))
        embed = discord.Embed(
            title=cfg[0] or "👋 أهلاً بك!",
            description=text,
            color=parse_color(cfg[2] or "5865F2"),
            timestamp=utcnow()
        )
        if cfg[3]:
            try:
                embed.set_image(url=cfg[3])
            except Exception:
                pass
        try:
            await member.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            # إذا الخاص مقفل، نسجل فشل الترحيب في اللوق
            fallback = log_embed("⚠️ تعذر إرسال ترحيب خاص", discord.Color.orange())
            fallback.add_field(name="👤 العضو", value=f"{member.mention} (`{member.id}`)", inline=False)
            fallback.add_field(name="📌 السبب", value="العضو لا يستقبل رسائل خاصة أو حدث خطأ من Discord.", inline=False)
            await send_system_log(member.guild, fallback)

    # فحص دعوة الدخول
    inviter_text = "غير معروف"
    used_code = "غير معروف"
    try:
        current = await member.guild.invites()
        old = invite_cache.get(member.guild.id, {})
        changed = next((inv for inv in current if inv.code in old and inv.uses > old[inv.code]["uses"]), None)
        if changed:
            used_code = changed.code
            inviter = changed.inviter
            inviter_text = inviter.mention if inviter else "غير معروف"
        invite_cache[member.guild.id] = {
            inv.code: {"uses": inv.uses or 0, "inviter_id": inv.inviter.id if inv.inviter else None}
            for inv in current
        }
    except (discord.Forbidden, discord.HTTPException):
        pass

    embed = log_embed("📥 عضو دخل السيرفر", discord.Color.green())
    embed.add_field(name="👤 العضو", value=f"{member.mention} (`{member.id}`)", inline=False)
    embed.add_field(name="🔗 دعوة الدخول", value=f"`{used_code}`", inline=True)
    embed.add_field(name="👤 الداعي", value=inviter_text, inline=True)
    await send_system_log(member.guild, embed)


@bot.event
async def on_member_remove(member):
    cursor.execute("DELETE FROM application_sessions WHERE guild_id=? AND user_id=?", (member.guild.id, member.id))
    db_commit()
    qticket_drafts.pop((member.guild.id, member.id), None)

    # المغادرة تحاول الخاص أولاً، وإذا تعذر ذلك تذهب للوق
    cfg = cursor.execute(
        "SELECT leave_title,leave_msg,leave_color,leave_img FROM system_config WHERE guild_id=?",
        (member.guild.id,)
    ).fetchone()
    dm_sent = False
    if cfg and cfg[1]:
        text = str(cfg[1]).replace("{user}", getattr(member, 'mention', str(member))).replace("{username}", member.display_name).replace("{server}", member.guild.name).replace("{count}", str(member.guild.member_count))
        embed = discord.Embed(
            title=cfg[0] or "👋 إلى اللقاء",
            description=text,
            color=parse_color(cfg[2] or "ED4245"),
            timestamp=utcnow()
        )
        if cfg[3]:
            try:
                embed.set_image(url=cfg[3])
            except Exception:
                pass
        try:
            await member.send(embed=embed)
            dm_sent = True
        except (discord.Forbidden, discord.HTTPException):
            dm_sent = False

    # إذا تعذر الخاص أو لم يتم إعداد رسالة المغادرة، سجلها في اللوق
    entry = await get_recent_audit_entry(member.guild, discord.AuditLogAction.kick, member.id)
    if entry:
        embed = log_embed("👢 عضو تم طرده", discord.Color.red())
        embed.add_field(name="👤 العضو", value=f"{member} (`{member.id}`)", inline=True)
        embed.add_field(name="🛡️ بواسطة", value=entry.user.mention if entry.user else "غير معروف", inline=True)
        embed.add_field(name="📝 السبب", value=truncate(entry.reason or "بدون سبب", 1024), inline=False)
        await send_system_log(member.guild, embed)
    elif not dm_sent:
        embed = log_embed("📤 عضو غادر السيرفر", discord.Color.orange())
        embed.add_field(name="👤 العضو", value=f"{member} (`{member.id}`)", inline=False)
        embed.add_field(name="📌 الملاحظة", value="تعذر إرسال رسالة المغادرة في الخاص، لذلك تم تسجيل المغادرة هنا.", inline=False)
        await send_system_log(member.guild, embed)


# ============================================================
# 18) لوق الرومات والرتب
# ============================================================
@bot.event
async def on_guild_channel_create(channel):
    if not channel.guild:
        return
    entry = await get_recent_audit_entry(channel.guild, discord.AuditLogAction.channel_create, channel.id)
    embed = log_embed("📁 تم إنشاء روم", discord.Color.green())
    embed.add_field(name="الروم", value=channel.mention if hasattr(channel, "mention") else str(channel), inline=True)
    embed.add_field(name="بواسطة", value=entry.user.mention if entry and entry.user else "غير معروف", inline=True)
    await send_system_log(channel.guild, embed)


@bot.event
async def on_guild_channel_delete(channel):
    if not channel.guild:
        return
    entry = await get_recent_audit_entry(channel.guild, discord.AuditLogAction.channel_delete, channel.id)
    embed = log_embed("🗑️ تم حذف روم", discord.Color.red())
    embed.add_field(name="الروم", value=f"`{channel.name}`", inline=True)
    embed.add_field(name="بواسطة", value=entry.user.mention if entry and entry.user else "غير معروف", inline=True)
    await send_system_log(channel.guild, embed)


@bot.event
async def on_guild_role_create(role):
    entry = await get_recent_audit_entry(role.guild, discord.AuditLogAction.role_create, role.id)
    embed = log_embed("🎭 تم إنشاء رتبة", discord.Color.green())
    embed.add_field(name="الرتبة", value=role.mention, inline=True)
    embed.add_field(name="بواسطة", value=entry.user.mention if entry and entry.user else "غير معروف", inline=True)
    await send_system_log(role.guild, embed)


@bot.event
async def on_guild_role_delete(role):
    entry = await get_recent_audit_entry(role.guild, discord.AuditLogAction.role_delete, role.id)
    embed = log_embed("🗑️ تم حذف رتبة", discord.Color.red())
    embed.add_field(name="الرتبة", value=f"`{role.name}`", inline=True)
    embed.add_field(name="بواسطة", value=entry.user.mention if entry and entry.user else "غير معروف", inline=True)
    await send_system_log(role.guild, embed)


# ============================================================
# 19) نظام الاختصارات - جميع أوامر الإدارة + Rank
# ============================================================
SHORTCUT_ACTIONS = {
    "clear": "حذف الرسائل",
    "warn": "تحذير عضو",
    "unwarn": "حذف آخر تحذير",
    "warnings": "عرض تحذيرات عضو",
    "timeout": "Timeout",
    "untimeout": "إلغاء Timeout",
    "silence": "إسكات عضو (Timeout)",
    "unsilence": "إلغاء الإسكات",
    "kick": "طرد عضو",
    "ban": "حظر عضو",
    "unban": "فك الحظر بالـ ID",
    "lock": "قفل الروم",
    "unlock": "فتح الروم",
    "rank": "عرض XP والرتبة",
    "addrole": "إضافة رتبة لعضو",
    "removerole": "سحب رتبة من عضو",
    "resetxp": "تصفير XP جميع الأعضاء",
}


def shortcut_target(message):
    return message.mentions[0] if message.mentions else None


async def send_rank_message(channel, member):
    row = cursor.execute(
        "SELECT text_xp, voice_xp, level FROM user_xp WHERE guild_id=? AND user_id=?",
        (member.guild.id, member.id)
    ).fetchone()

    text_xp = row[0] if row else 0
    voice_xp = row[1] if row else 0
    total = (text_xp or 0) + (voice_xp or 0)
    level, progress, needed = calculate_level(total)

    if row and row[2] != level:
        cursor.execute(
            "UPDATE user_xp SET level=? WHERE guild_id=? AND user_id=?",
            (level, member.guild.id, member.id)
        )
        db_commit()

    filled = min(12, int((progress / needed) * 12)) if needed else 0
    bar = "█" * filled + "░" * (12 - filled)

    embed = discord.Embed(
        title="📊 بطاقة XP",
        description=(
            f"**{member.display_name}**\n\n"
            f"**المستوى:** `{level}`\n"
            f"`{bar}`\n"
            f"**{progress:,} / {needed:,} XP**"
        ),
        color=discord.Color.blurple()
    )
    embed.add_field(name="✨ XP النص", value=f"`{text_xp:,}`", inline=True)
    embed.add_field(name="🔊 XP الصوت", value=f"`{voice_xp:,}`", inline=True)
    embed.add_field(name="💎 XP الكلي", value=f"`{total:,}`", inline=True)
    embed.set_thumbnail(url=member.display_avatar.url)
    await channel.send(embed=embed)


async def run_shortcut(message, action):
    member = message.author
    if not isinstance(member, discord.Member) or not message.guild:
        return

    args = message.content.strip().split()
    action = action.strip().lower()
    target = shortcut_target(message)

    try:
        if action == "rank":
            target = target or member
            await send_rank_message(message.channel, target)
            return

        if action == "clear":
            if not member.guild_permissions.manage_messages:
                return
            amount = int(args[1]) if len(args) > 1 and args[1].isdigit() else 10
            amount = max(1, min(amount, 100))
            deleted = await message.channel.purge(limit=amount)
            await message.channel.send(f"🗑️ تم حذف `{len(deleted)}` رسالة.", delete_after=4)
            return

        if action == "lock":
            if not member.guild_permissions.manage_channels:
                return
            await message.channel.set_permissions(
                message.guild.default_role,
                send_messages=False,
                view_channel=True,
                reason=f"Shortcut lock by {member}"
            )
            await message.channel.send("🔒 تم قفل الروم.", delete_after=4)
            return

        if action == "unlock":
            if not member.guild_permissions.manage_channels:
                return
            await message.channel.set_permissions(
                message.guild.default_role,
                send_messages=True,
                view_channel=True,
                reason=f"Shortcut unlock by {member}"
            )
            await message.channel.send("🔓 تم فتح الروم.", delete_after=4)
            return

        if action == "resetxp":
            if not member.guild_permissions.administrator:
                return
            cursor.execute(
                "UPDATE user_xp SET text_xp=0, voice_xp=0, level=1 WHERE guild_id=?",
                (message.guild.id,)
            )
            db_commit()
            await message.channel.send("♻️ تم تصفير XP لجميع أعضاء السيرفر.", delete_after=5)
            return

        if action in {"addrole", "removerole"}:
            if not isinstance(target, discord.Member):
                return
            if not (member.guild_permissions.manage_roles or member.guild_permissions.administrator):
                return
            if len(args) < 3:
                return

            role_token = args[2] if action in {"addrole", "removerole"} else ""
            role_id_match = re.search(r"\d+", role_token)
            role = member.guild.get_role(int(role_id_match.group())) if role_id_match else None
            if not role:
                return
            if role >= member.top_role and member.id != member.guild.owner_id:
                return
            if member.guild.me and role >= member.guild.me.top_role:
                return

            if action == "addrole":
                await target.add_roles(role, reason=f"Shortcut addrole by {member}")
                await message.channel.send(f"✅ تمت إضافة {role.mention} إلى {target.mention}.", delete_after=6)
            else:
                await target.remove_roles(role, reason=f"Shortcut removerole by {member}")
                await message.channel.send(f"✅ تم سحب {role.mention} من {target.mention}.", delete_after=6)
            return

        if action in {"timeout", "silence"}:
            if not member.guild_permissions.moderate_members or not isinstance(target, discord.Member):
                return
            if target == message.guild.owner or target.top_role >= member.top_role:
                return
            if message.guild.me and target.top_role >= message.guild.me.top_role:
                return

            if action == "silence":
                minutes = 60
                reason_start = 2
            else:
                minutes = int(args[2]) if len(args) > 2 and args[2].isdigit() else 5
                reason_start = 3

            minutes = max(1, min(minutes, MAX_TIMEOUT_MINUTES))
            reason = " ".join(args[reason_start:]) if len(args) > reason_start else f"Shortcut {action}"
            remember_timeout_context(message.guild.id, target.id, member.id, reason, f"{minutes} دقيقة")
            await target.timeout(datetime.timedelta(minutes=minutes), reason=reason)
            return

        if action in {"untimeout", "unsilence"}:
            if not member.guild_permissions.moderate_members or not isinstance(target, discord.Member):
                return
            if target == message.guild.owner or target.top_role >= member.top_role:
                return
            remember_timeout_context(message.guild.id, target.id, member.id, "Shortcut untimeout", "إلغاء")
            await target.timeout(None, reason="Shortcut untimeout")
            return

        if action == "warn":
            if not (member.guild_permissions.moderate_members or member.guild_permissions.manage_messages):
                return
            if not isinstance(target, discord.Member):
                return
            reason = " ".join(args[2:]) if len(args) > 2 else "بدون سبب"
            cursor.execute(
                "INSERT INTO warnings (guild_id,user_id,reason,moderator_id,date) VALUES (?,?,?,?,?)",
                (message.guild.id, target.id, reason, member.id, utcnow().isoformat())
            )
            db_commit()
            return

        if action == "warnings":
            if not member.guild_permissions.manage_messages and not member.guild_permissions.moderate_members:
                return
            if not isinstance(target, discord.Member):
                return
            rows = cursor.execute(
                "SELECT reason, moderator_id, date FROM warnings WHERE guild_id=? AND user_id=? ORDER BY id DESC",
                (message.guild.id, target.id)
            ).fetchall()
            if not rows:
                return await message.channel.send(f"✅ {target.mention} لا توجد عليه تحذيرات.", delete_after=5)
            lines = []
            for i, (reason, moderator_id, date) in enumerate(rows, 1):
                lines.append(f"**#{i}** — {reason}\nبواسطة: <@{moderator_id}>\n`{date}`")
            embed = discord.Embed(
                title=f"⚠️ تحذيرات {target}",
                description="\n\n".join(lines)[:4096],
                color=discord.Color.orange()
            )
            await message.channel.send(embed=embed)
            return

        if action == "unwarn":
            if not (member.guild_permissions.moderate_members or member.guild_permissions.manage_messages):
                return
            if not isinstance(target, discord.Member):
                return
            row = cursor.execute(
                "SELECT id FROM warnings WHERE guild_id=? AND user_id=? ORDER BY id DESC LIMIT 1",
                (message.guild.id, target.id)
            ).fetchone()
            if not row:
                return await message.channel.send("⚠️ لا يوجد تحذير لحذفه.", delete_after=5)
            cursor.execute("DELETE FROM warnings WHERE id=?", (row[0],))
            db_commit()
            return

        if action == "kick":
            if not member.guild_permissions.kick_members or not isinstance(target, discord.Member):
                return
            if target == message.guild.owner or target.top_role >= member.top_role:
                return
            reason = " ".join(args[2:]) if len(args) > 2 else "Shortcut kick"
            await target.kick(reason=reason)
            return

        if action == "ban":
            if not member.guild_permissions.ban_members or not isinstance(target, discord.Member):
                return
            if target == message.guild.owner or target.top_role >= member.top_role:
                return
            reason = " ".join(args[2:]) if len(args) > 2 else "Shortcut ban"
            await target.ban(reason=reason)
            return

        if action == "unban":
            if not member.guild_permissions.ban_members:
                return
            raw_id = args[1] if len(args) > 1 else ""
            if not raw_id.isdigit():
                return
            user = await bot.fetch_user(int(raw_id))
            await message.guild.unban(user, reason=f"Shortcut unban by {member}")
            return

    except (discord.Forbidden, discord.NotFound, discord.HTTPException, ValueError):
        return


# ============================================================
# 20) أوامر النظام والإعدادات
# ============================================================
# ============================================================
@bot.tree.command(name="set_system_log_channel", description="تعيين روم اللوق العام")
@app_commands.describe(channel="روم اللوق")
async def set_system_log_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    if not await check_admin_permission(interaction, "admin"):
        return await safe_respond(interaction, "❌ للإدارة فقط.", ephemeral=True)

    cursor.execute("""
        INSERT INTO system_config (guild_id, log_channel_id)
        VALUES (?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET log_channel_id=excluded.log_channel_id
    """, (interaction.guild.id, channel.id))
    db_commit()
    await safe_respond(interaction, f"✅ تم تعيين روم اللوق: {channel.mention}", ephemeral=True)


@bot.tree.command(name="antilink", description="تشغيل أو إيقاف نظام منع الروابط")
@app_commands.describe(enabled="True للتشغيل / False للإيقاف")
async def antilink(interaction: discord.Interaction, enabled: bool):
    if not await check_admin_permission(interaction, "admin"):
        return await safe_respond(interaction, "❌ للإدارة فقط.", ephemeral=True)

    cursor.execute("""
        INSERT INTO bot_settings (guild_id, levelup_channel_id, antilink_enabled)
        VALUES (?, NULL, ?)
        ON CONFLICT(guild_id) DO UPDATE SET antilink_enabled=excluded.antilink_enabled
    """, (interaction.guild.id, 1 if enabled else 0))
    db_commit()

    await safe_respond(
        interaction,
        f"✅ نظام منع الروابط: **{'مفعل' if enabled else 'متوقف'}**",
        ephemeral=True
    )


@bot.tree.command(name="set_auto_role", description="تعيين رتبة تلقائية للأعضاء الجدد")
@app_commands.describe(role="الرتبة")
async def set_auto_role(interaction: discord.Interaction, role: discord.Role):
    if not await check_admin_permission(interaction, "admin"):
        return await safe_respond(interaction, "❌ للإدارة فقط.", ephemeral=True)
    cursor.execute("""
        INSERT INTO auto_roles (guild_id, role_id) VALUES (?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET role_id=excluded.role_id
    """, (interaction.guild.id, role.id))
    db_commit()
    await safe_respond(interaction, f"✅ الرتبة التلقائية: {role.mention}", ephemeral=True)


@bot.tree.command(name="set_helper_role", description="تعيين رتبة المساعدين")
async def set_helper_role(interaction: discord.Interaction, role: discord.Role):
    if not await check_admin_permission(interaction, "admin"):
        return await safe_respond(interaction, "❌ للإدارة فقط.", ephemeral=True)
    helper, mod, admin = get_admin_roles(interaction.guild.id)
    cursor.execute("""
        INSERT INTO admin_roles (guild_id, helper_role, mod_role, admin_role) VALUES (?, ?, ?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET helper_role=excluded.helper_role
    """, (interaction.guild.id, role.id, mod, admin))
    db_commit()
    await safe_respond(interaction, f"✅ رتبة المساعدين: {role.mention}", ephemeral=True)


@bot.tree.command(name="set_mod_role", description="تعيين رتبة المشرفين")
async def set_mod_role(interaction: discord.Interaction, role: discord.Role):
    if not await check_admin_permission(interaction, "admin"):
        return await safe_respond(interaction, "❌ للإدارة فقط.", ephemeral=True)
    helper, mod, admin = get_admin_roles(interaction.guild.id)
    cursor.execute("""
        INSERT INTO admin_roles (guild_id, helper_role, mod_role, admin_role) VALUES (?, ?, ?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET mod_role=excluded.mod_role
    """, (interaction.guild.id, helper, role.id, admin))
    db_commit()
    await safe_respond(interaction, f"✅ رتبة المشرفين: {role.mention}", ephemeral=True)


@bot.tree.command(name="set_admin_role", description="تعيين رتبة الإدارة")
async def set_admin_role(interaction: discord.Interaction, role: discord.Role):
    if not await check_admin_permission(interaction, "admin"):
        return await safe_respond(interaction, "❌ للإدارة فقط.", ephemeral=True)
    helper, mod, admin = get_admin_roles(interaction.guild.id)
    cursor.execute("""
        INSERT INTO admin_roles (guild_id, helper_role, mod_role, admin_role) VALUES (?, ?, ?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET admin_role=excluded.admin_role
    """, (interaction.guild.id, helper, mod, role.id))
    db_commit()
    await safe_respond(interaction, f"✅ رتبة الإدارة: {role.mention}", ephemeral=True)


# ============================================================
# 21) أوامر XP
# ============================================================
@bot.tree.command(name="rank", description="عرض مستواك و XP")
@app_commands.describe(member="عضو آخر - اختياري")
async def rank(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user

    row = cursor.execute(
        "SELECT text_xp, voice_xp, level FROM user_xp WHERE guild_id=? AND user_id=?",
        (interaction.guild.id, member.id)
    ).fetchone()

    text_xp = row[0] if row else 0
    voice_xp = row[1] if row else 0
    stored_level = row[2] if row else 1

    total = text_xp + voice_xp
    level, progress, needed = calculate_level(total)

    # إصلاح المستوى المخزن لو كان من النظام القديم
    if stored_level != level:
        cursor.execute(
            "INSERT INTO user_xp (guild_id,user_id,text_xp,voice_xp,level) VALUES (?,?,?,?,?) "
            "ON CONFLICT(guild_id,user_id) DO UPDATE SET level=excluded.level",
            (interaction.guild.id, member.id, text_xp, voice_xp, level)
        )
        db_commit()

    filled = int((progress / needed) * 12) if needed else 0
    bar = "█" * filled + "░" * (12 - filled)

    embed = discord.Embed(
        title="📊 بطاقة XP",
        description=f"**{member.display_name}**\n\n**المستوى:** `{level}`\n`{bar}`\n**{progress:,} / {needed:,} XP**",
        color=discord.Color.blurple()
    )
    embed.add_field(name="✨ XP النص", value=f"`{text_xp:,}`", inline=True)
    embed.add_field(name="🔊 XP الصوت", value=f"`{voice_xp:,}`", inline=True)
    embed.add_field(name="💎 XP الكلي", value=f"`{total:,}`", inline=True)
    embed.set_thumbnail(url=member.display_avatar.url)
    await safe_respond(interaction, embed=embed)


@bot.tree.command(name="reset_all_xp", description="تصفير XP جميع أعضاء السيرفر")
async def reset_all_xp(interaction: discord.Interaction):
    if not await check_admin_permission(interaction, "admin"):
        return await safe_respond(interaction, "❌ للإدارة فقط.", ephemeral=True)

    cursor.execute(
        "UPDATE user_xp SET text_xp=0, voice_xp=0, level=1 WHERE guild_id=?",
        (interaction.guild.id,)
    )
    db_commit()
    await safe_respond(
        interaction,
        "♻️ تم تصفير XP لجميع أعضاء السيرفر. الجميع بدأ من **Level 1 / 0 XP**.",
        ephemeral=True
    )


@bot.tree.command(name="set_level_role", description="ربط رتبة بمستوى معين")
@app_commands.describe(level="المستوى", role="الرتبة")
async def set_level_role(interaction: discord.Interaction, level: int, role: discord.Role):
    if not await check_admin_permission(interaction, "admin"):
        return await safe_respond(interaction, "❌ للإدارة فقط.", ephemeral=True)

    if level < 2 or level > 100:
        return await safe_respond(interaction, "❌ المستوى يجب أن يكون بين 2 و100.", ephemeral=True)

    cursor.execute("""
        INSERT INTO level_roles (guild_id, level, role_id) VALUES (?, ?, ?)
        ON CONFLICT(guild_id, level) DO UPDATE SET role_id=excluded.role_id
    """, (interaction.guild.id, level, role.id))
    db_commit()
    await safe_respond(interaction, f"✅ عند الوصول للمستوى `{level}` يحصل العضو على {role.mention}", ephemeral=True)


@bot.tree.command(name="show_level_roles", description="عرض رتب المستويات")
async def show_level_roles(interaction: discord.Interaction):
    if not await check_admin_permission(interaction, "helper"):
        return await safe_respond(interaction, "❌ للإدارة فقط.", ephemeral=True)

    rows = cursor.execute(
        "SELECT level, role_id FROM level_roles WHERE guild_id=? ORDER BY level",
        (interaction.guild.id,)
    ).fetchall()

    if not rows:
        return await safe_respond(interaction, "لا توجد رتب مستويات.", ephemeral=True)

    lines = []
    for level, role_id in rows:
        role = interaction.guild.get_role(role_id)
        lines.append(f"**Level {level}:** {role.mention if role else '`رتبة محذوفة`'}")

    embed = discord.Embed(title="🏆 رتب المستويات", description="\n".join(lines), color=discord.Color.gold())
    await safe_respond(interaction, embed=embed, ephemeral=True)


# ============================================================
# 22) أوامر Welcome / Leave
# ============================================================
@bot.tree.command(name="welcome", description="إعداد رسالة ترحيب خاصة بالعضو")
@app_commands.describe(title="العنوان", message="الرسالة", color="لون Hex مثل #5865F2", image="رابط صورة اختياري", channel="روم الترحيب")
async def welcome(interaction: discord.Interaction, title: str, message: str, color: str = "5865F2", image: str = None, channel: discord.TextChannel = None):
    if not await check_admin_permission(interaction, "admin"):
        return await safe_respond(interaction, "❌ للإدارة فقط.", ephemeral=True)
    cursor.execute("""
        INSERT INTO system_config (guild_id, welcome_title, welcome_msg, welcome_color, welcome_img, welcome_channel_id)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET
            welcome_title=excluded.welcome_title, welcome_msg=excluded.welcome_msg,
            welcome_color=excluded.welcome_color, welcome_img=excluded.welcome_img,
            welcome_channel_id=excluded.welcome_channel_id
    """, (interaction.guild.id, title, message, color, image, channel.id if channel else None))
    db_commit()
    await safe_respond(interaction, f"✅ تم حفظ الترحيب{f' في {channel.mention}' if channel else ''}.", ephemeral=True)


@bot.tree.command(name="leave", description="إعداد رسالة مغادرة خاصة، واللوق احتياطًا")
@app_commands.describe(title="العنوان", message="الرسالة", color="لون Hex مثل #ED4245", image="رابط صورة اختياري", channel="روم المغادرة")
async def leave(interaction: discord.Interaction, title: str, message: str, color: str = "ED4245", image: str = None, channel: discord.TextChannel = None):
    if not await check_admin_permission(interaction, "admin"):
        return await safe_respond(interaction, "❌ للإدارة فقط.", ephemeral=True)
    cursor.execute("""
        INSERT INTO system_config (guild_id, leave_title, leave_msg, leave_color, leave_img, leave_channel_id)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET
            leave_title=excluded.leave_title, leave_msg=excluded.leave_msg,
            leave_color=excluded.leave_color, leave_img=excluded.leave_img,
            leave_channel_id=excluded.leave_channel_id
    """, (interaction.guild.id, title, message, color, image, channel.id if channel else None))
    db_commit()
    await safe_respond(interaction, f"✅ تم حفظ المغادرة{f' في {channel.mention}' if channel else ''}.", ephemeral=True)


# ============================================================
# 23) أوامر الإشراف
# ============================================================
@bot.tree.command(name="clear", description="حذف عدد من الرسائل")
@app_commands.describe(amount="عدد الرسائل 1-100")
async def clear(interaction: discord.Interaction, amount: int):
    if not await check_admin_permission(interaction, "helper"):
        return await safe_respond(interaction, "❌ لا تملك الصلاحية.", ephemeral=True)
    if amount < 1 or amount > 100:
        return await safe_respond(interaction, "❌ العدد من 1 إلى 100.", ephemeral=True)

    if not interaction.channel or not hasattr(interaction.channel, "purge"):
        return await safe_respond(interaction, "❌ هذا الروم لا يدعم الحذف الجماعي.", ephemeral=True)

    deleted = await interaction.channel.purge(limit=amount)
    await safe_respond(interaction, f"🗑️ تم حذف `{len(deleted)}` رسالة.", ephemeral=True)


@bot.tree.command(name="warn", description="تحذير عضو")
async def warn(interaction: discord.Interaction, member: discord.Member, reason: str = "بدون سبب"):
    if not await check_admin_permission(interaction, "mod"):
        return await safe_respond(interaction, "❌ لا تملك الصلاحية.", ephemeral=True)
    cursor.execute(
        "INSERT INTO warnings (guild_id,user_id,reason,moderator_id,date) VALUES (?,?,?,?,?)",
        (interaction.guild.id, member.id, reason, interaction.user.id, utcnow().isoformat())
    )
    db_commit()
    await safe_respond(interaction, f"⚠️ تم تحذير {member.mention}\n**السبب:** {reason}")


@bot.tree.command(name="warnings", description="عرض تحذيرات عضو")
async def warnings(interaction: discord.Interaction, member: discord.Member):
    if not await check_admin_permission(interaction, "helper"):
        return await safe_respond(interaction, "❌ لا تملك الصلاحية.", ephemeral=True)
    rows = cursor.execute(
        "SELECT reason, moderator_id, date FROM warnings WHERE guild_id=? AND user_id=? ORDER BY id DESC",
        (interaction.guild.id, member.id)
    ).fetchall()
    if not rows:
        return await safe_respond(interaction, "✅ لا توجد تحذيرات.", ephemeral=True)

    lines = []
    for i, (reason, moderator_id, date) in enumerate(rows, 1):
        lines.append(f"**#{i}** — {reason}\nبواسطة: <@{moderator_id}>\n`{date}`")
    embed = discord.Embed(title=f"⚠️ تحذيرات {member}", description="\n\n".join(lines)[:4096], color=discord.Color.orange())
    await safe_respond(interaction, embed=embed, ephemeral=True)


@bot.tree.command(name="timeout", description="إعطاء Timeout لعضو")
@app_commands.describe(member="العضو", minutes="المدة بالدقائق", reason="السبب")
async def timeout_slash(interaction: discord.Interaction, member: discord.Member, minutes: int, reason: str = "بدون سبب"):
    if not await check_admin_permission(interaction, "mod"):
        return await safe_respond(interaction, "❌ لا تملك الصلاحية.", ephemeral=True)
    if minutes < 1 or minutes > MAX_TIMEOUT_MINUTES:
        return await safe_respond(interaction, f"❌ المدة من 1 إلى {MAX_TIMEOUT_MINUTES} دقيقة.", ephemeral=True)
    if member == interaction.guild.owner or member.top_role >= interaction.user.top_role and interaction.user.id != interaction.guild.owner_id:
        return await safe_respond(interaction, "❌ لا يمكنك تطبيق Timeout على هذا العضو بسبب ترتيب الرتب.", ephemeral=True)
    if interaction.guild.me and member.top_role >= interaction.guild.me.top_role:
        return await safe_respond(interaction, "❌ رتبة العضو أعلى من رتبة البوت.", ephemeral=True)

    remember_timeout_context(interaction.guild.id, member.id, interaction.user.id, reason, f"{minutes} دقيقة")
    try:
        await member.timeout(datetime.timedelta(minutes=minutes), reason=reason)
    except discord.Forbidden:
        return await safe_respond(interaction, "❌ البوت لا يملك صلاحية Moderate Members.", ephemeral=True)
    except discord.HTTPException as e:
        return await safe_respond(interaction, f"❌ حدث خطأ من Discord: `{e}`", ephemeral=True)

    await safe_respond(interaction, f"🔇 تم Timeout لـ {member.mention} لمدة **{minutes} دقيقة**.")


@bot.tree.command(name="untimeout", description="إلغاء Timeout")
async def untimeout(interaction: discord.Interaction, member: discord.Member, reason: str = "بدون سبب"):
    if not await check_admin_permission(interaction, "mod"):
        return await safe_respond(interaction, "❌ لا تملك الصلاحية.", ephemeral=True)
    remember_timeout_context(interaction.guild.id, member.id, interaction.user.id, reason, "إلغاء")
    try:
        await member.timeout(None, reason=reason)
    except discord.Forbidden:
        return await safe_respond(interaction, "❌ لا أملك صلاحية Moderation.", ephemeral=True)
    await safe_respond(interaction, f"🔊 تم إلغاء Timeout عن {member.mention}.")


@bot.tree.command(name="kick", description="طرد عضو")
async def kick(interaction: discord.Interaction, member: discord.Member, reason: str = "بدون سبب"):
    if not await check_admin_permission(interaction, "mod"):
        return await safe_respond(interaction, "❌ لا تملك الصلاحية.", ephemeral=True)
    try:
        await member.kick(reason=reason)
        await safe_respond(interaction, f"👢 تم طرد {member.mention}.")
    except discord.Forbidden:
        await safe_respond(interaction, "❌ لا أستطيع طرد هذا العضو.", ephemeral=True)


@bot.tree.command(name="ban", description="حظر عضو")
async def ban(interaction: discord.Interaction, member: discord.Member, reason: str = "بدون سبب"):
    if not await check_admin_permission(interaction, "admin"):
        return await safe_respond(interaction, "❌ لا تملك الصلاحية.", ephemeral=True)
    try:
        await member.ban(reason=reason)
        await safe_respond(interaction, f"🔨 تم حظر {member.mention}.")
    except discord.Forbidden:
        await safe_respond(interaction, "❌ لا أستطيع حظر هذا العضو.", ephemeral=True)


@bot.tree.command(name="unban", description="فك حظر عضو بالـ ID")
async def unban(interaction: discord.Interaction, user_id: str):
    if not await check_admin_permission(interaction, "admin"):
        return await safe_respond(interaction, "❌ لا تملك الصلاحية.", ephemeral=True)
    try:
        user = await bot.fetch_user(int(user_id))
        await interaction.guild.unban(user, reason=f"Unban by {interaction.user}")
        await safe_respond(interaction, f"✅ تم فك الحظر عن `{user}`.")
    except (ValueError, discord.NotFound, discord.Forbidden, discord.HTTPException):
        await safe_respond(interaction, "❌ لم أستطع فك الحظر. تأكد من ID والصلاحيات.", ephemeral=True)


@bot.tree.command(name="lock", description="قفل الروم")
async def lock(interaction: discord.Interaction):
    if not await check_admin_permission(interaction, "mod"):
        return await safe_respond(interaction, "❌ لا تملك الصلاحية.", ephemeral=True)
    await interaction.channel.set_permissions(interaction.guild.default_role, send_messages=False, view_channel=True, reason=f"Locked by {interaction.user}")
    await safe_respond(interaction, "🔒 تم قفل الروم.")


@bot.tree.command(name="unlock", description="فتح الروم")
async def unlock(interaction: discord.Interaction):
    if not await check_admin_permission(interaction, "mod"):
        return await safe_respond(interaction, "❌ لا تملك الصلاحية.", ephemeral=True)
    await interaction.channel.set_permissions(interaction.guild.default_role, send_messages=True, view_channel=True, reason=f"Unlocked by {interaction.user}")
    await safe_respond(interaction, "🔓 تم فتح الروم.")


# ============================================================
# 24) أوامر الرتب والاختصارات
# ============================================================
@bot.tree.command(name="addrole", description="إضافة رتبة لعضو")
@app_commands.describe(member="العضو", role="الرتبة")
async def addrole(interaction: discord.Interaction, member: discord.Member, role: discord.Role):
    if not await check_admin_permission(interaction, "mod"):
        return await safe_respond(interaction, "❌ لا تملك الصلاحية.", ephemeral=True)
    if interaction.guild.me and role >= interaction.guild.me.top_role:
        return await safe_respond(interaction, "❌ رتبة البوت يجب أن تكون أعلى من الرتبة المطلوبة.", ephemeral=True)
    try:
        await member.add_roles(role, reason=f"Add role by {interaction.user}")
        await safe_respond(interaction, f"✅ تمت إضافة {role.mention} إلى {member.mention}.")
    except discord.Forbidden:
        await safe_respond(interaction, "❌ لا أستطيع إعطاء هذه الرتبة.", ephemeral=True)


@bot.tree.command(name="removerole", description="سحب رتبة من عضو")
@app_commands.describe(member="العضو", role="الرتبة")
async def removerole(interaction: discord.Interaction, member: discord.Member, role: discord.Role):
    if not await check_admin_permission(interaction, "mod"):
        return await safe_respond(interaction, "❌ لا تملك الصلاحية.", ephemeral=True)
    if interaction.guild.me and role >= interaction.guild.me.top_role:
        return await safe_respond(interaction, "❌ لا أستطيع التحكم بهذه الرتبة.", ephemeral=True)
    try:
        await member.remove_roles(role, reason=f"Remove role by {interaction.user}")
        await safe_respond(interaction, f"✅ تم سحب {role.mention} من {member.mention}.")
    except discord.Forbidden:
        await safe_respond(interaction, "❌ لا أستطيع سحب هذه الرتبة.", ephemeral=True)


# ============================================================
# 25) أوامر الاختصارات
# ============================================================
SHORTCUT_CHOICES = [
    app_commands.Choice(name="🗑️ clear — حذف الرسائل", value="clear"),
    app_commands.Choice(name="⚠️ warn — تحذير", value="warn"),
    app_commands.Choice(name="↩️ unwarn — حذف آخر تحذير", value="unwarn"),
    app_commands.Choice(name="📋 warnings — عرض التحذيرات", value="warnings"),
    app_commands.Choice(name="🔇 timeout — Timeout", value="timeout"),
    app_commands.Choice(name="🔊 untimeout — إلغاء Timeout", value="untimeout"),
    app_commands.Choice(name="🤐 silence — إسكات", value="silence"),
    app_commands.Choice(name="🔊 unsilence — إلغاء الإسكات", value="unsilence"),
    app_commands.Choice(name="👢 kick — طرد", value="kick"),
    app_commands.Choice(name="🔨 ban — حظر", value="ban"),
    app_commands.Choice(name="🔓 unban — فك الحظر", value="unban"),
    app_commands.Choice(name="🔒 lock — قفل الروم", value="lock"),
    app_commands.Choice(name="🔓 unlock — فتح الروم", value="unlock"),
    app_commands.Choice(name="📊 rank — XP", value="rank"),
    app_commands.Choice(name="➕ addrole — إضافة رتبة", value="addrole"),
    app_commands.Choice(name="➖ removerole — سحب رتبة", value="removerole"),
    app_commands.Choice(name="♻️ resetxp — تصفير XP", value="resetxp"),
]


@bot.tree.command(name="shortcut", description="إضافة أو تعديل اختصار لأحد أوامر الإدارة")
@app_commands.describe(
    trigger="الكلمة التي ستكتبها لتشغيل الاختصار، مثال: !ban",
    action="الأمر الذي سينفذه الاختصار"
)
@app_commands.choices(action=SHORTCUT_CHOICES)
async def shortcut(interaction: discord.Interaction, trigger: str, action: app_commands.Choice[str]):
    if not await check_admin_permission(interaction, "admin"):
        return await safe_respond(interaction, "❌ للإدارة فقط.", ephemeral=True)

    trigger = trigger.strip()
    if not trigger or len(trigger) > 50:
        return await safe_respond(interaction, "❌ الاختصار يجب أن يكون بين 1 و50 حرفاً.", ephemeral=True)
    if "\n" in trigger or "\r" in trigger:
        return await safe_respond(interaction, "❌ الاختصار لا يمكن أن يحتوي على سطر جديد.", ephemeral=True)

    action_value = action.value
    cursor.execute("""
        INSERT INTO shortcuts (guild_id, trigger, action) VALUES (?, ?, ?)
        ON CONFLICT(guild_id, trigger) DO UPDATE SET action=excluded.action
    """, (interaction.guild.id, trigger, action_value))
    db_commit()

    await safe_respond(
        interaction,
        f"✅ تم حفظ الاختصار\n`{trigger}` → **{SHORTCUT_ACTIONS[action_value]}** (`{action_value}`)",
        ephemeral=True
    )


@bot.tree.command(name="shortcuts", description="عرض جميع الاختصارات الموجودة")
async def shortcuts(interaction: discord.Interaction):
    if not await check_admin_permission(interaction, "helper"):
        return await safe_respond(interaction, "❌ لا تملك الصلاحية.", ephemeral=True)

    rows = cursor.execute(
        "SELECT trigger, action FROM shortcuts WHERE guild_id=? ORDER BY trigger",
        (interaction.guild.id,)
    ).fetchall()

    if not rows:
        return await safe_respond(interaction, "📭 لا توجد اختصارات محفوظة.", ephemeral=True)

    lines = []
    for trigger, action in rows:
        lines.append(f"`{trigger}` → **{SHORTCUT_ACTIONS.get(action, action)}**")

    embed = discord.Embed(
        title="⚡ اختصارات السيرفر",
        description="\n".join(lines)[:4096],
        color=discord.Color.blurple()
    )
    await safe_respond(interaction, embed=embed, ephemeral=True)


@bot.tree.command(name="remove_shortcut", description="حذف اختصار")
@app_commands.describe(trigger="الاختصار الذي تريد حذفه")
async def remove_shortcut(interaction: discord.Interaction, trigger: str):
    if not await check_admin_permission(interaction, "admin"):
        return await safe_respond(interaction, "❌ للإدارة فقط.", ephemeral=True)

    cursor.execute(
        "DELETE FROM shortcuts WHERE guild_id=? AND trigger=?",
        (interaction.guild.id, trigger.strip())
    )
    db_commit()
    await safe_respond(interaction, "✅ تم حذف الاختصار.", ephemeral=True)


# ============================================================
# 25) أوامر التكتات
# ============================================================
# ============================================================
@bot.tree.command(name="ticket_panel", description="فتح لوحة إعداد التكتات")
async def ticket_panel(interaction: discord.Interaction):
    if not await check_admin_permission(interaction, "admin"):
        return await safe_respond(interaction, "❌ للإدارة فقط.", ephemeral=True)

    row = cursor.execute(
        "SELECT log_channel_id FROM system_config WHERE guild_id=?",
        (interaction.guild.id,)
    ).fetchone()
    log_channel_id = row[0] if row else None

    embed = discord.Embed(
        title="🎫 DFG Ticket Dashboard",
        description=(
            "من هنا تقدر تبني لوحة التكتات.\n\n"
            "**ألوان الأزرار:**\n"
            "🔵 blue — ⚪ gray — 🟢 green — 🔴 red"
        ),
        color=discord.Color.blurple()
    )
    await safe_respond(interaction, embed=embed, view=SetupDashboardView(log_channel_id), ephemeral=True)


@bot.tree.command(name="stats", description="إحصائيات استلام التكتات")
async def stats(interaction: discord.Interaction):
    if not await check_admin_permission(interaction, "helper"):
        return await safe_respond(interaction, "❌ للإدارة فقط.", ephemeral=True)

    data = ticket_data.get("stats", {}).get(str(interaction.guild.id), {})
    if not data:
        return await safe_respond(interaction, "لا توجد إحصائيات بعد.", ephemeral=True)

    lines = []
    for user_id, count in sorted(data.items(), key=lambda x: x[1], reverse=True):
        lines.append(f"<@{user_id}> — **{count}** تكت")

    embed = discord.Embed(title="📊 Ticket Stats", description="\n".join(lines)[:4096], color=discord.Color.gold())
    await safe_respond(interaction, embed=embed, ephemeral=True)


# ============================================================
# 26) نظام تكت الأسئلة - منفصل عن نظام التكت الأساسي
# ============================================================
def get_qticket_config(guild_id):
    row=cursor.execute("SELECT staff_role_id,category_id,log_channel_id,welcome_message,questions_json,panel_title,panel_description FROM ticket_question_config WHERE guild_id=?",(guild_id,)).fetchone()
    if not row:
        cursor.execute("INSERT INTO ticket_question_config (guild_id) VALUES (?)",(guild_id,)); db_commit()
        return {'staff_role_id':None,'category_id':None,'log_channel_id':None,'welcome_message':'أهلاً بك في التكت!','questions':[],'panel_title':'🎫 فتح تكت','panel_description':'أجب عن الأسئلة أولاً، وبعدها سيتم فتح التكت.'}
    try: questions=json.loads(row[4] or '[]')
    except Exception: questions=[]
    return {'staff_role_id':row[0],'category_id':row[1],'log_channel_id':row[2],'welcome_message':row[3] or 'أهلاً بك في التكت!','questions':questions,'panel_title':row[5] or '🎫 فتح تكت','panel_description':row[6] or 'أجب عن الأسئلة أولاً، وبعدها سيتم فتح التكت.'}


def set_qticket_config(guild_id, **values):
    get_qticket_config(guild_id)
    allowed={'staff_role_id','category_id','log_channel_id','welcome_message','questions_json','panel_title','panel_description'}
    values={k:v for k,v in values.items() if k in allowed}
    if not values:return
    a=', '.join(f'{k}=?' for k in values); cursor.execute(f'UPDATE ticket_question_config SET {a} WHERE guild_id=?',list(values.values())+[guild_id]); db_commit()


class QTicketRoleSelect(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120); select=discord.ui.RoleSelect(placeholder='اختر رتبة موظفي التكت...',min_values=1,max_values=1); select.callback=self.cb; self.add_item(select)
    async def cb(self,interaction):
        role=self.children[0].values[0]; set_qticket_config(interaction.guild.id,staff_role_id=role.id); await interaction.response.edit_message(content=f'✅ رتبة موظفي التكت: {role.mention}',view=None)

class QTicketCategorySelect(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120); select=discord.ui.ChannelSelect(placeholder='اختر كاتيجوري التكت...',min_values=1,max_values=1,channel_types=[discord.ChannelType.category]); select.callback=self.cb; self.add_item(select)
    async def cb(self,interaction):
        ch=self.children[0].values[0]; set_qticket_config(interaction.guild.id,category_id=ch.id); await interaction.response.edit_message(content=f'✅ كاتيجوري التكت: **{ch.name}**',view=None)

class QTicketLogSelect(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120); select=discord.ui.ChannelSelect(placeholder='اختر روم لوق تكت الأسئلة...',min_values=1,max_values=1,channel_types=[discord.ChannelType.text]); select.callback=self.cb; self.add_item(select)
    async def cb(self,interaction):
        ch=self.children[0].values[0]; set_qticket_config(interaction.guild.id,log_channel_id=ch.id); await interaction.response.edit_message(content=f'✅ روم اللوق: {ch.mention}',view=None)

class QTicketMessageModal(discord.ui.Modal,title='📝 رسالة تكت الأسئلة'):
    title_text=discord.ui.TextInput(label='عنوان البانل',max_length=256,default='🎫 فتح تكت')
    description=discord.ui.TextInput(label='الوصف',style=discord.TextStyle.paragraph,max_length=2000,default='أجب عن الأسئلة أولاً، وبعدها سيتم فتح التكت.')
    welcome=discord.ui.TextInput(label='رسالة داخل التكت',style=discord.TextStyle.paragraph,max_length=1500,required=False,default='أهلاً بك! سيتم الرد عليك قريباً.')
    async def on_submit(self,interaction):
        set_qticket_config(interaction.guild.id,panel_title=str(self.title_text.value),panel_description=str(self.description.value),welcome_message=str(self.welcome.value) or 'أهلاً بك!')
        await interaction.response.send_message('✅ تم حفظ رسائل تكت الأسئلة.',ephemeral=True)

class QTicketQuestionSetup:
    def __init__(self,guild_id,count): self.guild_id=guild_id; self.count=count; self.questions=[]

class QTicketQuestionCount(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120); opts=[discord.SelectOption(label=f'{i} أسئلة',value=str(i),emoji='📝') for i in range(1,11)]; select=discord.ui.Select(placeholder='اختر من 1 إلى 10 أسئلة...',options=opts); select.callback=self.cb; self.add_item(select)
    async def cb(self,interaction):
        setup=QTicketQuestionSetup(interaction.guild.id,int(self.children[0].values[0])); await interaction.response.send_modal(QTicketQuestionModal(setup,0))

class QTicketQuestionModal(discord.ui.Modal):
    def __init__(self,setup,start):
        super().__init__(title=f'📝 الأسئلة {start+1}-{min(start+5,setup.count)}'); self.setup=setup; self.start=start; self.inputs=[]
        for i in range(start,min(start+5,setup.count)):
            f=discord.ui.TextInput(label=f'السؤال {i+1}',placeholder='اكتب السؤال...',max_length=200); self.inputs.append(f); self.add_item(f)
    async def on_submit(self,interaction):
        self.setup.questions += [str(x.value).strip() for x in self.inputs]
        if self.start+5<self.setup.count: return await interaction.response.send_modal(QTicketQuestionModal(self.setup,self.start+5))
        qs=[q for q in self.setup.questions if q]
        set_qticket_config(self.setup.guild_id,questions_json=json.dumps(qs,ensure_ascii=False))
        await interaction.response.send_message(f'✅ تم حفظ **{len(qs)}** سؤال.',ephemeral=True)

class QTicketPanelView(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    async def admin(self,interaction):
        if not await check_admin_permission(interaction,'admin'):
            await interaction.response.send_message('❌ للإدارة فقط.',ephemeral=True); return False
        return True
    @discord.ui.button(label='👤 رتبة الموظفين',style=discord.ButtonStyle.primary,custom_id='dfg_qticket_staff')
    async def staff(self,i,b):
        if await self.admin(i): await i.response.send_message('اختر رتبة موظفي التكت:',view=QTicketRoleSelect(),ephemeral=True)
    @discord.ui.button(label='🗂️ الكاتيجوري',style=discord.ButtonStyle.secondary,custom_id='dfg_qticket_category')
    async def category(self,i,b):
        if await self.admin(i): await i.response.send_message('اختر كاتيجوري التكت:',view=QTicketCategorySelect(),ephemeral=True)
    @discord.ui.button(label='📝 الأسئلة',style=discord.ButtonStyle.primary,custom_id='dfg_qticket_questions')
    async def questions(self,i,b):
        if await self.admin(i): await i.response.send_message('اختر عدد الأسئلة:',view=QTicketQuestionCount(),ephemeral=True)
    @discord.ui.button(label='💬 الرسائل',style=discord.ButtonStyle.secondary,custom_id='dfg_qticket_messages')
    async def messages(self,i,b):
        if await self.admin(i): await i.response.send_modal(QTicketMessageModal())
    @discord.ui.button(label='📋 روم اللوق',style=discord.ButtonStyle.secondary,custom_id='dfg_qticket_log')
    async def log(self,i,b):
        if await self.admin(i): await i.response.send_message('اختر روم اللوق:',view=QTicketLogSelect(),ephemeral=True)
    @discord.ui.button(label='🚀 نشر التكت',style=discord.ButtonStyle.success,custom_id='dfg_qticket_publish')
    async def publish(self,i,b):
        if not await self.admin(i): return
        cfg=get_qticket_config(i.guild.id); missing=[]
        if not cfg['staff_role_id']: missing.append('رتبة الموظفين')
        if not cfg['category_id']: missing.append('الكاتيجوري')
        if not cfg['questions']: missing.append('الأسئلة')
        if missing:return await i.response.send_message('❌ الناقص: '+ '، '.join(missing),ephemeral=True)
        embed=discord.Embed(title=cfg['panel_title'],description=cfg['panel_description'],color=discord.Color.blurple())
        embed.set_footer(text='أجب عن الأسئلة قبل فتح التكت')
        try: msg=await i.channel.send(embed=embed,view=QTicketOpenView())
        except Exception as e:return await i.response.send_message(f'❌ فشل النشر: `{e}`',ephemeral=True)
        cursor.execute('INSERT OR REPLACE INTO ticket_question_panels(message_id,guild_id,channel_id) VALUES (?,?,?)',(msg.id,i.guild.id,i.channel.id)); db_commit(); bot.add_view(QTicketOpenView(),message_id=msg.id)
        await i.response.send_message('✅ تم نشر تكت الأسئلة.',ephemeral=True)

# جلسات مؤقتة في الذاكرة فقط: إغلاق/الخروج من النموذج لا يترك جلسة عالقة.
qticket_drafts = {}


class QTicketOpenView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label='🎫 بدء التقديم للتكت',style=discord.ButtonStyle.success,custom_id='dfg_qticket_open')
    async def open(self,interaction,b):
        guild=interaction.guild; member=interaction.user; cfg=get_qticket_config(guild.id)
        role=guild.get_role(cfg['staff_role_id']) if cfg['staff_role_id'] else None
        if role and role in member.roles:
            return await interaction.response.send_message('❌ أنت من موظفي التكت ولا تحتاج لتعبئة الطلب.',ephemeral=True)
        qs=cfg['questions']
        if not qs:return await interaction.response.send_message('❌ لم يتم إعداد الأسئلة.',ephemeral=True)

        # إعادة البدء دائماً تلغي أي مسودة قديمة. لا توجد مهلة زمنية.
        qticket_drafts[(guild.id,member.id)] = {'index':0,'answers':[],'questions':list(qs),'started_at':utcnow().isoformat()}
        await interaction.response.send_modal(QTicketQuestionsModal(guild.id,member.id,0))


class QTicketQuestionsModal(discord.ui.Modal):
    def __init__(self,guild_id,user_id,start_index):
        draft=qticket_drafts.get((guild_id,user_id))
        qs=draft['questions'] if draft else get_qticket_config(guild_id)['questions']
        end=min(start_index+5,len(qs))
        super().__init__(title=f'🎫 أسئلة التكت {start_index+1}-{end}')
        self.guild_id=guild_id; self.user_id=user_id; self.start_index=start_index
        self.inputs=[]
        for idx in range(start_index,end):
            field=discord.ui.TextInput(
                label=truncate(qs[idx],45),
                placeholder='اكتب إجابتك هنا...',
                style=discord.TextStyle.paragraph,
                required=True,
                max_length=1500
            )
            self.inputs.append(field); self.add_item(field)

    async def on_submit(self,interaction):
        key=(self.guild_id,self.user_id)
        draft=qticket_drafts.get(key)
        if not draft:
            return await interaction.response.send_message('❌ تم إلغاء طلب فتح التكت. ابدأ من جديد.',ephemeral=True)
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message('❌ هذا الطلب ليس لك.',ephemeral=True)

        for field in self.inputs:
            draft['answers'].append(str(field.value).strip())
        next_index=self.start_index+len(self.inputs)

        if next_index < len(draft['questions']):
            draft['index']=next_index
            db_commit()
            await interaction.response.send_message(
                f'✅ تم حفظ الإجابات. يوجد **{len(draft["questions"])-next_index}** أسئلة متبقية.',
                view=QTicketNextPageView(self.guild_id,self.user_id),
                ephemeral=True
            )
            return

        await finish_qticket(interaction,self.guild_id,self.user_id,draft['questions'],draft['answers'])


class QTicketNextPageView(discord.ui.View):
    def __init__(self,guild_id,user_id):
        super().__init__(timeout=None)
        self.guild_id=guild_id; self.user_id=user_id

    @discord.ui.button(label='➡️ الأسئلة التالية',style=discord.ButtonStyle.primary)
    async def next_page(self,interaction,b):
        if interaction.user.id!=self.user_id:
            return await interaction.response.send_message('❌ هذا الزر ليس لك.',ephemeral=True)
        draft=qticket_drafts.get((self.guild_id,self.user_id))
        if not draft:
            return await interaction.response.send_message('❌ تم إلغاء طلب فتح التكت. ابدأ من جديد.',ephemeral=True)
        await interaction.response.send_modal(QTicketQuestionsModal(self.guild_id,self.user_id,draft['index']))

    @discord.ui.button(label='❌ إلغاء',style=discord.ButtonStyle.danger)
    async def cancel(self,interaction,b):
        if interaction.user.id!=self.user_id:
            return await interaction.response.send_message('❌ هذا الزر ليس لك.',ephemeral=True)
        qticket_drafts.pop((self.guild_id,self.user_id),None)
        await interaction.response.edit_message(content='❌ تم إلغاء طلب فتح التكت.',embed=None,view=None)


async def finish_qticket(interaction,guild_id,user_id,qs,answers):
    guild=bot.get_guild(guild_id)
    if not guild:
        qticket_drafts.pop((guild_id,user_id),None)
        return await interaction.response.send_message('❌ لم أستطع العثور على السيرفر.',ephemeral=True)
    cfg=get_qticket_config(guild_id); category=guild.get_channel(cfg['category_id']) if cfg['category_id'] else None; staff=guild.get_role(cfg['staff_role_id']) if cfg['staff_role_id'] else None
    if not category or not isinstance(category,discord.CategoryChannel) or not staff:
        qticket_drafts.pop((guild_id,user_id),None)
        return await interaction.response.send_message('❌ إعدادات التكت غير مكتملة.',ephemeral=True)

    # منع فتح أكثر من تكت مفتوح لنفس العضو في هذا النظام.
    for channel_id, data in list(ticket_data.get('tickets',{}).items()):
        if data.get('guild_id')==guild_id and data.get('owner_id')==user_id:
            existing=guild.get_channel(int(channel_id))
            if existing:
                qticket_drafts.pop((guild_id,user_id),None)
                return await interaction.response.send_message(f'❌ لديك تكت مفتوح بالفعل: {existing.mention}',ephemeral=True)
            ticket_data['tickets'].pop(channel_id,None)
    save_ticket_data()

    member=guild.get_member(user_id) or interaction.user
    overwrites={
        guild.default_role:discord.PermissionOverwrite(view_channel=False),
        member:discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True),
        staff:discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True)
    }
    channel=await guild.create_text_channel(f'question-ticket-{member.name}'[:90],category=category,overwrites=overwrites,reason='Question ticket opened')
    body=f'👤 العضو: {member.mention}\n\n'+'\n'.join(f'**❓ {n}. {q}**\n> {a or "بدون إجابة"}' for n,(q,a) in enumerate(zip(qs,answers),1))
    embed=discord.Embed(title='🎫 طلب فتح تكت',description=body[:4096],color=discord.Color.blurple())
    embed.add_field(name='📌 الحالة',value='مفتوح',inline=False); embed.set_footer(text='Question Ticket System')
    control_view=TicketControlView('أسئلة التكت',cfg.get('log_channel_id'),member.id,staff.id)
    control_message=await channel.send(content=f'{member.mention} {staff.mention}',embed=embed,view=control_view)
    ticket_data.setdefault('tickets',{})[str(channel.id)]={
        'guild_id':guild_id,'owner_id':member.id,'ticket_type':'أسئلة التكت','staff_role_id':staff.id,
        'claimed_by':None,'created_at':utcnow().isoformat(),'log_channel_id':cfg.get('log_channel_id'),'control_message_id':control_message.id
    }
    save_ticket_data()
    try: bot.add_view(control_view,message_id=control_message.id)
    except Exception: pass
    qticket_drafts.pop((guild_id,user_id),None)
    await interaction.response.send_message(f'✅ تم فتح التكت: {channel.mention}',ephemeral=True)


@bot.tree.command(name='ticket_questions_panel',description='لوحة نظام التكت الذي يطرح أسئلة قبل فتح التكت')
async def ticket_questions_panel(interaction:discord.Interaction):
    if not await check_admin_permission(interaction,'admin'):return await safe_respond(interaction,'❌ للإدارة فقط.',ephemeral=True)
    embed=discord.Embed(title='🎫 تكت الأسئلة - لوحة التحكم',description='هذا النظام منفصل عن `/ticket_panel`.\nاضبط الرتبة والكاتيجوري والأسئلة ثم انشر اللوحة.',color=discord.Color.blurple())
    await safe_respond(interaction,embed=embed,view=QTicketPanelView(),ephemeral=True)

# ============================================================
# 26) أوامر الرد التلقائي + الرتب بالأزرار + الدعوات
# ============================================================
class AutoReplyAddModal(discord.ui.Modal, title="🤖 إضافة رد تلقائي"):
    trigger = discord.ui.TextInput(label="الكلمة أو العبارة", max_length=100, required=True)
    response = discord.ui.TextInput(label="الرد", style=discord.TextStyle.paragraph, max_length=1500, required=True)

    async def on_submit(self, interaction: discord.Interaction):
        trigger = normalize_reply_text(str(self.trigger.value))
        response = str(self.response.value).strip()
        if not trigger or not response:
            return await safe_respond(interaction, "❌ الكلمة والرد مطلوبان.", ephemeral=True)
        cursor.execute(
            "INSERT OR REPLACE INTO auto_replies(guild_id,trigger,response) VALUES(?,?,?)",
            (interaction.guild.id, trigger, response)
        )
        db_commit()
        await safe_respond(interaction, f"✅ تم حفظ الرد التلقائي.\n**{trigger}** → {response}", ephemeral=True)


class AutoReplyEditModal(discord.ui.Modal, title="✏️ تعديل الرد التلقائي"):
    response = discord.ui.TextInput(label="الرد الجديد", style=discord.TextStyle.paragraph, max_length=1500, required=True)

    def __init__(self, guild_id, trigger, old_response):
        super().__init__()
        self.guild_id = guild_id
        self.trigger = trigger
        self.response.default = old_response

    async def on_submit(self, interaction: discord.Interaction):
        response = str(self.response.value).strip()
        cursor.execute("UPDATE auto_replies SET response=? WHERE guild_id=? AND trigger=?", (response, self.guild_id, self.trigger))
        db_commit()
        await safe_respond(interaction, "✅ تم تعديل الرد.", ephemeral=True)


class AutoReplyManagerView(discord.ui.View):
    def __init__(self, guild_id, page=0):
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.page = page
        self.refresh_select()

    def rows(self):
        return cursor.execute(
            "SELECT trigger,response FROM auto_replies WHERE guild_id=? ORDER BY trigger",
            (self.guild_id,)
        ).fetchall()

    def refresh_select(self):
        rows = self.rows()
        pages = max(1, (len(rows) + 24) // 25)
        self.page = min(max(self.page, 0), pages - 1)
        start = self.page * 25
        page_rows = rows[start:start + 25]
        select = discord.ui.Select(
            placeholder="اختر ردًا لإدارته…" if page_rows else "لا توجد ردود",
            min_values=1, max_values=1,
            disabled=not bool(page_rows),
            custom_id="dfg_auto_reply_manage_select"
        )
        for trigger, response in page_rows:
            select.add_option(label=trigger[:100], value=trigger[:100], description=truncate(response, 90))

        async def selected(interaction):
            trigger = select.values[0]
            row = cursor.execute("SELECT response FROM auto_replies WHERE guild_id=? AND trigger=?", (self.guild_id, trigger)).fetchone()
            if not row:
                return await safe_respond(interaction, "❌ الرد غير موجود.", ephemeral=True)
            view = AutoReplyEditDeleteView(self.guild_id, trigger, row[0])
            await safe_respond(interaction, f"🤖 إدارة الرد: **{trigger}**", view=view, ephemeral=True)

        select.callback = selected
        self._select = select
        # لا نمسح الأزرار؛ فقط استبدل الـ Select نفسه.
        old = next((x for x in self.children if isinstance(x, discord.ui.Select)), None)
        if old:
            self.remove_item(old)
        self.add_item(select)

    @discord.ui.button(label="◀ السابق", style=discord.ButtonStyle.secondary, custom_id="dfg_auto_reply_prev")
    async def previous(self, interaction, button):
        if self.page <= 0:
            return await safe_respond(interaction, "هذه أول صفحة.", ephemeral=True)
        self.page -= 1
        self.refresh_select()
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="التالي ▶", style=discord.ButtonStyle.secondary, custom_id="dfg_auto_reply_next")
    async def next_page(self, interaction, button):
        total = len(self.rows())
        if (self.page + 1) * 25 >= total:
            return await safe_respond(interaction, "هذه آخر صفحة.", ephemeral=True)
        self.page += 1
        self.refresh_select()
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="🔄 تحديث", style=discord.ButtonStyle.primary, custom_id="dfg_auto_reply_refresh")
    async def refresh(self, interaction, button):
        self.refresh_select()
        await interaction.response.edit_message(view=self)


class AutoReplyEditDeleteView(discord.ui.View):
    def __init__(self, guild_id, trigger, response):
        super().__init__(timeout=180)
        self.guild_id = guild_id
        self.trigger = trigger
        self.response_text = response

    @discord.ui.button(label="✏️ تعديل", style=discord.ButtonStyle.primary)
    async def edit(self, interaction, button):
        if not await check_admin_permission(interaction, "admin"):
            return await safe_respond(interaction, "❌ للإدارة فقط.", ephemeral=True)
        await interaction.response.send_modal(AutoReplyEditModal(self.guild_id, self.trigger, self.response_text))

    @discord.ui.button(label="🗑️ حذف", style=discord.ButtonStyle.danger)
    async def delete(self, interaction, button):
        if not await check_admin_permission(interaction, "admin"):
            return await safe_respond(interaction, "❌ للإدارة فقط.", ephemeral=True)
        cursor.execute("DELETE FROM auto_replies WHERE guild_id=? AND trigger=?", (self.guild_id, self.trigger))
        db_commit()
        await safe_respond(interaction, f"🗑️ تم حذف الرد **{self.trigger}**.", ephemeral=True)


class AutoReplyPanelView(discord.ui.View):
    def __init__(self, guild_id):
        super().__init__(timeout=None)
        self.guild_id = guild_id

    @discord.ui.button(label="➕ إضافة رد", style=discord.ButtonStyle.success, custom_id="dfg_auto_reply_add")
    async def add_reply(self, interaction, button):
        if not await check_admin_permission(interaction, "admin"):
            return await safe_respond(interaction, "❌ للإدارة فقط.", ephemeral=True)
        await interaction.response.send_modal(AutoReplyAddModal())

    @discord.ui.button(label="⚙️ إدارة الردود", style=discord.ButtonStyle.primary, custom_id="dfg_auto_reply_manage")
    async def manage(self, interaction, button):
        if not await check_admin_permission(interaction, "helper"):
            return await safe_respond(interaction, "❌ لا تملك الصلاحية.", ephemeral=True)
        rows = cursor.execute("SELECT trigger,response FROM auto_replies WHERE guild_id=? ORDER BY trigger", (self.guild_id,)).fetchall()
        embed = discord.Embed(title="🤖 إدارة الردود التلقائية", description=f"عدد الردود: **{len(rows)}**\nاختر ردًا من القائمة لتعديله أو حذفه.", color=discord.Color.blurple())
        await safe_respond(interaction, embed=embed, view=AutoReplyManagerView(self.guild_id), ephemeral=True)

    @discord.ui.button(label="📋 عرض الردود", style=discord.ButtonStyle.secondary, custom_id="dfg_auto_reply_list")
    async def list_replies(self, interaction, button):
        if not await check_admin_permission(interaction, "helper"):
            return await safe_respond(interaction, "❌ لا تملك الصلاحية.", ephemeral=True)
        rows = cursor.execute("SELECT trigger,response FROM auto_replies WHERE guild_id=? ORDER BY trigger", (self.guild_id,)).fetchall()
        if not rows:
            return await safe_respond(interaction, "📭 لا توجد ردود تلقائية حاليًا.", ephemeral=True)
        embed = discord.Embed(title="📋 الردود التلقائية", color=discord.Color.blurple())
        for trigger, response in rows[:25]:
            embed.add_field(name=f"💬 {trigger}", value=truncate(response, 1024), inline=False)
        if len(rows) > 25:
            embed.set_footer(text=f"يعرض أول 25 من أصل {len(rows)} — استخدم إدارة الردود للتنقل.")
        await safe_respond(interaction, embed=embed, ephemeral=True)


@bot.tree.command(name="auto_reply_panel", description="فتح لوحة تحكم الردود التلقائية")
async def auto_reply_panel(interaction: discord.Interaction):
    if not await check_admin_permission(interaction, "admin"):
        return await safe_respond(interaction, "❌ للإدارة فقط.", ephemeral=True)
    embed = discord.Embed(
        title="🤖 نظام الردود التلقائية",
        description="من هنا تضيف وتعدل وتحذف جميع الردود التلقائية بدون أوامر منفصلة.",
        color=discord.Color.blurple()
    )
    # لوحة التحكم تكون رسالة عادية حتى تبقى واضحة للإدارة ويمكن استرجاع أزرارها بعد إعادة التشغيل.
    msg = await interaction.channel.send(embed=embed, view=AutoReplyPanelView(interaction.guild.id))
    cursor.execute(
        "INSERT OR REPLACE INTO auto_reply_panels(message_id,guild_id,channel_id) VALUES(?,?,?)",
        (msg.id, interaction.guild.id, interaction.channel.id)
    )
    db_commit()
    try:
        bot.add_view(AutoReplyPanelView(interaction.guild.id), message_id=msg.id)
    except Exception:
        pass
    await safe_respond(interaction, "✅ تم نشر لوحة الردود التلقائية.", ephemeral=True)


@bot.tree.command(name="role_button_panel", description="إنشاء لوحة رتب بأزرار")
async def role_button_panel(interaction: discord.Interaction):
    if not await check_admin_permission(interaction,"admin"):
        return await safe_respond(interaction,"❌ للإدارة فقط.",ephemeral=True)
    embed=discord.Embed(title="🎭 لوحة الرتب بالأزرار",description="أضف الرتب ثم انشر اللوحة. العضو يضغط الزر لإضافة الرتبة، ويضغط مرة ثانية لسحبها.",color=discord.Color.blurple())
    await safe_respond(interaction,embed=embed,view=RolePanelBuilder(interaction.guild.id),ephemeral=True)

@bot.tree.command(name="invites", description="فحص دعوات عضو")
@app_commands.describe(member="العضو الذي تريد فحص دعواته")
async def invites(interaction: discord.Interaction, member: discord.Member=None):
    member=member or interaction.user
    if not await check_admin_permission(interaction,"helper") and member.id != interaction.user.id:
        return await safe_respond(interaction,"❌ لا تملك الصلاحية.",ephemeral=True)
    total=0; links=[]
    try:
        invs=await interaction.guild.invites()
        for inv in invs:
            if inv.inviter and inv.inviter.id==member.id:
                total += inv.uses or 0
                links.append(f"`{inv.code}` — **{inv.uses or 0}** استخدام")
    except (discord.Forbidden,discord.HTTPException):
        return await safe_respond(interaction,"❌ البوت يحتاج صلاحية **Manage Server** لقراءة الدعوات.",ephemeral=True)
    embed=discord.Embed(title="🔗 فحص الدعوات",color=discord.Color.blurple())
    embed.add_field(name="👤 العضو",value=member.mention,inline=True)
    embed.add_field(name="📨 إجمالي الاستخدامات",value=str(total),inline=True)
    embed.add_field(name="🔗 الروابط",value="\n".join(links)[:1024] if links else "لا توجد دعوات.",inline=False)
    await safe_respond(interaction,embed=embed,ephemeral=True)


# ============================================================
# 27) نظام المساعدة - اختيار القسم من القائمة
# ============================================================
def help_embed_for(category: str):
    embeds = {
        "security": discord.Embed(
            title="🛡️ الحماية واللوق",
            description=(
                "`/antilink` — تشغيل/إيقاف منع الروابط\n"
                "`/set_system_log_channel` — تعيين روم اللوق العام\n"
                "`/invites` — فحص دعوات عضو\n\n"
                "اللوق يشمل الرسائل المحذوفة والمعدلة، Timeout، الرتب، الدخول والخروج وغيرها."
            ), color=discord.Color.red()
        ),
        "xp": discord.Embed(
            title="✨ XP",
            description=(
                "`/rank` — عرض المستوى وXP\n"
                "`/set_level_role` — ربط رتبة بمستوى\n"
                "`/show_level_roles` — عرض رتب المستويات\n"
                "`/reset_all_xp` — تصفير XP جميع الأعضاء\n\n"
                "🎁 التهنئة لا تظهر إلا عند الحصول على رتبة مكافأة، وتصل للعضو في الخاص."
            ), color=discord.Color.gold()
        ),
        "tickets": discord.Embed(
            title="🎫 التكت",
            description=(
                "`/ticket_panel` — فتح لوحة إعداد التكت\n"
                "`/ticket_questions_panel` — تكت منفصل بأسئلة قبل فتح الروم\n"
                "`/stats` — إحصائيات استلام التكت\n\n"
                "✋ استلام التكت — للإدارة\n"
                "🔒 إغلاق التكت — للإدارة أو الإداري المستلم\n"
                "👤 صاحب التكت يطلب الإغلاق من الإداري المستلم.\n"
                "⭐ عند الإغلاق يصل للعضو نموذج تقييم ويتم تحديث نفس رسالة اللوق."
            ), color=discord.Color.blurple()
        ),
        "applications": discord.Embed(
            title="📝 التقديم على رتبة",
            description=(
                "`/application_panel` — إنشاء لوحة تحكم التقديم\n\n"
                "من اللوحة تضبط: رتبة القبول، رتبة السماح بالتقديم، الأسئلة، رسالة التقديم، وروم الطلبات.\n"
                "بعد النشر يبدأ العضو التقديم من الخاص، ثم يصل الطلب للإدارة مع الأسئلة والأجوبة وأزرار القبول والرفض."
            ), color=discord.Color.green()
        ),
        "moderation": discord.Embed(
            title="🔨 الإدارة",
            description=(
                "`/warn` — تحذير\n"
                "`/warnings` — عرض التحذيرات\n"
                "`/timeout` — Timeout\n"
                "`/untimeout` — إلغاء Timeout\n"
                "`/kick` — طرد\n"
                "`/ban` — حظر\n"
                "`/unban` — فك الحظر\n"
                "`/clear` — حذف الرسائل\n"
                "`/lock` — منع الكتابة\n"
                "`/unlock` — السماح بالكتابة\n"
                "`/addrole` — إضافة رتبة\n"
                "`/removerole` — سحب رتبة"
            ), color=discord.Color.orange()
        ),
        "system": discord.Embed(
            title="⚙️ النظام",
            description=(
                "`/set_auto_role` — رتبة تلقائية عند الدخول\n"
                "`/role_button_panel` — لوحة رتب بأزرار\n"
                "`/auto_reply_panel` — لوحة تحكم الردود التلقائية\n"
                "`/set_helper_role` — رتبة المساعدين\n"
                "`/set_mod_role` — رتبة المشرفين\n"
                "`/set_admin_role` — رتبة الإدارة\n"
                "`/welcome` — إعداد الترحيب\n"
                "`/leave` — إعداد المغادرة"
            ), color=discord.Color.green()
        ),
        "shortcuts": discord.Embed(
            title="⚡ الاختصارات",
            description=(
                "`/shortcut` — إضافة/تعديل اختصار\n"
                "`/shortcuts` — عرض الاختصارات\n"
                "`/remove_shortcut` — حذف اختصار\n\n"
                "الأوامر المتاحة:\n"
                "`clear` `warn` `unwarn` `warnings`\n"
                "`timeout` `untimeout` `silence` `unsilence`\n"
                "`kick` `ban` `unban` `lock` `unlock`\n"
                "`rank` `addrole` `removerole` `resetxp`"
            ), color=discord.Color.purple()
        ),
    }
    return embeds.get(category, discord.Embed(
        title="🤖 DFG Help",
        description="اختر القسم الذي تريد معرفة أوامره من القائمة بالأسفل.",
        color=discord.Color.blurple()
    ))


class HelpCategorySelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="الحماية", value="security", emoji="🛡️"),
            discord.SelectOption(label="XP", value="xp", emoji="✨"),
            discord.SelectOption(label="التكت", value="tickets", emoji="🎫"),
            discord.SelectOption(label="التقديم", value="applications", emoji="📝"),
            discord.SelectOption(label="الإدارة", value="moderation", emoji="🔨"),
            discord.SelectOption(label="النظام", value="system", emoji="⚙️"),
            discord.SelectOption(label="الاختصارات", value="shortcuts", emoji="⚡"),
        ]
        super().__init__(
            placeholder="📂 اختر قسم الأوامر...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="dfg_help_category"
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.edit_message(embed=help_embed_for(self.values[0]), view=self.view)


class HelpView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(HelpCategorySelect())


@bot.tree.command(name="help", description="عرض أوامر DFG حسب القسم")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🤖 DFG Help",
        description="نظام البوت مرتب على أقسام. اختر القسم من القائمة بالأسفل.",
        color=discord.Color.blurple()
    )
    await safe_respond(interaction, embed=embed, view=HelpView(), ephemeral=False)


# ============================================================
# 27) أخطاء أوامر Slash
# ============================================================
# ============================================================
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error):
    original = getattr(error, "original", error)

    if isinstance(original, app_commands.CheckFailure):
        return await safe_respond(interaction, "❌ لا تملك الصلاحية لتنفيذ هذا الأمر.", ephemeral=True)

    if isinstance(original, discord.Forbidden):
        return await safe_respond(interaction, "❌ البوت لا يملك الصلاحية المطلوبة.", ephemeral=True)

    if isinstance(original, discord.HTTPException):
        return await safe_respond(interaction, "❌ حدث خطأ من Discord أثناء تنفيذ الأمر.", ephemeral=True)

    print("[APP COMMAND ERROR]", repr(original))
    await safe_respond(interaction, "❌ حدث خطأ غير متوقع. راجع كونسول الاستضافة.", ephemeral=True)


# ============================================================
# 28) جاهزية البوت
# ============================================================
@bot.event
async def on_ready():
    print("=" * 50)
    print(f"DFG Online: {bot.user} | ID: {bot.user.id}")
    print(f"Servers: {len(bot.guilds)}")
    try:
        # مزامنة واحدة لكل دورة تشغيل؛ هذا يعالج حالات اختفاء الأوامر بعد إعادة الاتصال.
        if not getattr(bot, "_commands_synced", False):
            synced = await bot.tree.sync()
            bot._commands_synced = True
            print(f"Slash commands synced: {len(synced)}")
    except Exception as e:
        print("[READY SYNC ERROR]", repr(e))
    try:
        # تجهيز كاش الدعوات من البداية. يحتاج البوت Manage Server.
        for guild in bot.guilds:
            try:
                invs = await guild.invites()
                invite_cache[guild.id] = {inv.code:{"uses":inv.uses or 0,"inviter_id":inv.inviter.id if inv.inviter else None} for inv in invs}
            except (discord.Forbidden, discord.HTTPException):
                pass
    except Exception as e:
        print("[INVITE CACHE ERROR]", repr(e))

    print("=" * 50)
    if not voice_xp_loop.is_running():
        voice_xp_loop.start()


# ============================================================
# 29) التشغيل
# ============================================================
if __name__ == "__main__":
    if BOT_TOKEN == "ضع_توكن_البوت_الخاص_بك_هنا":
        print("[ERROR] ضع توكن البوت داخل BOT_TOKEN أولاً.")
    else:
        bot.run(BOT_TOKEN)
