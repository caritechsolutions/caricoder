#!/usr/bin/env python3

import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib
from config import Configuration  # Maintain the config class usage
import logging
from logging.handlers import RotatingFileHandler
import subprocess
import redis
import json
import sys
import os
import socket
import time
from datetime import datetime
from urllib.parse import urlparse, urlencode
from stats_collector import StatsCollector
from pathlib import Path





def setup_logging(channel_name, log_dir='logs', log_level='INFO'):
    """
    Set up logging configuration for the application.
    
    Args:
        channel_name (str): Name of the channel for log file identification
        log_dir (str): Directory to store log files
        log_level (str): Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    
    Returns:
        logger: Configured logging instance
    """
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
    """
    Handles the creation and management of a GStreamer pipeline for SRT input streams.
    Analyzes input stream characteristics and sets up appropriate parsing elements.
    """
    def __init__(self, channel_name, source_index=0):
        """
        Initialize the input handler with channel configuration and source selection.
        
        Args:
            channel_name (str): Name of the channel from configuration
            source_index (int): Index of the input source to use from channel configuration
        """
        self.logger = logging.getLogger(__name__)
        self.channel_name = channel_name


        # Set up DOT file directory before any pipeline operations
        self.dot_dir = "/root/caricoder/dot"
        Path(self.dot_dir).mkdir(parents=True, exist_ok=True)
        os.environ['GST_DEBUG_DUMP_DOT_DIR'] = self.dot_dir
        self.logger.info(f"Set up DOT file directory at {self.dot_dir}")


        self.source_index = source_index
        self.config = Configuration()
        self.channel_settings = self.config.get_channel_settings(channel_name)
        self.pipeline = None
        self.elements = {}
        self.socket_paths = []
        self.stats_collector = None
        self.srt_stats_timer = None
        self.video_pid = None
        self.audio_pid = None

        self.fds = {}  # Initialize the fds dictionary
        
        # Initialize GStreamer
        Gst.init(None)

        # Then set debug levels
        #Gst.debug_set_threshold_for_name('tsparse', Gst.DebugLevel.WARNING)
        #Gst.debug_set_threshold_for_name('mpegtsbase', Gst.DebugLevel.WARNING)
        #Gst.debug_set_threshold_for_name('h264parse', Gst.DebugLevel.WARNING)

        
        # Get inputs from configuration
        self.inputs = self.channel_settings.get('inputs', [])
        if not self.inputs:
            raise ValueError(f"No inputs defined for channel: {channel_name}")
        
        if self.source_index < 0 or self.source_index >= len(self.inputs):
            raise ValueError(f"Invalid source index {source_index}")
        
        self.selected_input = self.inputs[self.source_index]
        self.logger.debug(f"Selected input configuration: {self.selected_input}")
        
        # Verify input type is UDP
        if self.selected_input['type'] != 'udpsrc':
            raise ValueError("Only UDP input type is supported")
        
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

    def generate_dot_file(self, filename):
        """
        Generate a DOT file of the current pipeline state and convert it to PNG.
        
        Args:
            filename (str): Base name for the DOT file (without extension)
        """
        try:
            # Add channel name to the filename
            full_filename = f"{self.channel_name}_input_{filename}"
            
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
            self.logger.exception("Full traceback:")  # This will log the full stack trace


