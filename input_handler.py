#!/usr/bin/env python3

import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib
from config import Configuration
import logging
from logging.handlers import RotatingFileHandler
import subprocess
import redis
import json
import sys
import os
import socket
import time
import argparse
from datetime import datetime
from urllib.parse import urlparse, urlencode
from stats_collector import StatsCollector
from pathlib import Path

def setup_logging(channel_name, log_dir='logs', log_level='INFO'):
    """Set up logging configuration for the application."""
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_file = os.path.join(log_dir, f'input_handler_{channel_name}_{timestamp}.log')
    
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
    
    return logging.getLogger(__name__)

class InputPipelineHandler:
    def __init__(self, channel_name, source_index=0):
        self.logger = logging.getLogger(__name__)
        self.channel_name = channel_name
        self.source_index = source_index
        
        # Watchdog and restart parameters
        self.restart_attempts = 0
        self.handling_failure = False
        self.watchdog_timeouts_set = False
        
        # Watchdog timeouts (in milliseconds)
        self.INITIAL_WATCHDOG_TIMEOUT = 30000  # 30 seconds
        self.RUNNING_WATCHDOG_TIMEOUT = 5000   # 5 seconds
        self.RESTART_DELAY = 5  # seconds
        
        # Set up DOT file directory
        self.dot_dir = "/root/caricoder/dot"
        Path(self.dot_dir).mkdir(parents=True, exist_ok=True)
        os.environ['GST_DEBUG_DUMP_DOT_DIR'] = self.dot_dir
        
        self.config = Configuration()
        self.channel_settings = self.config.get_channel_settings(channel_name)
        self.pipeline = None
        self.elements = {}
        self.socket_paths = []
        self.stats_collector = None
        self.srt_stats_timer = None
        self.fds = {}
        
        # Initialize GStreamer
        Gst.init(None)
        
        # Get inputs configuration
        self.inputs = self.channel_settings.get('inputs', [])
        if not self.inputs:
            raise ValueError(f"No inputs defined for channel: {channel_name}")
        
        if self.source_index < 0 or self.source_index >= len(self.inputs):
            raise ValueError(f"Invalid source index {source_index}")
        
        self.selected_input = self.inputs[self.source_index]
        self.logger.debug(f"Selected input configuration: {self.selected_input}")
        
        # Verify input type is SRT
        if self.selected_input['type'] != 'srtsrc':
            raise ValueError("Only SRT input type is supported")
        
        # Initialize Redis for stats collection
        try:
            self.redis_client = redis.Redis(host='localhost', port=6379, decode_responses=True)
            self.redis_client.ping()
            self.logger.info("Successfully connected to Redis")
            self.stats_collector = StatsCollector(channel_name, self.redis_client)
        except redis.ConnectionError:
            self.logger.error("Failed to connect to Redis")
            self.redis_client = None
            self.stats_collector = None
            
        # Create shared memory directory
        self.socket_dir = "/tmp/caricoder"
        os.makedirs(self.socket_dir, exist_ok=True)

    def analyze_stream(self):
        """Analyze the input stream using ffprobe to detect codecs and PIDs."""
        input_config = self.selected_input
        uri = input_config.get('uri')
        
        self.logger.info(f"Analyzing stream: URI={uri}")

        try:
            cmd = [
                "ffprobe",
                "-v", "quiet",
                "-print_format", "json",
                "-show_format",
                "-show_streams",
                "-show_programs",
                "-i", uri
            ]
            
            self.logger.debug(f"Running ffprobe command: {' '.join(cmd)}")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            
            self.logger.debug(f"ffprobe stdout: {result.stdout}")
            if result.stderr:
                self.logger.debug(f"ffprobe stderr: {result.stderr}")
                
            if result.returncode != 0:
                raise RuntimeError(f"ffprobe failed with return code {result.returncode}")
            
            probe_data = json.loads(result.stdout)
            self.logger.debug(f"Probe data: {json.dumps(probe_data, indent=2)}")
            
            program = probe_data.get('programs', [None])[0]
            video_codec = audio_codec = None
            video_pid = audio_pid = None
            program_number = program.get('program_id') if program else None

            streams = program.get('streams', []) if program else probe_data.get('streams', [])
            for stream in streams:
                if stream['codec_type'] == 'video' and not video_codec:
                    video_codec = stream['codec_name']
                    video_pid = format_pid(stream.get('id', '0'))
                elif stream['codec_type'] == 'audio' and not audio_codec:
                    audio_codec = stream['codec_name']
                    audio_pid = format_pid(stream.get('id', '0'))

            #self._store_codec_info(video_codec, audio_codec, video_pid, audio_pid, program_number)
            # Store complete probe data
            self._store_codec_info(video_codec, audio_codec, video_pid, audio_pid, program_number, probe_data)

            return video_codec, audio_codec, video_pid, audio_pid, program_number

        except Exception as e:
            self.logger.error(f"Stream analysis failed: {str(e)}")
            raise

