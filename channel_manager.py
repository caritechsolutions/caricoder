#!/usr/bin/env python3

from config import Configuration
import logging
import subprocess
import json
from flask import Flask, request, jsonify
from flask_cors import CORS
import psutil
import signal
import time
import os  # Add this import
from pathlib import Path
from typing import List, Dict, Optional
from dataclasses import dataclass
from enum import Enum, auto
import argparse
from datetime import datetime
from logging.handlers import RotatingFileHandler

class InputType(Enum):
    SRT = auto()
    UDP = auto()
    RTSP = auto()
    HLS = auto()
    UNKNOWN = auto()

class TranscoderType(Enum):
    CPU_ONLY = auto()
    GPU_ONLY = auto()
    HYBRID_CPU_DECODE = auto()
    HYBRID_GPU_DECODE = auto()
    NONE = auto()

class OutputType(Enum):
    UDP = auto()
    SRT = auto()
    RIST = auto()
    HLS = auto()
    RTMP = auto()
    UNKNOWN = auto()

@dataclass
class ChannelConfig:
    name: str
    input_type: InputType
    transcoder_type: TranscoderType
    output_types: List[OutputType]
    raw_config: Dict

class ChannelProcess:
    def __init__(self, name: str, process: subprocess.Popen, process_type: str, index: int):
        self.name = name
        self.process = process
        self.process_type = process_type  # 'input', 'transcoder', or 'output'
        self.index = index
        self.start_time = time.time()
        
    def is_running(self) -> bool:
        if self.process.poll() is None:
            return True
        return False
        
    def get_status(self) -> Dict:
        return {
            "type": self.process_type,
            "pid": self.process.pid,
            "running": self.is_running(),
            "index": self.index,
            "uptime": int(time.time() - self.start_time)
        }

class ChannelManager:
    def __init__(self):
        self.config = Configuration()
        self.channels: Dict[str, ChannelConfig] = {}
        self.processes: Dict[str, Dict[str, ChannelProcess]] = {}
        self.logger = self._setup_logging()
        self.load_config()
        
        # Ensure log directories exist
        log_dirs = [
            "/root/caricoder/logs/srt_input",
            "/root/caricoder/logs/transcoder",
            "/root/caricoder/logs/udp_output"
        ]
        for dir_path in log_dirs:
            Path(dir_path).mkdir(parents=True, exist_ok=True)

