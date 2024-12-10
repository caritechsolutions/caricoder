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
from datetime import datetime
from collections import deque
from urllib.parse import urlparse, urlencode
from stats_collector import StatsCollector

def setup_logging(channel_name, log_dir, log_level='INFO'):
    """
    Configure logging with both console and file outputs, preventing duplication
    """
    

    # Create logs directory if it doesn't exist
    #log_dir = 'logs'
    #if not os.path.exists(log_dir):
    #    os.makedirs(log_dir)
    
    # Create a unique log filename with channel name and timestamp
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_file = os.path.join(log_dir, f'caricoder_{channel_name}_{timestamp}.log')
    
    
    # Create formatters
    file_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - [%(levelname)s] - %(message)s\n'
        'Thread: %(threadName)s - Process: %(process)d\n'
        '%(pathname)s:%(lineno)d\n'
    )
    console_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    # Get the root logger
    root_logger = logging.getLogger()
    
    # Remove any existing handlers to prevent duplication
    while root_logger.handlers:
        root_logger.removeHandler(root_logger.handlers[0])
    
    # Create and configure file handler with rotation
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=10*1024*1024,  # 10MB
        backupCount=5
    )
    file_handler.setFormatter(file_formatter)
    file_handler.setLevel(logging.DEBUG)  # File handler always captures detail
    
    # Create console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(console_formatter)
    console_handler.setLevel(log_level)  # Console shows only requested level
    
    # Set root logger level to the lowest level we want to capture
    root_logger.setLevel(min(file_handler.level, getattr(logging, log_level)))
    
    # Add handlers
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)
    
    # Return logger but don't create extra handlers
    return logging.getLogger(__name__)

class PipelineStateAdapter(logging.LoggerAdapter):
    """
    Adapter to inject pipeline state into all log messages
    """
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

