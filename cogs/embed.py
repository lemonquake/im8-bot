"""
IM8 Bot — Embed Editor Cog
Full-featured Embed Editor Hub with 10 major capabilities:
  1. Multi-Channel Posting (ChannelSelect)
  2. Author + Avatar Modal
  3. Image & Thumbnail Modal
  4. Embed Fields (Add/Remove)
  5. Footer + Timestamp
  6. Color Preset Picker
  7. Message Content (Ping Text)
  8. JSON Import/Export
  9. Embed Templates
 10. Rich Hub Status Bar
"""

import json
import discord
from discord import app_commands
from discord.ext import commands
import logging

from core.embed_script import EmbedScript

logger = logging.getLogger("im8bot.cogs.embed")


# ═══════════════════════════════════════════════════════════
#  TEMPLATES — Pre-built embed configurations
# ═══════════════════════════════════════════════════════════

EMBED_TEMPLATES = {
    "announcement": {
        "title": "📢  Announcement",
        "description": "Write your announcement details here.\n\nUse this space for the full message body.",
        "color": 0x00C9A7,
        "footer_text": "IM8 Health • Official Announcement",
        "use_timestamp": True,
    },
    "maintenance": {
        "title": "🔧  Scheduled Maintenance",
        "description": (
            "**Systems Affected:** All services\n"
            "**Window:** TBD\n"
            "**Expected Duration:** ~30 minutes\n\n"
            "We will be performing maintenance to improve performance and stability."
        ),
        "color": 0xFFA726,
        "footer_text": "IM8 Health • Infrastructure",
        "use_timestamp": True,
    },
    "event": {
        "title": "🎉  Community Event",
        "description": (
            "**📅 Date:** TBD\n"
            "**🕐 Time:** TBD\n"
            "**📍 Location:** TBD\n\n"
            "Join us for an exciting community event! More details coming soon."
        ),
        "color": 0x9C27B0,
        "footer_text": "IM8 Health • Events",
        "use_timestamp": True,
    },
    "welcome": {
        "title": "👋  Welcome to IM8 Health!",
        "description": (
            "We're glad to have you here. Please take a moment to:\n\n"
            "• Read the server rules\n"
            "• Introduce yourself\n"
            "• Pick your roles\n\n"
            "If you need any help, don't hesitate to ask!"
        ),
        "color": 0x00D26A,
        "footer_text": "IM8 Health • Welcome",
    },
    "rules": {
        "title": "📜  Server Rules",
        "description": (
            "Please follow these rules to keep our community healthy:\n\n"
            "**1.** Be respectful to all members\n"
            "**2.** No spam or self-promotion\n"
            "**3.** Keep discussions on-topic\n"
            "**4.** Follow Discord's Terms of Service\n"
            "**5.** Listen to staff instructions"
        ),
        "color": 0xFF6B6B,
        "footer_text": "IM8 Health • Community Guidelines",
    },
}


# ═══════════════════════════════════════════════════════════
#  COLOR PRESETS
# ═══════════════════════════════════════════════════════════

COLOR_PRESETS = [
    ("🩵 Brand Teal", 0x00C9A7),
    ("💚 Success Green", 0x00D26A),
    ("❤️ Error Red", 0xFF6B6B),
    ("🧡 Warning Amber", 0xFFA726),
    ("💙 Info Blue", 0x29B6F6),
    ("🤍 Pure White", 0xFFFFFF),
    ("🖤 Discord Dark", 0x2B2D31),
    ("💜 Royal Purple", 0x9C27B0),
]


# ═══════════════════════════════════════════════════════════
#  MODALS
# ═══════════════════════════════════════════════════════════

class ContentModal(discord.ui.Modal, title="✏️ Edit Content"):
    """Title + Description editor."""

    def __init__(self, script: EmbedScript, editor_view: discord.ui.View, index: int = 0):
        super().__init__()
        self.script = script
        self.editor_view = editor_view
        self.index = index
        
        state = self.script.embeds[index]

        self.emb_title = discord.ui.TextInput(
            label="Title",
            style=discord.TextStyle.short,
            placeholder="Embed Title",
            default=state.get("title"),
            required=False,
            max_length=256,
        )
        self.emb_desc = discord.ui.TextInput(
            label="Description",
            style=discord.TextStyle.paragraph,
            placeholder="Main embed body text… (supports Markdown)",
            default=state.get("description"),
            required=False,
            max_length=4000,
        )
        self.add_item(self.emb_title)
        self.add_item(self.emb_desc)

    async def on_submit(self, interaction: discord.Interaction):
        state = self.script.embeds[self.index]
        state["title"] = self.emb_title.value.strip() or None
        state["description"] = self.emb_desc.value.strip() or None
        
        # Sub-views refresh slightly differently or just call parent hub refresh
        if hasattr(self.editor_view, "refresh"):
            await self.editor_view.refresh(interaction)
        else:
            await interaction.response.send_message("✅ Content updated.", ephemeral=True)


