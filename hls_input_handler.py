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
from pathlib import Path
from stats_collector import StatsCollector

def setup_logging(channel_name, log_dir='logs', log_level='INFO'):
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

#main indent
class InputPipelineHandler:
    def __init__(self, channel_name, source_index=0):
        self.logger = logging.getLogger(__name__)
        self.channel_name = channel_name
        self.source_index = source_index
        
        # Set up DOT file directory
        self.dot_dir = "/root/caricoder/dot"
        Path(self.dot_dir).mkdir(parents=True, exist_ok=True)
        os.environ['GST_DEBUG_DUMP_DOT_DIR'] = self.dot_dir
        
        # Initialize configuration
        self.config = Configuration()
        self.channel_settings = self.config.get_channel_settings(channel_name)
        self.pipeline = None
        self.elements = {}
        self.socket_paths = []
        
        # Initialize GStreamer
        Gst.init(None)
        
        # Get inputs configuration
        self.inputs = self.channel_settings.get('inputs', [])
        if not self.inputs:
            raise ValueError(f"No inputs defined for channel: {channel_name}")
        
        if self.source_index < 0 or self.source_index >= len(self.inputs):
            raise ValueError(f"Invalid source index {source_index}")
        
        self.selected_input = self.inputs[self.source_index]
        
        # Verify input type is HLS
        if self.selected_input['type'] != 'hlssrc':
            raise ValueError("Only HLS input type is supported")
        
        # Initialize Redis for stats
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

#main indent
    def analyze_stream(self):
        """Analyze the HLS stream using ffprobe to detect codecs and stream properties."""
        input_config = self.selected_input
        uri = input_config.get('uri')
        
        self.logger.info(f"Analyzing HLS stream: URI={uri}")

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
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            
            if result.returncode != 0:
                raise RuntimeError(f"ffprobe failed with return code {result.returncode}")
                
            probe_data = json.loads(result.stdout)
            
            # Find video and audio streams
            video_stream = next((s for s in probe_data.get('streams', []) 
                               if s['codec_type'] == 'video'), None)
            audio_stream = next((s for s in probe_data.get('streams', []) 
                               if s['codec_type'] == 'audio'), None)
            
            if not video_stream or not audio_stream:
                raise RuntimeError("No valid video or audio streams found")
                
            # Get program info if available
            program = probe_data.get('programs', [{}])[0]
            
            # Extract needed info
            video_codec = video_stream.get('codec_name')
            audio_codec = audio_stream.get('codec_name')
            program_number = program.get('program_num', 0)
            
            # For HLS we don't use PIDs in the same way as MPEG-TS
            video_pid = 'auto'
            audio_pid = 'auto'
            
            # Store complete probe data
            self._store_codec_info(video_codec, audio_codec, video_pid, audio_pid, program_number, probe_data)
            
            return video_codec, audio_codec, video_pid, audio_pid, program_number

        except Exception as e:
            self.logger.error(f"Stream analysis failed: {str(e)}")
            raise

#main indent
    def create_pipeline(self):
        """Create and configure the GStreamer pipeline."""
        self.logger.info("Creating pipeline")
        self.pipeline = Gst.Pipeline.new("hls_pipeline")

        self._create_elements()
        self._link_static_elements()
        
        # Connect to pad-added signals for dynamic linking
        self.elements['hlsdemux'].connect("pad-added", self.on_pad_added)
        self.elements['tsdemux'].connect("pad-added", self.on_pad_added)

        # Set up message handling
        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self.on_pipeline_message)

        self.logger.info("Pipeline created successfully")
        self.setup_stats_collection()

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
                    'type': 'hlssrc',
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
    def generate_dot_file(self, filename):
        """Generate a DOT file of the current pipeline state"""
        try:
            # Add channel name to the filename
            full_filename = f"{self.channel_name}_input_{filename}"
            
            # Generate DOT file
            Gst.debug_bin_to_dot_file(
                self.pipeline,
                Gst.DebugGraphDetails.ALL,
                full_filename
            )
            
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
                self.logger.info(f"Generated pipeline visualization: {png_path}")
            except subprocess.CalledProcessError as e:
                self.logger.error(f"Failed to convert DOT to PNG: {e.stderr}")
            except FileNotFoundError:
                self.logger.error("graphviz 'dot' command not found")
                
        except Exception as e:
            self.logger.error(f"Error generating pipeline visualization: {str(e)}")

