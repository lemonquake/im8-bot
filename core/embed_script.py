"""
IM8 Bot — Embed Script (State Management)
Holds all state for the Embed Editor Hub, with serialization
for scheduling, JSON import/export, and template hydration.
Supports multiple embeds (up to 10).
"""

import json
import discord
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
import copy


class EmbedScript:
    """Holds the complete state of the message being edited, including multiple embeds."""

    def __init__(self, user_id: int):
        self.user_id = user_id

        # ── Message-level content ────────────────────
        self.content: Optional[str] = None
        self.buttons: List[Dict[str, Any]] = []  # Link buttons (url) or Role buttons (role_id)
        self.channels: List[discord.TextChannel] = []
        self.editing_message: Optional[discord.Message] = None

        # ── Multiple Embeds ──────────────────────────
        self.embeds: List[Dict[str, Any]] = [self._default_embed_state()]

    def _default_embed_state(self) -> Dict[str, Any]:
        """Returns a fresh dictionary for one embed's properties."""
        return {
            "title": None,
            "description": None,
            "color": None,
            "image_url": None,
            "thumbnail_url": None,
            "author_name": None,
            "author_icon": None,
            "author_url": None,
            "footer_text": None,
            "footer_icon": None,
            "use_timestamp": False,
            "fields": [],  # Each field: {'name': str, 'value': str, 'inline': bool}
        }

    @property
    def embed_count(self) -> int:
        return len(self.embeds)

    def set_embed_count(self, count: int):
        """Adjusts the number of embeds, preserving existing data where possible."""
        count = max(1, min(10, count))
        if count > len(self.embeds):
            for _ in range(count - len(self.embeds)):
                self.embeds.append(self._default_embed_state())
        elif count < len(self.embeds):
            self.embeds = self.embeds[:count]

    # ═══════════════════════════════════════════════
    #  Variable Resolution
    # ═══════════════════════════════════════════════

    def _resolve_text(self, text: Optional[str], member: Optional[discord.Member] = None) -> Optional[str]:
        """Replaces placeholders in text with dynamic values."""
        if not text or "{" not in text:
            return text

        guild = member.guild if member else None
        
        replacements = {
            "{user_mention}": member.mention if member else "(member mention)",
            "{user_name}": member.display_name if member else "(member name)",
            "{server_name}": guild.name if guild else "(server name)",
            "{member_count}": str(guild.member_count) if guild else "0",
            "{date_now}": datetime.now().strftime("%Y-%m-%d"),
        }

        for key, val in replacements.items():
            text = text.replace(key, val)
        return text

    # ═══════════════════════════════════════════════
    #  Build Embeds
    # ═══════════════════════════════════════════════

    def build_embeds(self, preview: bool = False, member: Optional[discord.Member] = None) -> List[discord.Embed]:
        """Constructs a list of discord.Embed objects from the current state."""
        discord_embeds = []
        for i, data in enumerate(self.embeds):
            title = self._resolve_text(data.get("title"), member)
            
            raw_desc = data.get("description")
            # In preview mode, ensure all embeds have at least some content so Discord doesn't reject them
            if not raw_desc and preview:
                raw_desc = "*(No description set)*"
            
            description = self._resolve_text(raw_desc, member)

            embed = discord.Embed(
                title=title,
                description=description,
                color=data.get("color") if data.get("color") is not None else 0x00C9A7  # brand default
            )

            # ── Author ───────────────────────────
            if data.get("author_name"):
                author_name = self._resolve_text(data["author_name"], member)
                author_kwargs = {"name": author_name}
                if data.get("author_icon"):
                    author_kwargs["icon_url"] = data["author_icon"]
                if data.get("author_url"):
                    author_kwargs["url"] = data["author_url"]
                embed.set_author(**author_kwargs)

            # ── Media ────────────────────────────
            if data.get("image_url"):
                embed.set_image(url=data["image_url"])
            if data.get("thumbnail_url"):
                embed.set_thumbnail(url=data["thumbnail_url"])

            # ── Footer ───────────────────────────
            footer_kwargs = {}
            if data.get("footer_text"):
                footer_text = self._resolve_text(data["footer_text"], member)
                footer_kwargs["text"] = footer_text
            if data.get("footer_icon"):
                footer_kwargs["icon_url"] = data["footer_icon"]
            if footer_kwargs:
                embed.set_footer(**footer_kwargs)

            # ── Timestamp ────────────────────────
            if data.get("use_timestamp"):
                embed.timestamp = datetime.now(timezone.utc)

            # ── Fields ───────────────────────────
            for field in data.get("fields", []):
                f_name = self._resolve_text(field.get("name", "Untitled"), member)
                f_val = self._resolve_text(field.get("value", "\u200b"), member)
                embed.add_field(
                    name=f_name,
                    value=f_val,
                    inline=field.get("inline", False),
                )
            
            discord_embeds.append(embed)
        return discord_embeds

    def to_embed(self, preview: bool = False) -> Optional[discord.Embed]:
        """Backward compatibility for single-embed logic (returns first embed)."""
        embeds = self.build_embeds(preview=preview)
        return embeds[0] if embeds else None

    # ═══════════════════════════════════════════════
    #  Serialization
    # ═══════════════════════════════════════════════

    def to_dict(self) -> Dict[str, Any]:
        """Convert state to dict for templates or DB storage."""
        return {
            "content": self.content,
            "embeds": copy.deepcopy(self.embeds),
            "buttons": copy.deepcopy(self.buttons),
            "target_channel_ids": [c.id for c in self.channels if hasattr(c, "id")],
        }

    def to_json(self) -> str:
        """Pretty-printed JSON export of the script state."""
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: Dict[str, Any], user_id: int) -> "EmbedScript":
        """Hydrate an EmbedScript from a dictionary."""
        script = cls(user_id)
        script.content = data.get("content")
        
        # Support legacy single-embed format and new multi-embed format
        if "embeds" in data:
            script.embeds = data["embeds"]
        else:
            # Fallback for single embed keys
            legacy_embed = script._default_embed_state()
            for key in legacy_embed.keys():
                if key in data:
                    legacy_embed[key] = data[key]
            # Special case for keys that were renamed or nested differently
            if "author_icon_url" in data: legacy_embed["author_icon"] = data["author_icon_url"]
            if "footer_icon_url" in data: legacy_embed["footer_icon"] = data["footer_icon_url"]
            script.embeds = [legacy_embed]

        script.buttons = data.get("buttons", [])
        return script

    @classmethod
    def from_message(cls, message: discord.Message, user_id: int) -> "EmbedScript":
        """Parses an existing message back into script state (handles multiple embeds)."""
        script = cls(user_id)
        script.content = message.content or None
        script.embeds = []

        for e in message.embeds:
            state = script._default_embed_state()
            state["title"] = e.title
            state["description"] = e.description
            state["color"] = e.color.value if e.color else None

            # Media
            if e.image: state["image_url"] = e.image.url
            if e.thumbnail: state["thumbnail_url"] = e.thumbnail.url

            # Author
            if e.author:
                state["author_name"] = e.author.name
                state["author_icon"] = str(e.author.icon_url) if e.author.icon_url else None
                state["author_url"] = str(e.author.url) if e.author.url else None

            # Footer
            if e.footer:
                state["footer_text"] = e.footer.text
                state["footer_icon"] = str(e.footer.icon_url) if e.footer.icon_url else None

            # Timestamp
            if e.timestamp: state["use_timestamp"] = True

            # Fields
            for field in e.fields:
                state["fields"].append({
                    "name": field.name,
                    "value": field.value,
                    "inline": field.inline,
                })
            script.embeds.append(state)

        if not script.embeds:
            script.embeds = [script._default_embed_state()]

        # Parse link and role buttons from components
        if message.components:
            for row in message.components:
                for component in row.children:
                    if not isinstance(component, discord.Button):
                        continue
                        
                    if component.style == discord.ButtonStyle.link:
                        script.buttons.append({"type": "link", "label": component.label, "url": component.url})
                    elif component.custom_id and component.custom_id.startswith("im8_role_"):
                        rid = int(component.custom_id.replace("im8_role_", ""))
                        script.buttons.append({"type": "role", "label": component.label, "role_id": rid})

        script.editing_message = message
        return script

    # ═══════════════════════════════════════════════
    #  Hub Summary (status bar text)
    # ═══════════════════════════════════════════════

    def status_summary(self) -> str:
        """Generates a markdown status block for the Hub message."""
        header = f"**🛠️ IM8 Embed Editor Hub** — `{self.embed_count} Embed(s)`"
        if self.editing_message:
            header += f"\n**✏️ Editing:** [Jump to message]({self.editing_message.jump_url})"
        lines = [header]
        lines.append("```")

        # Channels
        if self.channels:
            ch_names = ", ".join(f"#{c.name}" for c in self.channels[:10])
            extra = f" (+{len(self.channels) - 10} more)" if len(self.channels) > 10 else ""
            lines.append(f"  Channels  │ {ch_names}{extra}")
        else:
            lines.append("  Channels  │ (none selected)")

        # Message Content / Buttons indicator
        buttons_str = f"{len(self.buttons)} button(s)" if self.buttons else "none"
        ping_str = "✅" if self.content else "➖"
        lines.append(f"  Global    │ Ping: {ping_str}  ·  Links: {buttons_str}")
        
        # Summary of embeds (just a few flags for space)
        for i, data in enumerate(self.embeds):
            indicators = []
            if data.get("author_name"): indicators.append("👤")
            if data.get("image_url"): indicators.append("🖼️")
            if data.get("fields"): indicators.append("📋")
            if data.get("footer_text"): indicators.append("📎")
            
            ind_str = " ".join(indicators) if indicators else "(blank)"
            lines.append(f"  Embed #{i+1:02} │ {ind_str}")

        lines.append("```")
        return "\n".join(lines)