#main indent
    def _store_codec_info(self, video_codec, audio_codec, video_pid, audio_pid, program_number, probe_data):
        """Store codec information in JSON files with backward compatibility."""
        # Create base video info (original format)
        video_info = {
            "codec": video_codec,
            "pid": video_pid,
            "program_number": program_number
        }
        
        # Create base audio info (original format)
        audio_info = {
            "codec": audio_codec,
            "pid": audio_pid,
            "program_number": program_number
        }
        
        # Add extended information under new keys
        if probe_data:
            video_stream = next((s for s in probe_data.get('streams', []) 
                               if s['codec_type'] == 'video'), {})
            audio_stream = next((s for s in probe_data.get('streams', []) 
                               if s['codec_type'] == 'audio'), {})
            program = probe_data.get('programs', [{}])[0]
            format_info = probe_data.get('format', {})
            
            # Add extended video info
            video_info["extended"] = {
                'input': {
                    'type': 'srtsrc',
                    'uri': self.selected_input.get('uri'),
                    'format': format_info.get('format_name'),
                    'start_time': format_info.get('start_time'),
                    'nb_streams': format_info.get('nb_streams'),
                    'nb_programs': format_info.get('nb_programs')
                },
                'program': {
                    'id': program.get('program_num', 0),
                    'pmt_pid': program.get('pmt_pid'),
                    'pcr_pid': program.get('pcr_pid'),
                    'nb_streams': program.get('nb_streams'),
                    'bitrate': program.get('tags', {}).get('variant_bitrate')
                },
                'stream': {
                    'codec': {
                        'name': video_stream.get('codec_name'),
                        'long_name': video_stream.get('codec_long_name'),
                        'profile': video_stream.get('profile'),
                        'level': video_stream.get('level')
                    },
                    'format': {
                        'width': video_stream.get('width'),
                        'height': video_stream.get('height'),
                        'coded_width': video_stream.get('coded_width'),
                        'coded_height': video_stream.get('coded_height'),
                        'pix_fmt': video_stream.get('pix_fmt'),
                        'sample_aspect_ratio': video_stream.get('sample_aspect_ratio'),
                        'display_aspect_ratio': video_stream.get('display_aspect_ratio'),
                        'color_range': video_stream.get('color_range'),
                        'chroma_location': video_stream.get('chroma_location'),
                        'field_order': video_stream.get('field_order')
                    },
                    'encoding': {
                        'has_b_frames': video_stream.get('has_b_frames'),
                        'refs': video_stream.get('refs'),
                        'extradata_size': video_stream.get('extradata_size')
                    },
                    'timing': {
                        'r_frame_rate': video_stream.get('r_frame_rate'),
                        'avg_frame_rate': video_stream.get('avg_frame_rate'),
                        'time_base': video_stream.get('time_base'),
                        'start_pts': video_stream.get('start_pts'),
                        'start_time': video_stream.get('start_time')
                    },
                    'tags': video_stream.get('tags', {})
                }
            }
            
            # Add extended audio info
            audio_info["extended"] = {
                'input': video_info['extended']['input'],  # Share input info
                'program': video_info['extended']['program'],  # Share program info
                'stream': {
                    'codec': {
                        'name': audio_stream.get('codec_name'),
                        'long_name': audio_stream.get('codec_long_name'),
                        'profile': audio_stream.get('profile')
                    },
                    'format': {
                        'sample_fmt': audio_stream.get('sample_fmt'),
                        'sample_rate': audio_stream.get('sample_rate'),
                        'channels': audio_stream.get('channels'),
                        'channel_layout': audio_stream.get('channel_layout'),
                        'bits_per_sample': audio_stream.get('bits_per_sample')
                    },
                    'timing': {
                        'time_base': audio_stream.get('time_base'),
                        'start_pts': audio_stream.get('start_pts'),
                        'start_time': audio_stream.get('start_time')
                    },
                    'tags': audio_stream.get('tags', {})
                }
            }
        
        # Write info files
        video_info_path = f"{self.socket_dir}/{self.channel_name}_video_shm_info"
        audio_info_path = f"{self.socket_dir}/{self.channel_name}_audio_shm_info"
        
        with open(video_info_path, 'w') as f:
            json.dump(video_info, f, indent=2)
        with open(audio_info_path, 'w') as f:
            json.dump(audio_info, f, indent=2)
            
        self.logger.info(f"Stored codec info to: {video_info_path} and {audio_info_path}")

