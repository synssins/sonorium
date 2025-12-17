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
    QTabWidget, QGroupBox, QMessageBox, QStyle, QProgressDialog
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QProcess, QUrl
from PyQt6.QtGui import QIcon, QPixmap, QAction, QDesktopServices, QFont


# Constants
APP_NAME = "Sonorium"
APP_VERSION = "1.0.0"
DEFAULT_PORT = 8008
WIKI_URL = "https://github.com/synssins/sonorium/wiki"
REPO_URL = "https://github.com/synssins/sonorium"
REPO_ARCHIVE_URL = "https://github.com/synssins/sonorium/archive/refs/heads/dev.zip"

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
    """Thread to handle first-run setup (downloading core files)."""

    progress = pyqtSignal(str, int)  # message, percentage
    finished_setup = pyqtSignal(bool, str)  # success, message

    def __init__(self, app_dir: Path):
        super().__init__()
        self.app_dir = app_dir

    def run(self):
        """Download and extract core files from repo."""
        try:
            self.progress.emit("Creating folder structure...", 5)
            create_folder_structure()

            self.progress.emit("Downloading core files from GitHub...", 10)

            # Download the repo archive
            zip_path = self.app_dir / 'temp_download.zip'
            try:
                urllib.request.urlretrieve(REPO_ARCHIVE_URL, str(zip_path))
            except urllib.error.URLError as e:
                self.finished_setup.emit(False, f"Failed to download: {e}")
                return

            self.progress.emit("Extracting files...", 50)

            # Extract the archive
            extract_dir = self.app_dir / 'temp_extract'
            with zipfile.ZipFile(zip_path, 'r') as zf:
                zf.extractall(extract_dir)

            self.progress.emit("Installing core files...", 70)

            # Find the extracted folder (usually sonorium-dev/)
            extracted_folders = list(extract_dir.iterdir())
            if not extracted_folders:
                self.finished_setup.emit(False, "Downloaded archive is empty")
                return

            repo_root = extracted_folders[0]

            # Copy app/core contents to our core folder
            src_core = repo_root / 'app' / 'core'
            if src_core.exists():
                dst_core = self.app_dir / 'core'
                if dst_core.exists():
                    shutil.rmtree(dst_core)
                shutil.copytree(src_core, dst_core)
            else:
                self.finished_setup.emit(False, "Core folder not found in downloaded archive")
                return

            # Copy default themes if available
            src_themes = repo_root / 'app' / 'themes'
            if src_themes.exists():
                dst_themes = self.app_dir / 'themes'
                for theme in src_themes.iterdir():
                    if theme.is_dir():
                        dst_theme = dst_themes / theme.name
                        if not dst_theme.exists():
                            shutil.copytree(theme, dst_theme)

            self.progress.emit("Cleaning up...", 90)

            # Clean up temp files
            zip_path.unlink(missing_ok=True)
            shutil.rmtree(extract_dir, ignore_errors=True)

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

    def setup_ui(self):
        """Set up the main window UI."""
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(600, 450)

        # Set window icon - check multiple locations
        icon_path = None
        for path in [get_app_dir() / 'core' / 'icon.png',
                     get_app_dir() / 'icon.png',
                     Path(getattr(sys, '_MEIPASS', '')) / 'icon.png']:
            if path.exists():
                icon_path = path
                break

        if icon_path:
            self.setWindowIcon(QIcon(str(icon_path)))

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

        # Find icon
        icon_path = None
        for path in [get_app_dir() / 'core' / 'icon.png',
                     get_app_dir() / 'icon.png',
                     Path(getattr(sys, '_MEIPASS', '')) / 'icon.png']:
            if path.exists():
                icon_path = path
                break

        if icon_path:
            self.tray_icon.setIcon(QIcon(str(icon_path)))
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
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
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

    # Create and show main window
    window = MainWindow()

    # Show window unless start minimized
    config = load_config()
    if not config.get('start_minimized', False):
        window.show()

    sys.exit(app.exec())


if __name__ == '__main__':
    main()
