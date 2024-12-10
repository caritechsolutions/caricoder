#!/usr/bin/env python3

import os
import json
import time
import random
import logging
import signal
import psutil
from typing import Dict, List, Optional, Set, Tuple
from dataclasses import dataclass
from pathlib import Path
import requests
from logging.handlers import RotatingFileHandler
from datetime import datetime
from config import Configuration
from urllib.parse import urlparse
import socket

def setup_logging(log_dir: str = 'logs/monitor', log_level: str = 'INFO') -> logging.Logger:
    """Set up logging with both console and file outputs"""
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_file = os.path.join(log_dir, f'channel_monitor_{timestamp}.log')
    
    file_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - [%(levelname)s] - %(message)s\n'
        'Thread: %(threadName)s - Process: %(process)d\n'
        '%(pathname)s:%(lineno)d\n'
    )
    console_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    
    while root_logger.handlers:
        root_logger.removeHandler(root_logger.handlers[0])
    
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=5*1024*1024,
        backupCount=10,
        encoding='utf-8'
    )
    file_handler.setFormatter(file_formatter)
    file_handler.setLevel(logging.DEBUG)
    
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(console_formatter)
    console_handler.setLevel(getattr(logging, log_level))
    
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)
    
    logger = logging.getLogger(__name__)
    logger.debug(f"Logging initialized - Level: {log_level}, File: {log_file}")
    
    return logger

@dataclass
class ChannelState:
    """Represents the running state of a channel from the state file"""
    channel_name: str
    source_index: int
    input_pid: int
    transcoder_pid: Optional[int]
    output_pids: Dict[int, int]
    last_restart: float
    failure_count: int
    input_priority: Optional[int] = None
    logger: Optional[logging.Logger] = None

    @classmethod
    def from_file(cls, channel_name: str, file_path: str, config: Configuration, logger: logging.Logger) -> 'ChannelState':
        """Create ChannelState from a state file with configuration data"""
        logger.debug(f"Loading channel state from {file_path}")
        
        with open(file_path, 'r') as f:
            data = json.load(f)
            logger.debug(f"Loaded state data: {json.dumps(data, indent=2)}")
            
            # Get channel settings from configuration
            channel_settings = config.get_channel_settings(channel_name)
            inputs = channel_settings.get('inputs', [])
            current_index = data.get('source_index', 0)
            
            # Get priority for current input if available
            input_priority = None
            if 0 <= current_index < len(inputs):
                input_priority = inputs[current_index].get('priority', 50)
                logger.debug(f"Current input priority for index {current_index}: {input_priority}")
            
            state = cls(
                channel_name=channel_name,
                source_index=current_index,
                input_pid=data['input_pid'],
                transcoder_pid=data.get('transcoder_pid'),
                output_pids=data.get('output_pids', {}),
                last_restart=data.get('last_restart', 0),
                failure_count=data.get('failure_count', 0),
                input_priority=input_priority,
                logger=logger
            )
            
            logger.debug(f"Created channel state: {state}")
            return state

    def to_file(self, file_path: str) -> None:
        """Save ChannelState to a state file"""
        if self.logger:
            self.logger.debug(f"Saving channel state to {file_path}")
        
        data = {
            'source_index': self.source_index,
            'input_pid': self.input_pid,
            'transcoder_pid': self.transcoder_pid,
            'output_pids': self.output_pids,
            'last_restart': self.last_restart,
            'failure_count': self.failure_count
        }
        
        with open(file_path, 'w') as f:
            json.dump(data, f, indent=2)
            
        if self.logger:
            self.logger.debug(f"Saved state data: {json.dumps(data, indent=2)}")

    def __str__(self) -> str:
        return (
            f"ChannelState(name={self.channel_name}, index={self.source_index}, "
            f"priority={self.input_priority}, failures={self.failure_count})"
        )

