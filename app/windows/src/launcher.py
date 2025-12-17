"""
Sonorium Launcher - Native Windows application with PyQt6 UI.

This is the main executable that:
- Provides a native control window
- Shows system tray icon
- Manages the Python core application
- Handles first-run setup (creates folder structure, downloads core)
- Handles updates via updater.exe

PORTABLE: The EXE can be placed anywhere. On first run, it creates
the application structure around itself and downloads required files.
"""

import json
import logging
import os
import subprocess
import sys
import traceback
import webbrowser
import zipfile
import shutil
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QTextEdit, QSystemTrayIcon, QMenu,
    QDialog, QFormLayout, QSpinBox, QCheckBox, QComboBox,
    QTabWidget, QGroupBox, QMessageBox, QStyle, QProgressBar
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QProcess, QUrl
from PyQt6.QtGui import QIcon, QPixmap, QAction, QDesktopServices, QFont, QTextCursor


# Constants
APP_NAME = "Sonorium"
APP_VERSION = "0.1.8-alpha"
DEFAULT_PORT = 8008

# Global logger instance
_logger: Optional[logging.Logger] = None


def setup_logging() -> logging.Logger:
    """Set up logging to file and console."""
    global _logger
    if _logger is not None:
        return _logger

    # Create logs directory
    logs_dir = get_app_dir() / 'logs'
    logs_dir.mkdir(parents=True, exist_ok=True)

    # Create logger
    logger = logging.getLogger('sonorium_launcher')
    logger.setLevel(logging.DEBUG)

    # Prevent duplicate handlers on reload
    if logger.handlers:
        return logger

    # File handler - daily rotating log
    log_file = logs_dir / f'launcher_{datetime.now().strftime("%Y%m%d")}.log'
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_formatter = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)

    # Console handler (for debugging)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_formatter = logging.Formatter('[%(levelname)s] %(message)s')
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)

    _logger = logger

    # Log startup info
    logger.info("=" * 60)
    logger.info(f"Sonorium Launcher v{APP_VERSION} starting")
    logger.info(f"App directory: {get_app_dir()}")
    logger.info(f"Python: {sys.version}")
    logger.info(f"Frozen: {getattr(sys, 'frozen', False)}")
    if getattr(sys, 'frozen', False):
        logger.info(f"Executable: {sys.executable}")
    logger.info("=" * 60)

    return logger


def get_logger() -> logging.Logger:
    """Get the logger instance, creating it if necessary."""
    global _logger
    if _logger is None:
        return setup_logging()
    return _logger


WIKI_URL = "https://github.com/synssins/sonorium/wiki"
REPO_URL = "https://github.com/synssins/sonorium"

# GitHub Releases API URL (includes prereleases)
# Uses /releases to get all releases including alpha/beta
RELEASES_API_URL = "https://api.github.com/repos/synssins/sonorium/releases"
CORE_ZIP_FALLBACK = "https://github.com/synssins/sonorium/releases/download/v0.1.0-alpha/core.zip"

# Required folder structure (relative to app root)
REQUIRED_FOLDERS = ['core', 'config', 'logs', 'themes', 'plugins']


def get_app_dir() -> Path:
    """
    Get the application root directory.

    This is the directory where Sonorium.exe is located.
    All app folders (core, config, logs, themes, plugins) are created here.
    """
    if getattr(sys, 'frozen', False):
        # Running as compiled EXE - app root is where the EXE is
        return Path(sys.executable).parent
    else:
        # Running as script - use parent of windows/ folder for dev
        return Path(__file__).parent.parent


def get_config_path() -> Path:
    """Get path to config.json."""
    return get_app_dir() / 'config' / 'config.json'


def get_core_dir() -> Path:
    """Get path to core Python package."""
    return get_app_dir() / 'core'


def get_version_path() -> Path:
    """Get path to version.json in core."""
    return get_core_dir() / 'version.json'


def is_first_run() -> bool:
    """Check if this is the first run (core folder doesn't exist or is empty)."""
    core_dir = get_core_dir()
    if not core_dir.exists():
        return True
    # Check if core has the main script
    main_script = core_dir / 'sonorium' / 'main.py'
    return not main_script.exists()


def create_folder_structure():
    """Create the required folder structure."""
    app_dir = get_app_dir()
    for folder in REQUIRED_FOLDERS:
        folder_path = app_dir / folder
        folder_path.mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    """Load configuration from config.json."""
    config_path = get_config_path()
    if config_path.exists():
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {
        'server_port': DEFAULT_PORT,
        'start_minimized': False,
        'minimize_to_tray': True,
        'auto_start_server': True,
        'master_volume': 0.8,
        'repo_url': REPO_URL,
        'update_branch': 'dev'
    }


def save_config(config: dict):
    """Save configuration to config.json."""
    config_path = get_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2)