class CariCoder:
    def __init__(self, channel_name, source_index=0):
        # Add at the start of __init__, after logger initialization
        self.logger_extra = {'pipeline': None}
        self.base_logger = logging.getLogger(__name__)
        self.logger = PipelineStateAdapter(self.base_logger, self.logger_extra)
        
        # Add message tracking for filtering
        self.last_messages = {}
        self.message_counters = {}
        self.last_log_time = {}
        self.MESSAGE_THROTTLE_INTERVAL = 5  # seconds
        self.logger.info(f"Initializing CariCoder for channel: {channel_name}, source index: {source_index}")
        Gst.init(None)
        self.channel_name = channel_name
        self.source_index = source_index
        self.config = Configuration()
        self.channel_settings = self.config.get_channel_settings(channel_name)
        
        # Retrieve and validate inputs
        self.inputs = self.channel_settings.get('inputs', [])
        if not self.inputs:
            raise ValueError(f"No inputs defined for channel: {channel_name}")
        else:
            self.logger.info(f"Found inputs for {channel_name}, source index: {self.source_index}")
            self.logger.debug(f"Available inputs: {self.inputs}")

        # Validate source index
        if self.source_index < 0 or self.source_index >= len(self.inputs):
            raise ValueError(f"Invalid source index {self.source_index} for channel '{channel_name}'. Valid range: 0-{len(self.inputs) - 1}")
        
        self.selected_input = self.inputs[self.source_index]

        # Initialize other attributes
        self.pipeline = None
        self.elements = {}
        self.probe_pipeline = None
        self.probe_loop = None
        self.detected_caps = None
        self.video_codec = None
        self.audio_codec = None
        self.video_pid = None
        self.audio_pid = None
        self.program_number = None
        self.demux_settings = None
        self.mux_video_pid = None
        self.mux_audio_pid = None
        self.mux_video_pad = None
        self.mux_audio_pad = None
        self.flvmux_video_pid = None
        self.flvmux_audio_pid = None
        self.flvmux_video_pad = None
        self.flvmux_audio_pad = None
        self.output_names = []
        self.output_queue_names = []

        # stats 
        self.stats_collector = None
        self.stats_publisher = None
        self.fault_detector = None
        self.monitoring_thread = None
        self.srt_stats_timer = None

        # Enable DOT file generation
        Gst.debug_set_active(True)
        Gst.debug_set_default_threshold(Gst.DebugLevel.ERROR)

        self.logger.info(f"Initialized CariCoder with input: {self.selected_input}")

        # Initialize Redis client
        try:
            self.redis_client = redis.Redis(host='localhost', port=6379, decode_responses=True)
            self.redis_client.ping()  # Test the connection
            self.logger.info("Successfully connected to Redis")
            self.stats_collector = StatsCollector(channel_name, self.redis_client)
        except redis.ConnectionError:
            self.logger.error("Failed to connect to Redis. Make sure Redis is running.")
            self.redis_client = None
            self.stats_collector = None

    def should_log_message(self, msg_type, msg_content):
        """
        Determine if a message should be logged based on rate limiting and deduplication
        """
        current_time = time.time()
        msg_key = f"{msg_type}_{msg_content}"

        # Check if we've seen this message before
        if msg_key in self.last_messages:
            # If it's the same message, check the time interval
            time_since_last = current_time - self.last_log_time.get(msg_key, 0)
            if time_since_last < self.MESSAGE_THROTTLE_INTERVAL:
                self.message_counters[msg_key] = self.message_counters.get(msg_key, 0) + 1
                return False
            else:
                # If enough time has passed, log the message and the count of skipped messages
                if self.message_counters.get(msg_key, 0) > 0:
                    count = self.message_counters.get(msg_key, 0)
                    self.logger.debug(f"Skipped {count} similar messages for: {msg_type}")
                    self.message_counters[msg_key] = 0

        # Update tracking
        self.last_messages[msg_key] = msg_content
        self.last_log_time[msg_key] = current_time
        return True


    def analyze_stream(self):
        def format_pid(pid):
            """
            Format the PID to ensure it always has four digits after the 'x'.
            """
            self.logger.debug(f"Formatting PID: {pid}")
            if isinstance(pid, str):
                if pid.startswith('0x'):
                    # Remove '0x' prefix, zfill to 4 digits, then add '0x' back
                    formatted_pid = '0x' + pid[2:].zfill(4)
                else:
                    # If it doesn't start with '0x', just zfill to 4 digits
                    formatted_pid = pid.zfill(4)
            else:
                formatted_pid = pid  # Return as-is if not a string (e.g., if it's None)
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

        # Run ffprobe command
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
                        # Store the first video PID found
                        first_video_pid = stream_pid
                    
                    if stream_pid == video_pid:
                        # Found a match
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
                        # Store the first audio PID found
                        first_audio_pid = stream_pid
                    
                    if stream_pid == audio_pid:
                        # Found a match
                        audio_codec = stream['codec_name']
                        found_audio_pid = stream_pid
                        self.logger.info(f"Found matching audio PID: {found_audio_pid}")

        # After the loop, check if we need to use the first found PIDs
        if video_pid and not found_video_pid:
            video_codec = next((s['codec_name'] for s in matching_program.get('streams', []) if format_pid(s.get('id')) == first_video_pid), None)
            found_video_pid = first_video_pid
            self.logger.warning(f"No exact match for video PID {video_pid}. Using first found PID: {found_video_pid}")

        if audio_pid and not found_audio_pid:
            audio_codec = next((s['codec_name'] for s in matching_program.get('streams', []) if format_pid(s.get('id')) == first_audio_pid), None)
            found_audio_pid = first_audio_pid
            self.logger.warning(f"No exact match for audio PID {audio_pid}. Using first found PID: {found_audio_pid}")

        # Final check
        if not found_video_pid and not found_audio_pid:
            self.logger.error("No matching video or audio streams found")
            sys.exit(1)

        self.logger.info(f"Detected video codec: {video_codec}, PID: {found_video_pid}")
        self.logger.info(f"Detected audio codec: {audio_codec}, PID: {found_audio_pid}")
        self.logger.info(f"Detected Program: {found_program_number}")
        
        return video_codec, audio_codec, found_video_pid, found_audio_pid, found_program_number

    def create_pipeline(self):
        self.logger.info("About to check source details")
        self.video_codec, self.audio_codec, self.video_pid, self.audio_pid, self.program_number = self.analyze_stream()
 
        self.logger.info("Creating pipeline")
        self.pipeline = Gst.Pipeline.new("caricoder_pipeline")
        self.logger_extra['pipeline'] = self.pipeline  # Update the pipeline reference

        # Create and add elements
        self._create_elements()

        # Link static elements
        self._link_static_elements()

        # Connect pad-added signal for dynamic linking
        self.elements['tsdemux'].connect("pad-added", self.on_pad_added)

    def _create_elements(self):
        video_codecs = []
        video_options_list = []
        video_resolutions = []
        deinterlace = self.channel_settings['transcoding']['video'].get('deinterlace', False)
    
        for stream in self.channel_settings['transcoding']['video']['streams']:
            video_codecs.append(stream['codec'])
            video_options_list.append(stream.get('options', {}))
            video_resolutions.append(stream.get('resolution'))

        audio_codec = self.channel_settings['transcoding']['audio']['codec']
        audio_options = self.channel_settings['transcoding']['audio'].get('options', {})
        self.logger.info("Creating elements")

        # Source selection
        input_config = self.selected_input
        input_type = input_config['type']
        self.logger.info(f"Source Type in config: {input_type}")
        
        match input_type:
            case 'srtsrc':
                self.logger.info("SRT Source Selected")
                srt_settings = input_config
                srt = Gst.ElementFactory.make("srtsrc", "source")
                
                # Extract base URI and properties
                base_uri = srt_settings.get('uri', '')
                properties = srt_settings.get('options', {})

                # Construct the full URI with properties
                parsed_uri = urlparse(base_uri)
                query_string = urlencode(properties)
                full_uri = f"{parsed_uri.scheme}://{parsed_uri.netloc}{parsed_uri.path}"
                if query_string:
                    full_uri += f"?{query_string}"

                self.logger.info(f"Constructed SRT URI: {full_uri}")

                # Set the full URI
                try:
                    srt.set_property('uri', full_uri)
                    self.logger.info(f"Set SRT URI to: {full_uri}")
                except Exception as e:
                    self.logger.error(f"Failed to set SRT URI: {str(e)}")
                 
                # Final verification of all properties
                self.logger.info("Final SRT property values:")
                for prop in srt.list_properties():
                    prop_name = prop.name
                    if prop_name != 'passphrase':
                        prop_value = srt.get_property(prop_name)
                        self.logger.info(f"  {prop_name}: {prop_value}")

                self.elements['source'] = srt
                actual_latency = srt.get_property('latency')
                self.logger.info(f"Set SRT latency actual value: {actual_latency}")
                self.demux_settings = srt_settings.get('demux', {})
           
            case 'udpsrc':
                self.logger.info("UDP Source Selected") 
                udp_settings = input_config
                udp = Gst.ElementFactory.make("udpsrc", "source")
                self.logger.info(f"Setting source properties: {udp_settings}")
                for key, value in udp_settings.items():
                    if key not in ['type', 'demux', 'options']:
                        udp.set_property(key, value)
                        self.logger.info(f"UDP source setting {key} to {value}")

                self.elements['source'] = udp
                self.demux_settings = udp_settings.get('demux', {})
        
            case _:
                raise ValueError(f"Unsupported input type: {input_type}")

        # Set demux settings
        self.demux_settings = input_config.get('demux', {})
        self.logger.info(f"Demux settings: {self.demux_settings}")
        
        self.logger.info("Source completed")

        # Queue after source
        self.elements['queue1'] = Gst.ElementFactory.make("queue", "queue1")
        self.elements['queue1'].set_property("max-size-time", 500000000000)
        self.elements['queue1'].set_property("max-size-buffers", 1000000)
        self.elements['queue1'].set_property("leaky", 1)

        self.logger.info("queue1 created")