#main indent
    def _create_elements(self):
        """Create and configure all GStreamer elements for the pipeline."""
        self.logger.info("Creating GStreamer elements")
        
        # Create basic pipeline elements 
        self.elements.update({
            'source': Gst.ElementFactory.make("souphttpsrc", "source"),
            'queue1': Gst.ElementFactory.make("queue", "queue1"),
            'hlsdemux': Gst.ElementFactory.make("hlsdemux", "hlsdemux"),
            'queue2': Gst.ElementFactory.make("queue", "queue2"),
            'tsparse': Gst.ElementFactory.make("tsparse", "tsparse"),
            'tsdemux': Gst.ElementFactory.make("tsdemux", "tsdemux"),
            'video_queue1': Gst.ElementFactory.make("queue", "video_queue1"),
            'video_watchdog': Gst.ElementFactory.make("watchdog", "video_watchdog"),
            'audio_queue1': Gst.ElementFactory.make("queue", "audio_queue1"),
            'audio_watchdog': Gst.ElementFactory.make("watchdog", "audio_watchdog"),
            'audio_queue2': Gst.ElementFactory.make("queue", "audio_queue2"),
            'mpegtsmux': Gst.ElementFactory.make("mpegtsmux", "mux"),
            'identity': Gst.ElementFactory.make("identity", "identity"),
            'final_queue': Gst.ElementFactory.make("queue", "final_queue"),
            'output_watchdog': Gst.ElementFactory.make("watchdog", "output_watchdog"),
            'shmsink': Gst.ElementFactory.make("shmsink", "shmsink")
        })

        # Create video parser based on detected codec
        if self.video_codec == 'h264':
            self.elements['video_parser'] = Gst.ElementFactory.make("h264parse", "video_parser")
        elif self.video_codec in ['hevc', 'h265']:
            self.elements['video_parser'] = Gst.ElementFactory.make("h265parse", "video_parser")
        elif self.video_codec == 'mpeg2video':
            self.elements['video_parser'] = Gst.ElementFactory.make("mpegvideoparse", "video_parser")
        else:
            raise ValueError(f"Unsupported video codec: {self.video_codec}")

        # Create audio parser based on detected codec  
        if self.audio_codec == 'aac':
            self.elements['audio_parser'] = Gst.ElementFactory.make("aacparse", "audio_parser")
        elif self.audio_codec in ['mp2', 'mp3']:
            self.elements['audio_parser'] = Gst.ElementFactory.make("mpegaudioparse", "audio_parser")
        else:
            raise ValueError(f"Unsupported audio codec: {self.audio_codec}")

        self.elements['video_queue2'] = Gst.ElementFactory.make("queue", "video_queue2")

        # Verify parser creation
        if not self.elements['video_parser']:
            raise RuntimeError(f"Failed to create parser for video codec: {self.video_codec}")
        if not self.elements['audio_parser']:
            raise RuntimeError(f"Failed to create parser for audio codec: {self.audio_codec}")

        # Configure source
        self.elements['source'].set_property('location', self.selected_input['uri'])
        self.elements['source'].set_property('is-live', True)

        # Configure tsparse
        #self.elements['tsparse'].set_property('set-timestamps', True)
        #self.elements['tsparse'].set_property('smoothing-latency', 1000)

        # Configure watchdogs
        self.elements['video_watchdog'].set_property('timeout', 50000)
        self.elements['audio_watchdog'].set_property('timeout', 50000)
        self.elements['output_watchdog'].set_property('timeout', 50000)

        # Configure identity for stats
        self.elements['identity'].set_property('sync', True)
        self.elements['identity'].set_property('silent', False)

        # Configure queues
        queue_config = {
            'max-size-time': 300000000000,
            'max-size-buffers': 1000000,
            'leaky': 1
        }

        for name in ['queue1', 'queue2', 'video_queue1', 'video_queue2', 
                    'audio_queue1', 'audio_queue2', 'final_queue']:
            for prop, value in queue_config.items():
                self.elements[name].set_property(prop, value)

        # Configure muxer
        self.elements['mpegtsmux'].set_property('alignment', 7)

        # Configure shared memory sink
        shm_path = f"{self.socket_dir}/{self.channel_name}_muxed_shm"
        self.elements['shmsink'].set_property('socket-path', shm_path)
        self.elements['shmsink'].set_property('wait-for-connection', False)
        self.elements['shmsink'].set_property('sync', False)
        self.elements['shmsink'].set_property('async', False)
        self.elements['shmsink'].set_property('shm-size', 2000000)


        # Add all elements to pipeline
        for element in self.elements.values():
            self.pipeline.add(element)

