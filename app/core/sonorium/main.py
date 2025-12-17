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


def check_recovery_state(config_dir: Path) -> dict | None:
    """Check for recovery state file and return state if valid."""
    import json
    from datetime import datetime

    recovery_path = config_dir / 'recovery.json'
    if not recovery_path.exists():
        return None

    try:
        with open(recovery_path, 'r', encoding='utf-8') as f:
            state = json.load(f)

        # Validate the recovery state
        if state.get('reason') != 'update':
            logger.info("Recovery state found but reason is not 'update', ignoring")
            recovery_path.unlink()
            return None

        # Check if the recovery state is recent (within 5 minutes)
        timestamp = state.get('timestamp')
        if timestamp:
            recovery_time = datetime.fromisoformat(timestamp)
            age = (datetime.now() - recovery_time).total_seconds()
            if age > 300:  # 5 minutes
                logger.info(f"Recovery state is too old ({age:.0f}s), ignoring")
                recovery_path.unlink()
                return None

        logger.info(f"Valid recovery state found: theme={state.get('theme')}, preset={state.get('preset')}")
        return state

    except Exception as e:
        logger.warning(f"Failed to read recovery state: {e}")
        if recovery_path.exists():
            recovery_path.unlink()
        return None


def clear_recovery_state(config_dir: Path):
    """Remove recovery state file after successful recovery."""
    recovery_path = config_dir / 'recovery.json'
    if recovery_path.exists():
        recovery_path.unlink()
        logger.info("Cleared recovery state")


def run_server(host: str = '0.0.0.0', port: int = 8008, open_browser: bool = True):
    """Run the Sonorium web server."""
    import uvicorn
    from sonorium.config import get_config, get_config_dir, copy_bundled_themes
    from sonorium.app_device import SonoriumApp
    from sonorium.web_api import create_app, set_plugin_manager
    from sonorium.plugins.manager import PluginManager

    config = get_config()
    config_dir = get_config_dir()

    # Copy bundled themes to the themes directory if they don't exist
    copy_bundled_themes(Path(config.audio_path))

    # Check for recovery state (from update)
    recovery_state = check_recovery_state(config_dir)

    # Initialize the application
    app_instance = SonoriumApp(path_audio=config.audio_path)

    # Set volume from config (or recovery state)
    if recovery_state and recovery_state.get('volume'):
        app_instance.set_volume(recovery_state['volume'])
    else:
        app_instance.set_volume(config.master_volume)

    # Recovery playback takes priority over auto-play
    if recovery_state and recovery_state.get('theme'):
        theme_id = recovery_state['theme']
        preset_id = recovery_state.get('preset')
        theme = app_instance.get_theme(theme_id)
        if theme:
            logger.info(f"Recovering playback: theme={theme_id}, preset={preset_id}")
            app_instance.play(theme_id, preset_id)
            clear_recovery_state(config_dir)
        else:
            logger.warning(f"Recovery theme '{theme_id}' not found")
            clear_recovery_state(config_dir)
    # Auto-play last theme if configured (and no recovery)
    elif config.auto_play_on_start and config.last_theme:
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


def run_with_tray(host: str = '0.0.0.0', port: int = 8008):
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
    parser.add_argument('--host', default='0.0.0.0', help='Server host (default: 0.0.0.0 for network access)')
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