# TSParse
        self.elements['tsparse1'] = Gst.ElementFactory.make("tsparse", "tsparse1")
        self.elements['tsparse1'].set_property("set-timestamps", 1)
        self.elements['tsparse1'].set_property("smoothing-latency", 1000)
        
        self.logger.info("tsparse1 created")

        # TSdemux
        self.elements['tsdemux'] = Gst.ElementFactory.make("tsdemux", "demux")
        self.logger.info("tsdemux created")

        self.logger.info(f"Setting demux properties: {self.demux_settings}")
        if 'program-number' in self.demux_settings:
            self.elements['tsdemux'].set_property('program-number', int(self.program_number))
            self.logger.info(f"tsmux set to program {int(self.program_number)}")

        # Video elements
        self.elements['queue_video'] = Gst.ElementFactory.make("queue", "queue_video")
        self.elements['queue_video'].set_property("max-size-time", 500000000000)
        self.elements['queue_video'].set_property("max-size-buffers", 1000000)
        self.elements['queue_video'].set_property("leaky", 1)
        self.logger.info("queue_video created")

        #check to see which video decoder we will use and witch parser we will use
        match self.video_codec:
            case 'h264':
                self.logger.info(f"Setting video decoding for H264")
                self.elements['videoparse'] = Gst.ElementFactory.make("h264parse", "h264parse")
                self.logger.info("h264parse videoparse created")
                self.elements['videodecode'] = Gst.ElementFactory.make("avdec_h264", "avdec_h264")
                self.logger.info("avdec_h264 videodecode created")
            case 'hevc':
                self.logger.info(f"Setting video decoding for H265")
                self.elements['videoparse'] = Gst.ElementFactory.make("h265parse", "h265parse")
                self.logger.info("h265parse videoparse created")
                match video_codec:
                    case 'passthrough':
                        self.logger.info(f"Video will be passed through no need to decode")
                    case _:
                        self.logger.info(f"Video will be transcoded")
                        self.elements['videodecode'] = Gst.ElementFactory.make("avdec_h265", "avdec_h265")
                        self.logger.info("avdec_h265 videodecode created")
            case 'mpeg2video':
                self.logger.info(f"Setting video decoding for MPEG2")
                self.elements['videoparse'] = Gst.ElementFactory.make("mpegvideoparse", "mpegvideoparse")
                self.logger.info("mpegvideoparse videoparse created")
                match video_codec:
                    case 'passthrough':
                        self.logger.info(f"Video will be passed through no need to decode")
                    case _:
                        self.elements['videodecode'] = Gst.ElementFactory.make("avdec_mpeg2video", "avdec_mpeg2video")
                        self.logger.info("avdec_mpeg2video videodecode created")
            case _:
                raise ValueError(f"Unsupported Video Codec: {self.video_codec}")

        self.elements['passthrough_tee'] = Gst.ElementFactory.make("tee", "passthrough_tee")
        self.elements['passthrough_tee'].set_property('allow-not-linked', 1)

        self.elements['videoconvert'] = Gst.ElementFactory.make("videoconvert", "videoconvert")
        self.logger.info("videoconvert created")

        # Deinterlacing (applicable to all codecs)
        deinterlace = self.channel_settings['transcoding']['video'].get('deinterlace', False)
        match deinterlace:
            case True:
                self.logger.info("Creating deinterlace element")
                self.elements['deinterlace'] = Gst.ElementFactory.make("deinterlace", "deinterlace")
            case _:
                self.logger.info("Deinterlacing is disabled")

        self.elements['pre_video_encoder_tee'] = Gst.ElementFactory.make("tee", "pre_video_encoder_tee")
        self.elements['pre_video_encoder_tee'].set_property('allow-not-linked', 1)
        self.logger.info("tee_video_encoder_out created")

        for i, (video_codec, video_options, resolution) in enumerate(zip(video_codecs, video_options_list, video_resolutions), start=1):
            # Resolution scaling (applicable to all codecs)
            match resolution:
                case {'width': int(width), 'height': int(height)} if width > 0 and height > 0:
                    self.logger.info(f"Creating videoscale element for resolution {width}x{height} for stream {i}")
                    self.elements[f'videoscale{i}'] = Gst.ElementFactory.make("videoscale", f"videoscale{i}")
                    self.elements[f'capsfilter{i}'] = Gst.ElementFactory.make("capsfilter", f"resfilter{i}")
                    caps = Gst.Caps.from_string(f"video/x-raw,width={width},height={height}")
                    self.elements[f'capsfilter{i}'].set_property("caps", caps)
                case _:
                    self.logger.info(f"Resolution scaling is disabled for stream {i}")

            self.logger.info(f"Video decoder setup complete for stream {i}")
        
            # Video encoder setup
            self.logger.info(f"Setting up video encoder for stream {i}")
        
            match video_codec:
                case 'x264enc':
                    self.logger.info(f"Configuring H264 CPU Encoding for stream {i}")
                    self.elements[f'videoenc{i}'] = Gst.ElementFactory.make("x264enc", f"x264enc{i}")
                    self.logger.info(f"x264enc encoder created for stream {i}")
                    for key, value in video_options.items():
                        self.elements[f'videoenc{i}'].set_property(key, value)
                        self.logger.info(f"Video encoder setting {key} to {value} for stream {i}")

                case 'x265enc':
                    self.logger.info(f"Configuring H265 CPU Encoding for stream {i}")
                    self.elements[f'videoenc{i}'] = Gst.ElementFactory.make("x265enc", f"x265enc{i}")
                    self.logger.info(f"x265enc encoder created for stream {i}")
                    for key, value in video_options.items():
                        self.elements[f'videoenc{i}'].set_property(key, value)
                        self.logger.info(f"Video encoder setting {key} to {value} for stream {i}")
                case 'mpeg2enc':
                    self.logger.info(f"Configuring Mpeg2 CPU Encoding for stream {i}")
                    self.elements[f'videoenc{i}'] = Gst.ElementFactory.make("avenc_mpeg2video", f"mpeg2enc{i}")
                    self.logger.info(f"avenc_mpeg2video encoder created for stream {i}")
                    for key, value in video_options.items():
                        self.elements[f'videoenc{i}'].set_property(key, value)
                        self.logger.info(f"Video encoder setting {key} to {value} for stream {i}")
                case 'passthrough':
                    self.logger.info(f"Passing through Video for stream {i}")
                case _:
                    raise ValueError(f"Unsupported video codec: {video_codec} for stream {i}")
        
            self.elements[f'tee_video_out{i}'] = Gst.ElementFactory.make("tee", f"tee_video_out{i}")
            self.elements[f'tee_video_out{i}'].set_property('allow-not-linked', 1)
            self.logger.info(f"tee_video_out{i} created")

            self.elements[f'queue_video_out{i}'] = Gst.ElementFactory.make("queue", f"queue_video_out{i}")
            self.elements[f'queue_video_out{i}'].set_property("max-size-time", 500000000000)
            self.elements[f'queue_video_out{i}'].set_property("max-size-buffers", 1000000)
            self.elements[f'queue_video_out{i}'].set_property("leaky", 1)
            self.logger.info(f"queue_video_out{i} created")

        # Audio elements
        self.elements['queue_audio'] = Gst.ElementFactory.make("queue", "queue_audio")
        self.elements['queue_audio'].set_property("max-size-time", 500000000000)
        self.elements['queue_audio'].set_property("max-size-buffers", 1000000)
        self.elements['queue_audio'].set_property("leaky", 1)
        self.logger.info("queue_audio created")

        # Add audio element creation based on self.audio_codec
        match self.audio_codec:
            case 'mp2':
                self.logger.info(f"Setting audio decoding for MP2")
                self.elements['audioparse'] = Gst.ElementFactory.make("mpegaudioparse", "audioparse")
                self.logger.info("mpegaudioparse created")
                match audio_codec:
                    case 'passthrough':
                        self.logger.info(f"Audio will be passed through no need to decode")
                    case _:
                        self.elements['audiodecode'] = Gst.ElementFactory.make("avdec_mp2float", "audiodecode")
                        self.logger.info("avdec_mp2float created")
                        self.elements['audioconvert'] = Gst.ElementFactory.make("audioconvert", "audioconvert")
                        self.logger.info("audioconvert created")
                        self.elements['audioresample'] = Gst.ElementFactory.make("audioresample", "audioresample")
                        self.logger.info("audioresample created")             
            case 'aac':
                self.logger.info(f"Setting audio decoding for AAC")
                self.elements['audioparse'] = Gst.ElementFactory.make("aacparse", "audioparse")
                self.logger.info("aacparse created")
                match audio_codec:
                    case 'passthrough':
                        self.logger.info(f"Audio will be passed through no need to decode")
                    case _:
                        self.elements['audiodecode'] = Gst.ElementFactory.make("avdec_aac", "audiodecode")
                        self.logger.info("avdec_aac created")
                        self.elements['audioconvert'] = Gst.ElementFactory.make("audioconvert", "audioconvert")
                        self.logger.info("audioconvert created")
                        self.elements['audioresample'] = Gst.ElementFactory.make("audioresample", "audioresample")
                        self.logger.info("audioresample created")                
            case _:
                raise ValueError(f"Unsupported Audio Codec: {self.audio_codec}")

        self.elements['pre_audio_encoder_tee'] = Gst.ElementFactory.make("tee", "pre_audio_encoder_tee")
        self.elements['pre_audio_encoder_tee'].set_property('allow-not-linked', 1)
        self.logger.info("tee_video_out created")

        # Audio encoder setup
        self.logger.info(f"Setting up Audio encoder")
        
        match audio_codec:
            case 'avenc_aac':
                self.logger.info(f"Configuring AAC Encoding")
                self.elements['audioenc'] = Gst.ElementFactory.make("avenc_aac", "avenc_aac")
                self.logger.info("avenc_aac created")
                for key, value in audio_options.items():
                    if key == 'bitrate':
                        value = value * 1000  # Convert kbps to bps
                    self.elements['audioenc'].set_property(key, value)
                    self.logger.info(f"Video encoder setting {key} to {value}")
            case 'avenc_ac3':
                self.logger.info(f"Configuring AC3 Encoding")
                self.elements['audioenc'] = Gst.ElementFactory.make("avenc_ac3", "avenc_ac3")
                self.logger.info("avenc_ac3 created")
                for key, value in audio_options.items():
                    self.elements['audioenc'].set_property(key, value)
                    self.logger.info(f"Video encoder setting {key} to {value}")
            case 'avenc_mp2':
                self.logger.info(f"Configuring MP2 Encoding")
                self.elements['audioenc'] = Gst.ElementFactory.make("avenc_mp2", "mp2enc")
                self.logger.info("avenc_mp2 created")
                for key, value in audio_options.items():
                    if key == 'bitrate':
                        value = value * 1000  # Convert kbps to bps
                    self.elements['audioenc'].set_property(key, value)
                    self.logger.info(f"Video encoder setting {key} to {value}")
            case 'passthrough':
                self.logger.info(f"Passing through Audio")
            case _:
                raise ValueError(f"Unsupported audio codec: {audio_codec}")

        self.elements['queue_audio_out'] = Gst.ElementFactory.make("queue", "queue_audio_out")
        self.elements['queue_audio_out'].set_property("max-size-time", 500000000000)
        self.elements['queue_audio_out'].set_property("max-size-buffers", 1000000)
        self.elements['queue_audio_out'].set_property("leaky", 1)
        self.logger.info("queue_audio_out created")
        
        self.elements['tee_audio_out'] = Gst.ElementFactory.make("tee", "tee_audio_out")
        self.elements['tee_audio_out'].set_property('allow-not-linked', 1)
        self.logger.info("tee_audio_out created")