#main indent
    def _setup_logging(self) -> logging.Logger:
        """Set up logging with both console and file outputs"""
        # Create logs directory if it doesn't exist
        log_dir = "/root/caricoder/logs/channel_manager"
        os.makedirs(log_dir, exist_ok=True)
        
        # Create logger
        logger = logging.getLogger('ChannelManager')
        logger.setLevel(logging.DEBUG)
        
        # Create formatters
        file_formatter = logging.Formatter(
            '%(asctime)s - %(name)s - [%(levelname)s] - %(message)s\n'
            'Thread: %(threadName)s - Process: %(process)d\n'
            '%(pathname)s:%(lineno)d\n'
        )
        console_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        
        # Create file handler
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        log_file = os.path.join(log_dir, f'channel_manager_{timestamp}.log')
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=5*1024*1024,  # 5MB
            backupCount=10,
            encoding='utf-8'
        )
        file_handler.setFormatter(file_formatter)
        file_handler.setLevel(logging.DEBUG)
        
        # Create console handler
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(console_formatter)
        console_handler.setLevel(logging.DEBUG)  # Or use args.log_level from main()
        
        # Add handlers to logger
        logger.addHandler(file_handler)
        logger.addHandler(console_handler)
        
        logger.info(f"Logging initialized - File: {log_file}")
        return logger

    def load_config(self):
        """Load and parse the configuration using Configuration class"""
        try:
            channel_names = self.config.config['channels'].keys()

            for channel_name in channel_names:
                channel_config = self.config.get_channel_settings(channel_name)
                
                input_type = self._detect_input_type(channel_config)
                transcoder_type = self._detect_transcoder_type(channel_config)
                output_types = self._detect_output_types(channel_config)

                self.channels[channel_name] = ChannelConfig(
                    name=channel_name,
                    input_type=input_type,
                    transcoder_type=transcoder_type,
                    output_types=output_types,
                    raw_config=channel_config
                )

                self.logger.info(f"Loaded channel: {channel_name}")
                self.logger.info(f"  Input type: {input_type.name}")
                self.logger.info(f"  Transcoder type: {transcoder_type.name}")
                self.logger.info(f"  Output types: {[ot.name for ot in output_types]}")

        except Exception as e:
            self.logger.error(f"Error loading config: {str(e)}")
            raise

    def _detect_input_type(self, channel_config: Dict) -> InputType:
        try:
            inputs = channel_config.get('inputs', [])
            if not inputs:
                return InputType.UNKNOWN

            input_type = inputs[0].get('type', '').lower()
            return {
                'srtsrc': InputType.SRT,
                'udpsrc': InputType.UDP,
                'rtspsrc': InputType.RTSP,
                'hlssrc': InputType.HLS
            }.get(input_type, InputType.UNKNOWN)

        except Exception as e:
            self.logger.error(f"Error detecting input type: {str(e)}")
            return InputType.UNKNOWN

    def _detect_transcoder_type(self, channel_config: Dict) -> TranscoderType:
        try:
            transcoding = channel_config.get('transcoding', {})
            processing = channel_config.get('processing', {})

            video_settings = transcoding.get('video', {})
            video_streams = video_settings.get('streams', [])
            
            if not video_streams:
                video_passthrough = video_settings.get('codec') == 'passthrough'
            else:
                video_passthrough = all(stream.get('codec') == 'passthrough' for stream in video_streams)

            audio_passthrough = transcoding.get('audio', {}).get('codec') == 'passthrough'

            if video_passthrough and audio_passthrough:
                return TranscoderType.NONE

            transcoder_type = processing.get('type', 'cpu_only').upper()
            return {
                'CPU_ONLY': TranscoderType.CPU_ONLY,
                'GPU_ONLY': TranscoderType.GPU_ONLY,
                'HYBRID_CPU_DECODE': TranscoderType.HYBRID_CPU_DECODE,
                'HYBRID_GPU_DECODE': TranscoderType.HYBRID_GPU_DECODE
            }.get(transcoder_type, TranscoderType.CPU_ONLY)

        except Exception as e:
            self.logger.error(f"Error detecting transcoder type: {str(e)}")
            return TranscoderType.CPU_ONLY

    def _detect_output_types(self, channel_config: Dict) -> List[OutputType]:
        try:
            outputs = channel_config.get('outputs', [])
            output_types = []

            for output in outputs:
                output_type = output.get('type', '').upper()
                detected_type = {
                    'UDPSINK': OutputType.UDP,
                    'SRTSINK': OutputType.SRT,
                    'RISTSINK': OutputType.RIST,
                    'HLSSINK': OutputType.HLS,
                    'RTMPSINK': OutputType.RTMP
                }.get(output_type, OutputType.UNKNOWN)
                
                if detected_type != OutputType.UNKNOWN:
                    output_types.append(detected_type)

            return output_types if output_types else [OutputType.UNKNOWN]

        except Exception as e:
            self.logger.error(f"Error detecting output types: {str(e)}")
            return [OutputType.UNKNOWN]