class SetupThread(QThread):
    """Thread to handle first-run setup (downloading core files from GitHub Releases)."""

    progress = pyqtSignal(str, int)  # message, percentage
    finished_setup = pyqtSignal(bool, str)  # success, message

    def __init__(self, app_dir: Path):
        super().__init__()
        self.app_dir = app_dir
        self.logger = get_logger()

    def _get_download_url(self) -> str:
        """Get the core.zip download URL from GitHub Releases API."""
        self.logger.debug(f"Fetching releases from: {RELEASES_API_URL}")
        try:
            req = urllib.request.Request(
                RELEASES_API_URL,
                headers={'Accept': 'application/vnd.github.v3+json', 'User-Agent': 'Sonorium-Launcher'}
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                releases = json.loads(response.read().decode('utf-8'))
                self.logger.debug(f"Found {len(releases)} releases")
                # Get the first (most recent) release that has core.zip
                for release in releases:
                    tag = release.get('tag_name', 'unknown')
                    for asset in release.get('assets', []):
                        if asset.get('name') == 'core.zip':
                            url = asset.get('browser_download_url')
                            self.logger.info(f"Found core.zip in release {tag}: {url}")
                            return url
                self.logger.warning("No core.zip found in any release")
        except Exception as e:
            self.logger.error(f"Failed to fetch releases: {e}")
        # Fallback to direct release URL
        self.logger.info(f"Using fallback URL: {CORE_ZIP_FALLBACK}")
        return CORE_ZIP_FALLBACK

    def run(self):
        """Download and extract core files from GitHub Releases."""
        self.logger.info("=== First-run setup starting ===")
        try:
            self.progress.emit("Creating folder structure...", 5)
            self.logger.info("Creating folder structure...")
            create_folder_structure()
            self.logger.info(f"Folders created in: {self.app_dir}")

            self.progress.emit("Finding latest release...", 10)
            self.logger.info("Finding latest release...")
            download_url = self._get_download_url()

            self.progress.emit("Downloading core files...", 15)
            self.logger.info(f"Downloading from: {download_url}")

            # Download core.zip from releases
            zip_path = self.app_dir / 'core_download.zip'
            try:
                req = urllib.request.Request(download_url, headers={'User-Agent': 'Sonorium-Launcher'})
                with urllib.request.urlopen(req, timeout=60) as response:
                    content_length = response.headers.get('Content-Length', 'unknown')
                    self.logger.info(f"Download size: {content_length} bytes")
                    data = response.read()
                    with open(zip_path, 'wb') as f:
                        f.write(data)
                    self.logger.info(f"Downloaded {len(data)} bytes to {zip_path}")
            except urllib.error.URLError as e:
                self.logger.error(f"Download failed: {e}")
                self.finished_setup.emit(False, f"Failed to download: {e}")
                return

            self.progress.emit("Extracting files...", 60)
            self.logger.info(f"Extracting {zip_path} to {self.app_dir}")

            # Extract the archive - core.zip contains core/ and themes/ at root level
            with zipfile.ZipFile(zip_path, 'r') as zf:
                file_list = zf.namelist()
                self.logger.debug(f"Archive contains {len(file_list)} files")
                zf.extractall(self.app_dir)
            self.logger.info("Extraction complete")

            self.progress.emit("Verifying installation...", 85)

            # Verify core was extracted
            main_script = self.app_dir / 'core' / 'sonorium' / 'main.py'
            self.logger.info(f"Checking for main script: {main_script}")
            if not main_script.exists():
                self.logger.error(f"Main script not found: {main_script}")
                self.finished_setup.emit(False, "Core files not found after extraction")
                return
            self.logger.info("Verification passed - main.py found")

            self.progress.emit("Cleaning up...", 95)

            # Clean up temp files
            zip_path.unlink(missing_ok=True)
            self.logger.info("Cleaned up temp files")

            self.progress.emit("Setup complete!", 100)
            self.logger.info("=== Setup completed successfully ===")
            self.finished_setup.emit(True, "Setup completed successfully")

        except Exception as e:
            self.logger.exception(f"Setup failed with exception: {e}")
            self.finished_setup.emit(False, f"Setup failed: {e}")


class ServerThread(QThread):
    """Thread to run the Python core server."""

    output_received = pyqtSignal(str)
    server_started = pyqtSignal()
    server_stopped = pyqtSignal()
    error_occurred = pyqtSignal(str)

    def __init__(self, port: int = DEFAULT_PORT):
        super().__init__()
        self.port = port
        self.process: Optional[subprocess.Popen] = None
        self._stop_requested = False
        self.logger = get_logger()

    def run(self):
        """Run the server process using subprocess instead of QProcess for thread safety."""
        try:
            self.logger.info("ServerThread.run() starting")

            # Find Python executable
            app_dir = get_app_dir()
            core_dir = get_core_dir()

            if getattr(sys, 'frozen', False):
                # Try embedded Python first
                python_exe = app_dir / 'python' / 'python.exe'
                if not python_exe.exists():
                    # Fallback to system Python
                    self.logger.info("Embedded Python not found, using system Python")
                    python_exe = 'python'
                else:
                    self.logger.info(f"Using embedded Python: {python_exe}")
            else:
                python_exe = sys.executable
                self.logger.info(f"Using development Python: {python_exe}")

            # Start the server
            main_script = core_dir / 'sonorium' / 'main.py'

            if not main_script.exists():
                self.logger.error(f"Core not found: {main_script}")
                self.error_occurred.emit(f"Core not found: {main_script}")
                return

            args = [str(python_exe), str(main_script), '--no-tray', '--no-browser', '--port', str(self.port)]
            self.logger.info(f"Launching: {' '.join(args)}")

            # Set up environment
            env = os.environ.copy()
            env['PYTHONPATH'] = str(core_dir)
            self.logger.debug(f"PYTHONPATH set to: {core_dir}")

            self.output_received.emit(f"Starting server on port {self.port}...")

            # Use subprocess.Popen for thread-safe process management
            self.process = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=env,
                bufsize=1,
                universal_newlines=True,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
            )

            self.logger.info(f"Server process started with PID: {self.process.pid}")
            self.server_started.emit()

            # Read output line by line
            while not self._stop_requested:
                if self.process.poll() is not None:
                    # Process has exited
                    break

                # Read available output
                try:
                    line = self.process.stdout.readline()
                    if line:
                        line = line.rstrip()
                        self.output_received.emit(line)
                        self.logger.debug(f"[server] {line}")
                except Exception as e:
                    self.logger.warning(f"Error reading server output: {e}")
                    break

            # Get exit code
            exit_code = self.process.poll()
            if exit_code is None:
                # Process still running, we're stopping it
                exit_code = 0

            self.logger.info(f"Server process exited with code: {exit_code}")
            self.output_received.emit(f"Server stopped (exit code: {exit_code})")
            self.server_stopped.emit()

        except Exception as e:
            self.logger.exception(f"ServerThread.run() exception: {e}")
            self.error_occurred.emit(str(e))

    def stop(self):
        """Stop the server process."""
        self.logger.info("ServerThread.stop() called")
        self._stop_requested = True
        if self.process and self.process.poll() is None:
            self.logger.info("Terminating server process...")
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
                self.logger.info("Server terminated gracefully")
            except subprocess.TimeoutExpired:
                self.logger.warning("Server didn't terminate gracefully, killing")
                self.process.kill()
                self.process.wait()


