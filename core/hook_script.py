"""
IM8 Bot — Hook Script (State Management)
Extends EmbedScript to add Webhook Identity (Name & Avatar).
Supports "Normal Message" mode (content only).
"""

import discord
from typing import Optional, List, Dict, Any
from core.embed_script import EmbedScript

class HookScript(EmbedScript):
    """Holds the state for a Webhook-based message."""

    def __init__(self, user_id: int):
        super().__init__(user_id)
        
        # ── Webhook Identity ─────────────────────────
        self.hook_name: Optional[str] = "IM8 Hook"
        self.hook_avatar_url: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert state to dict, including identity fields."""
        data = super().to_dict()
        data.update({
            "hook_name": self.hook_name,
            "hook_avatar_url": self.hook_avatar_url,
        })
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any], user_id: int) -> "HookScript":
        """Hydrate a HookScript from a dictionary."""
        # Get base script
        base_script = super(HookScript, cls).from_dict(data, user_id)
        
        # Create hook script and copy base attributes
        script = cls(user_id)
        for attr, value in vars(base_script).items():
            setattr(script, attr, value)
            
        # Add identity
        script.hook_name = data.get("hook_name", "IM8 Hook")
        script.hook_avatar_url = data.get("hook_avatar_url")
        
        return script

    def status_summary(self) -> str:
        """Enhanced status bar including Hook identity."""
        lines = ["**🪝 IM8 Hook Editor Hub**"]
        lines.append("```")

        # Identity
        lines.append(f"  Identity  │ {self.hook_name or 'Default'}")
        lines.append(f"  Avatar    │ {'✅ Set' if self.hook_avatar_url else '➖ Default'}")

        # Channels
        if self.channels:
            ch_names = ", ".join(f"#{c.name}" for c in self.channels[:10])
            extra = f" (+{len(self.channels) - 10} more)" if len(self.channels) > 10 else ""
            lines.append(f"  Channels  │ {ch_names}{extra}")
        else:
            lines.append("  Channels  │ (none selected)")

        # Indicators
        first_embed = self.embeds[0]
        indicators = []
        has_embed = first_embed.get("title") or first_embed.get("description")
        indicators.append(f"Embed {'✅' if has_embed else '➖'}")
        indicators.append(f"Normal {'✅' if self.content else '➖'}")
        
        has_media = first_embed.get("image_url") or first_embed.get("thumbnail_url")
        indicators.append(f"Images {'✅' if has_media else '➖'}")
        lines.append(f"  Status    │ {' · '.join(indicators)}")

        lines.append("```")
        return "\n".join(lines)