#main indent
    def _run_process(self, command: list, channel_name: str, process_type: str, index: int) -> ChannelProcess:
        """Run a process and return its ChannelProcess object"""
        self.logger.info(f"Starting {process_type} for channel {channel_name}: {' '.join(command)}")
        try:
            # Ensure we have the proper environment
            env = os.environ.copy()
            
            # Add crucial environment variables
            env.update({
                'GST_DEBUG_DUMP_DOT_DIR': '/root/caricoder/dot',
                'GST_DEBUG': '3',  # Enable basic GStreamer debugging
                'GST_DEBUG_FILE': f'/root/caricoder/logs/{process_type}_{channel_name}_gst.log',
                'PYTHONUNBUFFERED': '1'  # Ensure Python output is unbuffered
            })

            # Create log directory if it doesn't exist
            log_dir = f"/root/caricoder/logs/{process_type}"
            os.makedirs(log_dir, exist_ok=True)

            # Open log files for stdout and stderr
            stdout_path = os.path.join(log_dir, f"{channel_name}_stdout.log")
            stderr_path = os.path.join(log_dir, f"{channel_name}_stderr.log")
            
            stdout_file = open(stdout_path, 'a')
            stderr_file = open(stderr_path, 'a')

            # Start the process with proper setup
            process = subprocess.Popen(
                command,
                stdout=stdout_file,
                stderr=stderr_file,
                env=env,
                preexec_fn=os.setsid,  # Create new process group
                close_fds=True  # Ensure clean file descriptor handling
            )

            # Store file handles for cleanup
            process._log_files = (stdout_file, stderr_file)
            
            self.logger.info(f"Started {process_type} process PID: {process.pid}")
            self.logger.info(f"Stdout log: {stdout_path}")
            self.logger.info(f"Stderr log: {stderr_path}")

            return ChannelProcess(channel_name, process, process_type, index)

        except Exception as e:
            self.logger.error(f"Failed to start {process_type} process: {str(e)}")
            raise