#main indent
    def analyze_stream(self):
        """
        Analyze the input stream using ffprobe to detect codecs and PIDs.
        Handles both explicitly configured PIDs and automatic detection with fallback.
        """
        def format_pid(pid):
            """Format PID to ensure it always has four digits after the 'x'."""
            self.logger.debug(f"Formatting PID: {pid}")
            if isinstance(pid, str):
                if pid.startswith('0x'):
                    formatted_pid = '0x' + pid[2:].zfill(4)
                else:
                    formatted_pid = pid.zfill(4)
            else:
                formatted_pid = pid
            self.logger.debug(f"Formatted PID: {formatted_pid}")
            return formatted_pid

        # Get configuration from channel settings
        input_config = self.selected_input
        uri = input_config.get('uri')
        program_number = input_config.get('demux', {}).get('program-number')
        video_pid = format_pid(input_config.get('demux', {}).get('video-pid'))
        audio_pid = format_pid(input_config.get('demux', {}).get('audio-pid'))

        # Log the initial configuration
        self.logger.info(f"Analyzing stream: URI={uri}, Program={program_number}, Video PID={video_pid}, Audio PID={audio_pid}")

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
            self.logger.debug(f"Executing ffprobe command: {' '.join(cmd)}")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            
            if result.returncode != 0:
                self.logger.error(f"ffprobe command failed: {result.stderr}")
                sys.exit(1)
            probe_data = json.loads(result.stdout)
            self.logger.debug(f"Probe data: {json.dumps(probe_data, indent=2)}")
        except subprocess.TimeoutExpired:
            self.logger.error("ffprobe command timed out")
            sys.exit(1)
        except json.JSONDecodeError:
            self.logger.error("Failed to parse ffprobe output")
            sys.exit(1)
        except Exception as e:
            self.logger.error(f"Error during ffprobe execution: {str(e)}")
            sys.exit(1)

        # Find the matching program
        matching_program = None
        for program in probe_data.get('programs', []):
            if program.get('program_id') == program_number:
                matching_program = program
                break

        # If no matching program, use the first one
        if not matching_program and probe_data.get('programs'):
            matching_program = probe_data['programs'][0]
            self.logger.warning(f"Specified program {program_number} not found. Using program {matching_program.get('program_id')}")
            # Reset PIDs as we're using a different program
            video_pid = audio_pid = None

        if not matching_program:
            self.logger.error("No valid program found in the stream")
            sys.exit(1)

        found_program_number = matching_program.get('program_id')
        self.logger.info(f"Using program number: {found_program_number}")

        # Find matching video and audio streams
        video_codec = audio_codec = None
        found_video_pid = found_audio_pid = None
        first_video_pid = first_audio_pid = None

        for stream in matching_program.get('streams', []):
            stream_pid = format_pid(stream.get('id'))
            self.logger.info(f"Found a PID -->: {stream_pid} of type: {stream['codec_type']}")
            
            if stream['codec_type'] == 'video':
                if not video_pid:
                    # No PID was set, use the first video stream encountered
                    video_codec = stream['codec_name']
                    found_video_pid = stream_pid
                    self.logger.info(f"No specific video PID provided. Using first found video PID: {found_video_pid}")
                else:
                    if first_video_pid is None:
                        first_video_pid = stream_pid
                    
                    if stream_pid == video_pid:
                        video_codec = stream['codec_name']
                        found_video_pid = stream_pid
                        self.logger.info(f"Found matching video PID: {found_video_pid}")
            
            elif stream['codec_type'] == 'audio':
                if not audio_pid:
                    # No PID was set, use the first audio stream encountered
                    audio_codec = stream['codec_name']
                    found_audio_pid = stream_pid
                    self.logger.info(f"No specific audio PID provided. Using first found audio PID: {found_audio_pid}")
                else:
                    if first_audio_pid is None:
                        first_audio_pid = stream_pid
                    
                    if stream_pid == audio_pid:
                        audio_codec = stream['codec_name']
                        found_audio_pid = stream_pid
                        self.logger.info(f"Found matching audio PID: {found_audio_pid}")

        # After the loop, check if we need to use the first found PIDs
        if video_pid and not found_video_pid:
            video_codec = next((s['codec_name'] for s in matching_program.get('streams', []) 
                              if format_pid(s.get('id')) == first_video_pid), None)
            found_video_pid = first_video_pid
            self.logger.warning(f"No exact match for video PID {video_pid}. Using first found PID: {found_video_pid}")

        if audio_pid and not found_audio_pid:
            audio_codec = next((s['codec_name'] for s in matching_program.get('streams', []) 
                              if format_pid(s.get('id')) == first_audio_pid), None)
            found_audio_pid = first_audio_pid
            self.logger.warning(f"No exact match for audio PID {audio_pid}. Using first found PID: {found_audio_pid}")

        # Final check
        if not found_video_pid and not found_audio_pid:
            self.logger.error("No matching video or audio streams found")
            sys.exit(1)

        self.logger.info(f"Detected video codec: {video_codec}, PID: {found_video_pid}")
        self.logger.info(f"Detected audio codec: {audio_codec}, PID: {found_audio_pid}")
        self.logger.info(f"Detected Program: {found_program_number}")

        # Store the detected PIDs for use in pad-added handler
        self.video_pid = found_video_pid
        self.audio_pid = found_audio_pid

        # Store codec info before returning
        self._store_codec_info(video_codec, audio_codec, found_video_pid, found_audio_pid, found_program_number)
        
        return video_codec, audio_codec, found_video_pid, found_audio_pid, found_program_number