# Muxer and output elements
        mux_settings = self.config.get_plugin_settings(self.channel_name, 'mpegtsmux')
        self.elements['mpegtsmux'] = Gst.ElementFactory.make("mpegtsmux", "mux")
        self.logger.info("mpegtsmux created")
        
        self.mux_video_pid = mux_settings.get('video-pid', [60])  # Default is now a list with one element
        if not isinstance(self.mux_video_pid, list):
            self.mux_video_pid = [self.mux_video_pid]  # Ensure it's a list
        self.mux_audio_pid = mux_settings.get('audio-pid', 61)
        self.mux_program_number = mux_settings.get('program-number', 1000)
    
        self.logger.info(f"PIDs from config video --> {self.mux_video_pid}  audio --> {self.mux_audio_pid} Program number --> {self.mux_program_number}")

        # Create a new empty GstStructure for the program map
        pm = Gst.Structure.new_empty("program_map")

        # Request sink pads with specific PIDs for video streams
        self.mux_video_pads = []
        for i, video_pid in enumerate(self.mux_video_pid):
            video_pad = self.elements['mpegtsmux'].request_pad_simple(f"sink_{video_pid}")
            self.mux_video_pads.append(video_pad)
            pm.set_value(video_pad.get_name(), self.mux_program_number)
            self.logger.info(f"Created video pad for PID {video_pid}")

        # Request sink pad for audio
        audio_pad = self.elements['mpegtsmux'].request_pad_simple(f"sink_{self.mux_audio_pid}")
        pm.set_value(audio_pad.get_name(), self.mux_program_number)
        self.logger.info(f"Created audio pad for PID {self.mux_audio_pid}")

        # Set the 'prog-map' property of the muxer
        self.elements['mpegtsmux'].set_property("prog-map", pm)
    
        self.logger.info(f"Muxer program map: {pm.to_string()}")
        self.elements['mpegtsmux'].set_property("alignment", 7)
    
        # Set other muxer properties
        for key, value in mux_settings.items():
            if key not in ['video-pid', 'audio-pid', 'program-number', 'type']:
                if key == 'bitrate':
                    value = value * 1000
                self.logger.info(f"Setting mux property: {key} = {value}")
                self.elements['mpegtsmux'].set_property(key, value)
    
        # Store the pads for later use
        self.mux_video_pad = self.mux_video_pads  # Now it's an array of video pads
        self.mux_audio_pad = audio_pad

        self.elements['tsparse2'] = Gst.ElementFactory.make("tsparse", "tsparse2")
        self.elements['tsparse2'].set_property("smoothing-latency", 4000)
        self.logger.info(f"tsparse2 created")

        self.elements['tee'] = Gst.ElementFactory.make("tee", "tee")
        self.elements['tee'].set_property('allow-not-linked', 1)
        self.logger.info(f"tee created")

        output_settings_list = self.config.get_output_settings(self.channel_name)
        output_counters = {}
        name_counter = 0
        
        for output_config in output_settings_list:
            name_counter = name_counter + 1
            output_type = output_config.get('type')
            if output_type not in output_counters:
                output_counters[output_type] = 1
            else:
                output_counters[output_type] += 1
            
            element_name = f"{output_type}{name_counter}"
            queue_name = f"sinkqueue{name_counter}"
            
            try:
                match output_type:
                    case 'udpsink':
                        self.logger.info(f"Setting up for UDP output")
                        self.elements[queue_name] = Gst.ElementFactory.make("queue", queue_name)
                        self.elements[queue_name].set_property("max-size-time", 0)
                        self.elements[queue_name].set_property("max-size-buffers", 2)
                        self.elements[queue_name].set_property("leaky", 2)
                        self.logger.info(f"queue {queue_name} created")
                        self.elements[element_name] = Gst.ElementFactory.make("udpsink", element_name)
                        self.elements[element_name].set_property('buffer-size', 4097152)  # 2MB buffer
                        self.elements[element_name].set_property('max-lateness', 20000000)  # 10ms
                        self.elements[element_name].set_property('async', 0)
                        self.elements[element_name].set_property('sync', 0)
                        self.logger.info(f"queue {element_name} created")
                    case 'rtmpsink':
                        self.logger.info(f"Setting up for RTMP output")
                        self.elements[f"flvmux_audio_queue{name_counter}"] = Gst.ElementFactory.make("queue", f"flvmux_audio_queue{name_counter}")
                        self.elements[f"flvmux_audio_queue{name_counter}"].set_property("max-size-time", 0)
                        self.elements[f"flvmux_audio_queue{name_counter}"].set_property("max-size-buffers", 0)
                        self.elements[f"flvmux_audio_queue{name_counter}"].set_property("leaky", 2)
                        self.logger.info(f"queue flvmux_audio_queue{name_counter} created")

                        self.elements[f"aacparse_audio_queue{name_counter}"] = Gst.ElementFactory.make("aacparse", f"aacparse_audio_queue{name_counter}")
                        self.logger.info(f"aacparse aacparse_video_queue{name_counter} created")

                        self.elements[f"h264parse_video_queue{name_counter}"] = Gst.ElementFactory.make("h264parse", f"h264parse_video_queue{name_counter}")
                        self.logger.info(f"h264parse h264parse_video_queue{name_counter} created")

                        self.elements[f"flvmux_video_queue{name_counter}"] = Gst.ElementFactory.make("queue", f"flvmux_video_queue{name_counter}")
                        self.elements[f"flvmux_video_queue{name_counter}"].set_property("max-size-time", 0)
                        self.elements[f"flvmux_video_queue{name_counter}"].set_property("max-size-buffers", 0)
                        self.elements[f"flvmux_video_queue{name_counter}"].set_property("leaky", 2)
                        self.logger.info(f"queue flvmux_video_queue{name_counter} created")

                        self.elements[f"flvmux{name_counter}"] = Gst.ElementFactory.make("flvmux", f"flvmux{name_counter}")
                        self.elements[f"flvmux{name_counter}"].set_property('streamable', True)
                        self.logger.info(f"flvmux flvmux{name_counter} created")

                        self.elements[queue_name] = Gst.ElementFactory.make("queue", queue_name)
                        self.elements[queue_name].set_property("max-size-time", 0)
                        self.elements[queue_name].set_property("max-size-buffers", 0)
                        self.elements[queue_name].set_property("leaky", 2)
                        self.logger.info(f"queue {queue_name} created")

                        self.elements[element_name] = Gst.ElementFactory.make("rtmp2sink", element_name)
                        self.elements[element_name].set_property('async', 0)
                        self.elements[element_name].set_property('sync', 0)
                        self.logger.info(f"rtmpsink {element_name} created")
                    case 'tcpserversink':
                        self.elements[queue_name] = Gst.ElementFactory.make("queue", queue_name)
                        self.elements[queue_name].set_property("max-size-time", 0)
                        self.elements[queue_name].set_property("max-size-buffers", 0)
                        self.elements[queue_name].set_property("leaky", 2)
                        self.logger.info(f"queue {queue_name} created")
               
                        self.elements[element_name] = Gst.ElementFactory.make("tcpserversink", element_name)
                        self.elements[element_name].set_property('async', 0)
                        self.elements[element_name].set_property('sync', 0)
                        self.logger.info(f"tcpserversink {element_name} created")
                    case 'ristsink':
                        self.logger.info(f"Setting up for RIST output")
                        self.elements[queue_name] = Gst.ElementFactory.make("queue", queue_name)
                        self.elements[queue_name].set_property("max-size-time", 0)
                        self.elements[queue_name].set_property("max-size-buffers", 0)
                        self.elements[queue_name].set_property("leaky", 2)
                        self.logger.info(f"queue {queue_name} created")
                        self.elements[f"rtpmp2tpay{name_counter}"] = Gst.ElementFactory.make("rtpmp2tpay", f"rtpmp2tpay{name_counter}")
                        self.logger.info(f"rtpmp2tpay{name_counter} created")
                        self.elements[element_name] = Gst.ElementFactory.make("ristsink", element_name)
                        self.logger.info(f"queue {element_name} created")
                    case 'srtsink':
                        self.logger.info(f"Setting up for SRT output")
                        self.elements[queue_name] = Gst.ElementFactory.make("queue", queue_name)
                        self.elements[queue_name].set_property("max-size-time", 0)
                        self.elements[queue_name].set_property("max-size-buffers", 0)
                        self.elements[queue_name].set_property("leaky", 2)
                        self.logger.info(f"queue {queue_name} created")
                        self.elements[element_name] = Gst.ElementFactory.make("srtsink", element_name)
                        self.elements[element_name].set_property('blocksize', 2097152)  # 2MB buffer
                        self.elements[element_name].set_property('max-lateness', 10000000)  # 10ms
                        self.elements[element_name].set_property('async', 0)
                        self.elements[element_name].set_property('sync', 0)
                        self.logger.info(f"queue {element_name} created")

                    case _:
                        self.logger.warning(f"Unknown output type '{output_type}'. Skipping this output.")
                        continue
                
                self.logger.info(f"Created {output_type} element: {element_name}")
                
                for key, value in output_config.items():
                    if key != 'type':
                        self.elements[element_name].set_property(key, value)
                        self.logger.info(f"{element_name} setting {key} to {value}")
                
                self.logger.info(f"{element_name} and {queue_name} setup complete")
                
                # Add the element name to the output_names array
                self.output_names.append(element_name)
                self.output_queue_names.append(queue_name)
                self.logger.info(f"Added {element_name} and {queue_name} to output_names array")
                
            except Exception as e:
                self.logger.error(f"Error setting up {output_type} output: {str(e)}")
                continue
        
        if not self.output_names:
            self.logger.warning("No outputs were successfully configured.")
        else:
            self.logger.info(f"Configured outputs: {', '.join(self.output_names)}")

        # Add all elements to pipeline
        for element in self.elements.values():
            self.pipeline.add(element)
            self.logger.info(f"Adding {element} to the pipeline")

    def _link_static_elements(self):
        video_streams = self.channel_settings['transcoding']['video']['streams']
        audio_codec = self.channel_settings['transcoding']['audio']['codec']
        audio_options = self.channel_settings['transcoding']['audio'].get('options', {})
        deinterlace = self.channel_settings['transcoding']['video'].get('deinterlace', False)

        self.logger.info("Linking static elements")
        
        # Link source to demuxer
        self.elements['source'].link(self.elements['queue1'])
        self.logger.info("Linked source to queue1")
        self.elements['queue1'].link(self.elements['tsparse1'])
        self.logger.info("Linked queue1 to tsparse1")
        self.elements['tsparse1'].link(self.elements['tsdemux'])
        self.logger.info("Linked tsparse1 to tsdemux")
        self.elements['queue_video'].link(self.elements['videoparse'])
        self.logger.info("Linked queue_video1 to videoparse")

        # Create and link the passthrough_tee
        self.elements['videoparse'].link(self.elements['passthrough_tee'])
        self.logger.info("Created and linked passthrough_tee")

        # Link passthrough_tee to videodecode and videoconvert
        self.elements['passthrough_tee'].link(self.elements['videodecode'])
        self.elements['videodecode'].link(self.elements['videoconvert'])
        self.logger.info("Linked passthrough_tee to videodecode and videoconvert")

        last_element = 'videoconvert'
        if deinterlace:
            self.logger.info("Linking deinterlace element")
            self.elements['videoconvert'].link(self.elements['deinterlace'])
            last_element = 'deinterlace'

        # Link to pre_video_encoder_tee
        self.elements[last_element].link(self.elements['pre_video_encoder_tee'])
        self.logger.info("Linked to pre_video_encoder_tee")