#main indent
    def _adjust_watchdog_timeouts(self, initial=False):
        """Adjust watchdog timeouts based on pipeline state"""
        timeout = self.INITIAL_WATCHDOG_TIMEOUT if initial else self.RUNNING_WATCHDOG_TIMEOUT
        
        self.logger.info(f"Setting watchdog timeouts to {timeout/1000} seconds")
        
        if 'video_watchdog' in self.elements:
            self.elements['video_watchdog'].set_property('timeout', timeout)
        if 'audio_watchdog' in self.elements:
            self.elements['audio_watchdog'].set_property('timeout', timeout)
        if 'watchdog_output' in self.elements:
            self.elements['watchdog_output'].set_property('timeout', timeout)

#main indent
    def _reduce_watchdog_timeouts(self):
        """Reduce watchdog timeouts after pipeline is stable"""
        self._adjust_watchdog_timeouts(initial=False)
        self.watchdog_timeouts_set = True
        return False  # Don't repeat the timeout

#main indent
    def _cleanup_shared_memory(self):
        """Clean up shared memory files and related resources"""
        files_to_cleanup = [
            f"{self.socket_dir}/{self.channel_name}_muxed_shm",
            f"{self.socket_dir}/{self.channel_name}_video_shm_info",
            f"{self.socket_dir}/{self.channel_name}_audio_shm_info"
        ]
        
        for file_path in files_to_cleanup:
            try:
                if os.path.exists(file_path):
                    os.unlink(file_path)
                    self.logger.info(f"Cleaned up shared memory file: {file_path}")
            except Exception as e:
                self.logger.error(f"Error cleaning up {file_path}: {str(e)}")

        # Clear socket paths list
        self.socket_paths = []
        
        # Clear file descriptors
        for fd_name, fd in self.fds.items():
            try:
                os.close(fd)
                self.logger.debug(f"Closed file descriptor for {fd_name}: {fd}")
            except Exception as e:
                self.logger.error(f"Error closing file descriptor for {fd_name}: {str(e)}")
        self.fds.clear()

