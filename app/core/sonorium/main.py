"""
Main entry point for standalone Sonorium application.

Starts the web server and optionally a system tray icon.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import threading
import webbrowser
from pathlib import Path

# Setup logging first
from sonorium.obs import logger


def run_server(host: str = '127.0.0.1', port: int = 8008, open_browser: bool = True):
    """Run the Sonorium web server."""
    import uvicorn
    from sonorium.config import get_config, copy_bundled_themes
    from sonorium.app_device import SonoriumApp
    from sonorium.web_api import create_app, set_plugin_manager
    from sonorium.plugins.manager import PluginManager

    config = get_config()

    # Copy bundled themes to the themes directory if they don't exist
    copy_bundled_themes(Path(config.audio_path))

    # Initialize the application
    app_instance = SonoriumApp(path_audio=config.audio_path)

    # Set volume from config
    app_instance.set_volume(config.master_volume)

    # Auto-play last theme if configured
    if config.auto_play_on_start and config.last_theme:
        theme = app_instance.get_theme(config.last_theme)
        if theme:
            app_instance.play(config.last_theme)

    # Initialize plugin manager
    plugin_manager = PluginManager(
        config=config,
        audio_path=Path(config.audio_path)
    )

    # Initialize plugins asynchronously
    async def init_plugins():
        await plugin_manager.initialize()

    asyncio.run(init_plugins())
    set_plugin_manager(plugin_manager)
    logger.info(f'Plugin manager initialized with {len(plugin_manager.plugins)} plugin(s)')

    # Create FastAPI app
    fastapi_app = create_app(app_instance)

    # Open browser
    if open_browser:
        def open_browser_delayed():
            import time
            time.sleep(1.5)
            webbrowser.open(f'http://{host}:{port}')

        threading.Thread(target=open_browser_delayed, daemon=True).start()

    logger.info(f'Starting Sonorium server at http://{host}:{port}')

    # Run server
    uvicorn.run(fastapi_app, host=host, port=port, log_level='info')


def run_with_tray(host: str = '127.0.0.1', port: int = 8008):
    """Run with system tray icon."""
    try:
        import pystray
        from PIL import Image
    except ImportError:
        logger.warning('pystray or PIL not installed. Running without tray icon.')
        run_server(host, port)
        return

    from sonorium.config import get_config

    config = get_config()
    server_thread = None
    stop_event = threading.Event()

    def start_server():
        nonlocal server_thread
        if server_thread is None or not server_thread.is_alive():
            server_thread = threading.Thread(
                target=run_server,
                args=(host, port, True),
                daemon=True
            )
            server_thread.start()

    def open_ui(icon, item):
        webbrowser.open(f'http://{host}:{port}')

    def quit_app(icon, item):
        stop_event.set()
        icon.stop()

    # Load icon - icon.png is at app/core/icon.png, this file is at app/core/sonorium/
    icon_path = Path(__file__).parent.parent / 'icon.png'
    if icon_path.exists():
        image = Image.open(icon_path)
    else:
        # Create a simple default icon
        image = Image.new('RGB', (64, 64), color='#1a1a2e')

    # Create tray menu
    menu = pystray.Menu(
        pystray.MenuItem('Open Sonorium', open_ui, default=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem('Quit', quit_app)
    )

    # Create tray icon
    icon = pystray.Icon('Sonorium', image, 'Sonorium', menu)

    # Start server
    start_server()

    # Run tray icon (blocks until quit)
    icon.run()


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description='Sonorium - Ambient Soundscape Mixer')
    parser.add_argument('--host', default='127.0.0.1', help='Server host (default: 127.0.0.1)')
    parser.add_argument('--port', type=int, default=8008, help='Server port (default: 8008)')
    parser.add_argument('--no-browser', action='store_true', help='Do not open browser on start')
    parser.add_argument('--no-tray', action='store_true', help='Run without system tray icon')

    args = parser.parse_args()

    if args.no_tray:
        run_server(args.host, args.port, not args.no_browser)
    else:
        run_with_tray(args.host, args.port)


if __name__ == '__main__':
    main()