class SetupDialog(QDialog):
    """First-run setup dialog."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Sonorium Setup")
        self.setFixedSize(450, 200)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowCloseButtonHint)
        self.setup_thread: Optional[SetupThread] = None
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)

        # Welcome message
        welcome = QLabel("<h2>Welcome to Sonorium!</h2>")
        welcome.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(welcome)

        info = QLabel("Setting up Sonorium for first use.\nDownloading required files from GitHub...")
        info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(info)

        # Progress
        self.progress_label = QLabel("Initializing...")
        self.progress_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.progress_label)

        # Progress bar (simple text-based)
        self.progress_bar = QLabel("[          ] 0%")
        self.progress_bar.setFont(QFont("Consolas", 10))
        self.progress_bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.progress_bar)

        layout.addStretch()

    def start_setup(self):
        """Start the setup process."""
        self.setup_thread = SetupThread(get_app_dir())
        self.setup_thread.progress.connect(self.on_progress)
        self.setup_thread.finished_setup.connect(self.on_finished)
        self.setup_thread.start()

    def on_progress(self, message: str, percent: int):
        """Handle progress update."""
        self.progress_label.setText(message)
        filled = int(percent / 10)
        bar = '=' * filled + ' ' * (10 - filled)
        self.progress_bar.setText(f"[{bar}] {percent}%")

    def on_finished(self, success: bool, message: str):
        """Handle setup finished."""
        if success:
            self.accept()
        else:
            QMessageBox.critical(self, "Setup Failed", message)
            self.reject()


class UpdateCheckThread(QThread):
    """Thread to check for updates."""

    update_available = pyqtSignal(dict)  # Release info dict
    no_update = pyqtSignal()
    error = pyqtSignal(str)

    def _parse_version(self, v: str) -> tuple:
        """Parse version string for comparison.

        Handles versions like "0.1.3-alpha" -> (0, 1, 3, 'alpha')
        Numbers are compared numerically, strings alphabetically.
        """
        parts = v.replace('-', '.').split('.')
        result = []
        for p in parts:
            try:
                result.append(int(p))
            except ValueError:
                result.append(p)
        return tuple(result)

    def run(self):
        """Check GitHub releases for updates."""
        logger = get_logger()
        logger.info(f"Checking for updates (current version: {APP_VERSION})")

        try:
            req = urllib.request.Request(
                RELEASES_API_URL,
                headers={'Accept': 'application/vnd.github.v3+json', 'User-Agent': 'Sonorium-Launcher'}
            )
            with urllib.request.urlopen(req, timeout=15) as response:
                releases = json.loads(response.read().decode('utf-8'))

            logger.debug(f"Found {len(releases)} releases")
            current = APP_VERSION.lstrip('v')
            current_parsed = self._parse_version(current)
            logger.debug(f"Current version parsed: {current_parsed}")

            # Check ALL releases for a newer version (releases are sorted newest first)
            for release in releases:
                if release.get('draft', False):
                    continue

                tag = release.get('tag_name', '').lstrip('v')
                tag_parsed = self._parse_version(tag)
                logger.debug(f"Comparing {tag} ({tag_parsed}) > {current} ({current_parsed})")

                if tag_parsed > current_parsed:
                    # Found a newer version - check if it has Sonorium.exe
                    for asset in release.get('assets', []):
                        if asset.get('name', '').lower() == 'sonorium.exe':
                            logger.info(f"Update available: {tag}")
                            self.update_available.emit({
                                'version': tag,
                                'tag_name': release.get('tag_name'),
                                'name': release.get('name', f'Version {tag}'),
                                'body': release.get('body', ''),
                                'download_url': asset.get('browser_download_url'),
                                'size': asset.get('size', 0),
                                'html_url': release.get('html_url', ''),
                            })
                            return
                    # Has newer version but no Sonorium.exe - continue checking older releases
                    logger.debug(f"Release {tag} has no Sonorium.exe, checking older releases")
                    continue

            # No newer version found with Sonorium.exe
            logger.info("No updates available")
            self.no_update.emit()

        except Exception as e:
            logger.error(f"Update check failed: {e}")
            self.error.emit(str(e))


class UpdateDownloadThread(QThread):
    """Thread to download and apply update."""

    progress = pyqtSignal(int, int)  # downloaded, total
    finished = pyqtSignal(bool, str)  # success, message/path

    def __init__(self, download_url: str, target_path: Path):
        super().__init__()
        self.download_url = download_url
        self.target_path = target_path

    def run(self):
        """Download the update."""
        try:
            req = urllib.request.Request(
                self.download_url,
                headers={'User-Agent': 'Sonorium-Launcher'}
            )

            with urllib.request.urlopen(req, timeout=120) as response:
                total_size = int(response.headers.get('content-length', 0))
                downloaded = 0
                chunk_size = 65536

                with open(self.target_path, 'wb') as f:
                    while True:
                        chunk = response.read(chunk_size)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        self.progress.emit(downloaded, total_size)

            self.finished.emit(True, str(self.target_path))

        except Exception as e:
            self.finished.emit(False, str(e))


class UpdateDialog(QDialog):
    """Dialog showing available update and download progress."""

    def __init__(self, release_info: dict, parent=None):
        super().__init__(parent)
        self.release_info = release_info
        self.download_thread: Optional[UpdateDownloadThread] = None
        self.downloaded_path: Optional[Path] = None
        self.setWindowTitle("Update Available")
        self.setMinimumSize(500, 400)
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)

        # Header
        header = QLabel(f"<h2>Sonorium {self.release_info['tag_name']} Available</h2>")
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(header)

        current_label = QLabel(f"Current version: {APP_VERSION}")
        current_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        current_label.setStyleSheet("color: gray;")
        layout.addWidget(current_label)

        # Release notes
        notes_label = QLabel("What's new:")
        layout.addWidget(notes_label)

        self.notes_text = QTextEdit()
        self.notes_text.setReadOnly(True)
        self.notes_text.setMarkdown(self.release_info.get('body', 'No release notes available.'))
        layout.addWidget(self.notes_text)

        # Download size
        size_mb = self.release_info.get('size', 0) / (1024 * 1024)
        size_label = QLabel(f"Download size: {size_mb:.1f} MB")
        size_label.setStyleSheet("color: gray;")
        layout.addWidget(size_label)

        # Progress bar (hidden initially)
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        self.status_label = QLabel("")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.status_label)

        # Buttons
        button_layout = QHBoxLayout()

        self.download_btn = QPushButton("Download && Install")
        self.download_btn.clicked.connect(self.start_download)
        button_layout.addWidget(self.download_btn)

        self.later_btn = QPushButton("Remind Later")
        self.later_btn.clicked.connect(self.reject)
        button_layout.addWidget(self.later_btn)

        self.skip_btn = QPushButton("Skip This Version")
        self.skip_btn.clicked.connect(self.skip_version)
        button_layout.addWidget(self.skip_btn)

        layout.addLayout(button_layout)

    def start_download(self):
        """Start downloading the update."""
        self.download_btn.setEnabled(False)
        self.later_btn.setEnabled(False)
        self.skip_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.status_label.setText("Downloading update...")

        # Download to temp location
        app_dir = get_app_dir()
        temp_path = app_dir / f'Sonorium_update_{self.release_info["version"]}.exe'

        self.download_thread = UpdateDownloadThread(
            self.release_info['download_url'],
            temp_path
        )
        self.download_thread.progress.connect(self.on_progress)
        self.download_thread.finished.connect(self.on_download_finished)
        self.download_thread.start()

    def on_progress(self, downloaded: int, total: int):
        """Update progress bar."""
        if total > 0:
            self.progress_bar.setMaximum(total)
            self.progress_bar.setValue(downloaded)
            mb_down = downloaded / (1024 * 1024)
            mb_total = total / (1024 * 1024)
            self.status_label.setText(f"Downloading: {mb_down:.1f} / {mb_total:.1f} MB")

    def on_download_finished(self, success: bool, message: str):
        """Handle download completion."""
        if success:
            self.downloaded_path = Path(message)
            self.status_label.setText("Download complete! Click 'Install Now' to apply update.")
            self.download_btn.setText("Install Now")
            self.download_btn.setEnabled(True)
            self.download_btn.clicked.disconnect()
            self.download_btn.clicked.connect(self.install_update)
        else:
            self.status_label.setText(f"Download failed: {message}")
            self.download_btn.setEnabled(True)
            self.later_btn.setEnabled(True)
            self.skip_btn.setEnabled(True)
            self.progress_bar.setVisible(False)

    def install_update(self):
        """Apply the downloaded update using updater.exe."""
        logger = get_logger()

        if not self.downloaded_path or not self.downloaded_path.exists():
            QMessageBox.critical(self, "Error", "Downloaded file not found")
            return

        # Get current EXE path
        if getattr(sys, 'frozen', False):
            current_exe = Path(sys.executable)
        else:
            QMessageBox.information(self, "Dev Mode",
                                  "Update cannot be applied in development mode.\n"
                                  f"Downloaded to: {self.downloaded_path}")
            self.accept()
            return

        app_dir = get_app_dir()
        updater_path = app_dir / 'updater.exe'

        # Check if updater.exe exists, download if not
        if not updater_path.exists():
            logger.info("updater.exe not found, downloading...")
            self.status_label.setText("Downloading updater...")
            QApplication.processEvents()

            if not self._download_updater(updater_path):
                QMessageBox.critical(self, "Error",
                                   "Could not download updater.exe.\n"
                                   "Please download it manually from the releases page.")
                return

        logger.info(f"Launching updater: {updater_path}")
        logger.info(f"  --target {current_exe}")
        logger.info(f"  --update {self.downloaded_path}")

        try:
            # Launch the updater
            subprocess.Popen(
                [
                    str(updater_path),
                    '--target', str(current_exe),
                    '--update', str(self.downloaded_path)
                ],
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
                close_fds=True
            )

            logger.info("Updater launched, exiting application")

            # Signal to quit the application
            self.done(2)  # Special code to indicate restart needed

        except Exception as e:
            logger.exception(f"Failed to launch updater: {e}")
            QMessageBox.critical(self, "Error", f"Failed to launch updater: {e}")

    def _download_updater(self, target_path: Path) -> bool:
        """Download updater.exe from GitHub releases."""
        logger = get_logger()

        try:
            # Get the updater.exe URL from the same release
            req = urllib.request.Request(
                RELEASES_API_URL,
                headers={'Accept': 'application/vnd.github.v3+json', 'User-Agent': 'Sonorium-Launcher'}
            )

            with urllib.request.urlopen(req, timeout=15) as response:
                releases = json.loads(response.read().decode('utf-8'))

            # Find updater.exe in any release
            for release in releases:
                if release.get('draft', False):
                    continue
                for asset in release.get('assets', []):
                    if asset.get('name', '').lower() == 'updater.exe':
                        url = asset.get('browser_download_url')
                        logger.info(f"Downloading updater from: {url}")

                        req = urllib.request.Request(url, headers={'User-Agent': 'Sonorium-Launcher'})
                        with urllib.request.urlopen(req, timeout=60) as resp:
                            with open(target_path, 'wb') as f:
                                f.write(resp.read())

                        logger.info(f"Downloaded updater.exe to {target_path}")
                        return True

            logger.error("updater.exe not found in any release")
            return False

        except Exception as e:
            logger.exception(f"Failed to download updater: {e}")
            return False

    def skip_version(self):
        """Mark this version as skipped."""
        # Save to config
        config = load_config()
        config['skipped_version'] = self.release_info['version']
        save_config(config)
        self.reject()


class SettingsDialog(QDialog):
    """Settings dialog window."""

    def __init__(self, config: dict, parent=None):
        super().__init__(parent)
        self.config = config.copy()
        self.setWindowTitle("Settings")
        self.setMinimumWidth(400)
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)

        # Tabs
        tabs = QTabWidget()

        # General tab
        general_tab = QWidget()
        general_layout = QVBoxLayout(general_tab)

        startup_group = QGroupBox("Startup")
        startup_layout = QFormLayout()

        self.start_minimized = QCheckBox()
        self.start_minimized.setChecked(self.config.get('start_minimized', False))
        startup_layout.addRow("Start minimized:", self.start_minimized)

        self.minimize_to_tray = QCheckBox()
        self.minimize_to_tray.setChecked(self.config.get('minimize_to_tray', True))
        startup_layout.addRow("Minimize to tray:", self.minimize_to_tray)

        self.auto_start_server = QCheckBox()
        self.auto_start_server.setChecked(self.config.get('auto_start_server', True))
        startup_layout.addRow("Auto-start server:", self.auto_start_server)

        startup_group.setLayout(startup_layout)
        general_layout.addWidget(startup_group)

        server_group = QGroupBox("Server")
        server_layout = QFormLayout()

        self.port_spin = QSpinBox()
        self.port_spin.setRange(1024, 65535)
        self.port_spin.setValue(self.config.get('server_port', DEFAULT_PORT))
        server_layout.addRow("Port:", self.port_spin)

        server_group.setLayout(server_layout)
        general_layout.addWidget(server_group)

        general_layout.addStretch()
        tabs.addTab(general_tab, "General")

        # Updates tab
        updates_tab = QWidget()
        updates_layout = QVBoxLayout(updates_tab)

        updates_group = QGroupBox("Updates")
        updates_form = QFormLayout()

        self.check_updates = QCheckBox()
        self.check_updates.setChecked(self.config.get('check_updates_on_startup', True))
        updates_form.addRow("Check on startup:", self.check_updates)

        updates_group.setLayout(updates_form)
        updates_layout.addWidget(updates_group)
        updates_layout.addStretch()
        tabs.addTab(updates_tab, "Updates")

        # Advanced tab
        advanced_tab = QWidget()
        advanced_layout = QVBoxLayout(advanced_tab)

        folders_group = QGroupBox("Folders")
        folders_layout = QVBoxLayout()

        open_app_btn = QPushButton("Open App Folder")
        open_app_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(get_app_dir()))))
        folders_layout.addWidget(open_app_btn)

        open_config_btn = QPushButton("Open Config Folder")
        open_config_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(get_app_dir() / 'config'))))
        folders_layout.addWidget(open_config_btn)

        open_logs_btn = QPushButton("Open Logs Folder")
        open_logs_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(get_app_dir() / 'logs'))))
        folders_layout.addWidget(open_logs_btn)

        open_themes_btn = QPushButton("Open Themes Folder")
        open_themes_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(get_app_dir() / 'themes'))))
        folders_layout.addWidget(open_themes_btn)

        folders_group.setLayout(folders_layout)
        advanced_layout.addWidget(folders_group)
        advanced_layout.addStretch()
        tabs.addTab(advanced_tab, "Advanced")

        layout.addWidget(tabs)

        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self.accept)
        button_layout.addWidget(save_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)

        layout.addLayout(button_layout)

    def get_config(self) -> dict:
        """Get updated config from dialog."""
        self.config['start_minimized'] = self.start_minimized.isChecked()
        self.config['minimize_to_tray'] = self.minimize_to_tray.isChecked()
        self.config['auto_start_server'] = self.auto_start_server.isChecked()
        self.config['server_port'] = self.port_spin.value()
        self.config['check_updates_on_startup'] = self.check_updates.isChecked()
        return self.config


class AboutDialog(QDialog):
    """About dialog window."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"About {APP_NAME}")
        self.setFixedSize(350, 250)
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Logo - check multiple locations
        logo_path = None
        for path in [get_app_dir() / 'core' / 'logo.png',
                     get_app_dir() / 'logo.png',
                     Path(getattr(sys, '_MEIPASS', '')) / 'logo.png']:
            if path.exists():
                logo_path = path
                break

        if logo_path:
            logo_label = QLabel()
            pixmap = QPixmap(str(logo_path)).scaled(100, 100, Qt.AspectRatioMode.KeepAspectRatio)
            logo_label.setPixmap(pixmap)
            logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(logo_label)

        # Title
        title_label = QLabel(f"<h2>{APP_NAME}</h2>")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title_label)

        # Version
        version_label = QLabel(f"Version {APP_VERSION}")
        version_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(version_label)

        # Description
        desc_label = QLabel("Ambient Soundscape Mixer")
        desc_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(desc_label)

        # GitHub link
        github_btn = QPushButton("GitHub")
        github_btn.clicked.connect(lambda: webbrowser.open(REPO_URL))
        layout.addWidget(github_btn)

        # Close button
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)