#main indent
    def _handle_watchdog_timeout(self):
        """Handle pipeline failure and attempt restart"""
        if self.handling_failure:
            return
            
        self.handling_failure = True
        self.restart_attempts += 1
        
        self.logger.warning(
            f"Attempting pipeline restart (attempt {self.restart_attempts})"
        )

        try:
            self.logger.info(f"Waiting {self.RESTART_DELAY} seconds before restart attempt")
            time.sleep(self.RESTART_DELAY)
            
            # Stop current pipeline
            if self.pipeline:
                self.pipeline.set_state(Gst.State.NULL)
                self.pipeline.get_state(Gst.CLOCK_TIME_NONE)
                self.pipeline = None
            
            # Clear elements
            for element in self.elements.values():
                element.set_state(Gst.State.NULL)
            self.elements.clear()

            # Clean up shared memory
            self._cleanup_shared_memory()
            
            # Reset watchdog timeout flag
            self.watchdog_timeouts_set = False
            
            # Re-analyze stream to get fresh codec info
            try:
                self.video_codec, self.audio_codec, self.video_pid, self.audio_pid, self.program_number = self.analyze_stream()
            except Exception as e:
                self.logger.error(f"Stream analysis failed during restart: {str(e)}")
                # If analysis fails, wait a bit and try the whole restart again
                time.sleep(2)
                self.handling_failure = False
                return self._handle_watchdog_timeout()
            
            # Create new pipeline with fresh codec info
            self.create_pipeline()
            
            # Set up message handling
            bus = self.pipeline.get_bus()
            bus.add_signal_watch()
            bus.connect("message", self.on_pipeline_message)
            
            # Start the pipeline
            ret = self.pipeline.set_state(Gst.State.PLAYING)
            if ret == Gst.StateChangeReturn.FAILURE:
                raise RuntimeError("Unable to set the pipeline to the playing state")
                
            # Reset handling_failure flag
            self.handling_failure = False
            
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to restart pipeline: {str(e)}")
            self.handling_failure = False
            return self._handle_watchdog_timeout()