#main indent
    def start_channel(self, channel_name: str, source_index: int = 0) -> Dict:
        """Start a channel's processes with specified source index"""
        try:
            if channel_name not in self.channels:
                return {"status": "error", "message": f"Channel {channel_name} not found"}

            if channel_name in self.processes:
                return {"status": "error", "message": f"Channel {channel_name} is already running"}

            channel = self.channels[channel_name]
            self.processes[channel_name] = {}

            # Start input handler based on type
            if channel.input_type == InputType.SRT:
                input_cmd = [
                    "python3", "input_handler.py",
                    "--log-dir", "/root/caricoder/logs/srt_input",
                    "--source-index", str(source_index),
                    channel_name
                ]
                self.processes[channel_name]['input'] = self._run_process(
                    input_cmd, channel_name, "input", source_index
                )
             
            elif channel.input_type == InputType.UDP:
                input_cmd = [
                    "python3", "udp_input_handler.py",
                    "--log-dir", "/root/caricoder/logs/udp_input",
                    "--source-index", str(source_index),
                    channel_name
                ]
                self.processes[channel_name]['input'] = self._run_process(
                    input_cmd, channel_name, "input", source_index
                )

            elif channel.input_type == InputType.HLS:
                input_cmd = [
                    "python3", "hls_input_handler.py",
                    "--log-dir", "/root/caricoder/logs/udp_input",
                    "--source-index", str(source_index),
                    channel_name
                ]
                self.processes[channel_name]['input'] = self._run_process(
                    input_cmd, channel_name, "input", source_index
                )
            
            time.sleep(2)

            # Start transcoder if needed
            needs_transcoding = channel.transcoder_type != TranscoderType.NONE
            if needs_transcoding:
                transcoder_cmd = [
                    "python3", "transcoder.py",
                    channel_name,
                    "--source-index", str(source_index),
                    "--log-dir", "/root/caricoder/logs/transcoder",
                    "--log-level", "DEBUG"
                ]
                self.processes[channel_name]['transcoder'] = self._run_process(
                    transcoder_cmd, channel_name, "transcoder", source_index
                )
                time.sleep(6)
          
            # Start HLS output
            hls_cmd = [
                "python3", "hls_output_handler.py",
                "--mode", "input" if not needs_transcoding else "output",
                "--log-dir", "/root/caricoder/logs/hls_output",
                "--log-level", "DEBUG",
                channel_name
            ]
            self.processes[channel_name]['hls_output'] = self._run_process(
                hls_cmd, channel_name, "hls_output", 0
            )
            time.sleep(2)

            # Start other output handlers
            for output_index, output_type in enumerate(channel.output_types):
                if output_type == OutputType.UDP:
                    output_cmd = [
                        "python3", "udp_output_handler.py",
                        "--output-index", str(output_index),
                        "--log-dir", "/root/caricoder/logs/udp_output",
                        "--log-level", "DEBUG",
                        channel_name
                    ]
                    self.processes[channel_name][f'output_{output_index}'] = self._run_process(
                        output_cmd, channel_name, "output", output_index
                    )
            
            # After all processes started successfully
            self.manage_state_file(channel_name, "write")
            return {"status": "success", "message": f"Channel {channel_name} started"}

        except Exception as e:
            self.logger.error(f"Error starting channel {channel_name}: {str(e)}")
            # Cleanup any started processes
            self.stop_channel(channel_name)
            return {"status": "error", "message": str(e)}

        

    def _cleanup_shared_memory(self, channel_name: str):
        """Clean up shared memory files for a channel"""
        socket_dir = "/tmp/caricoder"
        files_to_cleanup = [
            f"{socket_dir}/{channel_name}_muxed_shm",
            f"{socket_dir}/{channel_name}_transcoded_shm",
            f"{socket_dir}/{channel_name}_video_shm_info",
            f"{socket_dir}/{channel_name}_audio_shm_info"
        ]
    
        for file_path in files_to_cleanup:
            try:
                if os.path.exists(file_path):
                    os.unlink(file_path)
                    self.logger.info(f"Cleaned up shared memory file: {file_path}")
            except Exception as e:
                self.logger.error(f"Error cleaning up {file_path}: {str(e)}")

    def stop_channel(self, channel_name: str) -> Dict:
        """Stop all processes for a channel"""
        # Remove state file first
        self.manage_state_file(channel_name, "remove")
        try:
            if channel_name not in self.channels:
                return {"status": "error", "message": f"Channel {channel_name} not found"}

            if channel_name not in self.processes:
                return {"status": "error", "message": f"Channel {channel_name} is not running"}

            force_killed = False
            # Stop processes in reverse order (outputs first, input last)
            process_list = list(self.processes[channel_name].items())
            process_list.reverse()

            for process_type, process in process_list:
                if process.is_running():
                    self.logger.info(f"Stopping {process_type} for channel {channel_name}")
                    try:
                        # Send SIGINT to process group
                        os.killpg(os.getpgid(process.process.pid), signal.SIGINT)
                        
                        # Wait up to 10 seconds for graceful shutdown
                        try:
                            process.process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            self.logger.warning(f"{process_type} did not stop gracefully, sending SIGTERM")
                            os.killpg(os.getpgid(process.process.pid), signal.SIGTERM)
                            
                            try:
                                process.process.wait(timeout=5)
                            except subprocess.TimeoutExpired:
                                self.logger.warning(f"{process_type} did not terminate, sending SIGKILL")
                                os.killpg(os.getpgid(process.process.pid), signal.SIGKILL)
                                process.process.wait()
                                force_killed = True

                        # Close log files
                        if hasattr(process.process, '_log_files'):
                            stdout_file, stderr_file = process.process._log_files
                            stdout_file.close()
                            stderr_file.close()

                    except Exception as e:
                        self.logger.error(f"Error stopping {process_type}: {str(e)}")
                        force_killed = True

            # If any process was force-killed, clean up shared memory
            if force_killed:
                self.logger.warning("Force kill occurred, cleaning up shared memory files")
                self._cleanup_shared_memory(channel_name)

            # Remove from processes dict
            del self.processes[channel_name]
            return {"status": "success", "message": f"Channel {channel_name} stopped"}

        except Exception as e:
            self.logger.error(f"Error stopping channel {channel_name}: {str(e)}")
            # Attempt cleanup even on error
            try:
                self._cleanup_shared_memory(channel_name)
            except Exception as cleanup_error:
                self.logger.error(f"Error during emergency cleanup: {str(cleanup_error)}")
            return {"status": "error", "message": str(e)}

    def restart_channel(self, channel_name: str, source_index: int = 0) -> Dict:
        """Restart a channel's processes"""
        stop_result = self.stop_channel(channel_name)
        if stop_result["status"] == "error":
            return stop_result
            
        # Give processes time to clean up
        time.sleep(2)
        return self.start_channel(channel_name, source_index)

    def get_channel_status(self, channel_name: str = None) -> Dict:
        """Get status of all running processes for one or all channels"""
        try:
            status = {}
            channels_to_check = [channel_name] if channel_name else self.processes.keys()
            
            for chan in channels_to_check:
                if chan in self.processes:
                    # Update status and clean up dead processes
                    status[chan] = {
                        "running": True,
                        "processes": {}
                    }
                    for proc_type, process in self.processes[chan].items():
                        if process.is_running():
                            status[chan]["processes"][proc_type] = process.get_status()
                        else:
                            status[chan]["running"] = False
                
                elif channel_name:  # Only add non-running status if specific channel requested
                    status[chan] = {
                        "running": False,
                        "processes": {}
                    }
            
            return {"status": "success", "channels": status}

        except Exception as e:
            self.logger.error(f"Error getting channel status: {str(e)}")
            return {"status": "error", "message": str(e)}

