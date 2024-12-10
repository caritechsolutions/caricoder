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
import time
import argparse
from datetime import datetime
from collections import deque
from urllib.parse import urlparse, urlencode
from stats_collector import StatsCollector
from pathlib import Path

def setup_logging(channel_name, log_dir='logs', log_level='INFO'):
    """Configure logging with both console and file outputs"""
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_file = os.path.join(log_dir, f'transcoder_{channel_name}_{timestamp}.log')
    
    # Create formatters
    file_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - [%(levelname)s] - %(message)s\n'
        'Thread: %(threadName)s - Process: %(process)d\n'
        '%(pathname)s:%(lineno)d\n'
    )
    console_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    # Get the root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    
    # Remove any existing handlers
    while root_logger.handlers:
        root_logger.removeHandler(root_logger.handlers[0])
    
    # Create and configure file handler
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=5*1024*1024,
        backupCount=10,
        encoding='utf-8'
    )
    file_handler.setFormatter(file_formatter)
    file_handler.setLevel(logging.DEBUG)
    
    # Create console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(console_formatter)
    console_handler.setLevel(getattr(logging, log_level))
    
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)
    
    return logging.getLogger(__name__)

#main indent
class EncoderStatsMonitor:
    """Monitors real-time statistics from video and audio encoders using pad probes"""
    def __init__(self, channel_name, stats_collector):
        self.logger = logging.getLogger(__name__)
        self.channel_name = channel_name
        self.stats_collector = stats_collector
        
        # Initialize counters and timestamps
        self.last_ts = time.time()
        self.last_byte_count = 0
        self.frame_count = 0
        self.bytes_since_last = 0
        
        # Store video format info
        self.video_width = 0
        self.video_height = 0
        self.video_framerate = 0
        self.video_format = None

    def setup_video_encoder_probes(self, encoder):
        """Set up pad probes for video encoder"""
        try:
            # Get sink and src pads
            sink_pad = encoder.get_static_pad('sink')
            src_pad = encoder.get_static_pad('src')
            
            if not sink_pad or not src_pad:
                self.logger.error("Could not get encoder pads")
                return False
            
            # Add probes
            sink_pad.add_probe(Gst.PadProbeType.BUFFER, self._sink_pad_probe_cb)
            src_pad.add_probe(Gst.PadProbeType.BUFFER, self._src_pad_probe_cb)
            
            self.logger.info("Successfully set up video encoder probes")
            return True
            
        except Exception as e:
            self.logger.error(f"Error setting up encoder probes: {str(e)}")
            return False

    def _sink_pad_probe_cb(self, pad, info):
        """Callback for sink pad probe - monitors input format and frames"""
        try:
            buffer = info.get_buffer()
            caps = pad.get_current_caps()
            
            if caps:
                structure = caps.get_structure(0)
                
                # Get video format information - handle tuple returns correctly
                success, self.video_width = structure.get_int('width')
                success, self.video_height = structure.get_int('height')
                self.video_format = structure.get_string('format')
                
                # Get framerate - handle fraction tuple correctly
                success, fps_n, fps_d = structure.get_fraction('framerate')
                if success and fps_d != 0:
                    self.video_framerate = fps_n / fps_d

            # Count frames
            self.frame_count += 1
            
            # Create stats dictionary
            stats = {
                'input_width': self.video_width,
                'input_height': self.video_height,
                'input_format': self.video_format,
                'input_framerate': self.video_framerate,
                'frames_processed': self.frame_count
            }
            
            # Store stats
            if self.stats_collector:
                self.stats_collector.add_stats("video_encoder_input", stats)
            
        except Exception as e:
            self.logger.error(f"Error in sink pad probe: {str(e)}")
            
        return Gst.PadProbeReturn.OK

    def _src_pad_probe_cb(self, pad, info):
        """Callback for src pad probe - monitors output bitrate and encoded frames"""
        try:
            buffer = info.get_buffer()
            current_ts = time.time()
            
            # Calculate bitrate
            bytes_in_buffer = buffer.get_size()
            self.bytes_since_last += bytes_in_buffer
            
            # Update every second
            time_diff = current_ts - self.last_ts
            if time_diff >= 1.0:
                # Calculate bitrate in kbps
                bitrate = (self.bytes_since_last * 8) / (time_diff * 1024)
                
                # Get frame rate
                fps = self.frame_count / time_diff if time_diff > 0 else 0
                
                # Create stats dictionary
                stats = {
                    'output_bitrate_kbps': bitrate,
                    'output_fps': fps,
                    'bytes_encoded': self.bytes_since_last,
                    'frame_count': self.frame_count
                }
                
                # Store stats
                if self.stats_collector:
                    self.stats_collector.add_stats("video_encoder_output", stats)
                
                # Reset counters
                self.last_ts = current_ts
                self.bytes_since_last = 0
                self.frame_count = 0
                
                self.logger.debug(f"Encoder stats: {stats}")
            
        except Exception as e:
            self.logger.error(f"Error in src pad probe: {str(e)}")
            
        return Gst.PadProbeReturn.OK