#main indent
    def create_pipeline(self):
        """Create and configure the GStreamer pipeline"""
        self.logger.info("Creating pipeline")
        self.pipeline = Gst.Pipeline.new("input_pipeline")
        
        # Create elements
        self.elements.update({
            'source': Gst.ElementFactory.make("srtsrc", "source"),
            'queue1': Gst.ElementFactory.make("queue", "queue1"),
            'tsparse': Gst.ElementFactory.make("tsparse", "tsparse"),
            'queue2': Gst.ElementFactory.make("queue", "queue2"),
            'tsdemux': Gst.ElementFactory.make("tsdemux", "tsdemux"),
            'video_queue1': Gst.ElementFactory.make("queue", "video_queue1"),
            'audio_queue1': Gst.ElementFactory.make("queue", "audio_queue1"),
            'video_watchdog': Gst.ElementFactory.make("watchdog", "video_watchdog"),
            'audio_watchdog': Gst.ElementFactory.make("watchdog", "audio_watchdog"),
            'watchdog_output': Gst.ElementFactory.make("watchdog", "watchdog_output"),
            'mpegtsmux': Gst.ElementFactory.make("mpegtsmux", "mux"),
            'final_queue1': Gst.ElementFactory.make("queue", "final_queue1"),
            'final_tsparse': Gst.ElementFactory.make("tsparse", "final_tsparse"),
            'final_queue2': Gst.ElementFactory.make("queue", "final_queue2"),
            'shmsink': Gst.ElementFactory.make("shmsink", "shmsink")
        })

        # Configure source with SRT options
        srt_settings = self.selected_input
        base_uri = srt_settings.get('uri', '')
        options = srt_settings.get('options', {})
        
        # Set SRT properties
        self.elements['source'].set_property('uri', base_uri)
        
        # Set default latency if not specified
        latency = options.get('latency', 1000)
        self.elements['source'].set_property('latency', latency)
        
        # Set streamid if specified
        if 'streamid' in options:
            self.elements['source'].set_property('streamid', options['streamid'])
        
        # Set larger blocksize for better performance
        self.elements['source'].set_property('blocksize', 2097152)
        
        
        # Configure watchdogs with initial high timeout
        self._adjust_watchdog_timeouts(initial=True)
        
        # Configure queues
        for queue in ['queue1', 'queue2', 'video_queue1', 'audio_queue1', 'final_queue1', 'final_queue2']:
            self.elements[queue].set_property("leaky", 1)
            self.elements[queue].set_property("max-size-buffers", 0)
            self.elements[queue].set_property("max-size-time", 3000000000)
            self.elements[queue].set_property("max-size-bytes", 0)

        # Create parsers based on codec detection
        self.logger.info(f"Creating parsers for video codec: {self.video_codec}, audio codec: {self.audio_codec}")
        
        if self.video_codec == 'h264':
            self.elements['video_parser'] = Gst.ElementFactory.make("h264parse", "video_parser")
        elif self.video_codec == 'hevc':
            self.elements['video_parser'] = Gst.ElementFactory.make("h265parse", "video_parser")
        elif self.video_codec == 'mpeg2video':
            self.elements['video_parser'] = Gst.ElementFactory.make("mpegvideoparse", "video_parser")

        if self.audio_codec == 'aac':
            self.elements['audio_parser'] = Gst.ElementFactory.make("aacparse", "audio_parser")
        elif self.audio_codec in ['mp2', 'mp3']:
            self.elements['audio_parser'] = Gst.ElementFactory.make("mpegaudioparse", "audio_parser")

        # Configure shared memory sink
        shm_path = f"{self.socket_dir}/{self.channel_name}_muxed_shm"
        self.socket_paths = [shm_path]
        if os.path.exists(shm_path):
            try:
                os.unlink(shm_path)
            except Exception as e:
                self.logger.warning(f"Could not remove socket {shm_path}: {e}")

        self.elements['shmsink'].set_property('socket-path', shm_path)
        self.elements['shmsink'].set_property('wait-for-connection', False)
        self.elements['shmsink'].set_property('sync', False)
        self.elements['shmsink'].set_property('async', False)
        self.elements['shmsink'].set_property('shm-size', 2000000)

        # Add all elements to pipeline
        for element in self.elements.values():
            if element:  # Only add elements that were successfully created
                self.pipeline.add(element)

        # Link static elements
        if not self.elements['source'].link(self.elements['queue1']):
            raise RuntimeError("Failed to link source to queue1")
        if not self.elements['queue1'].link(self.elements['tsparse']):
            raise RuntimeError("Failed to link queue1 to tsparse")
        if not self.elements['tsparse'].link(self.elements['queue2']):
            raise RuntimeError("Failed to link tsparse to queue2")
        if not self.elements['queue2'].link(self.elements['tsdemux']):
            raise RuntimeError("Failed to link queue2 to tsdemux")

        # Link muxer output chain with watchdog
        if not self.elements['mpegtsmux'].link(self.elements['final_queue1']):
            raise RuntimeError("Failed to link mpegtsmux to final_queue1")
        if not self.elements['final_queue1'].link(self.elements['watchdog_output']):
            raise RuntimeError("Failed to link final_queue1 to watchdog_output")
        if not self.elements['watchdog_output'].link(self.elements['shmsink']):
            raise RuntimeError("Failed to link watchdog_output to shmsink")

        # Connect to pad-added signal for dynamic linking
        self.elements['tsdemux'].connect("pad-added", self.on_pad_added)

#main indent
    def on_pad_added(self, element, pad):
        """Handle dynamic pad connections from demuxer."""
        pad_name = pad.get_name()
        self.logger.info(f"New pad added: {pad_name}")

        if pad_name.startswith("video"):
            if 'video_parser' in self.elements:
                sink_pad = self.elements['video_queue1'].get_static_pad("sink")
                if pad.link(sink_pad) == Gst.PadLinkReturn.OK:
                    # Link video path through watchdog to muxer
                    if not self.elements['video_queue1'].link(self.elements['video_watchdog']):
                        self.logger.error("Failed to link video_queue1 to video_watchdog")
                        return
                    if not self.elements['video_watchdog'].link(self.elements['video_parser']):
                        self.logger.error("Failed to link video_watchdog to video_parser")
                        return
                    if not self.elements['video_parser'].link(self.elements['mpegtsmux']):
                        self.logger.error("Failed to link video_parser to mpegtsmux")
                        return
                    self.logger.info("Successfully linked video chain")
                else:
                    self.logger.error("Failed to link video pad")

        elif pad_name.startswith("audio"):
            if 'audio_parser' in self.elements:
                sink_pad = self.elements['audio_queue1'].get_static_pad("sink")
                if pad.link(sink_pad) == Gst.PadLinkReturn.OK:
                    # Link audio path through watchdog to muxer
                    if not self.elements['audio_queue1'].link(self.elements['audio_watchdog']):
                        self.logger.error("Failed to link audio_queue1 to audio_watchdog")
                        return
                    if not self.elements['audio_watchdog'].link(self.elements['audio_parser']):
                        self.logger.error("Failed to link audio_watchdog to audio_parser")
                        return
                    if not self.elements['audio_parser'].link(self.elements['mpegtsmux']):
                        self.logger.error("Failed to link audio_parser to mpegtsmux")
                        return
                    self.logger.info("Successfully linked audio chain")
                else:
                    self.logger.error("Failed to link audio pad")

