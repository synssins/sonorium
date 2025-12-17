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
import os
import subprocess
import sys
import webbrowser
import zipfile
import shutil
import urllib.request
import urllib.error
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
APP_VERSION = "0.1.1-alpha"
DEFAULT_PORT = 8008
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

    def _get_download_url(self) -> str:
        """Get the core.zip download URL from GitHub Releases API."""
        try:
            req = urllib.request.Request(
                RELEASES_API_URL,
                headers={'Accept': 'application/vnd.github.v3+json', 'User-Agent': 'Sonorium-Launcher'}
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                releases = json.loads(response.read().decode('utf-8'))
                # Get the first (most recent) release that has core.zip
                for release in releases:
                    for asset in release.get('assets', []):
                        if asset.get('name') == 'core.zip':
                            return asset.get('browser_download_url')
        except Exception:
            pass
        # Fallback to direct release URL
        return CORE_ZIP_FALLBACK

    def run(self):
        """Download and extract core files from GitHub Releases."""
        try:
            self.progress.emit("Creating folder structure...", 5)
            create_folder_structure()

            self.progress.emit("Finding latest release...", 10)
            download_url = self._get_download_url()

            self.progress.emit("Downloading core files...", 15)

            # Download core.zip from releases
            zip_path = self.app_dir / 'core_download.zip'
            try:
                req = urllib.request.Request(download_url, headers={'User-Agent': 'Sonorium-Launcher'})
                with urllib.request.urlopen(req, timeout=60) as response:
                    with open(zip_path, 'wb') as f:
                        f.write(response.read())
            except urllib.error.URLError as e:
                self.finished_setup.emit(False, f"Failed to download: {e}")
                return

            self.progress.emit("Extracting files...", 60)

            # Extract the archive - core.zip contains core/ and themes/ at root level
            with zipfile.ZipFile(zip_path, 'r') as zf:
                zf.extractall(self.app_dir)

            self.progress.emit("Verifying installation...", 85)

            # Verify core was extracted
            main_script = self.app_dir / 'core' / 'sonorium' / 'main.py'
            if not main_script.exists():
                self.finished_setup.emit(False, "Core files not found after extraction")
                return

            self.progress.emit("Cleaning up...", 95)

            # Clean up temp files
            zip_path.unlink(missing_ok=True)

            self.progress.emit("Setup complete!", 100)
            self.finished_setup.emit(True, "Setup completed successfully")

        except Exception as e:
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
        self.process: Optional[QProcess] = None
        self._stop_requested = False

    def run(self):
        """Run the server process."""
        self.process = QProcess()
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._handle_output)
        self.process.finished.connect(self._handle_finished)

        # Set up environment
        env = self.process.processEnvironment()
        if env.isEmpty():
            env = QProcess.systemEnvironment()

        core_dir = get_core_dir()
        env.insert('PYTHONPATH', str(core_dir))
        self.process.setProcessEnvironment(env)

        # Find Python executable
        app_dir = get_app_dir()
        if getattr(sys, 'frozen', False):
            # Try embedded Python first
            python_exe = app_dir / 'python' / 'python.exe'
            if not python_exe.exists():
                # Fallback to system Python
                python_exe = 'python'
        else:
            python_exe = sys.executable

        # Start the server
        main_script = core_dir / 'sonorium' / 'main.py'

        if not main_script.exists():
            self.error_occurred.emit(f"Core not found: {main_script}")
            return

        args = [str(main_script), '--no-tray', '--no-browser', '--port', str(self.port)]

        self.output_received.emit(f"Starting server on port {self.port}...")
        self.process.start(str(python_exe), args)

        if not self.process.waitForStarted(5000):
            self.error_occurred.emit("Failed to start server process")
            return

        self.server_started.emit()

        # Wait for process to finish
        while not self._stop_requested and self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.waitForFinished(100)

        self.server_stopped.emit()

    def _handle_output(self):
        """Handle output from server process."""
        if self.process:
            data = self.process.readAllStandardOutput().data().decode('utf-8', errors='replace')
            for line in data.strip().split('\n'):
                if line:
                    self.output_received.emit(line)

    def _handle_finished(self, exit_code, exit_status):
        """Handle server process finished."""
        self.output_received.emit(f"Server stopped (exit code: {exit_code})")

    def stop(self):
        """Stop the server process."""
        self._stop_requested = True
        if self.process and self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.terminate()
            if not self.process.waitForFinished(3000):
                self.process.kill()


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

    def run(self):
        """Check GitHub releases for updates."""
        try:
            req = urllib.request.Request(
                RELEASES_API_URL,
                headers={'Accept': 'application/vnd.github.v3+json', 'User-Agent': 'Sonorium-Launcher'}
            )
            with urllib.request.urlopen(req, timeout=15) as response:
                releases = json.loads(response.read().decode('utf-8'))

            # Find first non-draft release
            for release in releases:
                if release.get('draft', False):
                    continue

                tag = release.get('tag_name', '').lstrip('v')
                current = APP_VERSION.lstrip('v')

                # Parse versions for comparison
                def parse_ver(v):
                    # Handle versions like "0.1.0-alpha" -> (0, 1, 0, 'alpha')
                    parts = v.replace('-', '.').split('.')
                    result = []
                    for p in parts:
                        try:
                            result.append(int(p))
                        except ValueError:
                            result.append(p)
                    return tuple(result)

                if parse_ver(tag) > parse_ver(current):
                    # Find Sonorium.exe asset
                    for asset in release.get('assets', []):
                        if asset.get('name', '').lower() == 'sonorium.exe':
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

                # If we get here, current version is up to date
                self.no_update.emit()
                return

            self.no_update.emit()

        except Exception as e:
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
        """Apply the downloaded update."""
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

        # Create batch script to replace EXE after we exit
        app_dir = get_app_dir()
        batch_path = app_dir / 'update.bat'

        batch_script = f'''@echo off
echo Waiting for Sonorium to close...
timeout /t 2 /nobreak > nul
echo Applying update...
move /y "{self.downloaded_path}" "{current_exe}"
if errorlevel 1 (
    echo Update failed! Press any key to exit.
    pause
    exit /b 1
)
echo Update complete! Restarting Sonorium...
start "" "{current_exe}"
del "%~f0"
'''

        try:
            with open(batch_path, 'w') as f:
                f.write(batch_script)

            # Launch the batch script
            subprocess.Popen(
                ['cmd', '/c', str(batch_path)],
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
                close_fds=True
            )

            # Signal to quit the application
            self.done(2)  # Special code to indicate restart needed

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to apply update: {e}")

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
        self.config = load_config()
        self.server_thread: Optional[ServerThread] = None
        self.server_running = False
        self.update_check_thread: Optional[UpdateCheckThread] = None

        self.setup_ui()
        self.setup_tray()

        # Auto-start server if configured
        if self.config.get('auto_start_server', True):
            QTimer.singleShot(500, self.start_server)

        # Start minimized if configured
        if self.config.get('start_minimized', False):
            if self.config.get('minimize_to_tray', True):
                QTimer.singleShot(100, self.hide)
            else:
                QTimer.singleShot(100, self.showMinimized)

        # Check for updates after a short delay
        if self.config.get('check_updates_on_startup', True):
            QTimer.singleShot(3000, self.check_for_updates)

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
        if self.server_thread and self.server_thread.isRunning():
            return

        port = self.config.get('server_port', DEFAULT_PORT)
        self.server_thread = ServerThread(port)
        self.server_thread.output_received.connect(self.append_log)
        self.server_thread.server_started.connect(self.on_server_started)
        self.server_thread.server_stopped.connect(self.on_server_stopped)
        self.server_thread.error_occurred.connect(self.on_server_error)
        self.server_thread.start()

        self.start_stop_btn.setText("Starting...")
        self.start_stop_btn.setEnabled(False)

    def stop_server(self):
        """Stop the server."""
        if self.server_thread:
            self.append_log("Stopping server...")
            self.server_thread.stop()
            self.server_thread.wait(5000)

    def on_server_started(self):
        """Handle server started."""
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
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)

    # Set style
    app.setStyle("Fusion")

    # Check if first run - need to download core files
    if is_first_run():
        setup_dialog = SetupDialog()
        setup_dialog.show()
        setup_dialog.start_setup()

        if setup_dialog.exec() != QDialog.DialogCode.Accepted:
            # Setup failed or cancelled
            QMessageBox.critical(None, "Setup Failed",
                               "Sonorium setup was not completed.\n\n"
                               "Please check your internet connection and try again.")
            sys.exit(1)

    # Set application-wide icon (now that core is extracted)
    icon = get_icon()
    if icon:
        app.setWindowIcon(icon)

    # Create and show main window
    window = MainWindow()

    # Show window unless start minimized
    config = load_config()
    if not config.get('start_minimized', False):
        window.show()

    sys.exit(app.exec())


if __name__ == '__main__':
    main()
