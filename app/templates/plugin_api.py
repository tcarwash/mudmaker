"""Small API for trusted Python game mechanic plugins."""

class PluginAPI:
    def __init__(self):
        self.commands = {}
        self.effects = {}

    def command(self, name, handler, help_text=""):
        name = name.strip().lower()
        if not name or not name.replace("_", "").replace("-", "").isalnum() or name in self.commands:
            raise ValueError(f"Invalid or duplicate plugin command: {name}")
        self.commands[name] = (handler, help_text)

    def effect(self, name, handler):
        name = name.strip().lower()
        if not name or not name.replace("_", "").replace("-", "").isalnum() or name in self.effects:
            raise ValueError(f"Invalid or duplicate plugin effect: {name}")
        self.effects[name] = handler