#main indent
    def on_pipeline_message(self, bus, message):
        """Handle pipeline messages including watchdog timeouts"""
        t = message.type
        if t == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            error_msg = err.message
            debug_info = debug
            element_name = message.src.get_name()
            element_state = message.src.get_state(0)[1].value_nick

            # Handle watchdog timeout
            if 'Watchdog triggered' in error_msg:
                self.logger.error(f"Watchdog timeout detected in {element_name}")
                
                if 'video_watchdog' in element_name:
                    self.logger.error("Video stream watchdog timeout - possible video loss")
                elif 'audio_watchdog' in element_name:
                    self.logger.error("Audio stream watchdog timeout - possible audio loss")
                elif 'watchdog_output' in element_name:
                    self.logger.error("Output stream watchdog timeout - possible pipeline stall")
                
                self._handle_watchdog_timeout()
                
            else:
                self.logger.error(f"Pipeline error from element {element_name} (state: {element_state})")
                self.logger.error(f"Error message: {error_msg}")
                self.logger.error(f"Debug info: {debug_info}")

        elif t == Gst.MessageType.WARNING:
            warn, debug = message.parse_warning()
            self.logger.warning(f"Pipeline warning: {warn.message}")
            self.logger.debug(f"Warning debug info: {debug}")
            
        elif t == Gst.MessageType.STATE_CHANGED:
            if message.src == self.pipeline:
                old_state, new_state, pending_state = message.parse_state_changed()
                self.logger.info(f"Pipeline state changed from {old_state.value_nick} "
                               f"to {new_state.value_nick}")
                
                # When pipeline is successfully playing, adjust watchdog timeouts and reset counters
                if new_state == Gst.State.PLAYING and not self.watchdog_timeouts_set:
                    # Wait a few seconds before reducing timeouts
                    GLib.timeout_add(5000, self._reduce_watchdog_timeouts)
                    # Reset restart attempts on successful playback
                    self.restart_attempts = 0

        elif t == Gst.MessageType.EOS:
            self.logger.warning("End of stream reached")

#main indent
    def print_srt_stats(self):
        """Collect and store SRT statistics"""
        if isinstance(self.elements.get('source'), Gst.Element):
            stats = self.elements['source'].get_property('stats')
            if stats:
                stats_dict = {}
                for i in range(stats.n_fields()):
                    field_name = stats.nth_field_name(i)
                    field_value = stats.get_value(field_name)
                    if hasattr(field_value, 'to_string'):
                        field_value = field_value.to_string()
                    stats_dict[field_name] = field_value
                
                if self.stats_collector:
                    self.stats_collector.add_stats("srt_input", stats_dict)
                    self.logger.debug(f"Stored SRT stats for channel: {self.channel_name}")
            else:
                self.logger.warning("No SRT statistics available")
        else:
            self.logger.warning("SRT source element not found or wrong type")
        return True