#main indent
    def _link_static_elements(self):
        """Link the static elements in the pipeline."""
        # Link initial chain
        elements_to_link = [
            ('source', 'queue1'),
            ('queue1', 'hlsdemux')
        ]
        
        for src, dest in elements_to_link:
            if not self.elements[src].link(self.elements[dest]):
                raise RuntimeError(f"Failed to link {src} to {dest}")

        # Link final chain
        final_chain = [
            ('mpegtsmux', 'identity'),
            ('identity', 'final_queue'),
            ('final_queue', 'output_watchdog'),
            ('output_watchdog', 'shmsink')
        ]

        for src, dest in final_chain:
            if not self.elements[src].link(self.elements[dest]):
                raise RuntimeError(f"Failed to link {src} to {dest}")

#main indent
    def on_pad_added(self, element, pad):
        """Handle dynamic pad connections from demuxers."""
        pad_name = pad.get_name()
        self.logger.info(f"New pad added: {pad_name}")

        if element == self.elements['hlsdemux']:
            sink_pad = self.elements['queue2'].get_static_pad("sink")
            if pad.link(sink_pad) == Gst.PadLinkReturn.OK:
                # Link the rest of the chain
                if not self.elements['queue2'].link(self.elements['tsparse']):
                    self.logger.error("Failed to link queue2 to tsparse")
                    return
                if not self.elements['tsparse'].link(self.elements['tsdemux']):
                    self.logger.error("Failed to link tsparse to tsdemux")
                    return
                self.logger.info("Successfully linked HLS demuxer chain")

        elif element == self.elements['tsdemux']:
            if pad_name.startswith('video'):
                # Link video chain
                sink_pad = self.elements['video_queue1'].get_static_pad("sink")
                if pad.link(sink_pad) == Gst.PadLinkReturn.OK:
                    if not self.elements['video_queue1'].link(self.elements['video_watchdog']):
                        self.logger.error("Failed to link video_queue1 to video_watchdog")
                        return
                    if not self.elements['video_watchdog'].link(self.elements['video_parser']):
                        self.logger.error("Failed to link video_watchdog to video_parser")
                        return
                    if not self.elements['video_parser'].link(self.elements['video_queue2']):
                        self.logger.error("Failed to link video_parser to video_queue2")
                        return
                    if not self.elements['video_queue2'].link(self.elements['mpegtsmux']):
                        self.logger.error("Failed to link video_queue2 to mpegtsmux")
                        return
                    self.logger.info("Successfully linked video chain")
                else:
                    self.logger.error("Failed to link video pad")

            elif pad_name.startswith('audio'):
                # Link audio chain
                sink_pad = self.elements['audio_queue1'].get_static_pad("sink")
                if pad.link(sink_pad) == Gst.PadLinkReturn.OK:
                    if not self.elements['audio_queue1'].link(self.elements['audio_watchdog']):
                        self.logger.error("Failed to link audio_queue1 to audio_watchdog")
                        return
                    if not self.elements['audio_watchdog'].link(self.elements['audio_parser']):
                        self.logger.error("Failed to link audio_watchdog to audio_parser")
                        return
                    if not self.elements['audio_parser'].link(self.elements['audio_queue2']):
                        self.logger.error("Failed to link audio_parser to audio_queue2")
                        return
                    if not self.elements['audio_queue2'].link(self.elements['mpegtsmux']):
                        self.logger.error("Failed to link audio_queue2 to mpegtsmux")
                        return
                    self.logger.info("Successfully linked audio chain")
                else:
                    self.logger.error("Failed to link audio pad")

#main indent
    def setup_stats_collection(self):
        """Initialize stats collection"""
        self.last_bytes = 0
        self.last_time = time.time()
        self.stats = {
            'bitrate_mbps': 0,
            'bytes_received': 0,
            'buffer_level_bytes': 0,
            'buffer_level_time': 0,
            'input_caps': ''
        }

        # Add probe to identity element
        identity_pad = self.elements['identity'].get_static_pad('src')
        if identity_pad:
            identity_pad.add_probe(Gst.PadProbeType.BUFFER, self._stats_probe_cb)
            self.logger.info("Added probe to identity element")

        # Start stats collection timer
        self.stats_timer = GLib.timeout_add(5000, self.collect_stats)
        self.logger.info("Started stats collection timer")