#main indent
    def manage_state_file(self, channel_name: str, action: str = "write") -> None:
        """Manage channel state file - write or remove"""
        file_path = f"/root/caricoder/running/{channel_name}.json"
        
        try:
            if action == "write":
                # Get current process info from self.processes[channel_name]
                channel_processes = self.processes[channel_name]
                
                # Build output PIDs dictionary
                output_pids = {}
                for proc_name, proc in channel_processes.items():
                    if proc_name.startswith('output_'):
                        output_index = proc_name.split('_')[1]  # Get index from 'output_0'
                        output_pids[output_index] = proc.process.pid

                # Get transcoder PID if it exists
                transcoder_pid = None
                if 'transcoder' in channel_processes:
                    transcoder_pid = channel_processes['transcoder'].process.pid
                
                state = {
                    "source_index": channel_processes['input'].index,
                    "input_pid": channel_processes['input'].process.pid,
                    "transcoder_pid": transcoder_pid,
                    "output_pids": output_pids,
                    "last_restart": time.time(),
                    "failure_count": 0
                }
                
                with open(file_path, 'w') as f:
                    json.dump(state, f, indent=2)
                self.logger.debug(f"Wrote state file for {channel_name}")
                
            elif action == "remove":
                if os.path.exists(file_path):
                    os.remove(file_path)
                    self.logger.debug(f"Removed state file for {channel_name}")
                    
        except Exception as e:
            self.logger.error(f"Error managing state file for {channel_name}: {e}")

# Flask application
app = Flask(__name__)
CORS(app)
channel_manager = None

@app.route('/start', methods=['POST'])
def start_channel():
    data = request.get_json()
    channel_name = data.get('channel')
    source_index = data.get('source_index', 0)
    if not channel_name:
        return jsonify({"status": "error", "message": "Channel name is required"}), 400
    return jsonify(channel_manager.start_channel(channel_name, source_index))

@app.route('/stop', methods=['POST'])
def stop_channel():
    data = request.get_json()
    channel_name = data.get('channel')
    if not channel_name:
        return jsonify({"status": "error", "message": "Channel name is required"}), 400
    return jsonify(channel_manager.stop_channel(channel_name))

@app.route('/restart', methods=['POST'])
def restart_channel():
    data = request.get_json()
    channel_name = data.get('channel')
    source_index = data.get('source_index', 0)
    if not channel_name:
        return jsonify({"status": "error", "message": "Channel name is required"}), 400
    return jsonify(channel_manager.restart_channel(channel_name, source_index))

@app.route('/status', methods=['GET'])
def get_status():
    channel_name = request.args.get('channel')
    return jsonify(channel_manager.get_channel_status(channel_name))

@app.route('/list', methods=['GET'])
def list_channels():
    channels = {}
    for name, channel in channel_manager.channels.items():
        channels[name] = {
            "input_type": channel.input_type.name,
            "transcoder_type": channel.transcoder_type.name,
            "output_types": [ot.name for ot in channel.output_types],
            "running": name in channel_manager.processes
        }
    return jsonify({"channels": channels})

def main():
    global channel_manager
    parser = argparse.ArgumentParser(description="Channel Manager Service")
    parser.add_argument("--port", type=int, default=8001,
                       help="Port to run the service on (default: 8001)")
    parser.add_argument("--host", default="0.0.0.0",
                       help="Host to bind to (default: 0.0.0.0)")
    parser.add_argument("--log-level", 
                       choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
                       default='INFO', 
                       help="Set the logging level")
    
    args = parser.parse_args()
    
    channel_manager = ChannelManager()
    app.run(host=args.host, port=args.port)

if __name__ == "__main__":
    main()