#main indent
    def run(self):
        """Main run loop"""
        self.logger.info("Starting main run loop")
        
        try:
            # Create and set up pipeline
            self.video_codec, self.audio_codec, self.video_pid, self.audio_pid, self.program_number = self.analyze_stream()
            self.create_pipeline()
            
            # Start SRT stats collection
            self.srt_stats_timer = GLib.timeout_add(5000, self.print_srt_stats)
            
            # Generate initial DOT file
            self.generate_dot_file("initial")
            
            # Set up message handling
            bus = self.pipeline.get_bus()
            bus.add_signal_watch()
            bus.connect("message", self.on_pipeline_message)
            
            # Start the pipeline
            ret = self.pipeline.set_state(Gst.State.PLAYING)
            if ret == Gst.StateChangeReturn.FAILURE:
                raise RuntimeError("Unable to set the pipeline to the playing state")
            
            # Generate DOT file after pipeline is playing
            self.generate_dot_file("playing")
            
            # Run the main loop
            loop = GLib.MainLoop()
            try:
                loop.run()
            except KeyboardInterrupt:
                self.logger.info("Keyboard interrupt received")
            finally:
                self.cleanup()
                
        except Exception as e:
            self.logger.error(f"Error in main run loop: {str(e)}")
            self.cleanup()
            raise

#main indent
    def cleanup(self):
        """Cleanup resources"""
        self.logger.info("Starting cleanup")
        
        try:
            # Stop SRT stats collection
            if self.srt_stats_timer:
                GLib.source_remove(self.srt_stats_timer)
                self.logger.debug("Stopped SRT statistics collection")

            # Stop pipeline
            if self.pipeline:
                self.logger.info("Stopping pipeline")
                self.pipeline.set_state(Gst.State.NULL)
                self.pipeline.get_state(Gst.CLOCK_TIME_NONE)
                self.logger.info("Pipeline stopped")

            # Clean up shared memory
            self._cleanup_shared_memory()

        except Exception as e:
            self.logger.error(f"Error during cleanup: {str(e)}")
        finally:
            self.logger.info("Cleanup completed")

#main indent
    def generate_dot_file(self, filename):
        """Generate a DOT file of the current pipeline state"""
        try:
            full_filename = f"{self.channel_name}_input_{filename}"
            
            Gst.debug_bin_to_dot_file(
                self.pipeline,
                Gst.DebugGraphDetails.ALL,
                full_filename
            )
            
            dot_path = os.path.join(self.dot_dir, f"{full_filename}.dot")
            png_path = os.path.join(self.dot_dir, f"{full_filename}.png")
            
            if not os.path.exists(dot_path):
                self.logger.error(f"DOT file was not created at {dot_path}")
                return
                
            try:
                subprocess.run(
                    ['dot', '-Tpng', dot_path, '-o', png_path],
                    check=True,
                    capture_output=True,
                    text=True
                )
                self.logger.info(f"Generated pipeline visualization: {png_path}")
            except subprocess.CalledProcessError as e:
                self.logger.error(f"Failed to convert DOT to PNG: {e.stderr}")
            except FileNotFoundError:
                self.logger.error("graphviz 'dot' command not found")
                
        except Exception as e:
            self.logger.error(f"Error generating pipeline visualization: {str(e)}")

def format_pid(pid):
    """Format PID to ensure it always has four digits after the 'x'."""
    if isinstance(pid, str):
        if pid.startswith('0x'):
            return '0x' + pid[2:].zfill(4)
        return '0x' + pid.zfill(4)
    return pid

def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(description="SRT Input Pipeline Handler")
    parser.add_argument("--log-dir", default="logs", help="Directory to store log files")
    parser.add_argument("channel", help="Channel name from the configuration")
    parser.add_argument("--source-index", type=int, default=0,
                       help="Index of the input source to use (default: 0)")
    parser.add_argument("--log-level", 
                       choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
                       default='INFO', 
                       help="Set the logging level")
    
    args = parser.parse_args()
    
    # Initialize logging
    logger = setup_logging(args.channel, args.log_dir, args.log_level)
    logger.info(f"Starting Input Pipeline Handler for channel: {args.channel}")
    logger.info(f"Using source index: {args.source_index}")
    
    try:
        # Create and run input handler
        handler = InputPipelineHandler(args.channel, args.source_index)
        handler.run()
    except KeyboardInterrupt:
        logger.info("Exiting due to keyboard interrupt")
        sys.exit(0)
    except Exception as e:
        logger.critical(f"Fatal error: {str(e)}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()