class PipelineStateAdapter(logging.LoggerAdapter):
    """Adapter to inject pipeline state into all log messages"""
    def process(self, msg, kwargs):
        pipeline = self.extra.get('pipeline')
        if pipeline:
            try:
                _, current_state, _ = pipeline.get_state(0)
                state_info = f" [Pipeline State: {current_state.value_nick}]"
            except Exception:
                state_info = " [Pipeline State: Unknown]"
        else:
            state_info = " [Pipeline State: Not initialized]"
        
        return f"{msg}{state_info}", kwargs

def check_passthrough(config, channel_name):
    """Check if both audio and video are set to passthrough"""
    transcoding = config.get_channel_settings(channel_name).get('transcoding', {})
    
    video_streams = transcoding.get('video', {}).get('streams', [])
    all_video_passthrough = all(stream.get('codec') == 'passthrough' for stream in video_streams)
    
    audio_codec = transcoding.get('audio', {}).get('codec')
    audio_passthrough = audio_codec == 'passthrough'
    
    return all_video_passthrough and audio_passthrough

#main indent
    

class TranscodePipelineHandler:
#main indent
    def __init__(self, channel_name, source_index=0, log_dir='logs'):
        # Initialize logging
        self.logger_extra = {'pipeline': None}
        self.base_logger = logging.getLogger(__name__)
        self.logger = PipelineStateAdapter(self.base_logger, self.logger_extra)
        self.channel_name = channel_name
        self.source_index = source_index
        

        # Add restart counter
        self.restart_count = 0
        self.MAX_RESTART_ATTEMPTS = 10
        
        # Initialize configuration
        self.config = Configuration()
        self.channel_settings = self.config.get_channel_settings(channel_name)
        
        # Set up DOT file directory
        self.dot_dir = "/root/caricoder/dot"
        Path(self.dot_dir).mkdir(parents=True, exist_ok=True)
        os.environ['GST_DEBUG_DUMP_DOT_DIR'] = self.dot_dir
        self.logger.info(f"Set up DOT file directory at {self.dot_dir}")
        
        # Validate source index
        self.inputs = self.channel_settings.get('inputs', [])
        if not self.inputs:
            raise ValueError(f"No inputs defined for channel: {channel_name}")
            
        if self.source_index < 0 or self.source_index >= len(self.inputs):
            raise ValueError(f"Invalid source index {source_index} for channel '{channel_name}'. Valid range: 0-{len(self.inputs) - 1}")
        
        self.selected_input = self.inputs[self.source_index]
        
        # Initialize GStreamer
        Gst.init(None)
        
        # Initialize variables
        self.pipeline = None
        self.elements = {}
        self.socket_dir = "/tmp/caricoder"
        self.fds = {}
        self.video_info = None
        self.audio_info = None
        
        # Get transcoding settings
        self.transcode_settings = self.channel_settings.get('transcoding', {})
        if not self.transcode_settings:
            raise ValueError(f"No transcoding settings defined for channel: {channel_name}")
        
        # Initialize Redis for stats
        try:
            self.redis_client = redis.Redis(host='localhost', port=6379, decode_responses=True)
            self.redis_client.ping()
            self.logger.info("Successfully connected to Redis")
            self.stats_collector = StatsCollector(f"{channel_name}_transcode", self.redis_client)
        except redis.ConnectionError:
            self.logger.error("Failed to connect to Redis")
            self.redis_client = None
            self.stats_collector = None

    def _wait_for_codec_info(self):
        """Wait for codec info files to be available"""
        max_retries = 30  # 2.5 minutes maximum wait time
        retry_count = 0
        
        video_info_path = f"{self.socket_dir}/{self.channel_name}_video_shm_info"
        audio_info_path = f"{self.socket_dir}/{self.channel_name}_audio_shm_info"
        
        self.logger.info(f"Waiting for codec info files")
        
        while retry_count < max_retries:
            if os.path.exists(video_info_path) and os.path.exists(audio_info_path):
                try:
                    with open(video_info_path, 'r') as f:
                        self.video_info = json.load(f)
                    with open(audio_info_path, 'r') as f:
                        self.audio_info = json.load(f)
                    self.logger.info("Successfully loaded codec info files")
                    return True
                except Exception as e:
                    self.logger.warning(f"Error reading codec info files: {str(e)}")
            
            retry_count += 1
            self.logger.debug(f"Codec info files not ready, retry {retry_count}/{max_retries}")
            time.sleep(5)
        
        self.logger.error("Codec info files not available after timeout")
        return False



    def _create_queue(self, name):
        """Helper method to create a standardized queue element"""
        queue = Gst.ElementFactory.make("queue", name)
        queue.set_property("leaky", 1)
        queue.set_property("max-size-buffers", 0)
        queue.set_property("max-size-time", 3000000000)
        queue.set_property("max-size-bytes", 0)
        return queue

    def _load_codec_info(self):
        """Load codec information from shared memory info files"""
        try:
            with open(f"{self.socket_dir}/{self.channel_name}_video_shm_info", 'r') as f:
                self.video_info = json.load(f)
            with open(f"{self.socket_dir}/{self.channel_name}_audio_shm_info", 'r') as f:
                self.audio_info = json.load(f)
                
            self.logger.info(f"Loaded video codec info: {self.video_info}")
            self.logger.info(f"Loaded audio codec info: {self.audio_info}")
        except Exception as e:
            self.logger.error(f"Failed to load codec info: {e}")
            raise

    def generate_dot_file(self, filename):
        """Generate a DOT file of the current pipeline state and convert it to PNG."""
        try:
            # Add channel name to the filename
            full_filename = f"{self.channel_name}_transcode_{filename}"
            
            # Generate DOT file
            Gst.debug_bin_to_dot_file(self.pipeline, 
                                     Gst.DebugGraphDetails.ALL, 
                                     full_filename)
            
            # Construct file paths
            dot_path = os.path.join(self.dot_dir, f"{full_filename}.dot")
            png_path = os.path.join(self.dot_dir, f"{full_filename}.png")
            
            # Check if DOT file was created
            if not os.path.exists(dot_path):
                self.logger.error(f"DOT file was not created at {dot_path}")
                return
                
            # Convert DOT to PNG using graphviz
            try:
                subprocess.run(['dot', '-Tpng', dot_path, '-o', png_path], 
                             check=True,
                             capture_output=True,
                             text=True)
                self.logger.info(f"Generated pipeline visualization: DOT={dot_path}, PNG={png_path}")
            except subprocess.CalledProcessError as e:
                self.logger.error(f"Failed to convert DOT to PNG: {e.stderr}")
            except FileNotFoundError:
                self.logger.error("graphviz 'dot' command not found. Please install graphviz package.")
                
        except Exception as e:
            self.logger.error(f"Error generating pipeline visualization: {str(e)}")
            self.logger.exception("Full traceback:")

    def create_pipeline(self):
        """Create the GStreamer pipeline"""
        self.logger.info("Creating pipeline")
        self.pipeline = Gst.Pipeline.new("transcode_pipeline")
        self.logger_extra['pipeline'] = self.pipeline


        # Enable debug output for mpegtsmux
        Gst.debug_set_threshold_for_name('mpegtsmux', 5)  # Set to debug level 5

        # Create and add elements
        self._create_elements()

        # Link static elements
        self._link_static_elements()

        # Connect pad-added signal for dynamic linking
        self.elements['tsdemux'].connect("pad-added", self.on_pad_added)
        
        # Verify all elements are created
        for name, element in self.elements.items():
            if element is None:
                self.logger.error(f"Element {name} was not created successfully")
            else:
                self.logger.debug(f"Element {name} created successfully")

    def _create_elements(self):
        """Create all GStreamer elements"""
        self.logger.info("Creating elements")
        
        # Source elements (shared memory input)
        input_shm_path = f"{self.socket_dir}/{self.channel_name}_muxed_shm"
        self.logger.info(f"Setting up input from: {input_shm_path}")
        
        # Create base elements
        self.elements.update({
            'shmsrc': Gst.ElementFactory.make("shmsrc", "shmsrc"),
            'queue1': self._create_queue("queue1"),
            'tsparse1': Gst.ElementFactory.make("tsparse", "tsparse1"),
            'queue_tsparse1': self._create_queue("queue_tsparse1"),
            'watchdog_video': Gst.ElementFactory.make("watchdog", "watchdog_video"),
            'watchdog_audio': Gst.ElementFactory.make("watchdog", "watchdog_audio"),
            'watchdog_output': Gst.ElementFactory.make("watchdog", "watchdog_output"),
            'tsdemux': Gst.ElementFactory.make("tsdemux", "tsdemux")

        })

        # Configure source elements
        self.elements['shmsrc'].set_property('socket-path', input_shm_path)
        self.elements['shmsrc'].set_property('is-live', True)
        self.elements['shmsrc'].set_property('blocksize', 2097152)


        # Configure Watch dogs
        self.elements['watchdog_video'].set_property('timeout', 5000)  # 5 second timeout
        self.elements['watchdog_audio'].set_property('timeout', 10000)  # 5 second timeout
        self.elements['watchdog_output'].set_property('timeout', 15000)  # 5 second timeout

        # Create video elements based on input codec and passthrough settings
        video_streams = self.transcode_settings['video']['streams']
        self._create_video_elements(video_streams)

        # Create audio elements based on input codec and passthrough settings
        audio_settings = self.transcode_settings['audio']
        self._create_audio_elements(audio_settings)

        # Create muxer and output elements
        self._create_output_elements()

        # Add all elements to pipeline
        for element in self.elements.values():
            if element:  # Only add elements that were successfully created
                self.pipeline.add(element)