# Process each video stream
        for i, stream in enumerate(video_streams):
            video_codec = stream['codec']
            resolution = stream.get('resolution')

            if video_codec == 'passthrough':
                self.logger.info(f"Linking passthrough for stream {i+1}")
                self.elements['passthrough_tee'].link(self.elements[f'tee_video_out{i+1}'])
            else:
                self.logger.info(f"Processing stream {i+1}")
                if resolution:
                    self.logger.info(f"Linking videoscale and capsfilter for resolution {resolution['width']}x{resolution['height']}")
                    self.elements['pre_video_encoder_tee'].link(self.elements[f'videoscale{i+1}'])
                    self.elements[f'videoscale{i+1}'].link(self.elements[f'capsfilter{i+1}'])
                    self.elements[f'capsfilter{i+1}'].link(self.elements[f'videoenc{i+1}'])
                else:
                    self.elements['pre_video_encoder_tee'].link(self.elements[f'videoenc{i+1}'])
                
                self.elements[f'videoenc{i+1}'].link(self.elements[f'tee_video_out{i+1}'])
            
            self.elements[f'tee_video_out{i+1}'].link(self.elements[f'queue_video_out{i+1}'])        
            self.logger.info(f"tee_video_out{i+1} to queue_video_out{i+1}")

            self.logger.debug(f"Linking queue_video_out{i+1} to mux pad {self.mux_video_pad[i].get_name()}")
            result = self.elements[f'queue_video_out{i+1}'].link_pads("src", self.elements['mpegtsmux'], self.mux_video_pad[i].get_name())
            self.logger.debug(f"Link result for video stream {i+1}: {result}")

        # Pre-link audio elements
        self.elements['queue_audio'].link(self.elements['audioparse'])
        
        match audio_codec:
            case 'passthrough':
                self.logger.info(f"Audio will be passed through changing linking")
                self.elements['audioparse'].link(self.elements['pre_audio_encoder_tee'])
                self.elements['pre_audio_encoder_tee'].link(self.elements['tee_audio_out'])
                self.elements['tee_audio_out'].link(self.elements['queue_audio_out'])
            case _:
                self.elements['audioparse'].link(self.elements['audiodecode'])
                self.elements['audiodecode'].link(self.elements['audioconvert'])
                self.elements['audioconvert'].link(self.elements['audioresample'])
                self.elements['audioresample'].link(self.elements['pre_audio_encoder_tee'])
                self.elements['pre_audio_encoder_tee'].link(self.elements['audioenc'])
                self.elements['audioenc'].link(self.elements['tee_audio_out'])
                self.elements['tee_audio_out'].link(self.elements['queue_audio_out'])        

        self.logger.debug(f"Linking queue_audio_out to mux pad {self.mux_audio_pad.get_name()}")
        result = self.elements['queue_audio_out'].link_pads("src", self.elements['mpegtsmux'], self.mux_audio_pad.get_name())
        self.logger.debug(f"Link result for audio: {result}")

        # Link muxer to output
        self.elements['mpegtsmux'].link(self.elements['tsparse2'])
        self.logger.info("Linked mpegtsmux to tsparse2")
        self.elements['tsparse2'].link(self.elements['tee'])
        self.logger.info("Linked tsparse2 to tee")

        for index, (output_name, queue_name) in enumerate(zip(self.output_names, self.output_queue_names)):
           
            if 'rtmpsink' in output_name:
                # Extract the number from the rtmpsink name
                rtmp_number = output_name.replace('rtmpsink', '')
                flvmux_audio_queue_name = f"flvmux_audio_queue{rtmp_number}"
                flvmux_video_queue_name = f"flvmux_video_queue{rtmp_number}"
                h264parse_video_queue_name = f"h264parse_video_queue{rtmp_number}"
                aacparse_audio_queue_name = f"aacparse_audio_queue{rtmp_number}"
                flvmux_name = f"flvmux{rtmp_number}"
                
                self.logger.info(f"RTMP Output trying to link audio tee -> {aacparse_audio_queue_name} -> {flvmux_audio_queue_name} -> {flvmux_name}")
                status = self.elements['tee_audio_out'].link(self.elements[aacparse_audio_queue_name])
                self.logger.debug(f"RTMP link result for audio tee to aacparse: {status}")
                status = self.elements[aacparse_audio_queue_name].link(self.elements[flvmux_audio_queue_name])
                self.logger.debug(f"RTMP link result for aacparse to flvmux audio queue: {status}")
                status = self.elements[flvmux_audio_queue_name].link(self.elements[flvmux_name])
                self.logger.debug(f"RTMP link result for flvmux audio queue to mux: {status}")

                self.logger.info(f"RTMP Output trying to link video tee -> {h264parse_video_queue_name} -> {flvmux_video_queue_name} -> {flvmux_name}")
                status = self.elements['tee_video_out'].link(self.elements[h264parse_video_queue_name])
                self.logger.debug(f"RTMP link result for video parse: {status}")
                status = self.elements[h264parse_video_queue_name].link(self.elements[flvmux_video_queue_name])
                self.logger.debug(f"RTMP link result for video: {status}")
                status = self.elements[flvmux_video_queue_name].link(self.elements[flvmux_name])
                self.logger.debug(f"RTMP link result for video queue to mux: {status}")

                status3 = self.elements[flvmux_name].link(self.elements[queue_name])
                status4 = self.elements[queue_name].link(self.elements[output_name])
                
                self.logger.info(f"Linked Flvmux -> {queue_name} = {status3} then to -> {output_name} = {status4}")

            elif 'ristsink' in output_name:
                # Extract the number from the ristsink name
                rist_number = output_name.replace('ristsink', '')
                rtpmp2tpay_name = f"rtpmp2tpay{rist_number}"

                self.logger.info(f"Trying to link tee -> {rtpmp2tpay_name} -> {queue_name} -> {output_name}")
                status = self.elements['tee'].link(self.elements[rtpmp2tpay_name])
                status2 = self.elements[rtpmp2tpay_name].link(self.elements[queue_name])
                status3 = self.elements[queue_name].link(self.elements[output_name])

                self.logger.info(f"Linked tee -> {rtpmp2tpay_name} = {status} then {rtpmp2tpay_name} = {status2} then to -> {output_name} = {status3}")

            else:
                self.logger.info(f"Trying to link tee -> {queue_name} -> {output_name}")
                status = self.elements['tee'].link(self.elements[queue_name])
                status2 = self.elements[queue_name].link(self.elements[output_name])
                self.logger.info(f"Linked tee -> {queue_name} = {status} then to -> {output_name} = {status2}")

            status = status2 = None
        self.logger.info("Linking complete")

    def on_pad_added(self, element, pad):
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
                self.video_codec = self.detect_video_codec(caps)
                self.logger.info(f"Detected video codec: {self.video_codec}")
                sink_pad = self.elements['queue_video'].get_static_pad("sink")
                pad.link(sink_pad)
                self.logger.info(f"Linked video pad with PID: {pad_pid}")
            else:
                self.logger.info(f"Ignoring video pad with non-matching PID: {pad_pid}")

        elif pad_name.startswith("audio"):
            if pad_pid == self.audio_pid:
                self.audio_codec = self.detect_audio_codec(caps)
                self.logger.info(f"Detected audio codec: {self.audio_codec}")
                sink_pad = self.elements['queue_audio'].get_static_pad("sink")
                pad.link(sink_pad)
                self.logger.info(f"Linked audio pad with PID: {pad_pid}")
            else:
                self.logger.info(f"Ignoring audio pad with non-matching PID: {pad_pid}")
        else:
            self.logger.info(f"Ignoring pad of unknown type: {pad_name}")

    def detect_video_codec(self, caps):
        structure = caps.get_structure(0)
        name = structure.get_name()
        if name.startswith('video/x-h264'):
            return 'h264'
        elif name.startswith('video/x-h265'):
            return 'h265'
        elif name.startswith('video/mpeg'):
            return 'mpeg2'
        else:
            return 'unknown'

    def detect_audio_codec(self, caps):
        structure = caps.get_structure(0)
        name = structure.get_name()
        if name.startswith('audio/mpeg'):
            return 'mp3'
        elif name.startswith('audio/x-aac'):
            return 'aac'
        elif name.startswith('audio/x-ac3'):
            return 'ac3'
        else:
            return 'unknown'

    def print_srt_stats(self):
        if 'source' in self.elements and isinstance(self.elements['source'], Gst.Element):
            stats = self.elements['source'].get_property('stats')
            if stats:
                self.logger.info("Raw SRT Statistics structure:")
                self.logger.info(stats.to_string())
                
                stats_dict = {}
                for i in range(stats.n_fields()):
                    field_name = stats.nth_field_name(i)
                    field_value = stats.get_value(field_name)
                    stats_dict[field_name] = self._gvalue_to_python(field_value)
                
                self.logger.info(f"Parsed SRT Statistics: {json.dumps(stats_dict, indent=2)}")
                
                # Store stats using StatsCollector
                if self.stats_collector:
                    self.stats_collector.add_stats("srt_input", stats_dict)
                    self.logger.info(f"Stored SRT stats for channel: {self.channel_name}")
                else:
                    self.logger.warning("StatsCollector is not available. Stats not stored.")
                
            else:
                self.logger.warning("No SRT statistics available")
        else:
            self.logger.warning("SRT source element not found")
        return True  # Keep the timer running

    def _gvalue_to_python(self, gvalue):
        if isinstance(gvalue, Gst.ValueArray):
            return [self._gvalue_to_python(v) for v in gvalue]
        elif isinstance(gvalue, Gst.Structure):
            return {gvalue.nth_field_name(i): self._gvalue_to_python(gvalue.get_value(gvalue.nth_field_name(i))) 
                    for i in range(gvalue.n_fields())}
        elif isinstance(gvalue, gi.overrides.Gst.Fraction):
            return float(gvalue.num) / float(gvalue.denom)
        else:
            return gvalue

    def run(self):
        self.logger.info("Starting CariCoder run method")
        
        self.create_pipeline()

        # Start SRT stats printing
        self.srt_stats_timer = GLib.timeout_add(5000, self.print_srt_stats)  # Print every 5 seconds

        # Set up bus watch
        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self.on_message)

        # Generate DOT file before playing
        self.generate_dot_file("pipeline_initial")

        # Start the pipeline
        ret = self.pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            self.logger.error("Unable to set the pipeline to the playing state")
            return

        # Generate DOT file after pipeline is playing
        self.generate_dot_file("pipeline_playing")

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

    def generate_dot_file(self, filename):
        """Generate a DOT file of the current pipeline state."""
        dot_dir = os.environ.get('GST_DEBUG_DUMP_DOT_DIR')
        if dot_dir:
            if not os.path.exists(dot_dir):
                os.makedirs(dot_dir)
            Gst.debug_bin_to_dot_file(self.pipeline, Gst.DebugGraphDetails.ALL, filename)
            self.logger.info(f"DOT file generated: {filename}")
        else:
            self.logger.warning("GST_DEBUG_DUMP_DOT_DIR not set. Cannot generate DOT file.")

    def on_message(self, bus, message):
        t = message.type
        if t == Gst.MessageType.EOS:
            self.logger.info("End-of-stream reached")
        elif t == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            self.logger.error(f"Error: {err.message}", extra={
                'error_domain': err.domain,
                'error_code': err.code,
                'debug_info': debug,
                'source_element': message.src.get_name(),
                'error_details': {
                    'pipeline_state': self.pipeline.get_state(0)[1].value_nick if self.pipeline else "Unknown",
                    'element_state': message.src.get_state(0)[1].value_nick,
                    'element_name': message.src.get_name(),
                    'element_class': message.src.get_factory().get_class() if message.src.get_factory() else "Unknown"
                }
            })
        elif t == Gst.MessageType.WARNING:
            warn, debug = message.parse_warning()
            self.logger.warning(f"Warning: {warn.message}", extra={
                'warning_domain': warn.domain,
                'warning_code': warn.code,
                'debug_info': debug,
                'source_element': message.src.get_name(),
                'warning_details': {
                    'pipeline_state': self.pipeline.get_state(0)[1].value_nick if self.pipeline else "Unknown",
                    'element_state': message.src.get_state(0)[1].value_nick,
                    'element_name': message.src.get_name(),
                    'element_class': message.src.get_factory().get_class() if message.src.get_factory() else "Unknown"
                }
            })
        elif t == Gst.MessageType.STATE_CHANGED:
            if message.src == self.pipeline:
                old_state, new_state, pending_state = message.parse_state_changed()
                self.logger.info(f"Pipeline state changed from {old_state.value_nick} to {new_state.value_nick}",
                               extra={
                                   'pending_state': pending_state.value_nick,
                                   'pipeline_elements': [e.get_name() for e in self.pipeline.iterate_elements()]
                               })
        elif t == Gst.MessageType.ELEMENT:
            structure = message.get_structure()
            if structure:
                try:
                    # Create a simple message key
                    msg_key = f"{message.src.get_name()}:{structure.get_name()}"
                    current_time = time.time()
                    
                    # Only log if we haven't seen this message recently
                    if msg_key not in self.last_log_time or \
                       (current_time - self.last_log_time.get(msg_key, 0)) >= 5:  # 5 second throttle
                        
                        details = {}
                        for i in range(structure.n_fields()):
                            field_name = structure.nth_field_name(i)
                            try:
                                field_value = structure.get_value(field_name)
                                if hasattr(field_value, 'to_string'):
                                    field_value = field_value.to_string()
                                elif not isinstance(field_value, (str, int, float, bool)):
                                    field_value = str(field_value)
                                details[field_name] = field_value
                            except Exception as e:
                                details[field_name] = f"<error getting value: {str(e)}>"

                        self.logger.debug(f"Element message from {message.src.get_name()}: {structure.get_name()}", 
                                        extra={'message_details': details})
                        
                        # Update last log time
                        self.last_log_time[msg_key] = current_time
                        
                except Exception as e:
                    # Only log the first occurrence of each type of processing error
                    error_key = str(e)
                    if error_key not in self.last_log_time or \
                       (current_time - self.last_log_time.get(error_key, 0)) >= 5:
                        self.logger.warning(f"Error processing element message: {str(e)}")
                        self.last_log_time[error_key] = current_time

    def cleanup(self):
        # Stop the SRT stats collection timer
        if hasattr(self, 'srt_stats_timer'):
            GLib.source_remove(self.srt_stats_timer)

        # Clean up the pipeline
        if hasattr(self, 'pipeline'):
            self.pipeline.set_state(Gst.State.NULL)

        self.logger.info("Cleanup process completed")