#main indent
    def _stats_probe_cb(self, pad, info):
        """Callback for identity probe - calculates bitrate"""
        buffer = info.get_buffer()
        if buffer:
            self.last_bytes += buffer.get_size()
            
            # Get current caps if we don't have them
            if not self.stats['input_caps']:
                caps = pad.get_current_caps()
                if caps:
                    self.stats['input_caps'] = caps.to_string()
                    self.logger.info(f"Input caps: {self.stats['input_caps']}")

        return Gst.PadProbeReturn.OK

#main indent
    def collect_stats(self):
        """Periodic stats collection and publishing"""
        try:
            current_time = time.time()
            time_diff = current_time - self.last_time

            if time_diff > 0:
                # Calculate bitrate
                bytes_per_sec = self.last_bytes / time_diff
                self.stats['bitrate_mbps'] = (bytes_per_sec * 8) / (1024 * 1024)
                self.stats['bytes_received'] = self.last_bytes

                # Get queue buffer stats
                queue = self.elements['final_queue']
                if queue:
                    self.stats['buffer_level_bytes'] = queue.get_property('current-level-bytes')
                    self.stats['buffer_level_time'] = queue.get_property('current-level-time')

                # Log stats
                self.logger.debug(f"HLS Input Stats: {json.dumps(self.stats, indent=2)}")

                # Store in Redis via StatsCollector
                if self.stats_collector:
                    self.stats_collector.add_stats("hls_input", self.stats)

                # Reset counters
                self.last_bytes = 0
                self.last_time = current_time

        except Exception as e:
            self.logger.error(f"Error collecting stats: {str(e)}")

        return True  # Keep timer running

#main indent
    def run(self):
        """Main run loop"""
        self.logger.info("Starting main run loop")
        
        try:
            # Create and set up pipeline
            (self.video_codec, 
             self.audio_codec, 
             self.video_pid,
             self.audio_pid,
             self.program_number) = self.analyze_stream()
             
            self.logger.debug(f"Using video codec: {self.video_codec}")
            self.logger.debug(f"Using audio codec: {self.audio_codec}")
            self.logger.debug(f"Using program number: {self.program_number}")
            
            self.create_pipeline()
            
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
        
        # Remove socket files and info files
        paths_to_remove = self.socket_paths + [
            f"{self.socket_dir}/{self.channel_name}_video_shm_info",
            f"{self.socket_dir}/{self.channel_name}_audio_shm_info",
            f"{self.socket_dir}/{self.channel_name}_muxed_shm"
        ]
        
        for path in paths_to_remove:
            try:
                if os.path.exists(path):
                    os.unlink(path)
                    self.logger.debug(f"Removed file: {path}")
            except Exception as e:
                self.logger.error(f"Error removing file {path}: {str(e)}")

        # Clean up pipeline
        if self.pipeline:
            self.logger.info("Stopping pipeline")
            self.pipeline.set_state(Gst.State.NULL)
            self.pipeline.get_state(Gst.CLOCK_TIME_NONE)
            self.logger.info("Pipeline stopped")

        self.logger.info("Cleanup completed")

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
                self.logger.error(f"Watchdog timeout detected - {element_name}")
                self._handle_watchdog_timeout()
            else:
                self.logger.error(f"Pipeline error: {error_msg}")
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
                
        elif t == Gst.MessageType.EOS:
            self.logger.warning("End of stream reached")

#main indent
    def _handle_watchdog_timeout(self):
        """Handle pipeline failure and attempt restart"""
        self.logger.info("Handling watchdog timeout")
        try:
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
            for path in self.socket_paths:
                if os.path.exists(path):
                    os.unlink(path)
                    
            # Re-analyze stream and recreate pipeline
            self.create_pipeline()
            
            # Start the pipeline
            ret = self.pipeline.set_state(Gst.State.PLAYING)
            if ret == Gst.StateChangeReturn.FAILURE:
                raise RuntimeError("Unable to set the pipeline to the playing state")
                
            self.logger.info("Successfully restarted pipeline after watchdog timeout")
            
        except Exception as e:
            self.logger.error(f"Failed to restart pipeline: {str(e)}")
            raise

def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(description="HLS Input Pipeline Handler")
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