#main indent
    def _create_video_elements(self, video_streams):
        """Create video processing elements with additional queues and monitoring"""
        # Create video queue and parser
        self.elements['queue_video'] = self._create_queue("queue_video")
        
        # Create video parser based on input codec
        match self.video_info['codec']:
            case 'h264':
                self.elements['videoparse'] = Gst.ElementFactory.make("h264parse", "videoparse")
            case 'hevc':
                self.elements['videoparse'] = Gst.ElementFactory.make("h265parse", "videoparse")
            case 'mpeg2video':
                self.elements['videoparse'] = Gst.ElementFactory.make("mpegvideoparse", "videoparse")
            case _:
                raise ValueError(f"Unsupported input video codec: {self.video_info['codec']}")

        self.elements['queue_videoparse'] = self._create_queue("queue_videoparse")

        # For passthrough, we still need to create the output queue
        if video_streams[0]['codec'] == 'passthrough':
            self.elements['queue_video_out1'] = self._create_queue("queue_video_out1")
            return


        # Create encoder statistics monitor
        self.encoder_monitor = EncoderStatsMonitor(self.channel_name, self.stats_collector)

        # Create video processing chain for each stream
        for i, stream in enumerate(video_streams, start=1):
            self.logger.info(f"Creating video stream {i} elements")
            
            if stream['codec'] == 'passthrough':
                self.logger.info(f"Video stream {i} set to passthrough mode")
                continue

            # Create decoder and its queue
            match self.video_info['codec']:
                case 'h264':
                    self.elements[f'videodecode{i}'] = Gst.ElementFactory.make("avdec_h264", f"videodecode{i}")
                case 'hevc':
                    self.elements[f'videodecode{i}'] = Gst.ElementFactory.make("avdec_h265", f"videodecode{i}")
                case 'mpeg2video':
                    self.elements[f'videodecode{i}'] = Gst.ElementFactory.make("avdec_mpeg2video", f"videodecode{i}")
            
            self.elements[f'queue_videodecode{i}'] = self._create_queue(f"queue_videodecode{i}")

            # Create converter and its queue
            self.elements[f'videoconvert{i}'] = Gst.ElementFactory.make("videoconvert", f"videoconvert{i}")
            self.elements[f'queue_videoconvert{i}'] = self._create_queue(f"queue_videoconvert{i}")
            
            # Handle resolution scaling if specified
            if 'resolution' in stream:
                self.elements[f'videoscale{i}'] = Gst.ElementFactory.make("videoscale", f"videoscale{i}")
                self.elements[f'queue_videoscale{i}'] = self._create_queue(f"queue_videoscale{i}")
                
                self.elements[f'capsfilter{i}'] = Gst.ElementFactory.make("capsfilter", f"capsfilter{i}")
                self.elements[f'queue_capsfilter{i}'] = self._create_queue(f"queue_capsfilter{i}")
                
                caps = Gst.Caps.from_string(
                    f"video/x-raw,width={stream['resolution']['width']},height={stream['resolution']['height']}"
                )
                self.elements[f'capsfilter{i}'].set_property("caps", caps)

            # Create encoder
            match stream['codec']:
                case 'x264enc':
                    self.elements[f'videoenc{i}'] = Gst.ElementFactory.make("x264enc", f"videoenc{i}")
                    # Configure x264 specific properties
                    self.elements[f'videoenc{i}'].set_property("tune", 0x00000004)  # zero-latency
                    self.elements[f'videoenc{i}'].set_property("speed-preset", 2)  # superfast
                    self.elements[f'videoenc{i}'].set_property("bitrate", stream.get('options', {}).get('bitrate', 2000))
                    self.elements[f'videoenc{i}'].set_property("key-int-max", stream.get('options', {}).get('key-int-max', 60))
                    
                case 'x265enc':
                    self.elements[f'videoenc{i}'] = Gst.ElementFactory.make("x265enc", f"videoenc{i}")
                    # Configure x265 specific properties
                    self.elements[f'videoenc{i}'].set_property("tune", "zerolatency")
                    self.elements[f'videoenc{i}'].set_property("speed-preset", "superfast")
                    self.elements[f'videoenc{i}'].set_property("bitrate", stream.get('options', {}).get('bitrate', 2000))
                    
                case 'mpeg2enc':
                    self.elements[f'videoenc{i}'] = Gst.ElementFactory.make("avenc_mpeg2video", f"videoenc{i}")
                case _:
                    raise ValueError(f"Unsupported output video codec: {stream['codec']}")

            # Configure general encoder options
            for key, value in stream.get('options', {}).items():
                if key not in ['tune', 'speed-preset']:  # Skip already configured properties
                    try:
                        self.elements[f'videoenc{i}'].set_property(key, value)
                    except Exception as e:
                        self.logger.warning(f"Failed to set encoder property {key}={value}: {str(e)}")

            # Set up encoder monitoring
            if not self.encoder_monitor.setup_video_encoder_probes(self.elements[f'videoenc{i}']):
                self.logger.warning(f"Failed to set up encoder monitoring for stream {i}")

            # Create output queue
            self.elements[f'queue_video_out{i}'] = self._create_queue(f"queue_video_out{i}")
            
            # Log successful creation
            self.logger.info(f"Successfully created video processing chain for stream {i}")
            
            # Log encoder settings
            encoder_element = self.elements[f'videoenc{i}']
            try:
                self.logger.info(f"Encoder {i} settings:")
                self.logger.info(f"  Codec: {stream['codec']}")
                self.logger.info(f"  Bitrate: {encoder_element.get_property('bitrate')} kbps")
                if 'resolution' in stream:
                    self.logger.info(f"  Resolution: {stream['resolution']['width']}x{stream['resolution']['height']}")
            except Exception as e:
                self.logger.warning(f"Failed to log encoder settings: {str(e)}")

    def _create_audio_elements(self, audio_settings):
        """Create audio processing elements with additional queues"""
        # Create audio queue
        self.elements['queue_audio'] = self._create_queue("queue_audio")

        # Create audio parser based on input codec
        match self.audio_info['codec']:
            case 'mp2':
                self.elements['audioparse'] = Gst.ElementFactory.make("mpegaudioparse", "audioparse")
            case 'aac':
                self.elements['audioparse'] = Gst.ElementFactory.make("aacparse", "audioparse")
            case _:
                raise ValueError(f"Unsupported input audio codec: {self.audio_info['codec']}")

        self.elements['queue_audioparse'] = self._create_queue("queue_audioparse")

        if audio_settings['codec'] != 'passthrough':
            # Create decoder and its queue
            match self.audio_info['codec']:
                case 'mp2':
                    self.elements['audiodecode'] = Gst.ElementFactory.make("avdec_mp2float", "audiodecode")
                case 'aac':
                    self.elements['audiodecode'] = Gst.ElementFactory.make("avdec_aac", "audiodecode")
            
            self.elements['queue_audiodecode'] = self._create_queue("queue_audiodecode")

            # Create converters and their queues
            self.elements['audioconvert'] = Gst.ElementFactory.make("audioconvert", "audioconvert")
            self.elements['queue_audioconvert'] = self._create_queue("queue_audioconvert")
            
            self.elements['audioresample'] = Gst.ElementFactory.make("audioresample", "audioresample")
            self.elements['queue_audioresample'] = self._create_queue("queue_audioresample")

            # Create encoder
            match audio_settings['codec']:
                case 'avenc_aac':
                    self.elements['audioenc'] = Gst.ElementFactory.make("avenc_aac", "audioenc")
                case 'avenc_ac3':
                    self.elements['audioenc'] = Gst.ElementFactory.make("avenc_ac3", "audioenc")
                case 'avenc_mp2':
                    self.elements['audioenc'] = Gst.ElementFactory.make("avenc_mp2", "audioenc")
                case _:
                    raise ValueError(f"Unsupported output audio codec: {audio_settings['codec']}")

            # Configure encoder
            for key, value in audio_settings.get('options', {}).items():
                if key == 'bitrate':
                    value = value * 1000  # Convert kbps to bps
                self.elements['audioenc'].set_property(key, value)

        # Create audio output queue
        self.elements['queue_audio_out'] = self._create_queue("queue_audio_out")

    def _create_output_elements(self):
        """Create muxer and output elements with additional queues"""
        # Create muxer
        mux_settings = self.config.get_plugin_settings(self.channel_name, 'mpegtsmux')
        self.elements['mpegtsmux'] = Gst.ElementFactory.make("mpegtsmux", "mux")
        
        # Configure muxer PIDs
        self.mux_video_pids = mux_settings.get('video-pid', [60])
        if not isinstance(self.mux_video_pids, list):
            self.mux_video_pids = [self.mux_video_pids]
        self.mux_audio_pid = mux_settings.get('audio-pid', 61)
        self.mux_program_number = mux_settings.get('program-number', 1000)
        
        # Create program map
        pm = Gst.Structure.new_empty("program_map")
        
        # Request video pads
        self.mux_video_pads = []
        for i, video_pid in enumerate(self.mux_video_pids):
            video_pad = self.elements['mpegtsmux'].request_pad_simple(f"sink_{video_pid}")
            self.mux_video_pads.append(video_pad)
            pm.set_value(video_pad.get_name(), self.mux_program_number)
            
        # Request audio pad
        self.mux_audio_pad = self.elements['mpegtsmux'].request_pad_simple(f"sink_{self.mux_audio_pid}")
        pm.set_value(self.mux_audio_pad.get_name(), self.mux_program_number)
        
        # Create output elements with queues
        self.elements.update({
            'queue_mux': self._create_queue("queue_mux"),
            'tsparse2': Gst.ElementFactory.make("tsparse", "tsparse2"),
            'queue_tsparse2': self._create_queue("queue_tsparse2")
        })
        
        # Create shared memory sink
        output_shm_path = f"{self.socket_dir}/{self.channel_name}_transcoded_shm"
        self.elements['shmsink'] = Gst.ElementFactory.make("shmsink", "shmsink")
        self.elements['shmsink'].set_property('socket-path', output_shm_path)
        self.elements['shmsink'].set_property('wait-for-connection', False)
        self.elements['shmsink'].set_property('sync', True)
        self.elements['shmsink'].set_property('async', True)
        self.elements['shmsink'].set_property('shm-size', 4000000)

    def _link_static_elements(self):
        """Link all static elements in the pipeline"""
        try:
            # Link input elements
            self._link_elements_chain([
                ('shmsrc', 'queue1'),
                ('queue1', 'tsparse1'),
                ('tsparse1', 'queue_tsparse1'),
                ('queue_tsparse1', 'tsdemux')
            ])
            
            # Link output elements
            self._link_elements_chain([
                ('mpegtsmux', 'queue_mux'),
                ('queue_mux', 'tsparse2'),
                ('tsparse2', 'queue_tsparse2'),
                ('queue_tsparse2', 'watchdog_output'),
                ('watchdog_output', 'shmsink')
            ])
            
            self.logger.info("Successfully linked static elements")
        except Exception as e:
            self.logger.error(f"Error linking static elements: {str(e)}")
            raise

    def _link_video_chain(self):
        """Link the video processing chain with added queues"""
        video_streams = self.transcode_settings['video']['streams']
        for i, stream in enumerate(video_streams, start=1):
            try:
                if stream['codec'] == 'passthrough':
                    # Passthrough mode: link through parse queue
                    self._link_elements_chain([
                        ('queue_video', 'watchdog_video'),
                        ('watchdog_video', 'videoparse'),
                        ('videoparse', 'queue_videoparse'),
                        ('queue_videoparse', f'queue_video_out1')
                    ])
                else:
                    # Full processing chain with queues
                    chain = [
                        ('queue_video', 'watchdog_video'),
                        ('watchdog_video', 'videoparse'),
                        ('videoparse', 'queue_videoparse'),
                        ('queue_videoparse', f'videodecode{i}'),
                        (f'videodecode{i}', f'queue_videodecode{i}'),
                        (f'queue_videodecode{i}', f'videoconvert{i}'),
                        (f'videoconvert{i}', f'queue_videoconvert{i}')
                    ]
                    
                    if f'videoscale{i}' in self.elements:
                        chain.extend([
                            (f'queue_videoconvert{i}', f'videoscale{i}'),
                            (f'videoscale{i}', f'queue_videoscale{i}'),
                            (f'queue_videoscale{i}', f'capsfilter{i}'),
                            (f'capsfilter{i}', f'queue_capsfilter{i}'),
                            (f'queue_capsfilter{i}', f'videoenc{i}')
                        ])
                    else:
                        chain.append((f'queue_videoconvert{i}', f'videoenc{i}'))
                    
                    chain.append((f'videoenc{i}', f'queue_video_out{i}'))
                    self._link_elements_chain(chain)
                
                # Link to muxer
                self.elements[f'queue_video_out{i}'].link_pads(
                    "src", 
                    self.elements['mpegtsmux'], 
                    self.mux_video_pads[i-1].get_name()
                )
                
                self.logger.info(f"Successfully linked video chain {i}")
                
            except Exception as e:
                self.logger.error(f"Error linking video chain {i}: {str(e)}")
                raise

    def _link_audio_chain(self):
        """Link the audio processing chain with added queues"""
        try:
            if self.transcode_settings['audio']['codec'] == 'passthrough':
                # Passthrough mode
                self._link_elements_chain([
                    ('queue_audio', 'watchdog_audio'),
                    ('watchdog_audio', 'audioparse'),
                    ('audioparse', 'queue_audioparse'),
                    ('queue_audioparse', 'queue_audio_out')
                ])
            else:
                # Full processing chain with queues
                self._link_elements_chain([
                    ('queue_audio', 'watchdog_audio'),
                    ('watchdog_audio', 'audioparse'),
                    ('audioparse', 'queue_audioparse'),
                    ('queue_audioparse', 'audiodecode'),
                    ('audiodecode', 'queue_audiodecode'),
                    ('queue_audiodecode', 'audioconvert'),
                    ('audioconvert', 'queue_audioconvert'),
                    ('queue_audioconvert', 'audioresample'),
                    ('audioresample', 'queue_audioresample'),
                    ('queue_audioresample', 'audioenc'),
                    ('audioenc', 'queue_audio_out')
                ])

            # Link to muxer
            self.elements['queue_audio_out'].link_pads(
                "src", 
                self.elements['mpegtsmux'], 
                self.mux_audio_pad.get_name()
            )
            
            self.logger.info("Successfully linked audio chain")
            
        except Exception as e:
            self.logger.error(f"Error linking audio chain: {str(e)}")
            raise

    def _link_elements_chain(self, links):
        """Helper method to link a chain of elements with error checking"""
        for src, dest in links:
            if not self.elements[src].link(self.elements[dest]):
                raise RuntimeError(f"Failed to link {src} to {dest}")
            self.logger.debug(f"Successfully linked {src} to {dest}")

    def on_pad_added(self, element, pad):
        """Handle dynamic pad connections from demuxer"""
        pad_name = pad.get_name()
        caps = pad.get_current_caps()
        if not caps:
            self.logger.error(f"No caps on pad {pad_name}")
            return
            
        structure = caps.get_structure(0)
        caps_str = structure.to_string()
        self.logger.debug(f"Pad {pad_name} caps: {caps_str}")
        
        # Generate DOT file when new pad is added
        self.generate_dot_file(f"pad_added_{pad_name}")
        
        if structure.get_name().startswith('video'):
            self.logger.info(f"Found video pad {pad_name}, linking...")
            sink_pad = self.elements['queue_video'].get_static_pad("sink")
            if not sink_pad:
                self.logger.error("No sink pad on queue_video")
                return
                
            ret = pad.link(sink_pad)
            if ret != Gst.PadLinkReturn.OK:
                self.logger.error(f"Failed to link video pad: {ret}")
                return
                
            self.logger.info("Successfully linked video pad, setting up chain")
            self._link_video_chain()
                
        elif structure.get_name().startswith('audio'):
            self.logger.info(f"Found audio pad {pad_name}, linking...")
            sink_pad = self.elements['queue_audio'].get_static_pad("sink")
            if not sink_pad:
                self.logger.error("No sink pad on queue_audio")
                return
                
            ret = pad.link(sink_pad)
            if ret != Gst.PadLinkReturn.OK:
                self.logger.error(f"Failed to link audio pad: {ret}")
                return
                
            self.logger.info("Successfully linked audio pad, setting up chain")
            self._link_audio_chain()
            
        # Generate DOT file after pad is linked
        self.generate_dot_file(f"pad_linked_{pad_name}")

    def run(self):
        """Main run loop with pipeline visualization"""
        self.logger.info("Starting transcode pipeline")
        
        
        try:
            self.create_pipeline()
            
            # Set up message handling
            bus = self.pipeline.get_bus()
            bus.add_signal_watch()
            bus.connect("message", self.on_message)
            
            # Generate DOT file before starting
            self.generate_dot_file("before_playing")

            

            
            # Start the pipeline
            self.logger.info("Setting pipeline to PLAYING state")
            ret = self.pipeline.set_state(Gst.State.PLAYING)
            if ret == Gst.StateChangeReturn.FAILURE:
                raise RuntimeError("Failed to set pipeline to PLAYING state")
            elif ret == Gst.StateChangeReturn.ASYNC:
                self.logger.info("Pipeline is ASYNC, waiting for state change...")
                ret = self.pipeline.get_state(Gst.CLOCK_TIME_NONE)
                self.logger.info(f"Pipeline state change complete: {ret}")
            
            # Generate DOT file after pipeline is playing
            self.generate_dot_file("after_playing")
            
            # Run the main loop
            loop = GLib.MainLoop()
            try:
                loop.run()
            except KeyboardInterrupt:
                self.logger.info("Keyboard interrupt received")
                # Generate final DOT file before cleanup
                self.generate_dot_file("final_state")
            finally:
                self.cleanup()
                
        except Exception as e:
            self.logger.error(f"Error in transcode pipeline: {str(e)}")
            # Generate DOT file on error
            self.generate_dot_file("error_state")
            self.cleanup()
            raise

    def on_message(self, bus, message):
        """Handle pipeline messages"""
        t = message.type
        if t == Gst.MessageType.EOS:
            self.logger.warning("End of stream reached")

            
        elif t == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            error_msg = err.message
            debug_info = debug
            element_name = message.src.get_name()
            element_state = message.src.get_state(0)[1].value_nick
            self.logger.error(f"Pipeline error from element {element_name} (state: {element_state})")
            self.logger.error(f"Error message: {error_msg}")
            self.logger.error(f"Debug info: {debug_info}")
            

            # Handle watchdog timeout
            if ('Watchdog triggered' in error_msg):
                self.logger.error(f"Watchdog timeout detected - no data received for 5 seconds from {element_name}")
                self._handle_watchdog_timeout()

        elif t == Gst.MessageType.WARNING:
            warn, debug = message.parse_warning()
            self.logger.warning(f"Pipeline warning: {warn.message}")
            self.logger.warning(f"Debug info: {debug}")
        elif t == Gst.MessageType.STATE_CHANGED:
            if message.src == self.pipeline:
                old_state, new_state, pending_state = message.parse_state_changed()
                self.logger.info(f"Pipeline state changed from {old_state.value_nick} "
                               f"to {new_state.value_nick} (pending: {pending_state.value_nick})")

                # Reset restart counter when pipeline reaches PLAYING state
                if new_state == Gst.State.PLAYING:
                    self.restart_count = 0
                    self.logger.info("Pipeline reached PLAYING state, reset restart counter")