class MainWindow(QMainWindow):
    """Main application window."""

    def __init__(self):
        super().__init__()
        self.logger = get_logger()
        self.config = load_config()
        self.server_thread: Optional[ServerThread] = None
        self.server_running = False
        self.update_check_thread: Optional[UpdateCheckThread] = None

        self.logger.info("MainWindow initializing...")
        self.setup_ui()
        self.logger.debug("UI setup complete")
        self.setup_tray()
        self.logger.debug("Tray setup complete")

        # Auto-start server if configured
        if self.config.get('auto_start_server', True):
            self.logger.info("Auto-start server enabled, scheduling start")
            QTimer.singleShot(500, self.start_server)

        # Start minimized if configured
        if self.config.get('start_minimized', False):
            if self.config.get('minimize_to_tray', True):
                self.logger.info("Start minimized to tray")
                QTimer.singleShot(100, self.hide)
            else:
                self.logger.info("Start minimized to taskbar")
                QTimer.singleShot(100, self.showMinimized)

        # Check for updates after a short delay
        if self.config.get('check_updates_on_startup', True):
            self.logger.info("Scheduling update check")
            QTimer.singleShot(3000, self.check_for_updates)

        self.logger.info("MainWindow initialization complete")

    def check_for_updates(self, silent: bool = True):
        """Check for updates from GitHub."""
        self.update_check_thread = UpdateCheckThread()
        self.update_check_thread.update_available.connect(
            lambda info: self.on_update_available(info, silent)
        )
        self.update_check_thread.no_update.connect(
            lambda: self.on_no_update(silent)
        )
        self.update_check_thread.error.connect(
            lambda err: self.on_update_error(err, silent)
        )
        self.update_check_thread.start()

    def on_update_available(self, release_info: dict, silent: bool):
        """Handle update available."""
        # Check if user skipped this version
        skipped = self.config.get('skipped_version', '')
        if skipped == release_info['version']:
            self.append_log(f"Update {release_info['version']} available but skipped by user")
            return

        self.append_log(f"Update available: {release_info['tag_name']}")

        # Show update dialog
        dialog = UpdateDialog(release_info, self)
        result = dialog.exec()

        if result == 2:  # Special code for restart
            self.quit_app()

    def on_no_update(self, silent: bool):
        """Handle no update available."""
        if not silent:
            QMessageBox.information(self, "No Updates",
                                  f"You're running the latest version ({APP_VERSION}).")
        self.append_log(f"No updates available (current: {APP_VERSION})")

    def on_update_error(self, error: str, silent: bool):
        """Handle update check error."""
        if not silent:
            QMessageBox.warning(self, "Update Check Failed",
                              f"Could not check for updates:\n{error}")
        self.append_log(f"Update check failed: {error}")

    def setup_ui(self):
        """Set up the main window UI."""
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(600, 450)

        # Set window icon
        icon = get_icon()
        if icon:
            self.setWindowIcon(icon)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)

        # Status section
        status_layout = QHBoxLayout()

        self.status_indicator = QLabel("●")
        self.status_indicator.setStyleSheet("color: gray; font-size: 20px;")
        status_layout.addWidget(self.status_indicator)

        self.status_label = QLabel("Status: Stopped")
        self.status_label.setFont(QFont("Segoe UI", 11))
        status_layout.addWidget(self.status_label)

        status_layout.addStretch()
        layout.addLayout(status_layout)

        # Web UI section
        web_layout = QHBoxLayout()

        port = self.config.get('server_port', DEFAULT_PORT)
        self.web_url_label = QLabel(f"Web UI: http://localhost:{port}")
        web_layout.addWidget(self.web_url_label)

        self.open_browser_btn = QPushButton("Open in Browser")
        self.open_browser_btn.clicked.connect(self.open_web_ui)
        self.open_browser_btn.setEnabled(False)
        web_layout.addWidget(self.open_browser_btn)

        web_layout.addStretch()
        layout.addLayout(web_layout)

        # Log output
        log_label = QLabel("Log Output:")
        layout.addWidget(log_label)

        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setFont(QFont("Consolas", 9))
        self.log_output.setStyleSheet("background-color: #1e1e1e; color: #d4d4d4;")
        layout.addWidget(self.log_output)

        # Buttons
        button_layout = QHBoxLayout()

        self.start_stop_btn = QPushButton("Start Server")
        self.start_stop_btn.clicked.connect(self.toggle_server)
        button_layout.addWidget(self.start_stop_btn)

        settings_btn = QPushButton("Settings")
        settings_btn.clicked.connect(self.show_settings)
        button_layout.addWidget(settings_btn)

        button_layout.addStretch()
        layout.addLayout(button_layout)

        # Footer with version
        footer_layout = QHBoxLayout()

        self.version_label = QLabel(f"v{APP_VERSION}")
        self.version_label.setStyleSheet("color: gray;")
        footer_layout.addWidget(self.version_label)

        # App directory info
        app_dir_label = QLabel(f"App: {get_app_dir()}")
        app_dir_label.setStyleSheet("color: gray; font-size: 9px;")
        footer_layout.addWidget(app_dir_label)

        footer_layout.addStretch()
        layout.addLayout(footer_layout)

    def setup_tray(self):
        """Set up system tray icon."""
        self.tray_icon = QSystemTrayIcon(self)

        # Set tray icon
        icon = get_icon()
        if icon:
            self.tray_icon.setIcon(icon)
        else:
            self.tray_icon.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon))

        # Tray menu
        tray_menu = QMenu()

        # Status item (non-clickable)
        self.tray_status_action = QAction("● Sonorium Stopped", self)
        self.tray_status_action.setEnabled(False)
        tray_menu.addAction(self.tray_status_action)

        tray_menu.addSeparator()

        # Open Sonorium (native window)
        open_action = QAction("Open Sonorium", self)
        open_action.triggered.connect(self.show_and_activate)
        tray_menu.addAction(open_action)

        # Open Web UI
        web_ui_action = QAction("Open Web UI", self)
        web_ui_action.triggered.connect(self.open_web_ui)
        tray_menu.addAction(web_ui_action)

        tray_menu.addSeparator()

        # Settings
        settings_action = QAction("Settings...", self)
        settings_action.triggered.connect(self.show_settings)
        tray_menu.addAction(settings_action)

        tray_menu.addSeparator()

        # Help
        help_action = QAction("Help (Wiki)", self)
        help_action.triggered.connect(lambda: webbrowser.open(WIKI_URL))
        tray_menu.addAction(help_action)

        # Check for Updates
        update_action = QAction("Check for Updates...", self)
        update_action.triggered.connect(lambda: self.check_for_updates(silent=False))
        tray_menu.addAction(update_action)

        # About
        about_action = QAction("About", self)
        about_action.triggered.connect(self.show_about)
        tray_menu.addAction(about_action)

        tray_menu.addSeparator()

        # Exit
        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self.quit_app)
        tray_menu.addAction(exit_action)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self.tray_activated)
        self.tray_icon.show()

    def tray_activated(self, reason):
        """Handle tray icon activation."""
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            self.show_and_activate()

    def show_and_activate(self):
        """Show and activate the main window."""
        self.show()
        self.activateWindow()
        self.raise_()

    def toggle_server(self):
        """Start or stop the server."""
        if self.server_running:
            self.stop_server()
        else:
            self.start_server()

    def start_server(self):
        """Start the server."""
        self.logger.info("start_server() called")
        if self.server_thread and self.server_thread.isRunning():
            self.logger.debug("Server already running, ignoring start request")
            return

        port = self.config.get('server_port', DEFAULT_PORT)
        self.logger.info(f"Starting server on port {port}")
        self.server_thread = ServerThread(port)
        self.server_thread.output_received.connect(self.append_log)
        self.server_thread.server_started.connect(self.on_server_started)
        self.server_thread.server_stopped.connect(self.on_server_stopped)
        self.server_thread.error_occurred.connect(self.on_server_error)
        self.server_thread.start()
        self.logger.debug("ServerThread started")

        self.start_stop_btn.setText("Starting...")
        self.start_stop_btn.setEnabled(False)

    def stop_server(self):
        """Stop the server."""
        self.logger.info("stop_server() called")
        if self.server_thread:
            self.append_log("Stopping server...")
            self.logger.info("Requesting server stop...")
            self.server_thread.stop()
            self.server_thread.wait(5000)
            self.logger.info("Server stop completed")

    def on_server_started(self):
        """Handle server started."""
        self.logger.info("Server started successfully")
        self.server_running = True
        self.start_stop_btn.setText("Stop Server")
        self.start_stop_btn.setEnabled(True)
        self.open_browser_btn.setEnabled(True)
        self.status_indicator.setStyleSheet("color: #4caf50; font-size: 20px;")
        self.status_label.setText("Status: Running")
        self.tray_status_action.setText("● Sonorium Running")
        self.tray_icon.setToolTip(f"Sonorium - Running on port {self.config.get('server_port', DEFAULT_PORT)}")

    def on_server_stopped(self):
        """Handle server stopped."""
        self.logger.info("Server stopped")
        self.server_running = False
        self.start_stop_btn.setText("Start Server")
        self.start_stop_btn.setEnabled(True)
        self.open_browser_btn.setEnabled(False)
        self.status_indicator.setStyleSheet("color: gray; font-size: 20px;")
        self.status_label.setText("Status: Stopped")
        self.tray_status_action.setText("● Sonorium Stopped")
        self.tray_icon.setToolTip("Sonorium - Stopped")

    def on_server_error(self, error: str):
        """Handle server error."""
        self.logger.error(f"Server error: {error}")
        self.append_log(f"ERROR: {error}")
        self.start_stop_btn.setText("Start Server")
        self.start_stop_btn.setEnabled(True)
        QMessageBox.critical(self, "Server Error", error)

    def append_log(self, text: str):
        """Append text to log output."""
        self.log_output.append(text)
        # Auto-scroll to bottom
        scrollbar = self.log_output.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def open_web_ui(self):
        """Open the web UI in browser."""
        port = self.config.get('server_port', DEFAULT_PORT)
        webbrowser.open(f"http://localhost:{port}")

    def show_settings(self):
        """Show settings dialog."""
        dialog = SettingsDialog(self.config, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.config = dialog.get_config()
            save_config(self.config)

            # Update UI
            port = self.config.get('server_port', DEFAULT_PORT)
            self.web_url_label.setText(f"Web UI: http://localhost:{port}")

    def show_about(self):
        """Show about dialog."""
        dialog = AboutDialog(self)
        dialog.exec()

    def closeEvent(self, event):
        """Handle window close event."""
        if self.config.get('minimize_to_tray', True):
            event.ignore()
            self.hide()
        else:
            self.quit_app()

    def quit_app(self):
        """Quit the application."""
        self.stop_server()
        self.tray_icon.hide()
        QApplication.quit()


def get_icon() -> Optional[QIcon]:
    """Get the application icon from available locations."""
    for path in [get_app_dir() / 'core' / 'icon.png',
                 get_app_dir() / 'icon.png',
                 Path(getattr(sys, '_MEIPASS', '')) / 'icon.png']:
        if path.exists():
            return QIcon(str(path))
    return None


def main():
    """Main entry point."""
    # Initialize logging FIRST - before anything else
    logger = setup_logging()

    try:
        logger.info("Creating QApplication...")
        app = QApplication(sys.argv)
        app.setApplicationName(APP_NAME)
        app.setApplicationVersion(APP_VERSION)

        # Set style
        app.setStyle("Fusion")
        logger.info("QApplication created successfully")

        # Check if first run - need to download core files
        if is_first_run():
            logger.info("First run detected - starting setup")
            setup_dialog = SetupDialog()
            setup_dialog.show()
            setup_dialog.start_setup()

            if setup_dialog.exec() != QDialog.DialogCode.Accepted:
                # Setup failed or cancelled
                logger.error("Setup failed or was cancelled")
                QMessageBox.critical(None, "Setup Failed",
                                   "Sonorium setup was not completed.\n\n"
                                   "Please check your internet connection and try again.")
                sys.exit(1)
            logger.info("Setup completed successfully")
        else:
            logger.info("Existing installation detected")

        # Set application-wide icon (now that core is extracted)
        icon = get_icon()
        if icon:
            app.setWindowIcon(icon)
            logger.debug("Application icon set")
        else:
            logger.warning("No application icon found")

        # Create and show main window
        logger.info("Creating main window...")
        window = MainWindow()
        logger.info("Main window created")

        # Show window unless start minimized
        config = load_config()
        if not config.get('start_minimized', False):
            window.show()
            logger.info("Main window shown")
        else:
            logger.info("Starting minimized (per config)")

        logger.info("Entering Qt event loop")
        exit_code = app.exec()
        logger.info(f"Application exiting with code {exit_code}")
        sys.exit(exit_code)

    except Exception as e:
        logger.exception(f"Fatal error in main(): {e}")
        # Try to show error dialog
        try:
            QMessageBox.critical(None, "Fatal Error",
                               f"Sonorium encountered a fatal error:\n\n{e}\n\n"
                               f"Check logs folder for details.")
        except:
            pass
        sys.exit(1)


if __name__ == '__main__':
    main()