#main indent
class ChannelMonitor:
    """Monitors channel processes and handles failures"""
    
    def __init__(self, channel_manager_url: str, running_dir: str = "/root/caricoder/running",
                 log_level: str = "INFO"):
        self.channel_manager_url = channel_manager_url
        self.running_dir = Path(running_dir)
        self.running_dir.mkdir(parents=True, exist_ok=True)
        
        # Initialize logging
        self.logger = setup_logging(log_level=log_level)
        self.logger.debug(f"Initializing ChannelMonitor with URL: {channel_manager_url}")
        self.logger.debug(f"Using running directory: {running_dir}")
        
        # Initialize configuration
        self.config = Configuration()
        self.logger.debug("Configuration initialized")
        
        # Monitoring settings
        self.MIN_BACKOFF_TIME = 5  # seconds
        self.MAX_BACKOFF_TIME = 30  # seconds
        self.MAX_FAILURE_COUNT = 5
        self.CHECK_INTERVAL = 5  # seconds
        self.PROCESS_START_WAIT = 10  # seconds
        
        self.logger.debug(
            f"Monitor settings: Check Interval={self.CHECK_INTERVAL}s, "
            f"Max Failures={self.MAX_FAILURE_COUNT}, "
            f"Backoff Range={self.MIN_BACKOFF_TIME}-{self.MAX_BACKOFF_TIME}s"
        )
        
        # State tracking
        self.channels: Dict[str, ChannelState] = {}
        self.processed_restarts: Set[str] = set()

    def _calculate_backoff_time(self, failure_count: int) -> float:
        """Calculate exponential backoff time with random jitter"""
        # Higher failure counts result in longer maximum backoff times
        max_backoff = min(self.MAX_BACKOFF_TIME, 
                         self.MIN_BACKOFF_TIME * (2 ** failure_count))
        
        # Add random jitter between MIN and calculated max
        backoff = random.uniform(self.MIN_BACKOFF_TIME, max_backoff)
        
        self.logger.debug(
            f"Calculated backoff time: {backoff:.2f}s "
            f"(failure_count={failure_count}, max_backoff={max_backoff:.2f})"
        )
        
        return backoff

    def _check_process(self, pid: int) -> bool:
        """Check if a process is running"""
        try:
            process = psutil.Process(pid)
            is_running = process.is_running() and process.status() != psutil.STATUS_ZOMBIE
            self.logger.debug(f"Process {pid} status: {'Running' if is_running else 'Not Running'}")
            return is_running
        except psutil.NoSuchProcess:
            self.logger.debug(f"Process {pid} not found")
            return False

    def _find_best_input(self, channel_name: str) -> int:
        """Find the highest priority input for a channel"""
        self.logger.debug(f"Finding best input for channel: {channel_name}")
        
        try:
            channel_settings = self.config.get_channel_settings(channel_name)
            inputs = channel_settings.get('inputs', [])
            
            best_index = 0
            best_priority = -1
            
            for index, input_config in enumerate(inputs):
                priority = input_config.get('priority', 50)
                
                if priority > best_priority:
                    best_priority = priority
                    best_index = index
                    self.logger.debug(f"New best input found: index={index}, priority={priority}")
            
            self.logger.debug(f"Best input result: index={best_index}, priority={best_priority}")
            return best_index
                
        except Exception as e:
            self.logger.error(f"Error finding best input: {str(e)}")
            return 0

    def _handle_channel_failure(self, channel: ChannelState, failed_outputs: List[int]) -> None:
        """Handle channel failure, resetting and retrying with backoff after max failures"""
        self.logger.debug(
            f"Handling failure for channel {channel.channel_name}, "
            f"failed outputs: {failed_outputs}"
        )
        
        try:
            # Check for complete failure
            complete_failure = not self._check_process(channel.input_pid) or (
                channel.transcoder_pid and not self._check_process(channel.transcoder_pid)
            )
            
            self.logger.debug(f"Failure type: {'Complete' if complete_failure else 'Partial'}")
            
            if complete_failure:
                channel.failure_count += 1
                self.logger.debug(f"Failure count increased to {channel.failure_count}")
                
                if channel.failure_count >= self.MAX_FAILURE_COUNT:
                    self.logger.info(f"Maximum failures ({self.MAX_FAILURE_COUNT}) reached, resetting count and doing backoff")
                    channel.failure_count = 0
                
                # Read current state before restart
                state_file = self.running_dir / f"{channel.channel_name}.json"
                with open(state_file) as f:
                    old_state = json.load(f)
                self.logger.debug(f"Current state before restart: {json.dumps(old_state, indent=2)}")
                
                # Calculate and wait backoff time
                backoff_time = self._calculate_backoff_time(channel.failure_count)
                self.logger.info(f"Waiting {backoff_time:.2f}s before restarting {channel.channel_name}")
                time.sleep(backoff_time)
                
                # Issue restart command
                best_index = self._find_best_input(channel.channel_name)
                self.logger.debug(f"Restarting with best input: index={best_index}")
                
                response = requests.post(
                    f"{self.channel_manager_url}/restart",
                    json={
                        "channel": channel.channel_name,
                        "source_index": best_index
                    }
                )
                response.raise_for_status()
                self.logger.info("Restart request successful")
                
                # Wait for processes to start
                self.logger.info(f"Waiting {self.PROCESS_START_WAIT}s for processes to start")
                time.sleep(self.PROCESS_START_WAIT)
                
                # Read new state
                with open(state_file) as f:
                    new_state = json.load(f)
                self.logger.debug(f"New state after restart: {json.dumps(new_state, indent=2)}")
                
                # Verify PIDs changed
                if (new_state['input_pid'] == old_state['input_pid'] or
                    new_state['transcoder_pid'] == old_state['transcoder_pid'] or
                    new_state['output_pids'] == old_state['output_pids']):
                    self.logger.error("PIDs did not change after restart!")
                else:
                    self.logger.info("Restart verified - all PIDs updated")
                
                # Update our channel state with new values
                channel.input_pid = new_state['input_pid']
                channel.transcoder_pid = new_state['transcoder_pid']
                channel.output_pids = new_state['output_pids']
                channel.last_restart = new_state['last_restart']
                
            else:
                self.logger.info(f"Handling partial failure for {channel.channel_name}")
                for output_index in failed_outputs:
                    self.logger.debug(f"Restarting output {output_index}")
                    response = requests.post(
                        f"{self.channel_manager_url}/restart",
                        json={
                            "channel": channel.channel_name,
                            "source_index": channel.source_index
                        }
                    )
                    response.raise_for_status()
                    self.logger.info(f"Channel restart successful for output failure")
                
        except Exception as e:
            self.logger.error(f"Error handling failure: {str(e)}")

    def _load_channel_states(self) -> None:
        """Load all channel states from running directory"""
        self.logger.debug("Loading channel states from running directory")
        file_count = 0
        success_count = 0
        
        self.channels.clear()
        for file_path in self.running_dir.glob("*.json"):
            file_count += 1
            try:
                channel_name = file_path.stem
                self.logger.debug(f"Processing state file for channel: {channel_name}")
                
                self.channels[channel_name] = ChannelState.from_file(
                    channel_name, str(file_path), self.config, self.logger
                )
                success_count += 1
                
            except Exception as e:
                self.logger.error(f"Error loading state for {file_path}: {e}")
        
        self.logger.debug(
            f"Loaded {success_count}/{file_count} channel states successfully"
        )

    def monitor_channels(self) -> None:
        """Main monitoring loop"""
        self.logger.info("Starting channel monitoring loop")
        
        while True:
            try:
                self.logger.debug("\n" + "="*50)
                self.logger.debug("Starting monitoring iteration")
                
                self._load_channel_states()
                self.logger.debug(f"Monitoring {len(self.channels)} channels")
                
                for channel_name, channel in self.channels.items():
                    self.logger.debug(f"\nProcessing channel: {channel_name}")
                    
                    # Check process states
                    self.logger.debug("Checking process states:")
                    self.logger.debug(f"  Input PID: {channel.input_pid}")
                    if channel.transcoder_pid:
                        self.logger.debug(f"  Transcoder PID: {channel.transcoder_pid}")
                    self.logger.debug(f"  Output PIDs: {channel.output_pids}")
                    
                    # Check for failed outputs
                    failed_outputs = [
                        idx for idx, pid in channel.output_pids.items()
                        if not self._check_process(pid)
                    ]
                    if failed_outputs:
                        self.logger.debug(f"Found failed outputs: {failed_outputs}")
                        self._handle_channel_failure(channel, failed_outputs)
                    else:
                        self.logger.debug("All processes healthy")
                        
                        # Reset failure count on successful run
                        if channel.failure_count > 0:
                            self.logger.debug(
                                f"Resetting failure count from {channel.failure_count} to 0"
                            )
                            channel.failure_count = 0
                            state_file = self.running_dir / f"{channel.channel_name}.json"
                            channel.to_file(str(state_file))
                
                self.logger.debug(f"Iteration complete. Sleeping for {self.CHECK_INTERVAL}s")
                time.sleep(self.CHECK_INTERVAL)
                
            except KeyboardInterrupt:
                self.logger.info("Received keyboard interrupt, shutting down")
                break
            except Exception as e:
                self.logger.error(f"Error in monitoring loop: {str(e)}")
                self.logger.debug("Traceback:", exc_info=True)
                time.sleep(self.CHECK_INTERVAL)

#main indent
def main():
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Channel Monitoring System")
    parser.add_argument(
        "--channel-manager",
        default="http://localhost:8001",
        help="Channel manager URL"
    )
    parser.add_argument(
        "--running-dir",
        default="/root/caricoder/running",
        help="Directory for running state files"
    )
    parser.add_argument(
        "--log-level",
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
        default='INFO',
        help="Set the logging level"
    )
    
    args = parser.parse_args()
    
    monitor = ChannelMonitor(
        args.channel_manager,
        args.running_dir,
        log_level=args.log_level
    )
    monitor.monitor_channels()

if __name__ == "__main__":
    main()