#main indent
    def _wait_for_shared_memory(self):
        """Wait for shared memory file to be available"""
        input_shm_path = f"{self.socket_dir}/{self.channel_name}_muxed_shm"
        max_retries = 10  # 5 minutes maximum wait time (60 * 5 seconds)
        retry_count = 0
        
        self.logger.info(f"Waiting for shared memory file: {input_shm_path}")
        
        while retry_count < max_retries:
            if os.path.exists(input_shm_path):
                self.logger.info(f"Shared memory file is ready: {input_shm_path}")
                return True
                
            retry_count += 1
            self.logger.debug(f"Shared memory file not ready, retry {retry_count}/{max_retries}")
            time.sleep(5)
        
        self.logger.error(f"Shared memory file not available after {max_retries * 5} seconds")
        return False

#main indent
    def _cleanup_shared_memory(self):
        """Clean up shared memory files"""
        files_to_cleanup = [
            f"{self.socket_dir}/{self.channel_name}_transcoded_shm"
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


    def _handle_watchdog_timeout(self):
        """Recreate the pipeline with complete reinitialization"""
        self.logger.info("Recreating pipeline after failure with full reset")

        self.restart_count += 1
        self.logger.warning(f"Attempting pipeline restart {self.restart_count}/{self.MAX_RESTART_ATTEMPTS}")
        
        if self.restart_count >= self.MAX_RESTART_ATTEMPTS:
            self.logger.error(f"Maximum restart attempts ({self.MAX_RESTART_ATTEMPTS}) reached. Exiting.")
            self.cleanup()
            sys.exit(1)
        
        try:
            # 1. Full cleanup of existing pipeline
            if self.pipeline:
                self.pipeline.set_state(Gst.State.NULL)
                self.pipeline.get_state(Gst.CLOCK_TIME_NONE)  # Wait for completion
                self.pipeline = None
            
            # 2. Clear all elements and their references
            for element in self.elements.values():
                element.set_state(Gst.State.NULL)
            self.elements.clear()
            
            # 3. Reset GStreamer system
            #Gst.deinit()
            Gst.init(None)
            
            # 4. Reset internal state variables
            
            # Clean up shared memory
            self._cleanup_shared_memory()
            
            # 5. Short delay to ensure complete cleanup
            time.sleep(1)
            
            # 6. Recreate pipeline fresh
            
            self.create_pipeline()
            
            # Set up message handling
            bus = self.pipeline.get_bus()
            bus.add_signal_watch()
            bus.connect("message", self.on_message)
            
            # Generate DOT file before starting
            self.generate_dot_file("before_playing")


            # Wait for shared memory to be available before creating pipeline
            if not self._wait_for_shared_memory():
                self.logger.error("Shared memory file not available - cannot start pipeline")
                sys.exit(0)
            
            # Start the pipeline
            self.logger.info("Setting pipeline to PLAYING state")
            ret = self.pipeline.set_state(Gst.State.PLAYING)
            if ret == Gst.StateChangeReturn.FAILURE:
                raise RuntimeError("Failed to set pipeline to PLAYING state")
            elif ret == Gst.StateChangeReturn.ASYNC:
                self.logger.info("Pipeline is ASYNC, waiting for state change...")
                ret = self.pipeline.get_state(Gst.CLOCK_TIME_NONE)
                self.logger.info(f"Pipeline state change complete: {ret}")
            
            # Generate DOT file after pipeline is playing
            self.generate_dot_file("after_playing")
            
            # Run the main loop
            loop = GLib.MainLoop()
            try:
                loop.run()
            except KeyboardInterrupt:
                self.logger.info("Keyboard interrupt received")
                # Generate final DOT file before cleanup
                self.generate_dot_file("final_state")
            finally:
                self.cleanup()
                
        except Exception as e:
            self.logger.error(f"Error in transcode pipeline: {str(e)}")
            # Generate DOT file on error
            self.generate_dot_file("error_state")
            self.cleanup()
            raise

    def cleanup(self):
        """Cleanup resources"""
        self.logger.info("Starting cleanup")
        
        if self.pipeline:
            self.pipeline.set_state(Gst.State.NULL)
            self.logger.info("Pipeline stopped")

        # Remove socket files
        paths_to_remove = [
            f"{self.socket_dir}/{self.channel_name}_transcoded_shm"
        ]
        
        for path in paths_to_remove:
            try:
                if os.path.exists(path):
                    os.unlink(path)
                    self.logger.debug(f"Removed file: {path}")
            except Exception as e:
                self.logger.error(f"Error removing file {path}: {str(e)}")

        # Close file descriptors
        for fd_name, fd in self.fds.items():
            try:
                os.close(fd)
                self.logger.debug(f"Closed {fd_name} file descriptor: {fd}")
            except Exception as e:
                self.logger.error(f"Error closing file descriptor for {fd_name}: {str(e)}")

        self.logger.info("Cleanup completed")


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(description="Transcoder Pipeline Handler")
    parser.add_argument("channel", help="Channel name from the configuration")
    parser.add_argument("--source-index", type=int, default=0,
                       help="Index of the input source to use (default: 0)")
    parser.add_argument("--log-dir", default="logs", 
                       help="Directory to store log files")
    parser.add_argument("--log-level", 
                       choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
                       default='INFO', 
                       help="Set the logging level")
    
    args = parser.parse_args()
    
    # Initialize logging
    logger = setup_logging(args.channel, args.log_dir, args.log_level)
    logger.info(f"Starting Transcoder for channel: {args.channel}, source index: {args.source_index}")

        
    try:
        # Load configuration
        config = Configuration()
        
        # Check if transcoding is needed
        if check_passthrough(config, args.channel):
            logger.info("Both audio and video set to passthrough, transcoding not needed")
            sys.exit(0)

        # Create handler
        handler = TranscodePipelineHandler(args.channel, args.source_index)
        
        # Wait for shared memory and codec info
        if not handler._wait_for_shared_memory():
            logger.error("Shared memory not available after timeout, exiting")
            sys.exit(1)
            
        if not handler._wait_for_codec_info():
            logger.error("Codec info files not available after timeout, exiting")
            sys.exit(1)
            
        # Start the pipeline
        handler.run()
        
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Fatal error: {str(e)}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()