#main indent
    def create_pipeline(self):
        """Create and configure the GStreamer pipeline based on stream analysis"""
        self.logger.info("About to check source details")
        self.video_codec, self.audio_codec, self.video_pid, self.audio_pid, self.program_number = self.analyze_stream()
 
        self.logger.info("Creating pipeline")
        self.pipeline = Gst.Pipeline.new("caricoder_pipeline")

        # Create and add elements
        self._create_elements()

        # Link static elements
        self._link_static_elements()

        # Connect pad-added signal for dynamic linking
        self.elements['tsdemux'].connect("pad-added", self.on_pad_added)

        self.setup_stats_collection()


#main indent
    def _store_codec_info(self, video_codec, audio_codec, video_pid, audio_pid, program_number):
        """Store codec information in JSON files for reference."""
        # Create and store video info
        video_info = {
            'codec': video_codec,
            'pid': video_pid,
            'program_number': program_number
        }
        
        # Create and store audio info
        audio_info = {
            'codec': audio_codec,
            'pid': audio_pid,
            'program_number': program_number
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
    def _create_elements(self):
        """
        Create and configure all GStreamer elements for the pipeline.
        Sets up UDP source, queues, parsers, and sinks based on stream analysis.
        """
        self.logger.info("Creating GStreamer elements")
        
        # Create UDP source element
        udp_settings = self.selected_input
        udp = Gst.ElementFactory.make("udpsrc", "source")
        
        # Configure UDP URI and properties
        base_uri = udp_settings.get('uri', '')
        
        

        self.logger.info(f"Base UDP URI: {base_uri}")
        udp.set_property('uri', base_uri)
        #udp.set_property('do-timestamp', False)
        self.elements['source'] = udp

        # Create common pipeline elements
        self.elements.update({
            'queue1': Gst.ElementFactory.make("queue", "queue1"),
            'identity': Gst.ElementFactory.make("identity", "identity"),
            'jitterqueue': Gst.ElementFactory.make("queue", "jitterqueue"),
            'tsparse': Gst.ElementFactory.make("tsparse", "tsparse"),
            'queue2': Gst.ElementFactory.make("queue", "queue2"),
            'tsdemux': Gst.ElementFactory.make("tsdemux", "tsdemux"),
            'mpegtsmux': Gst.ElementFactory.make("mpegtsmux", "mux"),
            'final_queue1': Gst.ElementFactory.make("queue", "final_queue1"),
            'final_tsparse': Gst.ElementFactory.make("tsparse", "final_tsparse"),
            'final_queue2': Gst.ElementFactory.make("queue", "final_queue2"),
            'shmsink': Gst.ElementFactory.make("shmsink", "shmsink")
        })

        
        
        # Create queues with proper buffering for sync
        self.elements['video_queue1'] = Gst.ElementFactory.make("queue", "video_queue1")
        self.elements['audio_queue1'] = Gst.ElementFactory.make("queue", "audio_queue1")

        self.elements['video_queue2'] = Gst.ElementFactory.make("queue", "video_queue2")
        self.elements['audio_queue2'] = Gst.ElementFactory.make("queue", "audio_queue2")

        
        


        # increase udp buffer-size, hopefully help with a buffer
        self.elements['source'].set_property("buffer-size", 2097152)
 
        if self.program_number:
            self.elements['tsdemux'].set_property('program-number', self.program_number)
            self.logger.info(f"Set tsdemux to use program number: {self.program_number}")


        # Configure queues for better sync
        self.elements['queue1'].set_property("leaky", 1) 
        self.elements['queue1'].set_property("max-size-buffers", 0)
        self.elements['queue1'].set_property("max-size-time", 3000000000)
        self.elements['queue1'].set_property("max-size-bytes", 0) 

        self.elements['jitterqueue'].set_property("leaky", 1) 
        self.elements['jitterqueue'].set_property("max-size-buffers", 0)
        self.elements['jitterqueue'].set_property("max-size-time", 3000000000)
        self.elements['jitterqueue'].set_property("max-size-bytes", 0) 


        self.elements['queue2'].set_property("leaky", 1) 
        self.elements['queue2'].set_property("max-size-buffers", 0)
        self.elements['queue2'].set_property("max-size-time", 3000000000)
        self.elements['queue2'].set_property("max-size-bytes", 0) 

        self.elements['final_queue1'].set_property("leaky", 1) 
        self.elements['final_queue1'].set_property("max-size-buffers", 0)
        self.elements['final_queue1'].set_property("max-size-time", 3000000000)
        self.elements['final_queue1'].set_property("max-size-bytes", 0) 

        self.elements['final_queue2'].set_property("leaky", 1) 
        self.elements['final_queue2'].set_property("max-size-buffers", 0)
        self.elements['final_queue2'].set_property("max-size-time", 3000000000)
        self.elements['final_queue2'].set_property("max-size-bytes", 0) 


        self.elements['video_queue1'].set_property("leaky", 1) 
        self.elements['video_queue1'].set_property("max-size-buffers", 0)
        self.elements['video_queue1'].set_property("max-size-time", 3000000000)
        self.elements['video_queue1'].set_property("max-size-bytes", 0) 

        self.elements['audio_queue1'].set_property("leaky", 1) 
        self.elements['audio_queue1'].set_property("max-size-buffers", 0)
        self.elements['audio_queue1'].set_property("max-size-time", 3000000000)
        self.elements['audio_queue1'].set_property("max-size-bytes", 0)

        self.elements['video_queue2'].set_property("leaky", 1) 
        self.elements['video_queue2'].set_property("max-size-buffers", 0)
        self.elements['video_queue2'].set_property("max-size-time", 3000000000)
        self.elements['video_queue2'].set_property("max-size-bytes", 0) 

        self.elements['audio_queue2'].set_property("leaky", 1) 
        self.elements['audio_queue2'].set_property("max-size-buffers", 0)
        self.elements['audio_queue2'].set_property("max-size-time", 3000000000)
        self.elements['audio_queue2'].set_property("max-size-bytes", 0)



       
        
        # Configure muxer for better sync
        self.elements['mpegtsmux'].set_property("alignment", 7)  # Align on GOP
        
            
        

        # Create shared memory path
        shm_path = f"{self.socket_dir}/{self.channel_name}_muxed_shm"
        self.socket_paths = [shm_path]

        # Remove existing socket file if it exists
        if os.path.exists(shm_path):
            try:
                os.unlink(shm_path)
                self.logger.debug(f"Removed existing socket: {shm_path}")
            except Exception as e:
                self.logger.warning(f"Could not remove socket {shm_path}: {e}")

       

        # Create appropriate parser elements based on detected codecs
        self._create_codec_parsers()

        
        # Configure shared memory sink
        self.elements['shmsink'].set_property('socket-path', shm_path)
        self.elements['shmsink'].set_property('wait-for-connection', False)
        self.elements['shmsink'].set_property('async', True)
        self.elements['shmsink'].set_property('sync', True)
        self.elements['shmsink'].set_property('shm-size', 2000000)  # 2MB buffer

        self.logger.info(f"Created shared memory socket: {shm_path}")

        # Add all elements to pipeline
        for element in self.elements.values():
            self.pipeline.add(element)

    def _create_codec_parsers(self):
        """
        Create appropriate parser elements based on detected video and audio codecs.
        Maps common codecs to their corresponding GStreamer parser elements.
        """
        # Video codec parser mapping
        video_parser_map = {
            'h264': 'h264parse',
            'hevc': 'h265parse',
            'mpeg2video': 'mpegvideoparse'
        }
        
        # Audio codec parser mapping
        audio_parser_map = {
            'aac': 'aacparse',
            'mp3': 'mpegaudioparse',
            'mp2': 'mpegaudioparse',
            'ac3': 'ac3parse'
        }
        
        # Create video parser based on detected codec
        if self.video_codec in video_parser_map:
            parser_type = video_parser_map[self.video_codec]
            self.elements['video_parser'] = Gst.ElementFactory.make(parser_type, "video_parser")

            

            self.logger.info(f"Created video parser: {parser_type} for codec {self.video_codec}")
        else:
            self.logger.warning(f"Unsupported video codec: {self.video_codec}")
            
        # Create audio parser based on detected codec
        if self.audio_codec in audio_parser_map:
            parser_type = audio_parser_map[self.audio_codec]
            self.elements['audio_parser'] = Gst.ElementFactory.make(parser_type, "audio_parser")
            self.logger.info(f"Created audio parser: {parser_type} for codec {self.audio_codec}")
        else:
            self.logger.warning(f"Unsupported audio codec: {self.audio_codec}")

    def _link_static_elements(self):
        """
        Link the static (non-dynamic) elements in the pipeline.
        These are elements that don't require dynamic pad linking.
        """
        # Link source elements
        if not self.elements['source'].link(self.elements['queue1']):
            raise RuntimeError("Failed to link source to queue1")

        if not self.elements['queue1'].link(self.elements['identity']):
            raise RuntimeError("Failed to link queue1 to identity")

        if not self.elements['identity'].link(self.elements['jitterqueue']):
            raise RuntimeError("Failed to link identity to jitterqueue")


        if not self.elements['jitterqueue'].link(self.elements['tsparse']):
            raise RuntimeError("Failed to link jitterqueue to tsparse")

        if not self.elements['tsparse'].link(self.elements['queue2']):
            raise RuntimeError("Failed to link tsparse to queue2")

        if not self.elements['queue2'].link(self.elements['tsdemux']):
            raise RuntimeError("Failed to link queue2 to tsdemux")


        # Link muxer output chain
        if not self.elements['mpegtsmux'].link(self.elements['final_queue1']):
            raise RuntimeError("Failed to link mpegtsmux to final_queue1")

        if not self.elements['final_queue1'].link(self.elements['final_tsparse']):
            raise RuntimeError("Failed to link final_queue1 to final_tsparse")

        if not self.elements['final_tsparse'].link(self.elements['final_queue2']):
            raise RuntimeError("Failed to link final_tsparse to final_queue2")

        if not self.elements['final_queue2'].link(self.elements['shmsink']):
            raise RuntimeError("Failed to link final_queue2 to shmsink")


        self.logger.info("Successfully linked static elements")

#main indent
    def on_pad_added(self, element, pad):
        """
        Handle dynamic pad connections from demuxer.
        Uses stored PIDs to ensure correct stream selection and links through to muxer.
        """
        pad_name = pad.get_name()
        self.logger.info(f"New pad added to tsdemux: {pad_name}")

        # Get the capabilities of the pad
        caps = pad.get_current_caps()
        if caps:
            structure = caps.get_structure(0)
            caps_name = structure.get_name()
            self.logger.info(f"Pad caps: {caps_name}")

        # Extract PID from pad name
        pad_pid = '0x' + pad_name.split('_')[-1].zfill(4)

        if pad_name.startswith("video"):
            if pad_pid == self.video_pid:
                sink_pad = self.elements['video_queue1'].get_static_pad("sink")
                if not sink_pad.is_linked():
                    if pad.link(sink_pad) == Gst.PadLinkReturn.OK:
                        self.logger.info(f"Successfully linked video pad with PID: {pad_pid}")
                        # Link the rest of the video chain
                        self.elements['video_queue1'].link(self.elements['video_parser'])
                        self.elements['video_parser'].link(self.elements['video_queue2'])
                        self.elements['video_queue2'].link(self.elements['mpegtsmux'])
                        self.logger.info("Linked complete video chain to muxer")
                    else:
                        self.logger.error(f"Failed to link video pad with PID: {pad_pid}")
                else:
                    self.logger.warning(f"Video sink pad already linked, skipping additional video pad {pad_name}")
            else:
                self.logger.info(f"Ignoring video pad with non-matching PID: {pad_pid}")

        elif pad_name.startswith("audio"):
            if pad_pid == self.audio_pid:
                sink_pad = self.elements['audio_queue1'].get_static_pad("sink")
                if not sink_pad.is_linked():
                    if pad.link(sink_pad) == Gst.PadLinkReturn.OK:
                        self.logger.info(f"Successfully linked audio pad with PID: {pad_pid}")
                        # Link the rest of the audio chain
                        self.elements['audio_queue1'].link(self.elements['audio_parser'])
                        self.elements['audio_parser'].link(self.elements['audio_queue2'])
                        self.elements['audio_queue2'].link(self.elements['mpegtsmux'])
                        self.logger.info("Linked complete audio chain to muxer")
                    else:
                        self.logger.error(f"Failed to link audio pad with PID: {pad_pid}")
                else:
                    self.logger.warning(f"Audio sink pad already linked, skipping additional audio pad {pad_name}")
            else:
                self.logger.info(f"Ignoring audio pad with non-matching PID: {pad_pid}")
        else:
            self.logger.info(f"Ignoring pad of unknown type: {pad_name}")    


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

        # Add probe to UDP source
        src_pad = self.elements['source'].get_static_pad('src')
        if src_pad:
            src_pad.add_probe(Gst.PadProbeType.BUFFER, self._src_probe_cb)
            self.logger.info("Added probe to UDP source")

        # Start stats collection timer
        self.stats_timer = GLib.timeout_add(5000, self.collect_stats)
        self.logger.info("Started stats collection timer")

#main indent
    def _src_probe_cb(self, pad, info):
        """Callback for UDP source probe - calculates bitrate"""
        buffer = info.get_buffer()
        if buffer:
            # Update bytes count
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
                queue = self.elements['queue1']
                if queue:
                    self.stats['buffer_level_bytes'] = queue.get_property('current-level-bytes')
                    self.stats['buffer_level_time'] = queue.get_property('current-level-time')

                # Log stats
                self.logger.debug(f"UDP Input Stats: {json.dumps(self.stats, indent=2)}")

                # Store in Redis via StatsCollector
                if self.stats_collector:
                    self.stats_collector.add_stats("udp_input", self.stats)
                    self.logger.debug(f"Stored UDP input stats for channel: {self.channel_name}")

                # Reset counters
                self.last_bytes = 0
                self.last_time = current_time

        except Exception as e:
            self.logger.error(f"Error collecting stats: {str(e)}")
            self.logger.exception("Full stats collection error details:")

        return True  # Keep timer running



    def setup_pipeline_bus(self):
        """
        Setup message bus for pipeline monitoring.
        Adds signal watch and connects message handler.
        """
        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self.on_pipeline_message)
        self.logger.debug("Set up message bus for pipeline")

    def on_pipeline_message(self, bus, message):
        """
        Handle pipeline messages from GStreamer bus.
        Processes errors, warnings, state changes, and end-of-stream messages.
        
        Args:
            bus: The GStreamer bus that sent the message
            message: The message from the bus
        """
        t = message.type
        if t == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            self.logger.error(f"Pipeline error: {err.message}", extra={
                'error_domain': err.domain,
                'error_code': err.code,
                'debug_info': debug,
                'source_element': message.src.get_name(),
                'error_details': {
                    'pipeline_state': self.pipeline.get_state(0)[1].value_nick if self.pipeline else "Unknown",
                    'element_state': message.src.get_state(0)[1].value_nick,
                    'element_name': message.src.get_name()
                }
            })
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


    def run(self):
        """Main run loop"""
        self.logger.info("Starting main run loop")

        # Create and set up pipeline
        self.create_pipeline()

        

        # Generate DOT file before playing
        #self.generate_dot_file("pipeline_initial")

        # Start the pipeline
        ret = self.pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            self.logger.error("Unable to set the pipeline to the playing state")
            return

        # Generate DOT file after pipeline is playing
        # self.generate_dot_file("pipeline_playing")

        

        # Run the main loop
        loop = GLib.MainLoop()
        try:
            loop.run()
        except KeyboardInterrupt:
            self.logger.info("Keyboard interrupt received, stopping CariCoder")
            pass
        finally:
            self.cleanup()

        self.logger.info("CariCoder run method completed")

    def cleanup(self):
        """
        Cleanup resources and shut down pipeline.
        """
        self.logger.info("Starting cleanup process")
        
        # Generate final DOT file before cleanup
        self.generate_dot_file("pipeline_final")
        
        if hasattr(self, 'stats_timer'):
            GLib.source_remove(self.stats_timer)
            self.logger.debug("Removed stats collection timer")

        # Stop pipeline
        if self.pipeline:
            self.logger.info("Stopping pipeline")
            self.pipeline.set_state(Gst.State.NULL)
            self.logger.info("Pipeline stopped")

        # Remove socket files and info files
        paths_to_remove = self.socket_paths + [
            f"{self.socket_dir}/{self.channel_name}_video_shm_info",
            f"{self.socket_dir}/{self.channel_name}_audio_shm_info",
            f"{self.socket_dir}/{self.channel_name}_muxed_shm_info"
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
    """
    Main entry point for the application.
    Parses command line arguments and initializes the input handler.
    """
    # Parse command line arguments
    import argparse
    parser = argparse.ArgumentParser(description="UDP Input Pipeline Handler")
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


def format_pid(pid):
    """
    Format PID to ensure it always has four digits after the 'x'.
    
    Args:
        pid: The PID to format (can be string or integer)
        
    Returns:
        str: Formatted PID string
    """
    if isinstance(pid, str):
        if pid.startswith('0x'):
            formatted_pid = '0x' + pid[2:].zfill(4)
        else:
            formatted_pid = pid.zfill(4)
    else:
        formatted_pid = pid
    return formatted_pid


if __name__ == "__main__":
    main()