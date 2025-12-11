import logging

from fastapi import FastAPI
from fastapi.responses import StreamingResponse, HTMLResponse

from sonorium.theme import ThemeDefinition
from sonorium.version import __version__
from fmtr.tools import api, mqtt

for name in ["uvicorn.access", "uvicorn.error", "uvicorn"]:
    logger = logging.getLogger(name)
    logger.handlers.clear()
    logger.propagate = False


class ApiSonorium(api.Base):
    TITLE = f'Sonorium {__version__} Streaming API'
    URL_DOCS = '/docs'

    def __init__(self, client: mqtt.Client):
        super().__init__()
        self.client = client

    def get_endpoints(self):
        endpoints = [
            api.Endpoint(method_http=self.app.get, path='/', method=self.web_ui),
            api.Endpoint(method_http=self.app.get, path='/stream/{id}', method=self.stream),
            api.Endpoint(method_http=self.app.post, path='/api/enable_all/{theme_id}', method=self.enable_all),
            api.Endpoint(method_http=self.app.post, path='/api/disable_all/{theme_id}', method=self.disable_all),
            api.Endpoint(method_http=self.app.get, path='/api/status', method=self.status),
        ]
        return endpoints

    async def web_ui(self):
        """Serve a simple web UI for controlling Sonorium"""
        device = self.client.device
        themes = device.themes
        
        # Build theme cards
        theme_cards = ""
        for theme in themes:
            enabled_count = sum(1 for inst in theme.instances if inst.is_enabled)
            total = len(theme.instances)
            is_current = theme == themes.current
            current_class = "current" if is_current else ""
            
            recordings_list = ""
            for inst in theme.instances:
                status = "✓" if inst.is_enabled else "○"
                status_class = "enabled" if inst.is_enabled else "disabled"
                recordings_list += f'<div class="rec {status_class}"><span class="status">{status}</span> {inst.name}</div>'
            
            theme_cards += f'''
            <div class="theme-card {current_class}">
                <div class="theme-header">
                    <h3>{theme.name}</h3>
                    <span class="track-count">{enabled_count}/{total} enabled</span>
                </div>
                <div class="recordings">{recordings_list}</div>
                <div class="theme-actions">
                    <button onclick="enableAll('{theme.id}')">Enable All</button>
                    <button onclick="disableAll('{theme.id}')" class="secondary">Disable All</button>
                    <button onclick="playTheme('{theme.id}')" class="play">▶ Play</button>
                </div>
                <div class="stream-url">Stream: {theme.url}</div>
            </div>
            '''
        
        html = f'''<!DOCTYPE html>
<html>
<head>
    <title>Sonorium {__version__}</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta http-equiv="refresh" content="10">
    <style>
        * {{ box-sizing: border-box; }}
        body {{ 
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            color: #eee;
            margin: 0;
            padding: 20px;
            min-height: 100vh;
        }}
        .container {{ max-width: 800px; margin: 0 auto; }}
        h1 {{ 
            color: #00d4ff;
            text-align: center;
            margin-bottom: 10px;
        }}
        .subtitle {{
            text-align: center;
            color: #888;
            margin-bottom: 30px;
        }}
        .theme-card {{
            background: rgba(255,255,255,0.08);
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 20px;
            border: 2px solid transparent;
        }}
        .theme-card.current {{
            border-color: #00d4ff;
        }}
        .theme-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 15px;
        }}
        .theme-header h3 {{
            margin: 0;
            color: #fff;
        }}
        .track-count {{
            color: #00d4ff;
            font-size: 14px;
        }}
        .recordings {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
            gap: 8px;
            margin-bottom: 15px;
            max-height: 200px;
            overflow-y: auto;
        }}
        .rec {{
            padding: 6px 10px;
            background: rgba(0,0,0,0.3);
            border-radius: 6px;
            font-size: 12px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }}
        .rec.enabled {{ color: #00ff88; }}
        .rec.disabled {{ color: #666; }}
        .rec .status {{ margin-right: 5px; }}
        .theme-actions {{
            display: flex;
            gap: 10px;
        }}
        button {{
            padding: 10px 16px;
            border: none;
            border-radius: 8px;
            font-size: 14px;
            cursor: pointer;
            background: #00d4ff;
            color: #000;
            font-weight: bold;
        }}
        button:hover {{ opacity: 0.9; }}
        button.secondary {{
            background: rgba(255,255,255,0.2);
            color: #fff;
        }}
        button.play {{
            background: #00ff88;
            flex: 1;
        }}
        .stream-url {{
            margin-top: 10px;
            font-size: 11px;
            color: #666;
            font-family: monospace;
        }}
        .status-msg {{
            position: fixed;
            bottom: 20px;
            left: 50%;
            transform: translateX(-50%);
            background: #00d4ff;
            color: #000;
            padding: 10px 20px;
            border-radius: 8px;
            display: none;
        }}
        .status-msg.show {{ display: block; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>🎵 Sonorium {__version__}</h1>
        <p class="subtitle">Ambient Soundscape Mixer • Auto-refreshes every 10s</p>
        {theme_cards}
    </div>
    <div class="status-msg" id="status"></div>
    
    <script>
        function showStatus(msg) {{
            const el = document.getElementById('status');
            el.textContent = msg;
            el.classList.add('show');
            setTimeout(() => el.classList.remove('show'), 2000);
        }}
        
        async function enableAll(themeId) {{
            await fetch('/api/enable_all/' + themeId, {{method: 'POST'}});
            showStatus('All recordings enabled!');
            setTimeout(() => location.reload(), 500);
        }}
        
        async function disableAll(themeId) {{
            await fetch('/api/disable_all/' + themeId, {{method: 'POST'}});
            showStatus('All recordings disabled');
            setTimeout(() => location.reload(), 500);
        }}
        
        function playTheme(themeId) {{
            window.open('/stream/' + themeId, '_blank');
            showStatus('Opening audio stream...');
        }}
    </script>
</body>
</html>'''
        return HTMLResponse(content=html)

    async def stream(self, id: str):
        theme_def: ThemeDefinition = self.client.device.themes.id[id]
        stream = theme_def.get_stream()
        response = StreamingResponse(stream, media_type="audio/mpeg")
        return response

    async def enable_all(self, theme_id: str):
        """Enable all recordings in a theme"""
        theme_def: ThemeDefinition = self.client.device.themes.id[theme_id]
        for instance in theme_def.instances:
            instance.is_enabled = True
        return {"status": "ok", "theme": theme_id, "enabled": len(theme_def.instances)}

    async def disable_all(self, theme_id: str):
        """Disable all recordings in a theme"""
        theme_def: ThemeDefinition = self.client.device.themes.id[theme_id]
        for instance in theme_def.instances:
            instance.is_enabled = False
        return {"status": "ok", "theme": theme_id, "disabled": len(theme_def.instances)}

    async def status(self):
        """Get current status"""
        device = self.client.device
        themes_data = []
        for theme in device.themes:
            enabled_count = sum(1 for i in theme.instances if i.is_enabled)
            themes_data.append({
                "name": theme.name,
                "id": theme.id,
                "total_tracks": len(theme.instances),
                "enabled_tracks": enabled_count,
                "url": theme.url
            })
        return {
            "version": __version__,
            "current_theme": device.themes.current.name if device.themes.current else None,
            "themes": themes_data
        }


if __name__ == '__main__':
    ApiSonorium.launch()
