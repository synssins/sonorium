"""
Sonorium Theme Cycle Manager

Background task that monitors playing sessions and automatically
cycles themes based on their cycle configuration.
"""

from __future__ import annotations

import asyncio
import random
from datetime import datetime, timedelta
from typing import Optional, TYPE_CHECKING

from sonorium.obs import logger
from sonorium.core.state import Session, CycleConfig

if TYPE_CHECKING:
    from sonorium.core.session_manager import SessionManager
    from sonorium.theme import ThemeDefinition
    from fmtr.tools.iterator_tools import IndexList


class CycleManager:
    """
    Manages automatic theme cycling for sessions.
    
    Runs a background task that checks playing sessions and
    triggers theme changes based on their cycle configuration.
    """
    
    def __init__(
        self,
        session_manager: SessionManager = None,
        themes: IndexList[ThemeDefinition] = None,
        check_interval: float = 10.0,  # seconds between checks
    ):
        """
        Initialize CycleManager.
        
        Args:
            session_manager: SessionManager for accessing sessions
            themes: List of available themes
            check_interval: How often to check for needed cycles (seconds)
        """
        self.session_manager = session_manager
        self.themes = themes
        self.check_interval = check_interval
        
        self._task: Optional[asyncio.Task] = None
        self._running = False
        
        # Track cycle state per session (session_id -> runtime state)
        self._cycle_state: dict[str, dict] = {}
    
    def set_session_manager(self, session_manager: SessionManager):
        """Set the session manager (for deferred initialization)."""
        self.session_manager = session_manager
    
    def set_themes(self, themes: IndexList[ThemeDefinition]):
        """Set available themes."""
        self.themes = themes
    
    async def start(self):
        """Start the background cycle task."""
        if self._running:
            return
        
        self._running = True
        self._task = asyncio.create_task(self._cycle_loop())
        logger.info("CycleManager: Started background cycle task")
    
    async def stop(self):
        """Stop the background cycle task."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("CycleManager: Stopped background cycle task")
    
    async def _cycle_loop(self):
        """Main cycle loop - runs until stopped."""
        while self._running:
            try:
                await self._check_cycles()
            except Exception as e:
                logger.error(f"CycleManager: Error in cycle loop: {e}")
            
            await asyncio.sleep(self.check_interval)
    
    async def _check_cycles(self):
        """Check all playing sessions for needed theme cycles."""
        if not self.session_manager:
            return
        
        now = datetime.utcnow()
        
        for session in self.session_manager.list():
            if not session.is_playing:
                # Clean up state for non-playing sessions
                self._cycle_state.pop(session.id, None)
                continue
            
            if not session.cycle_config or not session.cycle_config.enabled:
                continue
            
            # Check if it's time to cycle
            if self._should_cycle(session, now):
                await self._cycle_theme(session)
    
    def _should_cycle(self, session: Session, now: datetime) -> bool:
        """
        Determine if a session should cycle to the next theme.
        
        Returns True if enough time has passed since last change.
        """
        state = self._get_cycle_state(session)
        
        if state['last_change'] is None:
            # First time - initialize and don't cycle yet
            state['last_change'] = now
            return False
        
        interval = timedelta(minutes=session.cycle_config.interval_minutes)
        next_change = state['last_change'] + interval
        
        return now >= next_change
    
    def _get_cycle_state(self, session: Session) -> dict:
        """Get or create runtime cycle state for a session."""
        if session.id not in self._cycle_state:
            self._cycle_state[session.id] = {
                'last_change': None,
                'current_index': 0,
                'shuffled_themes': None,
            }
        return self._cycle_state[session.id]
    
    def _get_theme_list(self, session: Session) -> list[str]:
        """
        Get the list of theme IDs to cycle through.
        
        If cycle_config.theme_ids is set, use that list.
        Otherwise, use all available themes.
        """
        if session.cycle_config.theme_ids:
            # Use configured theme list
            return session.cycle_config.theme_ids
        
        # Use all available themes
        if self.themes:
            return [t.id for t in self.themes]
        
        return []
    
    def _get_next_theme(self, session: Session) -> Optional[str]:
        """
        Get the next theme ID for a session.
        
        Handles both sequential and random cycling.
        """
        state = self._get_cycle_state(session)
        theme_list = self._get_theme_list(session)
        
        if not theme_list:
            return None
        
        # Filter out current theme to avoid repeating
        available = [t for t in theme_list if t != session.theme_id]
        if not available:
            # Only one theme - nothing to cycle to
            return None
        
        if session.cycle_config.randomize:
            # Random selection
            return random.choice(available)
        else:
            # Sequential - move to next index
            current_index = state.get('current_index', 0)
            
            # Find current theme in list
            try:
                if session.theme_id in theme_list:
                    current_pos = theme_list.index(session.theme_id)
                    next_pos = (current_pos + 1) % len(theme_list)
                else:
                    next_pos = current_index
            except ValueError:
                next_pos = 0
            
            state['current_index'] = next_pos
            return theme_list[next_pos]
    
    async def _cycle_theme(self, session: Session):
        """
        Cycle a session to its next theme.
        
        Uses SessionManager.update() to trigger crossfade.
        """
        next_theme_id = self._get_next_theme(session)
        
        if not next_theme_id:
            logger.debug(f"CycleManager: No next theme for session {session.id}")
            return
        
        if next_theme_id == session.theme_id:
            logger.debug(f"CycleManager: Same theme, skipping cycle for {session.id}")
            return
        
        # Get theme name for logging
        theme_name = next_theme_id
        if self.themes:
            theme = self.themes.id.get(next_theme_id)
            if theme:
                theme_name = theme.name
        
        logger.info(f"CycleManager: Cycling session '{session.name}' to theme '{theme_name}'")
        
        # Update session - this triggers crossfade via SessionManager
        self.session_manager.update(session.id, theme_id=next_theme_id)
        
        # Update cycle state
        state = self._get_cycle_state(session)
        state['last_change'] = datetime.utcnow()
    
    def reset_cycle(self, session_id: str):
        """
        Reset the cycle timer for a session.
        
        Call this when manually changing themes to restart the interval.
        """
        if session_id in self._cycle_state:
            self._cycle_state[session_id]['last_change'] = datetime.utcnow()
        else:
            self._cycle_state[session_id] = {
                'last_change': datetime.utcnow(),
                'current_index': 0,
                'shuffled_themes': None,
            }
    
    def get_cycle_status(self, session_id: str) -> Optional[dict]:
        """
        Get the current cycle status for a session.
        
        Returns dict with:
        - next_change: ISO timestamp of next scheduled change
        - seconds_until_change: Seconds until next change
        - themes_in_rotation: Number of themes being cycled
        """
        session = self.session_manager.get(session_id) if self.session_manager else None
        if not session or not session.cycle_config or not session.cycle_config.enabled:
            return None
        
        state = self._cycle_state.get(session_id)
        if not state or not state.get('last_change'):
            return {
                'next_change': None,
                'seconds_until_change': None,
                'themes_in_rotation': len(self._get_theme_list(session)),
            }
        
        interval = timedelta(minutes=session.cycle_config.interval_minutes)
        next_change = state['last_change'] + interval
        now = datetime.utcnow()
        seconds_until = max(0, (next_change - now).total_seconds())
        
        return {
            'next_change': next_change.isoformat(),
            'seconds_until_change': int(seconds_until),
            'themes_in_rotation': len(self._get_theme_list(session)),
        }