class AuthorSelectionView(discord.ui.View):
    """Sub-view for quick author settings: Me, Bot, or Custom."""

    def __init__(self, script: EmbedScript, editor_view: "EmbedEditorView"):
        super().__init__(timeout=120)
        self.script = script
        self.editor_view = editor_view

    @discord.ui.button(label="Set to Me", emoji="👤", style=discord.ButtonStyle.primary)
    async def btn_me(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.script.author_name = interaction.user.display_name
        self.script.author_icon = interaction.user.display_avatar.url
        await self.editor_view.refresh(interaction)

    @discord.ui.button(label="Set to Bot", emoji="🤖", style=discord.ButtonStyle.primary)
    async def btn_bot(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.script.author_name = interaction.client.user.name
        self.script.author_icon = interaction.client.user.display_avatar.url
        await self.editor_view.refresh(interaction)

    @discord.ui.button(label="Custom Edit", emoji="✏️", style=discord.ButtonStyle.secondary)
    async def btn_custom(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AuthorModal(self.script, self.editor_view))

    @discord.ui.button(label="Clear Author", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def btn_clear(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.script.author_name = None
        self.script.author_icon = None
        self.script.author_url = None
        await self.editor_view.refresh(interaction)

    @discord.ui.button(label="Back", emoji="🔙", style=discord.ButtonStyle.secondary)
    async def btn_back(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.editor_view.refresh(interaction)


class AuthorModal(discord.ui.Modal, title="👤 Edit Author Details"):
    """Author name, avatar, and link."""

    def __init__(self, script: EmbedScript, editor_view: discord.ui.View, index: int = 0):
        super().__init__()
        self.script = script
        self.editor_view = editor_view
        self.index = index
        
        state = self.script.embeds[index]

        self.author_name = discord.ui.TextInput(
            label="Author Name",
            style=discord.TextStyle.short,
            placeholder="e.g. IM8 Health Team",
            default=state.get("author_name"),
            required=False,
            max_length=256,
        )
        self.author_icon = discord.ui.TextInput(
            label="Author Avatar URL",
            style=discord.TextStyle.short,
            placeholder="https://i.imgur.com/...",
            default=state.get("author_icon"),
            required=False,
        )
        self.author_url = discord.ui.TextInput(
            label="Author Link URL (clickable name)",
            style=discord.TextStyle.short,
            placeholder="https://example.com",
            default=state.get("author_url"),
            required=False,
        )
        self.add_item(self.author_name)
        self.add_item(self.author_icon)
        self.add_item(self.author_url)

    async def on_submit(self, interaction: discord.Interaction):
        state = self.script.embeds[self.index]
        state["author_name"] = self.author_name.value.strip() or None
        state["author_icon"] = self.author_icon.value.strip() or None
        state["author_url"] = self.author_url.value.strip() or None
        
        if hasattr(self.editor_view, "refresh"):
            await self.editor_view.refresh(interaction)
        else:
            await interaction.response.send_message("✅ Author updated.", ephemeral=True)


class ImageModal(discord.ui.Modal, title="🖼️ Edit Images"):
    """Image URL + Thumbnail URL editor."""

    def __init__(self, script: EmbedScript, editor_view: discord.ui.View, index: int = 0):
        super().__init__()
        self.script = script
        self.editor_view = editor_view
        self.index = index
        
        state = self.script.embeds[index]

        self.image_url = discord.ui.TextInput(
            label="Large Image URL",
            style=discord.TextStyle.short,
            placeholder="https://... (displayed at bottom of embed)",
            default=state.get("image_url"),
            required=False,
        )
        self.thumb_url = discord.ui.TextInput(
            label="Thumbnail URL",
            style=discord.TextStyle.short,
            placeholder="https://... (small image in top-right)",
            default=state.get("thumbnail_url"),
            required=False,
        )
        self.add_item(self.image_url)
        self.add_item(self.thumb_url)

    async def on_submit(self, interaction: discord.Interaction):
        state = self.script.embeds[self.index]
        state["image_url"] = self.image_url.value.strip() or None
        state["thumbnail_url"] = self.thumb_url.value.strip() or None
        
        if hasattr(self.editor_view, "refresh"):
            await self.editor_view.refresh(interaction)
        else:
            await interaction.response.send_message("✅ Images updated.", ephemeral=True)


class FooterModal(discord.ui.Modal, title="📎 Edit Footer"):
    """Footer text, icon, and timestamp toggle."""

    def __init__(self, script: EmbedScript, editor_view: discord.ui.View, index: int = 0):
        super().__init__()
        self.script = script
        self.editor_view = editor_view
        self.index = index
        
        state = self.script.embeds[index]

        self.footer_text = discord.ui.TextInput(
            label="Footer Text",
            style=discord.TextStyle.short,
            placeholder="e.g. IM8 Health • Official",
            default=state.get("footer_text"),
            required=False,
            max_length=2048,
        )
        self.footer_icon = discord.ui.TextInput(
            label="Footer Icon URL",
            style=discord.TextStyle.short,
            placeholder="https://... (small icon next to footer text)",
            default=state.get("footer_icon"),
            required=False,
        )
        self.timestamp_toggle = discord.ui.TextInput(
            label="Show Timestamp? (yes / no)",
            style=discord.TextStyle.short,
            placeholder="yes or no",
            default="yes" if state.get("use_timestamp") else "no",
            required=False,
            max_length=3,
        )
        self.add_item(self.footer_text)
        self.add_item(self.footer_icon)
        self.add_item(self.timestamp_toggle)

    async def on_submit(self, interaction: discord.Interaction):
        state = self.script.embeds[self.index]
        state["footer_text"] = self.footer_text.value.strip() or None
        state["footer_icon"] = self.footer_icon.value.strip() or None
        ts = self.timestamp_toggle.value.strip().lower()
        state["use_timestamp"] = ts in ("yes", "y", "true", "1")
        
        if hasattr(self.editor_view, "refresh"):
            await self.editor_view.refresh(interaction)
        else:
            await interaction.response.send_message("✅ Footer updated.", ephemeral=True)


class PingContentModal(discord.ui.Modal, title="💬 Edit Ping / Message Text"):
    """Message content that appears outside the embed (for @pings)."""

    def __init__(self, script: EmbedScript, editor_view: "EmbedEditorView"):
        super().__init__()
        self.script = script
        self.editor_view = editor_view

        self.msg_content = discord.ui.TextInput(
            label="Message Content (outside embed)",
            style=discord.TextStyle.paragraph,
            placeholder="@everyone Check out this announcement!\n(Appears above the embed)",
            default=self.script.content,
            required=False,
            max_length=2000,
        )
        self.add_item(self.msg_content)

    async def on_submit(self, interaction: discord.Interaction):
        self.script.content = self.msg_content.value.strip() or None
        await self.editor_view.refresh(interaction)
class PingSelectionView(discord.ui.View):
    """Sub-view for quick mention toggles."""

    def __init__(self, script: EmbedScript, editor_view: "EmbedEditorView"):
        super().__init__(timeout=120)
        self.script = script
        self.editor_view = editor_view

    def _update_ping(self, ping: str | None):
        content = self.script.content or ""
        # Remove existing common pings
        for p in ["@everyone", "@here"]:
            content = content.replace(p, "").strip()
        
        if ping:
            content = f"{ping} {content}".strip()
        
        self.script.content = content if content else None

    @discord.ui.button(label="@everyone", style=discord.ButtonStyle.primary)
    async def btn_everyone(self, interaction: discord.Interaction, button: discord.ui.Button):
        self._update_ping("@everyone")
        await self.editor_view.refresh(interaction)

    @discord.ui.button(label="@here", style=discord.ButtonStyle.primary)
    async def btn_here(self, interaction: discord.Interaction, button: discord.ui.Button):
        self._update_ping("@here")
        await self.editor_view.refresh(interaction)

    @discord.ui.button(label="None", style=discord.ButtonStyle.secondary)
    async def btn_none(self, interaction: discord.Interaction, button: discord.ui.Button):
        self._update_ping(None)
        await self.editor_view.refresh(interaction)

    @discord.ui.button(label="Custom Text", emoji="✏️", style=discord.ButtonStyle.secondary)
    async def btn_custom(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(PingContentModal(self.script, self.editor_view))

    @discord.ui.button(label="Back", emoji="🔙", style=discord.ButtonStyle.secondary)
    async def btn_back(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.editor_view.refresh(interaction)

class ButtonModal(discord.ui.Modal, title="🔗 Add Link Button"):
    """Adds a URL link button to the embed message."""

    def __init__(self, script: EmbedScript, editor_view: "EmbedEditorView"):
        super().__init__()
        self.script = script
        self.editor_view = editor_view

        self.btn_label = discord.ui.TextInput(
            label="Button Label",
            style=discord.TextStyle.short,
            placeholder="Visit Website",
            required=True,
            max_length=80,
        )
        self.btn_url = discord.ui.TextInput(
            label="Button URL",
            style=discord.TextStyle.short,
            placeholder="https://...",
            required=True,
        )
        self.add_item(self.btn_label)
        self.add_item(self.btn_url)

    async def on_submit(self, interaction: discord.Interaction):
        if len(self.script.buttons) >= 5:
            await interaction.response.send_message(
                "❌ Maximum of **5** buttons allowed.", ephemeral=True
            )
            return
        self.script.buttons.append({
            "type": "link",
            "label": self.btn_label.value.strip(),
            "url": self.btn_url.value.strip(),
        })
        await self.editor_view.refresh(interaction)


class RoleButtonModal(discord.ui.Modal, title="🔘 Add Role Button"):
    """Adds a toggleable role button to the embed."""

    def __init__(self, script: EmbedScript, editor_view: "EmbedEditorView"):
        super().__init__()
        self.script = script
        self.editor_view = editor_view

        self.btn_label = discord.ui.TextInput(
            label="Button Label",
            style=discord.TextStyle.short,
            placeholder="Get Notifications",
            required=True,
            max_length=80,
        )
        self.role_id = discord.ui.TextInput(
            label="Role ID",
            style=discord.TextStyle.short,
            placeholder="Paste role ID here...",
            required=True,
        )
        self.add_item(self.btn_label)
        self.add_item(self.role_id)

    async def on_submit(self, interaction: discord.Interaction):
        if len(self.script.buttons) >= 5:
            await interaction.response.send_message(
                "❌ Maximum of **5** buttons allowed.", ephemeral=True
            )
            return

        try:
            rid = int(self.role_id.value.strip())
            role = interaction.guild.get_role(rid)
            if not role:
                raise ValueError("Role not found")
        except ValueError:
            await interaction.response.send_message("❌ Invalid Role ID. Please copy the ID from Server Settings.", ephemeral=True)
            return

        self.script.buttons.append({
            "type": "role",
            "label": self.btn_label.value.strip(),
            "role_id": rid,
        })
        await self.editor_view.refresh(interaction)


class SyntaxGuideModal(discord.ui.Modal, title="📖 Syntax & Placeholders"):
    """Displays help text for dynamic variables."""

    def __init__(self):
        super().__init__()
        # We use a paragraph TextInput just for display (read-only-ish)
        # or we could just use a Send Message approach, but Modal is cleaner for "Reading".
        # Actually, a Modal can't be purely read-only without inputs easily.
        # I'll use a normal View for help instead.
        pass

    @staticmethod
    def get_help_text():
        return (
            "### ✨ Dynamic Placeholders\n"
            "Use these tags in any text field (Title, Description, Fields):\n\n"
            "• `{user_mention}` — Mentions the person seeing the message.\n"
            "• `{user_name}` — Displays their display name.\n"
            "• `{server_name}` — The name of this server.\n"
            "• `{member_count}` — Total member count.\n"
            "• `{date_now}` — Current date (YYYY-MM-DD).\n\n"
            "**Mentions Guide:**\n"
            "• **The Viewer**: Use `{user_mention}` to ping the person seeing it.\n"
            "• **Specific User**: Use `<@USER_ID>` (e.g., `<@1234567890>`).\n"
            "• **Multiple Users**: Just repeat the tags or IDs.\n"
            "• **Everyone/Here**: Use the **Pink / Context** button in the Hub."
        )


class FieldModal(discord.ui.Modal, title="📋 Add Embed Field"):
    """Adds a field (name + value + inline) to the embed."""

    def __init__(self, script: EmbedScript, editor_view: discord.ui.View, index: int = 0):
        super().__init__()
        self.script = script
        self.editor_view = editor_view
        self.index = index

        self.field_name = discord.ui.TextInput(
            label="Field Name",
            style=discord.TextStyle.short,
            placeholder="e.g. Duration",
            required=True,
            max_length=256,
        )
        self.field_value = discord.ui.TextInput(
            label="Field Value",
            style=discord.TextStyle.paragraph,
            placeholder="e.g. 2 hours",
            required=True,
            max_length=1024,
        )
        self.field_inline = discord.ui.TextInput(
            label="Inline? (yes / no)",
            style=discord.TextStyle.short,
            placeholder="yes or no (inline fields sit side-by-side)",
            default="no",
            required=False,
            max_length=3,
        )
        self.add_item(self.field_name)
        self.add_item(self.field_value)
        self.add_item(self.field_inline)

    async def on_submit(self, interaction: discord.Interaction):
        state = self.script.embeds[self.index]
        if len(state.get("fields", [])) >= 25:
            await interaction.response.send_message(
                "❌ Maximum of **25** fields per embed.", ephemeral=True
            )
            return
        inline = self.field_inline.value.strip().lower() in ("yes", "y", "true", "1")
        state["fields"].append({
            "name": self.field_name.value.strip(),
            "value": self.field_value.value.strip(),
            "inline": inline,
        })
        
        if hasattr(self.editor_view, "refresh"):
            await self.editor_view.refresh(interaction)
        else:
            await interaction.response.send_message("✅ Field added.", ephemeral=True)

class TemplateSaveModal(discord.ui.Modal, title="💾 Save as Template"):
    """Names and saves the current EmbedScript to the database."""

    def __init__(self, script: EmbedScript):
        super().__init__()
        self.script = script

        self.tmpl_name = discord.ui.TextInput(
            label="Template Name",
            style=discord.TextStyle.short,
            placeholder="e.g. Weekly Update v2",
            required=True,
            max_length=50,
        )
        self.add_item(self.tmpl_name)

    async def on_submit(self, interaction: discord.Interaction):
        name = self.tmpl_name.value.strip()
        payload = self.script.to_json()
        
        db = interaction.client.database
        await db.execute(
            "INSERT INTO embed_templates (name, guild_id, created_by, payload) VALUES (?, ?, ?, ?)",
            (name, interaction.guild_id, interaction.user.id, payload)
        )
        
        await interaction.response.send_message(f"✅ Template **{name}** has been saved to the server vault!", ephemeral=True)


class CustomHexModal(discord.ui.Modal, title="🎨 Custom Hex Color"):
    """Fallback modal for entering a custom hex color."""

    def __init__(self, script: EmbedScript, editor_view: discord.ui.View, index: int = 0):
        super().__init__()
        self.script = script
        self.editor_view = editor_view
        self.index = index
        
        current_color = self.script.embeds[index].get("color")

        self.hex_input = discord.ui.TextInput(
            label="Hex Color Code",
            style=discord.TextStyle.short,
            placeholder="e.g. FF5733 or #FF5733",
            default=hex(current_color).replace("0x", "") if current_color else "",
            required=True,
            max_length=7,
        )
        self.add_item(self.hex_input)

    async def on_submit(self, interaction: discord.Interaction):
        raw = self.hex_input.value.strip().replace("#", "")
        try:
            self.script.embeds[self.index]["color"] = int(raw, 16)
        except ValueError:
            await interaction.response.send_message(
                "❌ Invalid hex color. Use format like `FF5733`.", ephemeral=True
            )
            return
        
        if hasattr(self.editor_view, "refresh"):
            await self.editor_view.refresh(interaction)
        else:
            await interaction.response.send_message("✅ Color updated.", ephemeral=True)


class JsonImportModal(discord.ui.Modal, title="📥 Import JSON"):
    """Imports embed state from a JSON string."""

    def __init__(self, script: EmbedScript, editor_view: "EmbedEditorView"):
        super().__init__()
        self.script = script
        self.editor_view = editor_view

        self.json_input = discord.ui.TextInput(
            label="Paste JSON here",
            style=discord.TextStyle.paragraph,
            placeholder='{"title": "My Embed", "description": "Hello!", ...}',
            required=True,
            max_length=4000,
        )
        self.add_item(self.json_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            data = json.loads(self.json_input.value)
        except json.JSONDecodeError as e:
            await interaction.response.send_message(
                f"❌ Invalid JSON: `{e}`", ephemeral=True
            )
            return

        # Hydrate onto the existing script (preserve user_id, channels, editing_message)
        imported = EmbedScript.from_dict(data, self.script.user_id)
        self.script.content = imported.content
        self.script.title = imported.title
        self.script.description = imported.description
        self.script.color = imported.color
        self.script.image_url = imported.image_url
        self.script.thumbnail_url = imported.thumbnail_url
        self.script.author_name = imported.author_name
        self.script.author_icon = imported.author_icon
        self.script.author_url = imported.author_url
        self.script.footer_text = imported.footer_text
        self.script.footer_icon = imported.footer_icon
        self.script.use_timestamp = imported.use_timestamp
        self.script.fields = imported.fields
        self.script.buttons = imported.buttons

        await self.editor_view.refresh(interaction)
class MessageImportModal(discord.ui.Modal, title="📥 Load from ID / URL"):
    """Fetches an existing message and hydrates the editor."""

    def __init__(self, script: EmbedScript, editor_view: "EmbedEditorView"):
        super().__init__()
        self.script = script
        self.editor_view = editor_view

        self.msg_ref = discord.ui.TextInput(
            label="Message ID or Link",
            placeholder="Paste message ID or URL here...",
            required=True,
        )
        self.add_item(self.msg_ref)

    async def on_submit(self, interaction: discord.Interaction):
        ref = self.msg_ref.value.strip()
        msg_id = None

        if "discord.com/channels/" in ref:
            try:
                msg_id = int(ref.split("/")[-1])
            except (ValueError, IndexError):
                pass
        else:
            try:
                msg_id = int(ref)
            except ValueError:
                pass

        if not msg_id:
            await interaction.response.send_message("❌ Invalid message ID or Link.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        try:
            # Try to find message in current channel, then everywhere the bot can see
            message = None
            for channel in interaction.guild.text_channels:
                try:
                    message = await channel.fetch_message(msg_id)
                    if message: break
                except discord.NotFound:
                    continue
            
            if not message:
                await interaction.followup.send("❌ Message not found in this server.", ephemeral=True)
                return

            imported = EmbedScript.from_message(message, self.script.user_id)
            # Hydrate
            self.script.content = imported.content
            self.script.embeds = imported.embeds
            self.script.buttons = imported.buttons
            
            await self.editor_view.refresh(interaction)
            await interaction.followup.send("✅ Embed loaded successfully!", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Error loading message: `{e}`", ephemeral=True)


class UrlImportModal(discord.ui.Modal, title="🌐 Import from URL"):
    """Fetches Embed JSON from a URL (e.g. GitHub/Pastebin)."""

    def __init__(self, script: EmbedScript, editor_view: "EmbedEditorView"):
        super().__init__()
        self.script = script
        self.editor_view = editor_view

        self.url_input = discord.ui.TextInput(
            label="JSON URL",
            placeholder="https://raw.githubusercontent.com/.../embed.json",
            required=True,
        )
        self.add_item(self.url_input)

    async def on_submit(self, interaction: discord.Interaction):
        url = self.url_input.value.strip()
        await interaction.response.defer(ephemeral=True)
        
        import aiohttp
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        await interaction.followup.send(f"❌ Failed to fetch URL (Status: {resp.status})", ephemeral=True)
                        return
                    data = await resp.json()
            
            imported = EmbedScript.from_dict(data, self.script.user_id)
            self.script.content = imported.content
            self.script.embeds = imported.embeds
            self.script.buttons = imported.buttons
            
            await self.editor_view.refresh(interaction)
            await interaction.followup.send("✅ Embed imported from URL!", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Error during import: `{e}`", ephemeral=True)

# ═══════════════════════════════════════════════════════════
#  SUB-VIEWS (Selects & Managers)
# ═══════════════════════════════════════════════════════════

class ColorPresetSelect(discord.ui.Select):
    """Dropdown for quick color selection."""

    def __init__(self, script: EmbedScript, editor_view: discord.ui.View, index: int = 0):
        self.script = script
        self.editor_view = editor_view
        self.index = index

        options = []
        for label, value in COLOR_PRESETS:
            options.append(discord.SelectOption(
                label=label,
                value=str(value),
            ))
        options.append(discord.SelectOption(
            label="✨ Custom Hex…",
            value="custom",
            description="Enter your own hex color code",
        ))

        super().__init__(
            placeholder="Pick a color…",
            options=options,
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        chosen = self.values[0]
        if chosen == "custom":
            await interaction.response.send_modal(CustomHexModal(self.script, self.editor_view, self.index))
        else:
            self.script.embeds[self.index]["color"] = int(chosen)
            if hasattr(self.editor_view, "refresh"):
                await self.editor_view.refresh(interaction)
            else:
                await interaction.response.send_message("✅ Color updated.", ephemeral=True)


class ColorSelectView(discord.ui.View):
    """Wrapper view containing the color preset selector."""

    def __init__(self, script: EmbedScript, editor_view: discord.ui.View, index: int = 0):
        super().__init__(timeout=120)
        self.script = script
        self.editor_view = editor_view
        self.index = index
        self.add_item(ColorPresetSelect(script, editor_view, index))

    @discord.ui.button(label="Back", emoji="🔙", style=discord.ButtonStyle.secondary, row=1)
    async def btn_back(self, interaction: discord.Interaction, button: discord.ui.Button):
        if hasattr(self.editor_view, "refresh"):
            await self.editor_view.refresh(interaction)
        else:
            await interaction.response.send_message("🔙 Returning...", ephemeral=True)


class TemplateSelectDropdown(discord.ui.Select):
    """Dropdown for selecting pre-built and custom embed templates."""

    def __init__(self, script: EmbedScript, editor_view: discord.ui.View, custom_templates: list = None):
        self.script = script
        self.editor_view = editor_view

        options = [
            discord.SelectOption(label="📢 Announcement", value="announcement", description="Official announcement template"),
            discord.SelectOption(label="🔧 Maintenance", value="maintenance", description="Scheduled downtime notice"),
            discord.SelectOption(label="🎉 Community Event", value="event", description="Event promotion template"),
            discord.SelectOption(label="👋 Welcome", value="welcome", description="New member welcome message"),
            discord.SelectOption(label="📜 Server Rules", value="rules", description="Community guidelines template"),
        ]

        if custom_templates:
            options.append(discord.SelectOption(label="──────────", value="divider", description="Server Vault Templates", default=False))
            for t in custom_templates:
                options.append(discord.SelectOption(
                    label=t['name'],
                    value=f"db_{t['id']}",
                    description=f"Saved by User ID: {t.get('created_by')}",
                    emoji="💾"
                ))

        super().__init__(
            placeholder="Choose a template…",
            options=options,
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "divider":
            await interaction.response.send_message("Please select a real template!", ephemeral=True)
            return

        template_data = None
        if self.values[0].startswith("db_"):
            task_id = int(self.values[0].split("_")[-1])
            db = interaction.client.database
            row = await db.fetch_one("SELECT payload FROM embed_templates WHERE id = ?", (task_id,))
            if row:
                template_data = json.loads(row['payload'])
        else:
            template_data = EMBED_TEMPLATES.get(self.values[0])

        if not template_data:
            await interaction.response.send_message("❌ Template not found.", ephemeral=True)
            return

        imported = EmbedScript.from_dict(template_data, self.script.user_id)
        # Hydrate all embeds and global settings
        self.script.content = imported.content
        self.script.embeds = imported.embeds
        self.script.buttons = imported.buttons

        await self.editor_view.refresh(interaction)


class TemplateSelectView(discord.ui.View):
    """Wrapper view for the template selector."""

    def __init__(self, script: EmbedScript, editor_view: discord.ui.View, custom_templates: list = None):
        super().__init__(timeout=120)
        self.editor_view = editor_view
        self.add_item(TemplateSelectDropdown(script, editor_view, custom_templates))

    @discord.ui.button(label="Back to Hub", emoji="🔙", style=discord.ButtonStyle.secondary, row=1)
    async def btn_back(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.editor_view.refresh(interaction)


class FieldManagerView(discord.ui.View):
    """Sub-view for managing embed fields for a specific embed index."""

    def __init__(self, script: EmbedScript, editor_view: discord.ui.View, index: int = 0):
        super().__init__(timeout=120)
        self.script = script
        self.editor_view = editor_view
        self.index = index

        state = self.script.embeds[index]

        # Build a remove-select if fields exist
        if state.get("fields"):
            options = []
            for i, field in enumerate(state["fields"]):
                name_preview = field["name"][:50]
                inline_tag = " [inline]" if field.get("inline") else ""
                options.append(discord.SelectOption(
                    label=f"#{i + 1}: {name_preview}{inline_tag}",
                    value=str(i),
                    description=field["value"][:100],
                ))
            self.remove_select = discord.ui.Select(
                placeholder="Select a field to remove…",
                options=options,
                min_values=1,
                max_values=1,
                row=0,
            )
            self.remove_select.callback = self._remove_callback
            self.add_item(self.remove_select)

    async def _remove_callback(self, interaction: discord.Interaction):
        state = self.script.embeds[self.index]
        idx = int(self.remove_select.values[0])
        if 0 <= idx < len(state.get("fields", [])):
            removed = state["fields"].pop(idx)
            await interaction.response.send_message(
                f"🗑️ Removed field **{removed['name']}**.", ephemeral=True
            )
            # Refresh view (it's either a sub-view or the hub)
            if hasattr(self.editor_view, "refresh"):
                await self.editor_view.refresh(interaction)
        else:
            await interaction.response.send_message("❌ Invalid field index.", ephemeral=True)

    @discord.ui.button(label="Add Field", emoji="➕", style=discord.ButtonStyle.success, row=1)
    async def btn_add_field(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(FieldModal(self.script, self.editor_view, self.index))

    @discord.ui.button(label="Clear All Fields", emoji="🗑️", style=discord.ButtonStyle.danger, row=1)
    async def btn_clear_fields(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.script.embeds[self.index]["fields"].clear()
        if hasattr(self.editor_view, "refresh"):
            await self.editor_view.refresh(interaction)
        else:
            await interaction.response.send_message("🗑️ Fields cleared.", ephemeral=True)

    @discord.ui.button(label="Back", emoji="🔙", style=discord.ButtonStyle.secondary, row=1)
    async def btn_back(self, interaction: discord.Interaction, button: discord.ui.Button):
        if hasattr(self.editor_view, "refresh"):
            await self.editor_view.refresh(interaction)
        else:
            await interaction.response.send_message("🔙 Returning...", ephemeral=True)


class ChannelSelectView(discord.ui.View):
    """Sub-view with a native ChannelSelect for picking target channels."""

    def __init__(self, script: EmbedScript, editor_view: "EmbedEditorView"):
        super().__init__(timeout=120)
        self.script = script
        self.editor_view = editor_view
        self._selected_channels: list[discord.TextChannel] = list(script.channels)

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text, discord.ChannelType.news],
        placeholder="Select target channels…",
        min_values=1,
        max_values=25,
        row=0,
    )
    async def channel_select(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        # Additive logic: only add if not already in the list
        count_added = 0
        for ch in select.values:
            resolved = interaction.guild.get_channel(ch.id)
            if resolved and isinstance(resolved, (discord.TextChannel, discord.Thread)):
                if resolved not in self._selected_channels:
                    self._selected_channels.append(resolved)
                    count_added += 1

        ch_list = ", ".join(f"#{c.name}" for c in self._selected_channels[-10:])
        if len(self._selected_channels) > 10:
            ch_list = f"...{ch_list}"

        await interaction.response.send_message(
            f"➕ Added **{count_added}** new channel(s).\n"
            f"📍 **Current Total:** {len(self._selected_channels)} channel(s)\n"
            f"📜 **Selection:** {ch_list}\n\n"
            "*Use the dropdown again to add more, or press Save.*",
            ephemeral=True,
        )

    @discord.ui.button(label="Confirm & Send Now", emoji="📢", style=discord.ButtonStyle.success, row=1)
    async def btn_confirm_send(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._selected_channels:
            await interaction.response.send_message(
                "❌ No channels selected. Pick at least one channel first.", ephemeral=True
            )
            return

        self.script.channels = self._selected_channels
        embeds = self.script.build_embeds()

        # Build button view if any
        btn_view = discord.ui.View()
        if self.script.buttons:
            for btn in self.script.buttons:
                if btn.get("type") == "role":
                    btn_view.add_item(discord.ui.Button(
                        label=btn["label"], 
                        custom_id=f"im8_role_{btn['role_id']}", 
                        style=discord.ButtonStyle.primary
                    ))
                else:
                    btn_view.add_item(discord.ui.Button(
                        label=btn["label"], 
                        url=btn.get("url"), 
                        style=discord.ButtonStyle.link
                    ))

        sent_to = []
        failed = []
        for channel in self._selected_channels:
            try:
                await channel.send(
                    content=self.script.content,
                    embeds=embeds,
                    view=btn_view if self.script.buttons else None,
                )
                sent_to.append(f"#{channel.name}")
            except Exception as e:
                failed.append(f"#{channel.name}: {e}")

        # Build result message
        result_lines = [f"**✅ Sent to {len(sent_to)} channel(s):**"]
        result_lines.append(", ".join(sent_to))
        if failed:
            result_lines.append(f"\n**❌ Failed ({len(failed)}):**")
            result_lines.extend(failed)

        # Show preview of first embed in result
        main_preview = embeds[0] if embeds else None
        
        await interaction.response.edit_message(
            content="\n".join(result_lines),
            embed=main_preview,
            view=None,
        )

    @discord.ui.button(label="Save & Back", emoji="💾", style=discord.ButtonStyle.primary, row=1)
    async def btn_save_back(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.script.channels = self._selected_channels
        await self.editor_view.refresh(interaction)

    @discord.ui.button(label="Clear Channels", emoji="🗑️", style=discord.ButtonStyle.danger, row=1)
    async def btn_clear(self, interaction: discord.Interaction, button: discord.ui.Button):
        self._selected_channels = []
        await interaction.response.send_message("🗑️ Selection cleared.", ephemeral=True)

    @discord.ui.button(label="Cancel", emoji="❌", style=discord.ButtonStyle.secondary, row=1)
    async def btn_cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.editor_view.refresh(interaction)


# ═══════════════════════════════════════════════════════════
#  SCHEDULING & GLOBAL MEMORY
# ═══════════════════════════════════════════════════════════

class ScheduleView(discord.ui.View):
    """Sub-view for configuring a future broadcast time."""

    def __init__(self, script: EmbedScript, editor_view: "EmbedEditorView"):
        super().__init__(timeout=300)
        self.script = script
        self.editor_view = editor_view
        
        # State
        self.day_offset = 0 # 0=Today, 1=Tomorrow, etc.
        self.hour = 12
        self.minute = 0
        self.ampm = "PM"

    @discord.ui.select(
        placeholder="Pick a Day...",
        options=[
            discord.SelectOption(label="Today", value="0"),
            discord.SelectOption(label="Tomorrow", value="1"),
            discord.SelectOption(label="In 2 Days", value="2"),
            discord.SelectOption(label="In 3 Days", value="3"),
            discord.SelectOption(label="In 4 Days", value="4"),
        ],
        row=0
    )
    async def select_day(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.day_offset = int(select.values[0])
        await interaction.response.defer()

    @discord.ui.select(
        placeholder="Hour...",
        options=[discord.SelectOption(label=f"{h}", value=f"{h}") for h in range(1, 13)],
        row=1
    )
    async def select_hour(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.hour = int(select.values[0])
        await interaction.response.defer()

    @discord.ui.select(
        placeholder="Minute...",
        options=[
            discord.SelectOption(label="00", value="0"),
            discord.SelectOption(label="15", value="15"),
            discord.SelectOption(label="30", value="30"),
            discord.SelectOption(label="45", value="45"),
        ],
        row=2
    )
    async def select_minute(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.minute = int(select.values[0])
        await interaction.response.defer()

    @discord.ui.button(label="AM", style=discord.ButtonStyle.secondary, row=3)
    async def btn_am(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.ampm = "AM"
        await interaction.response.edit_message(content=self._build_prompt())

    @discord.ui.button(label="PM", style=discord.ButtonStyle.secondary, row=3)
    async def btn_pm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.ampm = "PM"
        await interaction.response.edit_message(content=self._build_prompt())

    @discord.ui.button(label="Confirm & Schedule", emoji="⏳", style=discord.ButtonStyle.success, row=4)
    async def btn_confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.script.channels:
            await interaction.response.send_message("❌ Pick at least one channel first!", ephemeral=True)
            return

        from datetime import datetime, timedelta
        now = datetime.now()
        run_date = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=self.day_offset)
        
        # Adjust hour for AM/PM
        h = self.hour
        if self.ampm == "PM" and h < 12: h += 12
        if self.ampm == "AM" and h == 12: h = 0
        
        run_date = run_date.replace(hour=h, minute=self.minute)

        if run_date <= now:
            await interaction.response.send_message("❌ Cannot schedule in the past!", ephemeral=True)
            return

        # SAVE TO DB & SCHEDULE
        cog = interaction.client.get_cog("EmbedEditor")
        await cog.schedule_task(
            guild_id=interaction.guild_id,
            channel_ids=[c.id for c in self.script.channels],
            payload=self.script.to_dict(),
            run_at=run_date,
            created_by=interaction.user.id
        )

        await interaction.response.edit_message(
            content=f"✅ **Scheduled!**\nYour embed will be sent to **{len(self.script.channels)}** channel(s) at `{run_date.strftime('%Y-%m-%d %I:%M %p')}`.",
            embed=None,
            view=None
        )

    @discord.ui.button(label="Back", emoji="🔙", style=discord.ButtonStyle.secondary, row=4)
    async def btn_back(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.editor_view.refresh(interaction)

    def _build_prompt(self):
        day_text = ["Today", "Tomorrow", "In 2 Days", "In 3 Days", "In 4 Days"][self.day_offset]
        return f"**⏳ Scheduling Suite**\nSelected: `{day_text} @ {self.hour:02}:{self.minute:02} {self.ampm}`"


class ManageScheduledView(discord.ui.View):
    """Global Memory: View and manage all pending tasks."""

    def __init__(self, tasks: list, bot: discord.Client, editor_view: "EmbedEditorView"):
        super().__init__(timeout=180)
        self.tasks = tasks
        self.bot = bot
        self.editor_view = editor_view

        if tasks:
            options = []
            for t in tasks[:25]:
                dt = t['run_at']
                options.append(discord.SelectOption(
                    label=f"ID #{t['id']} @ {dt}",
                    description=f"Targets: {len(json.loads(t['target_channels']))} channels",
                    value=str(t['id'])
                ))
            self.task_select = discord.ui.Select(placeholder="Select a task to cancel...", options=options)
            self.task_select.callback = self._cancel_callback
            self.add_item(self.task_select)

    async def _cancel_callback(self, interaction: discord.Interaction):
        task_id = self.task_select.values[0]
        cog = self.bot.get_cog("EmbedEditor")
        await cog.cancel_task(int(task_id))
        await interaction.response.send_message(f"🗑️ Task **#{task_id}** cancelled and removed from Global Memory.", ephemeral=True)
        await self.editor_view.refresh(interaction)

    @discord.ui.button(label="Back to Hub", emoji="🔙", style=discord.ButtonStyle.secondary)
    async def btn_back(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.editor_view.refresh(interaction)




# ═══════════════════════════════════════════════════════════
#  THE HUB VIEW — Main Editor Interface
# ═══════════════════════════════════════════════════════════

class EmbedCountView(discord.ui.View):
    """Sub-view for choosing the number of embeds (1-10)."""

    def __init__(self, script: EmbedScript, editor_view: "EmbedEditorView"):
        super().__init__(timeout=120)
        self.script = script
        self.editor_view = editor_view

    @discord.ui.select(
        placeholder="How many embeds? (Current: 1)",
        options=[discord.SelectOption(label=f"{i}", value=str(i), emoji="📑") for i in range(1, 11)],
        row=0
    )
    async def select_count(self, interaction: discord.Interaction, select: discord.ui.Select):
        count = int(select.values[0])
        self.script.set_embed_count(count)
        await self.editor_view.refresh(interaction)

    @discord.ui.button(label="Back", emoji="🔙", style=discord.ButtonStyle.secondary, row=1)
    async def btn_back(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.editor_view.refresh(interaction)

class SingleEmbedEditorView(discord.ui.View):
    """Sub-view to edit specific properties of a single embed stage."""

    def __init__(self, script: EmbedScript, editor_view: "EmbedEditorView", index: int):
        super().__init__(timeout=300)
        self.script = script
        self.editor_view = editor_view
        self.index = index

    @discord.ui.button(label="Content", emoji="📝", style=discord.ButtonStyle.secondary, row=0)
    async def btn_content(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ContentModal(self.script, self, self.index))

    @discord.ui.button(label="Author", emoji="👤", style=discord.ButtonStyle.secondary, row=0)
    async def btn_author(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AuthorModal(self.script, self, self.index))

    @discord.ui.button(label="Images", emoji="🖼️", style=discord.ButtonStyle.secondary, row=0)
    async def btn_images(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ImageModal(self.script, self, self.index))

    @discord.ui.button(label="Color", emoji="🎨", style=discord.ButtonStyle.secondary, row=0)
    async def btn_color(self, interaction: discord.Interaction, button: discord.ui.Button):
        color_view = ColorSelectView(self.script, self) # ColorSelectView needs update or handles index?
        # For simplicity, color presets will set the color for this specific index
        # We need to make sure ColorSelectView sets self.script.embeds[index]['color']
        # Let's adjust ColorSelectView later or inline it here.
        await interaction.response.edit_message(content="🎨 Select color for this embed:", view=color_view)

    @discord.ui.button(label="Fields", emoji="📋", style=discord.ButtonStyle.secondary, row=1)
    async def btn_fields(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = FieldManagerView(self.script, self, self.index)
        await interaction.response.edit_message(content=f"📋 **Field Manager (Embed #{self.index + 1})**", view=view)

    @discord.ui.button(label="Footer", emoji="📎", style=discord.ButtonStyle.secondary, row=1)
    async def btn_footer(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(FooterModal(self.script, self, self.index))

    @discord.ui.button(label="Back to Hub", emoji="🔙", style=discord.ButtonStyle.primary, row=2)
    async def btn_back(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.editor_view.refresh(interaction)

class EmbedEditorView(discord.ui.View):
    """The central Hub for the Embed Editor. All sub-editors return here."""

    def __init__(self, script: EmbedScript = None):
        super().__init__(timeout=None)
        self.script = script
        self._original_interaction: discord.Interaction | None = None
        if script:
            self._build_dynamic_buttons()

    def _build_dynamic_buttons(self):
        self.clear_items()
        
        # Row 0 & 1: Embed Selection Buttons
        for i in range(self.script.embed_count if self.script else 1):
            row = 0 if i < 5 else 1
            btn = discord.ui.Button(
                label=f"E{i+1}", 
                emoji="📑", 
                style=discord.ButtonStyle.primary, 
                row=row,
                custom_id=f"im8_embed_select_{i}"
            )
            self.add_item(btn)

        # Row 2: Functional Buttons
        self.add_item(discord.ui.Button(label="Add Link", emoji="🔗", style=discord.ButtonStyle.secondary, row=2, custom_id="im8_embed_btn_add_link"))
        self.add_item(discord.ui.Button(label="Ping / Context", emoji="💬", style=discord.ButtonStyle.secondary, row=2, custom_id="im8_embed_btn_ping"))
        self.add_item(discord.ui.Button(label="Clear Buttons", emoji="🗑️", style=discord.ButtonStyle.danger, row=2, custom_id="im8_embed_btn_clear_links"))
        self.add_item(discord.ui.Button(label="Syntax Guide", emoji="📖", style=discord.ButtonStyle.secondary, row=2, custom_id="im8_embed_btn_syntax"))

        # Row 3: Draft/Config
        self.add_item(discord.ui.Button(label="Templates", emoji="📄", style=discord.ButtonStyle.secondary, row=3, custom_id="im8_embed_btn_templates"))
        self.add_item(discord.ui.Button(label="Save Template", emoji="💾", style=discord.ButtonStyle.success, row=3, custom_id="im8_embed_btn_save_template"))
        self.add_item(discord.ui.Button(label="Embed Count", emoji="🔢", style=discord.ButtonStyle.secondary, row=3, custom_id="im8_embed_btn_count"))
        self.add_item(discord.ui.Button(label="Load ID", emoji="📥", style=discord.ButtonStyle.secondary, row=3, custom_id="im8_embed_btn_import"))
        self.add_item(discord.ui.Button(label="Import URL", emoji="🌐", style=discord.ButtonStyle.secondary, row=3, custom_id="im8_embed_btn_url_import"))

        # Row 4: Action/Broadcast
        self.add_item(discord.ui.Button(label="Choose Channels", emoji="📍", style=discord.ButtonStyle.success, row=4, custom_id="im8_embed_btn_channels"))
        self.add_item(discord.ui.Button(label="Schedule", emoji="⏳", style=discord.ButtonStyle.secondary, row=4, custom_id="im8_embed_btn_schedule"))
        self.add_item(discord.ui.Button(label="Manage Scheduled", emoji="🗓️", style=discord.ButtonStyle.secondary, row=4, custom_id="im8_embed_btn_manage_scheduled"))
        self.add_item(discord.ui.Button(label="Export JSON", emoji="📤", style=discord.ButtonStyle.secondary, row=4, custom_id="im8_embed_btn_export"))

    def _make_callback(self, index: int):
        async def callback(interaction: discord.Interaction):
            view = SingleEmbedEditorView(self.script, self, index)
            embeds = self.script.build_embeds(preview=True)
            await interaction.response.edit_message(
                content=f"**✏️ Editing Embed #{index + 1}**\n*Configure this specific embed's details.*",
                embed=embeds[index],
                view=view
            )
        return callback

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        cid = interaction.data.get("custom_id", "")
        
        # 1. State Hydration (if bot restarted)
        if not self.script:
            row = await interaction.client.database.fetch_one(
                "SELECT payload FROM editor_sessions WHERE message_id = ?",
                (interaction.message.id,)
            )
            if row:
                self.script = EmbedScript.from_dict(json.loads(row['payload']), interaction.user.id)
                self._build_dynamic_buttons()
            else:
                await interaction.response.send_message(
                    "❌ This editor session has expired or was removed. Please start a new one.", 
                    ephemeral=True
                )
                return False

        # 2. Handle Embed Selection Buttons
        if cid.startswith("im8_embed_select_"):
            index = int(cid.split("_")[-1])
            view = SingleEmbedEditorView(self.script, self, index)
            embeds = self.script.build_embeds(preview=True, member=interaction.user)
            await interaction.response.edit_message(
                content=f"**✏️ Editing Embed #{index + 1}**\n*Configure text, media, and metadata.*",
                embed=embeds[index],
                view=view
            )
            return False

        # 3. Handle Hub Functional Buttons
        if cid == "im8_embed_btn_add_link":
            await interaction.response.send_modal(ButtonModal(self.script, self))
            return False
        if cid == "im8_embed_btn_syntax":
            await interaction.response.send_message(SyntaxGuideModal.get_help_text(), ephemeral=True)
            return False
        if cid == "im8_embed_btn_clear_links":
            self.script.buttons.clear()
            await self.refresh(interaction)
            return False
        if cid == "im8_embed_btn_ping":
            view = PingSelectionView(self.script, self)
            await interaction.response.edit_message(content="**💬 Ping / Content**", view=view)
            return False
        if cid == "im8_embed_btn_channels":
            view = ChannelSelectView(self.script, self)
            await interaction.response.edit_message(content="**📍 Select Channels**", view=view)
            return False
        if cid == "im8_embed_btn_templates":
            db = interaction.client.database
            customs = await db.fetch_all("SELECT * FROM embed_templates WHERE guild_id = ?", (interaction.guild_id,))
            view = TemplateSelectView(self.script, self, customs)
            await interaction.response.edit_message(content="**📄 Choose a Template**", view=view)
            return False
        if cid == "im8_embed_btn_save_template":
            await interaction.response.send_modal(TemplateSaveModal(self.script))
            return False
        if cid == "im8_embed_btn_count":
            view = EmbedCountView(self.script, self)
            await interaction.response.edit_message(content="**🔢 Set Embed Count (1-10)**", view=view)
            return False
        if cid == "im8_embed_btn_import":
            await interaction.response.send_modal(MessageImportModal(self.script, self))
            return False
        if cid == "im8_embed_btn_schedule":
            view = ScheduleView(self.script, self)
            await interaction.response.edit_message(content=view._build_prompt(), view=view)
            return False
        if cid == "im8_embed_btn_url_import":
            await interaction.response.send_modal(UrlImportModal(self.script, self))
            return False
        if cid == "im8_embed_btn_manage_scheduled":
            cog = interaction.client.get_cog("EmbedEditor")
            tasks = await cog.get_pending_tasks(interaction.guild_id)
            if not tasks:
                await interaction.response.send_message("📭 No pending scheduled posts.", ephemeral=True)
                return False
            view = ManageScheduledView(tasks, interaction.client, self)
            await interaction.response.edit_message(content="**🗓️ Managed Scheduled Tasks**", embed=None, view=view)
            return False
        if cid == "im8_embed_btn_export":
            json_str = self.script.to_json()
            if len(json_str) <= 1900:
                await interaction.response.send_message(f"**📤 JSON Export:**\n```json\n{json_str}\n```", ephemeral=True)
            else:
                import io
                file = discord.File(io.BytesIO(json_str.encode()), filename="embed_export.json")
                await interaction.response.send_message("📤 JSON Export (file):", file=file, ephemeral=True)
            return False
        return True

    async def refresh(self, interaction: discord.Interaction):
        """Updates the Hub preview and saves session to DB."""
        self._original_interaction = interaction
        self._build_dynamic_buttons()
        
        # Save to DB for persistence
        await interaction.client.database.execute(
            "REPLACE INTO editor_sessions (message_id, user_id, session_type, payload) VALUES (?, ?, ?, ?)",
            (interaction.message.id, self.script.user_id, "embed", self.script.to_json())
        )

        embeds = self.script.build_embeds(preview=True, member=interaction.user)
        # Discord only allows 10 embeds per message, which is our max
        
        try:
            await interaction.response.edit_message(
                content=self.script.status_summary(),
                embeds=embeds,
                view=self,
            )
        except discord.InteractionResponded:
            try:
                msg = await interaction.original_response()
                await msg.edit(
                    content=self.script.status_summary(),
                    embeds=embeds,
                    view=self,
                )
            except Exception:
                pass
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════
#  COG REGISTRATION
# ═══════════════════════════════════════════════════════════

@app_commands.default_permissions(manage_messages=True)
class EmbedEditor(commands.Cog):
    """Advanced interactive Embed Builder Hub with Persistent Global Memory."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        """Reload pending tasks from DB on startup and register persistent views."""
        self.bot.loop.create_task(self._reload_tasks())
        # Register persistent Hub
        self.bot.add_view(EmbedEditorView())

    async def _reload_tasks(self):
        await self.bot.wait_until_ready()
        tasks = await self.bot.database.fetch_all("SELECT * FROM scheduled_tasks WHERE status = 'pending'")
        count = 0
        from datetime import datetime
        for t in tasks:
            run_at = datetime.fromisoformat(t['run_at'])
            # If it's too late (e.g. > 1 hour), mark as failed, otherwise schedule for now
            if run_at < datetime.now():
                if (datetime.now() - run_at).total_seconds() < 3600:
                    run_at = datetime.now() # Run immediately
                else:
                    await self.bot.database.execute("UPDATE scheduled_tasks SET status = 'failed', last_error = 'Missed during downtime' WHERE id = ?", (t['id'],))
                    continue

            self.bot.scheduler.add_date_job(
                func=self.execute_scheduled_post,
                job_id=f"embed_{t['id']}",
                run_date=run_at,
                kwargs={"task_id": t['id']}
            )
            count += 1
        if count > 0:
            logger.info(f"Restored {count} scheduled embed tasks from Global Memory.")

    async def schedule_task(self, guild_id, channel_ids, payload, run_at, created_by):
        cursor = await self.bot.database.execute(
            "INSERT INTO scheduled_tasks (guild_id, target_channels, task_type, payload, run_at, created_by) VALUES (?, ?, ?, ?, ?, ?)",
            (guild_id, json.dumps(channel_ids), "embed_broadcast", json.dumps(payload), run_at.isoformat(), created_by)
        )
        task_id = cursor.lastrowid
        self.bot.scheduler.add_date_job(
            func=self.execute_scheduled_post,
            job_id=f"embed_{task_id}",
            run_date=run_at,
            kwargs={"task_id": task_id}
        )

    async def cancel_task(self, task_id: int):
        await self.bot.database.execute("UPDATE scheduled_tasks SET status = 'cancelled' WHERE id = ?", (task_id,))
        self.bot.scheduler.remove_job(f"embed_{task_id}")

    async def get_pending_tasks(self, guild_id: int):
        return await self.bot.database.fetch_all("SELECT * FROM scheduled_tasks WHERE guild_id = ? AND status = 'pending'", (guild_id,))

    async def execute_scheduled_post(self, task_id: int):
        task = await self.bot.database.fetch_one("SELECT * FROM scheduled_tasks WHERE id = ?", (task_id,))
        if not task or task['status'] != 'pending': return

        try:
            payload = json.loads(task['payload'])
            channel_ids = json.loads(task['target_channels'])
            
            # Reconstruct script for easy sending
            script = EmbedScript.from_dict(payload, task['created_by'])
            embeds = script.build_embeds()
            
            # Buttons
            view = discord.ui.View()
            for b in script.buttons:
                if b.get("type") == "role":
                    view.add_item(discord.ui.Button(
                        label=b['label'], 
                        custom_id=f"im8_role_{b['role_id']}", 
                        style=discord.ButtonStyle.primary
                    ))
                else:
                    view.add_item(discord.ui.Button(
                        label=b['label'], 
                        url=b.get("url"), 
                        style=discord.ButtonStyle.link
                    ))

            sent_count = 0
            for cid in channel_ids:
                channel = self.bot.get_channel(cid)
                if not channel: channel = await self.bot.fetch_channel(cid)
                if channel:
                    await channel.send(content=script.content, embeds=embeds, view=view if script.buttons else None)
                    sent_count += 1
            
            await self.bot.database.execute("UPDATE scheduled_tasks SET status = 'sent' WHERE id = ?", (task_id,))
            logger.info(f"Scheduled task #{task_id} broadcasted to {sent_count} channels.")
        except Exception as e:
            logger.error(f"Failed to execute scheduled task #{task_id}: {e}")
            await self.bot.database.execute("UPDATE scheduled_tasks SET status = 'failed', last_error = ? WHERE id = ?", (str(e), task_id))

    @app_commands.command(
        name="embed",
        description="Launch the interactive Embed Editor Hub.",
    )
    async def embed_cmd(self, interaction: discord.Interaction) -> None:
        """Starts a fresh Embed Editor session."""
        script = EmbedScript(user_id=interaction.user.id)
        view = EmbedEditorView(script)
        view._original_interaction = interaction

        # NOT ephemeral if we want persistence across bot restarts
        await interaction.response.send_message(
            content=script.status_summary(),
            embeds=script.build_embeds(preview=True),
            view=view,
            ephemeral=False,
        )
        
        # Save session to DB
        msg = await interaction.original_response()
        await interaction.client.database.execute(
            "INSERT INTO editor_sessions (message_id, user_id, session_type, payload) VALUES (?, ?, ?, ?)",
            (msg.id, interaction.user.id, "embed", script.to_json())
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(EmbedEditor(bot))