if __name__ == "__main__":
    import argparse

    # Command Line Argument Parsing
    parser = argparse.ArgumentParser(description="CariCoder: Configurable streaming application")
    parser.add_argument("--log-dir", default="logs", help="Directory to store log files")
    parser.add_argument("channel", help="Channel name from the configuration")
    parser.add_argument("--log-level", choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'], 
                        default='INFO', help="Set the logging level")
    parser.add_argument("--source-index", type=int, default=0,
                        help="Index of the input source to use (default: 0)")
    
    args = parser.parse_args()

    # Initialize enhanced logging
    #logger = setup_logging(args.channel, args.log_level)
    logger = setup_logging(args.channel, args.log_dir, args.log_level)
    logger.info(f"Starting CariCoder with channel: {args.channel}, log level: {args.log_level}, source index: {args.source_index}")

    # CariCoder Execution
    try:
        coder = CariCoder(args.channel, args.source_index)
        coder.run()
    except ValueError as e:
        logger.error(f"Configuration error: {str(e)}")
        sys.exit(1)
    except IndexError as e:
        logger.error(f"Invalid source index: {str(e)}")
        sys.exit(1)
    except Exception as e:
        logger.critical(f"Fatal error in CariCoder: {str(e)}", exc_info=True)
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received, exiting CariCoder")
        sys.exit(0)